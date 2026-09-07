#!/usr/bin/env python3
"""Spreadsheet extraction and tabular chunking.

WHY THIS EXISTS
    The SharePoint corpus is largely .xlsx -- EDI mapping specs and data-migration
    source sheets -- and those are the documents that name EKKO / VBAK, so they are
    what an agent retrieves when it asks about field mappings. Two silent defects made
    that content ungroundable:

      * extract dropped empty cells (`str(c) for c in row if c is not None`), which
        shifts every later value LEFT, so a value appears under the wrong column's
        header. Not vague -- wrong.
      * chunk() did text.split() and rejoined on single spaces, collapsing every tab
        and newline, so a sheet became one undifferentiated run of words with no row
        or column boundaries at all.

    Both are the kind of thing that never raises and quietly degrades every deliverable
    grounded on a spreadsheet, so they get assertions.

Runs on any laptop: openpyxl writes a real workbook to a temp dir, so this exercises
the actual extractor rather than a stand-in.

Usage:
    python brain-tests/test_table_chunking.py
"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-54s got=%r" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def check_true(label, got):
    check(label, bool(got), True)


def main():
    try:
        import openpyxl
    except ImportError:
        print("openpyxl not installed — skipping (pip install openpyxl)")
        return 0
    import sharepoint_ingest as si

    tmp = Path(tempfile.mkdtemp(prefix="s4pc-xlsx-"))
    xlsx = tmp / "850_Purchase Order_v11.0.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PO Mapping"
    ws.append(["EDI Field", "SAP Field", "Table", "Req", "Len"])
    ws.append(["BEG03", "EBELN", "EKKO", "Y", 10])
    # The row that exposed the shift: two interior blanks.
    ws.append(["BEG05", None, "EKKO", None, 8])
    ws.append(["N104", "LIFNR", "LFA1", "Y", 10])
    # A cell containing a tab and a newline must not forge structure downstream.
    ws.append(["NOTE", "free\ttext\nwith breaks", "", "", ""])
    ws2 = wb.create_sheet("Vendor Mapping")
    ws2.append(["EDI Field", "SAP Field", "Table"])
    ws2.append(["N101", "LIFNR", "LFA1"])
    wb.save(xlsx)

    text = si.extract_text(xlsx)
    lines = text.split("\n")

    print("\nextraction preserves structure")
    check_true("rows are on separate lines", len(lines) >= 7)
    check_true("sheet markers present", lines[0].startswith("[Sheet: PO Mapping"))
    hdr = lines[1].split("\t")
    check("header has 5 columns", len(hdr), 5)

    # THE column-shift regression. "EKKO" must stay in column 3 (index 2).
    shifted = next(ln for ln in lines if ln.startswith("BEG05"))
    cells = shifted.split("\t")
    check("interior blanks keep their column", cells[:4], ["BEG05", "", "EKKO", ""])
    check("'EKKO' is under the Table header", hdr[cells.index("EKKO")], "Table")
    check("trailing blanks are trimmed", shifted.endswith("8"), True)

    note = next(ln for ln in lines if ln.startswith("NOTE"))
    check("a tab inside a cell is flattened", note.count("\t"), 1)
    check_true("a newline inside a cell is flattened", "\n" not in note)

    print("\nchunking keeps rows intact and repeats the header")
    chunks = si.chunk_table(text)
    check_true("at least one chunk per sheet", len(chunks) >= 2)
    for i, c in enumerate(chunks):
        first = c.split("\n")[0]
        check_true("chunk %d starts with its sheet marker" % i,
                   first.startswith("[Sheet: "))
        check_true("chunk %d carries a header row" % i,
                   "EDI Field\tSAP Field" in c)
    # Sheets must never be merged into one chunk -- their columns differ.
    check("sheets are not mixed together",
          sum(1 for c in chunks if "Vendor Mapping" in c.split("\n")[0]), 1)

    print("\nthe prose chunker would have destroyed this (regression evidence)")
    flat = si.chunk(text)[0]
    check("prose chunker collapses all tabs", "\t" in flat, False)
    check("prose chunker collapses all newlines", "\n" in flat, False)
    check_true("tabular chunker keeps them", "\t" in chunks[0] and "\n" in chunks[0])

    print("\nrow budget is respected without splitting a row")
    wide = si.chunk_table("\n".join(
        ["[Sheet: Big]", "A\tB\tC"] + ["v%d\tw%d\tx%d" % (i, i, i) for i in range(600)]))
    check_true("a long sheet splits into several chunks", len(wide) > 1)
    for c in wide:
        body = c.split("\n")[2:]
        check_true("every body row has all 3 columns",
                   all(len(r.split("\t")) == 3 for r in body))

    print()
    if FAILS:
        print("== %d FAILED" % len(FAILS))
        for f in FAILS:
            print("   " + f)
        return 1
    print("== all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
