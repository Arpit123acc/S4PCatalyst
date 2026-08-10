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

    stats = {
        "nodes":    len(nodes),
        "edges":    edge_count,
        "areas":    len(areas_clean),
        "by_type":  {t: sum(1 for n in nodes.values() if n["type"] == t)
                     for t in ("api", "cds_view", "badi")},
    }

    return {
        "nodes":  nodes,
        "edges":  {k: sorted(v) for k, v in edges.items()},
        "areas":  areas_clean,
        "stats":  stats,
    }


# ── graph I/O ─────────────────────────────────────────────────────────────────────

def save_graph(graph_data: dict) -> dict:
    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    with open(GRAPH_PATH, "w", encoding="utf-8") as fh:
        json.dump(graph_data, fh, ensure_ascii=False, separators=(",", ":"))
    return graph_data["stats"]


def _load() -> tuple:
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "Graph not built — run: python mcp-server/graph/build_graph.py"
    except Exception as exc:
        return None, "Graph load error: %s" % exc


# ── query API ────────────────────────────────────────────────────────────────────

def get_object_graph(object_name: str, depth: int = 1) -> dict:
    """
    Return an object and its connected neighbours up to `depth` hops.
    Falls back to area-mates when the object has no name-match edges.
    """
    graph, err = _load()
    if graph is None:
        return {"error": err}

    nodes = graph["nodes"]
    edges = graph["edges"]
    areas = graph["areas"]

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

    result = {
        "object":    resolved,
        "type":      root_meta.get("type", ""),
        "area":      root_meta.get("area", ""),
        "title":     root_meta.get("title") or root_meta.get("use_case", ""),
        "depth":     depth,
        "edge_mode": "area_fallback" if area_fallback else "name_match",
        "connections": grouped,
        "total_connections": len(connected),
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
