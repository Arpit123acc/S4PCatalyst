"""
S4PC Catalog — SQLite persistence layer.

Drop-in replacement for the released_*.json + experience_db.json files.
Migrates automatically on first use; existing JSON files become read-only seeds.

Zero-dependency — sqlite3 is Python 3.9+ stdlib.
"""

import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "catalog.db")

_JSON_APIS       = os.path.join(_HERE, "released_apis.json")
_JSON_CDS        = os.path.join(_HERE, "released_cds_views.json")
_JSON_BADIS      = os.path.join(_HERE, "released_badis.json")
_JSON_EXPERIENCE = os.path.join(_HERE, "experience_db.json")
_JSON_LINT       = os.path.join(_HERE, "forbidden_patterns.json")


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS apis (
    name                   TEXT PRIMARY KEY,
    title                  TEXT,
    protocol               TEXT,
    area                   TEXT,
    communication_scenario TEXT,
    key_entities           TEXT,
    operations             TEXT,
    hub_url                TEXT,
    notes                  TEXT,
    source                 TEXT
);
CREATE TABLE IF NOT EXISTS cds_views (
    name     TEXT PRIMARY KEY,
    replaces TEXT,
    area     TEXT,
    notes    TEXT,
    source   TEXT
);
CREATE TABLE IF NOT EXISTS badis (
    name                TEXT PRIMARY KEY,
    title               TEXT,
    area                TEXT,
    extensibility_type  TEXT,
    business_context    TEXT,
    use_case            TEXT,
    verified_in_tenant  INTEGER DEFAULT 0,
    source              TEXT
);
CREATE TABLE IF NOT EXISTS experience (
    id       TEXT PRIMARY KEY,
    category TEXT,
    topic    TEXT,
    lesson   TEXT,
    impact   TEXT,
    tags     TEXT,
    added    TEXT,
    source   TEXT
);
CREATE TABLE IF NOT EXISTS lint_rules (
    id          TEXT PRIMARY KEY,
    severity    TEXT,
    pattern     TEXT,
    message     TEXT,
    alternative TEXT,
    examples    TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    """Open catalog.db, create schema, auto-migrate from JSON on first run."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    _auto_migrate(con)
    return con


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jdump(val):
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return val


def _jload(val):
    if val is None:
        return None
    if isinstance(val, str) and val[:1] in ("[", "{"):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return val


def _load_json_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


# ── Row -> dict converters ────────────────────────────────────────────────────

def _api_row(row):
    d = {
        "name":                   row["name"],
        "title":                  row["title"],
        "protocol":               row["protocol"],
        "area":                   row["area"],
        "communication_scenario": row["communication_scenario"],
        "key_entities":           _jload(row["key_entities"]),
        "operations":             _jload(row["operations"]),
        "hub_url":                row["hub_url"],
        "notes":                  row["notes"],
    }
    if row["source"]:
        d["_source"] = row["source"]
    return d


def _cds_row(row):
    d = {
        "name":     row["name"],
        "replaces": _jload(row["replaces"]),
        "area":     row["area"],
        "notes":    row["notes"],
    }
    if row["source"]:
        d["_source"] = row["source"]
    return d


def _badi_row(row):
    d = {
        "name":               row["name"],
        "title":              row["title"],
        "area":               row["area"],
        "extensibility_type": row["extensibility_type"],
        "business_context":   row["business_context"],
        "use_case":           row["use_case"],
        "verified_in_tenant": bool(row["verified_in_tenant"]),
    }
    if row["source"]:
        d["_source"] = row["source"]
    return d


def _exp_row(row):
    return {
        "id":       row["id"],
        "category": row["category"],
        "topic":    row["topic"],
        "lesson":   row["lesson"],
        "impact":   row["impact"],
        "tags":     _jload(row["tags"]) or [],
        "added":    row["added"],
        "source":   row["source"],
    }


def _lint_row(row):
    d = {
        "id":          row["id"],
        "severity":    row["severity"],
        "pattern":     row["pattern"],
        "message":     row["message"],
        "alternative": row["alternative"],
    }
    ex = _jload(row["examples"])
    if ex:
        d["examples"] = ex
    return d


# ── Meta key/value store ──────────────────────────────────────────────────────

def _get_meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return _jload(row["value"]) if row else None


def _set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, _jdump(value)))


# ── Load functions — return same shape as the original JSON files ─────────────

def load_apis():
    """Returns {"_meta": {...}, "apis": [...]}  (mirrors released_apis.json)."""
    con = get_conn()
    try:
        rows = con.execute("SELECT * FROM apis ORDER BY name").fetchall()
        meta = _get_meta(con, "_meta_apis") or {}
        return {"_meta": meta, "apis": [_api_row(r) for r in rows]}
    finally:
        con.close()


def load_cds_views():
    """Returns {"_meta": {...}, "views": [...]}  (mirrors released_cds_views.json)."""
    con = get_conn()
    try:
        rows = con.execute("SELECT * FROM cds_views ORDER BY name").fetchall()
        meta = _get_meta(con, "_meta_cds_views") or {}
        return {"_meta": meta, "views": [_cds_row(r) for r in rows]}
    finally:
        con.close()


def load_badis():
    """Returns {"_meta": {...}, "badis": [...]}  (mirrors released_badis.json)."""
    con = get_conn()
    try:
        rows = con.execute("SELECT * FROM badis ORDER BY name").fetchall()
        meta = _get_meta(con, "_meta_badis") or {}
        return {"_meta": meta, "badis": [_badi_row(r) for r in rows]}
    finally:
        con.close()


def load_experience():
    """Returns {"_meta": {...}, "entries": [...]}  (mirrors experience_db.json)."""
    con = get_conn()
    try:
        rows = con.execute("SELECT * FROM experience ORDER BY id").fetchall()
        meta = _get_meta(con, "_meta_experience") or {}
        return {"_meta": meta, "entries": [_exp_row(r) for r in rows]}
    finally:
        con.close()


def load_lint_rules():
    """Returns {"rules": [...]}  (mirrors forbidden_patterns.json)."""
    con = get_conn()
    try:
        rows = con.execute("SELECT * FROM lint_rules ORDER BY id").fetchall()
        return {"rules": [_lint_row(r) for r in rows]}
    finally:
        con.close()


# ── Write: experience (only catalog table written at runtime by the MCP server) ─

def append_experience(entry):
    """Upsert one experience entry into the database."""
    con = get_conn()
    try:
        con.execute(
            "INSERT OR REPLACE INTO experience(id,category,topic,lesson,impact,tags,added,source)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (entry.get("id"), entry.get("category"), entry.get("topic"),
             entry.get("lesson"), entry.get("impact"), _jdump(entry.get("tags")),
             entry.get("added"), entry.get("source"))
        )
        con.commit()
    finally:
        con.close()


def sync_experience_to_seed(entry):
    """Auto-sync one entry to experience_db.json immediately after it is recorded.

    Called by the MCP server every time record_experience fires, so the JSON seed
    stays current without any manual export step. Teammates get the lesson on the
    next git pull — no button click required.
    Skips silently if the entry id is already in the seed (idempotent).
    """
    try:
        try:
            with open(_JSON_EXPERIENCE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError):
            data = {"_meta": {}, "entries": []}
        if any(e.get("id") == entry.get("id") for e in data.get("entries", [])):
            return  # already present — nothing to do
        data.setdefault("entries", []).append(entry)
        with open(_JSON_EXPERIENCE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except Exception:
        pass  # never fail the pipeline run over a seed write


# ── Write: catalog sync (used exclusively by sync_hub.py) ────────────────────

_API_BACKFILL  = ("hub_url", "title", "communication_scenario", "notes", "protocol", "area")
_CDS_BACKFILL  = ("notes",)
_BADI_BACKFILL = ("title", "use_case", "business_context", "area")


def merge_apis(hub_entries, backfill_fields=_API_BACKFILL):
    """Upsert API entries from the Hub. Returns (existing_count, added, backfilled)."""
    con = get_conn()
    try:
        existing_count = con.execute("SELECT COUNT(*) FROM apis").fetchone()[0]
        added = backfilled = 0
        for e in hub_entries:
            name = e.get("name")
            if not name:
                continue
            row = con.execute("SELECT * FROM apis WHERE name=?", (name,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT OR IGNORE INTO apis(name,title,protocol,area,communication_scenario,"
                    "key_entities,operations,hub_url,notes,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (name, e.get("title"), e.get("protocol"), e.get("area"),
                     e.get("communication_scenario"),
                     _jdump(e.get("key_entities")), _jdump(e.get("operations")),
                     e.get("hub_url"), e.get("notes"), e.get("_source"))
                )
                added += 1
            else:
                for f in backfill_fields:
                    if row[f] is None and e.get(f) is not None:
                        con.execute("UPDATE apis SET %s=? WHERE name=?" % f, (e[f], name))
                        backfilled += 1
        con.commit()
        return existing_count, added, backfilled
    finally:
        con.close()


def merge_cds_views(hub_entries, backfill_fields=_CDS_BACKFILL):
    """Upsert CDS view entries from the Hub. Returns (existing_count, added, backfilled)."""
    con = get_conn()
    try:
        existing_count = con.execute("SELECT COUNT(*) FROM cds_views").fetchone()[0]
        added = backfilled = 0
        for e in hub_entries:
            name = e.get("name")
            if not name:
                continue
            row = con.execute("SELECT * FROM cds_views WHERE name=?", (name,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT OR IGNORE INTO cds_views(name,replaces,area,notes,source)"
                    " VALUES(?,?,?,?,?)",
                    (name, _jdump(e.get("replaces")), e.get("area"),
                     e.get("notes"), e.get("_source"))
                )
                added += 1
            else:
                for f in backfill_fields:
                    if row[f] is None and e.get(f) is not None:
                        con.execute("UPDATE cds_views SET %s=? WHERE name=?" % f, (e[f], name))
                        backfilled += 1
        con.commit()
        return existing_count, added, backfilled
    finally:
        con.close()


def merge_badis(hub_entries, backfill_fields=_BADI_BACKFILL):
    """Upsert BAdI entries from the Hub. Returns (existing_count, added, backfilled)."""
    con = get_conn()
    try:
        existing_count = con.execute("SELECT COUNT(*) FROM badis").fetchone()[0]
        added = backfilled = 0
        for e in hub_entries:
            name = e.get("name")
            if not name:
                continue
            row = con.execute("SELECT * FROM badis WHERE name=?", (name,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT OR IGNORE INTO badis(name,title,area,extensibility_type,"
                    "business_context,use_case,verified_in_tenant,source) VALUES(?,?,?,?,?,?,?,?)",
                    (name, e.get("title"), e.get("area"), e.get("extensibility_type"),
                     e.get("business_context"), e.get("use_case"),
                     1 if e.get("verified_in_tenant") else 0, e.get("_source"))
                )
                added += 1
            else:
                for f in backfill_fields:
                    if row[f] is None and e.get(f) is not None:
                        con.execute("UPDATE badis SET %s=? WHERE name=?" % f, (e[f], name))
                        backfilled += 1
        con.commit()
        return existing_count, added, backfilled
    finally:
        con.close()


# ── Auto-migration from JSON files ────────────────────────────────────────────

def _auto_migrate(con):
    """Run migration exactly once (guarded by the _migrated flag in the meta table)."""
    if _get_meta(con, "_migrated"):
        return
    _do_migrate(con)
    _set_meta(con, "_migrated", "done")
    con.commit()


def _do_migrate(con):
    # APIs
    data = _load_json_file(_JSON_APIS, {"apis": [], "_meta": {}})
    if data.get("_meta"):
        _set_meta(con, "_meta_apis", data["_meta"])
    for e in data.get("apis", []):
        if not e.get("name"):
            continue
        con.execute(
            "INSERT OR IGNORE INTO apis(name,title,protocol,area,communication_scenario,"
            "key_entities,operations,hub_url,notes,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (e.get("name"), e.get("title"), e.get("protocol"), e.get("area"),
             e.get("communication_scenario"),
             _jdump(e.get("key_entities")), _jdump(e.get("operations")),
             e.get("hub_url"), e.get("notes"), e.get("_source"))
        )

    # CDS Views
    data = _load_json_file(_JSON_CDS, {"views": [], "_meta": {}})
    if data.get("_meta"):
        _set_meta(con, "_meta_cds_views", data["_meta"])
    for e in data.get("views", []):
        if not e.get("name"):
            continue
        con.execute(
            "INSERT OR IGNORE INTO cds_views(name,replaces,area,notes,source) VALUES(?,?,?,?,?)",
            (e.get("name"), _jdump(e.get("replaces")), e.get("area"),
             e.get("notes"), e.get("_source"))
        )

    # BAdIs
    data = _load_json_file(_JSON_BADIS, {"badis": [], "_meta": {}})
    if data.get("_meta"):
        _set_meta(con, "_meta_badis", data["_meta"])
    for e in data.get("badis", []):
        if not e.get("name"):
            continue
        con.execute(
            "INSERT OR IGNORE INTO badis(name,title,area,extensibility_type,"
            "business_context,use_case,verified_in_tenant,source) VALUES(?,?,?,?,?,?,?,?)",
            (e.get("name"), e.get("title"), e.get("area"), e.get("extensibility_type"),
             e.get("business_context"), e.get("use_case"),
             1 if e.get("verified_in_tenant") else 0, e.get("_source"))
        )

    # Experience
    data = _load_json_file(_JSON_EXPERIENCE, {"entries": [], "_meta": {}})
    if data.get("_meta"):
        _set_meta(con, "_meta_experience", data["_meta"])
    for e in data.get("entries", []):
        if not e.get("id"):
            continue
        con.execute(
            "INSERT OR IGNORE INTO experience(id,category,topic,lesson,impact,tags,added,source)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (e.get("id"), e.get("category"), e.get("topic"), e.get("lesson"),
             e.get("impact"), _jdump(e.get("tags")), e.get("added"), e.get("source"))
        )

    # Lint rules
    data = _load_json_file(_JSON_LINT, {"rules": []})
    for r in data.get("rules", []):
        if not r.get("id"):
            continue
        con.execute(
            "INSERT OR IGNORE INTO lint_rules(id,severity,pattern,message,alternative,examples)"
            " VALUES(?,?,?,?,?,?)",
            (r.get("id"), r.get("severity"), r.get("pattern"), r.get("message"),
             r.get("alternative"), _jdump(r.get("examples")))
        )

    n_apis = con.execute("SELECT COUNT(*) FROM apis").fetchone()[0]
    n_cds  = con.execute("SELECT COUNT(*) FROM cds_views").fetchone()[0]
    n_badi = con.execute("SELECT COUNT(*) FROM badis").fetchone()[0]
    n_exp  = con.execute("SELECT COUNT(*) FROM experience").fetchone()[0]
    n_lint = con.execute("SELECT COUNT(*) FROM lint_rules").fetchone()[0]
    print("catalog.db: migrated %d APIs, %d CDS views, %d BAdIs, %d experience entries, %d lint rules"
          % (n_apis, n_cds, n_badi, n_exp, n_lint))


# ── CLI: force re-migration ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    if force:
        con = sqlite3.connect(DB_PATH)
        con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        con.execute("DELETE FROM meta WHERE key='_migrated'")
        con.commit()
        con.close()
        print("Migration flag cleared.")
    get_conn().close()
    print("Done: %s" % DB_PATH)
