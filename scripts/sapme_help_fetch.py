#!/usr/bin/env python3
"""
Fetch help.sap.com documents through its content API instead of its HTML shell.

WHY THIS EXISTS
    help.sap.com is a Vue single-page app. Requesting a doc URL returns a
    1,160-byte stub with no content in it -- measured across 720 of the 899
    public URLs in the two catalogues, every one identical. Plain downloading
    cannot work, and five guessed "raw content" endpoints all returned the same
    283-character placeholder.

    Reading a HAR of one page load found the real API. It is three calls, it is
    documented nowhere, and -- the part that matters -- it needs NO SESSION.
    Verified anonymously against a scope item that was not the one captured.

THE CHAIN
    /docs/{product_url}/{deliverable_url}          <- what the catalogue gives us
      |
      v  http.svc/deliverableMetadata?product_url=&deliverable_url=&version=LATEST
         -> data.deliverable.id, data.filePath
      |
      v  http.svc/pagecontent?deliverableInfo=1&deliverable_id=&file_path=
         -> data.body                        the topic HTML
            data.deliverable.fullToc         every OTHER topic in the document

    So one catalogue URL yields a whole multi-topic document, not one page:
    Purpose, Prerequisites, Overview Table, Test Procedures, Appendix. The test
    procedures are usually the substance, and they are not on the landing topic.

    `buildNo` appears in the browser's calls and is NOT required; omitting it
    returns the same payload. `deliverable` is a dict -- the id is nested at
    data.deliverable.id, and passing the dict earns an HTTP 500.

OUTPUT
    Writes one combined HTML file per deliverable into the same files/ directory
    the plain downloader uses, and records it in the same fetch_manifest.json,
    so sapme_ingest.py picks these up with no special case.

DO NOT RUN THIS ALONGSIDE sapme_fetch.py
    Both write the same brain/<source>/fetch_manifest.json, and each reads the
    whole file, mutates it in memory and writes it back. Run concurrently, the
    slower one's final write silently discards everything the other recorded --
    observed here, six help documents vanished from the manifest while the plain
    downloader was still going. Sequence them; the files on disk survive either
    way, but the manifest is the record of what was fetched.

USAGE
    python3.11 scripts/sapme_help_fetch.py --dry-run
    python3.11 scripts/sapme_help_fetch.py --limit 20
    python3.11 scripts/sapme_help_fetch.py
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN = BASE_DIR / "brain"
SVC = "https://help.sap.com/http.svc"

SOURCES = {
    "sapbp": (BRAIN / "sapbp" / "raw" / "bom_manifest.json", "name"),
    "sapactivate": (BRAIN / "sapactivate" / "raw" / "accelerators.json", "title"),
}

# sapbp's 505 help URLs are skipped by default, and that is a measured decision
# rather than a preference. They are all BOM.175 "Test script (SAP Help Portal)"
# -- the same test script already arriving as .xlsx and .docx for the same scope
# item. Word-set overlap on two sampled items:
#
#     help <-> xlsx   jaccard 0.79-0.80   0.91-0.93 of help's vocabulary in xlsx
#     docx <-> xlsx   jaccard 0.59-0.84   0.76-0.97 of docx's vocabulary in xlsx
#
# The xlsx is 2-3x larger and close to a superset of both. Ingesting all three
# would put three near-copies of one test script in the index for ~500 scope
# items, and vectorstore.py already records what a large block of similar new
# documents does to ranking: 90 of the top 200 candidates became the new source
# and a filtered query returned 2 hits where 184 qualified.
#
# sapactivate's 218 are methodology documents with no such twin, so they are
# fetched in full. Pass --include-sapbp-help to override deliberately.
DUPLICATED_BY_DOWNLOAD = {"sapbp"}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

try:
    import truststore                       # noqa: PLC0415

    truststore.inject_into_ssl()
except ImportError:
    pass


def api(path, params):
    url = f"{SVC}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def split_doc_url(url):
    """/docs/{product}/{deliverable} -> (product, deliverable). None if not that shape."""
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "docs":
        return parts[1], parts[2]
    return None, None


def toc_paths(deliverable):
    """Every topic file in the document, walking nested toc entries."""
    seen, stack = [], list(deliverable.get("fullToc") or [])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        u = node.get("u")
        if u and u not in seen:
            seen.append(u)
        for k in ("c", "children", "nodes"):
            if isinstance(node.get(k), list):
                stack.extend(node[k])
    return seen


def fetch_document(product, deliverable_url, rate, max_topics):
    meta = api("deliverableMetadata", {
        "product_url": product, "deliverable_url": deliverable_url,
        "version": "LATEST", "loadlandingpageontopicnotfound": "true"})
    d = meta.get("data") or {}
    dev = (d.get("deliverable") or {}).get("id")
    first = d.get("filePath")
    if not (dev and first):
        return None, 0

    page = api("pagecontent", {"deliverableInfo": "1", "deliverable_id": dev,
                               "file_path": first})
    pd = page.get("data") or {}
    info = pd.get("deliverable") or {}
    title = info.get("title") or deliverable_url
    parts = [f"<h1>{title}</h1>", pd.get("body") or ""]

    paths = [p for p in toc_paths(info) if p != first][:max_topics]
    for p in paths:
        time.sleep(rate)
        try:
            sub = api("pagecontent", {"deliverableInfo": "0",
                                      "deliverable_id": dev, "file_path": p})
            body = (sub.get("data") or {}).get("body")
            if body:
                parts.append(body)
        except Exception:                                # noqa: BLE001
            continue
    return title, "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rate", type=float, default=0.3)
    ap.add_argument("--max-topics", type=int, default=25,
                    help="topics per document; a few have very long tables of contents")
    ap.add_argument("--include-sapbp-help", action="store_true",
                    help="also fetch the 505 sapbp help URLs (see DUPLICATED_BY_DOWNLOAD)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    for name in (list(SOURCES) if a.source == "all" else [a.source]):
        if name in DUPLICATED_BY_DOWNLOAD and not a.include_sapbp_help:
            print(f"\n=== {name}: skipped — its help pages duplicate the xlsx/docx "
                  f"already downloaded (--include-sapbp-help to override)")
            continue
        man_p, title_key = SOURCES[name]
        if not man_p.exists():
            print(f"({name}: no catalogue)")
            continue
        rows = json.loads(man_p.read_text(encoding="utf-8"))

        todo, seen = [], set()
        for r in rows:
            url = r.get("url") or ""
            if urllib.parse.urlparse(url).netloc != "help.sap.com":
                continue
            prod, deliv = split_doc_url(url)
            if not prod or url in seen:
                continue
            seen.add(url)
            todo.append((r.get("id"), r.get(title_key), url, prod, deliv))

        state_p = BRAIN / name / "fetch_manifest.json"
        state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
        todo = [t for t in todo if state.get(t[2], {}).get("status") != "ok"]
        if a.limit:
            todo = todo[:a.limit]

        print(f"\n=== {name}: {len(todo)} help.sap.com documents to fetch")
        if a.dry_run:
            for t in todo[:5]:
                print(f"   {t[3]} / {t[4][:70]}")
            continue
        if not todo:
            continue

        files_d = BRAIN / name / "files"
        files_d.mkdir(parents=True, exist_ok=True)
        tally = Counter()
        for i, (rid, title, url, prod, deliv) in enumerate(todo, 1):
            try:
                doc_title, html = fetch_document(prod, deliv, a.rate, a.max_topics)
            except urllib.error.HTTPError as e:
                tally[f"http_{e.code}"] += 1
                state[url] = {"status": f"http_{e.code}", "id": rid}
                continue
            except Exception as exc:                     # noqa: BLE001
                tally["error"] += 1
                state[url] = {"status": "error", "id": rid, "detail": str(exc)[:140]}
                continue

            if not html or len(html) < 800:
                tally["thin"] += 1
                state[url] = {"status": "thin", "id": rid}
                continue

            fn = f"help_{(rid or deliv)[:70]}.html".replace("/", "_")
            (files_d / fn).write_text(html, encoding="utf-8")
            state[url] = {"status": "ok", "id": rid, "kind": "html", "file": fn,
                          "bytes": len(html), "content_type": "text/html",
                          "title": doc_title or title}
            tally["ok"] += 1

            if i % 20 == 0 or i == len(todo):
                print(f"   {i}/{len(todo)}  " +
                      "  ".join(f"{k}={v}" for k, v in tally.most_common()), flush=True)
                state_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
            time.sleep(a.rate)
        state_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"   -> {tally['ok']} documents written to {files_d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
