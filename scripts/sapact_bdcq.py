#!/usr/bin/env python3
"""
Extract the Business Driven Configuration Questionnaires into L1 facts and L4 prose.

WHY THESE MATTER MORE THAN THEIR FILE COUNT SUGGESTS
    Sixteen workbooks, one per line of business, and each row is a configuration
    decision a customer has to make: a scope item, the SSCUI (Self-Service
    Configuration UI) that implements it, the question to ask, and SAP's own
    guidance on answering it. For an agent working the Prepare and Explore
    phases this is not background reading, it is the source material -- and the
    SSCUI id is an object type the brain does not currently model at all.

WHY THE PARSING IS DEFENSIVE
    SAP has not standardised the template across lines of business. Five shapes
    were found in sixteen files, all measured rather than assumed:

      Finance, Manufacturing   Process | Scope Ref | SAP ID | SSCUI Reference | ...
      Manufacturing (2023)     Process | Ref | Expert Configuration ID | ...
      Sourcing & Procurement   ... | Configuration Reference | ...   (not SSCUI)
      Asset, Quality Mgmt      sheet "Content Details"; Solution Processes | ...
      Public Sector            Application Subarea | Configuration Item | ...
      Two Tier                 Scope Item No | Line of Business | Scenario | ...

    An exact-column anchor therefore cannot work. Two earlier attempts proved
    it: keying on "Scope Ref" read 12 of 16 as empty, and keying on "SSCUI
    Reference" read 8 as empty while LOSING one the first attempt had found.
    So the header row is located structurally -- the first row carrying several
    populated cells -- and columns are matched on substrings.

OUTPUT
    brain/sapactivate/raw/bdcq.json     one record per configuration question
    brain/sapactivate/chunks/bdcq_*     L4 prose, tagged phase + scope_item_id

    The records are L1: "which SSCUI configures scope item J58" is an exact
    lookup. The question and guidance text is L4, because it is prose a human
    wrote and an agent should retrieve by meaning.

USAGE
    python3.11 scripts/sapact_bdcq.py --dry-run
    python3.11 scripts/sapact_bdcq.py
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN = BASE_DIR / "brain" / "sapactivate"
STATE = BRAIN / "fetch_manifest.json"
FILES = BRAIN / "files"
OUT_JSON = BRAIN / "raw" / "bdcq.json"
CHUNKS = BRAIN / "chunks"

# Substring matched against a normalised header cell, first hit wins. Order
# matters: "configuration item" must beat the bare "item" of another column.
COLUMNS = {
    "scope":      ("scope ref", "scope item no", "solution processes",
                   "solution process", "scope item", "ref"),
    "config_id":  ("sap id", "expert configuration id", "configuration item"),
    "config_ref": ("sscui reference", "configuration reference",
                   "configuration activity", "business process configuration"),
    "relevant":   ("project relevant", "in scope", "go-live relevance"),
    "area":       ("application subarea", "area", "line of business"),
    "topic":      ("functionality", "topic", "scenario"),
    "definition": ("topic definition", "definition"),
    # "question text" before "question", or the 3-column SAP Build sheet binds
    # to its "Question #" index column and every row reads as a bare number.
    "question":   ("question text", "question"),
    "guidance":   ("solution",),
    "level":      ("level",),
}
# 3, not 5: the SAP Build questionnaire is three columns wide and a higher
# floor silently skipped the whole workbook.
MIN_HEADER_CELLS = 3


def norm(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def find_header(ws, scan=10):
    """The header is the first row in the sheet carrying several populated cells.

    Structural rather than name-based, because the names differ per template and
    a missed header silently yields an empty workbook rather than an error.
    """
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=scan, values_only=True), 1):
        cells = [norm(c) for c in row]
        if sum(1 for c in cells if c) >= MIN_HEADER_CELLS:
            return i, [c.lower() for c in cells]
    return None, []


def map_columns(header):
    out = {}
    for key, needles in COLUMNS.items():
        for n in needles:
            idx = next((i for i, h in enumerate(header) if h and n in h), None)
            if idx is not None and idx not in out.values():
                out[key] = idx
                break
    return out


def parse(path, lob):
    import openpyxl                                     # noqa: PLC0415

    recs = []
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    for ws in wb.worksheets:
        if ws.title.strip().lower() in ("template overview", "change history", "status"):
            continue
        hrow, header = find_header(ws)
        if not header:
            continue
        cols = map_columns(header)
        if "question" not in cols and "config_ref" not in cols:
            continue
        for row in ws.iter_rows(min_row=hrow + 1, values_only=True):
            rec = {k: norm(row[i]) for k, i in cols.items()
                   if i < len(row) and norm(row[i])}
            if not (rec.get("question") or rec.get("config_ref")):
                continue
            rec["lob"] = lob
            rec["scope_items"] = re.findall(r"\b[0-9][0-9A-Z]{2}\b|\b[A-Z][0-9A-Z]{2}\b",
                                            rec.get("scope", ""))
            recs.append(rec)
    wb.close()
    return recs


def as_prose(r):
    bits = [f"SAP S/4HANA Cloud configuration question — {r['lob']}."]
    if r.get("scope"):
        bits.append(f"Scope item(s): {r['scope']}.")
    if r.get("config_ref"):
        bits.append(f"Configuration activity: {r['config_ref']}"
                    + (f" (SSCUI {r['config_id']})." if r.get("config_id") else "."))
    for k, label in (("area", "Area"), ("topic", "Topic")):
        if r.get(k):
            bits.append(f"{label}: {r[k]}.")
    if r.get("definition"):
        bits.append(r["definition"])
    if r.get("question"):
        bits.append(f"Question to the customer: {r['question']}")
    if r.get("guidance"):
        bits.append(f"SAP guidance: {r['guidance']}")
    return " ".join(bits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not STATE.exists():
        sys.exit("no fetch_manifest.json — run sapme_fetch.py first")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    books = [v for v in state.values()
             if v.get("status") == "ok" and "uestionnaire" in (v.get("title") or "")]
    if not books:
        sys.exit("no questionnaires fetched — "
                 "python3.11 scripts/sapme_fetch.py --source sapactivate --match questionnaire")

    all_recs, per, missed = [], {}, []
    for v in sorted(books, key=lambda x: x["title"]):
        lob = (v["title"].replace("Business Driven Configuration Questionnaire", "")
               .replace(".xlsx", "").strip(" -") or v["title"])
        try:
            recs = parse(FILES / v["file"], lob)
        except Exception as exc:                        # noqa: BLE001
            missed.append((lob, str(exc)[:60]))
            continue
        per[lob] = len(recs)
        all_recs.extend(recs)

    scopes = {s for r in all_recs for s in r["scope_items"]}
    sscuis = {r["config_id"] for r in all_recs if r.get("config_id", "").isdigit()}
    print(f"workbooks               : {len(books)}")
    print(f"configuration questions : {len(all_recs)}")
    print(f"distinct scope items    : {len(scopes)}")
    print(f"distinct SSCUI ids      : {len(sscuis)}")
    print("\nper line of business:")
    for k, n in sorted(per.items(), key=lambda x: -x[1]):
        print(f"   {n:>4}  {k[:52]}")
    for lob, err in missed:
        print(f"   FAIL  {lob}: {err}")

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(all_recs, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    CHUNKS.mkdir(parents=True, exist_ok=True)
    written = 0
    for i, r in enumerate(all_recs):
        text = as_prose(r)
        if len(text) < 120:
            continue
        rec = {
            "id": f"bdcq_{i:05d}",
            "text": text,
            "source": f"Business Driven Configuration Questionnaire — {r['lob']}",
            "source_system": "sap_activate",
            "content_type": "configuration_questionnaire",
            "deliverable_type": "configuration",
            "phase": "Prepare, Explore",
            "lob": r["lob"],
        }
        if r["scope_items"]:
            rec["scope_item_id"] = r["scope_items"][0]
        (CHUNKS / f"bdcq_{i:05d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1
    print(f"\nwrote {OUT_JSON}")
    print(f"wrote {written} chunks -> {CHUNKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
