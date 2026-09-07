#!/usr/bin/env python3
"""
S4PC Digital Brain — index builder.

Reads all catalog JSON files (APIs, CDS views, BAdIs, experience_db) and
every output/<RUN-ID>/run.json, then builds the semantic search index used
by semantic_search and find_similar_delivery MCP tools.

Backend (auto-detected):
  • sentence-transformers installed  →  dense embeddings (all-MiniLM-L6-v2)
  • not installed                    →  BM25 TF-IDF fallback (pure stdlib)

Install dense backend:
    pip install sentence-transformers

Usage:
    python mcp-server/vector/build_index.py

Run whenever:
  - sentence-transformers is installed for the first time
  - catalog files change (new APIs / CDS views / BAdIs added)
  - a new pipeline run completes and you want it in the Experience Graph
  - first-time setup
"""

import glob
import json
import os
import sys

# Progress output contains non-ASCII characters; a Windows cp1252 console would raise
# UnicodeEncodeError and make this script exit non-zero even on a successful build.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# allow: `python mcp-server/vector/build_index.py` from project root
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import engine

BASE_DIR    = os.path.dirname(_HERE)           # mcp-server/
CATALOG_DIR = os.path.join(BASE_DIR, "catalog")
OUTPUT_DIR  = os.path.join(os.path.dirname(BASE_DIR), "output")  # project root/output

sys.path.insert(0, CATALOG_DIR)
import db as _catalog_db  # noqa: E402


def _load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


def build_documents():
    """Collect all documents from catalogs + run history. Returns list of engine-ready dicts."""
    docs = []

    # ── Released APIs ────────────────────────────────────────────────────────────
    for api in _catalog_db.load_apis().get("apis", []):
        text = " ".join(filter(None, [
            api.get("name", ""),
            api.get("title", ""),
            api.get("area", ""),
            api.get("protocol", ""),
            api.get("notes", ""),
            " ".join(api.get("key_entities", []) or []),
            " ".join(api.get("operations", []) or []),
        ]))
        docs.append({
            "id":   api["name"],
            "type": "api",
            "text": text,
            "metadata": {
                "name":                   api["name"],
                "title":                  api.get("title"),
                "area":                   api.get("area"),
                "protocol":               api.get("protocol"),
                "hub_url":                api.get("hub_url"),
                "communication_scenario": api.get("communication_scenario"),
                "key_entities":           api.get("key_entities"),
                "operations":             api.get("operations"),
            },
        })

    # ── Released CDS Views ───────────────────────────────────────────────────────
    for view in _catalog_db.load_cds_views().get("views", []):
        replaces = view.get("replaces") or []
        if isinstance(replaces, str):
            replaces = [replaces]
        text = " ".join(filter(None, [
            view.get("name", ""),
            view.get("area", ""),
            view.get("notes", ""),
            " ".join(replaces),
        ]))
        docs.append({
            "id":   view["name"],
            "type": "cds_view",
            "text": text,
            "metadata": {
                "name":     view["name"],
                "area":     view.get("area"),
                "replaces": replaces,
                "notes":    view.get("notes"),
            },
        })

    # ── Released BAdIs ───────────────────────────────────────────────────────────
    for badi in _catalog_db.load_badis().get("badis", []):
        text = " ".join(filter(None, [
            badi.get("name", ""),
            badi.get("title", ""),
            badi.get("area", ""),
            badi.get("business_context", ""),
            badi.get("use_case", ""),
            badi.get("description", ""),
        ]))
        docs.append({
            "id":   badi["name"],
            "type": "badi",
            "text": text,
            "metadata": {
                "name":               badi["name"],
                "title":              badi.get("title"),
                "area":               badi.get("area"),
                "use_case":           badi.get("use_case"),
                "extensibility_type": badi.get("extensibility_type"),
            },
        })

    # ── Experience DB ────────────────────────────────────────────────────────────
    for entry in _catalog_db.load_experience().get("entries", []):
        text = " ".join(filter(None, [
            entry.get("topic", ""),
            entry.get("lesson", ""),
            entry.get("category", ""),
            entry.get("impact", ""),
            " ".join(entry.get("tags", []) or []),
        ]))
        docs.append({
            "id":   entry.get("id") or (entry.get("topic", "")[:40]),
            "type": "experience",
            "text": text,
            "metadata": {
                "id":       entry.get("id"),
                "topic":    entry.get("topic"),
                "category": entry.get("category"),
                "impact":   entry.get("impact"),
                "lesson":   (entry.get("lesson", ""))[:400],
            },
        })

    # ── Layer 3 — past pipeline runs (Experience Graph) ──────────────────────────
    if os.path.isdir(OUTPUT_DIR):
        run_paths = glob.glob(os.path.join(OUTPUT_DIR, "**", "run.json"), recursive=True)
        run_paths += glob.glob(os.path.join(OUTPUT_DIR, "run.json"))
        for run_path in run_paths:
            try:
                run = _load(run_path)
                if not run:
                    continue
                run_id = run.get("run_id") or os.path.basename(os.path.dirname(run_path))
                parts = [
                    run.get("fd_name", ""),
                    run.get("requirement_summary", ""),
                    run.get("approved_approach", ""),
                    run.get("extensibility_mode", ""),
                    run.get("summary", ""),
                    " ".join(run.get("objects_used", []) or []) if isinstance(run.get("objects_used"), list)
                    else str(run.get("objects_used", "")),
                ]
                for val in run.values():
                    if isinstance(val, dict):
                        parts.append(val.get("summary", ""))
                        parts.append(val.get("verdict", ""))
                text = " ".join(p for p in parts if p and isinstance(p, str)).strip()
                if not text:
                    continue
                docs.append({
                    "id":   "run:" + run_id,
                    "type": "delivery",
                    "text": text,
                    "metadata": {
                        "run_id":             run_id,
                        "fd_name":            run.get("fd_name"),
                        "approved_approach":  run.get("approved_approach"),
                        "extensibility_mode": run.get("extensibility_mode"),
                        "objects_used":       run.get("objects_used"),
                        "run_path":           run_path,
                    },
                })
            except Exception:
                continue

    return docs


# Retrieval quality, worst to best. A rebuild must never move the LIVE index down
# this list by accident.
_BACKEND_RANK = {"tfidf": 0, "dense": 1, "bedrock": 2}


def _guard_downgrade(new_backend, allow):
    """Refuse to replace a stronger index with a weaker one.

    engine.backend() reports what THIS HOST is configured for; the live index carries
    the backend it was actually BUILT with, and the two diverge easily -- an index
    built where sentence-transformers or boto3 was installed, rebuilt somewhere it is
    not, silently becomes TF-IDF keyword overlap. Nothing would fail: the build prints
    success, semantic_search keeps answering, and every paraphrased query quietly
    stops matching.

    Same posture as embed_chunks' shrink guard and keyword_index's row-count check:
    a derived index may only be replaced by something at least as good, unless a
    human says otherwise.
    """
    if allow:
        return
    meta = engine.index_meta()
    if not meta.get("present"):
        return                      # no live index — a first build cannot downgrade
    live = meta.get("engine")       # RAW: None means the header never declared one
    hint = ("  Fix the host and re-run: on a Bedrock host set S4PC_VECTOR_BACKEND=bedrock "
            "(no install needed); otherwise pip install sentence-transformers.\n"
            "  Pass --allow-downgrade only if a weaker index is genuinely intended.\n"
            "  Nothing was written.")
    # An UNDECLARED engine is not evidence of a weak index — it is an old header, and
    # index_meta's display default would read it as 'tfidf' and conclude there is
    # nothing to protect. Refuse instead of guessing: this guard exists precisely for
    # the case where we cannot see what we are about to overwrite.
    if live is None:
        sys.exit(
            "REFUSING to rebuild: the live index (%d docs) does not declare which "
            "backend built it, so a downgrade cannot be ruled out.\n"
            "  This host is configured for '%s'.\n%s"
            % (meta.get("docs", 0), new_backend, hint))
    if _BACKEND_RANK.get(new_backend, -1) < _BACKEND_RANK.get(live, -1):
        sys.exit(
            "REFUSING to rebuild: the live index was built with '%s' but this host is "
            "configured for '%s', which is weaker.\n"
            "  Publishing it would silently downgrade semantic_search to keyword "
            "overlap — no error, just worse answers.\n%s" % (live, new_backend, hint))


if __name__ == "__main__":
    allow_downgrade = "--allow-downgrade" in sys.argv
    be = engine.backend()
    print("S4PC Digital Brain — building semantic search index  [backend: %s]" % be)
    if be == "tfidf":
        print("  Tip: pip install sentence-transformers  for dense semantic embeddings")
    _guard_downgrade(be, allow_downgrade)

    docs = build_documents()

    by_type: dict = {}
    for d in docs:
        by_type[d["type"]] = by_type.get(d["type"], 0) + 1

    print("  Documents collected:")
    for t, n in sorted(by_type.items()):
        print("    %-15s %d" % (t, n))
    print("  Total: %d" % len(docs))

    count = engine.build_and_save(docs)
    print("Index written: %d documents -> %s" % (count, engine.INDEX_PATH))
    if be == "dense":
        print("  Embedding matrix: %s" % engine.EMBED_PATH)
    print("Done. MCP tools semantic_search and find_similar_delivery are now active.")
