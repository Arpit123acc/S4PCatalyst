#!/usr/bin/env python3
"""
Pluggable vector-store backend for the S4PC Public Cloud Brain.

The Public Cloud Brain is meant to grow into a large, multi-source knowledge base.
To keep it scalable and portable, embedding and search go through this one
interface instead of talking to a specific store. Swap the backend with an env
var — no change to embed_chunks.py / brain_search.py / the MCP server.

    BRAIN_BACKEND = faiss     (default) file-based FAISS index — POC / single node
                  = pgvector           Postgres + pgvector — shared, scales, filterable
                  (opensearch / others slot in the same way)

Interface (backend-agnostic):
    store = get_store(dim)                       # for building
    store.add(vectors, metadatas); store.persist()
    store = get_store(dim, load=True)            # for querying
    hits = store.search(query_vector, k, filters)  # -> [{"score", **metadata}]

Every chunk's metadata carries a `source_system` (sharepoint, sap_scope_catalog,
accelerator_hub, ...) so many sources coexist in one brain and can be filtered.

Install: faiss -> pip install faiss-cpu numpy ; pgvector -> pip install psycopg2-binary
"""

import os
import json
from pathlib import Path

BASE_DIR  = Path(__file__).resolve().parent.parent
INDEX_DIR = BASE_DIR / "brain" / "index"


# ── Interface ──────────────────────────────────────────────────────────────────
class VectorStore:
    """Backend-agnostic vector store. Subclasses implement add/persist/search."""
    def add(self, vectors, metadatas):        # append a batch
        raise NotImplementedError
    def persist(self):                        # finalize a build (write files / commit)
        raise NotImplementedError
    def search(self, vector, k, filters=None):  # -> [{"score", **metadata}]
        raise NotImplementedError
    def count(self):
        raise NotImplementedError


# ── FAISS backend (file-based; POC / single node) ──────────────────────────────
class FaissStore(VectorStore):
    INDEX_PATH = INDEX_DIR / "faiss.index"
    META_PATH  = INDEX_DIR / "metadata.json"

    def __init__(self, dim, load=False):
        import faiss  # lazy
        self._faiss = faiss
        self.dim = dim
        self.metas = []
        if load:
            if not self.INDEX_PATH.exists():
                raise FileNotFoundError(
                    f"No FAISS index at {self.INDEX_PATH}. Build it: "
                    f"python3.11 scripts/embed_chunks.py")
            self.index = faiss.read_index(str(self.INDEX_PATH))
            self.metas = json.loads(self.META_PATH.read_text(encoding="utf-8"))
            self.dim = self.index.d
        else:
            self.index = faiss.IndexFlatIP(dim)   # cosine on L2-normalized vectors

    def add(self, vectors, metadatas):
        import numpy as np
        self.index.add(np.array(vectors, dtype="float32"))
        self.metas.extend(metadatas)

    def persist(self):
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self._faiss.write_index(self.index, str(self.INDEX_PATH))
        self.META_PATH.write_text(json.dumps(self.metas, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    def search(self, vector, k, filters=None):
        import numpy as np
        active = {f: v for f, v in (filters or {}).items() if v}
        fetch  = min(len(self.metas), k * 20 if active else k) or k
        qv = np.array([vector], dtype="float32")
        scores, ids = self.index.search(qv, fetch)
        hits = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            m = self.metas[idx]
            if any(str(m.get(f, "")).lower() != str(v).lower() for f, v in active.items()):
                continue
            hits.append({"score": round(float(score), 4), **m})
            if len(hits) >= k:
                break
        return hits

    def count(self):
        return self.index.ntotal


# ── pgvector backend (Postgres / RDS; shared, scales, SQL-filterable) ──────────
class PgVectorStore(VectorStore):
    """
    Postgres + pgvector. Connection from PGVECTOR_DSN (or standard PG* env vars).
    Table:
        brain_chunks(id bigserial pk, embedding vector(dim), metadata jsonb)
    Cosine distance via the `<=>` operator; score = 1 - distance. Build mode drops
    and recreates the table (clean rebuild, like FAISS overwrite) and adds an HNSW
    index for fast ANN at scale. Load mode infers the dimension from the table.
    """
    TABLE = os.environ.get("PGVECTOR_TABLE", "brain_chunks")

    def __init__(self, dim, load=False):
        import psycopg2  # lazy
        self.dim = dim
        dsn = os.environ.get("PGVECTOR_DSN", "")
        self.conn = psycopg2.connect(dsn) if dsn else psycopg2.connect()
        self.conn.autocommit = False
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("SELECT to_regclass(%s)", (self.TABLE,))
            exists = cur.fetchone()[0] is not None
            if load:
                if not exists:
                    raise FileNotFoundError(
                        f"pgvector table '{self.TABLE}' not found. Build it first: "
                        f"BRAIN_BACKEND=pgvector python3.11 scripts/embed_chunks.py")
                cur.execute(f"SELECT vector_dims(embedding) FROM {self.TABLE} LIMIT 1")
                row = cur.fetchone()
                if not row:
                    raise FileNotFoundError(
                        f"pgvector table '{self.TABLE}' is empty. Build it first.")
                self.dim = int(row[0])
            else:
                # clean rebuild — matches FAISS overwrite semantics, handles dim change
                cur.execute(f"DROP TABLE IF EXISTS {self.TABLE}")
                cur.execute(
                    f"CREATE TABLE {self.TABLE} ("
                    f"  id bigserial PRIMARY KEY,"
                    f"  embedding vector({dim}),"
                    f"  metadata jsonb)")
        self.conn.commit()

    def add(self, vectors, metadatas):
        from psycopg2.extras import execute_values
        rows = [(self._vec(v), json.dumps(m, ensure_ascii=False))
                for v, m in zip(vectors, metadatas)]
        with self.conn.cursor() as cur:
            execute_values(
                cur,
                f"INSERT INTO {self.TABLE} (embedding, metadata) VALUES %s",
                rows, template="(%s::vector, %s::jsonb)")

    def persist(self):
        # HNSW index for fast cosine ANN at scale (safe/no-op if it already exists).
        with self.conn.cursor() as cur:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.TABLE}_hnsw "
                f"ON {self.TABLE} USING hnsw (embedding vector_cosine_ops)")
        self.conn.commit()

    def search(self, vector, k, filters=None):
        active = {f: v for f, v in (filters or {}).items() if v}
        where = ""
        if active:
            where = "WHERE " + " AND ".join("metadata->>%s = %s" for _ in active)
        qvec = self._vec(vector)
        sql = (f"SELECT metadata, 1 - (embedding <=> %s::vector) AS score "
               f"FROM {self.TABLE} {where} "
               f"ORDER BY embedding <=> %s::vector LIMIT %s")
        # param order: SELECT vector, WHERE (key,value)*, ORDER BY vector, LIMIT
        params = [qvec] + sum(([f, str(v)] for f, v in active.items()), []) + [qvec, k]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return [{"score": round(float(score), 4), **metadata}
                    for metadata, score in cur.fetchall()]

    def count(self):
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {self.TABLE}")
            return cur.fetchone()[0]

    @staticmethod
    def _vec(v):
        return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


# ── Factory ────────────────────────────────────────────────────────────────────
def get_store(dim, load=False, backend=None):
    backend = (backend or os.environ.get("BRAIN_BACKEND", "faiss")).lower()
    if backend == "faiss":
        return FaissStore(dim, load=load)
    if backend == "pgvector":
        return PgVectorStore(dim, load=load)
    raise ValueError(f"Unknown BRAIN_BACKEND '{backend}' (use: faiss | pgvector)")
