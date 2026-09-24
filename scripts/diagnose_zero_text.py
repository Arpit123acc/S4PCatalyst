#!/usr/bin/env python3
"""Which fetched documents yield no usable text, and would Textract help?

Read-only. Fetches nothing, writes nothing but its own report, and never
touches the corpus or the fetch state.

WHY THIS EXISTS
    sapme_ingest counts documents that extract to under MIN_USEFUL_CHARS as
    `too_short` and skips them. The count is visible; the reason is not. A
    plan to run those through AWS Textract was approved on the strength of the
    count alone, and Textract only helps ONE of the causes below.

    Paying to OCR a spreadsheet that openpyxl simply could not open would buy
    nothing, and the bill arrives either way.

THE CAUSES, WHICH NEED DIFFERENT FIXES
    scanned_pdf      pages, no text layer, images present  -> Textract helps
    empty_pdf        pages, no text layer, NO images       -> nothing to OCR
    extractor_failed the library raised; file may be fine  -> fix the extractor
    library_missing  openpyxl / python-docx / pymupdf absent on this host
    genuinely_short  it really is a two-line document      -> nothing to do
    unreadable       truncated or not the format it claims -> re-download

    Only the first is a Textract candidate. Reporting them together is what
    turned "75 documents" into a procurement decision with no evidence under it.

METHOD
    Re-extract with sapme_ingest's OWN functions -- xlsx_text, docx_text,
    pdf_text, html_text -- so "too short" here means exactly what it means
    during an ingest. Writing a second extractor would measure a different
    thing and quietly disagree with the pipeline.

Usage:
    python3.11 scripts/diagnose_zero_text.py
    python3.11 scripts/diagnose_zero_text.py --source sapbp --show 40
    python3.11 scripts/diagnose_zero_text.py --json /tmp/zero-text.json
"""

import sys
import json
import zipfile
import argparse
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pdf_shape(path):
    """Pages, characters and embedded images -- the three facts that decide OCR."""
    try:
        import fitz                                          # noqa: PLC0415
    except ImportError:
        return None
    try:
        with fitz.open(str(path)) as doc:
            pages = doc.page_count
            chars = sum(len(p.get_text()) for p in doc)
            imgs = sum(len(p.get_images(full=True)) for p in doc)
        return {"pages": pages, "chars": chars, "images": imgs}
    except Exception as exc:                                 # noqa: BLE001
        return {"error": type(exc).__name__, "detail": str(exc)[:80]}


def zip_shape(path):
    """Is this really the Office format its extension claims?

    .docx and .xlsx are both PK archives, so the magic bytes cannot tell them
    apart and a mislabelled file reaches the wrong library. The internal part
    names can: word/document.xml against xl/workbook.xml.
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except Exception as exc:                                 # noqa: BLE001
        return {"error": type(exc).__name__}
    kind = ("docx" if any(n.startswith("word/") for n in names) else
            "xlsx" if any(n.startswith("xl/") for n in names) else
            "pptx" if any(n.startswith("ppt/") for n in names) else "unknown")
    return {"real_kind": kind, "parts": len(names),
            "media": sum(1 for n in names if "/media/" in n)}


def classify_cause(path, text, suffix, libs):
    """Why did this produce no text? One cause per document, most specific first."""
    if text is None:
        return "library_missing", {}
    size = path.stat().st_size

    if suffix == ".pdf":
        shape = pdf_shape(path)
        if shape is None:
            return "library_missing", {"need": "pymupdf"}
        if "error" in shape:
            return "unreadable", shape
        if shape["chars"] >= 400:
            # The extractor disagrees with a direct read: that is a bug here or
            # in the dispatch, not a property of the document.
            return "extractor_failed", shape
        if shape["images"] > 0:
            return "scanned_pdf", shape
        return "empty_pdf", shape

    if suffix in (".xlsx", ".xlsm", ".docx", ".pptx"):
        shape = zip_shape(path)
        if "error" in shape:
            return "unreadable", shape
        declared = suffix.lstrip(".")
        if shape["real_kind"] not in (declared, "unknown"):
            # A spreadsheet named .docx reaches python-docx and returns "".
            return "wrong_format", shape
        if not libs.get(declared if declared != "xlsm" else "xlsx", True):
            return "library_missing", shape
        if shape["media"] and len(text or "") == 0:
            return "scanned_pdf", shape      # an Office file of pasted images
        return "genuinely_short", dict(shape, chars=len(text or ""), bytes=size)

    if size < 2000:
        return "genuinely_short", {"bytes": size, "chars": len(text or "")}
    return "extractor_failed", {"bytes": size, "chars": len(text or "")}


def which_libs():
    out = {}
    for name, mod in (("xlsx", "openpyxl"), ("docx", "docx"), ("pdf", "fitz")):
        try:
            __import__(mod)
            out[name] = True
        except ImportError:
            out[name] = False
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="sapbp")
    ap.add_argument("--show", type=int, default=12, help="examples per cause")
    ap.add_argument("--json", help="write the full finding list here")
    ap.add_argument("--threshold", type=int, default=None,
                    help="override MIN_USEFUL_CHARS for the sweep")
    a = ap.parse_args()

    import sapme_ingest as ing                               # noqa: PLC0415

    thresh = a.threshold or ing.MIN_USEFUL_CHARS
    libs = which_libs()
    print("== extractor libraries on this host")
    for k, v in sorted(libs.items()):
        print("   %-6s %s" % (k, "present" if v else "MISSING -- would report as too_short"))
    if not all(libs.values()):
        print("   A missing library makes good documents look empty. Fix that first;")
        print("   the counts below are not trustworthy until every row says present.")

    brain = Path(ing.BRAIN)
    state_p = brain / a.source / "fetch_manifest.json"
    files_d = brain / a.source / "files"
    if not state_p.exists():
        return "no fetch state at %s" % state_p

    state = json.loads(state_p.read_text(encoding="utf-8"))
    todo = [(u, v) for u, v in state.items() if v.get("status") == "ok"]
    print("\n== re-extracting %d fetched document(s) with the ingest's own functions"
          % len(todo))
    print("   threshold: %d chars (MIN_USEFUL_CHARS)" % thresh)

    causes = collections.defaultdict(list)
    ok = 0
    for i, (_url, st) in enumerate(todo, 1):
        if i % 500 == 0:
            print("   [%d/%d]" % (i, len(todo)), flush=True)
        path = files_d / (st.get("file") or "")
        if not st.get("file") or not path.exists():
            causes["missing_file"].append((st.get("file"), {}))
            continue
        suffix = path.suffix.lower()
        try:
            if suffix in (".xlsx", ".xlsm", ".xls"):
                text = ing.xlsx_text(path)
            elif suffix == ".docx":
                text = ing.docx_text(path)
            elif suffix == ".pdf" or st.get("kind") == "pdf":
                text = ing.pdf_text(path)
            elif st.get("kind") == "zip":
                text = ing.docx_text(path)
            else:
                text = ing.html_text(path.read_bytes())
        except Exception as exc:                             # noqa: BLE001
            causes["extractor_raised"].append(
                (path.name, {"error": type(exc).__name__, "detail": str(exc)[:70]}))
            continue

        if text is not None and len(text) >= thresh:
            ok += 1
            continue
        cause, detail = classify_cause(path, text, suffix, libs)
        causes[cause].append((path.name, detail))

    thin = sum(len(v) for v in causes.values())
    print("\n== %d of %d extracted cleanly; %d did not" % (ok, len(todo), thin))
    if not thin:
        print("   Nothing to diagnose.")
        return None

    print("\n== by cause")
    for cause, rows in sorted(causes.items(), key=lambda kv: -len(kv[1])):
        print("   %-18s %5d" % (cause, len(rows)))

    for cause, rows in sorted(causes.items(), key=lambda kv: -len(kv[1])):
        print("\n-- %s (%d)" % (cause, len(rows)))
        for name, detail in rows[:a.show]:
            print("   %-52s %s" % (str(name)[:52], detail))
        if len(rows) > a.show:
            print("   ... and %d more" % (len(rows) - a.show))

    if a.json:
        Path(a.json).write_text(json.dumps(
            {c: [{"file": n, **d} for n, d in v] for c, v in causes.items()},
            indent=1), encoding="utf-8")
        print("\n   wrote %s" % a.json)

    ocr = len(causes.get("scanned_pdf", []))
    print("\n== what to do")
    # Name the Textract population explicitly. The approved plan assumed it was
    # all of them; it is only this row, and everything else has a cheaper fix.
    print("   Textract candidates (scanned_pdf)      %d" % ocr)
    print("   fixable in code, no OCR needed         %d"
          % sum(len(causes.get(c, [])) for c in
                ("extractor_failed", "extractor_raised", "wrong_format", "library_missing")))
    print("   nothing to recover                     %d"
          % sum(len(causes.get(c, [])) for c in
                ("genuinely_short", "empty_pdf", "unreadable", "missing_file")))
    if not ocr:
        print("\n   No scanned PDFs. Textract would cost money and recover nothing --")
        print("   every remaining cause is either a code fix or genuinely empty.")
    else:
        print("\n   Only those %d are worth OCR. Price it on that number, not on the" % ocr)
        print("   total: AWS Textract bills per page, so check the 'pages' field above.")
    return None


if __name__ == "__main__":
    err = main()
    if err:
        sys.exit("\n%s" % err)
