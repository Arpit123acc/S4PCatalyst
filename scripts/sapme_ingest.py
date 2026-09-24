#!/usr/bin/env python3
"""
Turn the fetched SAP material into brain chunks, tagged for multi-agent retrieval.

WHAT THIS ADDS THAT THE BRAIN DOES NOT ALREADY HAVE
    mcp-server/catalog/scope_items.json already holds 679 scope items, and
    embed_chunks.load_scope_items() already indexes them -- but each one embeds as
    a single synthesised sentence built from its fields. Measured 2026-09-21: all
    657 Process Navigator processes are ALREADY in that catalogue, so this adds no
    new scope items at all. What it adds is PROSE. Scope item 16R currently embeds
    as the line "Bank Integration with SAP Multi-Bank Connectivity"; Process
    Navigator carries 1,599 characters of Overview, Key Process Flow and Business
    Benefits for the same item. That is the win -- depth on what is already there,
    not breadth.

FACETS, AND WHY THEY MATTER MORE THAN THE TEXT
    _META_FIELDS already includes `phase` and `agent_role`, so the SAP Activate
    tagging drops straight in with no schema change. An onboarding agent then
    filters instead of hoping: phase=Explore + agent_role="Testing Expert" is an
    exact set, where a similarity search over the same corpus is a guess.

BOTH FORMATS ARE INGESTED, AND THAT WAS A CORRECTION
    The counts match at 2,811 .xlsx and 2,811 .docx, which looked like the same
    test script twice. Opening a pair showed otherwise: the .docx is the narrative
    (purpose, prerequisites, roles, master data) and the .xlsx is the Cloud ALM
    test-case definition -- Activity/Action steps with instructions and expected
    results, 116k characters from a single file. They are complementary, so both
    are ingested. See xlsx_text() for what the workbooks do NOT contain.

NO PII MASKING
    Same call as webdocs_ingest.py: this is SAP's own published material, not
    client delivery documents. sharepoint_ingest.mask() is for the latter.

USAGE
    python3.11 scripts/sapme_ingest.py --dry-run
    python3.11 scripts/sapme_ingest.py
    python3.11 scripts/sapme_ingest.py --source sapactivate

    Then, and this is not optional:
        python3.11 scripts/embed_chunks.py        # roots already wired in
        pm2 restart s4pc-mcp
"""

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from html_to_text import html_text       # noqa: E402  ONE html->text rule
from sharepoint_ingest import chunk            # noqa: E402  same chunk size as the corpus

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN = BASE_DIR / "brain"

MIN_USEFUL_CHARS = 400          # below this it is a stub, a shell or a bad extract

# Limits for expanding a plain .zip. An archive is attacker-shaped even when it
# is not an attack: the catalogue links a 19.7 MB bundle, and nothing says a
# future one will not be 2 GB unpacked. Checked against the declared
# uncompressed size BEFORE reading, so a zip bomb is refused rather than
# survived.
MAX_ARCHIVE_MEMBERS = 250
MAX_MEMBER_BYTES = 60 * 1024 * 1024
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
# Which members are worth extracting. Anything else in a bundle -- images, the
# .msg files, licence stubs -- costs time and contributes no prose.
ARCHIVE_MEMBER_SUFFIXES = {".docx", ".xlsx", ".xlsm", ".pdf", ".txt", ".html", ".htm", ".csv"}


def extract_text(path, kind=None):
    """Route a downloaded file to the right extractor. THE ONE DISPATCH.

    Callers must not re-derive this. diagnose_zero_text.py copied the branches
    inline while importing the extractor functions, which looked like reuse and
    was not: when the .zip branch was added here, the diagnostic kept routing
    archives to python-docx and went on reporting a document the ingest had
    already recovered. A rule in two code paths, and the copy is what rots --
    the third instance of that shape in one day, this one self-inflicted.

    `kind` is the fetch state's classification, used only where the filename
    carries no usable suffix.
    """
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return xlsx_text(path)
    if suffix == ".docx":
        return docx_text(path)
    if suffix == ".pdf" or kind == "pdf":
        return pdf_text(path)
    if suffix == ".zip":
        return archive_text(path)
    if kind == "zip":
        # PK magic bytes, no useful extension: an Office file OR a plain
        # archive, and only the internal part names tell them apart. This used
        # to go straight to docx_text, so a 19.7 MB bundle of user guides
        # raised inside python-docx, returned "", and counted as too_short --
        # the whole archive lost without an error.
        return docx_text(path) if is_office_package(path) else archive_text(path)
    return html_text(path.read_bytes())


def is_office_package(path):
    """True for .docx/.xlsx/.pptx, which are themselves zips.

    classify() returns "zip" from the PK magic bytes for BOTH an Office file
    and a plain archive, so magic bytes cannot route them. The internal part
    names can: word/, xl/, ppt/. Without this an archive branch would swallow
    every Word document whose URL happened to carry no extension.
    """
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.startswith(("word/", "xl/", "ppt/")) for n in z.namelist())
    except Exception:                                    # noqa: BLE001
        return False


def archive_text(path):
    """Text from every readable document inside a plain .zip.

    WHY THIS EXISTS
        The catalogue links bundles, not only single documents --
        Two-Tier_ERP_Assets_User_Guides_Templates.zip is 19.7 MB of user guides
        and templates. It reached docx_text, which is python-docx, which raised
        and returned "", so the file counted as too_short and 19.7 MB of content
        was dropped in silence. Nothing failed loudly enough to notice.

    ONE LEVEL ONLY. A nested archive is skipped and named in the output rather
    than recursed into: unbounded recursion over untrusted zips is how a
    decompression bomb gets in, and no catalogue bundle so far needs it.

    Members are concatenated with a header naming each one, so a retrieval hit
    inside a bundle can still say which document it came from.
    """
    try:
        zf = zipfile.ZipFile(path)
    except Exception:                                    # noqa: BLE001
        return ""

    parts, total, skipped = [], 0, Counter()
    with zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        for info in members[:MAX_ARCHIVE_MEMBERS]:
            suffix = Path(info.filename).suffix.lower()
            if suffix not in ARCHIVE_MEMBER_SUFFIXES:
                skipped[suffix or "(none)"] += 1
                continue
            # Declared size, checked before reading a single byte.
            if info.file_size > MAX_MEMBER_BYTES:
                skipped["oversized"] += 1
                continue
            if total + info.file_size > MAX_ARCHIVE_BYTES:
                skipped["budget"] += 1
                break
            try:
                blob = zf.read(info)
            except Exception:                            # noqa: BLE001
                skipped["unreadable"] += 1
                continue
            total += len(blob)
            with tempfile.TemporaryDirectory() as td:
                # The extractors take a path, and openpyxl/python-docx both
                # want a real file rather than a stream.
                tmp = Path(td) / ("m" + (suffix or ".bin"))
                tmp.write_bytes(blob)
                if suffix in (".xlsx", ".xlsm"):
                    text = xlsx_text(tmp)
                elif suffix == ".docx":
                    text = docx_text(tmp)
                elif suffix == ".pdf":
                    text = pdf_text(tmp)
                elif suffix in (".html", ".htm"):
                    text = html_text(blob)
                else:
                    text = blob.decode("utf-8", errors="replace")
            if text:
                parts.append("## %s\n%s" % (info.filename, text))
    if len(members) > MAX_ARCHIVE_MEMBERS:
        parts.append("[%d further member(s) not read: archive member cap]"
                     % (len(members) - MAX_ARCHIVE_MEMBERS))
    return "\n\n".join(parts)


def docx_text(path):
    try:
        import docx                                      # noqa: PLC0415
    except ImportError:
        return None
    try:
        d = docx.Document(str(path))
    except Exception:                                    # noqa: BLE001
        return ""
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    # A test script's substance is its step table, and python-docx keeps tables
    # out of .paragraphs entirely -- they would be dropped in silence.
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def pdf_text(path):
    try:
        import fitz                                      # noqa: PLC0415
    except ImportError:
        return None                                      # reported, not fatal
    try:
        with fitz.open(str(path)) as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception:                                    # noqa: BLE001
        return ""


# Columns of the SAP Cloud ALM test-case sheet (BOM.169). Verified against a real
# file 2026-09-21 rather than assumed.
TC_KEY = "Test Case GUID"
TC_CARRY = ("Test Case GUID", "Test Case Name*", "[Scope GUID]", "[Scope Name]",
            "[Solution Process GUID]", "[Solution Process Name]",
            "[Solution Process Flow GUID]", "[Solution Process Flow Name]",
            "Test Case Status")


def _cell(v):
    """Cell text. Instructions arrive as HTML fragments, so strip them."""
    s = "" if v is None else str(v)
    return html_text(s.encode("utf-8")) if "<" in s and ">" in s else s.strip()


def xlsx_text(path):
    """Readable prose from a Cloud ALM test-case workbook.

    An earlier pass skipped .xlsx entirely, assuming BOM.169 was just BOM.115 in
    another format -- the counts matched at 2,811 each. Opening a pair disproved
    it. The .docx is the narrative test script (purpose, prerequisites, roles);
    the .xlsx is the Cloud ALM test-case definition, and it is the richer of the
    two: one file rendered to 116,522 characters of Activity/Action steps with
    instructions and expected results, against the docx's narrative. Do not skip
    it again.

    It yields NO L1 edges, though, and that was also measured rather than
    assumed. Every bracketed column -- [Scope GUID], [Solution Process GUID],
    Activity Target Name/URL -- is empty in all 333 rows, because SAP ships these
    as IMPORT TEMPLATES for Cloud ALM and the customer fills those in. Only
    Activity Title, Action Title, Action Instructions and Action Expected Result
    carry data. The scope_item -> process -> application chain therefore has to
    come from the OData entities (SolutionProcessDiagramApplicationFilter), not
    from here.

    Sheets that are not test cases fall back to a plain row rendering.
    """
    try:
        import openpyxl                                  # noqa: PLC0415
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception:                                    # noqa: BLE001
        return ""

    out = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header, cols = None, []
        buf = []
        for row in rows:
            vals = ["" if c is None else str(c).strip() for c in row]
            if header is None:
                if any(v == TC_KEY for v in vals):
                    header, cols = True, vals
                else:
                    buf.append(row)
                continue
            rec = {c: _cell(v) for c, v in zip(cols, row) if c}
            if not any(rec.values()):
                continue
            out.append(rec)

        if header is None:
            # Not a test-case sheet. Render rows as text so reference workbooks
            # still contribute something, without pretending to understand them.
            lines = [" | ".join(str(c) for c in r if c not in (None, ""))
                     for r in buf]
            body = "\n".join(l for l in lines if l.strip())
            if body:
                out.append({"__plain__": f"[{ws.title}]\n{body}"})
    wb.close()

    # Forward-fill: the test-case columns are written once, on the first row of
    # each case, and left blank on its remaining Activity/Action rows.
    carried, parts, current = {}, [], None
    for rec in out:
        if "__plain__" in rec:
            parts.append(rec["__plain__"])
            continue
        for k in TC_CARRY:
            if rec.get(k):
                carried[k] = rec[k]
        name = carried.get("Test Case Name*")
        if name and name != current:
            current = name
            parts.append(f"\n\nTest case: {name}")
            scope, proc = carried.get("[Scope Name]"), carried.get("[Solution Process Name]")
            if scope:
                parts.append(f"Scope: {scope}")
            if proc:
                parts.append(f"Solution process: {proc}")
        act, action = rec.get("Activity Title*"), rec.get("Action Title*")
        if act:
            parts.append(f"\nActivity: {act}")
        if rec.get("Activity Target Name"):
            parts.append(f"Application: {rec['Activity Target Name']}")
        if action:
            parts.append(f"Step: {action}")
        if rec.get("Action Instructions*"):
            parts.append(rec["Action Instructions*"])
        if rec.get("Action Expected Result"):
            parts.append(f"Expected result: {rec['Action Expected Result']}")

    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()


def write_chunks(out_dir, doc_id, text, meta, tally):
    pieces = chunk(text)
    for i, body in enumerate(pieces):
        rec = dict(meta)
        rec["id"] = f"{doc_id}_{i}"
        rec["text"] = body
        (out_dir / f"{doc_id}_{i}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    tally["chunks"] += len(pieces)
    return len(pieces)


def ingest_processes(dry, tally):
    """The 657 solution-process descriptions -- the highest-value text here."""
    src = BRAIN / "sapbp" / "raw" / "processes.json"
    if not src.exists():
        print("   (no processes.json — run sapbp_catalog.py)")
        return
    out = BRAIN / "sapbp" / "chunks"
    if not dry:
        out.mkdir(parents=True, exist_ok=True)
    rows = json.loads(src.read_text(encoding="utf-8"))
    for r in rows:
        text = html_text((r.get("description") or "").encode("utf-8"))
        if len(text) < MIN_USEFUL_CHARS:
            tally["process_too_short"] += 1
            continue
        scope = r.get("externalId")
        meta = {
            "source": f"SAP Best Practices — {r.get('name')}",
            "source_system": "sap_best_practices",
            "content_type": "solution_process",
            "deliverable_type": "business_process_design",
            "scope_item_id": scope,
            "lob": r.get("businessProcessGroupName"),
            "relative_path": f"processnavigator/{scope}",
        }
        tally["processes"] += 1
        if not dry:
            write_chunks(out, f"sapbp_proc_{scope}", text, meta, tally)


def ingest_files(source, dry, tally):
    """Documents downloaded by sapme_fetch.py, joined back to their facets."""
    state_p = BRAIN / source / "fetch_manifest.json"
    files_d = BRAIN / source / "files"
    if not state_p.exists():
        print(f"   ({source}: nothing fetched yet)")
        return
    state = json.loads(state_p.read_text(encoding="utf-8"))

    # THE FILES ON DISK ARE THE GROUND TRUTH, NOT THE MANIFEST.
    # Both fetchers read the whole manifest, mutate it in memory and write it
    # back, so running two at once loses whatever the slower one did not know
    # about. Measured 2026-09-21: 236 help documents on disk against 196 in the
    # manifest -- 40 real files that ingest would simply never have seen, with
    # nothing anywhere reporting a problem. Rather than rely on the fetchers
    # never overlapping, anything present in files/ is adopted here.
    known = {str(v.get("file")) for v in state.values() if v.get("file")}
    adopted = 0
    for f in sorted(files_d.iterdir()) if files_d.is_dir() else []:
        if f.name in known or not f.is_file():
            continue
        # help_<id>.<ext> and <id>.<ext> both carry the catalogue id in the stem
        stem = f.stem[5:] if f.name.startswith("help_") else f.stem
        state[f"adopted://{f.name}"] = {
            "status": "ok", "id": stem, "file": f.name,
            "kind": "html" if f.suffix == ".html" else f.suffix.lstrip("."),
            "bytes": f.stat().st_size, "title": None, "adopted": True,
        }
        adopted += 1
    if adopted:
        print(f"   adopted {adopted} file(s) present on disk but missing from the manifest")

    cat_p = (BRAIN / "sapbp" / "raw" / "bom_manifest.json" if source == "sapbp"
             else BRAIN / "sapactivate" / "raw" / "accelerators.json")
    facets = {}
    if cat_p.exists():
        for r in json.loads(cat_p.read_text(encoding="utf-8")):
            facets.setdefault(r.get("id"), r)

    out = BRAIN / source / "chunks"
    if not dry:
        out.mkdir(parents=True, exist_ok=True)

    # ANNOUNCED BEFORE THE WORK, not after. Extraction is the slow part -- one
    # Cloud ALM workbook renders to 116,522 characters -- so logging only on
    # completion leaves the run silent for minutes at a time with nothing to
    # distinguish it from a hang. It was read as one. sharepoint_ingest carries
    # the same note and the same fix; this is the file that still lacked it.
    todo = [(u, v) for u, v in state.items() if v.get("status") == "ok"]
    total = len(todo)
    for i, (url, st) in enumerate(todo, 1):
        path = files_d / st["file"]
        if i == 1 or i % 250 == 0 or i == total:
            print("   [%d/%d] %s" % (i, total, str(st.get("file"))[:64]), flush=True)
        if not path.exists():
            tally["missing_file"] += 1
            continue
        text = extract_text(path, st.get("kind"))

        if text is None:
            tally["needs_pymupdf"] += 1
            continue
        if not text or len(text) < MIN_USEFUL_CHARS:
            tally["too_short"] += 1
            continue

        f = facets.get(st.get("id"), {})
        if source == "sapbp":
            meta = {
                "source": st.get("title") or f.get("name") or path.stem,
                "source_system": "sap_best_practices",
                "content_type": "test_script" if "test script" in
                                (f.get("name") or "").lower() else "accelerator",
                "deliverable_type": "test_script" if "test script" in
                                    (f.get("name") or "").lower() else "configuration",
                "scope_item_id": f.get("scope_item"),
                "lob": f.get("lob"),
                "relative_path": f"processnavigator/files/{path.name}",
            }
        else:
            meta = {
                "source": st.get("title") or f.get("title") or path.stem,
                "source_system": "sap_activate",
                "content_type": "accelerator",
                "deliverable_type": "methodology",
                # phase and agent_role are existing _META_FIELDS, so SAP's own
                # tagging lands with no schema change. Multi-valued facets are
                # joined rather than dropped: an accelerator that serves both
                # Prepare and Explore must be findable from either.
                "phase": ", ".join(f.get("phase") or []) or None,
                "agent_role": ", ".join(f.get("role") or []) or None,
                "relative_path": f"roadmapviewer/files/{path.name}",
            }
            meta = {k: v for k, v in meta.items() if v is not None}

        doc_id = f"{source}_{hashlib.sha1(url.encode()).hexdigest()[:12]}"
        tally[f"{source}_docs"] += 1
        tally[f"{source}_{suffix.lstrip('.') or 'web'}"] += 1
        if not dry:
            write_chunks(out, doc_id, text, meta, tally)



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["sapbp", "sapactivate", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tally = Counter()
    if a.source in ("sapbp", "all"):
        print("== solution process descriptions")
        ingest_processes(a.dry_run, tally)
        print("== sapbp documents")
        ingest_files("sapbp", a.dry_run, tally)
    if a.source in ("sapactivate", "all"):
        print("== sapactivate documents")
        ingest_files("sapactivate", a.dry_run, tally)

    print("\n== tally")
    for k, v in sorted(tally.items()):
        print(f"   {v:>7}  {k}")
    if tally.get("needs_pymupdf"):
        print("\n   PDFs skipped — pip3.11 install pymupdf to include them.")
    if a.dry_run:
        print("\nDRY RUN — no chunks written.")
    else:
        print("\nNext: python3.11 scripts/embed_chunks.py && pm2 restart s4pc-mcp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
