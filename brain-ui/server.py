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
import re
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
BACKUP_MARKER = BASE_DIR / "brain" / ".last_backup"   # written by scripts/backup_brain.sh

# brain/ is git-ignored and not restored by bootstrap, so a stale backup is a real
# risk that is otherwise invisible until a restore is needed. Surface its age here.
BACKUP_STALE_HOURS = 36        # daily cron + headroom for one missed run

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
    stats.update(_backup_status())
    _stats_cache = stats
    return stats


def _backup_status():
    """Age of the last successful S3 backup. Not cached with the rest — it changes."""
    import datetime
    if not BACKUP_MARKER.exists():
        return {"backup_at": None, "backup_age_hours": None, "backup_stale": True}
    try:
        ts = datetime.datetime.strptime(
            BACKUP_MARKER.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return {"backup_at": None, "backup_age_hours": None, "backup_stale": True}
    age = (datetime.datetime.utcnow() - ts).total_seconds() / 3600.0
    return {"backup_at": ts.strftime("%Y-%m-%d %H:%M UTC"),
            "backup_age_hours": round(age, 1),
            "backup_stale": age > BACKUP_STALE_HOURS}


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


_mcp = None

def _load_mcp():
    """The governance server, imported lazily and kept.

    Lazily because a viewer that nobody has asked for a trace should not be holding the
    vector and graph engines: the host has 3.7 GB and s4pc-mcp already carries them under a
    2 GB ceiling. The engines inside are themselves lazy, so this import is cheap until a
    trace actually runs.

    Imported rather than called over HTTP on :3002 so the trace runs the SAME code an agent
    runs — the release verdict especially. Reimplementing that here would be a second copy of
    the rules that drifts from the first, and a demo that disagrees with the pipeline is worse
    than no demo. (It would also need an API key, since S4PC_API_KEYS applies to loopback too.)
    """
    global _mcp
    if _mcp is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "s4pc_mcp_trace", str(BASE_DIR / "mcp-server" / "server.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _mcp = mod
    return _mcp


def _call(mcp, tool, args):
    """Run one governance tool, timed. Never raises — a layer that fails is reported as a
    failed layer, because a trace that dies halfway tells the audience less than one that
    says which layer was unavailable.

    A tool that returns {"error": ...} counts as failed. It does NOT raise, so reading only
    its results would render an unavailable layer as an empty one — the same "fails by
    returning less" trap layer_health exists to catch, and the worst possible thing to have
    happen while showing this to an audience.
    """
    import time as _t
    started = _t.time()
    try:
        payload = mcp.TOOLS[tool]["handler"](args)
        err = payload.get("error") if isinstance(payload, dict) else None
    except KeyError:
        payload, err = {}, "tool '%s' is not registered on this host" % tool
    except Exception as exc:                                  # noqa: BLE001
        payload, err = {}, str(exc)
    return payload, err, int((_t.time() - started) * 1000)


def _graph_connections(payload, limit=6):
    """Flatten get_object_graph's `connections`, which is a dict keyed by object type
    ({"api": [...], "badi": [...], "cds_view": [...]}) — not a flat list under `related`
    or `edges`. Guessing those names showed an empty graph on a host holding 275k edges."""
    out = []
    for otype, entries in (payload.get("connections") or {}).items():
        for e in (entries or []):
            if isinstance(e, dict) and e.get("name"):
                out.append({"name": e["name"], "type": otype,
                            "score": None, "note": (e.get("title") or "")[:80]})
            if len(out) >= limit:
                return out
    return out


def _objects_from(payload, limit=6):
    """Object names a retrieval surfaced, wherever the tool happens to put them."""
    names, seen = [], set()
    for hit in (payload.get("results") or payload.get("hits") or []):
        if not isinstance(hit, dict):
            continue
        for key in ("object_name", "name", "id", "object"):
            v = hit.get(key)
            if isinstance(v, str) and v.strip() and v.upper() not in seen:
                seen.add(v.upper()); names.append(v.strip()); break
        for v in (hit.get("objects_mentioned") or []):
            if isinstance(v, str) and v.strip() and v.upper() not in seen:
                seen.add(v.upper()); names.append(v.strip())
    return names[:limit]


def run_trace(payload):
    """One question, and what each layer contributed to answering it.

    The point of the panel: L2/L4 surface candidate objects, L1 expands them into their
    neighbourhood, L3 supplies the lessons that apply, and only then does the governance
    check say whether any of it is actually released. A chat answer collapses all of that
    into prose and discards the provenance, which is the part worth showing.
    """
    query = (payload.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        mcp = _load_mcp()
    except Exception as exc:                                  # noqa: BLE001
        return {"error": "governance server could not be loaded: %s" % exc}

    layers, total = [], 0

    def add(layer, title, tool, what, data, err, ms, items):
        layers.append({"layer": layer, "title": title, "tool": tool, "what": what,
                       "ms": ms, "error": err, "count": len(items), "items": items})

    # L2 — released-object semantic index (catalog side)
    l2, err2, ms2 = _call(mcp, "semantic_search", {"query": query, "top_k": 5})
    total += ms2
    l2_items = [{"name": h.get("object_name") or h.get("name") or "",
                 "type": h.get("object_type") or h.get("type") or "",
                 "score": h.get("score")}
                for h in (l2.get("results") or [])[:5] if isinstance(h, dict)]
    add("L2", "Semantic index", "semantic_search",
        "released SAP objects matching the meaning of the question", l2, err2, ms2, l2_items)

    # L4 — the learning corpus
    l4, err4, ms4 = _call(mcp, "search_brain", {"query": query, "top_k": 3})
    total += ms4
    l4_items = [{"name": h.get("title") or h.get("source_file") or h.get("chunk_file") or "",
                 "type": h.get("deliverable_type") or h.get("source_system") or "",
                 "score": h.get("score")}
                for h in (l4.get("results") or [])[:3] if isinstance(h, dict)]
    add("L4", "Learning corpus", "search_brain",
        "past delivery documents that answer this kind of question", l4, err4, ms4, l4_items)

    # L1 — the object graph, expanded around the best candidate the searches surfaced.
    # An object name typed directly is a seed in its own right: the sharpest demo of this
    # whole stack is asking about one specific object (a real one beside a fabricated one),
    # and that must not depend on a semantic search happening to surface it first.
    seeds = _objects_from(l2) or _objects_from(l4)
    if not seeds and re.match(r"^[A-Za-z][A-Za-z0-9_]{3,}$", query):
        seeds = [query]
    g, l1_items, err1, ms1 = {}, [], None, 0
    if seeds:
        g, err1, ms1 = _call(mcp, "get_object_graph", {"object_name": seeds[0]})
        total += ms1
        l1_items = _graph_connections(g)
    add("L1", "Object graph", "get_object_graph",
        ("what %s connects to (%s in total)" % (seeds[0], (g.get("total_connections") if seeds else 0)))
        if seeds else "nothing to expand — no object surfaced above", {}, err1, ms1, l1_items)

    # L3 — lessons this team has already recorded
    l3, err3, ms3 = _call(mcp, "query_experience", {"query": query})
    total += ms3
    l3_items = [{"name": e.get("id") or "", "type": e.get("category") or "",
                 "score": None, "note": (e.get("topic") or "")[:120]}
                for e in (l3.get("results") or [])[:3] if isinstance(e, dict)]
    add("L3", "Experience", "query_experience",
        "lessons from previous runs that apply here", l3, err3, ms3, l3_items)

    # Governance — the gate. Verdicts for whatever the layers above surfaced.
    gov_items, msg = [], 0
    for name in seeds[:3]:
        v, errv, msv = _call(mcp, "check_object_release_state", {"object_name": name})
        msg += msv
        gov_items.append({"name": name, "type": v.get("evidence") or ("error" if errv else ""),
                          "score": None, "note": v.get("verdict") or errv or ""})
    total += msg
    add("GOV", "Release governance", "check_object_release_state",
        "whether each object is actually released — verdict AND evidence", {}, None, msg, gov_items)

    return {"query": query, "total_ms": total, "layers": layers}


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
            if "error" not in stats:
                # build_stats() is cached, but backup age must not be — re-read the
                # marker each request or the UI would show a frozen "hours ago".
                stats = dict(stats, **_backup_status())
            return self._send(500 if "error" in stats else 200, stats)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/search", "/api/trace"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": "bad request: %s" % exc})
        result = run_trace(payload) if path == "/api/trace" else run_search(payload)
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
