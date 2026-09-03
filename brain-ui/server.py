#!/usr/bin/env python3
"""
Brain Explorer — a standalone visualisation UI for the S4PC Public Cloud Brain.

Deliberately SEPARATE from webapp/app.py: that app owns the delivery pipeline and
its human checkpoints, and must not grow a second responsibility. This one is
read-only over the brain and can be shown to a client without exposing any
pipeline control surface.

    python3.11 brain-ui/server.py            # port 8400
    python3.11 brain-ui/server.py --port 9000

Endpoints
    GET  /              the UI
    GET  /health        liveness
    GET  /api/stats     corpus composition, aggregated from the vector-store metadata
    POST /api/search    {"query": str, top_k?, phase?, agent_role?,
                         deliverable_type?, source_system?, dedup?}

Read-only: it never writes to the index and exposes no ingest path. Search needs
boto3 + the vector backend (Bedrock Titan embeds the query); /api/stats needs only
the metadata file, so the UI still renders its composition view on a box without
boto3 installed.
"""

import os
import sys
import json
import argparse
import collections
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR   = Path(__file__).resolve().parent / "ui"
sys.path.insert(0, str(BASE_DIR / "scripts"))

INDEX_DIR  = BASE_DIR / "brain" / "index"
META_PATH  = INDEX_DIR / "metadata.json"
INDEX_PATH = INDEX_DIR / "faiss.index"

# Facets worth charting. Order is the display order in the UI.
FACETS = ("source_system", "phase", "agent_role", "deliverable_type", "content_type")

# SAP Activate runs in this order; counts alone would sort it meaninglessly.
PHASE_ORDER = ("Discover", "Prepare", "Explore", "Realize", "Deploy", "Run",
               "Reference", "General")

_stats_cache = None


def _facet_counts(metas, field):
    counts = collections.Counter(str(m.get(field) or "unknown") for m in metas)
    items = [{"label": k, "count": v} for k, v in counts.most_common()]
    if field == "phase":
        rank = {p: i for i, p in enumerate(PHASE_ORDER)}
        items.sort(key=lambda d: (rank.get(d["label"], len(rank)), -d["count"]))
    return items


def build_stats():
    """Aggregate the corpus once and cache it — 49k records is too slow per request."""
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    if not META_PATH.exists():
        return {"error": "No brain index at %s. Build it with "
                         "python3.11 scripts/embed_chunks.py" % INDEX_DIR}
    metas = json.loads(META_PATH.read_text(encoding="utf-8"))
    stats = {
        "chunks":       len(metas),
        "documents":    len({m.get("source") for m in metas}),
        "index_bytes":  INDEX_PATH.stat().st_size if INDEX_PATH.exists() else 0,
        "backend":      os.environ.get("BRAIN_BACKEND", "faiss"),
        "embed_model":  os.environ.get("TITAN_MODEL", "amazon.titan-embed-text-v2:0"),
        "region":       os.environ.get("AWS_REGION", "us-east-1"),
        "scope_items":  len({m.get("scope_item_id") for m in metas if m.get("scope_item_id")}),
        "facets":       {f: _facet_counts(metas, f) for f in FACETS},
    }
    _stats_cache = stats
    return stats


def run_search(payload):
    try:
        import brain_search
    except Exception as exc:
        return {"error": "Search unavailable: %s. Install deps on this host "
                         "(pip3.11 install boto3 faiss-cpu numpy)." % exc}
    query = (payload.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        hits = brain_search.search(
            query,
            k=int(payload.get("top_k") or 10),
            phase=payload.get("phase") or None,
            agent_role=payload.get("agent_role") or None,
            deliverable_type=payload.get("deliverable_type") or None,
            source_system=payload.get("source_system") or None,
            dedup_source=bool(payload.get("dedup", True)),
        )
    except SystemExit as exc:
        return {"error": "Brain index not ready: %s" % exc}
    except Exception as exc:
        return {"error": "search failed: %s" % exc}

    # The index stores only a pointer to each chunk; the UI wants the prose.
    for h in hits:
        h["snippet"] = _chunk_snippet(h.get("chunk_file"))
    return {"query": query, "count": len(hits), "results": hits}


def _chunk_snippet(chunk_file, limit=600):
    if not chunk_file:
        return ""
    try:
        fp = BASE_DIR / "brain" / chunk_file
        text = json.loads(fp.read_text(encoding="utf-8")).get("text", "")
    except Exception:
        return ""
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


class _Handler(BaseHTTPRequestHandler):
    server_version = "S4PCBrainExplorer/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            f = UI_DIR / "index.html"
            if not f.exists():
                return self._send(500, {"error": "ui/index.html missing"})
            return self._send(200, f.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if path == "/health":
            return self._send(200, {"status": "ok", "service": "brain-explorer"})
        if path == "/api/stats":
            stats = build_stats()
            return self._send(500 if "error" in stats else 200, stats)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/search":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": "bad request: %s" % exc})
        result = run_search(payload)
        return self._send(500 if "error" in result else 200, result)


class _Threaded(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description="S4PC Brain Explorer UI")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRAIN_UI_PORT", 8400)))
    ap.add_argument("--host", default=os.environ.get("BRAIN_UI_HOST", "127.0.0.1"),
                    help="0.0.0.0 to expose beyond localhost (put it behind a "
                         "proxy that terminates TLS and authenticates)")
    args = ap.parse_args()

    stats = build_stats()
    if "error" in stats:
        sys.stderr.write("[brain-ui] WARNING: %s\n" % stats["error"])
    else:
        sys.stderr.write("[brain-ui] corpus: %d chunks / %d documents\n"
                         % (stats["chunks"], stats["documents"]))
    sys.stderr.write("[brain-ui] http://%s:%d\n" % (args.host, args.port))
    sys.stderr.flush()
    _Threaded((args.host, args.port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
