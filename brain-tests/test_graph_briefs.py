#!/usr/bin/env python3
"""graph_engine.briefs_for_names — the L4/L3 -> L1 edge, and its refusal to guess.

WHY THIS EXISTS
    briefs_for_names annotates the SAP objects named in a retrieved delivery document
    with their position in the object graph (business area, connection count). It is
    the cheap batched sibling of get_object_graph, and it deliberately resolves names
    LESS eagerly than get_object_graph does.

    That difference is the whole point and the reason for this file. get_object_graph
    resolves "I_PurchaseOrder" to "I_PurchaseOrderAPI01" by prefix, because a human
    typed a partial name and wants the nearest node. The names briefs_for_names
    receives were EXTRACTED from a document, so they are already whole: prefix
    resolution would silently stamp a mention with a DIFFERENT object's business area,
    and a wrong area reads exactly like a right one. Saying nothing is correct there.

    Also pins that objects absent from the catalog are simply omitted. The corpus
    names plenty the catalog does not carry -- classical tables above all -- and that
    silence is itself the clean-core signal, so it must not become a fabricated node.

Synthetic graph in a temp file; never touches mcp-server/graph/graph.json.

Usage:
    python brain-tests/test_graph_briefs.py
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp-server" / "graph"))

import graph_engine as ge                                     # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="s4pc-graph-"))
GRAPH = TMP / "graph.json"

GRAPH.write_text(json.dumps({
    "nodes": {
        "I_PurchaseOrder":               {"area": "Sourcing and Procurement",
                                          "type": "cds_view"},
        "I_PurchaseOrderAPI01":          {"area": "Sourcing and Procurement",
                                          "type": "cds_view"},
        "API_PURCHASEORDER_PROCESS_SRV": {"area": "Sourcing and Procurement",
                                          "type": "api"},
        "I_MaterialStock":               {"area": "Inventory", "type": "cds_view"},
        "BADI_LONELY":                   {"area": "Finance", "type": "badi"},
    },
    "edges": {
        "I_PurchaseOrder": ["I_PurchaseOrderAPI01",
                            "API_PURCHASEORDER_PROCESS_SRV"],
        "I_PurchaseOrderAPI01": ["I_PurchaseOrder"],
        "API_PURCHASEORDER_PROCESS_SRV": ["I_PurchaseOrder"],
        "I_MaterialStock": [],
    },
    "areas": {"Sourcing and Procurement": ["I_PurchaseOrder"],
              "Inventory": ["I_MaterialStock"], "Finance": ["BADI_LONELY"]},
    "stats": {"nodes": 5, "edges": 4},
}), encoding="utf-8")

ge.GRAPH_PATH = str(GRAPH)
ge._CACHE.update(mtime=None, graph=None, lower=None)

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-48s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def main():
    print("exact resolution")
    b = ge.briefs_for_names(["I_MaterialStock"])
    check("area is returned", b["I_MaterialStock"]["area"], "Inventory")
    check("type is returned", b["I_MaterialStock"]["type"], "cds_view")
    # An isolated node must report 0, not be omitted -- "released but connected to
    # nothing" is exactly the signal a reader wants when picking an object.
    check("isolated node reports 0 connections",
          b["I_MaterialStock"]["connections"], 0)
    b = ge.briefs_for_names(["I_PurchaseOrder"])
    check("connection count is the edge count",
          b["I_PurchaseOrder"]["connections"], 2)

    print("\ncase-insensitive, keyed on the name AS GIVEN")
    b = ge.briefs_for_names(["i_materialstock"])
    check("lowercase input resolves", "i_materialstock" in b, True)
    # Keyed as given so the caller can join back onto its own hit without
    # re-normalising; `resolved` carries the graph's spelling.
    check("and reports the graph's spelling",
          b["i_materialstock"]["resolved"], "I_MaterialStock")

    print("\nNO prefix matching -- the safety property")
    # get_object_graph WOULD resolve this by prefix. Here it must not: the node
    # I_PurchaseOrderItem does not exist, and answering with I_PurchaseOrder's
    # Sourcing area would be a fabricated fact about a real mention.
    check("a longer unknown name is not prefix-matched back",
          ge.briefs_for_names(["I_PurchaseOrderItem"]), {})
    check("get_object_graph still DOES prefix-match (contrast)",
          ge.get_object_graph("I_PurchaseOrderAPI").get("object"),
          "I_PurchaseOrderAPI01")

    print("\nabsent objects are omitted, never invented")
    b = ge.briefs_for_names(["EKKO", "VBAK", "I_MaterialStock"])
    check("classical tables carry no graph position", sorted(b), ["I_MaterialStock"])
    check("a wholly unknown batch is empty",
          ge.briefs_for_names(["EKKO", "EKPO"]), {})

    print("\nbatching and degradation")
    b = ge.briefs_for_names(["I_MaterialStock", "BADI_LONELY",
                             "API_PURCHASEORDER_PROCESS_SRV"])
    check("all three resolved in one call", len(b), 3)
    check("empty input", ge.briefs_for_names([]), {})
    check("None input", ge.briefs_for_names(None), {})
    check("blanks are skipped", ge.briefs_for_names(["", "   ", None]), {})
    # A missing graph must yield {} rather than raising: the annotation is additive,
    # and a host with no graph built still has to be able to search.
    ge.GRAPH_PATH = str(TMP / "nonexistent.json")
    ge._CACHE.update(mtime=None, graph=None, lower=None)
    check("no graph on this host -> {}", ge.briefs_for_names(["I_MaterialStock"]), {})

    print("\nthe lowercase index is dropped with the graph that built it")
    # Regression on the cache: a rebuilt graph renames nodes, so a retained lowercase
    # map would resolve a name to a node that no longer exists.
    two = TMP / "graph2.json"
    two.write_text(json.dumps({
        "nodes": {"I_Renamed": {"area": "Finance", "type": "cds_view"}},
        "edges": {}, "areas": {"Finance": ["I_Renamed"]}, "stats": {"nodes": 1},
    }), encoding="utf-8")
    ge.GRAPH_PATH = str(two)
    ge._CACHE.update(mtime=None, graph=None, lower=None)
    check("the new graph's node resolves",
          ge.briefs_for_names(["i_renamed"])["i_renamed"]["resolved"], "I_Renamed")
    check("the old graph's node is gone",
          ge.briefs_for_names(["i_materialstock"]), {})

    print()
    if FAILS:
        print("== %d FAILED" % len(FAILS))
        for f in FAILS:
            print("   " + f)
        return 1
    print("== all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
