"""
Postgres-backed experience store for the S4PC brain (serverless / shared target).

Mirrors the two RUNTIME functions the MCP server needs from catalog/db.py —
load_experience() and append_experience(entry) — but backed by the same Aurora
Postgres the pgvector brain uses (PGVECTOR_DSN). This is what makes record_experience
work on Lambda, whose filesystem is read-only (SQLite writes are impossible there).

Selected with EXPERIENCE_BACKEND=postgres; the default stays SQLite so the local/EC2
POC is byte-for-byte unchanged. On first use, if the experience table is empty, it
backfills from the bundled SQLite seed (db.load_experience()) so a fresh deploy has
full delivery history. The git-tracked experience_db.json seed is kept current by a
nightly export (see lambda/README.md), not by a per-write sync.

Install: pip install psycopg2-binary
"""

import os
import re
import json

import db as _sqlite_store      # catalog/ is on sys.path; server.py imports it first. Stdlib-only,
                                # so this costs nothing and keeps ONE definition of _norm_tags.

_TABLE = os.environ.get("EXPERIENCE_TABLE", "experience")
if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", _TABLE):      # identifier, never user free-text
    raise ValueError("EXPERIENCE_TABLE must be a plain SQL identifier: %r" % _TABLE)

_CREATE = (
    "CREATE TABLE IF NOT EXISTS {t} ("
    "  id         text PRIMARY KEY,"
    "  category   text,"
    "  topic      text,"
    "  lesson     text,"
    "  impact     text,"
    "  tags       jsonb,"
    "  added      text,"
    "  source     text,"
    "  run_id     text,"
    "  agent      text,"
    "  created_at timestamptz DEFAULT now())"
).format(t=_TABLE)

# Additive, for a table created before run_id existed. CREATE TABLE IF NOT EXISTS
# would leave such a table untouched and every INSERT below would then fail.
_ADD_RUN_ID = "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS run_id text".format(t=_TABLE)
_ADD_AGENT  = "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS agent text".format(t=_TABLE)
# Every row predating attribution came from the RICEFW pipeline -- it was the only
# agent. Backfilling says so rather than leaving them null, because a column
# populated for new rows and null for old ones is worse than no column: a filter
# over it returns a subset that reads as a complete answer. Runs once; the WHERE
# clause makes a repeat a no-op and never overwrites a real value.
DEFAULT_AGENT = "ricefw-builder"
_BACKFILL_AGENT = ("UPDATE {t} SET agent=%s WHERE agent IS NULL OR agent=''"
                   ).format(t=_TABLE)

_INSERT = (
    "INSERT INTO {t}(id,category,topic,lesson,impact,tags,added,source,run_id,agent) "
    "VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) "
    "ON CONFLICT (id) DO UPDATE SET "
    "  category=EXCLUDED.category, topic=EXCLUDED.topic, lesson=EXCLUDED.lesson, "
    "  impact=EXCLUDED.impact, tags=EXCLUDED.tags, added=EXCLUDED.added, "
    "  source=EXCLUDED.source, run_id=EXCLUDED.run_id, agent=EXCLUDED.agent"
).format(t=_TABLE)

_SELECT = ("SELECT id,category,topic,lesson,impact,tags,added,source,run_id,agent "
           "FROM {t} ORDER BY id").format(t=_TABLE)


def _connect():
    import psycopg2  # lazy — importing this module stays cheap
    dsn = os.environ.get("PGVECTOR_DSN", "")
    con = psycopg2.connect(dsn, connect_timeout=5) if dsn else psycopg2.connect(connect_timeout=5)
    con.autocommit = True
    return con


def _insert(con, entry):
    with con.cursor() as cur:
        cur.execute(_INSERT, (
            entry.get("id"), entry.get("category"), entry.get("topic"),
            entry.get("lesson"), entry.get("impact"),
            # Same canonical spelling as the SQLite store — imported, not reimplemented,
            # so the two backends can never disagree about what a tag is.
            json.dumps(_sqlite_store._norm_tags(entry.get("tags")), ensure_ascii=False),
            entry.get("added"), entry.get("source"), entry.get("run_id") or None,
            entry.get("agent") or DEFAULT_AGENT))


def _ensure(con):
    """Create the table if needed; backfill from the bundled SQLite seed when empty."""
    with con.cursor() as cur:
        cur.execute(_CREATE)
        cur.execute(_ADD_RUN_ID)
        cur.execute(_ADD_AGENT)
        cur.execute(_BACKFILL_AGENT, (DEFAULT_AGENT,))
        cur.execute("SELECT count(*) FROM {t}".format(t=_TABLE))
        empty = cur.fetchone()[0] == 0
    if empty:
        try:
            import db as _seed          # catalog/ is on sys.path (added by server.py)
            for e in _seed.load_experience().get("entries", []):
                if e.get("id"):
                    _insert(con, e)
        except Exception:
            pass                        # a missing seed must not block the store


def load_experience():
    """Returns {"_meta": {...}, "entries": [...]}  — same shape as db.load_experience()."""
    con = _connect()
    try:
        _ensure(con)
        with con.cursor() as cur:
            cur.execute(_SELECT)
            rows = cur.fetchall()
        entries = []
        for r in rows:
            e = {"id": r[0], "category": r[1], "topic": r[2], "lesson": r[3],
                 "impact": r[4], "tags": r[5] or [], "added": r[6], "source": r[7]}
            if r[8]:                       # omitted when unset — matches db._exp_row
                e["run_id"] = r[8]
            # Always present, unlike run_id: an absent agent would be
            # indistinguishable from a lesson belonging to no agent, and the
            # backfill exists so that case cannot arise.
            e["agent"] = r[9] or DEFAULT_AGENT
            entries.append(e)
        return {"_meta": {"source": "Aurora Postgres (%s)" % _TABLE,
                          "note": "shared experience store; git seed exported nightly"},
                "entries": entries}
    finally:
        con.close()


def append_experience(entry):
    """Upsert one experience entry into Postgres (the shared store)."""
    con = _connect()
    try:
        _ensure(con)
        _insert(con, entry)
    finally:
        con.close()
