#!/usr/bin/env python3
"""
S4PC unified MCP server — AWS Lambda adapter (long-term, serverless target).

This is a THIN adapter over mcp-server/server.py. It does NOT re-implement any
tool or the JSON-RPC layer: it imports server.handle_request (the exact same code
the EC2/stdio POC runs) and just translates between an API Gateway HTTP API (v2)
event and the MCP Streamable-HTTP wire format. The POC path is unaffected — this
file is only loaded inside Lambda.

Wire behaviour mirrors server.http_server():
  * POST /mcp        -> handle_request(); reply as SSE (data: <json>\\n\\n) when the
                        client sends Accept: text/event-stream (Claude Code does),
                        else plain application/json. Mcp-Session-Id on initialize.
  * POST /mcp (notif)-> 202 Accepted, no body (id is None).
  * GET  /mcp        -> 405 (no server-initiated stream).
  * GET  /health     -> 200 {status: ok}.
  * OPTIONS          -> 204 + CORS.

Brain vectors live in Aurora (BRAIN_BACKEND=pgvector); no faiss binary, no index
file to load — search_brain runs a single SQL query. The Postgres DSN is fetched
from Secrets Manager at cold start (env DB_SECRET_ARN), so no credential ever sits
in the Lambda configuration or logs. Bedrock is reached via the function's IAM role.
"""

import os
import sys
import json
import uuid

# ── Make the server code importable ────────────────────────────────────────────
# In the deployed package, build.sh co-locates mcp-server/ and scripts/ next to this
# file. When running from the repo (local test), they live one level up. Add whichever
# exists — the other candidate is simply skipped.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _base in (_HERE, _ROOT):
    for _sub in ("mcp-server", "scripts"):
        _p = os.path.join(_base, _sub)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)

# ── One-time DSN hydration from Secrets Manager (before importing the brain) ────
def _hydrate_db_dsn():
    """Populate PGVECTOR_DSN from Secrets Manager if only the secret ARN is given.

    Keeps the Postgres credential out of the Lambda env config: the function is
    granted secretsmanager:GetSecretValue on exactly one secret, fetched at cold
    start. The secret may be a raw DSN string or the standard RDS JSON shape
    ({username,password,host,port,dbname}). Never logged.
    """
    if os.environ.get("PGVECTOR_DSN"):
        return
    secret_arn = os.environ.get("DB_SECRET_ARN")
    if not secret_arn:
        return
    try:
        import boto3
        sm = boto3.client("secretsmanager")
        raw = sm.get_secret_value(SecretId=secret_arn).get("SecretString", "")
        try:
            d = json.loads(raw)
            dsn = ("host=%s port=%s dbname=%s user=%s password=%s" % (
                d.get("host", ""), d.get("port", 5432),
                d.get("dbname", d.get("dbName", "postgres")),
                d.get("username", ""), d.get("password", "")))
        except json.JSONDecodeError:
            dsn = raw  # already a libpq DSN / URL
        os.environ["PGVECTOR_DSN"] = dsn
    except Exception as exc:            # brain degrades gracefully; governance is unaffected
        sys.stderr.write("[s4pc-lambda] DSN hydration skipped: %s\n" % exc)

_hydrate_db_dsn()
os.environ.setdefault("BRAIN_BACKEND", "pgvector")        # vectors in Aurora, no faiss
os.environ.setdefault("EXPERIENCE_BACKEND", "postgres")   # record_experience -> shared Aurora DB

import server  # noqa: E402  — reuses handle_request, TOOLS, audit, metrics

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id, Accept",
}


def _resp(status, headers=None, body=""):
    h = dict(_CORS)
    if headers:
        h.update(headers)
    return {"statusCode": status, "headers": h,
            "body": body, "isBase64Encoded": False}


def _event_method(event):
    ctx = (event.get("requestContext") or {}).get("http") or {}
    return (ctx.get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or "/"


def _event_headers(event):
    # HTTP API v2 lowercases header keys; normalise defensively.
    return {(k or "").lower(): v for k, v in (event.get("headers") or {}).items()}


def _event_body(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8", errors="replace")
    return body


def lambda_handler(event, context):
    method = _event_method(event)
    path = _event_path(event)

    if method == "OPTIONS":
        return _resp(204)

    if method == "GET":
        if path.endswith("/health"):
            return _resp(200, {"Content-Type": "application/json"},
                         json.dumps({"status": "ok", "server": "s4pc",
                                     "mode": getattr(server, "MODE", "offline"),
                                     "tools": len(server.TOOLS)}))
        # /mcp GET — no server-initiated event stream
        return _resp(405, {"Allow": "POST, OPTIONS"})

    if method != "POST":
        return _resp(405, {"Allow": "POST, OPTIONS"})

    # ── POST: a JSON-RPC message ───────────────────────────────────────────────
    try:
        msg = json.loads(_event_body(event))
    except Exception as exc:
        return _resp(400, {"Content-Type": "text/plain"}, "Bad request: %s" % exc)

    msg_id = msg.get("id")
    if msg_id is None:
        return _resp(202)          # notification (e.g. notifications/initialized)

    try:
        result = server.handle_request(msg)
        reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    except ValueError as exc:
        reply = {"jsonrpc": "2.0", "id": msg_id,
                 "error": {"code": -32601, "message": str(exc)}}
    except Exception as exc:
        reply = {"jsonrpc": "2.0", "id": msg_id,
                 "error": {"code": -32603, "message": "Internal error: %s" % exc}}

    body_json = json.dumps(reply, ensure_ascii=False)
    headers = {}
    if msg.get("method") == "initialize":
        headers["Mcp-Session-Id"] = str(uuid.uuid4())

    accept = _event_headers(event).get("accept", "")
    if "text/event-stream" in accept:
        headers["Content-Type"] = "text/event-stream"
        headers["Cache-Control"] = "no-cache"
        return _resp(200, headers, "data: " + body_json + "\n\n")
    headers["Content-Type"] = "application/json"
    return _resp(200, headers, body_json)
