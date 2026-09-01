#!/usr/bin/env python3
"""
S4PC Brain MCP server — semantic RAG over the Public Cloud Brain.

Kept SEPARATE from the governance server (mcp-server/server.py) on purpose: that
server is offline, pure-stdlib and never reaches the network. This one performs
retrieval-augmented search using Amazon Bedrock Titan embeddings + a FAISS index
(built by scripts/embed_chunks.py), so it needs boto3/faiss and the EC2 IAM
instance profile. It degrades gracefully: if the deps or index are missing, the
tool returns a helpful message instead of crashing.

Exposes one tool:
    search_brain(query, top_k?, phase?, agent_role?, deliverable_type?,
                 source_system?, dedup?)
        → top matching chunks from the SharePoint delivery docs + SAP scope
          catalog, with phase/agent/source metadata. Deduplicates to distinct
          source documents by default. All content was PII-masked at ingest.

Run (registered via .mcp.json), or standalone:
    python3.11 mcp-server/brain_server.py --tool search_brain '{"query":"cutover plan"}'

Install (on the server that runs the brain):
    pip3.11 install boto3 faiss-cpu numpy
"""

import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME      = "s4pc-brain"
SERVER_VERSION   = "1.0.0"

_SOURCE = ("S4PC Public Cloud Brain — SharePoint delivery documents + SAP scope-item "
           "catalog, embedded with Amazon Bedrock Titan. All content was client/person/PII "
           "masked at ingest. Re-verify any SAP object name on api.sap.com / SAP Help before use.")


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


TOOLS = {
    "search_brain": {
        "description": ("Semantic search over the S4PC Public Cloud Brain — the harvested SharePoint "
                        "delivery knowledge (FDs, TDs, workshop decks, test/cutover/change material) "
                        "plus the SAP scope-item catalog, embedded with Bedrock Titan. Returns the most "
                        "relevant chunks with phase / agent role / deliverable / source. Optional filters "
                        "narrow to a phase (Discover/Prepare/Explore/Realize/Deploy/Run), an agent role "
                        "(e.g. build_agent, qe_agent), or a deliverable type. All content is PII-masked. "
                        "Use to ground a deliverable in prior delivery experience."),
        "schema": {"type": "object", "properties": {
            "query":            {"type": "string", "description": "Natural-language query"},
            "top_k":            {"type": "integer", "description": "Number of results (default 5)"},
            "phase":            {"type": "string", "description": "Filter: Discover/Prepare/Explore/Realize/Deploy/Run"},
            "agent_role":       {"type": "string", "description": "Filter: e.g. build_agent, qe_agent, pmo_agent"},
            "deliverable_type": {"type": "string", "description": "Filter: e.g. functional_design, test_strategy"},
            "source_system":    {"type": "string", "description": "Filter: sharepoint | sap_scope_catalog | accelerator_hub | ..."},
            "dedup":            {"type": "boolean", "description": "Collapse to one hit per source document (default true)"}},
            "required": ["query"]},
        "handler": tool_search_brain,
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

            body = json.dumps(reply, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            if msg.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", str(uuid.uuid4()))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class _ThreadedServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    sys.stderr.write("[s4pc-brain] HTTP MCP server starting on port %d\n" % port)
    sys.stderr.write("[s4pc-brain] Register: claude mcp add s4pc-brain --transport http http://localhost:%d/mcp\n" % port)
    sys.stderr.flush()
    srv = _ThreadedServer(("0.0.0.0", port), _Handler)
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--tool":
        cli()
    elif len(sys.argv) > 1 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 3001
        http_server(port)
    else:
        main()
