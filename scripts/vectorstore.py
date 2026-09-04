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

# `phase` and `agent_role` describe DELIVERY PROVENANCE — which project phase and
# which role produced an artifact. The sources below were not produced by a project
# at all: they are vendor documentation and SAP's own catalogs. Filtering them by
# phase is a category error, and it silently hides them.
#
# It matters because the brain serves more than one agent. The UI5 docs are tagged
# phase=Realize purely to match the S4PC pipeline's build call, so a discovery agent
# asking phase=Explore, or an architect asking phase=Design, gets a confident page of
# delivery documents with the authoritative source missing. That is the pipeline's
# vocabulary dictating what every other consumer can see.
#
# Scoped by SOURCE, not by content_type. Measured 2026-09-04: content_type=reference
# covers 2,416 chunks of which 1,462 are SharePoint delivery material that DOES have
# provenance — exempting those would be wrong. By source it is 954 chunks (1.9%).
#
# Only phase/agent_role are exempted. deliverable_type and source_system are
# descriptive rather than provenance, so they still filter these sources normally --
# source_system="developer_docs" and deliverable_type="ui5_docs" keep working.
# abap_guidance (internal ABAP Cloud / RAP review standards, scripts/guidance_ingest.py)
# is exempt for the same reason and is deliberately NOT public-by-construction: these
# documents can carry a client name, so brain_regression keeps their names hashed in
# the committed baseline. Provenance-exemption and name-publicity are separate
# decisions and this source is the case where they diverge.
PROVENANCE_EXEMPT_SOURCES = {"developer_docs", "sap_scope_catalog", "abap_guidance"}
PROVENANCE_FIELDS = {"phase", "agent_role"}


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

    def score_ids(self, vector, chunk_ids):
        """Cosine for specific chunk ids -> {id: score}. Missing ids are omitted.

        Needed by hybrid retrieval: a chunk found only by the BM25 half has no
        similarity score, and the alternatives are both bad -- reporting None
        breaks every consumer that formats a number (brain-ui renders `score` as a
        0-1 meter), and inventing 0.0 states a false measurement. Both real
        backends can answer this exactly and cheaply, so the score always means the
        same thing regardless of which retriever surfaced the hit.

        Default returns {} so a backend that cannot do it degrades to "unscored"
        rather than raising.
        """
        return {}


# ── FAISS backend (file-based; POC / single node) ──────────────────────────────
class FaissStore(VectorStore):
    INDEX_PATH = INDEX_DIR / "faiss.index"
    META_PATH  = INDEX_DIR / "metadata.json"

    def __init__(self, dim, load=False):
        import faiss  # lazy
        self._faiss = faiss
        self.dim = dim
        self.metas = []
        self._id_pos = None      # id -> index position; built lazily by score_ids()
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
        """Write the index atomically: build to temp files, validate, then swap.

        Writing straight over the live files meant a rebuild that died partway
        (throttling, a dropped connection, OOM) left the brain corrupt with no
        rollback -- and a full rebuild is ~20 minutes over 49k chunks, so the
        window is not small.

        The two files must move TOGETHER. metadata.json is positional, 1:1 with the
        vectors in faiss.index, so a half-completed swap is not a clean failure --
        it is a silently mismatched index where every hit returns the wrong
        document. Hence: validate the pair, keep the previous pair, swap, and roll
        back if the second swap fails.
        """
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        idx_tmp  = self.INDEX_PATH.with_suffix(self.INDEX_PATH.suffix + ".tmp")
        meta_tmp = self.META_PATH.with_suffix(self.META_PATH.suffix + ".tmp")

        self._faiss.write_index(self.index, str(idx_tmp))
        meta_tmp.write_text(json.dumps(self.metas, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        # Validate the pair before it becomes live, not after.
        written = self._faiss.read_index(str(idx_tmp))
        if written.ntotal != len(self.metas):
            idx_tmp.unlink(missing_ok=True); meta_tmp.unlink(missing_ok=True)
            raise RuntimeError(
                "refusing to publish a mismatched index: %d vectors vs %d metadata "
                "entries" % (written.ntotal, len(self.metas)))

        idx_prev  = self.INDEX_PATH.with_suffix(self.INDEX_PATH.suffix + ".prev")
        meta_prev = self.META_PATH.with_suffix(self.META_PATH.suffix + ".prev")
        had_prev = self.INDEX_PATH.exists() and self.META_PATH.exists()
        if had_prev:
            os.replace(self.INDEX_PATH, idx_prev)
            os.replace(self.META_PATH, meta_prev)
        try:
            os.replace(idx_tmp, self.INDEX_PATH)
            os.replace(meta_tmp, self.META_PATH)
        except Exception:
            if had_prev:                     # put the working index back
                os.replace(idx_prev, self.INDEX_PATH)
                os.replace(meta_prev, self.META_PATH)
            raise
        # Keep .prev on disk as the rollback copy; the next successful build replaces it.

    def search(self, vector, k, filters=None):
        """Top-k by cosine, optionally filtered. Widens the window until k survive.

        FAISS has no native metadata filter, so filtering happens AFTER the vector
        search -- and that makes a fixed over-fetch window quietly wrong. Measured
        2026-09-04, right after 275 UI5 chunks were added: for a UI5 query with
        phase=Explore, 90 of the top 200 candidates were the new developer_docs, the
        filter discarded them, and 2 hits came back. The index held 184 qualifying
        documents. Nothing errored; the caller just got a short answer.

            window=200   developer_docs=90   Explore survivors=2
            window=3000  developer_docs=158  Explore survivors=184

        So the failure worsens with every source added, which is the opposite of what
        a growing brain should do, and it hits filtered callers only -- exactly the
        per-phase agents this brain is meant to serve. Escalate instead of guessing:
        widen and retry until k survive or the index is exhausted. Unfiltered queries
        are unaffected and still fetch exactly k.
        """
        import numpy as np
        active = {f: v for f, v in (filters or {}).items() if v}
        qv = np.array([vector], dtype="float32")
        total = len(self.metas)

        def excluded(m):
            exempt = m.get("source_system") in PROVENANCE_EXEMPT_SOURCES
            for f, v in active.items():
                if exempt and f in PROVENANCE_FIELDS:
                    continue          # not phase-specific; a phase filter must not hide it
                if str(m.get(f, "")).lower() != str(v).lower():
                    return True
            return False

        def window(fetch):
            scores, ids = self.index.search(qv, min(fetch, total))
            hits = []
            for score, idx in zip(scores[0], ids[0]):
                if idx < 0:
                    continue
                m = self.metas[idx]
                if excluded(m):
                    continue
                hits.append({"score": round(float(score), 4), **m})
                if len(hits) >= k:
                    break
            return hits

        if not active:
            return window(k)
        fetch = max(k * 20, k)
        while True:
            hits = window(fetch)
            if len(hits) >= k or fetch >= total:
                return hits
            fetch *= 4

    def count(self):
        return self.index.ntotal

    def score_ids(self, vector, chunk_ids):
        """Exact cosine for specific ids. No re-embedding, no approximation.

        IndexFlatIP stores vectors verbatim, so reconstruct() returns the original
        embedding; both it and the query were L2-normalised at build time
        (Titan `normalize: true`), so a dot product IS the cosine -- the same number
        search() would have reported.
        """
        import numpy as np
        if self._id_pos is None:
            self._id_pos = {m.get("id"): i for i, m in enumerate(self.metas)}
        q = np.array(vector, dtype="float32")
        out = {}
        for cid in chunk_ids:
            pos = self._id_pos.get(cid)
            if pos is None:
                continue
            out[cid] = round(float(np.dot(self.index.reconstruct(int(pos)), q)), 4)
        return out


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

    def score_ids(self, vector, chunk_ids):
        """Exact cosine for specific ids, computed in the database."""
        ids = [c for c in chunk_ids if c]
        if not ids:
            return {}
        qvec = self._vec(vector)
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->>'id', 1 - (embedding <=> %s::vector) "
                f"FROM {self.TABLE} WHERE metadata->>'id' = ANY(%s)", (qvec, ids))
            return {cid: round(float(score), 4) for cid, score in cur.fetchall()}

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
