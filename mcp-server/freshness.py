"""Cross-layer freshness — does each layer still describe the same world?

WHY THIS EXISTS
    The brain is four stores, and three of them are DERIVED from the others:

      L1  graph/graph.json          built from the released-object catalog
      L2  vector/index.json         built from L1's objects + L3's lessons + runs
      L3  catalog/experience_db     the source of truth for lessons
      L4  brain/index/*             FAISS vectors + keyword.db + object mentions

    A derived store that silently stops matching its source is this project's most
    expensive failure mode, because a stale answer is indistinguishable from a current
    one. It had already happened, unnoticed: on 2026-09-07 L3 held 32 lessons while
    L2 had indexed 29, so `find_similar_delivery` could not see the three most recent
    lessons and said so to nobody. `record_experience` appends to L3 and does not
    touch L2, and nothing compared the two.

    L4 is the layer that got this right -- monthly_refresh.sh rebuilds it and a
    40-case regression gates the result. This module gives L1/L2/L3 the same kind of
    check, cheaply enough to run inside an MCP call.

WHAT IT DOES NOT DO
    It does not rebuild anything. Rebuilding L2 means re-embedding ~10.8k documents,
    which is not something a status call should trigger as a side effect. This reports;
    `rebuild_vector_index` fixes.

Pure stdlib, no imports from the rest of the server, so it cannot itself be the thing
that breaks a status call.
"""

import json
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)

L1_PATH   = os.path.join(BASE_DIR, "graph", "graph.json")
L2_PATH   = os.path.join(BASE_DIR, "vector", "index.json")
L2_EMB    = os.path.join(BASE_DIR, "vector", "index.npy")
L3_PATH   = os.path.join(BASE_DIR, "catalog", "experience_db.json")
L4_KEYWORD = os.path.join(REPO_DIR, "brain", "index", "keyword.db")
L4_VECTORS = os.path.join(REPO_DIR, "brain", "index", "metadata.json")
L4_FAISS   = os.path.join(REPO_DIR, "brain", "index", "faiss.index")

# L2 indexes these three object types out of L1. Kept as a constant because the
# L1<->L2 comparison is only meaningful over the types L2 actually ingests.
_L1_TYPES_IN_L2 = ("api", "cds_view", "badi")


def _stamp(path):
    """mtime as UTC ISO, or None. The cheap half of every check below."""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
    except OSError:
        return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "not built"
    except Exception as exc:
        return None, "unreadable: %s" % exc


def _l1():
    data, err = _read_json(L1_PATH)
    if data is None:
        return {"present": False, "error": err, "built_at": _stamp(L1_PATH)}
    stats = data.get("stats") or {}
    return {"present": True, "built_at": _stamp(L1_PATH),
            "nodes": stats.get("nodes") or len(data.get("nodes") or {}),
            "edges": stats.get("edges"), "areas": stats.get("areas"),
            "by_type": stats.get("by_type") or {}}


def _l2():
    data, err = _read_json(L2_PATH)
    if data is None:
        return {"present": False, "error": err, "built_at": _stamp(L2_PATH)}
    docs = data.get("docs") or []
    by_type = {}
    for d in docs:
        t = d.get("type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    return {"present": True, "built_at": _stamp(L2_PATH),
            # The engine the index was BUILT with, which is what its scores mean --
            # not this host's configured preference. See engine.index_meta().
            "engine": data.get("engine"), "model": data.get("model"),
            "docs": len(docs), "by_type": by_type,
            "embeddings_present": os.path.exists(L2_EMB)}


def _l3():
    data, err = _read_json(L3_PATH)
    if data is None:
        return {"present": False, "error": err, "built_at": _stamp(L3_PATH)}
    entries = data.get("entries") or []
    return {"present": True, "built_at": _stamp(L3_PATH), "lessons": len(entries)}


def _l4(deep=False):
    out = {"present": os.path.exists(L4_FAISS) or os.path.exists(L4_KEYWORD),
           "vectors_built_at": _stamp(L4_VECTORS),
           "keyword_built_at": _stamp(L4_KEYWORD),
           "vector_index_present": os.path.exists(L4_FAISS),
           "keyword_index_present": os.path.exists(L4_KEYWORD)}
    if os.path.exists(L4_KEYWORD):
        import sqlite3
        try:
            con = sqlite3.connect("file:%s?mode=ro" % L4_KEYWORD, uri=True)
            out["keyword_rows"] = con.execute("SELECT count(*) FROM meta").fetchone()[0]
            has_m = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='object_mentions'").fetchone()
            out["mentions_indexed"] = bool(has_m)
            if has_m:
                rows, objs = con.execute(
                    "SELECT count(*), count(DISTINCT object_name) "
                    "FROM object_mentions").fetchone()
                out["mention_rows"], out["distinct_objects"] = rows, objs
            con.close()
        except Exception as exc:
            out["keyword_error"] = str(exc)
    # metadata.json is a positional LIST of ~50k entries, so counting it costs a full
    # parse. Only on request -- the mtime comparison below catches the failure that
    # actually happens (vectors rebuilt, keyword index not) without paying for it.
    if deep and os.path.exists(L4_VECTORS):
        data, err = _read_json(L4_VECTORS)
        out["vector_rows"] = len(data) if isinstance(data, list) else None
        if err:
            out["vector_error"] = err
    return out


def experience_index_lag():
    """Has L3 changed since L2 was built? Two stat() calls, no JSON parse.

    For the HOT path -- query_experience / record_experience -- where parsing the
    ~10.8k-doc index.json that report() reads would be an unreasonable per-call cost.
    It answers a slightly weaker question than the exact count ("L3 moved after L2 was
    built" rather than "L2 is short by 3"), but that is the same actionable signal and
    the same fix, so it is what belongs on a tool response.

    Returns a human-readable warning, or None when in step.
    """
    l2, l3 = _stamp(L2_PATH), _stamp(L3_PATH)
    if not l2 or not l3 or l3 <= l2:
        return None
    return ("The semantic index (L2) was built %s, but the lesson store (L3) changed "
            "%s. semantic_search and find_similar_delivery therefore may not see the "
            "newest lessons — they fail by returning less, which looks like a normal "
            "result. Fix: rebuild_vector_index (or layer_health for the exact gap)."
            % (l2, l3))


def _check(name, status, detail, fix=None):
    entry = {"check": name, "status": status, "detail": detail}
    if fix:
        entry["fix"] = fix
    return entry


def consistency(layers):
    """Compare each derived store against its source. STALE means rebuild needed."""
    checks = []
    l1, l2, l3, l4 = (layers["L1_object_graph"], layers["L2_semantic_index"],
                      layers["L3_experience"], layers["L4_brain"])

    # L3 -> L2. The one that was already silently wrong.
    if l2.get("present") and l3.get("present"):
        indexed, actual = l2["by_type"].get("experience", 0), l3["lessons"]
        checks.append(_check(
            "L3_lessons_in_L2",
            "OK" if indexed == actual else "STALE",
            "L2 indexed %d lesson(s); L3 holds %d" % (indexed, actual),
            None if indexed == actual else
            "rebuild_vector_index — until then semantic_search and "
            "find_similar_delivery cannot see the %d newest lesson(s)"
            % abs(actual - indexed)))

    # L1 -> L2.
    if l2.get("present") and l1.get("present"):
        in_l2 = sum(l2["by_type"].get(t, 0) for t in _L1_TYPES_IN_L2)
        nodes = l1.get("nodes") or 0
        checks.append(_check(
            "L1_objects_in_L2",
            "OK" if in_l2 == nodes else "STALE",
            "L2 indexed %d catalog object(s); L1 graph has %d node(s)" % (in_l2, nodes),
            None if in_l2 == nodes else
            "the catalog changed under one of them — rebuild both: "
            "python mcp-server/graph/build_graph.py && "
            "python mcp-server/vector/build_index.py"))

    # L4's two halves must describe the same corpus: they are joined on chunk id at
    # query time, so a keyword index older than the vectors yields hits with no score.
    if l4.get("vectors_built_at") and l4.get("keyword_built_at"):
        in_step = l4["keyword_built_at"] >= l4["vectors_built_at"]   # ISO sorts
        checks.append(_check(
            "L4_halves_in_step",
            "OK" if in_step else "STALE",
            "keyword index built %s; vectors built %s"
            % (l4["keyword_built_at"], l4["vectors_built_at"]),
            None if in_step else
            "vectors were rebuilt after the keyword index — run "
            "python3.11 scripts/keyword_index.py"))

    # The mention table backs both directions of the document<->object edge.
    if l4.get("keyword_index_present"):
        indexed = l4.get("mentions_indexed")
        checks.append(_check(
            "L4_object_mentions",
            "OK" if indexed else "STALE",
            "%d mention(s) of %d distinct object(s)"
            % (l4.get("mention_rows", 0), l4.get("distinct_objects", 0))
            if indexed else "keyword.db predates the object-mention index",
            None if indexed else
            "rebuild with python3.11 scripts/keyword_index.py — until then "
            "get_object_usage reports 'not indexed', NOT 'never used'"))

    for key, layer in layers.items():
        if not layer.get("present"):
            checks.append(_check("%s_present" % key, "MISSING",
                                 layer.get("error") or "not built"))
    return checks


def report(deep=False):
    """Full cross-layer freshness report. `status` is STALE if any check is."""
    layers = {"L1_object_graph": _l1(), "L2_semantic_index": _l2(),
              "L3_experience": _l3(), "L4_brain": _l4(deep=deep)}
    checks = consistency(layers)
    bad = [c for c in checks if c["status"] != "OK"]
    return {
        "status": "OK" if not bad else "ATTENTION",
        "layers": layers,
        "consistency": checks,
        "attention": [c["check"] for c in bad],
        "note": ("L1/L2 are DERIVED from the released-object catalog and L3; L4 is a "
                 "separate corpus. STALE means a derived store no longer matches its "
                 "source, which reads exactly like a current answer — rebuild before "
                 "trusting the affected layer. MISSING means never built on this host "
                 "(expected for L4 off the delivery server)."),
    }
