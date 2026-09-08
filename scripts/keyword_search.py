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

TWO MORE DIRECTIONS OVER THE SAME DB
    keyword_index.py also records which SAP object names appear in which chunk, so
    this module answers both directions of the document<->object edge:
      mentions_for_chunks()   forward -- annotate a page of hits in ONE query
      documents_for_object()  reverse -- "which delivery documents mention EKKO?"
    Both degrade to an explicit "not indexed" rather than to a bare empty result,
    because "never mentioned" and "never indexed" are opposite claims to a reader.

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
# Document-lifecycle columns, added 2026-09-07. Selected SEPARATELY from SELECT_COLS
# because a keyword.db built before that change does not have them, and a single
# SELECT naming them would fail outright rather than degrade -- see _lifecycle_cols().
LIFECYCLE_COLS = ["doc_family", "doc_version", "is_current", "superseded_by"]

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


@lru_cache(maxsize=1)
def _lifecycle_cols():
    """Which lifecycle columns this keyword.db actually has.

    Returns () for an index built before the columns existed, so every query below
    degrades to "no lifecycle information" instead of raising OperationalError. Same
    reasoning as has_mentions(): the caller must be able to tell "not indexed" from
    "this document is current", because they are different claims.
    """
    try:
        have = {r[1] for r in _con().execute("PRAGMA table_info(meta)").fetchall()}
    except Exception:
        return ()
    return tuple(c for c in LIFECYCLE_COLS if c in have)


def _folder(doc):
    """The folder a document row lives in, "" when the index carries no path.

    Normalised across separators because relative_path is produced by pathlib on the
    ingest host: Linux writes "MM/Spec.docx", a Windows ingest writes "MM\\Spec.docx",
    and the same corpus must bucket identically either way.
    """
    p = (doc.get("relative_path") or "").replace("\\", "/")
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _collapse_by_folder(docs):
    """doc_version.collapse, applied WITHIN each folder rather than across the corpus.

    Collapsing globally merges any rows sharing a filename, which undoes the whole
    point of carrying relative_path: "Interface Spec.docx" in MM/ and in SD/ went back
    to being one row with its mentions summed.

    Within a folder is the right scope because revisions of one artifact are filed
    together -- so 850_Purchase Order_v2.0 .. _v11.0 still collapse to one entry --
    while two documents that merely share a name do not merge. When they genuinely are
    the same artifact filed twice, the cost is one extra row; when they are different
    documents, merging them would attribute one's content to the other. A false merge
    is worse than a missed one, the same doctrine resolve_families follows.

    Note this is only about COUNTING and DISPLAY. is_current / superseded_by are still
    resolved corpus-globally at index time, so a revision filed in the wrong folder is
    still correctly marked superseded.
    """
    try:
        import doc_version                                # noqa: PLC0415
    except Exception:
        return docs                                       # collapsing is a nicety
    if not has_path():
        return doc_version.collapse(docs)
    buckets = {}
    for d in docs:
        buckets.setdefault(_folder(d), []).append(d)
    out = []
    for group in buckets.values():
        out.extend(doc_version.collapse(group))
    return out


@lru_cache(maxsize=1)
def has_path():
    """Whether this keyword.db carries relative_path (added 2026-09-08).

    `source` is a filename, so two documents with the same name in different folders
    were indistinguishable and got grouped into one row. An index built before the
    column exists must degrade to "no path information" rather than raise -- and, more
    importantly, must keep grouping the old way, because splitting on a column that is
    NULL for every row would report every document as having one unknown location.
    """
    try:
        have = {r[1] for r in _con().execute("PRAGMA table_info(meta)").fetchall()}
    except Exception:
        return False
    return "relative_path" in have


def lifecycle_for_chunks(chunk_ids):
    """{chunk_id: {doc_family, doc_version, is_current, superseded_by}} for these chunks.

    One query for a whole page of hits, and it covers hits the VECTOR half found on
    its own -- those come from metadata.json, which carries no lifecycle fields, so
    without this join a vector-only hit could never be marked superseded.
    """
    cols = _lifecycle_cols()
    ids = [c for c in (chunk_ids or []) if c]
    if not ids or not cols:
        return {}
    out = {}
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        marks = ",".join("?" * len(batch))
        sql = ("SELECT chunk_id, %s FROM meta WHERE chunk_id IN (%s)"
               % (", ".join(cols), marks))
        try:
            rows = _con().execute(sql, batch).fetchall()
        except sqlite3.OperationalError:
            return {}
        for r in rows:
            rec = dict(zip(cols, r[1:]))
            if "is_current" in rec and rec["is_current"] is not None:
                rec["is_current"] = bool(rec["is_current"])   # SQLite has no bool
            out[r[0]] = rec
    return out


@lru_cache(maxsize=1)
def has_mentions():
    """Whether this keyword.db carries the object_mentions table (added 2026-09-07).

    A keyword.db built before that change has no such table, and every mention query
    would raise. Callers must be able to tell "not indexed yet" apart from "this
    object was never mentioned" -- they mean opposite things to a reader, and only the
    first is fixed by rebuilding. freshness.py reports on exactly this.
    """
    try:
        return bool(_con().execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='object_mentions'"
        ).fetchone())
    except Exception:
        return False


def mentions_for_chunks(chunk_ids):
    """FORWARD edge: {chunk_id: [object names as written]} for the given chunks.

    One query for a whole page of search hits, rather than the previous design's one
    file read per hit. Returns {} when the table is absent so annotation degrades to
    "no annotation" instead of taking down the search.
    """
    ids = [c for c in (chunk_ids or []) if c]
    if not ids or not has_mentions():
        return {}
    out = {}
    # Chunked IN(...) to stay clear of SQLITE_MAX_VARIABLE_NUMBER on a big top_k.
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        marks = ",".join("?" * len(batch))
        sql = ("SELECT m.chunk_id, om.display_name FROM object_mentions om "
               "JOIN meta m ON m.rowid = om.chunk_rowid "
               "WHERE m.chunk_id IN (%s)" % marks)
        try:
            rows = _con().execute(sql, batch).fetchall()
        except sqlite3.OperationalError:
            return {}
        for chunk_id, name in rows:
            bucket = out.setdefault(chunk_id, [])
            if name not in bucket:
                bucket.append(name)
    return out


def usage_counts_for_objects(names, source_system=None):
    """REVERSE edge, BATCHED: {OBJECT_NAME: {mentions, documents, filenames}}.

    documents_for_object answers the same question in full for ONE object, which is
    the right shape for get_object_usage and the wrong shape for annotating a page of
    hits: N objects would mean N round-trips for a count the caller only wants as a
    signal. This is one query for all of them, and deliberately returns counts only --
    a caller that wants the document list has get_object_usage.

    `documents` is distinct ARTIFACTS (families), matching documents_for_object's
    total_artifacts, so the two can never disagree about how much precedent exists.
    Returns {} when the mention table is absent, so annotation degrades to "no
    annotation" rather than to the false claim "no prior usage".
    """
    wanted, seen = [], set()
    for n in (names or []):
        s = str(n or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            wanted.append(s)
    if not wanted or not has_mentions():
        return {}
    fam_expr = ("coalesce(m.doc_family, m.source)" if _lifecycle_cols()
                else "m.source")
    out = {}
    for start in range(0, len(wanted), 400):     # SQLITE_MAX_VARIABLE_NUMBER
        batch = wanted[start:start + 400]
        marks = ",".join("?" * len(batch))
        sql = ("SELECT om.object_name, count(*), count(DISTINCT %s), "
               "count(DISTINCT m.source) "
               "FROM object_mentions om JOIN meta m ON m.rowid = om.chunk_rowid "
               "WHERE om.object_name IN (%s)" % (fam_expr, marks))
        params = list(batch)
        if source_system:
            sql += " AND lower(coalesce(m.source_system,'')) = ?"
            params.append(str(source_system).lower())
        sql += " GROUP BY om.object_name"
        try:
            rows = _con().execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return {}
        for nm, mentions, artifacts, filenames in rows:
            out[nm] = {"mentions": mentions, "documents": artifacts,
                       "filenames": filenames}
    return out


def documents_for_objects(names, limit=5, source_system=None,
                          collapse_versions=True):
    """Which corpus documents mention the MOST of these objects, ranked by overlap.

    The join a recorded lesson needs. A lesson names a handful of SAP objects; the
    delivery documents naming the same ones are the closest thing to its evidence,
    and until now there was no edge from L3 back into L4 at all.

    Ranked by how many DISTINCT requested objects each document shares, before raw
    mention count -- sharing three of a lesson's objects is a stronger link than
    naming one of them thirty times, and ordering by mentions alone would surface the
    corpus's largest spreadsheets for every lesson.

    Note on collapsing: `shared` and `mentions` are the SURVIVING revision's own
    figures, not the family's. doc_version.collapse sums `mentions` across a family by
    design, which is right for "how much precedent exists" and wrong here -- the
    question is which single document to go read.
    """
    wanted, seen = [], set()
    for n in (names or []):
        s = str(n or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            wanted.append(s)
    if not wanted or not has_mentions():
        return []
    marks = ",".join("?" * len(wanted[:400]))
    params = list(wanted[:400])
    where = "om.object_name IN (%s)" % marks
    if source_system:
        where += " AND lower(coalesce(m.source_system,'')) = ?"
        params.append(str(source_system).lower())
    lifecycle = bool(_lifecycle_cols())
    # Over-fetch before collapsing, for the same reason documents_for_object does: a
    # versioned family can hold a dozen members and would otherwise fill the page.
    fetch = int(limit) * 12 if collapse_versions and lifecycle else int(limit)
    path_expr = "m.relative_path" if has_path() else "NULL"
    group_by = ("m.source, m.source_system, m.relative_path" if has_path()
                else "m.source, m.source_system")
    sql = ("SELECT m.source, m.source_system, m.deliverable_type, m.phase, "
           "count(DISTINCT om.object_name) AS shared, count(*) AS hits, "
           "group_concat(DISTINCT om.display_name), min(m.chunk_id), %s " % path_expr +
           "FROM object_mentions om JOIN meta m ON m.rowid = om.chunk_rowid "
           "WHERE " + where +
           " GROUP BY " + group_by +
           " ORDER BY shared DESC, hits DESC, m.source LIMIT ?")
    try:
        rows = _con().execute(sql, params + [fetch]).fetchall()
    except sqlite3.OperationalError:
        return []
    docs = [{"source": r[0], "source_system": r[1], "deliverable_type": r[2],
             "phase": r[3], "shared_objects": r[4], "mentions": r[5],
             "objects": sorted((r[6] or "").split(",")) if r[6] else [],
             "sample_chunk_id": r[7], "relative_path": r[8]} for r in rows]
    if collapse_versions:
        docs = _collapse_by_folder(docs)
        docs.sort(key=lambda d: (-(d.get("shared_objects") or 0),
                                 -(d.get("mentions") or 0), d.get("source") or ""))
    return docs[:int(limit)]


def documents_for_object(object_name, limit=10, source_system=None,
                         collapse_versions=True):
    """REVERSE edge: which corpus documents mention this SAP object.

    The question a delivery accelerator actually gets asked ("have we used this
    before, and where?") and the one no amount of query-time extraction could answer,
    because it requires having looked at every chunk rather than at the ones a query
    happened to return.

    Returns {"indexed": bool, "documents": [...], "total_mentions": n,
             "total_documents": n}. `indexed=False` means the table is missing --
    report that as "not indexed", never as "no prior usage".
    """
    name = (object_name or "").strip().upper()
    if not name:
        return {"indexed": has_mentions(), "documents": [], "total_mentions": 0,
                "total_documents": 0, "total_artifacts": 0}
    if not has_mentions():
        return {"indexed": False, "documents": [], "total_mentions": 0,
                "total_documents": 0, "total_artifacts": 0}
    where, params = "om.object_name = ?", [name]
    if source_system:
        where += " AND lower(coalesce(m.source_system,'')) = ?"
        params.append(str(source_system).lower())
    base = ("FROM object_mentions om JOIN meta m ON m.rowid = om.chunk_rowid "
            "WHERE " + where)
    # Distinct ARTIFACTS, not distinct filenames. The raw count is what made the first
    # real query read "104 mentions across 21 documents" when ten of those documents
    # were versions of one EDI spec. Falls back to source on a pre-lifecycle index.
    lifecycle = bool(_lifecycle_cols())
    fam_expr = "coalesce(m.doc_family, m.source)" if lifecycle else "m.source"
    # Two documents can share a FILENAME while living in different folders, and
    # grouping on the name alone merged them into one row with their mentions summed.
    # Split on the path where the index has one; a pre-path index keeps the old
    # grouping, because splitting on a column that is NULL everywhere would report
    # every document as having a single unknown location.
    path_expr = "m.relative_path" if has_path() else "NULL"
    group_by = ("m.source, m.source_system, m.relative_path" if has_path()
                else "m.source, m.source_system")
    # coalesce to the filename, because count(DISTINCT) skips NULL: webdocs and
    # scope-catalog rows carry no path, and counting them as zero locations would make
    # total_locations read lower than total_documents for no reason.
    loc_expr = ("coalesce(m.relative_path, m.source)" if has_path() else "m.source")
    try:
        total_mentions, total_docs, total_artifacts, total_locations = _con().execute(
            "SELECT count(*), count(DISTINCT m.source), count(DISTINCT %s), "
            "count(DISTINCT %s) %s" % (fam_expr, loc_expr, base),
            params).fetchone()
        # Over-fetch before collapsing: a versioned family can hold a dozen members,
        # so applying LIMIT first would fill the page with one artifact's revisions
        # and drop genuinely different documents off the end.
        fetch = int(limit) * 12 if collapse_versions and lifecycle else int(limit)
        rows = _con().execute(
            "SELECT m.source, m.source_system, m.deliverable_type, m.phase, "
            "count(*) AS hits, min(m.chunk_id), %s " % path_expr + base +
            " GROUP BY " + group_by + " ORDER BY hits DESC, m.source LIMIT ?",
            params + [fetch]).fetchall()
    except sqlite3.OperationalError:
        return {"indexed": False, "documents": [], "total_mentions": 0,
                "total_documents": 0, "total_artifacts": 0}
    docs = [{"source": r[0], "source_system": r[1], "deliverable_type": r[2],
             "phase": r[3], "mentions": r[4], "sample_chunk_id": r[5],
             "relative_path": r[6]} for r in rows]
    if collapse_versions:
        docs = _collapse_by_folder(docs)
        docs.sort(key=lambda d: (-(d.get("mentions") or 0), d.get("source") or ""))
    docs = docs[:int(limit)]
    out = {"indexed": True, "documents": docs,
           "total_mentions": total_mentions, "total_documents": total_docs,
           "total_artifacts": total_artifacts, "collapsed": bool(collapse_versions)}
    # Reported only when it ADDS something. total_locations > total_documents means
    # the same filename exists in more than one folder, which is the case a reader
    # needs told: it is either the same artifact filed twice, or two different
    # documents sharing a name, and nothing here can tell those apart.
    if has_path():
        out["total_locations"] = total_locations
        if total_locations > total_docs:
            out["path_note"] = (
                "%d file location(s) for %d distinct filename(s) — at least one name "
                "exists in more than one folder. Compare relative_path before treating "
                "two hits as the same document."
                % (total_locations, total_docs))
    return out


def search(query, k=10, filters=None):
    """Top-k BM25 hits. Returns [{keyword_score, id, source, ...}], best first.

    keyword_score is NEGATED bm25 so that, as with cosine, higher is better.
    (SQLite's bm25() returns more-negative for better matches.)
    """
    match = build_match(query)
    if not match:
        return []
    where_sql, params = _where(filters)
    # relative_path is appended CONDITIONALLY rather than living in SELECT_COLS: a
    # keyword.db built before the column exists would fail this SELECT outright
    # instead of degrading, which is the same reason LIFECYCLE_COLS is kept separate.
    select = list(SELECT_COLS) + (["relative_path"] if has_path() else [])
    cols = ", ".join("m.%s" % c for c in select)
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
        h = dict(zip(select, r))
        h["id"] = h.pop("chunk_id")
        h["keyword_score"] = round(-float(r[-1]), 4)
        out.append(h)
    return out
