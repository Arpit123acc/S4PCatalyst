"""
S4PC Digital Brain — Layer 2+3 vector engine.

Backends, chosen by S4PC_VECTOR_BACKEND (unset = auto: dense if available, else tfidf):

  bedrock  Amazon Bedrock Titan Text Embeddings v2 (1024-dim) via the host IAM role.
           True semantic match with NO local model: nothing to install and no
           resident model in the MCP server process. Preferred on the delivery host,
           which has 3.7 GB RAM and already runs Titan for search_brain, so this adds
           no new dependency, no new credential and no memory pressure.
           Index files: index.npy (float32 matrix) + index.json (metadata)
  dense    sentence-transformers (all-MiniLM-L6-v2, 384-dim), model held in memory.
           Needs `pip install sentence-transformers`, which pulls PyTorch (~2-3 GB) and
           keeps the model resident at query time — do not use it on a small host.
           Index files: index.npy + index.json
  tfidf    BM25-ish TF-IDF, pure stdlib. Keyword overlap only: no synonyms, no
           paraphrases. Always available, and the reason a missing backend degrades
           quietly rather than failing.
           Index file: index.json (sparse vectors)

    S4PC_VECTOR_BACKEND=bedrock          # explicit; recommended on the delivery host
    S4PC_EMBED_MODEL=all-MiniLM-L6-v2    # dense backend only
    AWS_REGION / TITAN_MODEL             # bedrock backend only

Re-run build_index.py after changing backend or adding catalog data — an index built by
one backend is not readable by another, and `search()` dispatches on the engine recorded
in the index header, so a stale index silently keeps the old backend.
"""

import json
import math
import os
import re
from collections import Counter

_HERE      = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(_HERE, "index.json")   # metadata (all backends)
EMBED_PATH = os.path.join(_HERE, "index.npy")    # float32 matrix (dense + bedrock)

MODEL_NAME = os.environ.get("S4PC_EMBED_MODEL", "all-MiniLM-L6-v2")

# Bedrock backend
TITAN_MODEL = os.environ.get("TITAN_MODEL", "amazon.titan-embed-text-v2:0")
AWS_REGION  = os.environ.get("AWS_REGION", "us-east-1")
TITAN_DIM   = int(os.environ.get("TITAN_DIM", "1024"))
TITAN_MAX_CHARS = 40_000        # Titan input cap; matches scripts/brain_search.py

_MODEL_CACHE = None  # lazy-loaded; avoids re-loading on every search call
_BEDROCK_CACHE = None


def _load_model():
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        _MODEL_CACHE = SentenceTransformer(MODEL_NAME)
        return _MODEL_CACHE
    except ImportError:
        return None
    except Exception:
        return None


def _bedrock_client():
    """Lazy Bedrock runtime client. None when boto3 or credentials are absent."""
    global _BEDROCK_CACHE
    if _BEDROCK_CACHE is not None:
        return _BEDROCK_CACHE
    try:
        import boto3  # noqa: PLC0415
        _BEDROCK_CACHE = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        return _BEDROCK_CACHE
    except Exception:
        return None


def backend():
    """Which backend this process would BUILD with.

    Explicit S4PC_VECTOR_BACKEND wins; unset falls back to the historical
    auto-detect so existing installs behave exactly as before.
    """
    want = (os.environ.get("S4PC_VECTOR_BACKEND") or "").strip().lower()
    if want in ("bedrock", "dense", "tfidf"):
        return want
    return "dense" if _load_model() is not None else "tfidf"


# ── Bedrock Titan backend ──────────────────────────────────────────────────────

def _embed_titan(client, text):
    """Embed one string. Titan v2 has no batch API, so callers parallelise."""
    body = json.dumps({"inputText": (text or " ")[:TITAN_MAX_CHARS],
                       "dimensions": TITAN_DIM, "normalize": True})
    for attempt in range(4):                    # Bedrock throttles under concurrency
        try:
            resp = client.invoke_model(modelId=TITAN_MODEL, body=body)
            return json.loads(resp["body"].read())["embedding"]
        except Exception:
            if attempt == 3:
                raise
            import time as _t  # noqa: PLC0415
            _t.sleep(1.5 * (attempt + 1))
    return None


def _build_bedrock(documents):
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    client = _bedrock_client()
    if client is None:
        raise RuntimeError("boto3 / AWS credentials unavailable for the bedrock backend")
    texts = [d["text"] or " " for d in documents]
    print("  Encoding %d documents with %s (%d-dim) in %s"
          % (len(texts), TITAN_MODEL, TITAN_DIM, AWS_REGION))
    vectors = [None] * len(texts)

    def _one(i):
        vectors[i] = _embed_titan(client, texts[i])
        n = sum(1 for v in vectors if v is not None)
        if n % 500 == 0:
            print("    %d/%d" % (n, len(texts)), flush=True)

    with ThreadPoolExecutor(max_workers=8) as pool:   # 8 keeps well inside Bedrock limits
        list(pool.map(_one, range(len(texts))))

    emb = np.array(vectors, dtype="float32")
    np.save(EMBED_PATH, emb)
    meta = [{"id": d["id"], "type": d["type"], "metadata": d.get("metadata", {})}
            for d in documents]
    _write_json(INDEX_PATH, {"engine": "bedrock", "model": TITAN_MODEL,
                             "dim": TITAN_DIM, "docs": meta})
    return len(documents)


def _search_bedrock(query, top_k, filter_type, min_score):
    client = _bedrock_client()
    if client is None:
        return {"error": "boto3 / AWS credentials unavailable — cannot embed the query. "
                         "Rebuild with S4PC_VECTOR_BACKEND=tfidf for an offline index."}
    try:
        q_vec = _embed_titan(client, query)
    except Exception as exc:
        return {"error": "Bedrock embedding failed: %s" % exc}
    return _search_matrix(q_vec, top_k, filter_type, min_score, default_threshold=0.25)


# ── Dense backend ──────────────────────────────────────────────────────────────

def _build_dense(documents):
    import numpy as np
    model = _load_model()
    texts = [d["text"] or " " for d in documents]
    print("  Encoding %d documents with model: %s" % (len(texts), MODEL_NAME))
    emb = model.encode(
        texts, batch_size=64, show_progress_bar=True,
        normalize_embeddings=True, convert_to_numpy=True,
    )
    np.save(EMBED_PATH, emb.astype(np.float32))
    meta = [
        {"id": d["id"], "type": d["type"], "metadata": d.get("metadata", {})}
        for d in documents
    ]
    _write_json(INDEX_PATH, {"engine": "dense", "model": MODEL_NAME, "docs": meta})
    return len(documents)


def _search_matrix(q_vec, top_k, filter_type, min_score, default_threshold=0.25):
    """Cosine search of a query vector against index.npy.

    Shared by the dense and bedrock backends — they differ only in how the query is
    embedded, so keeping one matrix path means a scoring change cannot drift between them.
    """
    try:
        import numpy as np
    except ImportError:
        return {"error": "numpy not installed — required for vector search"}
    try:
        matrix = np.load(EMBED_PATH)
        with open(INDEX_PATH, encoding="utf-8") as fh:
            index = json.load(fh)
    except FileNotFoundError:
        return {"error": "Index not built — run: python mcp-server/vector/build_index.py"}
    except Exception as exc:
        return {"error": "Index load error: %s" % exc}

    q_vec = np.asarray(q_vec, dtype="float32")
    if q_vec.shape[0] != matrix.shape[1]:
        return {"error": "Query dim %d != index dim %d — the index was built by a "
                         "different backend. Re-run build_index.py."
                         % (q_vec.shape[0], matrix.shape[1])}
    scores = matrix @ q_vec  # cosine similarity (both L2-normalised)
    threshold = min_score if min_score is not None else default_threshold

    hits = []
    for idx in scores.argsort()[::-1]:
        if len(hits) >= top_k:
            break
        score = float(scores[idx])
        if score < threshold:
            break
        doc = index["docs"][int(idx)]
        if filter_type and doc["type"] != filter_type:
            continue
        hits.append({
            "score":    round(score, 4),
            "id":       doc["id"],
            "type":     doc["type"],
            "metadata": doc["metadata"],
        })
    return hits


def _search_dense(query, top_k, filter_type, min_score):
    model = _load_model()
    if model is None:
        return {"error": "sentence-transformers not available for dense search"}
    q_vec = model.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0]
    return _search_matrix(q_vec, top_k, filter_type, min_score, default_threshold=0.25)


# ── TF-IDF fallback ────────────────────────────────────────────────────────────

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "into", "and", "or", "not", "this",
    "that", "these", "those", "it", "its", "use", "used", "using",
    "sap", "cloud", "public", "edition", "s4hana", "api", "view",
}


def _tokenize(text):
    text = (text or "").lower()
    raw = re.findall(r"[a-z][a-z0-9]{1,}", text)
    expanded = []
    for tok in raw:
        expanded.append(tok)
        parts = [p for p in tok.split("_") if len(p) > 1]
        expanded.extend(parts)
    return [t for t in expanded if t not in _STOP and len(t) > 1]


def _build_tfidf(documents):
    N = len(documents)
    if not N:
        _write_json(INDEX_PATH, {"engine": "tfidf", "idf": {}, "docs": []})
        return 0
    tokenized = [_tokenize(d["text"]) for d in documents]
    df = Counter()
    for tokens in tokenized:
        df.update(set(tokens))
    idf = {term: math.log((N + 1) / (cnt + 1)) + 1.0 for term, cnt in df.items()}
    index_docs = []
    for i, doc in enumerate(documents):
        tf_raw = Counter(tokenized[i])
        total  = max(len(tokenized[i]), 1)
        vec    = {t: (cnt / total) * idf[t] for t, cnt in tf_raw.items()}
        mag    = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vec    = {k: v / mag for k, v in vec.items()}
        index_docs.append({
            "id":       doc["id"],
            "type":     doc["type"],
            "tfidf":    vec,
            "metadata": doc.get("metadata", {}),
        })
    _write_json(INDEX_PATH, {"engine": "tfidf", "idf": idf, "docs": index_docs})
    return N


def _search_tfidf(query, top_k, filter_type, min_score):
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            index = json.load(fh)
    except FileNotFoundError:
        return {"error": "Index not built — run: python mcp-server/vector/build_index.py"}
    except Exception as exc:
        return {"error": "Index load error: %s" % exc}

    idf    = index.get("idf", {})
    tokens = _tokenize(query)
    tf     = Counter(tokens)
    total  = max(len(tokens), 1)
    q_vec  = {t: (cnt / total) * idf[t] for t, cnt in tf.items() if t in idf}
    mag    = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
    q_vec  = {k: v / mag for k, v in q_vec.items()}
    if not q_vec:
        return []

    threshold = min_score if min_score is not None else 0.04
    hits = []
    for doc in index.get("docs", []):
        if filter_type and doc["type"] != filter_type:
            continue
        score = sum(q_vec.get(t, 0.0) * v for t, v in doc["tfidf"].items())
        if score >= threshold:
            hits.append({
                "score":    round(score, 4),
                "id":       doc["id"],
                "type":     doc["type"],
                "metadata": doc["metadata"],
            })
    hits.sort(key=lambda h: -h["score"])
    return hits[:top_k]


# ── helpers ────────────────────────────────────────────────────────────────────

def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))


# ── public API ─────────────────────────────────────────────────────────────────

def build_and_save(documents):
    """Build the index with the selected backend. Returns document count.

    A requested backend that cannot run is a hard error, not a silent downgrade to
    tfidf: an index that quietly loses semantic search looks identical to a good one.
    """
    want = backend()
    if want == "bedrock":
        return _build_bedrock(documents)          # raises if boto3/creds missing
    if want == "dense":
        if _load_model() is None:
            raise RuntimeError(
                "S4PC_VECTOR_BACKEND=dense but sentence-transformers is not importable. "
                "pip install sentence-transformers, or use S4PC_VECTOR_BACKEND=bedrock.")
        return _build_dense(documents)
    if not os.environ.get("S4PC_VECTOR_BACKEND"):
        print("  No dense backend available — using TF-IDF (keyword overlap only).")
        print("  For real semantic search set S4PC_VECTOR_BACKEND=bedrock (no install "
              "needed where the host has Bedrock access) and re-run.")
    return _build_tfidf(documents)


def search(query, top_k=5, filter_type=None, min_score=None):
    """Search the index, dispatching on the backend recorded when it was built."""
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            header = json.load(fh)
        eng = header.get("engine", "tfidf")
    except Exception:
        eng = "tfidf"

    if eng == "bedrock" and os.path.exists(EMBED_PATH):
        return _search_bedrock(query, top_k, filter_type, min_score)
    if eng == "dense" and os.path.exists(EMBED_PATH):
        return _search_dense(query, top_k, filter_type, min_score)
    return _search_tfidf(query, top_k, filter_type, min_score or 0.04)
