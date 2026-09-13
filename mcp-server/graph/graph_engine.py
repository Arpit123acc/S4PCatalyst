"""
S4PC Digital Brain — Layer 1: Live Object Graph engine.

Builds an adjacency graph that links released SAP objects (APIs, CDS views, BAdIs,
business events) by two complementary strategies:

  1. Name-fragment edges  — cross-type links when names share a meaningful SAP
     business-concept prefix (e.g. I_PurchaseOrder ↔ API_PURCHASEORDER_PROCESS_SRV
     ↔ MM_PURCH_DOC_CHECK are all detected as Purchase-Order related).

  2. Area fallback        — when an object has no name-match edges, get_object_graph
     returns all objects in the same business area, grouped by type.

The "live" enrichment path (sync_object_graph with live_enrich=True) adds OData
entity+field metadata from the connected tenant to each API node, so agents can
see actual field names rather than just object names.

Persistence: mcp-server/graph/graph.json
Zero-dependency — pure Python 3.9+ stdlib only.
"""

import json
import os
import re
from collections import defaultdict, deque

GRAPH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph.json")

# ── SAP name normaliser ──────────────────────────────────────────────────────────

# Standard module/VDM prefixes that carry no business meaning
_PFX = re.compile(
    r'^(?:API|CE|YY1|Z)_|^[ICARE]_|^[A-Z]{2,4}_',
    re.I,
)
# Standard structural/technical suffixes
_SFX = re.compile(
    r'(?:_PROCESS_SRV|_SRV_\d+|_SRV|_IN|_OUT|_0001|_0002|_0003'
    r'|_CHECK|_MODIFY(?:_HDR|_ITEM|_HEAD|_LINE)?'
    r'|_CREATE|_SAVE|_CHANGE|_VALIDATE|_ENRICH'
    r'|_HDR|_HEAD|_HEADER|_ITEM|_ITM|_LINE)$',
    re.I,
)
# Noise tokens that add no selectivity
_NOISE = {"doc", "item", "itm", "hdr", "head", "line", "data", "info",
          "basic", "query", "read", "entry", "list", "set"}


def _extract_tokens(name: str) -> frozenset:
    """
    Return a set of meaningful business-concept tokens from an SAP object name.

    Examples
    --------
    API_PURCHASEORDER_PROCESS_SRV  → {purchaseorder}
    I_PurchaseOrder                → {purchase, order}
    MM_PURCH_DOC_CHECK             → {purch}
    CE_PURCHASEORDER_0001          → {purchaseorder}
    I_JournalEntryItem             → {journal, entry}   (item filtered as noise)
    API_JOURNALENTRYITEMBASIC_SRV  → {journalentryitembasic}
    """
    s = _PFX.sub("", name)
    s = _SFX.sub("", s)

    # 1. split on underscore → fragments (handles ALL_CAPS names)
    parts: list[str] = []
    for frag in s.split("_"):
        if not frag:
            continue
        # 2. split CamelCase fragments (handles PascalCase names like PurchaseOrder)
        camel = re.findall(r"[A-Z][a-z0-9]+|[A-Z]+(?=[A-Z][a-z]|$)|[a-z][a-z0-9]*", frag)
        if camel:
            parts.extend(camel)
        else:
            parts.append(frag)

    return frozenset(
        p.lower() for p in parts
        if len(p) >= 3 and p.lower() not in _NOISE
    )


def _names_are_related(toks_a: frozenset, toks_b: frozenset,
                       min_prefix: int = 3, min_longer: int = 6) -> bool:
    """
    Return True when any token of A is a prefix of any token of B (or vice versa),
    the prefix is at least `min_prefix` characters, and the longer token is at least
    `min_longer` characters (guards against spurious short-word matches).

    Handles SAP naming asymmetry:
      • APIs:     fused identifiers  (purchaseorder)
      • CDS views: CamelCase split   (purchase + order)
      • BAdIs:    3-char module abbr  (pur → purchase/purchaseorder)
    """
    for a in toks_a:
        for b in toks_b:
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if len(shorter) >= min_prefix and len(longer) >= min_longer and longer.startswith(shorter):
                return True
    return False


# ── typed edges: the ontology layer ──────────────────────────────────────────────
#
# WHY THIS EXISTS ALONGSIDE THE NAME HEURISTIC
#     Every edge in `edges` means exactly one thing: two object names share a >=3-char
#     token prefix (_names_are_related). That is a useful guess for "show me the
#     neighbourhood", but it cannot express HOW two objects relate, and — the part that
#     matters — a guess and a fact are indistinguishable to a caller.
#
#     These sources emit edges that SAP's own catalog metadata declares, carrying the
#     relation AND its provenance, so a caller can ask for only what it can defend.
#
# CONFIDENCE TIERS — the same discipline as verdict/evidence in CLAUDE.md
#     declared   catalog metadata states the relation outright. Citable.
#     observed   inferred from our corpus (co-occurrence). Precedent, never a contract.
#     heuristic  the name-prefix match in `edges`. Navigation only.
#
#     The existing 137,843 edges are all `heuristic` and CANNOT be retro-typed — a
#     relation is not recoverable from a prefix match. The graph improves by adding
#     declared sources, not by upgrading what is already there.

REL_TYPES  = ("replaces", "exposes", "requires", "extends", "belongs_to")
CONFIDENCE = ("declared", "observed", "heuristic")
_CONF_RANK = {"heuristic": 1, "observed": 2, "declared": 3}


def _edge(frm: str, to: str, rel: str, source: str,
          confidence: str, target_kind: str = "") -> dict:
    e = {"from": frm, "to": to, "rel": rel, "source": source, "confidence": confidence}
    if target_kind:
        e["target_kind"] = target_kind
    return e


def _load_scope_items() -> tuple:
    """(active, retired) scope items from the catalog, or ([], []) if unavailable.

    Loaded here rather than passed in so build_graph keeps its signature and every
    existing caller — build_graph.py, sync_object_graph — picks the scope layer up
    without changing. A missing catalog degrades to no scope edges, reported in stats
    rather than raised: L1 is still valid without them.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "catalog", "scope_items.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return (d.get("scope_items") or [], d.get("retired_scope_items") or [])
    except Exception:
        return ([], [])


def replaces_edges(ctx: dict):
    """cds_view --replaces--> classical_table, from views[].replaces.

    The clean-core query in both directions: "what replaced EKKO" and "what is
    I_PurchaseOrder the successor to". 167 edges over 119 distinct tables today.

    The target is a classical table, which is NOT a released object — it is the thing
    the platform rules forbid. Targets land in `ext_nodes`, never in `nodes`, so they
    can never be listed by get_area_map or counted as catalog objects. See build_graph.
    """
    for v in ctx["cds_views"]:
        name = (v.get("name") or "").strip()
        if not name:
            continue
        rep = v.get("replaces") or []
        if isinstance(rep, str):
            rep = [rep]
        for tbl in rep:
            tbl = (tbl or "").strip()
            if tbl:
                yield _edge(name, tbl, "replaces",
                            "catalog:released_cds_views.replaces",
                            "declared", target_kind="classical_table")


def exposes_edges(ctx: dict):
    """api --exposes--> cds_view, from apis[].key_entities.

    OData entity sets are named A_<Concept>; the released view behind one is
    I_<Concept>. Only emitted when that view actually exists in the catalog — about
    60% of key_entities resolve today. An entity that does not resolve is SKIPPED,
    never invented: emitting a link to a view we cannot find would be exactly the
    naming_heuristic_only mistake this layer exists to avoid.
    """
    by_lower = {(v.get("name") or "").lower(): (v.get("name") or "")
                for v in ctx["cds_views"] if v.get("name")}
    for a in ctx["apis"]:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        for ent in a.get("key_entities") or []:
            ent = (ent or "").strip()
            if not ent:
                continue
            stem   = ent[2:] if ent[:2].upper() == "A_" else ent
            target = by_lower.get(("i_" + stem).lower())
            if target:
                yield _edge(name, target, "exposes",
                            "catalog:released_apis.key_entities", "declared")


def scope_dependency_edges(ctx: dict):
    """scope_item --requires--> scope_item, from scope_items[].required_scope_items.

    Entries are dicts ({"to": "BKJ", "conditional": false}), not bare ids, and the
    `conditional` flag is carried onto the edge: a conditional prerequisite is not the
    same commitment as a hard one, and collapsing the two would overstate scope.

    22 targets are not in the active list — 2 are RETIRED and 20 unknown to this
    catalog. They are still emitted, because "this scope item depends on something
    retired" is exactly the kind of finding a functional agent should surface. The
    ext_node carries the distinction.
    """
    for s in ctx["scope_items"]:
        sid = (s.get("scope_item_id") or "").strip()
        if not sid:
            continue
        for dep in s.get("required_scope_items") or []:
            to = (dep.get("to") or "").strip() if isinstance(dep, dict) else str(dep).strip()
            if not to:
                continue
            e = _edge(sid, to, "requires",
                      "catalog:scope_items.required_scope_items",
                      "declared", target_kind="scope_item")
            if isinstance(dep, dict) and dep.get("conditional"):
                e["conditional"] = True
            yield e


def scope_master_data_edges(ctx: dict):
    """scope_item --requires--> master_data, from scope_items[].required_master_data.

    The values are ids in a SEPARATE namespace — 73 of the 79 are not scope items at
    all. We can state that the dependency exists and cite it; we cannot name the
    object, because this catalog does not carry master-data descriptions. The ext_node
    is therefore an id with no title, which is honest rather than invented.
    """
    for s in ctx["scope_items"]:
        sid = (s.get("scope_item_id") or "").strip()
        if not sid:
            continue
        for md in s.get("required_master_data") or []:
            md = (md or "").strip()
            if md:
                yield _edge(sid, md, "requires",
                            "catalog:scope_items.required_master_data",
                            "declared", target_kind="master_data")


def scope_taxonomy_edges(ctx: dict):
    """scope_item --belongs_to--> lob / business_area.

    The business taxonomy a functional agent navigates by. Note it does NOT join to
    the 33 `area` values on released objects: only 3 overlap exactly (Inventory,
    Production Planning, Quality Management). Bridging scope items to objects needs a
    crosswalk or corpus co-occurrence — neither is derivable here, so neither is faked.
    """
    for s in ctx["scope_items"]:
        sid = (s.get("scope_item_id") or "").strip()
        if not sid:
            continue
        for field, kind in (("lob", "lob"), ("business_area", "business_area")):
            val = (s.get(field) or "").strip()
            if val:
                yield _edge(sid, val, "belongs_to",
                            "catalog:scope_items.%s" % field,
                            "declared", target_kind=kind)


# Registry. Adding a relation is adding a generator here — no schema migration, and
# no change to any consumer, because typed edges are carried alongside the adjacency
# rather than replacing it. Still to land: requires (communication_scenario, 74) and
# extends (badis[].business_context, 46).
EDGE_SOURCES = (
    replaces_edges,
    exposes_edges,
    scope_dependency_edges,
    scope_master_data_edges,
    scope_taxonomy_edges,
)


# ── graph builder ────────────────────────────────────────────────────────────────

def build_graph(apis: list, cds_views: list, badis: list) -> dict:
    """
    Build the object graph from catalog lists.

    Returns
    -------
    {
      "nodes": {name: {type, area, title, ...}},
      "edges": {name: [related_name, ...]},
      "areas": {area_name: [name, ...]},
      "stats": {...}
    }
    """
    nodes:  dict[str, dict]       = {}
    edges:  dict[str, set]        = defaultdict(set)
    areas:  dict[str, list]       = defaultdict(list)

    # ── register nodes ────────────────────────────────────────────────────────────
    for obj in apis:
        name = obj.get("name", "")
        if not name:
            continue
        area = obj.get("area", "")
        nodes[name] = {
            "type":     "api",
            "area":     area,
            "title":    obj.get("title", ""),
            "protocol": obj.get("protocol", ""),
            "hub_url":  obj.get("hub_url", ""),
            "communication_scenario": obj.get("communication_scenario", ""),
            "key_entities": obj.get("key_entities", []),
            "operations":   obj.get("operations", []),
        }
        if area:
            areas[area].append(name)

    for obj in cds_views:
        name = obj.get("name", "")
        if not name:
            continue
        area = obj.get("area", "")
        replaces = obj.get("replaces") or []
        if isinstance(replaces, str):
            replaces = [replaces]
        nodes[name] = {
            "type":     "cds_view",
            "area":     area,
            "title":    obj.get("notes", ""),
            "replaces": replaces,
        }
        if area:
            areas[area].append(name)

    for obj in badis:
        name = obj.get("name", "")
        if not name:
            continue
        area = obj.get("area", "")
        nodes[name] = {
            "type":               "badi",
            "area":               area,
            "title":              obj.get("title", ""),
            "use_case":           obj.get("use_case", ""),
            "extensibility_type": obj.get("extensibility_type", "developer"),
        }
        if area:
            areas[area].append(name)

    # ── build name-fragment edges (cross-type only) ───────────────────────────────
    all_names = list(nodes.keys())
    tok_map   = {n: _extract_tokens(n) for n in all_names}

    edge_count = 0
    for i, a in enumerate(all_names):
        t_a, toks_a = nodes[a]["type"], tok_map[a]
        if not toks_a:
            continue
        for b in all_names[i + 1:]:
            t_b, toks_b = nodes[b]["type"], tok_map[b]
            if t_a == t_b:          # same-type edges add noise, skip them
                continue
            if not toks_b:
                continue
            if _names_are_related(toks_a, toks_b):
                edges[a].add(b)
                edges[b].add(a)
                edge_count += 1

    # ── deduplicate area lists ────────────────────────────────────────────────────
    areas_clean = {k: sorted(set(v)) for k, v in areas.items() if k}

    # ── typed edges (ontology layer) ──────────────────────────────────────────────
    # Deliberately NOT merged into `edges`: that adjacency is what get_area_map,
    # briefs_for_names, the BFS in get_object_graph and freshness._l1 all read, and
    # mixing declared relations into it would change every existing caller's results
    # and the L1 node/edge counts the freshness checks compare against.
    scope_items, retired_items = _load_scope_items()
    scope_by_id   = {s.get("scope_item_id"): s for s in scope_items if s.get("scope_item_id")}
    retired_by_id = {s.get("scope_item_id"): s for s in retired_items if s.get("scope_item_id")}

    ctx = {"apis": apis, "cds_views": cds_views, "badis": badis,
           "scope_items": scope_items, "retired_scope_items": retired_items}

    typed_edges: list[dict]      = []
    ext_nodes:   dict[str, dict] = {}
    seen: set = set()

    def _register_ext(name: str, kind: str) -> None:
        """A referenced entity that is NOT a released-object catalog entry.

        Kept out of `nodes` deliberately: that keeps stats.nodes at the released-object
        count the freshness checks compare against, and means none of these can ever be
        listed by get_area_map as though they were released.
        """
        if name in nodes or name in ext_nodes:
            return
        meta = {"type": kind, "catalog_object": False}
        if kind == "classical_table":
            # The one kind where release state is the point: forbidden on Public Cloud.
            meta["released"] = False
        elif kind == "scope_item":
            src = scope_by_id.get(name) or retired_by_id.get(name)
            meta["retired"] = name in retired_by_id
            if src and src.get("description"):
                meta["description"] = src["description"]
            if not src:
                # Referenced by a dependency but absent from both lists — say so rather
                # than implying it is active.
                meta["known"] = False
        ext_nodes[name] = meta

    for source_fn in EDGE_SOURCES:
        for e in source_fn(ctx):
            key = (e["from"], e["to"], e["rel"])
            if key in seen:
                continue
            seen.add(key)
            kind = e.pop("target_kind", "")
            if kind:
                _register_ext(e["to"], kind)
            # Scope items are edge SOURCES too, so register the origin as well or a
            # graph query on a scope item id would resolve to nothing.
            if e["from"] not in nodes and e["from"] in scope_by_id:
                _register_ext(e["from"], "scope_item")
            typed_edges.append(e)

    by_rel, by_kind = {}, {}
    for e in typed_edges:
        by_rel[e["rel"]] = by_rel.get(e["rel"], 0) + 1
    for meta in ext_nodes.values():
        k = meta.get("type", "?")
        by_kind[k] = by_kind.get(k, 0) + 1

    stats = {
        # `nodes` stays released-objects-only. ext_nodes are counted separately, so
        # freshness.py's L1_objects_in_L2 check (L1 nodes vs L2 api+cds+badi docs)
        # keeps tying out at 10,736 instead of going falsely STALE.
        "nodes":    len(nodes),
        "edges":    edge_count,
        "areas":    len(areas_clean),
        "by_type":  {t: sum(1 for n in nodes.values() if n["type"] == t)
                     for t in ("api", "cds_view", "badi")},
        "typed_edges":   len(typed_edges),
        "typed_by_rel":  by_rel,
        "ext_nodes":     len(ext_nodes),
        "ext_by_kind":   by_kind,
        "scope_items_loaded": len(scope_items),
    }

    return {
        "nodes":       nodes,
        "edges":       {k: sorted(v) for k, v in edges.items()},
        "areas":       areas_clean,
        "typed_edges": typed_edges,
        "ext_nodes":   ext_nodes,
        "stats":       stats,
    }


# ── graph I/O ─────────────────────────────────────────────────────────────────────

def save_graph(graph_data: dict) -> dict:
    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    with open(GRAPH_PATH, "w", encoding="utf-8") as fh:
        json.dump(graph_data, fh, ensure_ascii=False, separators=(",", ":"))
    return graph_data["stats"]


# Parsed graph, keyed on the file's mtime. graph.json is ~10.7k nodes and ~138k
# edges, so re-parsing it per call is far too expensive now that the graph is read on
# search paths (semantic_search attaches area + degree to each catalog hit) rather
# than only by an explicit get_object_graph call.
#
# Keyed on MTIME rather than cached outright so that sync_object_graph / build_graph.py
# are picked up automatically: a plain lru_cache here would serve the pre-rebuild graph
# until the process restarted, which is precisely the class of silent staleness
# freshness.py exists to catch.
_CACHE: dict = {"mtime": None, "graph": None, "lower": None, "typed": None}


def _load() -> tuple:
    try:
        mtime = os.path.getmtime(GRAPH_PATH)
    except OSError:
        return None, "Graph not built — run: python mcp-server/graph/build_graph.py"
    if _CACHE["mtime"] == mtime and _CACHE["graph"] is not None:
        return _CACHE["graph"], None
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as fh:
            graph = json.load(fh)
    except FileNotFoundError:
        return None, "Graph not built — run: python mcp-server/graph/build_graph.py"
    except Exception as exc:
        return None, "Graph load error: %s" % exc
    # `lower` and `typed` are dropped with the graph they indexed: a rebuilt graph
    # renames nodes and re-derives relations, and a stale index would resolve a name to
    # a node that no longer exists or hand back edges the rebuild has already dropped.
    _CACHE.update(mtime=mtime, graph=graph, lower=None, typed=None)
    return graph, None


def _typed_index(graph: dict) -> dict:
    """{name: {"out": [edge, ...], "in": [edge, ...]}}, built once per graph load.

    Both directions are indexed because the useful clean-core question is the inbound
    one — "what replaced EKKO" — and EKKO is only ever an edge TARGET.
    """
    idx = _CACHE.get("typed")
    if idx is not None:
        return idx
    built: dict[str, dict] = {}
    for e in graph.get("typed_edges") or []:
        built.setdefault(e["from"], {"out": [], "in": []})["out"].append(e)
        built.setdefault(e["to"],   {"out": [], "in": []})["in"].append(e)
    _CACHE["typed"] = built
    return built


def _filter_typed(edges: list, rel_types=None, min_confidence: str = "") -> list:
    """Apply the per-agent projection: which relations, and how defensible."""
    floor = _CONF_RANK.get(min_confidence or "", 0)
    wanted = {r.strip().lower() for r in (rel_types or []) if str(r or "").strip()}
    out = []
    for e in edges:
        if wanted and e.get("rel", "").lower() not in wanted:
            continue
        if _CONF_RANK.get(e.get("confidence", ""), 0) < floor:
            continue
        out.append(e)
    return out


def briefs_for_names(names) -> dict:
    """L4/L3 -> L1, batched: {name as given: {resolved, area, connections}}.

    WHY THIS EXISTS SEPARATELY FROM get_object_graph
        get_object_graph answers deeply about ONE object -- BFS over neighbours, area
        mates, a full payload. Annotating the objects mentioned across a page of
        delivery-document hits needs the opposite shape: one shallow fact per name,
        for tens of names, cheaply. Calling get_object_graph per mention would run a
        BFS per name and, worse, its miss path scans every node twice.

    EXACT AND CASE-INSENSITIVE ONLY -- no prefix matching. get_object_graph resolves
    "I_PurchaseOrder" to "I_PurchaseOrderAPI01" because a human typed a partial name
    and wants the nearest node. These names were EXTRACTED from a document, so they
    are already whole: prefix-resolving one would silently label a mention with a
    different object's business area, which is worse than saying nothing.

    Names absent from the graph are simply omitted -- the corpus mentions plenty of
    objects the catalog does not carry (classical tables especially), and that absence
    is itself information the caller can read.
    """
    wanted = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    if not wanted:
        return {}
    graph, _err = _load()
    if not graph:
        return {}
    nodes = graph.get("nodes") or {}
    edges = graph.get("edges") or {}
    lower = _CACHE.get("lower")
    if lower is None:
        # Built once per graph load, not once per call: the miss path in
        # get_object_graph is an O(nodes) scan, and 10.7k nodes x 30 mentions a page
        # is the kind of cost that only shows up under load.
        lower = {k.lower(): k for k in nodes}
        _CACHE["lower"] = lower
    out = {}
    for name in wanted:
        node_id = name if name in nodes else lower.get(name.lower())
        if not node_id:
            continue
        node = nodes.get(node_id) or {}
        out[name] = {"resolved": node_id,
                     "area": node.get("area") or None,
                     "type": node.get("type") or None,
                     "connections": len(edges.get(node_id) or [])}
    return out


# ── query API ────────────────────────────────────────────────────────────────────

def get_object_graph(object_name: str, depth: int = 1,
                     rel_types=None, min_confidence: str = "") -> dict:
    """
    Return an object and its connected neighbours up to `depth` hops.
    Falls back to area-mates when the object has no name-match edges.

    `rel_types` / `min_confidence` filter the TYPED edges only — this is where a
    per-agent projection lives. One store, many views: a RICEFW agent asks for
    replaces/exposes/extends, a functional agent for requires/belongs_to, an
    architect for min_confidence="declared" and nothing else. The heuristic
    adjacency in `connections` is unaffected by either.
    """
    graph, err = _load()
    if graph is None:
        return {"error": err}

    nodes = graph["nodes"]
    edges = graph["edges"]
    areas = graph["areas"]
    typed = _typed_index(graph)

    # ── case-insensitive lookup ───────────────────────────────────────────────────
    resolved = object_name
    if resolved not in nodes:
        lo = object_name.lower()
        # 1. exact case-insensitive
        exact = [n for n in nodes if n.lower() == lo]
        if exact:
            resolved = exact[0]
        else:
            # 2. starts-with (e.g. "I_PurchaseOrder" → "I_PurchaseOrderAPI01")
            #    NOT a general substring match — that picks up false positives
            #    like "API_PURCHASEORDER_PROCESS_SRV" for query "I_PurchaseOrder"
            starts = [n for n in nodes if n.lower().startswith(lo)]
            if starts:
                resolved = starts[0]
            else:
                # 3. ext_node — a referenced entity that is NOT a released object,
                #    e.g. the classical table EKKO. It has no adjacency and no area,
                #    so there is nothing to BFS; what it has is inbound typed edges,
                #    and those answer the question actually being asked: what am I
                #    allowed to use instead?
                ext = (graph.get("ext_nodes") or {})
                ext_id = ext.get(object_name) and object_name
                if not ext_id:
                    for k in ext:
                        if k.lower() == lo:
                            ext_id = k
                            break
                if ext_id:
                    emeta = ext.get(ext_id, {})
                    ekind = emeta.get("type", "external")
                    t_e   = typed.get(ext_id, {"out": [], "in": []})
                    outb  = _filter_typed(t_e.get("out", []), rel_types, min_confidence)
                    inb   = _filter_typed(t_e.get("in", []),  rel_types, min_confidence)

                    def _ext_conn(e, other, direction):
                        m = nodes.get(other) or ext.get(other) or {}
                        o = {"name": other, "rel": e["rel"], "direction": direction,
                             "confidence": e["confidence"], "source": e["source"],
                             "type": m.get("type", ""),
                             "catalog_object": m.get("catalog_object", other in nodes)}
                        for k in ("released", "retired", "known", "description"):
                            if k in m:
                                o[k] = m[k]
                        if e.get("conditional"):
                            o["conditional"] = True
                        if other in nodes and nodes[other].get("area"):
                            o["area"] = nodes[other]["area"]
                        return o

                    note = ("Not a released-object catalog entry — referenced by the "
                            "catalog, not listed in it.")
                    if ekind == "classical_table":
                        note = ("Classical table — NOT available on Public Cloud. "
                                "Use the released view(s) below instead.")
                    elif ekind == "scope_item":
                        note = ("SAP scope item (business process), not a released "
                                "object. Its prerequisites are below.")

                    result = {
                        "object": ext_id,
                        "type":   ekind,
                        "catalog_object": False,
                        "note":   note,
                        "typed_connections":
                            [_ext_conn(e, e["to"], "outbound") for e in outb] +
                            [_ext_conn(e, e["from"], "inbound") for e in inb],
                    }
                    for k in ("released", "retired", "known", "description"):
                        if k in emeta:
                            result[k] = emeta[k]
                    result["total_typed_connections"] = len(result["typed_connections"])
                    return result
                return {
                    "error": "Object '%s' not found in graph." % object_name,
                    "hint":  "Use get_area_map to browse by area, or semantic_search to find object names.",
                    "available_areas": sorted(areas.keys()),
                }

    root_meta = nodes[resolved]

    # ── BFS up to depth hops via name-match edges ─────────────────────────────────
    visited   = {resolved}
    frontier  = {resolved}
    for _ in range(max(depth, 1)):
        nxt = set()
        for n in frontier:
            for nb in edges.get(n, []):
                if nb not in visited:
                    nxt.add(nb)
                    visited.add(nb)
        frontier = nxt
        if not frontier:
            break

    connected = visited - {resolved}

    # ── area fallback when no edges ───────────────────────────────────────────────
    area_fallback = False
    if not connected:
        area_name = root_meta.get("area", "")
        if area_name:
            area_fallback = True
            connected = set(areas.get(area_name, [])) - {resolved}

    # ── group by type ─────────────────────────────────────────────────────────────
    grouped: dict[str, list] = {}
    for nb in sorted(connected):
        meta = nodes.get(nb, {})
        t    = meta.get("type", "other")
        entry = {
            "name":  nb,
            "area":  meta.get("area", ""),
            "title": meta.get("title") or meta.get("use_case", ""),
        }
        if t == "api":
            entry["protocol"]    = meta.get("protocol", "")
            entry["hub_url"]     = meta.get("hub_url", "")
        if t == "badi":
            entry["use_case"]    = meta.get("use_case", "")
            entry["ext_type"]    = meta.get("extensibility_type", "")
        if t == "cds_view":
            entry["replaces"]    = meta.get("replaces", [])
        grouped.setdefault(t, []).append(entry)

    # ── typed connections (ontology layer) ────────────────────────────────────────
    # Reported separately from `connections`, never merged into it: these carry a
    # relation and a provenance tier, and flattening them into the heuristic
    # adjacency would destroy exactly the distinction they exist to make.
    ext_all  = graph.get("ext_nodes") or {}
    t_entry  = typed.get(resolved, {"out": [], "in": []})
    t_out    = _filter_typed(t_entry.get("out", []), rel_types, min_confidence)
    t_in     = _filter_typed(t_entry.get("in", []),  rel_types, min_confidence)

    def _conn(e, other, direction):
        """Render one typed edge.

        `released` / `retired` are emitted ONLY when actually known. Defaulting
        released=True for anything without the key would have quietly asserted release
        state for scope items and master-data ids, which is the precise mistake the
        evidence rule in CLAUDE.md exists to prevent.
        """
        meta = nodes.get(other) or ext_all.get(other) or {}
        out = {"name": other, "rel": e["rel"], "direction": direction,
               "confidence": e["confidence"], "source": e["source"],
               "type": meta.get("type", ""),
               "catalog_object": meta.get("catalog_object", other in nodes)}
        for k in ("released", "retired", "known", "description"):
            if k in meta:
                out[k] = meta[k]
        if e.get("conditional"):
            out["conditional"] = True
        return out

    typed_conns = ([_conn(e, e["to"], "outbound") for e in t_out] +
                   [_conn(e, e["from"], "inbound") for e in t_in])

    result = {
        "object":    resolved,
        "type":      root_meta.get("type", ""),
        "area":      root_meta.get("area", ""),
        "title":     root_meta.get("title") or root_meta.get("use_case", ""),
        "depth":     depth,
        "edge_mode": "area_fallback" if area_fallback else "name_match",
        "connections": grouped,
        "total_connections": len(connected),
        "typed_connections": typed_conns,
        "total_typed_connections": len(typed_conns),
    }
    # surface useful fields for the root
    if root_meta.get("type") == "api":
        result["protocol"]    = root_meta.get("protocol", "")
        result["hub_url"]     = root_meta.get("hub_url", "")
        result["key_entities"] = root_meta.get("key_entities", [])
    if root_meta.get("type") == "badi":
        result["use_case"]   = root_meta.get("use_case", "")
        result["ext_type"]   = root_meta.get("extensibility_type", "")
    if root_meta.get("type") == "cds_view":
        result["replaces"]   = root_meta.get("replaces", [])

    return result


def get_area_map(area: str) -> dict:
    """Return all released objects in a business area, grouped by type."""
    graph, err = _load()
    if graph is None:
        return {"error": err}

    areas = graph["areas"]
    nodes = graph["nodes"]

    # case-insensitive area match
    lo = area.lower()
    matched = next((a for a in areas if a.lower() == lo), None)
    if not matched:
        matched = next((a for a in areas if lo in a.lower()), None)
    if not matched:
        return {
            "error":           "Area '%s' not found." % area,
            "available_areas": sorted(areas.keys()),
        }

    by_type: dict[str, list] = {"api": [], "cds_view": [], "badi": []}
    for name in areas[matched]:
        meta = nodes.get(name, {})
        t    = meta.get("type", "other")
        entry = {
            "name":  name,
            "title": meta.get("title") or meta.get("use_case", ""),
        }
        if t == "api":
            entry["protocol"] = meta.get("protocol", "")
            entry["hub_url"]  = meta.get("hub_url", "")
        if t == "badi":
            entry["use_case"] = meta.get("use_case", "")
        by_type.setdefault(t, []).append(entry)

    return {
        "area":       matched,
        "apis":       by_type.get("api", []),
        "cds_views":  by_type.get("cds_view", []),
        "badis":      by_type.get("badi", []),
        "total":      len(areas[matched]),
        "note":       "Objects are catalog seeds — confirm release state on SAP Business Accelerator Hub / Custom Logic app / ADT.",
    }


def list_areas(graph_data: dict | None = None) -> dict:
    """Return all business areas with object-type counts."""
    if graph_data is None:
        graph_data, err = _load()
        if graph_data is None:
            return {"error": err}

    areas = graph_data["areas"]
    nodes = graph_data["nodes"]

    summary = {}
    for area, names in sorted(areas.items()):
        counts: dict[str, int] = {}
        for n in names:
            t = nodes.get(n, {}).get("type", "other")
            counts[t] = counts.get(t, 0) + 1
        summary[area] = counts
    return {"areas": summary, "total_areas": len(summary)}
