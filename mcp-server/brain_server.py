#!/usr/bin/env python3
"""
S4PC Brain MCP server — semantic RAG over the Public Cloud Brain.

Kept SEPARATE from the governance server (mcp-server/server.py) on purpose: that
server is offline, pure-stdlib and never reaches the network. This one performs
retrieval-augmented search using Amazon Bedrock Titan embeddings + a FAISS index
(built by scripts/embed_chunks.py), so it needs boto3/faiss and the EC2 IAM
instance profile. It degrades gracefully: if the deps or index are missing, the
tool returns a helpful message instead of crashing.

Exposes four tools - one retrieval, three exact lookups:
    search_brain(query, top_k?, phase?, agent_role?, deliverable_type?,
                 source_system?, dedup?)
        → top matching chunks across all indexed sources, with phase/agent/
          source metadata. Deduplicates to distinct source documents by
          default. Client delivery documents are PII-masked at ingest; SAP's
          own published material is not (it is not client data). Filter or
          check `source_system` to know which you have.

    lookup_accelerator(query?, scope_item?, phase?, lob?, source?, needs_auth?)
        -> SAP accelerators with their URL and whether opening one needs a
          session. Exact, no embeddings.
    lookup_scope_item(query?, limit?)
        -> scope items by id or business topic; retired ones flagged.
    lookup_process(query?, scope_item?, lob?, application?, limit?)
        -> what a scope item DOES: Fiori apps, process steps, roles; and the
          reverse edge, which scope items use a given app.

Run (registered via .mcp.json), or standalone:
    python3.11 mcp-server/brain_server.py --tool search_brain '{"query":"cutover plan"}'

Install (on the server that runs the brain):
    pip3.11 install boto3 faiss-cpu numpy
"""

import os
import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME      = "s4pc-brain"
SERVER_VERSION   = "1.0.0"

# Named sources and an honest masking statement, because agents quote this line
# into deliverables. It previously claimed two sources and blanket PII masking;
# by 2026-09-22 the corpus was seven sources, 75% of it SAP's own published
# material, which is deliberately NOT masked -- masking is for client documents,
# per the same reasoning in webdocs_ingest.py. A provenance string that
# understates its sources and overstates its handling is worse than none.
_SOURCE = ("S4PC Public Cloud Brain — Accenture delivery documents (SharePoint), SAP Best "
           "Practices process descriptions and test scripts (Signavio Process Navigator), "
           "SAP Activate accelerators and configuration questionnaires (Roadmap Viewer), the "
           "SAP scope-item catalog, and vendor developer documentation; embedded with Amazon "
           "Bedrock Titan. Client delivery documents are PII-masked at ingest; SAP-published "
           "material is indexed unmasked. Check the source_system field on each hit for its "
           "origin, and re-verify any SAP object name on api.sap.com / SAP Help before use.")


def tool_search_brain(args):
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        import brain_search
    except Exception as exc:                      # deps missing, etc.
        return {"error": "Brain unavailable: %s. Install deps "
                "(pip3.11 install boto3 faiss-cpu numpy) and build the index "
                "(python3.11 scripts/embed_chunks.py)." % exc}
    try:
        hits = brain_search.search(
            query,
            k=int(args.get("top_k") or 5),
            phase=args.get("phase"),
            agent_role=args.get("agent_role"),
            deliverable_type=args.get("deliverable_type"),
            source_system=args.get("source_system"),
            dedup_source=bool(args.get("dedup", True)),   # distinct docs by default (agent grounding)
        )
    except SystemExit as exc:                     # brain_search exits if index absent
        return {"error": "Brain index not ready: %s" % exc}
    except Exception as exc:
        return {"error": "search failed: %s" % exc}
    return {
        "verified": False,
        "source":   _SOURCE,
        "query":    query,
        "filters":  {k: args.get(k) for k in ("phase", "agent_role", "deliverable_type")
                     if args.get(k)},
        "results":  hits,
        "note":     "Cosine similarity over FAISS (Bedrock Titan). Use as delivery "
                    "reference/context, not as an authoritative SAP object source.",
    }


def _lookup_mod():
    import accelerator_lookup                              # noqa: PLC0415
    return accelerator_lookup


def tool_lookup_accelerator(args):
    try:
        al = _lookup_mod()
    except Exception as exc:
        return {"error": "accelerator lookup unavailable: %s" % exc}
    try:
        res = al.lookup(
            query=args.get("query"),
            scope_item=args.get("scope_item"),
            phase=args.get("phase"),
            lob=args.get("lob"),
            source=args.get("source"),
            needs_auth=args.get("needs_auth"),
            limit=int(args.get("limit") or 20),
        )
    except Exception as exc:
        return {"error": "lookup failed: %s" % exc}
    res["verified"] = False
    res["note"] = ("Exact catalog lookup, not retrieval - an empty result means no "
                   "catalog row matches, which is information rather than a miss. "
                   "URLs are SAP's; needs_auth true means a SAP for Me / SAML "
                   "session is needed to open it.")
    return res


def tool_lookup_scope_item(args):
    try:
        al = _lookup_mod()
    except Exception as exc:
        return {"error": "scope lookup unavailable: %s" % exc}
    try:
        res = al.scope_items(query=args.get("query"), limit=int(args.get("limit") or 20))
    except Exception as exc:
        return {"error": "lookup failed: %s" % exc}
    res["verified"] = False
    res["note"] = ("The 679-row SAP scope-item catalog, matched exactly. An empty "
                   "result means NO scope item carries that name - do not fall back "
                   "to search_brain and quote its nearest prose hit as if it were "
                   "one. retired: true means SAP withdrew the scope item.")
    return res


def tool_lookup_process(args):
    try:
        import process_lookup                               # noqa: PLC0415
    except Exception as exc:
        return {"error": "process lookup unavailable: %s" % exc}
    try:
        res = process_lookup.lookup(
            query=args.get("query"),
            scope_item=args.get("scope_item"),
            lob=args.get("lob"),
            application=args.get("application"),
            capability=args.get("capability"),
            with_steps=bool(args.get("with_steps", True)),
            limit=int(args.get("limit") or 10),
        )
    except Exception as exc:
        return {"error": "lookup failed: %s" % exc}
    res["verified"] = False
    res["note"] = ("Exact index over SAP Best Practices processes, built from the "
                   "Process Navigator. An empty applications or steps list means SAP "
                   "published none for that process, not that the lookup failed - see "
                   "`coverage` for how many scope items carry each. Fiori app names are "
                   "SAP's; they are not release-state claims, which still come from "
                   "check_object_release_state.")
    return res


TOOLS = {
    "search_brain": {
        "description": ("HYBRID search over the S4PC Public Cloud Brain: a dense vector ranking (Bedrock "
                        "Titan cosine) fused with BM25 keyword ranking. Because the lexical half is "
                        "present, EXACT IDENTIFIERS work as lookups — quote the real string "
                        "(API_CLFN_PRODUCT_SRV, 'scope item 1NN', UnusedVariablesRule) rather than "
                        "paraphrasing it, and prefer the precise term over a description of it. "
                        "FOUR KINDS OF KNOWLEDGE: (1) harvested SharePoint delivery knowledge — FDs, TDs, "
                        "workshop decks, test/cutover/change material; (2) the SAP scope-item catalog; "
                        "(3) OFFICIAL DEVELOPER DOCUMENTATION for side-by-side / UI code — SAP UI5, SAP "
                        "Fiori Elements, CAP and Node.js, mirrored here as source_system='developer_docs'; "
                        "(4) internal ABAP Cloud / RAP review standards as source_system='abap_guidance'. "
                        "For (3), SEARCH HERE FIRST rather than fetching the vendor site: ui5.sap.com is a "
                        "single-page app that returns a ~2 KB JavaScript shell to any fetch, so a web fetch "
                        "of it succeeds while grounding nothing. This works offline and on Bedrock, where "
                        "web tools may be unavailable. Each hit carries `score` (cosine, always comparable), "
                        "`retrievers` (which halves found it — 'vector'+'keyword' means both agreed, the "
                        "strongest signal) and phase / agent role / deliverable / source. All SharePoint "
                        "content is PII-masked. Use to ground a deliverable in prior delivery experience, in "
                        "authoritative vendor documentation, or in the ABAP Cloud review standard. Results "
                        "are retrieval context for grounding — they are NOT a release contract; object "
                        "release state still comes from check_object_release_state. Each hit also carries "
                        "`objects_mentioned`: the SAP object names appearing in that chunk's text. Via the "
                        "governance server those names arrive with a CURRENT release verdict, so a document "
                        "that cites a since-deprecated object is visible as such — but the verdict is about "
                        "the object today, not a claim the document was right then. To go the other way "
                        "(object -> which documents mention it), use get_object_usage. Hits also carry "
                        "`is_current` / `doc_version` / `superseded_by`: the corpus holds multiple "
                        "revisions of the same document (one spec exists as v2.0 through v11.0), and "
                        "`is_current: false` means you are reading a SUPERSEDED revision — prefer the "
                        "named successor before quoting it. `is_current: null` means the index predates "
                        "lifecycle tracking, which is NOT the same as current."),
        "schema": {"type": "object", "properties": {
            "query":            {"type": "string", "description": "Natural-language query"},
            "top_k":            {"type": "integer", "description": "Number of results (default 5)"},
            "phase":            {"type": "string", "description": "Filter: Discover/Prepare/Explore/Realize/Deploy/Run. NOTE: vendor documentation is phase-independent and is tagged Realize, so a phase filter will hide it — omit this, or filter on source_system/deliverable_type instead, when looking for developer docs."},
            "agent_role":       {"type": "string", "description": "Filter: e.g. build_agent, qe_agent, pmo_agent"},
            "deliverable_type": {"type": "string", "description": "Filter: e.g. functional_design, test_strategy; for vendor docs: ui5_docs | cap_docs | nodejs_docs"},
            "source_system":    {"type": "string", "description": "Filter: sharepoint | sap_scope_catalog | developer_docs (official UI5/Fiori-Elements/CAP/Node docs) | abap_guidance (internal ABAP Cloud / RAP review standard) | sap_bpd | accelerator_hub | ..."},
            "dedup":            {"type": "boolean", "description": "Collapse to one hit per source document (default true)"}},
            "required": ["query"]},
        "handler": tool_search_brain,
    },
    "lookup_accelerator": {
        "description": "EXACT LOOKUP over the 6,935-row SAP accelerator catalogs (Signavio Process Navigator + SAP Activate Roadmap Viewer). Use this, NOT search_brain, when you need the ARTIFACT rather than its contents: the URL, whether it is public or behind a login, its access level, its scope item. search_brain indexes the TEXT of documents and holds no link back to the file it came from; this returns the link. No embeddings, so it is instant and works even with the brain index absent. SEARCHES BUSINESS TOPIC, NOT TITLE: Best Practices titles are generic - 6,127 of 6,204 rows read 'Test script', 'Test script (SAP Cloud ALM)' or 'Test script (SAP Help Portal)' - so the query is matched against the SCOPE ITEM's description from the 679-row scope catalog, joined on the row's scope item id. Ask for 'invoice settlement', not 'test script'. An exact id or scope item ('2LH') always outranks a text match. AN EMPTY RESULT IS AN ANSWER: it means no catalog row matches, which search_brain structurally cannot tell you because it always returns its top k. needs_auth true means support.sap.com behind SAML and the human needs a SAP for Me session; false means help.sap.com or api.sap.com and anyone can open it; null means the host rule could not be loaded - treat that as unknown, not as public. Release state still comes from check_object_release_state: this tool knows about files, not objects.",
        "schema": {"type": "object", "properties": {
            "query":      {"type": "string", "description": "Business topic, scope item id, or accelerator id. Match a topic ('invoice settlement'), not a document type ('test script')."},
            "scope_item": {"type": "string", "description": "Exact scope item filter, e.g. 2LH"},
            "phase":      {"type": "string", "description": "Exact phase filter (SAP Activate rows only): Discover/Prepare/Explore/Realize/Deploy/Run"},
            "lob":        {"type": "string", "description": "Line of business substring, e.g. Finance"},
            "source":     {"type": "string", "description": "sap_best_practices | sap_activate"},
            "needs_auth": {"type": "boolean", "description": "true = only artifacts needing a SAP session; false = only public ones"},
            "limit":      {"type": "integer", "description": "Max results (default 20)"}},
            "required": []},
        "handler": tool_lookup_accelerator,
    },
    "lookup_scope_item": {
        "description": "EXACT LOOKUP over the 679-row SAP scope-item catalog - 'which scope item covers X?'. Use this, NOT search_brain, for that question. A scope item is one short line ('2LH - Automated Invoice Settlement'), and a short line cannot out-score paragraphs of prose on cosine similarity, so the brain reliably buries the correct scope item beneath delivery documents that merely discuss the topic at length. Matching the catalog directly is both exact and instant. AN EMPTY RESULT MEANS NO SUCH SCOPE ITEM EXISTS, and that is the answer - say so, rather than falling back to search_brain and quoting its nearest prose hit as though it were a scope item. 'supplier invoice processing' returns nothing, because no scope item is named that; 'invoice' returns 21. retired: true means SAP has withdrawn the scope item - per CLAUDE.md a retired catalog hit means the OPPOSITE of available, so never cite one as current without saying so.",
        "schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Scope item id ('2LH') or business topic ('invoice')"},
            "limit": {"type": "integer", "description": "Max results (default 20)"}},
            "required": []},
        "handler": tool_lookup_scope_item,
    },
    "lookup_process": {
        "description": "EXACT LOOKUP over the 657 SAP Best Practices processes for this release: what a scope item DOES. Returns its Fiori applications, its process steps, the roles that perform them, its line of business, and its changeCategory for the release. Use this, NOT search_brain, for 'what does 2LH involve' - the authoritative record is structured and short, so it loses on cosine similarity to any document that merely discusses the topic at length. ALSO ANSWERS THE REVERSE EDGE, which nothing else here can: pass `application` to get every scope item that uses a given Fiori app ('Post Goods Receipt for Inbound Delivery' returns 16). That is the impact-analysis question asked whenever an app is being extended or replaced. Matched as a substring, so a partial label works. COVERAGE IS REPORTED, NOT ASSUMED. 537 of 657 scope items carry applications and 574 carry steps, so an empty list means SAP published none for that process rather than the lookup failing; the `coverage` block gives the current numbers. Capabilities are absent entirely because SAP had published no SolutionCapabilityHierarchy rows for the 2608 release - 0, against 53,067 for 2602. ALSO CARRIES SAP's FOUR-LEVEL CAPABILITY TAXONOMY - solution capability, business capability, business area, line of business - which exists nowhere else in this brain above the LoB level. Filter with `capability` to ask 'which scope items deliver Invoice Management' (26). BUT CHECK capability_source_release ON EVERY ROW: SAP had published no capability data for 2608, so it is joined from the newest release that has it (2602), by scope item rather than by process GUID. The taxonomy is stable across releases in a way an API contract is not, but say which release it came from if you quote it. Rows with has_country_process false are catalog scope items this country has no process for: they carry steps and a name but no LoB or applications. Pairs with lookup_scope_item (which scope item covers a topic) and lookup_accelerator (which documents describe it, and their URLs). App names are SAP's labels and are not release contracts: object release state still comes from check_object_release_state.",
        "schema": {"type": "object", "properties": {
            "query":       {"type": "string", "description": "Scope item id ('2LH'), process name, app name, or step text"},
            "scope_item":  {"type": "string", "description": "Exact scope item filter, e.g. 2LH"},
            "lob":         {"type": "string", "description": "Line of business substring, e.g. Finance"},
            "application": {"type": "string", "description": "REVERSE EDGE: return every scope item using this Fiori app (substring match)"},
            "capability":  {"type": "string", "description": "Business area, business capability or solution capability, any level (substring). 'Invoice Management' returns 26 scope items."},
            "with_steps":  {"type": "boolean", "description": "Include the full step and role lists (default true)"},
            "limit":       {"type": "integer", "description": "Max results (default 10)"}},
            "required": []},
        "handler": tool_lookup_process,
    },
}


# ── MCP JSON-RPC over stdio ────────────────────────────────────────────────────
def _make_result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}

def handle_request(msg):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Semantic RAG over the S4PC Public Cloud Brain (Bedrock Titan + FAISS).",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": t["description"], "inputSchema": t["schema"]}
                          for n, t in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": "Unknown tool: %s" % name}], "isError": True}
        try:
            return _make_result(TOOLS[name]["handler"](args))
        except Exception as exc:
            return {"content": [{"type": "text", "text": "Tool error: %s" % exc}], "isError": True}
    raise ValueError("Unknown method: %s" % method)

def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg_id = msg.get("id")
        if "method" not in msg or msg_id is None:
            continue                               # notification — no response
        try:
            result = handle_request(msg)
            reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except ValueError as exc:
            reply = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": str(exc)}}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()

def cli():
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    raw  = sys.argv[3] if len(sys.argv) > 3 else "{}"
    if name not in TOOLS:
        print(json.dumps({"error": "unknown tool", "tools": sorted(TOOLS)}))
        sys.exit(2)
    print(json.dumps(TOOLS[name]["handler"](json.loads(raw)), indent=2, ensure_ascii=False))

def http_server(port=3001):
    """Streamable-HTTP MCP transport — run on EC2, forward via SSH tunnel.
    On EC2:  nohup python3.11 mcp-server/brain_server.py --http 3001 > brain/http.out 2>&1 &
    Locally: SSH tunnel localhost:3001 -> EC2:3001  (see start-brain-tunnel.bat)
    Register: claude mcp add s4pc-brain --transport http http://localhost:3001/mcp
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    import uuid

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Mcp-Session-Id")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/health":
                body = json.dumps({"status": "ok", "server": SERVER_NAME}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in ("/mcp", "/"):
                # Streamable HTTP spec: GET /mcp is for server-initiated messages.
                # We don't push events, so return 405 — client should POST instead.
                self.send_response(405)
                self.send_header("Allow", "POST, OPTIONS")
                self._cors()
                self.end_headers()
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path not in ("/mcp", "/"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                msg = json.loads(raw)
            except Exception as exc:
                self.send_error(400, "Bad request: %s" % exc)
                return

            msg_id = msg.get("id")
            if msg_id is None:
                # Notification — acknowledge without body
                self.send_response(202)
                self._cors()
                self.end_headers()
                return

            try:
                result = handle_request(msg)
                reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            except ValueError as exc:
                reply = {"jsonrpc": "2.0", "id": msg_id,
                         "error": {"code": -32601, "message": str(exc)}}
            except Exception as exc:
                reply = {"jsonrpc": "2.0", "id": msg_id,
                         "error": {"code": -32603, "message": str(exc)}}

            # MCP Streamable HTTP: respond with SSE when client requests it (Claude Code does).
            accept = self.headers.get("Accept", "")
            body_json = json.dumps(reply, ensure_ascii=False)
            session_id = str(uuid.uuid4()) if msg.get("method") == "initialize" else None

            if "text/event-stream" in accept:
                sse_body = ("data: " + body_json + "\n\n").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self._cors()
                if session_id:
                    self.send_header("Mcp-Session-Id", session_id)
                self.send_header("Content-Length", str(len(sse_body)))
                self.end_headers()
                self.wfile.write(sse_body)
            else:
                body_bytes = body_json.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                if session_id:
                    self.send_header("Mcp-Session-Id", session_id)
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)

    class _ThreadedServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    # Loopback by default — this transport is unauthenticated. An SSH tunnel resolves
    # its forward target on this host, so 127.0.0.1 serves it without a wildcard bind.
    host = os.environ.get("S4PC_MCP_HOST", "127.0.0.1")
    sys.stderr.write("[s4pc-brain] HTTP MCP server starting on %s:%d\n" % (host, port))
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write("[s4pc-brain] WARNING: bound to %s — no authentication.\n" % host)
    sys.stderr.write("[s4pc-brain] Register: claude mcp add s4pc-brain --transport http http://localhost:%d/mcp\n" % port)
    sys.stderr.flush()
    srv = _ThreadedServer((host, port), _Handler)
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--tool":
        cli()
    elif len(sys.argv) > 1 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 3001
        http_server(port)
    else:
        main()
