#!/usr/bin/env python3
"""Produce Fulcrum's scope-catalog.json from the brain, replacing the scraper.

WHAT THIS REPLACES
    The BDCQ and KDD agents read a scope-catalog.json written by a Chrome
    extension that scrapes the SAP for Me DOM. That file is the agents' only
    source of SAP process content, and it arrives by a route with three
    problems: it breaks whenever SAP changes its markup, it needs a human with
    a browser session, and it carries no release identity -- nothing in it says
    which S/4HANA release the text describes.

    The brain already holds the same content from the official EAXService
    OData API: 657 solution processes with HTML descriptions, joined to the
    679-row scope item catalogue from SAP Help. Same data, authoritative
    source, and it refreshes unattended.

WHY THE AGENTS NEED NO CHANGES
    Their contract is four fields on catalog.processes[]: id, name, lob,
    description. The first three map straight across. The fourth is the one
    that matters and the one that would have broken a naive mapping:

        item.description is PROSE, not a name.

    kdd-generator/pre-generate.js runs extractOverview(), extractSteps() and
    extractBenefits() over it, and each scans for exact heading LINES --
    "Overview", "Key Process Flow", "Business Benefits". The brain's scope
    item `description` field is just the title ("Business Event Handling"), so
    mapping description->description would have produced KDDs with empty
    overviews, flows and benefits, silently, for all 657 items.

    The prose lives in processes.json instead, as HTML. Converted with
    html_to_text (the same parser the ingest uses), the headings survive:
    Overview 657/657, Key Process Flow 652/657, Business Benefits 656/657 --
    measured, not assumed, and re-checked on every run by --verify.

WHAT IT DELIBERATELY DOES NOT DO
    It does not change a line of Fulcrum. One file, same shape, same path.
    The seam stays at the file, which keeps the blast radius at zero and
    leaves a live HTTP call to the brain available later if it is ever worth
    the failure modes it brings.

Usage:
    python3.11 scripts/export_scope_catalog.py --out /path/to/Fulcrum/scope-catalog.json
    python3.11 scripts/export_scope_catalog.py --verify-only      # check, write nothing
"""

import sys
import json
import argparse
import collections
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from html_to_text import html_text                  # noqa: E402  ONE html->text rule

BASE_DIR  = Path(__file__).resolve().parent.parent
PROCESSES = BASE_DIR / "brain" / "sapbp" / "raw" / "processes.json"
SCOPE     = BASE_DIR / "mcp-server" / "catalog" / "scope_items.json"
MANIFEST  = BASE_DIR / "brain" / "sapbp" / "manifest.json"

# The exact heading lines kdd-generator/pre-generate.js scans for. If SAP
# restructures its HTML these stop appearing, every extract*() returns empty,
# and the KDDs generate with blank overviews and no error anywhere. That is
# the failure this file exists to make loud.
REQUIRED_HEADINGS = ("Overview", "Key Process Flow", "Business Benefits")
# Below this share, refuse to write. Not 100%: five processes genuinely lack a
# Key Process Flow section today, and a threshold that fails on real data gets
# switched off. Well above what a markup change would leave standing.
MIN_HEADING_SHARE = 0.90


# ONE ROW PER SCOPE ITEM. processes.json carries a row per scope item PER
# COUNTRY: on the production brain 2,456 rows collapse to 679 scope items, up
# to four variants each. Fulcrum's catalogue has no country dimension -- its
# generator emits 15 KDD questions per entry -- so shipping the variants
# un-collapsed produces ~36,800 questions instead of ~10,000, the same scope
# item repeated under different localisations. Plausible-looking and wrong.
#
# Preference order, most general first. XX is SAP's cross-country marker; DE
# is this brain's primary fetch country, so it is the variant with the fullest
# history behind it.
COUNTRY_PREFERENCE = ("XX", "DE", "US", "GB")


def pick_variant(variants):
    """The single row to publish for one scope item. Returns (row, country)."""
    for want in COUNTRY_PREFERENCE:
        for v in variants:
            if (v.get("country_ID") or "").strip().upper() == want:
                return v, want
    # No preferred country present. Take the longest description -- the most
    # content -- and break ties on country code so the choice is stable across
    # runs rather than depending on dict ordering.
    best = max(variants, key=lambda v: (len(v.get("description") or ""),
                                        (v.get("country_ID") or "")))
    return best, (best.get("country_ID") or "?")


def load(p, what):
    if not p.exists():
        raise SystemExit("missing %s (%s). Run the catalogue fetch first." % (p, what))
    return json.loads(p.read_text(encoding="utf-8"))


def build():
    """Join scope-item identity to process prose. Returns (rows, report)."""
    procs = load(PROCESSES, "solution process descriptions")
    scope = load(SCOPE, "scope item catalogue")

    # The catalogue holds THREE things: _meta, scope_items (679) and a
    # SEPARATE retired_scope_items (143). Indexing the top-level dict matched
    # nothing and every row came out with an empty lob -- which
    # getDomainScopeRefs() filters on, so every domain lookup would have
    # returned nothing. It only surfaced because the run reports its join.
    live = scope.get("scope_items") or []
    gone = scope.get("retired_scope_items") or []
    scope_rows = {}
    for r in live:
        scope_rows[str(r.get("scope_item_id", "")).strip()] = r
    for r in gone:
        # Retired rows are a different list, not a flag on the live one. A
        # withdrawn scope item still has prose and still gets scraped today,
        # so keep it and mark it rather than dropping it silently.
        sid = str(r.get("scope_item_id", "")).strip()
        scope_rows.setdefault(sid, dict(r, retired="True"))
    retired_ids = {str(r.get("scope_item_id", "")).strip() for r in gone}

    # Collapse country variants BEFORE anything else, so every count below
    # reports scope items rather than localisations.
    by_id = {}
    for p in procs:
        sid = (p.get("externalId") or "").strip()
        if sid:
            by_id.setdefault(sid, []).append(p)
    chosen, country_used = [], collections.Counter()
    for sid, variants in by_id.items():
        v, c = pick_variant(variants)
        chosen.append(v)
        country_used[c] += 1
    collapsed = len(procs) - len(chosen)

    rows, no_prose, no_scope_row = [], [], []
    for p in chosen:
        sid = (p.get("externalId") or "").strip()
        text = html_text((p.get("description") or "").encode("utf-8"))
        if not text.strip():
            no_prose.append(sid)
            continue
        s = scope_rows.get(sid) or {}
        if not s:
            # The process exists but the scope catalogue has no row for it.
            # Keep it -- the prose is the valuable part -- but say so, because
            # lob will be empty and getDomainScopeRefs() filters on lob.
            no_scope_row.append(sid)
        rows.append({
            "id": sid,
            # The scope catalogue's title is the authoritative one; fall back
            # to the process name when the catalogue has no row.
            "name": s.get("description") or p.get("enName") or p.get("name") or sid,
            "lob": s.get("lob") or "",
            "description": text,
            # Extras Fulcrum does not read today. `retired` is the useful one:
            # SAP withdrew these, and generating a KDD for one is work nobody
            # can use.
            "retired": sid in retired_ids or str(s.get("retired", "")).lower() == "true",
            "businessArea": s.get("business_area") or "",
        })

    rows.sort(key=lambda r: r["id"])
    report = {"processes": len(procs), "written": len(rows),
              "scope_items": len(by_id), "collapsed": collapsed,
              "country_used": country_used,
              "dropped_no_prose": no_prose, "no_scope_row": no_scope_row,
              "retired": sum(1 for r in rows if r["retired"])}
    return rows, report


def verify(rows):
    """Do the agents' parsers still find what they scan for?

    Checked on the OUTPUT, every run, because the input is SAP's HTML and
    nobody tells us when it changes. A catalogue that parses to nothing looks
    exactly like a catalogue that parsed fine until you open the KDD.
    """
    counts = {h: 0 for h in REQUIRED_HEADINGS}
    for r in rows:
        lines = {l.strip() for l in r["description"].split("\n")}
        for h in REQUIRED_HEADINGS:
            if h in lines:
                counts[h] += 1
    n = len(rows) or 1
    worst = min(counts.values()) / n if rows else 0.0
    return counts, worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="where to write scope-catalog.json")
    ap.add_argument("--verify-only", action="store_true",
                    help="run the checks and report; write nothing")
    ap.add_argument("--force", action="store_true",
                    help="write even if the heading check fails (say why in the PR)")
    a = ap.parse_args()

    rows, rep = build()
    counts, worst = verify(rows)

    print("== source")
    print("   process rows            %d" % rep["processes"])
    print("   distinct scope items    %d" % rep["scope_items"])
    if rep["collapsed"]:
        # Not a footnote. processes.json is per scope item PER COUNTRY, and
        # Fulcrum's catalogue has no country dimension, so this line is the
        # difference between ~10,000 KDD questions and ~36,800 with the same
        # scope item repeated under four localisations.
        print("   collapsed country variants %d  -> kept %s"
              % (rep["collapsed"],
                 ", ".join("%s:%d" % kv for kv in rep["country_used"].most_common())))
    print("   scope items joined      %d" % (len(rows) - len(rep["no_scope_row"])))
    print("   written                 %d" % rep["written"])
    if rep["dropped_no_prose"]:
        print("   dropped, no prose       %d  %s" %
              (len(rep["dropped_no_prose"]), ", ".join(rep["dropped_no_prose"][:8])))
    if rep["no_scope_row"]:
        # lob drives getDomainScopeRefs(); an empty lob silently drops the item
        # from every domain lookup, so this is not cosmetic.
        print("   no catalogue row (lob empty) %d  %s" %
              (len(rep["no_scope_row"]), ", ".join(rep["no_scope_row"][:8])))
    print("   retired scope items     %d  (flagged, not removed)" % rep["retired"])

    print("\n== the agents' parsers, checked against this output")
    for h, c in counts.items():
        print("   %-20s %4d / %d" % (h, c, len(rows)))
    ok = worst >= MIN_HEADING_SHARE
    print("   worst heading share     %.1f%%  (floor %.0f%%)  %s"
          % (100 * worst, 100 * MIN_HEADING_SHARE, "OK" if ok else "FAIL"))

    if not ok:
        print("\n   SAP's HTML no longer carries the sections pre-generate.js scans")
        print("   for. Writing this would produce KDDs with empty overviews, key")
        print("   flows and benefits -- with no error at generation time. Refusing.")
        print("   Compare a description against kdd-generator/pre-generate.js")
        print("   extractOverview/extractSteps/extractBenefits before using --force.")
        if not a.force:
            return 1

    # Enforced, not assumed. The unit test asserted unique ids and passed,
    # because the snapshot it ran against happened to hold one country. The
    # production brain holds four, and the check that mattered was the one
    # nothing ran against real data. So assert it here, on every export.
    ids = [r["id"] for r in rows]
    dupes = [i for i, n in collections.Counter(ids).items() if n > 1]
    print("   unique scope item ids   %d of %d rows   %s"
          % (len(set(ids)), len(ids), "OK" if not dupes else "FAIL"))
    if dupes:
        print("\n   %d id(s) appear more than once: %s"
              % (len(dupes), ", ".join(sorted(dupes)[:10])))
        print("   Fulcrum emits 15 KDD questions per entry, so duplicates")
        print("   multiply the output and repeat the same scope item.")
        print("   Refusing to write.")
        return 1

    if a.verify_only:
        print("\n   --verify-only: nothing written.")
        return 0
    if not a.out:
        print("\n   no --out given; nothing written. Pass the Fulcrum path.")
        return 0

    man = {}
    try:
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        pass

    payload = {
        # Fulcrum's UI reads cat.version and compares it against
        # helpers.expectedRelease() -- "2608" for the second half of 2026.
        # Without it the home page showed "CATALOG VERSION undefined" while
        # still claiming "Up to date", because parseInt(undefined) < 2608 is
        # NaN < 2608, which is false. A missing value that reads as current.
        "version": man.get("target_release") or "",
        # Settings reads these two directly. Without them the page showed
        # "Loaded from SAP: Unknown" and "Country: —" for a catalogue that
        # knew both -- the facts were in _source, just not where the UI looks.
        "extractedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Not one country: the export collapses per-country variants to one row
        # per scope item. Reporting a single code would be a lie, so report the
        # mix that was actually kept.
        "country": ", ".join("%s %d" % kv for kv in rep["country_used"].most_common()),
        # Fulcrum reads exactly this key; public/js/app.js rejects a file
        # without it.
        "processes": rows,
        # Provenance the scraped file never had. Which release this text
        # describes is the question nobody could answer before.
        "_source": {
            "origin": "S4PC brain — EAXService OData + SAP Help scope item catalogue",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target_release": man.get("target_release"),
            "internal_version": man.get("internal_version"),
            "count": len(rows),
            "replaces": "Chrome extension DOM scrape of SAP for Me",
        },
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write via a temp file in the same directory and replace, so a reader
    # cannot catch a half-written catalogue. Fulcrum's server.js re-reads on
    # fs.watch, which fires while a plain write is still in progress.
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    print("\n   wrote %s  (%.1f MB)" % (out, out.stat().st_size / 1e6))
    print("   release: %s v%s" % (man.get("target_release"), man.get("internal_version")))
    print("\n   Fulcrum needs no code change: same path, same shape, same four fields.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
