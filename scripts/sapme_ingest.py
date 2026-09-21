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

WHY XLSX IS SKIPPED ON PURPOSE
    Every test script exists twice -- BOM.169 as .xlsx (SAP Cloud ALM format) and
    BOM.115 as .docx -- 2,811 of each. Taking the .docx and skipping its .xlsx twin
    halves the corpus, drops a dependency, and avoids the real error: a spreadsheet
    is structured data, and embedding a table as prose gives fuzzy hits on
    something that deserves exact lookup. Structured spreadsheets belong in L1.

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
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sharepoint_ingest import chunk            # noqa: E402  same chunk size as the corpus

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN = BASE_DIR / "brain"

MIN_USEFUL_CHARS = 400          # below this it is a stub, a shell or a bad extract


class _Text(HTMLParser):
    """Visible text from HTML. Drops script/style, keeps block boundaries."""

    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "br", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        t = re.sub(r"[ \t]+", " ", "".join(self.out))
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def html_text(raw):
    p = _Text()
    try:
        p.feed(raw.decode("utf-8", errors="replace"))
    except Exception:                                   # noqa: BLE001
        return ""
    return p.text()


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

    cat_p = (BRAIN / "sapbp" / "raw" / "bom_manifest.json" if source == "sapbp"
             else BRAIN / "sapactivate" / "raw" / "accelerators.json")
    facets = {}
    if cat_p.exists():
        for r in json.loads(cat_p.read_text(encoding="utf-8")):
            facets.setdefault(r.get("id"), r)

    out = BRAIN / source / "chunks"
    if not dry:
        out.mkdir(parents=True, exist_ok=True)

    for url, st in state.items():
        if st.get("status") != "ok":
            continue
        path = files_d / st["file"]
        if not path.exists():
            tally["missing_file"] += 1
            continue
        suffix = path.suffix.lower()

        if suffix in (".xlsx", ".xlsm", ".xls"):
            tally["skipped_xlsx"] += 1          # deliberate — see the module docstring
            continue
        if suffix == ".docx" or st.get("kind") == "zip":
            text = docx_text(path)
        elif suffix == ".pdf" or st.get("kind") == "pdf":
            text = pdf_text(path)
        else:
            text = html_text(path.read_bytes())

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
