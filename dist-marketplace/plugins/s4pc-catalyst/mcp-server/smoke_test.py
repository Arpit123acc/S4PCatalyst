#!/usr/bin/env python3
"""
S4PC MCP smoke test — call every read-only governance tool once and report failures.

WHY THIS EXISTS
    A crash inside a single MCP tool does not stop a pipeline run: the agent simply
    falls back to the catalog-read heuristic and writes "confirm on api.sap.com" into
    the deliverable. The governance gate silently degrades instead of failing loudly.
    That is exactly how `check_object_release_state` stayed broken — `_table_map()` hit
    `"replaces": null` on 8777 of 8928 synced CDS entries, because `.get(key, default)`
    returns the STORED null rather than the default.

    Run this after any catalog sync (`sync_hub.py`), any server.py change, and before
    trusting a pipeline run's release verdicts.

USAGE
    python mcp-server/smoke_test.py          # exit 0 = all good, 1 = a tool is broken
"""
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "catalog"))
sys.path.insert(0, _HERE)

import server  # noqa: E402

# Read-only tools + representative arguments. Tools that mutate state, deploy, or need a
# live SAP/BTP connection are listed in SKIP with the reason.
CASES = {
    "tool_check_object_release_state": {"object_name": "I_Product"},
    "tool_search_released_apis":       {"query": "purchase"},
    "tool_search_released_badis":      {"query": "purchase"},
    "tool_extensibility_advisor":      {"requirement": "add a custom field to the purchase order"},
    "tool_abap_cloud_lint":            {"code": "CLASS zcl_x DEFINITION PUBLIC. ENDCLASS."},
    "tool_query_experience":           {"topic": "purchase"},
    "tool_get_reference_links":        {},
    "tool_semantic_search":            {"query": "purchase requisition"},
    "tool_find_similar_delivery":      {"description": "purchase requisition app"},
    "tool_get_object_graph":           {"object_name": "I_Product"},
    "tool_get_area_map":               {"area": "Procurement"},
    "tool_guardrails_status":          {},
    "tool_observability_snapshot":     {},
    "tool_file_probe":                 {"file_path": "CLAUDE.md"},
}
SKIP = {
    "tool_rebuild_vector_index": "rebuilds the index",
    "tool_sync_object_graph":    "rewrites the graph",
    "tool_record_experience":    "writes to the experience DB",
    "tool_btp_deploy":           "deploys to BTP",
    "tool_odata_get_metadata":   "needs a live SAP connection",
    "tool_odata_query":          "needs a live SAP connection",
    "tool_sap_connection_test":  "needs a live SAP connection",
    "tool_extract_docx":         "needs a .docx input",
}

# Verdicts these objects must produce — catches a tool that "runs" but answers wrongly.
VERDICTS = {
    "BAPI_PO_CREATE1": "NOT_AVAILABLE",   # forbidden pattern
    "VBAK":            "NOT_AVAILABLE",   # classical table -> needs the CDS table map
    "I_Product":       "LIKELY_RELEASED",  # seed catalog hit
}


def main():
    tools = sorted(n for n in dir(server) if n.startswith("tool_") and callable(getattr(server, n)))
    broken, ok, skipped, untested = [], [], [], []

    for name in tools:
        if name in SKIP:
            skipped.append("%s (%s)" % (name[5:], SKIP[name]))
            continue
        if name not in CASES:
            untested.append(name[5:])
            continue
        try:
            getattr(server, name)(CASES[name])
            ok.append(name[5:])
        except Exception as exc:
            broken.append((name[5:], "%s: %s" % (type(exc).__name__, exc)))
            traceback.print_exc()

    # Verdict correctness (only meaningful if the tool itself ran)
    wrong = []
    for obj, want in VERDICTS.items():
        try:
            got = server.tool_check_object_release_state({"object_name": obj}).get("verdict")
            if got != want:
                wrong.append("%s -> %s (expected %s)" % (obj, got, want))
        except Exception as exc:
            wrong.append("%s raised %s" % (obj, type(exc).__name__))

    print("S4PC MCP smoke test")
    print("  OK        : %d  (%s)" % (len(ok), ", ".join(ok)))
    print("  skipped   : %d  (%s)" % (len(skipped), ", ".join(skipped)))
    if untested:
        print("  NO TEST   : %d  (%s)  <- add a case above" % (len(untested), ", ".join(untested)))
    if broken:
        print("  BROKEN    : %d" % len(broken))
        for n, err in broken:
            print("      %-34s %s" % (n, err))
    if wrong:
        print("  WRONG VERDICT: %d" % len(wrong))
        for w in wrong:
            print("      %s" % w)

    failed = bool(broken or wrong)
    print("\nRESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
