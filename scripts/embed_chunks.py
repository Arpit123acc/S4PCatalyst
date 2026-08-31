#!/usr/bin/env python3
"""
Embed the Public Cloud Brain chunks into a FAISS vector index.

Reads every chunk JSON under brain/ (SharePoint chunks + SAP scope-item chunks),
embeds each with Amazon Bedrock Titan Text Embeddings v2 (via the EC2 IAM instance
profile — no keys), and writes a FAISS index plus a parallel metadata sidecar.

Auth: standard AWS credential chain (EC2 IAM instance profile). No API keys.

Outputs (brain/index/):
    faiss.index      the vector index (IndexFlatIP — exact cosine on L2-normed vecs)
    metadata.json    per-vector metadata, aligned by position with the index
    manifest.json    build info (model, dim, count, sources, timestamp)

Usage:
    python3.11 scripts/embed_chunks.py                 # embed all chunks
    python3.11 scripts/embed_chunks.py --limit 50      # smoke test on 50 chunks
    python3.11 scripts/embed_chunks.py --dim 512       # smaller/cheaper vectors

Install:
    pip3.11 install boto3 faiss-cpu numpy
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR   = Path(__file__).resolve().parent.parent
BRAIN_DIR  = BASE_DIR / "brain"
# Chunk sources to embed (add more roots here as the brain grows).
CHUNK_ROOTS = [
    BRAIN_DIR / "sharepoint" / "chunks",     # SharePoint delivery docs
]
# The SAP scope catalog is embedded directly from its committed JSON (no need to
# pre-emit 679 files, and no dependency on the source xlsx on the server).
SCOPE_CATALOG = BASE_DIR / "mcp-server" / "catalog" / "scope_items.json"
INDEX_DIR   = BRAIN_DIR / "index"
INDEX_PATH  = INDEX_DIR / "faiss.index"
META_PATH   = INDEX_DIR / "metadata.json"
MANIFEST    = INDEX_DIR / "manifest.json"

REGION      = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID    = os.environ.get("TITAN_MODEL", "amazon.titan-embed-text-v2:0")
EMBED_DIM   = int(os.environ.get("TITAN_DIM", "1024"))     # v2 supports 256/512/1024
MAX_CHARS   = 40_000                                        # Titan v2 ~8k tokens

# Metadata fields carried from each chunk into the index sidecar.
_META_FIELDS = [
    "id", "source", "source_system", "phase", "agent_role", "deliverable_type",
    "content_type", "relative_path", "folder_path", "scope_item_id", "lob",
    "business_area",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("embed_chunks")


def bedrock_client():
    try:
        import boto3
    except ImportError:
        sys.exit("boto3 not installed. Run: pip3.11 install boto3 faiss-cpu numpy")
    return boto3.client("bedrock-runtime", region_name=REGION)


def embed_text(client, text, dim):
    """Return a Titan v2 embedding (list[float]); retries on throttling."""
    from botocore.exceptions import ClientError
    body = json.dumps({
        "inputText": text[:MAX_CHARS],
        "dimensions": dim,
        "normalize": True,          # L2-normalized → inner product == cosine
    })
    delay = 1.0
    for attempt in range(6):
        try:
            resp = client.invoke_model(modelId=MODEL_ID, body=body)
            return json.loads(resp["body"].read())["embedding"]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "TooManyRequestsException") and attempt < 5:
                time.sleep(delay)
                delay = min(delay * 2, 20)
                continue
            raise


def load_chunks(limit=None):
    """Yield (text, metadata) for every chunk JSON under the chunk roots."""
    files = []
    for root in CHUNK_ROOTS:
        if root.exists():
            files.extend(sorted(root.rglob("*.json")))
    if limit:
        files = files[:limit]
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Skipping unreadable chunk %s: %s", fp.name, e)
            continue
        text = (data.get("text") or "").strip()
        if not text:
            continue
        meta = {k: data[k] for k in _META_FIELDS if k in data}
        meta.setdefault("source_system", "sharepoint")   # multi-source tag
        meta["chunk_file"] = str(fp.relative_to(BRAIN_DIR))
        yield text, meta


def load_scope_items(limit=None):
    """Yield (text, metadata) for each SAP scope item, straight from the catalog."""
    if not SCOPE_CATALOG.exists():
        log.warning("Scope catalog not found (%s) — skipping scope items.", SCOPE_CATALOG)
        return
    catalog = json.loads(SCOPE_CATALOG.read_text(encoding="utf-8"))
    items = catalog.get("scope_items", [])
    if limit:
        items = items[:limit]
    for it in items:
        lobs = ", ".join(sorted({c["lob"] for c in it.get("classifications", []) if c.get("lob")}))
        bas  = ", ".join(sorted({c["business_area"] for c in it.get("classifications", []) if c.get("business_area")}))
        deps = ", ".join(e["to"] for e in it.get("required_scope_items", [])) or "none"
        md   = ", ".join(it.get("required_master_data", [])) or "none"
        text = (
            f"SAP S/4HANA Cloud Public Edition scope item {it['scope_item_id']}: "
            f"{it.get('description','')}. Lines of Business: {lobs}. "
            f"Business Areas: {bas}. Application component: {it.get('component','')}. "
            f"Provisioning: {it.get('provisioning','')}. "
            f"Required scope items (dependencies): {deps}. "
            f"Required master data: {md}. "
            f"Available in {it.get('available_country_count', 0)} country markets."
        )
        yield text, {
            "id":               f"scope_{it['scope_item_id']}",
            "source":           "SAP Scope Item Catalog",
            "source_system":    "sap_scope_catalog",
            "scope_item_id":    it["scope_item_id"],
            "lob":              (it.get("classifications") or [{}])[0].get("lob"),
            "business_area":    (it.get("classifications") or [{}])[0].get("business_area"),
            "deliverable_type": "scope_item_reference",
            "content_type":     "reference",
            "phase":            "Reference",
            "agent_role":       "reference",
            "chunk_file":       "catalog:scope_items",
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Only embed the first N chunks (smoke test)")
    ap.add_argument("--dim", type=int, default=EMBED_DIM, help="Embedding dimensions (256/512/1024)")
    ap.add_argument("--no-scope", action="store_true", help="Skip the SAP scope-item catalog")
    ap.add_argument("--backend", default=None, help="Vector store: faiss (default) | pgvector")
    args = ap.parse_args()

    from vectorstore import get_store    # pluggable backend (faiss/pgvector)
    backend = (args.backend or os.environ.get("BRAIN_BACKEND", "faiss")).lower()

    client = bedrock_client()
    log.info("Bedrock region=%s model=%s dim=%d backend=%s", REGION, MODEL_ID, args.dim, backend)
    try:
        store = get_store(args.dim, load=False, backend=backend)
    except ImportError as e:
        sys.exit(f"Backend '{backend}' deps missing: {e}")

    sources = [("chunks", load_chunks(limit=args.limit))]
    if not args.no_scope:
        sources.append(("scope items", load_scope_items(limit=args.limit)))

    metas = []
    n = 0
    for label, gen in sources:
        start = n
        buf_vecs, buf_metas = [], []
        for text, meta in gen:
            buf_vecs.append(embed_text(client, text, args.dim))
            buf_metas.append(meta)
            n += 1
            if len(buf_vecs) >= 200:              # flush in batches (scales to huge corpora)
                store.add(buf_vecs, buf_metas)
                metas.extend(buf_metas); buf_vecs, buf_metas = [], []
                log.info("  embedded %d...", n)
        if buf_vecs:
            store.add(buf_vecs, buf_metas)
            metas.extend(buf_metas)
        log.info("  %s: %d embedded", label, n - start)

    if not metas:
        sys.exit("Nothing to embed. Run the ingest first "
                 "(python3.11 scripts/sharepoint_ingest.py --local).")

    store.persist()

    def tally(field):
        out = {}
        for m in metas:
            out[m.get(field, "?")] = out.get(m.get(field, "?"), 0) + 1
        return dict(sorted(out.items(), key=lambda x: -x[1]))

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "model": MODEL_ID, "region": REGION, "dimensions": args.dim, "backend": backend,
        "total_vectors": store.count(), "built_utc": datetime.now(timezone.utc).isoformat(),
        "by_source_system": tally("source_system"),
        "by_phase": tally("phase"), "by_agent_role": tally("agent_role"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("Done. %d vectors via %s backend.", store.count(), backend)
    log.info("Sources: %s", tally("source_system"))
    log.info("Phases:  %s", tally("phase"))


if __name__ == "__main__":
    main()
