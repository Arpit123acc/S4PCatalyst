#!/usr/bin/env python3
"""
BM25 keyword search over the brain, built by keyword_index.py.

The keyword half of hybrid retrieval. See keyword_index.py for why it exists
(short version: cosine similarity answers exact-identifier lookups by vibe).

FILTERS ARE APPLIED IN SQL, NOT AFTER
    The FAISS path has to over-fetch and filter in Python, which starves: on
    2026-09-04 a phase-filtered UI5 query returned 2 hits out of 184 qualifying
    documents because the new source occupied the fixed over-fetch window. SQLite
    applies WHERE before LIMIT with an index behind it, so this path cannot starve.
    That is also what the pgvector backend will do -- post-hoc filtering is a FAISS
    limitation, not the design.

    The SEMANTICS, however, must match the vector path exactly, or the same query
    returns different corpora through the two retrievers and fusion quietly
    misranks. So the provenance-exemption constants are IMPORTED from vectorstore
    rather than restated here: changing the rule changes both paths at once.

QUERY SANITISATION IS NOT OPTIONAL
    FTS5 MATCH is an expression language -- quotes, parentheses, `*`, `:`, `^`, `-`
    and the bare words AND/OR/NOT are all operators. Passing a user query straight
    in is a syntax error at best ("what's the cutover plan?") and a silently
    different query at worst. Every term is therefore extracted and quoted.

Usage:
    from keyword_search import search
    hits = search("ATC check profile before transport", k=10, filters={...})
"""

import re
import sqlite3
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "brain" / "index" / "keyword.db"

from vectorstore import PROVENANCE_EXEMPT_SOURCES, PROVENANCE_FIELDS

# Whitelist for anything interpolated into SQL. Filter names arrive from callers
# (the MCP tool takes them from an agent), so they are never trusted as identifiers.
FILTERABLE = {"source_system", "phase", "agent_role", "deliverable_type"}
SELECT_COLS = ["chunk_id", "source", "source_system", "phase", "agent_role",
               "deliverable_type", "chunk_file", "scope_item_id"]

# Matches the index's tokenizer: unicode61 + '_' as a token character, so
# API_CLFN_PRODUCT_SRV survives as one term on the query side too.
_TERM = re.compile(r"[A-Za-z0-9_]+")
MAX_TERMS = 40          # a pathological query must not become a 500-term OR


def available():
    return DB_PATH.exists()


@lru_cache(maxsize=1)
def _con():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            "No keyword index at %s. Build it: python3.11 scripts/keyword_index.py"
            % DB_PATH)
    # Read-only + check_same_thread=False: the MCP server may serve concurrently,
    # and nothing here writes.
    return sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True,
                           check_same_thread=False)


def build_match(query):
    """Turn a natural-language query into a safe FTS5 MATCH expression.

    Terms are OR'd, not AND'd: BM25 already rewards documents that carry more of
    the rare terms, whereas AND would return nothing for any query with one word
    the corpus does not contain.
    """
    terms = _TERM.findall(query or "")[:MAX_TERMS]
    # Quoting makes each term a literal string, so FTS5 operators inside a user
    # query cannot change the parse.
    return " OR ".join('"%s"' % t for t in terms) if terms else ""


def _where(filters):
    """Build the filter SQL. Mirrors FaissStore.search()'s excluded() exactly."""
    clauses, params = [], []
    for field, value in (filters or {}).items():
        if not value or field not in FILTERABLE:
            continue
        if field in PROVENANCE_FIELDS:
            # phase/agent_role describe delivery provenance. Vendor docs and SAP's
            # own catalogs have none, so a phase filter must not hide them.
            marks = ",".join("?" * len(PROVENANCE_EXEMPT_SOURCES))
            clauses.append("(m.source_system IN (%s) OR lower(coalesce(m.%s,'')) = ?)"
                           % (marks, field))
            params.extend(sorted(PROVENANCE_EXEMPT_SOURCES))
            params.append(str(value).lower())
        else:
            clauses.append("lower(coalesce(m.%s,'')) = ?" % field)
            params.append(str(value).lower())
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


def search(query, k=10, filters=None):
    """Top-k BM25 hits. Returns [{keyword_score, id, source, ...}], best first.

    keyword_score is NEGATED bm25 so that, as with cosine, higher is better.
    (SQLite's bm25() returns more-negative for better matches.)
    """
    match = build_match(query)
    if not match:
        return []
    where_sql, params = _where(filters)
    cols = ", ".join("m.%s" % c for c in SELECT_COLS)
    sql = ("SELECT %s, bm25(fts) AS bm FROM fts JOIN meta m ON m.rowid = fts.rowid "
           "WHERE fts MATCH ?%s ORDER BY bm LIMIT ?" % (cols, where_sql))
    try:
        rows = _con().execute(sql, [match] + params + [k]).fetchall()
    except sqlite3.OperationalError as e:
        # A malformed MATCH must degrade to "no keyword hits", never take down a
        # search that the vector half can still answer.
        if "fts5" in str(e).lower() or "malformed" in str(e).lower():
            return []
        raise
    out = []
    for r in rows:
        h = dict(zip(SELECT_COLS, r))
        h["id"] = h.pop("chunk_id")
        h["keyword_score"] = round(-float(r[-1]), 4)
        out.append(h)
    return out
