#!/usr/bin/env python3
"""Produce Fulcrum's bdcq-questions.json from the brain's BDCQ workbooks.

WHAT THIS RETIRES
    The last thing the Chrome extension was still needed for. It scanned the
    SAP Roadmap Viewer for Business Driven Configuration Questionnaire
    workbooks, downloaded them, parsed them with SheetJS in the browser and
    wrote bdcq-questions.json into the Fulcrum folder.

    The brain already holds all 16 of those workbooks, downloaded from the SAP
    Activate accelerator catalogue -- confirmed as the same questionnaires from
    the same source. With this exporter and export_scope_catalog.py, every file
    the extension produced comes from the brain instead, and the extension can
    go. That matters beyond tidiness: it needs Chrome, a live SAP session and
    the File System Access API, none of which exist on a server, so it was the
    reason these agents could not be hosted.

THE SHAPE, read from bdcq-agent/routes.js rather than guessed
    { totalDomains, generatedAt, domains: [
        { domain, filename, sheets: [ { sheet, headers: [...],
                                        rows: [ {header: value}, ... ] } ] } ] }
    routes.js rejects any file whose `domains` is not an array, counts
    questions as rows per sheet, and treats a two-letter sheet name as a
    country. rows are OBJECTS, not arrays -- bdcq-builder.js calls
    Object.values(row).

THE HEADER IS NOT ROW 1
    These workbooks open with a title block: a logo row, a copyright line, a
    "Last updated" line, then the real header. In the Sales questionnaire the
    header is row 3 and columns A-B are empty padding. Assuming row 1 would
    produce a sheet whose headers are blanks and whose every question is
    keyed by an empty string -- parsed "successfully", useless downstream,
    and silent. So the header row is detected and the choice is reported.

Usage:
    python3.11 scripts/export_bdcq_questions.py --out /path/to/Fulcrum/bdcq/bdcq-questions.json
    python3.11 scripts/export_bdcq_questions.py --verify-only
"""

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
ACCEL    = BASE_DIR / "brain" / "sapactivate" / "raw" / "accelerators.json"
STATE    = BASE_DIR / "brain" / "sapactivate" / "fetch_manifest.json"
FILES    = BASE_DIR / "brain" / "sapactivate" / "files"

BDCQ_TITLE = re.compile(r"business[\s-]*driven\s+configuration\s+questionnaire", re.I)
# How far down to look for the header. The title blocks seen run to row 3;
# ten gives room without wandering into the questions themselves.
HEADER_SCAN_ROWS = 10
# A header row needs this many labelled columns. Two is a Change History
# sheet ("Date", "Description"), which is real but not a questionnaire.
MIN_HEADER_CELLS = 3


def bdcq_files():
    """The downloaded BDCQ workbooks, with the domain each one covers."""
    accel = json.loads(ACCEL.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    out = []
    for r in accel:
        title = r.get("title") or ""
        if not BDCQ_TITLE.search(title):
            continue
        st = state.get(r.get("url")) or {}
        if st.get("status") != "ok" or not st.get("file"):
            continue
        p = FILES / st["file"]
        if not p.exists():
            continue
        # "Business Driven Configuration Questionnaire - Sales.xlsx" -> "Sales".
        # The domain is what getDomainScopeRefs() matches on, so a wrong split
        # here makes the agent find nothing for that module.
        dom = re.sub(r".*questionnaire\s*-?\s*", "", title, flags=re.I)
        dom = re.sub(r"\.(xlsx|xlsm|xls)$", "", dom, flags=re.I).strip()
        out.append({"path": p, "domain": dom or title, "filename": title})
    return sorted(out, key=lambda d: d["domain"])


def find_header(ws):
    """Row index (1-based) of the header, or None.

    The most-populated row in the opening block, provided something follows
    it. Picking blindly would happily choose a merged title cell.
    """
    best, best_n = None, 0
    rows = list(ws.iter_rows(min_row=1, max_row=HEADER_SCAN_ROWS, values_only=True))
    for i, row in enumerate(rows, 1):
        n = sum(1 for c in row if c is not None and str(c).strip())
        if n > best_n:
            best, best_n = i, n
    if best is None or best_n < MIN_HEADER_CELLS:
        return None
    return best


def read_sheet(ws):
    """One sheet -> {sheet, headers, rows} or None if it carries no questions."""
    hrow = find_header(ws)
    if hrow is None:
        return None
    rows_iter = list(ws.iter_rows(min_row=hrow, values_only=True))
    if len(rows_iter) < 2:
        return None
    raw_headers = rows_iter[0]
    # Keep the column INDEX for each real header: the questionnaires pad with
    # empty columns A-B, and zipping a compacted header list against full rows
    # would shift every value one or two columns left.
    cols = [(i, re.sub(r"\s+", " ", str(h)).strip())
            for i, h in enumerate(raw_headers)
            if h is not None and str(h).strip()]
    if len(cols) < MIN_HEADER_CELLS:
        return None

    out_rows = []
    for raw in rows_iter[1:]:
        rec = {}
        for i, name in cols:
            v = raw[i] if i < len(raw) else None
            if v is None:
                continue
            s = str(v).strip()
            if s:
                rec[name] = s
        if rec:
            out_rows.append(rec)
    if not out_rows:
        return None
    return {"sheet": ws.title, "headers": [n for _i, n in cols], "rows": out_rows}


def build():
    try:
        import openpyxl                                  # noqa: PLC0415
    except ImportError:
        raise SystemExit("openpyxl not installed:  pip3.11 install openpyxl")

    domains, skipped = [], []
    for f in bdcq_files():
        try:
            wb = openpyxl.load_workbook(f["path"], data_only=True, read_only=True)
        except Exception as exc:                         # noqa: BLE001
            skipped.append((f["domain"], "unreadable: %s" % type(exc).__name__))
            continue
        sheets = []
        for name in wb.sheetnames:
            s = read_sheet(wb[name])
            if s:
                sheets.append(s)
            else:
                skipped.append(("%s/%s" % (f["domain"], name), "no header + data"))
        wb.close()
        if sheets:
            domains.append({"domain": f["domain"], "filename": f["filename"],
                            "sheets": sheets})
        else:
            skipped.append((f["domain"], "no usable sheet"))
    return domains, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="where to write bdcq-questions.json")
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args()

    domains, skipped = build()
    total_q = sum(len(s["rows"]) for d in domains for s in d["sheets"])

    print("== BDCQ workbooks from the brain")
    print("   domains        %d" % len(domains))
    print("   questions      %d" % total_q)
    for d in domains:
        print("   %-42s %3d sheet(s)  %5d row(s)"
              % (d["domain"][:42], len(d["sheets"]),
                 sum(len(s["rows"]) for s in d["sheets"])))
    if skipped:
        # Named, not silently dropped: a questionnaire that vanishes here looks
        # exactly like one SAP never published.
        print("\n   skipped %d:" % len(skipped))
        for what, why in skipped[:10]:
            print("      %-46s %s" % (str(what)[:46], why))

    print("\n== the agent's contract")
    ok = bool(domains) and all(
        d.get("domain") and isinstance(d.get("sheets"), list) and
        all(s.get("headers") and isinstance(s.get("rows"), list) for s in d["sheets"])
        for d in domains)
    print("   domains is a non-empty array of {domain, filename, sheets}   %s"
          % ("OK" if ok else "FAIL"))
    print("   rows are objects (bdcq-builder calls Object.values)          %s"
          % ("OK" if all(isinstance(r, dict) for d in domains
                         for s in d["sheets"] for r in s["rows"][:3]) else "FAIL"))
    if not ok:
        return 1
    if a.verify_only or not a.out:
        print("\n   nothing written%s." % ("" if a.verify_only else " (pass --out)"))
        return 0

    payload = {
        "totalDomains": len(domains),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domains": domains,
        "_source": {
            "origin": "S4PC brain — SAP Activate accelerator catalogue",
            "workbooks": len(domains),
            "replaces": "Chrome extension scan of the SAP Roadmap Viewer",
        },
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    print("\n   wrote %s  (%.1f MB)" % (out, out.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
