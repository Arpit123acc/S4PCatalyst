"""
S4PC Digital Brain — Layer 2+3 vector engine.

Backend selection (automatic, no config needed):
  1. sentence-transformers installed  →  dense embeddings (all-MiniLM-L6-v2, 384-dim)
     Finds synonyms, related concepts, paraphrases — true semantic match.
     Index files: index.npy (float32 matrix)  +  index.json (metadata)
  2. sentence-transformers NOT installed  →  BM25 TF-IDF fallback (pure stdlib)
     Index file: index.json (TF-IDF sparse vectors)

Install the dense backend (once, then re-run build_index.py):
    pip install sentence-transformers

Override the model via env var (e.g. a locally cached path):
    S4PC_EMBED_MODEL=all-MiniLM-L6-v2   (default)

Re-run build_index.py after installing sentence-transformers or adding catalog data.
"""

import json
import math
import os
import re
from collections import Counter

_HERE      = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(_HERE, "index.json")   # metadata (both backends)
EMBED_PATH = os.path.join(_HERE, "index.npy")    # float32 matrix (dense only)

MODEL_NAME = os.environ.get("S4PC_EMBED_MODEL", "all-MiniLM-L6-v2")

_MODEL_CACHE = None  # lazy-loaded; avoids re-loading on every search call


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


def backend():
    """Return 'dense' if sentence-transformers is available, else 'tfidf'."""
    return "dense" if _load_model() is not None else "tfidf"


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


def _search_dense(query, top_k, filter_type, min_score):
    try:
        import numpy as np
    except ImportError:
        return {"error": "numpy not installed — required for dense search"}
    try:
        matrix = np.load(EMBED_PATH)
        with open(INDEX_PATH, encoding="utf-8") as fh:
            index = json.load(fh)
    except FileNotFoundError:
        return {"error": "Index not built — run: python mcp-server/vector/build_index.py"}
    except Exception as exc:
        return {"error": "Index load error: %s" % exc}

    model = _load_model()
    if model is None:
        return {"error": "sentence-transformers not available for dense search"}

    q_vec = model.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0]
    scores = matrix @ q_vec  # cosine similarity (both L2-normalised)
    threshold = min_score if min_score is not None else 0.25

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
    """Build index using the best available backend. Returns document count."""
    if _load_model() is not None:
        return _build_dense(documents)
    print("  sentence-transformers not found — using TF-IDF fallback.")
    print("  To upgrade: pip install sentence-transformers  then re-run build_index.py")
    return _build_tfidf(documents)


def search(query, top_k=5, filter_type=None, min_score=None):
    """Search the index. Auto-detects which backend was used to build it."""
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            header = json.load(fh)
        eng = header.get("engine", "tfidf")
    except Exception:
        eng = "tfidf"

    if eng == "dense" and os.path.exists(EMBED_PATH):
        return _search_dense(query, top_k, filter_type, min_score)
    return _search_tfidf(query, top_k, filter_type, min_score or 0.04)
