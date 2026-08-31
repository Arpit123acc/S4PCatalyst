#!/usr/bin/env python3
"""
Search the Public Cloud Brain (FAISS + Bedrock Titan).

Embeds a query with Amazon Bedrock Titan (EC2 IAM instance profile — no keys),
runs a cosine similarity search over the FAISS index built by embed_chunks.py,
and returns the top matching chunks with their phase / agent / source metadata.
Supports metadata filters (phase, agent role, deliverable type) applied after the
vector search so the survivors keep their true similarity ranking.

Usable two ways:
  * CLI:      python3.11 scripts/brain_search.py "how do we do cutover" --phase Deploy
  * import:   from brain_search import search;  hits = search("...", k=5, phase="Realize")

Install:
    pip3.11 install boto3 faiss-cpu numpy
"""

import os
import sys
import json
import argparse
from pathlib import Path
from functools import lru_cache

BASE_DIR   = Path(__file__).resolve().parent.parent
INDEX_DIR  = BASE_DIR / "brain" / "index"
INDEX_PATH = INDEX_DIR / "faiss.index"
META_PATH  = INDEX_DIR / "metadata.json"

REGION     = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID   = os.environ.get("TITAN_MODEL", "amazon.titan-embed-text-v2:0")
MAX_CHARS  = 40_000


@lru_cache(maxsize=1)
def _load():
    """Load and cache the FAISS index + metadata + Bedrock client."""
    try:
        import faiss, boto3          # noqa: F401
    except ImportError:
        sys.exit("Missing deps. Run: pip3.11 install boto3 faiss-cpu numpy")
    if not INDEX_PATH.exists():
        sys.exit(f"No index at {INDEX_PATH}. Build it first: "
                 f"python3.11 scripts/embed_chunks.py")
    index = faiss.read_index(str(INDEX_PATH))
    metas = json.loads(META_PATH.read_text(encoding="utf-8"))
    client = boto3.client("bedrock-runtime", region_name=REGION)
    dim = index.d
    return index, metas, client, dim


def _embed_query(client, text, dim):
    body = json.dumps({"inputText": text[:MAX_CHARS], "dimensions": dim, "normalize": True})
    resp = client.invoke_model(modelId=MODEL_ID, body=body)
    return json.loads(resp["body"].read())["embedding"]


def search(query, k=5, phase=None, agent_role=None, deliverable_type=None):
    """Return the top-k brain chunks for a query, optionally filtered by metadata.

    Each hit: {score, id, source, phase, agent_role, deliverable_type, text_ref}.
    Filters are applied after the vector search, so we over-fetch to keep k results.
    """
    import numpy as np
    index, metas, client, dim = _load()

    filters = {"phase": phase, "agent_role": agent_role, "deliverable_type": deliverable_type}
    active  = {f: v for f, v in filters.items() if v}
    # over-fetch when filtering so enough survive; cap at corpus size
    fetch = min(len(metas), k * 20 if active else k)

    qv = np.array([_embed_query(client, query, dim)], dtype="float32")
    scores, ids = index.search(qv, fetch)

    hits = []
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0:
            continue
        m = metas[idx]
        if any(str(m.get(f, "")).lower() != str(v).lower() for f, v in active.items()):
            continue
        hits.append({
            "score":            round(float(score), 4),
            "id":               m.get("id"),
            "source":           m.get("source"),
            "phase":            m.get("phase"),
            "agent_role":       m.get("agent_role"),
            "deliverable_type": m.get("deliverable_type"),
            "scope_item_id":    m.get("scope_item_id"),
            "chunk_file":       m.get("chunk_file"),
        })
        if len(hits) >= k:
            break
    return hits


def _read_chunk_text(chunk_file):
    fp = BASE_DIR / "brain" / chunk_file
    try:
        return json.loads(fp.read_text(encoding="utf-8")).get("text", "")
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="Natural-language query")
    ap.add_argument("-k", type=int, default=5, help="Number of results")
    ap.add_argument("--phase", help="Filter: Discover/Prepare/Explore/Realize/Deploy/Run")
    ap.add_argument("--agent", dest="agent_role", help="Filter: e.g. build_agent, qe_agent")
    ap.add_argument("--deliverable", dest="deliverable_type", help="Filter: e.g. test_strategy")
    ap.add_argument("--text", action="store_true", help="Print the matched chunk text")
    args = ap.parse_args()

    hits = search(args.query, k=args.k, phase=args.phase,
                  agent_role=args.agent_role, deliverable_type=args.deliverable_type)
    if not hits:
        print("No matches (check filters or that the index is built).")
        return
    for i, h in enumerate(hits, 1):
        tags = " · ".join(str(h[f]) for f in ("phase", "agent_role", "deliverable_type") if h.get(f))
        print(f"\n[{i}] score={h['score']}  {tags}")
        print(f"    source: {h['source']}"
              + (f"  scope={h['scope_item_id']}" if h.get("scope_item_id") else ""))
        if args.text:
            snippet = _read_chunk_text(h["chunk_file"])[:500].replace("\n", " ")
            print(f"    {snippet}...")


if __name__ == "__main__":
    main()
