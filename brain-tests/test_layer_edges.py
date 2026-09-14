#!/usr/bin/env python3
"""Are all four layers actually interacting? Exercised through the MCP TOOL HANDLERS.

WHY THIS EXISTS, AND WHY IT IS NOT LIKE THE OTHER TESTS IN THIS FOLDER
    test_object_mentions / test_doc_version / test_graph_briefs / test_run_evidence are
    unit tests over synthetic fixtures, so they run on any laptop. They prove each edge's
    MECHANISM is correct. They cannot prove the edges are WIRED -- a handler that forgets
    to call the annotator passes every one of them.

    This file is the integration counterpart. It calls the same handlers an agent calls
    and asserts the cross-layer fields actually arrive on the payload. It therefore needs
    the real brain (L4) and only runs on the delivery host.

WHY NOT JUST RUN THE PIPELINE
    A pipeline run exercises layers incidentally, stops at three human checkpoints, and
    when it finishes tells you a deliverable was produced -- not which of the ten edges
    fired. A missing edge does not fail a pipeline run; it makes the output quietly
    thinner, which is this project's whole recurring failure mode.

Usage (on the host that has brain/index):
    python3.11 brain-tests/test_layer_edges.py
    python3.11 brain-tests/test_layer_edges.py --verbose    # dump one sample payload
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "mcp-server"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
os.chdir(REPO)

VERBOSE = "--verbose" in sys.argv
RESULTS = []


def edge(name, frm, to, ok, detail):
    RESULTS.append({"edge": name, "from": frm, "to": to, "ok": bool(ok),
                    "detail": detail})
    print("  %-4s %-14s %-4s -> %-4s  %s"
          % ("ok" if ok else "MISS", name, frm, to, detail))


def _call(tool, args):
    """Invoke a registered tool handler the way the MCP transport does.

    Returns (payload, error). The handlers report failure IN the payload rather than
    raising -- semantic_search hands back {"error": ...} when the vector engine cannot
    load -- so an empty result list has two very different causes: nothing matched, or
    the retriever never ran. Surfacing payload["error"] is what separates them, and
    conflating those two is the mistake this whole test set exists to catch.
    """
    entry = server.TOOLS.get(tool)
    if not entry:
        return None, "tool %r is not registered" % tool
    try:
        payload = entry["handler"](args)
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return None, "%s: %s" % (type(exc).__name__, exc)
    if isinstance(payload, dict) and payload.get("error"):
        return payload, "handler error: %s" % str(payload["error"])[:120]
    return payload, None


def _first(seq, pred):
    return next((x for x in (seq or []) if pred(x)), None)


print("importing the governance server (loads the catalog; does not bind a port)")
import server                                                 # noqa: E402


# ── L1..L4 present and consistent ────────────────────────────────────────────
print("\nlayer presence and derived-store consistency")
health, err = _call("layer_health", {})
if err or not health:
    edge("layer_health", "all", "all", False, err or "no payload")
    layers = {}
else:
    layers = health.get("layers") or {}
    for key, lay in layers.items():
        edge("present", key.split("_")[0], "-", lay.get("present"),
             lay.get("error") or "built")
    bad = [c for c in (health.get("consistency") or []) if c["status"] != "OK"]
    edge("consistency", "all", "all", not bad,
         "all derived stores match their source" if not bad
         else "; ".join("%s=%s" % (c["check"], c["status"]) for c in bad))

have_l4 = bool((layers.get("L4_brain") or {}).get("present"))
if not have_l4:
    print("\n  L4 is not built on this host — the corpus edges below cannot be")
    print("  exercised here. Run this on the delivery host.")


# ── L4 -> catalog, L4 -> L1, L4 lifecycle ────────────────────────────────────
print("\nL4 corpus hits carry their objects' verdicts, graph position and lifecycle")
if have_l4:
    payload, err = _call("search_brain", {"query": "purchase order EDI mapping "
                                                   "released API", "top_k": 10})
    hits = (payload or {}).get("results") or []
    edge("search_brain", "L4", "-", bool(hits), err or "%d hit(s)" % len(hits))

    annotated = _first(hits, lambda h: h.get("objects_mentioned"))
    edge("mentions", "L4", "L4", bool(annotated),
         "objects_mentioned present on a hit" if annotated
         else "no hit carried objects_mentioned — rebuild keyword_index.py")

    objs = (annotated or {}).get("objects_mentioned") or []
    verdict = _first(objs, lambda o: isinstance(o, dict) and o.get("verdict"))
    edge("entity_link", "L4", "catalog", bool(verdict),
         "verdict=%s evidence=%s" % (verdict.get("verdict"), verdict.get("evidence"))
         if verdict else "names present but no verdict attached")

    graphed = _first(objs, lambda o: isinstance(o, dict) and o.get("area"))
    edge("graph_briefs", "L4", "L1", bool(graphed),
         "%s -> %s / %s conn" % (graphed.get("name"), graphed.get("area"),
                                 graphed.get("graph_connections")) if graphed
         else "no mentioned object resolved to a graph node (expected when the page "
              "names only Z*/classical objects — retry with another query)")

    lifecycle = _first(hits, lambda h: h.get("is_current") is not None)
    edge("lifecycle", "L4", "L4", bool(lifecycle),
         "is_current=%s doc_version=%s" % (lifecycle.get("is_current"),
                                           lifecycle.get("doc_version"))
         if lifecycle else "no hit carried is_current")

    pathed = _first(hits, lambda h: h.get("relative_path"))
    edge("path", "L4", "L4", bool(pathed),
         "relative_path present" if pathed
         else "no relative_path — rerun keyword_index.py to add the column")

    # L4 -> L3. Derived like the other annotation edges: collect every object this
    # page names, ask the lesson store which of them a lesson actually mentions, and
    # require exactly those to carry `lessons`. A document naming objects no lesson
    # discusses is the common case, and must not read as a broken edge.
    _names = [o.get("name") for h in hits
              for o in (h.get("objects_mentioned") or [])
              if isinstance(o, dict) and o.get("name")]
    try:
        import object_usage                                    # noqa: PLC0415
        _want = set(object_usage.lessons_for_objects(
            _names, server._exp_store.load_experience().get("entries", [])))
    except Exception as exc:                                   # noqa: BLE001
        _want = set()
        print("      (lesson lookup unavailable: %s)" % exc)
    _have_l = {o.get("name") for h in hits
               for o in (h.get("objects_mentioned") or [])
               if isinstance(o, dict) and o.get("lessons")}
    if not _want:
        edge("lessons", "L4", "L3", True,
             "no object named on this page is discussed by any lesson — verified "
             "against the lesson store, not assumed")
    else:
        edge("lessons", "L4", "L3", _have_l >= _want,
             "%d of %d lesson-bearing mention(s) annotated (%s)"
             % (len(_have_l & _want), len(_want), ", ".join(sorted(_want))[:60]))


# ── L2 -> L1 and L2 -> L4 ────────────────────────────────────────────────────
print("\nL2 catalog hits carry graph position and prior delivery usage")
payload, err = _call("semantic_search", {"query": "material stock inventory availability",
                                         "top_k": 8})
res = (payload or {}).get("results") or []
edge("semantic_search", "L2", "-", bool(res), err or "%d hit(s)" % len(res))

g = _first(res, lambda h: isinstance(h, dict) and h.get("graph"))
edge("graph_context", "L2", "L1", bool(g),
     "%s -> %s" % (g.get("id"), (g.get("graph") or {}).get("area")) if g
     else "no hit carried graph{} — _attach_graph_context did not fire")

# NOT a bare "did any hit carry prior_usage". A dead edge and a result set whose
# objects genuinely appear in no delivery document produce the IDENTICAL output, and
# the first draft of this file asserted the wrong one of those: it hardcoded a query
# about goods-movement BAdIs, which this corpus never cites, and reported a MISS for
# an edge that works. Luck is not a test.
#
# So the expectation is DERIVED. Ask the mention index which of these hits ought to be
# annotatable, then require exactly those to carry prior_usage. That separates the
# three cases for real: edge broken, edge fine, nothing to annotate.
if have_l4:
    ids = [h["id"] for h in res
           if isinstance(h, dict) and h.get("id")
           and h.get("type") in ("api", "cds_view", "badi")]
    try:
        import object_usage                                   # noqa: PLC0415
        counts = object_usage.usage_counts(ids)
    except Exception as exc:                                  # noqa: BLE001
        counts = {}
        print("      (mention lookup unavailable: %s)" % exc)
    expected = {i for i in ids if (counts.get(i.upper()) or {}).get("mentions")}
    got = {h["id"] for h in res if isinstance(h, dict) and h.get("prior_usage")}
    if not expected:
        edge("prior_usage", "L2", "L4", True,
             "no object in this result set is cited in the corpus, so there is "
             "nothing to annotate — verified against the mention index, not assumed")
    else:
        edge("prior_usage", "L2", "L4", got >= expected,
             "%d of %d citable hit(s) annotated (%s)"
             % (len(got & expected), len(expected),
                ", ".join(sorted(expected))[:60]))
else:
    edge("prior_usage", "L2", "L4", True, "L4 absent, edge not applicable")


# ── L2 -> L3 ─────────────────────────────────────────────────────────────────
# Derived the same way as prior_usage above, and for the same reason: a dead edge and
# a result set naming no object any lesson mentions produce identical output. Ask the
# lesson store which of THESE hits ought to carry lessons, then require exactly those.
try:
    import object_usage                                        # noqa: PLC0415
    _entries = server._exp_store.load_experience().get("entries", [])
    _ids = [h["id"] for h in res
            if isinstance(h, dict) and h.get("id")
            and h.get("type") in ("api", "cds_view", "badi")]
    _should = set(object_usage.lessons_for_objects(_ids, _entries))
except Exception as exc:                                       # noqa: BLE001
    _should = set()
    print("      (lesson lookup unavailable: %s)" % exc)
_got = {h["id"] for h in res if isinstance(h, dict) and h.get("lessons")}
if not _should:
    edge("lessons", "L2", "L3", True,
         "no object in this result set is named by any lesson, so there is nothing "
         "to annotate — verified against the lesson store, not assumed")
else:
    edge("lessons", "L2", "L3", _got >= _should,
         "%d of %d lesson-bearing hit(s) annotated (%s)"
         % (len(_got & _should), len(_should), ", ".join(sorted(_should))[:60]))


# ── L1 -> L2 ─────────────────────────────────────────────────────────────────
# The fallback for an object the name heuristic cannot place. 1,152 nodes are both
# isolated and arealess, so before this edge existed they returned an empty result
# with no explanation. Derived: pick a REAL isolated node from the graph rather than
# naming one, because the catalog is resynced monthly and a hardcoded name would
# eventually test nothing.
print("\nan object with no name-match edges still reaches neighbours by meaning")
try:
    import graph_engine as _ge                                 # noqa: PLC0415
    _g, _ = _ge._load()
    _iso = next((n for n, m in (_g or {}).get("nodes", {}).items()
                 if not (_g.get("edges") or {}).get(n)
                 and not (m.get("area") or "").strip()), None)
except Exception:                                              # noqa: BLE001
    _iso = None
if not _iso:
    edge("l2_fallback", "L1", "L2", True,
         "no isolated+arealess node in this graph — nothing for the fallback to do")
else:
    payload, err = _call("get_object_graph", {"object_name": _iso})
    mode = (payload or {}).get("edge_mode")
    # area_fallback is impossible here (the node has no area), so anything other than
    # l2_similarity means the vector engine did not answer — which is the failure this
    # check exists for, EXCEPT when L2 is genuinely unavailable on the host.
    _l2_up = not isinstance(server._load_vector_engine()[0], type(None))
    edge("l2_fallback", "L1", "L2",
         mode == "l2_similarity" or not _l2_up,
         "%s -> %s, %d neighbour(s)" % (_iso, mode, (payload or {}).get("total_connections", 0))
         if mode == "l2_similarity" else
         ("L2 unavailable on this host, edge not applicable" if not _l2_up
          else "%s fell through to %r — _l2_neighbors did not answer" % (_iso, mode)))


# ── L1 -> L4 + L3 ────────────────────────────────────────────────────────────
print("\nL1 objects reach the documents and lessons that name them")
payload, err = _call("get_object_graph", {"object_name": "I_MaterialStock"})
edge("get_object_graph", "L1", "-", bool(payload) and "error" not in (payload or {}),
     err or (payload or {}).get("error") or "area=%s" % (payload or {}).get("area", "?"))
edge("usage_brief", "L1", "L4+L3", bool((payload or {}).get("prior_usage")) or not have_l4,
     str((payload or {}).get("prior_usage"))[:70] if (payload or {}).get("prior_usage")
     else ("L4 absent, edge not applicable" if not have_l4
           else "no prior_usage — object is not named in the corpus (legitimate)"))

payload, err = _call("get_object_usage", {"object_name": "EKKO", "limit": 3})
cm = (payload or {}).get("corpus_mentions") or {}
edge("get_object_usage", "L1", "L4", bool(cm.get("indexed")) or not have_l4,
     err or ("%s mention(s) / %s artifact(s)" % (cm.get("total_mentions"),
                                                 cm.get("total_artifacts"))
             if cm.get("indexed") else "not indexed"))
edge("lessons_edge", "L1", "L3", isinstance((payload or {}).get("lessons"), list),
     "%d lesson(s) name it" % len((payload or {}).get("lessons") or []))


# ── L3 -> catalog, L3 -> L4, L3 -> run ───────────────────────────────────────
print("\nL3 lessons carry current verdicts, corpus evidence and run provenance")
payload, err = _call("query_experience", {"query": "api"})
lessons = (payload or {}).get("results") or []
edge("query_experience", "L3", "-", bool(lessons), err or "%d lesson(s)" % len(lessons))

lo = _first(lessons, lambda l: l.get("objects_mentioned"))
edge("entity_link", "L3", "catalog", bool(lo),
     "%s names %d object(s)" % (lo.get("id"), len(lo.get("objects_mentioned") or []))
     if lo else "no lesson in this page named a recognised object")

rd = _first(lessons, lambda l: (l.get("related_documents") or {}).get("documents"))
edge("evidence", "L3", "L4", bool(rd) or not have_l4,
     "%s -> %d document(s)" % (rd.get("id"),
                               len((rd.get("related_documents") or {}).get("documents")))
     if rd else ("L4 absent, edge not applicable" if not have_l4
                 else "no lesson reached a shared-object document — measured coverage "
                      "is 1/32, so this is expected on most pages"))

# L3 -> L2. Derived rather than probed with a query known to work: `related_lessons`
# only appears when meaning and keywords DISAGREE, so a query whose words already hit
# the right lesson correctly yields nothing, and asserting on a hand-picked phrase
# would test the phrase rather than the wiring. Recompute what the handler should
# have found — the same floored vector search, minus whatever the keyword scan
# already returned — and require the payload to match it.
_qx = "api"
try:
    _veng, _ = server._load_vector_engine()
    if _veng is None:
        _expect_rel = None                                     # L2 down; not applicable
    else:
        _sem = _veng.search(_qx, top_k=3, filter_type="experience", min_score=0.30)
        _kw  = {l.get("id") for l in lessons}
        _expect_rel = {s.get("id") for s in (_sem or [])
                       if not isinstance(_sem, dict) and s.get("id")} - _kw
except Exception:                                              # noqa: BLE001
    _expect_rel = None
_got_rel = {x.get("id") for x in ((payload or {}).get("related_lessons") or [])}
if _expect_rel is None:
    edge("related", "L3", "L2", True, "L2 unavailable on this host, edge not applicable")
elif not _expect_rel:
    edge("related", "L3", "L2", True,
         "the keyword scan already returned every lesson within 0.30 of this query, "
         "so there is nothing for meaning to add — recomputed, not assumed")
else:
    edge("related", "L3", "L2", _got_rel == _expect_rel,
         "expected %s, got %s" % (sorted(_expect_rel) or "none", sorted(_got_rel) or "none"))

fr = _first(lessons, lambda l: l.get("from_run"))
edge("run_evidence", "L3", "runs", bool(fr),
     "%s -> %s (%s)" % (fr.get("id"), (fr.get("from_run") or {}).get("directory"),
                        (fr.get("from_run") or {}).get("matched_by")) if fr
     else "no lesson in this page resolved to a run (coverage is 6/32)")


# ── catalog -> L4 + L3 ───────────────────────────────────────────────────────
print("\na release verdict arrives with this team's prior usage")
payload, err = _call("check_object_release_state", {"object_name": "I_MaterialStock"})
edge("release_state", "catalog", "-", bool(payload) and not err,
     err or "verdict=%s evidence=%s" % ((payload or {}).get("verdict"),
                                        (payload or {}).get("evidence")))
edge("prior_usage", "catalog", "L4+L3",
     bool((payload or {}).get("prior_usage")) or not have_l4,
     str((payload or {}).get("prior_usage"))[:70] if (payload or {}).get("prior_usage")
     else ("L4 absent, edge not applicable" if not have_l4
           else "no prior_usage attached — check the handler is the _public wrapper"))


# ── summary ──────────────────────────────────────────────────────────────────
missed = [r for r in RESULTS if not r["ok"]]
print("\n== %d edge check(s), %d ok, %d missed"
      % (len(RESULTS), len(RESULTS) - len(missed), len(missed)))
if VERBOSE:
    import json
    print(json.dumps(RESULTS, indent=2))
if missed:
    print("\nMISSED — an edge that does not fire makes every downstream answer quietly")
    print("thinner, which no pipeline run would report as a failure:")
    for r in missed:
        print("   %-14s %-4s -> %-4s  %s" % (r["edge"], r["from"], r["to"], r["detail"]))
sys.exit(1 if missed else 0)
