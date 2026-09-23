#!/usr/bin/env python3
"""Project each scope item's process flow into a searchable L4 chunk.

WHY THIS EXISTS
    Process flows live in L1 only. The BPMN is distilled to ordered steps and
    roles, joined onto the scope item, and served exactly by lookup_process —
    but none of it is embedded, so asking search_brain for "the process flow for
    supplier invoicing" finds prose ABOUT process flows (the BPD .docx files)
    and never the flow itself. The two-hop path lookup_scope_item ->
    lookup_process works and is invisible to anyone who does not already know it.

    One chunk per scope item fixes the discoverability without touching L1.

DERIVED, NOT A SECOND SOURCE OF TRUTH
    Every chunk is generated from process_index.json on each build and the file
    says so in its own text: the authoritative ordered list is
    lookup_process(scope_item=...). L1 remains the record; this is a signpost to
    it, the same way a release verdict is the governed fact and the document
    that mentions the object is not.

    That distinction is why this is regenerated rather than maintained. A fact
    kept in two places drifts; a fact projected into a second form on every
    build cannot, and if it ever does that is a build bug with one obvious fix.

WHY ITS OWN CHUNK ROOT
    brain/sapflows/chunks, not brain/sapbp/chunks. The sapbp root belongs to the
    document ingest, which already has orphan-pruning keyed on "is there a source
    file for this chunk" — and these chunks have no source document by design.
    Nothing prunes them today; keeping them out of that tree means nothing can.

Usage:
    python3.11 scripts/sapbp_build_flow_chunks.py
    python3.11 scripts/sapbp_build_flow_chunks.py --dry-run
"""

import json
import shutil
import argparse
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
INDEX    = BASE_DIR / "brain" / "sapbp" / "raw" / "process_index.json"
OUT_DIR  = BASE_DIR / "brain" / "sapflows" / "chunks"
SOURCE_SYSTEM = "sap_process_flow"


def chunk_text(r):
    """The searchable prose. Written so a plain-English question matches it.

    Leads with the words someone would actually type -- "process flow for X" --
    then the steps and roles, then the Fiori apps, which are often how a person
    recognises a process they cannot name.
    """
    sid  = r["scope_item"]
    # names already carry "(2LH)"; a second parenthetical reads badly
    name = (r.get("name") or sid).replace("(%s)" % sid, "").strip(" -—")
    out = ["Process flow for scope item %s — %s." % (sid, name)]
    if r.get("lob"):
        out.append(" Line of business: %s." % r["lob"])
    if r.get("target_release"):
        out.append(" SAP release %s." % r["target_release"])
    steps = r.get("steps") or []
    roles = r.get("roles") or []
    apps  = r.get("applications") or []
    caps  = r.get("capabilities") or []
    if steps:
        out.append("\n\nProcess steps in order: " + "; ".join(steps) + ".")
    if roles:
        out.append("\n\nBusiness roles involved: " + "; ".join(roles) + ".")
    if apps:
        out.append("\n\nFiori applications used: " + "; ".join(apps) + ".")
    if caps:
        areas = []
        for c in caps:
            a = c.get("business_area")
            if a and a not in areas:
                areas.append(a)
        if areas:
            out.append("\n\nBusiness areas: " + "; ".join(areas) + ".")
    if r.get("diagrams"):
        out.append("\n\nProcess flow diagrams: " + "; ".join(r["diagrams"]) + ".")
    out.append("\n\nThis is a searchable summary generated from the process index. "
               "The authoritative ordered list of steps and roles comes from "
               "lookup_process(scope_item=\"%s\")." % sid)
    return "".join(out)


def build(dry=False):
    if not INDEX.exists():
        raise SystemExit("%s missing — run sapbp_build_process_index.py first" % INDEX)
    doc  = json.loads(INDEX.read_text(encoding="utf-8"))
    rows = doc.get("scope_items") or []
    meta = doc.get("_meta") or {}

    written, skipped, chars = 0, 0, 0
    if not dry:
        # Rebuilt wholesale: a scope item that loses its flow must lose its
        # chunk too, or the index keeps serving a flow the source no longer has.
        if OUT_DIR.exists():
            shutil.rmtree(OUT_DIR)
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    for r in rows:
        if not (r.get("steps") or r.get("roles")):
            skipped += 1                      # nothing to describe
            continue
        sid = r["scope_item"]
        text = chunk_text(r)
        chars += len(text)
        written += 1
        if dry:
            continue
        (OUT_DIR / ("%s.json" % sid)).write_text(json.dumps({
            "id": "flow_%s" % sid,
            "text": text,
            "source": "Process flow — %s (%s)" % (r.get("name") or sid, sid),
            # Explicit: embed_chunks defaults an absent source_system to
            # "sharepoint", which would file these as delivery documents.
            "source_system": SOURCE_SYSTEM,
            "deliverable_type": "process_flow",
            "content_type": "process_flow",
            "scope_item_id": sid,
            "lob": r.get("lob"),
            "relative_path": "sapflows/chunks/%s.json" % sid,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== process flows from %s (release %s)" % (INDEX.name, meta.get("target_release")))
    print("   scope items in index   %d" % len(rows))
    print("   flow chunks written    %d" % written)
    print("   skipped (no steps)     %d" % skipped)
    print("   average chunk length   %d chars" % (chars // max(written, 1)))
    if dry:
        print("\nDRY RUN — nothing written.")
    else:
        print("\n   wrote %s" % OUT_DIR)
        print("   run embed_chunks.py to index them")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    return build(ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
