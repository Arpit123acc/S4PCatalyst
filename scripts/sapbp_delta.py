#!/usr/bin/env python3
"""What changed since the last refresh, and what needs re-downloading.

WHY A DELTA AT ALL
    A full refresh re-downloads ~13,600 documents from support.sap.com behind a
    SAML session that expires in minutes. SAP ships roughly quarterly and 77% of
    solution processes come back as "No Change", so almost all of that cost buys
    nothing. The catalogue fetch itself is cheap — three OData calls, a few
    minutes — so the delta belongs on the DOWNLOAD, not on the fetch.

THE PART THE FETCH STATE ALREADY HANDLES, AND THE PART IT CANNOT
    sapme_fetch keys its state on URL, so a genuinely new document is already
    fetched once and skipped thereafter. That covers additions for free.

    What it cannot see is SAP republishing a document behind an unchanged URL —
    the link is stable across releases while the file behind it changes. Nothing
    in the URL moves, so the fetcher skips it forever and the corpus quietly
    holds the old release. contentReleaseVersion_ID is the field that moves, and
    comparing it against the previous snapshot is the whole point of this script.

    That is the same failure this codebase keeps meeting: no error, no empty
    result, just a stale answer that looks exactly like a current one.

THREE MODES
    --check     One request. Has SAP shipped a new release, or changed the
                service contract? Cheap enough for a cron, and the only part
                that is safe to run unattended -- it reads and reports, and
                never starts a download that needs a human session.
    (default)   Diff the current bom_manifest against bom_manifest.prev and
                report what moved. Reads nothing from the network.
    --apply     The same diff, but clears the fetch-state entry for every row
                whose content changed, so the next sapme_fetch re-downloads
                exactly those and nothing else.

Usage:
    export SAPME_COOKIE='<Cookie header from a pr.alm.me.sap.com request>'   # --check only
    python3.11 scripts/sapbp_delta.py --check
    python3.11 scripts/sapbp_delta.py
    python3.11 scripts/sapbp_delta.py --apply
"""

import sys
import json
import argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_DIR = Path(__file__).resolve().parent.parent
RAW      = BASE_DIR / "brain" / "sapbp" / "raw"
MANIFEST = RAW.parent / "manifest.json"
CUR      = RAW / "bom_manifest.json"
PREV     = RAW / "bom_manifest.prev.json"
STATE    = RAW.parent / "fetch_manifest.json"
OUT      = RAW / "delta.json"

# changeCategory is a LIST, comma-joined: "Addition,Upgrade" is one value SAP
# returns. Filtering positively on the categories we know would silently drop it
# and, worse, drop "Retired" -- the one that means stop serving something. So the
# rule is negative: anything that is not exactly "No Change" has moved.
UNCHANGED = "No Change"


def _load(p, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def check_release():
    """Has SAP shipped anything? One request, read-only, safe for a cron."""
    from sapbp_catalog import releases_for, DEFAULT_STABLE_ID, NotAuthenticated  # noqa: PLC0415
    man = _load(MANIFEST, {}) or {}
    have_rel = man.get("target_release")
    have_ver = man.get("internal_version")
    have_etag = man.get("metadata_etag")

    rows = releases_for(man.get("stable_id") or DEFAULT_STABLE_ID)
    if not rows:
        print("!! no releases returned — cannot tell. Treat as unknown, not as unchanged.")
        return 2
    top = min(rows, key=lambda r: r.get("seq") or 99)
    rel, ver = top.get("targetRelease"), top.get("internalVersion")

    print("== recorded : release %s  v%s" % (have_rel, have_ver))
    print("== live     : release %s  v%s  [%s]" % (rel, ver, top.get("ID")))

    if rel == have_rel and ver == have_ver:
        print("\nNo change. Nothing to do.")
        return 0
    if rel != have_rel:
        print("\nNEW RELEASE: %s -> %s" % (have_rel, rel))
    else:
        # internalVersion moves within a release; a refresh that only watched
        # the release number would sit on stale content until the next quarter.
        print("\nSAME RELEASE, NEW CONTENT: v%s -> v%s" % (have_ver, ver))
    print("   next: re-run sapbp_catalog.py, then sapbp_delta.py --apply")
    if have_etag:
        print("\n   Also compare metadata_etag after the fetch. A changed etag means SAP")
        print("   altered the service contract, and a delta computed across that is not")
        print("   trustworthy -- re-read the field meanings before believing the diff.")
    return 1


def diff():
    """Classify every current row against the previous snapshot."""
    cur = _load(CUR)
    if cur is None:
        raise SystemExit("%s missing — run sapbp_catalog.py first" % CUR)
    prev = _load(PREV)
    if prev is None:
        return None, ("no previous snapshot (%s). This is a first run: everything is "
                      "new by definition, and the fetch state already handles that."
                      % PREV.name)

    pv = {r.get("id"): r for r in prev if r.get("id")}
    cv = {r.get("id"): r for r in cur if r.get("id")}

    out = {"new": [], "content_changed": [], "url_changed": [], "retired": [],
           "unchanged": 0}
    for rid, r in cv.items():
        if not r.get("download"):
            continue                       # excluded from the corpus anyway
        old = pv.get(rid)
        if old is None:
            out["new"].append(r)
        elif (r.get("content_release_version") !=
              old.get("content_release_version")):
            out["content_changed"].append(r)
        elif r.get("url") != old.get("url"):
            out["url_changed"].append(r)
        else:
            out["unchanged"] += 1

    # Gone from the catalogue. NOT deleted: the corpus is often the only record
    # of how something used to work, and CLAUDE.md treats a withdrawn object as
    # meaning the opposite of available rather than as absent.
    for rid, old in pv.items():
        if rid not in cv and old.get("download"):
            out["retired"].append(old)
    return out, None


def report(d):
    print("== delta against the previous fetch")
    print("   new documents            %d" % len(d["new"]))
    print("   content changed          %d   (same URL, new contentReleaseVersion)" %
          len(d["content_changed"]))
    print("   URL changed              %d" % len(d["url_changed"]))
    print("   retired from catalogue   %d   (kept, flagged — never deleted)" % len(d["retired"]))
    print("   unchanged                %d" % d["unchanged"])
    todo = len(d["new"]) + len(d["content_changed"]) + len(d["url_changed"])
    total = todo + d["unchanged"]
    if total:
        print("\n   to re-download: %d of %d  (%.1f%%)" % (todo, total, 100.0 * todo / total))
    for k in ("content_changed", "retired"):
        if d[k]:
            print("\n   %s, by document name:" % k.replace("_", " "))
            for n, c in Counter(r.get("name") for r in d[k]).most_common(6):
                print("      %4d  %s" % (c, n))


def apply_delta(d):
    """Clear fetch state for changed rows so the next fetch re-downloads them.

    Only the changed ones. Deleting the whole state file would work and would
    also re-download 13,600 documents, which is the cost this script exists to
    avoid.
    """
    state = _load(STATE, {}) or {}
    cleared = 0
    for r in d["content_changed"] + d["url_changed"]:
        u = r.get("url")
        if u and u in state:
            del state[u]
            cleared += 1
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("\n   cleared %d fetch-state entr%s — next sapme_fetch re-downloads those"
          % (cleared, "y" if cleared == 1 else "ies"))
    print("   new documents need no clearing: absent from the state is already 'fetch me'")
    return cleared


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="release detection only — one request, safe unattended")
    ap.add_argument("--apply", action="store_true",
                    help="clear fetch state for changed rows")
    a = ap.parse_args()

    if a.check:
        return check_release()

    d, why = diff()
    if d is None:
        print("== %s" % why)
        return 0
    report(d)

    OUT.write_text(json.dumps({
        "counts": {k: (len(v) if isinstance(v, list) else v) for k, v in d.items()},
        "content_changed": [r.get("url") for r in d["content_changed"]],
        "url_changed":     [r.get("url") for r in d["url_changed"]],
        "new":             [r.get("url") for r in d["new"]],
        "retired":         [{"id": r.get("id"), "name": r.get("name"),
                             "url": r.get("url")} for r in d["retired"]],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n   wrote %s" % OUT)

    if a.apply:
        apply_delta(d)
    elif d["content_changed"] or d["url_changed"]:
        print("   run with --apply to queue those for re-download")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ImportError as exc:                      # pragma: no cover
        raise SystemExit("could not import sapbp_catalog: %s" % exc)
    except Exception as exc:
        # --check is the one mode that touches the network, and an expired
        # pr.alm session is its most likely outcome by far. A traceback there
        # reads as a broken script rather than a cookie that needs refreshing,
        # which is exactly the confusion that cost two evenings on the fetcher.
        if type(exc).__name__ == "NotAuthenticated":
            raise SystemExit(
                "\nNOT AUTHENTICATED: %s\n"
                "  Refresh SAPME_COOKIE from a pr.alm.me.sap.com request "
                "and re-run." % exc)
        raise
