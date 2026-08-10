"""
S4PC Digital Brain — Layer 2+3 TF-IDF vector engine.

Zero-dependency (Python 3.9+ stdlib only). No pip installs required.
Implements BM25-style TF-IDF with cosine similarity over the catalog + runs corpus.
Index is persisted as mcp-server/vector/index.json (built by build_index.py).
"""

import json
import math
import os
import re
from collections import Counter

INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.json")

# ── stopwords ────────────────────────────────────────────────────────────────────

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "into", "and", "or", "not", "this",
    "that", "these", "those", "it", "its", "use", "used", "using",
    "sap", "cloud", "public", "edition", "s4hana", "api", "view",
}

# ── tokeniser ────────────────────────────────────────────────────────────────────

def _tokenize(text):
    """Tokenise + expand underscore-delimited and CamelCase fragments."""
    text = (text or "").lower()
    raw = re.findall(r"[a-z][a-z0-9]{1,}", text)
    expanded = []
    for tok in raw:
        expanded.append(tok)
        # split on underscore fragments (for CDS/API names like i_salesorder)
        parts = [p for p in tok.split("_") if len(p) > 1]
        expanded.extend(parts)
    return [t for t in expanded if t not in _STOP and len(t) > 1]

# ── index builder ────────────────────────────────────────────────────────────────

def _build(documents):
    """
    Build TF-IDF index with BM25 IDF weighting and L2-normalised document vectors.

    documents: list of {"id": str, "type": str, "text": str, "metadata": dict}
    Returns:   {"idf": {term: float}, "docs": [{id, type, tfidf, metadata}]}
    """
    N = len(documents)
    if not N:
        return {"idf": {}, "docs": []}

    tokenized = [_tokenize(d["text"]) for d in documents]

    df = Counter()
    for tokens in tokenized:
        df.update(set(tokens))

    # BM25-style smooth IDF: log((N+1)/(df+1)) + 1
    idf = {term: math.log((N + 1) / (cnt + 1)) + 1.0 for term, cnt in df.items()}

    index_docs = []
    for i, doc in enumerate(documents):
        tf_raw = Counter(tokenized[i])
        total = max(len(tokenized[i]), 1)
        vec = {t: (cnt / total) * idf[t] for t, cnt in tf_raw.items()}
        # L2 normalise so cosine = dot product
        mag = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vec = {k: v / mag for k, v in vec.items()}
        index_docs.append({
            "id":       doc["id"],
            "type":     doc["type"],
            "tfidf":    vec,
            "metadata": doc.get("metadata", {}),
        })

    return {"idf": idf, "docs": index_docs}

# ── query ────────────────────────────────────────────────────────────────────────

def _query_vec(query_text, idf):
    tokens = _tokenize(query_text)
    tf = Counter(tokens)
    total = max(len(tokens), 1)
    vec = {t: (cnt / total) * idf[t] for t, cnt in tf.items() if t in idf}
    mag = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / mag for k, v in vec.items()}

# ── public API ───────────────────────────────────────────────────────────────────

def search(query, top_k=5, filter_type=None, min_score=0.04):
    """
    Search the pre-built index.
    Returns list of {score, id, type, metadata} sorted by score desc.
    Returns {"error": ...} dict if the index file is missing.
    """
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as fh:
            index = json.load(fh)
    except FileNotFoundError:
        return {"error": "Index not built — run: python mcp-server/vector/build_index.py"}
    except Exception as e:
        return {"error": "Index load error: %s" % e}

    idf = index.get("idf", {})
    q_vec = _query_vec(query, idf)
    if not q_vec:
        return []

    hits = []
    for doc in index.get("docs", []):
        if filter_type and doc["type"] != filter_type:
            continue
        # cosine = dot product (both L2-normalised)
        score = sum(q_vec.get(t, 0.0) * v for t, v in doc["tfidf"].items())
        if score >= min_score:
            hits.append({
                "score":    round(score, 4),
                "id":       doc["id"],
                "type":     doc["type"],
                "metadata": doc["metadata"],
            })

    hits.sort(key=lambda h: -h["score"])
    return hits[:top_k]

def build_and_save(documents):
    """Build the TF-IDF index and persist to INDEX_PATH. Returns document count."""
    index = _build(documents)
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
    return len(documents)
