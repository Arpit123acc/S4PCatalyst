#!/usr/bin/env python3
"""
Download the documents catalogued by sapbp_catalog.py and sapact_catalog.py.

WHY FETCH IS ITS OWN STAGE
    Same reason webdocs_ingest.py splits them: the network step is the flaky one.
    A failed download then costs no embedding, the run resumes where it stopped,
    and the retrieved bytes stay on disk to inspect when a parse looks wrong later.
    With ~6,900 URLs and a session that expires in hours, resumability is not a
    nicety -- a run WILL be interrupted.

THE FAILURE THIS EXISTS TO CATCH
    support.sap.com answers an unauthenticated request for a .xlsx with HTTP 200,
    content-type text/html, and a SAML auto-submit form (measured 2026-09-19).
    Status, byte count and "no exception raised" all say success. Writing those
    would fill the corpus with thousands of identical login pages that each look
    like a document.

    Here the check is DETERMINISTIC, unlike the character-count floor
    webdocs_ingest needs for prose: xlsx and docx are zips and begin "PK\\x03\\x04",
    PDFs begin "%PDF". A file claiming to be one of those and starting with "<" is
    the login wall, full stop. On the first such response the run STOPS rather
    than continuing -- an expired session does not fix itself, and 3,000 more
    attempts would just be 3,000 more login pages.

USAGE
    # public hosts only -- needs no session at all (~900 documents)
    python3.11 scripts/sapme_fetch.py --public-only

    # everything, including the SAML-protected majority
    export SAPME_COOKIE='<Cookie header from a support.sap.com request>'
    python3.11 scripts/sapme_fetch.py

    python3.11 scripts/sapme_fetch.py --source sapactivate --limit 25   # a taster
    python3.11 scripts/sapme_fetch.py --dry-run                         # plan only

NOTE ON THE COOKIE
    The catalogue fetchers need a pr.alm.me.sap.com session. THIS needs a
    support.sap.com one -- a different host, so a different cookie. Grab it from
    a request to support.sap.com in DevTools after opening any accelerator.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN = BASE_DIR / "brain"

SOURCES = {
    "sapbp": {
        "manifest": BRAIN / "sapbp" / "raw" / "bom_manifest.json",
        "files": BRAIN / "sapbp" / "files",
        "state": BRAIN / "sapbp" / "fetch_manifest.json",
        "title": lambda r: r.get("name"),
    },
    "sapactivate": {
        "manifest": BRAIN / "sapactivate" / "raw" / "accelerators.json",
        "files": BRAIN / "sapactivate" / "files",
        "state": BRAIN / "sapactivate" / "fetch_manifest.json",
        "title": lambda r: r.get("title"),
    },
}

PUBLIC_HOSTS = {
    "help.sap.com", "learning.sap.com", "www.sap.com", "sap.com",
    "blogs.sap.com", "community.sap.com", "pages.community.sap.com",
    "s4hanacloud.community.sap", "discovery-center.cloud.sap",
    "dam.sap.com", "d.dam.sap.com", "api.sap.com", "cap.cloud.sap", "ui5.sap.com",
}

SAML_MARKERS = ("samlform", "accounts.sap.com/saml2", "login.support.html",
                "j_security_check", "samlrequest")

# Hosts SAPME_COOKIE is actually for. A login page from one of these means the
# session died and the run must stop. A login page from anywhere else means that
# third party wants its own sign-in, which is a per-document dead end, not a
# reason to abandon the other 6,000.
SESSION_HOSTS = {"support.sap.com", "launchpad.support.sap.com", "me.sap.com",
                 "pr.alm.me.sap.com"}

# A page shorter than this is a shell, a cookie wall or an error -- not prose.
# Borrowed from webdocs_ingest.py, which measured it.
MIN_USEFUL_HTML = 800

# A full browser header set, not just a User-Agent. Measured 2026-09-21: with a
# UA alone, www.sap.com returned 403 for all 35 of its URLs; with the Sec-Fetch-*
# and Accept-Language headers below the same URLs return 200 and 60 KB of prose.
# The edge in front of those sites fingerprints the header SET, so a lone UA reads
# as a bot no matter what it claims to be. blogs.sap.com and s4hanacloud.community
# still refuse -- stronger protection, and not worth defeating.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",          # no gzip: we inspect magic bytes
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

try:
    import truststore                      # noqa: PLC0415

    truststore.inject_into_ssl()
except ImportError:
    pass


class SessionExpired(RuntimeError):
    pass


def classify(body, url):
    """What did we actually get? Magic bytes first, they cannot be argued with."""
    if body[:4] == b"PK\x03\x04":
        return "zip"                       # xlsx / docx / pptx
    if body[:4] == b"%PDF":
        return "pdf"
    head = body[:4000].decode("utf-8", errors="replace").lower()
    if any(m in head for m in SAML_MARKERS):
        return "login"
    if head.lstrip().startswith(("<!doctype", "<html", "<?xml")):
        return "html"
    if not body.strip():
        return "empty"
    return "other"


def safe_name(item_id, url):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if len(ext) > 6 or not re.fullmatch(r"\.[a-z0-9]+", ext or ""):
        ext = ""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", str(item_id))[:80]
    return f"{stem}{ext}"


# Errors that mean "the network blinked", not "this document is unavailable".
# A 6,000-URL run produced 903 getaddrinfo failures in one burst -- DNS giving up
# under sustained lookups, or a VPN flap -- and every one of those URLs was fine
# on retry. Treating them as permanent would have written off 15% of the corpus.
TRANSIENT = ("getaddrinfo", "temporarily unavailable", "timed out", "timeout",
             "connection reset", "forcibly closed", "remote end closed",
             "incompleteread", "connection aborted", "broken pipe")


def _safe_url(url):
    """Percent-encode what urllib will not accept raw.

    Catalogue URLs are not all clean: one carries a literal space and another an
    en-dash, and urllib raises before the request is made -- "URL can't contain
    control characters" and "'ascii' codec can't encode character '\\u2013'".
    Re-quoting the path leaves already-encoded URLs untouched because % is safe.
    """
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        p.scheme, p.netloc,
        urllib.parse.quote(p.path, safe="/%:@!$&'()*+,;=~"),
        urllib.parse.quote(p.query, safe="=&%:/?@!$'()*+,;~"),
        p.fragment))


def fetch(url, cookie, attempts=3):
    last = None
    for i in range(attempts):
        req = urllib.request.Request(_safe_url(url))
        for k, v in BROWSER_HEADERS.items():
            req.add_header(k, v)
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read(), r.headers.get("Content-Type", ""), r.geturl()
        except urllib.error.HTTPError:
            raise                                   # a real answer; do not retry
        except Exception as exc:                    # noqa: BLE001
            last = exc
            if not any(t in str(exc).lower() for t in TRANSIENT):
                raise
            time.sleep(2 * (i + 1))                 # brief, widening backoff
    raise last


def load_rows(name, public_only):
    cfg = SOURCES[name]
    if not cfg["manifest"].exists():
        sys.exit(f"{cfg['manifest']} missing — run the catalogue fetcher first.")
    rows = json.loads(cfg["manifest"].read_text(encoding="utf-8"))
    out = []
    for r in rows:
        url = r.get("url") or ""
        if not url.startswith("http"):
            continue
        host = urllib.parse.urlparse(url).netloc
        needs_auth = r.get("needs_auth")
        if needs_auth is None:                       # sapbp has no such column
            needs_auth = host not in PUBLIC_HOSTS
        if public_only and needs_auth:
            continue
        out.append({"id": r.get("id"), "title": cfg["title"](r), "url": url,
                    "host": host, "needs_auth": needs_auth})
    # One URL, one download. The Process Navigator manifest repeats a URL across
    # rows, and fetching the same bytes twice is pure waste.
    seen, deduped = set(), []
    for r in out:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        deduped.append(r)
    return deduped


def session_is_live(rows, cookie):
    """Fetch ONE authenticated row to prove the session works. True/False/None.

    None when there is nothing to test with -- no cookie, or no row needing one.

    WHY BEFORE THE LOOP
        A support.sap.com session expires in minutes, so the common case is
        pasting a cookie that has already died. The run then announces "12,044
        to go", fetches one document, discovers a login page and stops. Nothing
        is damaged -- the guard works -- but the operator has spent a paste
        cycle to learn one bit of information, and on a corpus this size that
        happened repeatedly.

        Checking first turns a two-minute round trip into a one-second answer,
        and lets the run carry on with the public rows instead of doing nothing
        at all, which is the outcome that actually wastes an evening.

    Deliberately NOT a substitute for the in-loop check. A session can expire
    during a run of ten thousand files, and that is exactly when it matters
    most that a login page is never written into the corpus.
    """
    if not cookie:
        return None
    probe = next((r for r in rows if r.get("needs_auth") and r.get("url")), None)
    if not probe:
        return None
    try:
        body, _status, kind = fetch(probe["url"], cookie, attempts=1)
    except Exception:
        return None          # transient: let the real loop retry properly
    return kind != "login"


def save_state(path, state):
    """Write the state file, MERGING whatever is on disk first.

    Each run loads the whole state dict at startup and rewrites it wholesale, so
    two concurrent fetches used to clobber each other: the second writer's copy
    never contained the first's entries. That is not hypothetical -- a second
    help-fetch started alongside a running one on 2026-09 left 236 files on disk
    against 196 in the manifest.

    The damage was always bounded, because the files themselves survive and
    sapme_ingest adopts orphans from disk. What was lost was the RECORD, so a
    later resume re-downloaded thousands of files it already had and the
    manifest stopped describing reality.

    Re-reading before each write makes concurrent runs safe: entries are keyed
    by URL, two fetches work on disjoint URLs, so a merge cannot conflict. The
    on-disk copy wins only for keys this process never touched.
    """
    merged = {}
    try:
        merged = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    merged.update(state)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    ap.add_argument("--public-only", action="store_true",
                    help="skip everything needing a session")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--match", default="",
                    help="only URLs whose title contains this (case-insensitive), "
                         "so a high-value subset can jump the queue")
    ap.add_argument("--rate", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--include-duplicates", action="store_true",
                    help="Also fetch secondary-country copies of scope items the "
                         "primary country already covers (tripling near-identical "
                         "test scripts). Off by default.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cookie = os.environ.get("SAPME_COOKIE", "").strip()
    names = list(SOURCES) if a.source == "all" else [a.source]
    rc = 0

    for name in names:
        cfg = SOURCES[name]
        rows = load_rows(name, a.public_only)
        # Honour the catalog's download flag. sapbp_catalog.mark_downloads
        # decides it, because it is the only place that knows each row's country
        # and which scope items the primary country already covers. A row marked
        # False is a real artifact that lookup_accelerator still reports with its
        # URL -- it is excluded from the CORPUS, not denied.
        if not a.include_duplicates:
            skipped = [r for r in rows if r.get("download") is False]
            rows = [r for r in rows if r.get("download") is not False]
            if skipped:
                log.info("%s: skipping %d localized duplicate(s); --include-duplicates "
                         "to fetch them", name, len(skipped))
        if a.match:
            m = a.match.lower()
            rows = [r for r in rows if m in (r.get("title") or "").lower()]
        if a.limit:
            rows = rows[:a.limit]

        state = {}
        if cfg["state"].exists():
            state = json.loads(cfg["state"].read_text(encoding="utf-8"))
        todo = [r for r in rows if state.get(r["url"], {}).get("status") != "ok"]

        need_auth = sum(1 for r in todo if r["needs_auth"])
        print(f"\n=== {name}: {len(rows)} urls, {len(rows) - len(todo)} already fetched, "
              f"{len(todo)} to go ({need_auth} need a session)")
        if need_auth and not cookie and not a.public_only:
            print("   WARNING: SAPME_COOKIE unset — those will return login pages.")
            print("   Use --public-only, or export a support.sap.com cookie.")
        elif need_auth and not a.public_only and not a.dry_run:
            live = session_is_live(todo, cookie)
            if live is False:
                print("   SESSION IS DEAD — the cookie returns a login page.")
                print(f"   Skipping the {need_auth} rows that need one and fetching "
                      f"the {len(todo) - need_auth} public rows instead.")
                print("   Re-capture SAPME_COOKIE from a support.sap.com request "
                      "and re-run to get the rest.")
                todo = [r for r in todo if not r.get("needs_auth")]
                if not todo:
                    print("   Nothing left to do without a session.")
                    continue
            elif live:
                print("   session ok")
        if a.dry_run:
            for h, n in Counter(r["host"] for r in todo).most_common(10):
                print(f"   {n:>5}  {h}")
            continue
        if not todo:
            continue

        cfg["files"].mkdir(parents=True, exist_ok=True)
        tally = Counter()
        try:
            for i, r in enumerate(todo, 1):
                try:
                    body, ctype, final = fetch(r["url"], cookie if r["needs_auth"] else "")
                except urllib.error.HTTPError as e:
                    tally[f"http_{e.code}"] += 1
                    state[r["url"]] = {"status": f"http_{e.code}", "id": r["id"]}
                    continue
                except Exception as exc:                       # noqa: BLE001
                    tally["error"] += 1
                    state[r["url"]] = {"status": "error", "id": r["id"],
                                       "detail": str(exc)[:160]}
                    continue

                kind = classify(body, r["url"])
                # A login page only means OUR session died if it came from a host
                # our cookie is for. The catalogue also links third-party sites
                # with their own sign-in -- a BTP Fiori launchpad
                # (flpnwc-*.dispatcher.hana.ondemand.com), WalkMe, Mural. Treating
                # those as session expiry aborted a run 200 documents in, on a
                # host we never had a session for and never will.
                if kind == "login" and r["host"] not in SESSION_HOSTS:
                    tally["needs_other_login"] += 1
                    state[r["url"]] = {"status": "needs_other_login", "id": r["id"],
                                       "host": r["host"]}
                    time.sleep(a.rate)
                    continue
                if kind == "login":
                    # Stop. An expired session does not recover, and every further
                    # request would write another copy of the same login page.
                    state[r["url"]] = {"status": "login_page", "id": r["id"]}
                    save_state(cfg["state"], state)
                    raise SessionExpired(
                        f"{name}: got a login page for {r['url'][:90]}\n"
                        "   The support.sap.com session is missing or expired. Refresh\n"
                        "   SAPME_COOKIE and re-run -- everything already fetched is kept.")
                if kind == "html" and len(re.sub(r"<[^>]+>", " ", body.decode(
                        "utf-8", errors="replace"))) < MIN_USEFUL_HTML:
                    kind = "shell"

                if kind in ("zip", "pdf", "html"):
                    path = cfg["files"] / safe_name(r["id"], r["url"])
                    path.write_bytes(body)
                    state[r["url"]] = {"status": "ok", "id": r["id"], "kind": kind,
                                       "file": path.name, "bytes": len(body),
                                       "content_type": ctype, "title": r["title"]}
                else:
                    state[r["url"]] = {"status": kind, "id": r["id"], "bytes": len(body)}
                tally[kind] += 1

                if i % 25 == 0 or i == len(todo):
                    print(f"   {i}/{len(todo)}  " +
                          "  ".join(f"{k}={v}" for k, v in tally.most_common()), flush=True)
                    save_state(cfg["state"], state)
                time.sleep(a.rate)
        except SessionExpired as exc:
            print(f"\nSTOPPED: {exc}")
            rc = 1
        finally:
            save_state(cfg["state"], state)
            ok = sum(1 for v in state.values() if v.get("status") == "ok")
            print(f"   -> {ok} usable files in {cfg['files']}")
            print(f"   -> state: {cfg['state']}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
