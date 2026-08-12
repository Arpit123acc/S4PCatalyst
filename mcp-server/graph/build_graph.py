#!/usr/bin/env python3
"""
S4PC Digital Brain — Layer 1: Live Object Graph builder.

Reads the three released-object catalog files and builds an adjacency graph
linking APIs, CDS views, and BAdIs by shared business-concept prefixes in their
technical names. The graph is used by get_object_graph and get_area_map MCP tools.

Usage:
    python mcp-server/graph/build_graph.py

Run whenever:
  - catalog files change (new objects added / areas corrected)
  - first-time setup (run after build_index.py)

Zero-dependency — pure Python 3.9+ stdlib only.
"""

import os
import sys

# The graph is written BEFORE the closing spot-check prints. Those prints contain non-ASCII
# characters, so on a Windows cp1252 console they raised UnicodeEncodeError and the script exited
# rc=1 — every caller (sync_hub --rebuild, the webapp's background rebuild) then reported a FAILED
# graph rebuild even though graph.json had been written correctly. Force UTF-8 output instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import graph_engine

BASE_DIR    = os.path.dirname(_HERE)   # mcp-server/
CATALOG_DIR = os.path.join(BASE_DIR, "catalog")

sys.path.insert(0, CATALOG_DIR)
import db as _catalog_db  # noqa: E402


if __name__ == "__main__":
    print("S4PC Digital Brain — building Live Object Graph (Layer 1)...")

    apis      = _catalog_db.load_apis().get("apis", [])
    cds_views = _catalog_db.load_cds_views().get("views", [])
    badis     = _catalog_db.load_badis().get("badis", [])

    print("  Input: %d APIs, %d CDS views, %d BAdIs" % (len(apis), len(cds_views), len(badis)))

    graph = graph_engine.build_graph(apis, cds_views, badis)
    stats = graph_engine.save_graph(graph)

    print("  Nodes: %d  Edges: %d  Areas: %d" % (stats["nodes"], stats["edges"], stats["areas"]))
    print("  By type:", stats["by_type"])
    print("Graph written -> %s" % graph_engine.GRAPH_PATH)

    # Quick connectivity check — spot-check a few well-known objects
    checks = [
        "I_PurchaseOrder",
        "API_PURCHASEORDER_PROCESS_SRV",
        "I_SalesDocument",
        "API_BUSINESS_PARTNER",
        "I_JournalEntryItem",
    ]
    print("\n  Spot-check (name-match edges):")
    for name in checks:
        result = graph_engine.get_object_graph(name, depth=1)
        if "error" in result:
            print("    %-45s → NOT FOUND" % name)
        else:
            n_conn = result.get("total_connections", 0)
            mode   = result.get("edge_mode", "")
            print("    %-45s → %d connections (%s)" % (name, n_conn, mode))

    print("\nDone. MCP tools get_object_graph, get_area_map, and sync_object_graph are now active.")
