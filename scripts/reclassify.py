#!/usr/bin/env python3
"""
Reclassify the brain's metadata IN PLACE — without re-embedding.

Phase / agent-role / deliverable-type are derived from the document (path +
text), independent of the embedding vector. When the classification rules
improve, re-apply them to the existing index by rewriting the metadata sidecar
and the chunk JSONs — the vectors don't change, so there are NO Bedrock calls.

Reads brain/index/metadata.json (aligned 1:1 with the FAISS vectors by list
position), re-runs the CURRENT classifiers from sharepoint_ingest on each chunk's
stored (already-masked) text, and updates phase/agent_role/deliverable_type/
source_system in place — order preserved, so FAISS alignment is untouched. Also
updates the matching chunk JSON and refreshes the manifest tallies. metadata.json
is backed up to metadata.json.bak first.

Usage:
    python3.11 scripts/reclassify.py --dry-run   # report what would change
    python3.11 scripts/reclassify.py             # apply (with backup)
"""

import sys
import json
import shutil
import argparse
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

BASE_DIR  = Path(__file__).resolve().parent.parent
BRAIN_DIR = BASE_DIR / "brain"
META_PATH = BRAIN_DIR / "index" / "metadata.json"
MANIFEST  = BRAIN_DIR / "index" / "manifest.json"

sys.path.insert(0, str(BASE_DIR / "scripts"))
import sharepoint_ingest as si          # reuse the (fixed) classifiers


def _chunk_text(meta):
    cf = meta.get("chunk_file")
    if not cf:
        return ""
    try:
        return json.loads((BRAIN_DIR / cf).read_text(encoding="utf-8")).get("text", "")
    except Exception:
        return ""


def classify(meta):
    """Return (phase, agent_role, deliverable_type, source_system, scope_item_id)
    from the current rules, or None for fixed entries that never change."""
    if meta.get("source_system") == "sap_scope_catalog":
        return None                                   # authoritative reference — fixed
    source = meta.get("source", "") or ""
    rel    = meta.get("relative_path", source) or source
    bpd = si.detect_sap_bpd(source)                   # item 3: BPD regex
    if bpd:
        return ("Reference", "reference", "business_process_doc", "sap_bpd", bpd)
    text  = _chunk_text(meta)                          # item 2: content + word-boundary
    return (si.detect_phase(rel, text), si.detect_agent_role(rel, text),
            si.detect_deliverable_type(rel, text), "sharepoint",
            meta.get("scope_item_id"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report changes, write nothing")
    args = ap.parse_args()

    if not META_PATH.exists():
        sys.exit(f"No metadata at {META_PATH}. Build the index first (embed_chunks.py).")
    metas = json.loads(META_PATH.read_text(encoding="utf-8"))
    n = len(metas)
    print(f"Loaded {n} vectors' metadata.")

    before = Counter(m.get("phase") for m in metas)
    changed = updated_chunks = 0
    for m in metas:
        res = classify(m)
        if res is None:
            continue
        phase, agent, deliv, src_sys, scope = res
        cur = (m.get("phase"), m.get("agent_role"), m.get("deliverable_type"),
               m.get("source_system"))
        if cur == (phase, agent, deliv, src_sys):
            continue
        changed += 1
        if args.dry_run:
            continue
        m["phase"], m["agent_role"], m["deliverable_type"] = phase, agent, deliv
        m["source_system"], m["scope_item_id"] = src_sys, scope
        cf = m.get("chunk_file")                        # keep the chunk JSON consistent
        if cf:
            fp = BRAIN_DIR / cf
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                d.update(phase=phase, agent_role=agent, deliverable_type=deliv,
                         source_system=src_sys, scope_item_id=scope)
                fp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                updated_chunks += 1
            except Exception:
                pass

    after = Counter(m.get("phase") for m in metas)
    print(("Would change" if args.dry_run else "Changed"),
          f"{changed}/{n} vectors"
          + ("" if args.dry_run else f" ({updated_chunks} chunk files updated)."))
    print("Phase before:", dict(before.most_common()))
    print("Phase after: ", dict(after.most_common()))
    if args.dry_run:
        print("\nDry run — nothing written. Re-run without --dry-run to apply.")
        return

    assert len(metas) == n, "metadata length changed — aborting to protect FAISS alignment"
    shutil.copy2(META_PATH, META_PATH.with_name("metadata.json.bak"))
    META_PATH.write_text(json.dumps(metas, ensure_ascii=False, indent=2), encoding="utf-8")

    if MANIFEST.exists():
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for field, key in (("source_system", "by_source_system"),
                           ("phase", "by_phase"), ("agent_role", "by_agent_role")):
            man[key] = dict(Counter(m.get(field, "?") for m in metas).most_common())
        man["reclassified_utc"] = datetime.now(timezone.utc).isoformat()
        MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nDone. metadata.json + chunk JSONs + manifest updated "
          "(backup: brain/index/metadata.json.bak).")
    print("FAISS vectors unchanged — no re-embed needed.")


if __name__ == "__main__":
    main()
