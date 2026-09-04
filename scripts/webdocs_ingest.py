#!/usr/bin/env python3
"""
Harvest curated developer-doc pages into the brain as `source_system=developer_docs`.

WHY THIS EXISTS
    .claude/agents/developer.md tells the developer agent to READ the official CAP /
    UI5 / Node docs and ground side-by-side code in them, via WebFetch. On this host
    that instruction cannot work: Claude Code runs on Amazon Bedrock, where the
    WebSearch tool is unavailable, and the agent's own fallback is "if a fetch fails,
    cite the URL — never block the build". So the grounding step degrades in silence.
    This puts the same documentation in the brain instead, where search_brain can
    reach it offline, on Bedrock, with no web tools at all.

DESIGN
    Follows the SharePoint pattern — harvest to chunk files on disk, then let
    embed_chunks.py pick them up from CHUNK_ROOTS. Keeping the flaky network step out
    of the expensive embedding step means a failed fetch costs nothing, the run is
    resumable, and the extracted text stays inspectable.

    Curated, NOT crawled: a spider over vendor docs is a politeness/ToS problem and
    would pull thousands of low-value pages. Sources live in webdocs_sources.json.

    No PII masking: these are public vendor docs, unlike the SharePoint corpus.

USAGE
    python3.11 scripts/webdocs_ingest.py                 # fetch + write chunks
    python3.11 scripts/webdocs_ingest.py --dry-run       # fetch + report, write nothing
    python3.11 scripts/webdocs_ingest.py --report        # show the last manifest

    Then re-embed so the brain actually contains them:
        S4PC_VECTOR_BACKEND=bedrock python3.11 mcp-server/vector/build_index.py
        python3.11 scripts/embed_chunks.py
        pm2 restart s4pc-mcp
"""

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR    = Path(__file__).resolve().parent.parent
SOURCES     = Path(__file__).resolve().parent / "webdocs_sources.json"
OUT_DIR     = BASE_DIR / "brain" / "webdocs"
CHUNKS_DIR  = OUT_DIR / "chunks"
RAW_DIR     = OUT_DIR / "raw"
MANIFEST    = OUT_DIR / "manifest.json"

CHUNK_WORDS   = 512          # match sharepoint_ingest so chunk sizes stay comparable
CHUNK_OVERLAP = 64
RATE_LIMIT_S  = 1.5          # be a polite client to someone else's docs site
TIMEOUT_S     = 30
UA = "S4PC-Catalyst-brain-ingest/1.0 (internal delivery accelerator; contact repo owner)"

# A page shorter than this almost certainly means we got a JS shell, a cookie wall or
# an error page rather than prose. Reported as SHELL and NOT stored — an empty
# "document" in the brain is worse than a missing one, because it looks like success.
MIN_USEFUL_CHARS = 800

# Markdown pulled from raw.githubusercontent.com gets a much lower floor. The 800
# threshold above exists to catch a JS shell served in place of prose, and that
# failure mode cannot occur on the raw CDN: a missing file is an HTTP 404, which
# lands in the FAILED path instead. Applying the HTML floor here just discarded
# short-but-real reference pages (measured 2026-09-04: sap-ui-model-type-boolean at
# 724 chars, setting-the-default-binding-mode at 306). This floor only guards against
# a genuinely empty file.
MIN_USEFUL_CHARS_MD = 150

_SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "svg", "form"}


class _TextExtractor(HTMLParser):
    """Minimal HTML -> text. Stdlib only, to keep this script dependency-free."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "tr", "pre"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self):
        raw = " ".join(self._parts)
        raw = re.sub(r"[ \t\xa0]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def fetch(url, accept="text/html,application/xhtml+xml"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "en",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


# ── GitHub-hosted docs (the route for SPA sites that serve no fetchable prose) ──
# ui5.sap.com is an SPA whose every topic URL is a '#/topic/...' fragment, so the
# server only ever returns a ~2 KB shell. SAP publishes the same documentation as
# markdown at github.com/SAP-docs/*, which IS fetchable. That repo is the only
# route to UI5 grounding -- the gap that let finding F-17 (OData apostrophe
# quoting) through in SUPPLIER-PO-STATUS-VIEWER-FD-R2.
#
# Filenames carry content hashes (glossary-9ef211e.md) that change when SAP
# republishes, so paths CANNOT be curated by hand -- they are discovered at run
# time from the git tree. Budget: the tree API is 1 call against GitHub's
# 60-requests/hour unauthenticated limit, then every document body comes from
# raw.githubusercontent.com, which is CDN-served and not part of that budget.

GITHUB_TREE = "https://api.github.com/repos/%s/git/trees/%s?recursive=1"
GITHUB_RAW  = "https://raw.githubusercontent.com/%s/%s/%s"
GITHUB_BLOB = "https://github.com/%s/blob/%s/%s"
RAW_RATE_S  = 0.3        # CDN, not the API -- polite but no need for the 1.5s crawl delay


def github_paths(repo, ref, prefix, match=None, max_files=100):
    """Discover .md paths under `prefix`, newest tree. Returns [] and explains on failure."""
    tree = json.loads(fetch(GITHUB_TREE % (repo, ref), accept="application/vnd.github+json"))
    if tree.get("truncated"):
        print("   NOTE    %s tree was truncated by GitHub — some paths not visible" % repo)
    hits = [n["path"] for n in tree.get("tree", [])
            if n.get("type") == "blob"
            and n["path"].startswith(prefix)
            and n["path"].endswith(".md")]
    if match:
        low = [m.lower() for m in match]
        hits = [p for p in hits if any(m in p.lower() for m in low)]
    hits.sort()
    # A cap, not a coincidence: without it a folder rename upstream silently turns a
    # 40-page ingest into a 2,000-page one and the embedding bill with it.
    #
    # When it does bind, take an even stride rather than the first N. These filenames
    # sort by topic, so a prefix of 60 out of 267 is every "actions-*" and "adapting-*"
    # page and nothing whatsoever past the letter a -- a biased sample that looks like
    # coverage. A stride keeps the spread across topics. Tighten `match` instead of
    # relying on this; a binding cap means the filter is too broad.
    if len(hits) > max_files:
        matched = len(hits)
        step = matched / float(max_files)
        hits = [hits[int(i * step)] for i in range(max_files)]
        print("   NOTE    %s matched %d files, sampled %d evenly across topics "
              "(narrow `match` for full coverage)" % (prefix, matched, max_files))
    return hits


def chunk(text):
    words = text.split()
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + CHUNK_WORDS]))
        if i + CHUNK_WORDS >= len(words):
            break
        i += CHUNK_WORDS - CHUNK_OVERLAP
    return out


def doc_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def store_doc(raw, url, topic, dtype, cite, previous, counts, manifest, dry_run,
              min_chars=MIN_USEFUL_CHARS):
    """Extract -> SHELL-check -> chunk -> write one document. Shared by both sources.

    `url` identifies the document (manifest key, chunk id); `cite` is what a brain hit
    shows a human — for GitHub docs those differ, so a citation opens the rendered page
    rather than a raw.githubusercontent URL.
    """
    parser = _TextExtractor()
    parser.feed(raw)
    text = parser.text()
    entry = {"url": url, "topic": topic, "deliverable_type": dtype,
             "chars": len(text),
             "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}

    if len(text) < min_chars:
        # Loud, not silent: this is the WebFetch failure mode we are replacing.
        entry.update(status="SHELL", chunks=0)
        counts["SHELL"] += 1
        print("   SHELL   %s  (%d chars — JS shell or wall, NOT stored)" % (url, len(text)))
        manifest.append(entry)
        return

    pieces = chunk(text)
    entry.update(status="ok", chunks=len(pieces))
    prior = previous.get(url)
    if prior and prior.get("content_hash") == entry["content_hash"]:
        counts["unchanged"] += 1
        note = " (unchanged since last harvest)"
    else:
        note = " (new or CHANGED)"
    counts["ok"] += 1
    print("   ok      %s  %d chars -> %d chunks%s" % (url, len(text), len(pieces), note))

    if not dry_run:
        did = doc_id(url)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / ("%s.txt" % did)).write_text(text, encoding="utf-8")
        out = CHUNKS_DIR / dtype
        out.mkdir(parents=True, exist_ok=True)
        for n, piece in enumerate(pieces):
            (out / ("%s_%04d.json" % (did, n))).write_text(json.dumps({
                "id":               "%s_%04d" % (did, n),
                "text":             piece,
                "source":           topic,
                "source_system":    "developer_docs",
                "deliverable_type": dtype,
                "content_type":     "reference",
                "phase":            "Realize",
                "agent_role":       "build_agent",
                "relative_path":    cite,
            }, ensure_ascii=False), encoding="utf-8")
    manifest.append(entry)


def harvest_github(gh_sources, previous, counts, manifest, dry_run):
    """Harvest markdown docs SAP publishes on GitHub (the only route to UI5 prose)."""
    for src in gh_sources:
        repo, ref = src["repo"], src.get("ref", "main")
        prefix, dtype = src["include_prefix"], src.get("deliverable_type", "developer_docs")
        label = src.get("topic", "%s/%s" % (repo, prefix))
        print("\n-- %s  [%s %s]" % (label, repo, prefix))
        try:
            paths = github_paths(repo, ref, prefix, src.get("match"),
                                 src.get("max_files", 100))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            # Most likely cause: GitHub's 60/hour unauthenticated API limit.
            counts["FAILED"] += 1
            print("   FAILED  tree %s (%s)" % (repo, str(exc)[:90]))
            manifest.append({"url": GITHUB_TREE % (repo, ref), "topic": label,
                             "deliverable_type": dtype, "status": "FAILED",
                             "error": str(exc)[:160], "chars": 0, "chunks": 0})
            continue

        print("   found %d markdown files" % len(paths))
        for i, path in enumerate(paths):
            if i:
                time.sleep(RAW_RATE_S)
            raw_url = GITHUB_RAW % (repo, ref, path)
            try:
                body = fetch(raw_url, accept="text/plain")
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                counts["FAILED"] += 1
                print("   FAILED  %s  (%s)" % (path, str(exc)[:70]))
                manifest.append({"url": raw_url, "topic": label, "deliverable_type": dtype,
                                 "status": "FAILED", "error": str(exc)[:160],
                                 "chars": 0, "chunks": 0})
                continue
            # Cite the rendered blob, not the raw CDN URL — a human following the
            # citation should land on a readable page.
            store_doc(body, raw_url, "%s — %s" % (label, Path(path).stem),
                      dtype, GITHUB_BLOB % (repo, ref, path),
                      previous, counts, manifest, dry_run,
                      min_chars=MIN_USEFUL_CHARS_MD)


def main():
    ap = argparse.ArgumentParser(description="Harvest developer docs into the brain")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    ap.add_argument("--report", action="store_true", help="print the last manifest and exit")
    ap.add_argument("--only", choices=["web", "github"], default=None,
                    help="Harvest just one source kind (default: both)")
    args = ap.parse_args()

    if args.report:
        if not MANIFEST.exists():
            sys.exit("No manifest yet — run the ingest first.")
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        print("harvested_at: %s" % m.get("harvested_at"))
        for e in m.get("documents", []):
            print("  %-7s %-5s chunks=%-4s %s" % (
                e.get("status"), e.get("chars"), e.get("chunks"), e.get("url")))
        return

    cfg = json.loads(SOURCES.read_text(encoding="utf-8"))
    sources = cfg.get("sources") or []
    gh_sources = cfg.get("github_sources") or []
    if args.only == "web":
        gh_sources = []
    elif args.only == "github":
        sources = []
    print("== harvesting %d curated pages + %d github doc sets%s" % (
        len(sources), len(gh_sources), " (DRY RUN)" if args.dry_run else ""))

    manifest, counts = [], {"ok": 0, "SHELL": 0, "FAILED": 0, "unchanged": 0}
    previous = {}
    if MANIFEST.exists():
        try:
            previous = {d["url"]: d for d in
                        json.loads(MANIFEST.read_text(encoding="utf-8")).get("documents", [])}
        except Exception:
            previous = {}

    for i, src in enumerate(sources):
        url, topic = src["url"], src.get("topic", src["url"])
        if i:
            time.sleep(RATE_LIMIT_S)
        dtype = src.get("deliverable_type", "developer_docs")
        try:
            html = fetch(url)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            counts["FAILED"] += 1
            print("   FAILED  %s  (%s)" % (url, str(exc)[:70]))
            manifest.append({"url": url, "topic": topic, "deliverable_type": dtype,
                             "status": "FAILED", "error": str(exc)[:160],
                             "chars": 0, "chunks": 0})
            continue
        store_doc(html, url, topic, dtype, url, previous, counts, manifest, args.dry_run)

    if gh_sources:
        harvest_github(gh_sources, previous, counts, manifest, args.dry_run)

    if not args.dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps({
            "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "documents": manifest,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n== %d ok (%d unchanged), %d shell, %d failed" % (
        counts["ok"], counts["unchanged"], counts["SHELL"], counts["FAILED"]))
    if counts["SHELL"] or counts["FAILED"]:
        print("== Sources reporting SHELL return a JS app, not prose — they need a different")
        print("   approach (a docs archive/sitemap, or a vendor-provided bundle), not this script.")
    if not args.dry_run and counts["ok"]:
        print("== Next: python3.11 scripts/embed_chunks.py   (then pm2 restart s4pc-mcp)")
    # A harvest that stored nothing is a failure, not a no-op — exit non-zero so a
    # scheduled run surfaces instead of looking successful.
    sys.exit(0 if counts["ok"] else 1)


if __name__ == "__main__":
    main()
