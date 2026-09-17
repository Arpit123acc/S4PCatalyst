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
    "  created_at timestamptz DEFAULT now())"
).format(t=_TABLE)

# Additive, for a table created before run_id existed. CREATE TABLE IF NOT EXISTS
# would leave such a table untouched and every INSERT below would then fail.
_ADD_RUN_ID = "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS run_id text".format(t=_TABLE)

_INSERT = (
    "INSERT INTO {t}(id,category,topic,lesson,impact,tags,added,source,run_id) "
    "VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) "
    "ON CONFLICT (id) DO UPDATE SET "
    "  category=EXCLUDED.category, topic=EXCLUDED.topic, lesson=EXCLUDED.lesson, "
    "  impact=EXCLUDED.impact, tags=EXCLUDED.tags, added=EXCLUDED.added, "
    "  source=EXCLUDED.source, run_id=EXCLUDED.run_id"
).format(t=_TABLE)

_SELECT = ("SELECT id,category,topic,lesson,impact,tags,added,source,run_id "
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
            entry.get("added"), entry.get("source"), entry.get("run_id") or None))


def _ensure(con):
    """Create the table if needed; backfill from the bundled SQLite seed when empty."""
    with con.cursor() as cur:
        cur.execute(_CREATE)
        cur.execute(_ADD_RUN_ID)
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
