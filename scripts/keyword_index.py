#!/usr/bin/env python3
"""
Build the keyword (BM25) half of the brain's hybrid retrieval.

WHY THIS EXISTS
    Pure vector search is weak exactly where the agents need precision: exact
    identifiers. "API_PURCHASEORDER_PROCESS_SRV" and "scope item 11J" are lookups,
    not semantic questions, and a cosine over a 49k-chunk corpus answers them by
    vibe.

    Measured 2026-09-04 (regression case R-062): the query "ATC check profile that
    must pass before a transport" should reach the ABAP Cloud review standard, which
    answers it verbatim -- 'ATC check profile "ABAP Cloud" must pass with zero
    Critical/High findings before any transport'. It did not make the top 10. Drop
    the word "transport" and that document ranks 1st at 0.477; keep it and three
    SharePoint transport-process documents win at 0.338/0.332/0.325. Note the winners
    score LOWER than the right answer did without that term: one dominant common word
    pulls the query embedding into the wrong cluster, and 8 chunks cannot outvote
    44,586 on term competition. BM25 does not have this failure mode -- it scores
    "ATC" by inverse document frequency, so a rare term stays decisive.

WHY SQLITE FTS5
    stdlib (matches this project's zero-dependency posture -- no new wheel on a
    production box), a real BM25 implementation rather than one hand-rolled here, and
    on-disk so it costs no resident memory in the MCP server, which already caches the
    FAISS index. Contentless (`content=''`) because chunk text is already on disk in
    brain/*/chunks -- storing it twice would add ~150 MB for nothing.

WHY IT IMPORTS THE LOADERS FROM embed_chunks
    The two indexes MUST cover the same corpus. guidance_ingest.py already shipped 8
    chunks that the embedder silently skipped because CHUNK_ROOTS had not been
    updated -- the ingest reported success and the content simply was not searchable.
    Importing load_chunks/load_scope_items means there is exactly one definition of
    "what is in the brain", so that class of bug cannot recur here.

TOKENIZER
    `tokenchars '_'` keeps underscores inside tokens, so API_CLFN_PRODUCT_SRV is ONE
    rare term instead of four common ones (API / CLFN / PRODUCT / SRV). That is the
    whole point of the keyword half: high IDF on an identifier makes it decisive.

Outputs (brain/index/):
    keyword.db       FTS5 + metadata, published atomically with a .prev rollback copy

Usage:
    python3.11 scripts/keyword_index.py
    python3.11 scripts/keyword_index.py --allow-shrink    # deliberate smaller corpus
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR  = Path(__file__).resolve().parent.parent
INDEX_DIR = BASE_DIR / "brain" / "index"
DB_PATH   = INDEX_DIR / "keyword.db"

# Metadata carried into the keyword index. Deliberately the FILTERABLE fields plus
# the identity fields -- everything brain_search needs to fuse and to apply the same
# filters as the vector path, and nothing else.
META_COLS = ["chunk_id", "source", "source_system", "phase", "agent_role",
             "deliverable_type", "chunk_file", "scope_item_id"]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("keyword_index")


def _schema(cur):
    cur.execute("""
        CREATE TABLE meta (
            rowid            INTEGER PRIMARY KEY,
            chunk_id         TEXT NOT NULL,
            source           TEXT,
            source_system    TEXT,
            phase            TEXT,
            agent_role       TEXT,
            deliverable_type TEXT,
            chunk_file       TEXT,
            scope_item_id    TEXT
        )""")
    # Contentless: index only, no stored copy of the text.
    cur.execute("CREATE VIRTUAL TABLE fts USING fts5("
                "text, content='', tokenize=\"unicode61 tokenchars '_'\")")


def _indexes(cur):
    # Built AFTER the bulk insert -- indexing as you go is markedly slower.
    for col in ("source_system", "phase", "agent_role", "deliverable_type", "chunk_id"):
        cur.execute("CREATE INDEX idx_meta_%s ON meta(%s)" % (col, col))


def build(rows, allow_shrink=False):
    """Write rows to a temp DB, validate, then swap it over the live one."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    existing = 0
    if DB_PATH.exists():
        try:
            con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
            existing = con.execute("SELECT count(*) FROM meta").fetchone()[0]
            con.close()
        except Exception:
            existing = 0

    # Same guard as embed_chunks.py: a partial build must never replace the live
    # index. A 200-vector smoke test overwrote a 49,438-vector brain on 2026-09-03.
    if existing > len(rows) and not allow_shrink:
        sys.exit(
            "REFUSING to publish: this build has %d rows but the live keyword index "
            "has %d.\n  Re-run over the full corpus, or pass --allow-shrink if a "
            "smaller index is genuinely intended.\n  Nothing was written." %
            (len(rows), existing))

    tmp = DB_PATH.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)

    con = sqlite3.connect(str(tmp))
    cur = con.cursor()
    # Bulk-load pragmas; durability does not matter for a rebuildable derived index.
    cur.execute("PRAGMA journal_mode=OFF")
    cur.execute("PRAGMA synchronous=OFF")
    _schema(cur)

    cur.executemany(
        "INSERT INTO meta (rowid, %s) VALUES (?,?,?,?,?,?,?,?,?)" % ",".join(META_COLS),
        [(i, *(r["meta"].get(c) for c in META_COLS)) for i, r in enumerate(rows, 1)])
    cur.executemany("INSERT INTO fts (rowid, text) VALUES (?,?)",
                    [(i, r["text"]) for i, r in enumerate(rows, 1)])
    _indexes(cur)
    con.commit()

    n = cur.execute("SELECT count(*) FROM meta").fetchone()[0]
    # The two tables are joined on rowid, so a mismatch is not a clean failure --
    # it is a keyword hit carrying another chunk's metadata. Validate before publish.
    fts_n = cur.execute("SELECT count(*) FROM fts").fetchone()[0]
    con.close()
    if n != len(rows) or fts_n != len(rows):
        tmp.unlink(missing_ok=True)
        sys.exit("refusing to publish a mismatched keyword index: %d meta / %d fts "
                 "rows for %d inputs" % (n, fts_n, len(rows)))

    prev = DB_PATH.with_suffix(".db.prev")
    had_prev = DB_PATH.exists()
    if had_prev:
        os.replace(DB_PATH, prev)
    try:
        os.replace(tmp, DB_PATH)
    except Exception:
        if had_prev:
            os.replace(prev, DB_PATH)
        raise
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-shrink", action="store_true",
                    help="Permit publishing a smaller index than the live one.")
    ap.add_argument("--no-scope", action="store_true", help="Skip the SAP scope catalog")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # One definition of the corpus, shared with the embedder -- see module docstring.
    from embed_chunks import load_chunks, load_scope_items

    rows = []
    for label, gen in (("chunks", load_chunks()),
                       ("scope items", () if args.no_scope else load_scope_items())):
        before = len(rows)
        for text, meta in gen:
            m = dict(meta)
            m["chunk_id"] = m.get("id")
            rows.append({"text": text, "meta": m})
        log.info("  loaded %s: %d items", label, len(rows) - before)

    if not rows:
        sys.exit("Nothing to index. Run the ingest first.")

    missing_id = [r for r in rows if not r["meta"].get("chunk_id")]
    if missing_id:
        # chunk_id is the fusion key between the two retrievers. Without it a hit
        # cannot be matched to its vector counterpart and would double-count in RRF.
        sys.exit("%d chunks have no id -- cannot fuse without a stable key."
                 % len(missing_id))

    n = build(rows, allow_shrink=args.allow_shrink)
    size_mb = DB_PATH.stat().st_size / 1e6
    log.info("Done. %d rows in %s (%.1f MB)", n, DB_PATH.name, size_mb)

    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    tally = con.execute("SELECT source_system, count(*) FROM meta "
                        "GROUP BY source_system ORDER BY 2 DESC").fetchall()
    con.close()
    log.info("Sources: %s", {s: c for s, c in tally})


if __name__ == "__main__":
    main()
