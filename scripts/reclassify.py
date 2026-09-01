#!/usr/bin/env python3
"""
Reclassify the brain's metadata IN PLACE — without re-embedding.

Phase / agent-role / deliverable-type are derived from the document (path +
text), independent of the embedding vector. When the classification rules
improve, re-apply them to the existing index by rewriting the metadata sidecar
and the chunk JSONs — the vectors don't change, so there are NO Bedrock calls.

Classification is done PER DOCUMENT (not per chunk): the ingest classifies each
document once on its whole text and stamps every chunk with that result. This
tool mirrors that — it groups a document's chunks (by doc-id = the chunk-id
prefix), reconstructs the document text from those chunks in order, classifies
once, and applies the result to all of them. (Classifying each 512-word chunk on
its own wrongly dumps every body chunk without a keyword into General.)

Order of the metadata list is preserved, so the 1:1 FAISS alignment is untouched.
metadata.json is backed up to metadata.json.bak first.

Usage:
    python3.11 scripts/reclassify.py --dry-run   # report what would change
    python3.11 scripts/reclassify.py             # apply (with backup)
"""

import sys
import json
import shutil
import argparse
from pathlib import Path
from collections import Counter, defaultdict
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


def _chunk_idx(meta):
    try:
        return int(str(meta.get("id", "")).rsplit("_", 1)[1])
    except Exception:
        return 0


def _doc_text(metas, indices):
    """Reconstruct up to _CONTENT_SCAN chars of a document's text from its chunks
    (in chunk order) — matches how ingest classified on the whole document."""
    parts, total = [], 0
    for i in sorted(indices, key=lambda j: _chunk_idx(metas[j])):
        t = _chunk_text(metas[i])
        if not t:
            continue
        parts.append(t)
        total += len(t)
        if total >= si._CONTENT_SCAN:
            break
    return "\n".join(parts)


def classify_doc(m0, doc_text):
    """(phase, agent_role, deliverable_type, source_system, scope_item_id) for a
    document, from the current rules; m0 is any chunk of it (for path/source)."""
    source = m0.get("source", "") or ""
    rel    = m0.get("relative_path", source) or source
    bpd = si.detect_sap_bpd(source)                   # item 3: BPD regex
    if bpd:
        return ("Reference", "reference", "business_process_doc", "sap_bpd", bpd)
    return (si.detect_phase(rel, doc_text), si.detect_agent_role(rel, doc_text),
            si.detect_deliverable_type(rel, doc_text), "sharepoint",
            m0.get("scope_item_id"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report changes, write nothing")
    args = ap.parse_args()

    if not META_PATH.exists():
        sys.exit(f"No metadata at {META_PATH}. Build the index first (embed_chunks.py).")
    metas = json.loads(META_PATH.read_text(encoding="utf-8"))
    n = len(metas)
    print(f"Loaded {n} vectors' metadata.")

    # group chunk positions by document (doc-id = chunk-id prefix); skip fixed refs
    groups = defaultdict(list)
    for i, m in enumerate(metas):
        if m.get("source_system") == "sap_scope_catalog":
            continue
        doc_id = str(m.get("id", "")).rsplit("_", 1)[0] or m.get("relative_path") or str(i)
        groups[doc_id].append(i)
    print(f"{len(groups)} documents across {sum(len(v) for v in groups.values())} chunks "
          f"(+{n - sum(len(v) for v in groups.values())} fixed scope-catalog vectors).")

    before = Counter(m.get("phase") for m in metas)
    changed = updated_chunks = 0
    for indices in groups.values():
        m0 = metas[min(indices, key=lambda j: _chunk_idx(metas[j]))]
        phase, agent, deliv, src_sys, scope = classify_doc(m0, _doc_text(metas, indices))
        for i in indices:
            m = metas[i]
            if (m.get("phase"), m.get("agent_role"), m.get("deliverable_type"),
                    m.get("source_system")) == (phase, agent, deliv, src_sys):
                continue
            changed += 1
            # update in memory always, so the "after" tally is accurate even in a
            # dry run; only the on-disk writes below are gated by --dry-run
            m["phase"], m["agent_role"], m["deliverable_type"] = phase, agent, deliv
            m["source_system"], m["scope_item_id"] = src_sys, scope
            if args.dry_run:
                continue
            cf = m.get("chunk_file")                    # keep the chunk JSON consistent
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
