#!/usr/bin/env python3
"""
S4PC Catalyst — demo webapp for the S/4HANA Public Cloud agentic pipeline.

Zero-dependency (Python 3.9+ stdlib), cross-platform (macOS / Windows / Linux).
Serves the Catalyst-style UI and a JSON API that is wired to the REAL MCP server
logic in ../mcp-server/server.py — the playground tools, catalogs, guardrails and
admin dashboard all reflect actual state, nothing is mocked.

Run:  python3 webapp/app.py        (macOS/Linux)
      py -3 webapp\\app.py          (Windows)
Env:  S4PC_UI_PORT (default 8321), S4PC_UI_HOST (default 127.0.0.1),
      S4PC_UI_NO_BROWSER=1 to suppress auto-open.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import threading
import urllib.parse
import uuid
import webbrowser
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
UI_DIR = os.path.join(APP_DIR, "ui")
MCP_DIR = os.path.join(ROOT_DIR, "mcp-server")

# Load the catalog DB module directly (avoids a second full server.py load)
_db_spec = importlib.util.spec_from_file_location(
    "s4pc_catalog_db", os.path.join(MCP_DIR, "catalog", "db.py"))
_catalog_db = importlib.util.module_from_spec(_db_spec)
_db_spec.loader.exec_module(_catalog_db)

HOST = os.environ.get("S4PC_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("S4PC_UI_PORT", "8321"))
# Optional shared-instance access control (HTTP Basic auth). When S4PC_ACCESS_PASSWORD is set,
# the whole app requires it; when it is unset (the default) the app is open — local single-user
# mode, behaviour unchanged. Used for the "one shared machine" hosting option.
ACCESS_USER = os.environ.get("S4PC_ACCESS_USER", "team")
ACCESS_PASSWORD = os.environ.get("S4PC_ACCESS_PASSWORD", "")
STARTED_AT = time.time()

# ------------------------------------------------ load the real MCP module ---

def _load_mcp():
    spec = importlib.util.spec_from_file_location("s4pc_mcp", os.path.join(MCP_DIR, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

MCP = _load_mcp()

# UI-side request stats (in-memory; MCP-side stats live in mcp-server/logs)
UI_STATS = {"requests": 0, "tool_runs": {}, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
_LOCK = threading.Lock()

# ------------------------------------------------------------- data readers ---

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default

def list_skills():
    skills_dir = os.path.join(ROOT_DIR, ".claude", "skills")
    out = []
    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            md = os.path.join(skills_dir, name, "SKILL.md")
            if not os.path.isfile(md):
                continue
            with open(md, "r", encoding="utf-8") as fh:
                text = fh.read()
            desc = ""
            m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
            if m:
                desc = m.group(1).strip()
            out.append({"name": name, "description": desc,
                        "size_kb": round(len(text) / 1024, 1), "path": ".claude/skills/%s/SKILL.md" % name,
                        "content": text})
    return out

def list_workflows():
    wf_dir = os.path.join(ROOT_DIR, "workflows")
    out = []
    if os.path.isdir(wf_dir):
        for fname in sorted(os.listdir(wf_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(wf_dir, fname)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            title = fname[:-3]
            m = re.match(r"#\s*(.+)", text)
            if m:
                title = m.group(1).strip()
            wtype = "RICEFW" if fname.upper().startswith("RICEFW") else "Process"
            desc = ""
            m = re.search(r"\*\*Realization:\*\*\s*(.+)", text)
            if not m:
                m = re.search(r"\*\*Skill:\*\*\s*(.+)", text)
            if m:
                desc = re.sub(r"[`*]", "", m.group(1)).strip()
            out.append({"file": fname, "title": title, "type": wtype, "description": desc,
                        "size_kb": round(len(text) / 1024, 1), "content": text})
    return out

def _canon_workflow(label, extensibility_mode=""):
    """Collapse the engine's free-text pipeline labels into ONE canonical string per variant, so the
    Workflow Explorer dropdown shows a clean, de-duplicated type list (fixes mojibake and the
    '12 steps' / '14 steps incl. BTP deploy' drift). Non-pipeline workflows pass through unchanged.

    extensibility_mode is the authoritative source: only side-by-side runs qualify for the
    14-step BTP variant — even if the workflow label was incorrectly written as 14-step because
    an evaluated-but-rejected BTP option existed in the proposal."""
    s = (label or "").strip()
    mode = (extensibility_mode or "").lower()
    if not s:
        return "RICEFW Pipeline (12 steps)"
    low = s.lower()
    if "ricefw" in low or "pipeline" in low:
        is_btp_workflow = ("btp" in low or "deploy" in low)
        is_side_by_side = "side" in mode
        if is_btp_workflow and is_side_by_side:
            return "RICEFW Pipeline (14 steps, incl. BTP deploy)"
        return "RICEFW Pipeline (12 steps)"
    return s

def list_runs():
    """Pipeline runs = output/<ID>/run.json manifests written by the s4pc-ricefw-pipeline skill."""
    out_dir = os.path.join(ROOT_DIR, "output")
    runs = []
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            manifest = os.path.join(out_dir, name, "run.json")
            data = read_json(manifest)
            if not data:
                continue
            data["folder"] = name
            data["workflow"] = _canon_workflow(data.get("workflow"), data.get("extensibility_mode", ""))
            run_dir_path = os.path.join(out_dir, name)
            data["files"] = sorted(
                f for f in os.listdir(run_dir_path)
                if f != "run.json" and not f.startswith(".")
                and os.path.isfile(os.path.join(run_dir_path, f))
            )
            runs.append(data)
    # Derive the version chain from ids/fd_source so run history is robust regardless of what
    # the engine writes into run.json (each FD's runs: v1 = base id, re-runs = '-R<n>').
    def _ver(rid):
        m = re.search(r"-R(\d+)$", rid or "")
        return int(m.group(1)) if m else 1
    for r in runs:
        r["version"] = _ver(r.get("id") or r.get("folder"))
    by_fd = {}
    for r in runs:
        by_fd.setdefault(r.get("fd_source"), []).append(r)
    for group in by_fd.values():
        group.sort(key=lambda r: r["version"])
        for i, r in enumerate(group):
            r["previous_run"] = group[i - 1].get("id") if i > 0 else None
            r["run_count_for_fd"] = len(group)
    return {"runs": runs, "source": "output/<ID>/run.json — written by the pipeline skill on every run"}

def run_file(run_id, fname):
    # both names must be plain (no separators/traversal) and resolve inside output/<run_id>/
    if not re.match(r"^[A-Za-z0-9._ -]+$", run_id or "") or not re.match(r"^[A-Za-z0-9._ -]+$", fname or ""):
        return {"error": "Invalid name"}, 400
    path = os.path.join(ROOT_DIR, "output", run_id, fname)
    if not os.path.isfile(path):
        return {"error": "Not found"}, 404
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return {"run": run_id, "file": fname, "content": fh.read()}, 200

SAFE_NAME = re.compile(r"^[A-Za-z0-9._ -]{1,120}$")

def list_inputs():
    """FD documents queued in input/ — the developer's entry point to the pipeline."""
    in_dir = os.path.join(ROOT_DIR, "input")
    items = []
    if os.path.isdir(in_dir):
        for fname in sorted(os.listdir(in_dir)):
            if fname.startswith(".") or not fname.lower().endswith((".md", ".txt")):
                continue
            path = os.path.join(in_dir, fname)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            title = fname
            m = re.match(r"#\s*(.+)", text)
            if m:
                title = m.group(1).strip()
            rel = "input/" + fname
            linked = [r["id"] for r in list_runs()["runs"] if r.get("fd_source") == rel]
            items.append({"file": fname, "path": rel, "title": title,
                          "size_kb": round(len(text) / 1024, 1), "content": text,
                          "linked_runs": linked})
    return {"inputs": items}

def save_input(name, content):
    if not SAFE_NAME.match(name or "") or "/" in (name or "") or "\\" in (name or ""):
        return {"error": "Invalid file name (letters, digits, dot, dash, space only)"}, 400
    if not name.lower().endswith((".md", ".txt")):
        name += ".md"
    if not content or not content.strip():
        return {"error": "Empty document"}, 400
    if len(content) > 2_000_000:
        return {"error": "Document too large (2 MB max)"}, 400
    in_dir = os.path.join(ROOT_DIR, "input")
    os.makedirs(in_dir, exist_ok=True)
    path = os.path.join(in_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    MCP.audit("fd_uploaded", {"file": name, "size": len(content)})
    return {"ok": True, "path": "input/" + name,
            "next_step": "In Claude Code: Run the s4pc-ricefw-pipeline on input/%s" % name}, 200

# ---------------------------------------------------------------------------
# Document text extraction (Word .docx / PDF / .txt / .md) — pure stdlib.
# The FD is extracted to clean markdown AT UPLOAD TIME so the pipeline always
# receives readable text and never halts at Intake on an unreadable binary.
# ---------------------------------------------------------------------------

def _decode_text(raw):
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")

def _docx_to_text(raw):
    """Word .docx = a zip of XML. Read the document part(s), keep paragraph/tab
    breaks, strip tags, unescape entities. Uses only zipfile/io/html/re."""
    import zipfile, io, html as _html
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        parts = [n for n in ("word/document.xml",) if n in names]
        parts += sorted(n for n in names if re.match(r"word/(header|footer)\d*\.xml$", n))
        if not parts:
            raise ValueError("not a Word .docx (no word/document.xml)")
        out = []
        for part in parts:
            xml = z.read(part).decode("utf-8", "replace")
            # drop non-content runs so field codes / deleted text don't leak in
            xml = re.sub(r"<w:instrText\b[^>]*>.*?</w:instrText>", "", xml, flags=re.S)
            xml = re.sub(r"<w:delText\b[^>]*>.*?</w:delText>", "", xml, flags=re.S)
            xml = re.sub(r"<w:tab\b[^>]*/?>", "\t", xml)
            xml = re.sub(r"<w:br\b[^>]*/?>", "\n", xml)
            xml = re.sub(r"</w:p\b[^>]*>", "\n", xml)     # paragraph → newline
            xml = re.sub(r"<[^>]+>", "", xml)              # drop remaining tags
            out.append(_html.unescape(xml))
    text = "\n".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _inflate(data):
    import zlib
    for wbits in (15, -15, 47):                            # zlib, raw-deflate, gzip/auto
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(data) + d.flush()
            if out:
                return out
        except Exception:
            continue
    return data

def _pdf_unescape(s):
    s = s.replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")
    s = s.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    return re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), s)

def _pdf_to_text(raw):
    """Best-effort PDF text-layer extraction with stdlib only: inflate content
    streams (FlateDecode via zlib) and pull text from Tj/TJ operators. Works for
    PDFs with a real text layer; scanned/CID-font PDFs may yield little."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        data = m.group(1).rstrip(b"\r\n")
        dec = _inflate(data)
        if b"BT" not in dec and b"Tj" not in dec and b"TJ" not in dec:
            continue                                       # skip image/font streams
        s = dec.decode("latin-1", "replace")
        for tok in re.finditer(r"\((?:\\.|[^()\\])*\)|\bT[dD]\b|\bT\*\b|'|\"", s):
            g = tok.group(0)
            if g.startswith("("):
                out.append(_pdf_unescape(g[1:-1]))
            else:
                out.append("\n")                           # positioning op → line break
    text = "".join(out)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _salvage_text(raw):
    """Legacy binary .doc (OLE) — no clean stdlib parser; pull readable runs."""
    txt = raw.decode("latin-1", "replace")
    runs = re.findall(r"[ -~\t]{6,}", txt)
    return "\n".join(r.strip() for r in runs).strip()

def extract_document_text(filename, raw):
    """Return (text, note). Chooses a parser by extension, with content sniffing
    as a fallback so a mis-named file still works."""
    ext = (os.path.splitext(filename or "")[1] or "").lower()
    if ext in (".md", ".txt", ""):
        return _decode_text(raw), "plain text"
    if ext == ".docx" or raw[:4] == b"PK\x03\x04":
        try:
            return _docx_to_text(raw), "extracted from Word .docx"
        except Exception:
            pass
    if ext == ".pdf" or raw[:5] == b"%PDF-":
        return _pdf_to_text(raw), "extracted from PDF text layer (best-effort)"
    if ext == ".doc":
        return _salvage_text(raw), "legacy .doc salvage (best-effort) — prefer .docx"
    return _decode_text(raw), "decoded as text"

def save_input_document(name, b64):
    """Accept a base64-encoded uploaded file (.docx/.pdf/.txt/.md/.doc), extract
    its text server-side, and save clean markdown into input/. Never fails on an
    unreadable file — saves a stub with a note so the pipeline is not blocked."""
    import base64
    try:
        raw = base64.b64decode(b64 or "", validate=False)
    except Exception as exc:
        return {"error": "Bad file data: %s" % exc}, 400
    if not raw:
        return {"error": "Empty file"}, 400
    if len(raw) > 15_000_000:
        return {"error": "File too large (15 MB max)"}, 400
    try:
        text, note = extract_document_text(name, raw)
    except Exception as exc:
        text, note = "", "extraction error: %s" % exc
    base = re.sub(r"\.(docx|pdf|doc|txt|md)$", "", name or "", flags=re.I)
    base = re.sub(r"[^A-Za-z0-9._ -]", "-", base).strip()[:100] or "FD"
    md_name = base + ".md"
    if not text.strip():
        text = ("# %s\n\n_No machine-readable text could be extracted from the uploaded file "
                "(%s). It may be a scanned/image-only document. Paste or edit the FD content "
                "here — the pipeline will not be blocked._\n" % (base, note))
        note = "no text layer found — stub saved (pipeline not blocked)"
    res, code = save_input(md_name, text)
    if code == 200:
        res["chars"] = len(text)
        res["note"] = note
        MCP.audit("fd_extracted", {"source": name, "saved": md_name,
                                   "chars": len(text), "note": note})
    return res, code

# Tools surfaced on the MCP Servers reference tab. The others (live-only OData tools and the
# offline catalog-search tools that public sources supersede) still work for the pipeline —
# they're just hidden from the reference display to keep it relevant.
UI_VISIBLE_TOOLS = {
    # ── clean-core gates ────────────────────────────────────────────────────────
    "check_object_release_state", "abap_cloud_lint", "extensibility_advisor",
    "get_reference_links",
    # ── experience ──────────────────────────────────────────────────────────────
    "query_experience", "record_experience",
    # ── Digital Brain: Layer 1 (Live Object Graph) ───────────────────────────────
    "get_object_graph", "get_area_map", "sync_object_graph",
    # ── Digital Brain: Layer 2+3 (Knowledge Vectors + Experience Graph) ──────────
    "semantic_search", "find_similar_delivery", "rebuild_vector_index",
}

def mcp_inventory():
    all_tools = [{"name": n, "description": t["description"],
                  "live_only": n in ("odata_query", "odata_get_metadata", "sap_connection_test")}
                 for n, t in MCP.TOOLS.items()]
    tools = [t for t in all_tools if t["name"] in UI_VISIBLE_TOOLS]
    return {
        "servers": [{
            "name": "s4pc-mcp",
            "title": "S/4HANA Public Cloud clean-core MCP",
            "version": MCP.CONFIG.get("server", {}).get("version", "1.0.0"),
            "transport": "stdio (zero-dependency Python)",
            "mode": MCP.MODE,
            "tool_count": len(tools),
            "hidden_tool_count": len(all_tools) - len(tools),
            "tools": tools,
            "resources": 5,
            "prompts": len(MCP.PROMPTS),
        }]
    }

def brain_status():
    """Return Digital Brain Layer 1 + Layer 2 backend info without loading the full model."""
    vector_dir = os.path.join(MCP_DIR, "vector")
    graph_dir  = os.path.join(MCP_DIR, "graph")
    index_path = os.path.join(vector_dir, "index.json")
    embed_path = os.path.join(vector_dir, "index.npy")
    graph_path = os.path.join(graph_dir,  "graph.json")

    st_installed  = importlib.util.find_spec("sentence_transformers") is not None
    index_exists  = os.path.isfile(index_path)
    embed_exists  = os.path.isfile(embed_path)
    graph_exists  = os.path.isfile(graph_path)

    engine = "not_built"; model = ""; doc_count = 0; index_mtime = None
    if index_exists:
        try:
            index_mtime = int(os.path.getmtime(index_path))
            with open(index_path, encoding="utf-8") as fh:
                d = json.load(fh)
            engine = d.get("engine", "unknown")
            model  = d.get("model", "")
            doc_count = len(d.get("docs", []))
        except Exception:
            pass

    graph_nodes = 0; graph_edges = 0; graph_areas = 0; graph_mtime = None
    if graph_exists:
        try:
            graph_mtime = int(os.path.getmtime(graph_path))
            if os.path.getsize(graph_path) < 100_000_000:
                with open(graph_path, encoding="utf-8") as fh:
                    gd = json.load(fh)
                gs = gd.get("stats", {})
                graph_nodes = gs.get("nodes", 0)
                graph_edges = gs.get("edges", 0)
                graph_areas = gs.get("areas", 0)
        except Exception:
            pass

    return {
        "st_installed": st_installed,
        "index_exists": index_exists,
        "embed_exists": embed_exists,
        "active_engine": engine,
        "model": model,
        "doc_count": doc_count,
        "index_mtime": index_mtime,
        "graph_exists": graph_exists,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "graph_areas": graph_areas,
        "graph_mtime": graph_mtime,
        "auto_rebuild": True,
    }


def summary():
    con = _catalog_db.get_conn()
    try:
        apis       = con.execute("SELECT COUNT(*) FROM apis").fetchone()[0]
        badis      = con.execute("SELECT COUNT(*) FROM badis").fetchone()[0]
        cds        = con.execute("SELECT COUNT(*) FROM cds_views").fetchone()[0]
        lint       = con.execute("SELECT COUNT(*) FROM lint_rules").fetchone()[0]
        experience = con.execute("SELECT COUNT(*) FROM experience").fetchone()[0]
    finally:
        con.close()
    agents = len((read_json(os.path.join(APP_DIR, "data", "agents.json"), {}) or {}).get("agents", []))
    return {
        "skills": len(list_skills()), "workflows": len(list_workflows()),
        "agents": agents, "tools": len(MCP.TOOLS), "experience": experience,
        "catalog": {"apis": apis, "badis": badis, "cds_views": cds, "lint_rules": lint},
        "mode": MCP.MODE, "python": sys.version.split()[0], "platform": sys.platform,
    }

def usage_data():
    """Aggregate per-run token usage for the Usage dashboard tab."""
    usage = _load_usage()
    runs_map = {r['id']: r for r in list_runs()['runs']}
    result = []
    seen = set()
    for run_id, rec in sorted(usage.get('runs', {}).items(),
                               key=lambda x: x[1].get('last_updated', ''), reverse=True):
        seen.add(run_id)
        run = runs_map.get(run_id, {})
        phases = [{'job_id': j.get('job'), 'kind': j.get('kind'),
                   'input_tokens': j.get('input', 0), 'output_tokens': j.get('output', 0),
                   'cost_usd': j.get('cost_usd', 0.0)} for j in rec.get('jobs', [])]
        result.append({'run_id': run_id, 'title': run.get('title') or rec.get('fd', ''),
                       'status': run.get('status', 'unknown'), 'phases': phases,
                       'total_input': rec.get('input', 0), 'total_output': rec.get('output', 0),
                       'total_cost': rec.get('est_cost_usd', 0.0)})
    for rid, run in sorted(runs_map.items(), key=lambda x: x[1].get('created', ''), reverse=True):
        if rid not in seen:
            result.append({'run_id': rid, 'title': run.get('title', ''),
                           'status': run.get('status', ''), 'phases': [],
                           'total_input': 0, 'total_output': 0, 'total_cost': 0.0})
    return result

def admin_data():
    metrics = read_json(os.path.join(MCP_DIR, "logs", "metrics.json"), {}) or {}
    audit_path = os.path.join(MCP_DIR, "logs", "audit.jsonl")
    tail = []
    try:
        with open(audit_path, "r", encoding="utf-8") as fh:
            for line in fh.readlines():
                if line.strip():
                    tail.append(json.loads(line))
    except Exception:
        pass
    calls = metrics.get("calls", {})
    total_calls = sum(c.get("count", 0) for c in calls.values())
    total_errors = sum(c.get("errors", 0) for c in calls.values())
    with _LOCK:
        ui = dict(UI_STATS, tool_runs=dict(UI_STATS["tool_runs"]))
    usage = _load_usage()
    uruns = sorted(usage.get("runs", {}).values(), key=lambda r: r.get("last_updated", ""), reverse=True)
    utot = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
            "total_tokens": 0, "cost_usd": 0.0, "est_cost_usd": 0.0, "runs": len(uruns)}
    for r in uruns:
        for k in ("input", "output", "cache_read", "cache_creation", "total_tokens"):
            utot[k] += r.get(k, 0)
        utot["cost_usd"] += r.get("cost_usd", 0)
        utot["est_cost_usd"] += r.get("est_cost_usd", 0)
    utot["est_cost_usd"] = round(utot["est_cost_usd"], 4)
    utot["cost_usd"] = round(utot["cost_usd"], 4)
    return {
        "mcp": {"total_tool_calls": total_calls, "total_errors": total_errors,
                "per_tool": calls, "rate_limited": metrics.get("rate_limited", 0),
                "last_started": metrics.get("started_at")},
        "ui": ui,
        "audit_tail": list(reversed(tail)),
        "uptime_seconds": int(time.time() - STARTED_AT),
        "pipeline_usage": {"runs": uruns, "totals": utot, "rates_usd_per_mtok": COST_RATES},
    }

def settings_data():
    guard = MCP.GUARD
    env_set = {
        "SAP_BASE_URL": bool(os.environ.get("SAP_BASE_URL")),
        "SAP_COMM_USER": bool(os.environ.get("SAP_COMM_USER")),
        "SAP_COMM_PASSWORD": bool(os.environ.get("SAP_COMM_PASSWORD")),
    }
    return {
        "mode": MCP.MODE,
        "writes_allowed": MCP.ALLOW_WRITES,
        "env": env_set,
        "allowlist": guard.get("odata_service_allowlist", []),
        "rate_limit": guard.get("max_requests_per_minute"),
        "odata_max_top": guard.get("odata_max_top"),
        "server_version": MCP.CONFIG.get("server", {}).get("version"),
        "install_path": ROOT_DIR,
        "python": sys.version.split()[0],
        "platform": {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, sys.platform),
        "config_path": os.path.join("mcp-server", "config.json"),
    }

# ------------------------------------------------------- pipeline engine ---
# Executes the pipeline headlessly via the Claude Code CLI (`claude -p`) — the
# developer's logged-in session is the runtime, so still no API keys anywhere.

JOBS = {}  # job_id -> {proc, log, fd, kind, started, prompt_head}
JOBS_LOCK = threading.Lock()
ENGINE_LOG_DIR = os.path.join(APP_DIR, "logs")
os.makedirs(ENGINE_LOG_DIR, exist_ok=True)

# Tools the headless session may use without prompting (permission prompts
# cannot be answered in -p mode; anything else would stall the run).
HEADLESS_ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep",
    "Bash(python3 mcp-server/server.py:*)", "Bash(python mcp-server/server.py:*)",
    "Bash(py -3 mcp-server/server.py:*)", "Bash(mkdir:*)", "Bash(ls:*)",
    "mcp__s4pc__*", "Skill",
]
# NOTE: "Task" is intentionally NOT allowed here. Agent-to-agent interaction in the pipeline is
# implemented at the Python layer (each checkpoint spawns a fresh claude -p process via
# _phase_[a-e]_prompt), not inside a single session. That approach gives clean context per phase,
# avoids the growing-context problem of a long monolithic run, and never stalls on a permission
# prompt. The .claude/agents/ specialists remain available for INTERACTIVE delegation.
# Web verification tools are OFF by default for IP safety — pipeline runs are then fully offline
# (object release is confirmed via the local catalog + SAP naming rules, and deliverables cite the
# authoritative SAP URLs for manual/tenant confirmation, so no content leaves the machine).
# Set S4PC_ALLOW_WEB=1 to force web tools ON for EVERY build. Independent of that flag, side-by-side
# (BTP) builds auto-enable WebFetch/WebSearch per run (see _run_wants_web) so CAP/UI5 code is grounded
# in the authoritative developer docs (cap.cloud.sap, ui5.sap.com, nodejs, npm, w3schools, community).
# Key-user and ABAP-Cloud runs stay fully offline.
_WEB_FORCED_ON = os.environ.get("S4PC_ALLOW_WEB", "").strip().lower() in ("1", "true", "yes", "on")
if _WEB_FORCED_ON:
    HEADLESS_ALLOWED_TOOLS += ["WebFetch", "WebSearch"]


def _run_wants_web(run_id):
    """Auto-enable live developer-doc fetching for side-by-side (BTP) builds only.
    CAP/UI5 code must be grounded in the authoritative docs; key-user / ABAP-Cloud runs stay
    offline. Reads the run's extensibility mode from output/<run_id>/run.json."""
    if _WEB_FORCED_ON:
        return True
    if not run_id:
        return False
    try:
        with open(os.path.join(ROOT_DIR, "output", run_id, "run.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    mode = (data.get("extensibility_mode") or "").lower()
    if "side" in mode or "btp" in mode:
        return True
    wf = (data.get("workflow") or "").lower()
    return "btp" in wf or "14" in wf

# ─── Per-phase scoped prompts ────────────────────────────────────────────────
# Each pipeline checkpoint boundary spawns a FRESH claude -p process whose
# context is limited to only the files that phase needs. This keeps each
# process's context small, avoids re-reading accumulated deliverables from
# prior phases, and eliminates the growing-context problem of the old
# single-session design. CLAUDE.md + steering docs are always in scope via
# the workspace config, so platform rules never need to be restated here.
#
# Mapping:  start          → Phase A (steps 1-4 + CP1)
#           CP1 approved   → Phase B (steps 6-7 + CP2)
#           CP2 approved   → Phase C (steps 8-11 + CP3)
#           CP3 approved   → Phase D (step 12 + optional 13)
#           CP-Deploy appr → Phase E (step 14 only)

_PLAIN_ENGLISH_RULE = (
    "DELIVERABLE WRITING RULES — apply to every .md file you write:\n"
    "  • TOKEN BUDGET: keep total output for this phase under 3,000 words. Be concise — cut anything a reader can infer.\n"
    "  • Use plain, simple English. Explain SAP terms in brackets on first use only.\n"
    "  • Use numbered steps for developer actions. One action per step, ≤20 words.\n"
    "  • Short sentences (≤20 words). Active voice. No filler phrases ('Please note that...', 'It is important to...').\n"
    "  • A junior SAP developer (6 months experience) must understand every instruction without extra help.\n"
)

# Exact findings schema the webapp UI reads — ALL phases that write run.json findings[] MUST use these field names.
_FINDINGS_SCHEMA = (
    "FINDINGS SCHEMA — use these EXACT field names every time you write a finding to run.json findings[]:\n"
    '  {"id":"F-01","severity":"Critical|Major|Minor|Info","source":"<gate or step name e.g. Gate 1>",\n'
    '   "description":"<plain English: what the problem is — one sentence>",\n'
    '   "resolution":"<what the developer must do — one to three numbered steps>",\n'
    '   "verify":"<how to confirm it is fixed — one sentence>",\n'
    '   "status":"Open|Resolved"}\n'
    "  severity values: Critical = blocks build/deploy; Major = serious gap; Minor = improvement; Info = note only.\n"
    "  status: Open = not yet fixed; Resolved = fixed in this phase.\n"
    "  NEVER use values like 'blocker_for_build', 'gate2_fix_applied', 'minor_open' — use the labels above.\n"
)

_SBPA_PHASE_B_INSTRUCTIONS = (
    "SBPA DELIVERY MODE — Steps 6-7 produce process design documentation, NOT code.\n\n"
    "── STEP 6 · SBPA PROCESS DESIGN (Developer) ────────────────────────────────\n"
    "Set step 6 → RUNNING. Clear checkpoint_request. Append CP1 decision to human_approvals.\n"
    "Write output/%(rid)s/06-sbpa-design.md with these sections:\n"
    "  1. PROCESS OVERVIEW — process name, trigger (CE_* Business Event or API call or Manual), one-para summary.\n"
    "  2. PROCESS STEPS — numbered list; for each step state:\n"
    "       Type: Automated Action | Human Task | Decision Gateway | Notification | Sub-process\n"
    "       Automated Action: SAP Build Action Library project name + action name.\n"
    "       Human Task: approver role, form fields (name/type/required), decisions available, deadline.\n"
    "       Decision Gateway: condition expression, all branches (including else/default).\n"
    "       Notification: recipient role, message content, channel (email/in-app).\n"
    "  3. S/4 INTEGRATION — trigger configuration (Business Event ID + filter), write-back APIs (released OData only), cross-ref with 03-release-verdicts.md.\n"
    "  4. CONTEXT VARIABLES — variable name, data type, source (trigger payload / S/4 API / user input).\n"
    "  5. ERROR HANDLING — what happens when each Automated Action fails; escalation path if Human Task times out.\n"
    "  6. NAMING CONTRACT — SBPA project name, process name, each artifact name (follow Naming Contract from CP1).\n"
    "  7. PREREQUISITES CHECKLIST —\n"
    "       [ ] SAP Build Process Automation subscription active in BTP subaccount\n"
    "       [ ] SAP Build Action Library enabled and required action projects available\n"
    "       [ ] Business Events enabled in S/4 tenant (if trigger = Business Event)\n"
    "       [ ] S/4 ↔ BTP connectivity configured (comm arrangement or Integration Suite)\n"
    "       [ ] Approver roles created and users assigned in SBPA\n"
    "Update run.json: step 6 → PASS, deliverables += '06-sbpa-design.md'.\n\n"
    "── STEP 7 · GATE 2: PROCESS DESIGN REVIEW + CHECKPOINT 2 ───────────────────\n"
    "Set step 7 → RUNNING (gate=true). Re-read 06-sbpa-design.md independently. Check:\n"
    "  • Every process path terminates — no orphaned or looping steps.\n"
    "  • Every Human Task has: specific approver role (not 'any user'), form fields, timeout, escalation.\n"
    "  • Every Decision Gateway has an exhaustive set of branches (else/default covered).\n"
    "  • S/4 trigger uses a released Business Event (CE_* prefix) or released OData API.\n"
    "  • Write-back to S/4 uses released APIs only — cross-check with 03-release-verdicts.md.\n"
    "  • No hardcoded user IDs, email addresses, or client-specific values.\n"
    "  • Error handling defined for every Automated Action.\n"
    "Verdict: SHIP (all covered) | FIX (gaps to close) | REDESIGN (structural issue).\n"
    "If FIX: update 06-sbpa-design.md before writing the checkpoint.\n"
    "Update run.json: step 7 → PASS (gate), gate_results entry:\n"
    '  {"name":"Process Design Review","status":"SHIP|FIX|REDESIGN","detail":"<one line>"}\n'
    "Write checkpoint_request:\n"
    "  checkpoint: 'CP2 · Code approval'  (reuse same checkpoint name — pipeline routing unchanged)\n"
    "  summary: <verdict + top 3 findings>\n"
    "  options: ['approve','adjust','reject']\n"
    "  material: '06-sbpa-design.md'\n"
    "  code_files: [{id:'PD-01', file:'06-sbpa-design.md', summary:'<one line>', material:'06-sbpa-design.md'}]\n"
    "Set status → awaiting_approval, step 7 → AWAITING_APPROVAL. THEN EXIT.\n"
)

_SBPA_PHASE_C_INSTRUCTIONS = (
    "SBPA DELIVERY MODE — Steps 8-11 use SBPA-adapted quality checks (no ABAP lint, no unit tests).\n\n"
    "FILES TO READ (in addition to those listed above):\n"
    "  output/%(rid)s/06-sbpa-design.md   (the approved process design)\n\n"
    "── STEP 8 · PROCESS DESIGN COMPLETENESS CHECK (replaces Lint) ──────────────\n"
    "Set step 8 → RUNNING. Re-read 06-sbpa-design.md. Tick each item:\n"
    "  [ ] All process paths terminate (no orphaned steps, no infinite loops)\n"
    "  [ ] All Human Tasks: approver role specific, form fields defined, timeout set, escalation path defined\n"
    "  [ ] All Decision Gateways: exhaustive branches including else/default\n"
    "  [ ] All Automated Actions: SAP Build Action Library project + action name specified\n"
    "  [ ] S/4 trigger identified and marked released/verified in 03-release-verdicts.md\n"
    "  [ ] Write-back APIs (if any) are released — cross-check with 03-release-verdicts.md\n"
    "  [ ] Context variables typed and sourced\n"
    "  [ ] Error handling defined for every Automated Action\n"
    "  [ ] No hardcoded user IDs, emails, or client-specific values\n"
    "Each unchecked item = one finding (severity: Critical if it blocks process, Major otherwise).\n"
    "Write output/%(rid)s/07-completeness-check.md (checklist + findings).\n"
    "Update run.json: step 8 → PASS or FAIL, findings[].\n\n"
    "── STEP 9 · INTEGRATION TEST SCENARIOS (replaces Unit Tests) ───────────────\n"
    "Set step 9 → RUNNING. Write output/%(rid)s/08-test-scenarios.md.\n"
    "MANDATORY scenarios — write one numbered scenario for each:\n"
    "  1. Happy path — trigger fires, all automated actions succeed, all Human Tasks approved, S/4 updated.\n"
    "  2. Rejection path — Human Task rejected; process terminates gracefully; S/4 not incorrectly updated.\n"
    "  3. Timeout/escalation — Human Task deadline exceeded; escalation notification sent; escalation approver acts.\n"
    "  4. Action failure — an Automated Action (SAP Build Action) fails; error handling path executes; notification sent.\n"
    "  5. Re-trigger guard — process triggered twice for same object; de-duplication behavior described.\n"
    "For each scenario: Scenario ID | Trigger payload | Steps to execute | Expected outcome | How to verify in S/4.\n"
    "Update run.json: step 9 → PASS, deliverables += '08-test-scenarios.md'.\n\n"
    "── STEP 10 · TECHNICAL DESIGN ──────────────────────────────────────────────\n"
    "Set step 10 → RUNNING. Write output/%(rid)s/05-technical-design.md:\n"
    "  • SBPA project name, process name (from Naming Contract).\n"
    "  • Trigger: Business Event ID + filter, or API endpoint + payload schema.\n"
    "  • Action Library projects used: name, version, actions invoked.\n"
    "  • Context variable table: name | type | source | used in step.\n"
    "  • S/4 integration: connectivity method (comm arrangement / Integration Suite), auth type.\n"
    "  • Security: process visibility (who sees running instances), data sensitivity classification.\n"
    "  • SAP Discovery Center link: https://discovery-center.cloud.sap/serviceCatalog/sap-build-process-automation — pricing metric: active users.\n"
    "  • Tenant verification checklist: one item per NOT_VERIFIED object from 03-release-verdicts.md.\n"
    "Update run.json: step 10 → PASS, deliverables += '05-technical-design.md'.\n\n"
    "── STEP 11 · GATE 3: PEER REVIEW (Challenger) ──────────────────────────────\n"
    "Set step 11 → RUNNING (gate=true). Review 06-sbpa-design.md, 08-test-scenarios.md, 05-technical-design.md.\n"
    "Check:\n"
    "  • All 5 mandatory test scenarios present and realistic.\n"
    "  • Process design complete (no gaps from step 8 check left open without justification).\n"
    "  • TD matches design (same steps, same variables, same integrations).\n"
    "  • Released objects only on S/4 side.\n"
    "  • No hardcoded values.\n"
    "Compute quality_score:\n"
    "  Start 100. Deduct: Critical open ×15, Major open ×10, Minor open ×5,\n"
    "  Missing mandatory test scenario ×10 each, No escalation paths ×15, No error handling ×20.\n"
    "Write output/%(rid)s/09-review.md.\n"
    "Update run.json: step 11 → PASS, findings[], quality_score, gates_passed.\n"
    'gate_results entry: {"name":"Peer Review","status":"PASS|CONDITIONAL_PASS|FAIL","detail":"<one line>"}\n'
)

_SBPA_PHASE_D_INSTRUCTIONS = (
    "SBPA DELIVERY MODE — Package produces a Configuration Handover Guide. NO automated deploy.\n\n"
    "── STEP 12 · PACKAGE — CONFIGURATION HANDOVER GUIDE ────────────────────────\n"
    "Set step 12 → RUNNING.\n"
    "Write output/%(rid)s/10-handover-guide.md with these sections:\n\n"
    "  SECTION 1 — WHAT THIS AUTOMATION DOES (2-3 sentences, plain English)\n\n"
    "  SECTION 2 — PREREQUISITES (complete BEFORE opening SBPA)\n"
    "  Copy the prerequisites checklist from 06-sbpa-design.md Section 7.\n"
    "  Add any items identified during peer review.\n\n"
    "  SECTION 3 — S/4 SIDE CONFIGURATION (do this first)\n"
    "  Numbered steps to enable the Business Event or API trigger in the S/4 tenant.\n"
    "  Each step: what to click, what to enter, how to verify it worked.\n\n"
    "  SECTION 4 — SBPA PROJECT SETUP\n"
    "  Numbered steps to create and configure the SBPA project:\n"
    "  Step 1: Log in to SAP Build Process Automation.\n"
    "  Step 2: Create new project — name: <exact name from Naming Contract>.\n"
    "  Step 3: Connect Action Library projects (name each one).\n"
    "  Steps 4+: Configure each process step from 06-sbpa-design.md in order.\n"
    "  (Write enough detail that a consultant who has never seen the FD can follow it.)\n\n"
    "  SECTION 5 — TESTING\n"
    "  Reference 08-test-scenarios.md. For each scenario: how to trigger it, what to verify.\n\n"
    "  SECTION 6 — SIGN-OFF CHECKLIST\n"
    "  [ ] All 5 test scenarios executed and passed\n"
    "  [ ] Business event fires correctly from S/4\n"
    "  [ ] Human task forms display and submit correctly\n"
    "  [ ] Escalation path triggered and notifications received\n"
    "  [ ] S/4 data updated correctly (if write-back exists)\n"
    "  [ ] Process visible only to authorised users\n\n"
    "  SECTION 7 — DELIVERABLES INDEX\n"
    "  List all output files with one-line description each.\n\n"
    "Call record_experience for anything non-obvious this SBPA run taught.\n"
    "Update run.json: step 12 → PASS, deliverables += '10-handover-guide.md',\n"
    "  status → 'completed', gates_passed → '3/3'.\n"
    "NOTE: SBPA has no automated BTP deploy. Pipeline is COMPLETE after step 12.\n"
    "Do NOT add steps 13 or 14. THEN EXIT.\n"
)

_CATALOG_FALLBACK = (
    "CATALOG FALLBACK (use when MCP tools are unavailable — read each file ONCE, "
    "evaluate all objects in-context; do NOT call the MCP server CLI repeatedly).\n"
    "NOTE: the live catalog is catalog.db (SQLite); the JSON files below are seed snapshots "
    "and may be stale if sync_hub.py has been run since initial setup.\n"
    "  mcp-server/catalog/released_cds_views.json\n"
    "  mcp-server/catalog/released_badis.json\n"
    "  mcp-server/catalog/released_apis.json\n"
    "  mcp-server/catalog/forbidden_patterns.json\n"
    "Verdict rules: forbidden_patterns hit → NOT_AVAILABLE (blocker); in a catalog "
    "OR matches released naming (CDS I_/C_/A_/R_/E_, API API_*/*_SRV/CE_*) → "
    "LIKELY_RELEASED; otherwise → NOT_VERIFIED (look up on authoritative list, never "
    "mark as unreleased). Standard CDS views (I_MaterialStock, I_Product, …) are always "
    "LIKELY_RELEASED — never downgrade them."
)

def _phase_a_prompt(fd_path, rid):
    """Steps 1-4 + stop at CP1. Fresh run — only the FD and the seeded run skeleton."""
    return (
        "HEADLESS PIPELINE — Phase A (Steps 1-4 + Checkpoint 1) for run %(rid)s.\n\n"
        "Execute steps 1-4 of the RICEFW pipeline for the FD at %(fd)s, then STOP at CP1.\n"
        "Do NOT read .claude/skills/s4pc-ricefw-pipeline/SKILL.md — this prompt replaces it.\n"
        "CLAUDE.md platform rules apply in full (clean core, released objects only, no BAPIs).\n\n"
        "FILES TO READ (nothing else):\n"
        "  %(fd)s                     (read first)\n"
        "  output/%(rid)s/run.json    (seeded skeleton — update it, keep the same id '%(rid)s')\n\n"
        "%(plain_english)s\n\n"
        "%(catalog)s\n\n"
        "── STEP 1 · INTAKE (Delivery Lead) ──────────────────────────────────────────\n"
        "Set run.json steps[0].status → RUNNING. Then:\n"
        "  • Restate FD scope in ≤10 lines.\n"
        "  • Classify RICEFW type: Report | Interface | Conversion | Enhancement | Form | Workflow\n"
        "  • List open questions for the human (do NOT silently answer them).\n"
        "Write output/%(rid)s/01-discovery.md\n"
        "Update run.json: step 1 → PASS, run.json 'type' → classified type.\n\n"
        "── STEP 2 · SOLUTION PROPOSAL (Extensibility Architect) ─────────────────────\n"
        "Set step 2 → RUNNING. Then:\n"
        "  • Call query_experience for similar past runs (cite EXP-ids in the proposal).\n"
        "  • Call extensibility_advisor for a first-pass recommendation.\n"
        "  • Decompose FD into capabilities; pick mode per capability\n"
        "    (key_user | developer | side_by_side — mixed is normal).\n"
        "  • Feasibility/Approach/Cost rating table for ALL options (runners-up included).\n"
        "    BTP services: include Discovery Center link + pricing metric.\n"
        "  • MANDATORY when solution creates custom objects: Custom-Object Naming Contract\n"
        "    (columns: id, object, type, created_in, suggested technical name with Z/Y/YY1_ namespace).\n"
        "Write output/%(rid)s/02-solution-proposal.md\n"
        "Update run.json: step 2 → PASS, mode_split filled.\n\n"
        "── STEP 3 · OBJECT INVENTORY (Extensibility Architect) ──────────────────────\n"
        "Set step 3 → RUNNING. Then:\n"
        "  • Check EVERY API / BAdI / CDS view / table via check_object_release_state\n"
        "    (or catalog fallback above).\n"
        "  • NOT_AVAILABLE → redesign (BAPI, classical table, enhancement point, Smart Form).\n"
        "  • LIKELY_RELEASED → released; add 'confirm on authoritative source' note.\n"
        "  • NOT_VERIFIED → look up on authoritative list for its type; never mark as unreleased.\n"
        "  Authoritative URLs: CDS → SAP Help Released CDS Views list;\n"
        "    BAdIs → SAP Help List of BAdIs; APIs → api.sap.com.\n"
        "Write output/%(rid)s/03-release-verdicts.md\n"
        "Update run.json: step 3 → PASS.\n\n"
        "── STEP 4 · GATE 1: RELEASE CHECK (Clean-Core Reviewer — independent re-check) ──\n"
        "Set step 4 → RUNNING (gate=true). Re-derive every verdict from scratch — no rubber-stamping.\n"
        "  • NOT_AVAILABLE → gate FAILS → redesign required before proceeding.\n"
        "  • LIKELY_RELEASED / NOT_VERIFIED → CONDITIONAL_PASS; each gets a tenant-verification line.\n"
        "Update run.json: step 4 → PASS or FAIL, gates_passed.\n"
        "gate_results entry schema (use exact field names):\n"
        '  {"name":"Release Check","status":"PASS|CONDITIONAL_PASS|FAIL","detail":"<one-line summary>"}\n\n'
        "── CHECKPOINT 1 — STOP ──────────────────────────────────────────────────────\n"
        "Write to output/%(rid)s/run.json:\n"
        '  "status": "awaiting_approval"\n'
        "  steps where n=5: status → AWAITING_APPROVAL\n"
        '  "checkpoint_request": {\n'
        '    "checkpoint": "CP1 · Solution approval",\n'
        '    "summary": "<≤5 lines: which approach is recommended and why, key open questions>",\n'
        '    "options": ["approve", "adjust", "reject"],\n'
        '    "material": "02-solution-proposal.md",\n'
        '    "approach_options": [    ← MANDATORY — one entry per approach considered (all of them)\n'
        '      {\n'
        '        "id": "A",\n'
        '        "label": "<short label e.g. Developer Extensibility (RAP + Fiori Elements)>",\n'
        '        "mode": "<key_user|developer|side_by_side|mixed>",\n'
        '        "is_btp": false,\n'
        '        "is_sbpa": false,\n'
        '        "recommended": true,\n'
        '        "mandated": false,\n'
        '        "feasible": true,\n'
        '        "summary": "<2-3 sentences describing this approach>",\n'
        '        "pros": ["<pro 1>", "<pro 2>"],\n'
        '        "cons": ["<con 1>", "<con 2>"],\n'
        '        "why_not": null,\n'
        '        "naming_contract": [ {"id":"NCA-01","object":"<obj>","type":"<type>","created_in":"<key_user|developer|side_by_side>","name":"<Z.../YY1_.../CAP name>"} ]\n'
        '      },\n'
        '      {\n'
        '        "id": "B",\n'
        '        "label": "<label for option B>",\n'
        '        "mode": "<mode>",\n'
        '        "is_btp": true,\n'
        '        "is_sbpa": false,\n'
        '        "recommended": false,\n'
        '        "mandated": false,\n'
        '        "feasible": true,\n'
        '        "summary": "<description>",\n'
        '        "pros": ["..."],\n'
        '        "cons": ["..."],\n'
        '        "why_not": "<why this was not recommended>",\n'
        '        "naming_contract": [ {"id":"NCB-01","object":"<obj>","type":"<type>","created_in":"side_by_side","name":"<CAP entity/service/destination name>"} ]\n'
        '      }\n'
        "    ],\n"
        "SBPA GUIDANCE: If the FD involves workflow automation, approval chains, or process orchestration,\n"
        "include an SBPA approach option with mode='sbpa', is_btp=true, is_sbpa=true.\n"
        "SBPA is a BTP service (SAP Discovery Center: sap-build-process-automation).\n"
        "SBPA produces configuration documentation (not deployable code) — state this clearly in pros/cons.\n"
        "CLIENT-MANDATED MODE: Read any 'Client Constraints' section in the FD. If the client mandates an\n"
        "  extensibility mode (e.g. 'side-by-side / BTP only', even when it is not the clean-core best fit):\n"
        "    • Keep your HONEST technical recommendation — set recommended=true on the best-fit option so\n"
        "      the human sees the trade-off. Do NOT silently rewrite the recommendation to match the mandate.\n"
        "    • Make the mandated mode a BUILD-READY option: set mandated=true on it, give it a full summary,\n"
        "      real pros/cons, the released objects it uses, and — if BTP — the SAP Discovery Center link +\n"
        "      pricing metric in its cons. It must be genuinely selectable and buildable, never a stub.\n"
        "    • Feasibility is the only hard gate: if the mandated mode truly cannot be built with released\n"
        "      artifacts, set feasible=false and why_not=<the concrete blocker>, and say so in the summary —\n"
        "      a client mandate cannot override platform reality.\n"
        "    • Author the naming_contract (below) for the MANDATED option so a mandated build is name-locked\n"
        "      from the start; if no mode is mandated, author it for the recommended option.\n"
        "  If NO client constraint is present, set mandated=false + feasible=true on every option normally.\n"
        "PER-APPROACH NAMING (REQUIRED): give EACH build-viable option its own naming contract in TWO places,\n"
        "  with identical objects/names: (1) structured in that option's approach_options[i].naming_contract\n"
        "  (shown above), and (2) as a sub-table in 02-solution-proposal.md. Mode-appropriate objects: Z/Y\n"
        "  CDS+RAP for developer, YY1_ fields for key user, CAP entities/services/destinations for\n"
        "  side-by-side. The webapp swaps the editable name grid to the SELECTED option's contract, so a\n"
        "  client-mandated override (e.g. BTP over RAP) never shows the other mode's names.\n"
        '    "naming_contract": [  ← TOP-LEVEL mirror: copy the DEFAULT option\'s contract here (the mandated\n'
        "                             option if one is mandated, else the recommended one) for initial display\n"
        '      {"id":"NC-01","object":"<name>","type":"<type>","created_in":"<key_user|developer|side_by_side>","name":"<Z.../YY1_.../CAP name>"}\n'
        "    ]\n"
        "  }\n"
        "The developer will SELECT one approach from approach_options — Build (Phase B) uses the selected one,\n"
        "NOT necessarily the recommended one. A mandated option (mandated=true) is pre-selected in the UI.\n"
        "is_btp=true on the selected approach triggers BTP deploy steps 13-14 in Phase D.\n"
        "Set run.json.workflow to 'RICEFW Pipeline (14 steps, incl. BTP deploy)' (ASCII only, no smart quotes)\n"
        "  if the DEFAULT-SELECTED option has is_btp=true — that is the mandated option when mandated=true,\n"
        "  otherwise the recommended one. Otherwise keep 'RICEFW Pipeline (12 steps)'. The label is\n"
        "  re-confirmed from the actually-selected approach in Phase D, so a review-time override still works.\n"
        "THEN EXIT. Do NOT continue to step 6.\n"
    ) % {"rid": rid, "fd": fd_path, "catalog": _CATALOG_FALLBACK, "plain_english": _PLAIN_ENGLISH_RULE + _FINDINGS_SCHEMA}


def _phase_b_prompt(rid, fd_path, decision, notes, fc_txt, cp1_slug,
                    selected_approach="", selected_approach_label="",
                    selected_is_btp=False, selected_is_sbpa=False):
    """Steps 6-7 + stop at CP2. Starts from the approved solution proposal."""
    _header = (
        "HEADLESS PIPELINE — Phase B (Steps 6-7 + Checkpoint 2) for run %(rid)s.\n\n"
        "Checkpoint 1 ('CP1 · Solution approval') was %(decision)s by the developer.\n"
        "Developer notes: %(notes)s\n"
        "%(fc_txt)s\n"
        "Do NOT read SKILL.md — this prompt replaces it for this phase.\n"
        "CLAUDE.md platform rules apply in full.\n\n"
        "FILES TO READ (nothing else):\n"
        "  output/%(rid)s/run.json\n"
        "  output/%(rid)s/02-solution-proposal.md   (approved proposal + naming contract)\n"
        "  output/%(rid)s/03-release-verdicts.md     (released objects to use)\n"
        "  output/%(rid)s/decisions/%(cp1_slug)s.json  (CP1 decision + notes)\n"
        "  %(fd)s                                    (requirements reference only)\n\n"
        "%(plain_english)s\n\n"
        "Selected approach: %(sel_label)s [id=%(sel_id)s, is_btp=%(is_btp)s, is_sbpa=%(is_sbpa)s]\n"
        "Build ONLY this approach — ignore the other options from the solution proposal.\n\n"
    ) % {"rid": rid, "fd": fd_path, "decision": decision, "notes": notes or "—",
         "fc_txt": fc_txt, "cp1_slug": cp1_slug,
         "sel_id": selected_approach or "recommended",
         "sel_label": selected_approach_label or "as recommended in proposal",
         "is_btp": str(selected_is_btp).lower(),
         "is_sbpa": str(selected_is_sbpa).lower(),
         "plain_english": _PLAIN_ENGLISH_RULE + _FINDINGS_SCHEMA}
    if selected_is_sbpa:
        return _header + (_SBPA_PHASE_B_INSTRUCTIONS % {"rid": rid})
    return _header + (
        "── STEP 6 · BUILD (Developer) ───────────────────────────────────────────────\n"
        "Set step 6 → RUNNING. Then:\n"
        "  • Append the CP1 decision to run.json.human_approvals if not already present.\n"
        "  • Clear run.json.checkpoint_request.\n"
        "  • Build per the approved proposal — use EXACT object names from the Naming Contract.\n"
        "    Developer mode: RAP / CDS / released BAdIs — ABAP for Cloud Development only.\n"
        "    Key user mode: Custom Fields & Logic config steps, BAdI implementation outline.\n"
        "    Side-by-side: CAP Node.js + SAPUI5 (follow cap.cloud.sap, ui5.sap.com docs).\n"
        "  • NAMING SOURCE OF TRUTH: the decision file decisions/%(cp1_slug)s.json carries the human-locked\n"
        "    naming_contract for the SELECTED approach — if present and non-empty, build against THOSE exact\n"
        "    names verbatim. Only if it is absent, fall back to the SELECTED approach's sub-table in\n"
        "    02-solution-proposal.md. Never mix names across modes (e.g. RAP Z-names on a BTP build).\n"
        "  • Apply any 'adjust' notes from the developer verbatim.\n"
        "Write output/%(rid)s/06-build.md\n"
        "Update run.json: step 6 → PASS, deliverables += '06-build.md'.\n\n"
        "── STEP 7 · GATE 2: CODE REVIEW + CHECKPOINT 2 (Clean-Core Reviewer) ────────\n"
        "Set step 7 → RUNNING (gate=true). Re-open 06-build.md with fresh eyes. Check:\n"
        "  • Released objects only (no classical ABAP, no BAPIs, no enhancement points).\n"
        "  • Authorization checks present; no hardcoded secrets; input validation at boundaries.\n"
        "  • Upgrade-safety (no deprecated APIs, no implicit dependencies on internal tables).\n"
        "Verdict: SHIP | FIX | REDESIGN with specific findings.\n"
        "If FIX: apply fixes to 06-build.md before writing the checkpoint request.\n"
        "Record verdict in run.json findings[].\n"
        "Write output/%(rid)s/07-gate2-review.md — list every finding with: ID | Severity | Description | Recommended fix.\n"
        "Update run.json: step 7 → PASS (gate), gates_passed.\n"
        "gate_results entry schema (use exact field names):\n"
        '  {"name":"Code Review","status":"SHIP|FIX|REDESIGN","detail":"<one-line verdict summary>"}\n\n'
        "Then write to output/%(rid)s/run.json:\n"
        '  "status": "awaiting_approval"\n'
        "  steps where n=7: status → AWAITING_APPROVAL\n"
        '  "checkpoint_request": {\n'
        '    "checkpoint": "CP2 · Code approval",\n'
        '    "summary": "<gate verdict, key review findings for the developer>",\n'
        '    "options": ["approve", "adjust", "reject"],\n'
        '    "material": "06-build.md",\n'
        '    "code_files": [  ← one entry per file / section in 06-build.md\n'
        '      {"id":"CF-01","file":"<file or section name>","summary":"<one line>","material":"06-build.md"}\n'
        "    ]\n"
        "  }\n"
        "THEN EXIT. Do NOT continue to step 8.\n"
    ) % {"rid": rid, "cp1_slug": cp1_slug}


def _phase_c_prompt(rid, fd_path, decision, notes, fc_txt, cp2_slug, is_sbpa=False):
    """Steps 8-11 + stop at CP3. Starts from the approved code (or SBPA process design)."""
    if is_sbpa:
        _fmt = {"rid": rid, "fd": fd_path, "decision": decision, "notes": notes or "—",
                "fc_txt": fc_txt, "cp2_slug": cp2_slug, "catalog": _CATALOG_FALLBACK,
                "plain_english": _PLAIN_ENGLISH_RULE + _FINDINGS_SCHEMA}
        _header = (
            "HEADLESS PIPELINE — Phase C (Steps 8-11 + Checkpoint 3) for run %(rid)s.\n\n"
            "Checkpoint 2 ('CP2 · Code approval') was %(decision)s by the developer.\n"
            "Developer notes: %(notes)s\n"
            "%(fc_txt)s\n"
            "Do NOT read SKILL.md — this prompt replaces it.\n"
            "CLAUDE.md platform rules apply in full.\n\n"
            "FILES TO READ (nothing else):\n"
            "  output/%(rid)s/run.json\n"
            "  output/%(rid)s/06-sbpa-design.md       (the approved process design)\n"
            "  output/%(rid)s/02-solution-proposal.md   (for TD context — mode split, objects)\n"
            "  output/%(rid)s/03-release-verdicts.md    (for integration object verification)\n"
            "  output/%(rid)s/decisions/%(cp2_slug)s.json\n"
            "  %(fd)s                                   (for test ACs + TD requirements)\n\n"
            "%(plain_english)s\n\n"
            "Start by appending the CP2 decision to run.json.human_approvals and clearing checkpoint_request.\n\n"
            "%(catalog)s\n\n"
        ) % _fmt
        _sbpa_steps = _SBPA_PHASE_C_INSTRUCTIONS % {"rid": rid}
        _cp3 = (
            "── CHECKPOINT 3 / QUALITY GATE ──────────────────────────────────────────────\n"
            "Compute quality_score first (formula: 100 minus Critical-resolved×5, Major-open×8,\n"
            "Major-resolved×2, Minor-open×3, Minor-resolved×1; floor 0).\n\n"
            "Count open_critical = findings where severity=Critical AND status=Open.\n"
            "Count open_major   = findings where severity=Major   AND status=Open.\n\n"
            "RULE — findings review checkpoint fires if ANY of these is true:\n"
            "  • open_critical > 0  (Critical findings ALWAYS block — no exceptions)\n"
            "  • open_major > 0     (Major findings ALWAYS require developer sign-off)\n"
            "  • quality_score < 70 (score too low regardless of severity mix)\n\n"
            "IF none of the above (open_critical=0 AND open_major=0 AND score≥70):\n"
            "  Write standard CP3 to output/%(rid)s/run.json:\n"
            "  status → awaiting_approval, step 11 → AWAITING_APPROVAL\n"
            '  checkpoint_request: {"checkpoint":"CP3 · Acceptance","summary":"<verdict + score>",\n'
            '  "options":["approve","adjust","reject"],"material":"09-review.md"}\n'
            "  THEN EXIT. Do NOT continue to step 12.\n\n"
            "IF findings review fires:\n"
            "  Write a FINDINGS REVIEW checkpoint to output/%(rid)s/run.json:\n"
            "  status → awaiting_approval, step 11 → AWAITING_APPROVAL\n"
            '  checkpoint_request: {\n'
            '    "checkpoint": "CP3 · Findings Review (quality score too low)",\n'
            '    "summary": "Quality score: <X>/100. Open Critical: <n>. Open Major: <n>. Each Critical and Major finding requires a decision (fix or accept) before the pipeline can proceed. Minor findings are advisory.",\n'
            '    "options": ["approve","adjust","reject"],\n'
            '    "material": "09-review.md",\n'
            '    "findings_review": [\n'
            '      {\n'
            '        "id": "F-01",\n'
            '        "severity": "Critical|Major|Minor",\n'
            '        "what_is_wrong": "<plain English: what the problem is>",\n'
            '        "what_to_do": "<numbered steps the developer must take to fix it>",\n'
            '        "how_to_verify": "<how to confirm it is fixed>",\n'
            '        "action": null\n'
            '      }\n'
            '    ]\n'
            "  }\n"
            "  THEN EXIT. Do NOT continue to step 12.\n"
        ) % {"rid": rid}
        return _header + _sbpa_steps + _cp3
    return (
        "HEADLESS PIPELINE — Phase C (Steps 8-11 + Checkpoint 3) for run %(rid)s.\n\n"
        "Checkpoint 2 ('CP2 · Code approval') was %(decision)s by the developer.\n"
        "Developer notes: %(notes)s\n"
        "%(fc_txt)s\n"
        "Do NOT read SKILL.md — this prompt replaces it.\n"
        "CLAUDE.md platform rules apply in full.\n\n"
        "FILES TO READ (nothing else):\n"
        "  output/%(rid)s/run.json\n"
        "  output/%(rid)s/06-build.md  OR  output/%(rid)s/06-code.md  (the Step 6 build output — read whichever exists)\n"
        "  output/%(rid)s/02-solution-proposal.md   (for TD context — mode split, objects)\n"
        "  output/%(rid)s/decisions/%(cp2_slug)s.json\n"
        "  %(fd)s                                   (for test ACs + TD requirements)\n\n"
        "%(plain_english)s\n\n"
        "Start by appending the CP2 decision to run.json.human_approvals and clearing checkpoint_request.\n\n"
        "%(catalog)s\n\n"
        "── STEP 7B · APPLY CP2 CORRECTIONS (Developer) ─────────────────────────────\n"
        "Locate the Step 6 build output: check for output/%(rid)s/06-build.md first;\n"
        "if that file does not exist, use output/%(rid)s/06-code.md instead.\n"
        "Read that file plus every file comment from the CP2 decision\n"
        "(listed in %(fc_txt)s above). Apply each comment as a targeted correction to\n"
        "the relevant artifact.\n"
        "Write output/%(rid)s/06-build-corrected.md — this is the CANONICAL code\n"
        "reference for all subsequent steps (8-11 and deploy).\n"
        "If there are no CP2 file comments: write 06-build-corrected.md as a verbatim\n"
        "copy of the build output file, prepending this one-line header comment:\n"
        "  <!-- Step 7B: no corrections required by CP2 reviewer — code unchanged -->\n"
        "Update run.json deliverables: append {\"step\":\"7B\",\"file\":\"06-build-corrected.md\",\"status\":\"done\"}.\n\n"
        "── STEP 8 · LINT (Clean-Core Reviewer) ──────────────────────────────────────\n"
        "Set step 8 → RUNNING. Then:\n"
        "  • Run abap_cloud_lint on every ABAP artifact in 06-build-corrected.md.\n"
        "    If no ABAP (e.g. pure side-by-side): write a brief 'no ABAP artifacts — not applicable' note.\n"
        "  • On FAIL: fix and re-lint (max 3 rounds; on 3rd FAIL log as open Critical finding).\n"
        "Write output/%(rid)s/07-lint-report.md\n"
        "Update run.json: step 8 → PASS or FAIL, findings[].\n\n"
        "── STEP 9 · UNIT TEST DESIGN (Test Agent) ────────────────────────────────────\n"
        "Set step 9 → RUNNING. Then:\n"
        "  • ABAP Unit test classes (developer mode) or API test scripts (side-by-side mode).\n"
        "  • Include negative tests and edge cases. Map tests to FD acceptance criteria.\n"
        "Write output/%(rid)s/09-unit-tests.md\n"
        "Update run.json: step 9 → PASS.\n\n"
        "── STEP 10 · FD ANALYSIS + TECHNICAL DESIGN (Extensibility Architect + Developer) ──\n"
        "Set step 10 → RUNNING. Read: FD, 06-build-corrected.md, 09-unit-tests.md, 02-solution-proposal.md, 03-release-verdicts.md.\n\n"
        "Write output/%(rid)s/10-technical-design.md — a COMPLETE, PUBLISHABLE technical design document.\n"
        "Include as the first section of the document a requirement traceability matrix:\n"
        "  One row per FD acceptance criterion → the object/method that satisfies it → pass/fail/open.\n\n"
        "The document MUST contain every section below.  Do not skip or merge sections.\n\n"
        "  ## 0. Document Header\n"
        "     RICEFW type | Run ID | FD source | Extensibility mode | Version | Date | Status\n\n"
        "  ## 1. Executive Summary  (3-5 sentences: business problem, solution approach, key constraints)\n\n"
        "  ## 2. Scope\n"
        "     In scope: list each capability with its extensibility mode.\n"
        "     Out of scope: anything explicitly deferred or excluded in the FD.\n\n"
        "  ## 3. Architecture Overview\n"
        "     Full ASCII diagram showing all custom objects, their connections, and the released SAP\n"
        "     objects/APIs they consume.  Label every arrow with the protocol (OData V4, BAdI, SQL join, etc.).\n\n"
        "  ## 4. Custom-Object Naming Contract\n"
        "     Table: NC-ID | Technical Name | Object Type | Created in (key_user/developer) | Description.\n"
        "     These are the verbatim names locked at CP1.\n\n"
        "  ## 5. Detailed Object Specifications\n"
        "     One sub-section per custom object (NC-01, NC-02, …).  Content depends on type:\n"
        "     • CDS Interface/Consumption View: full DDL excerpt (define view entity … select from … association … where … annotated key fields); list every exposed field with technical name, data type, and label.\n"
        "     • RAP Business Object / BDEF: entity block with keys, all associations, all action/function signatures (name, parameter names + types, result type); managed/unmanaged decision.\n"
        "     • ABAP Class (RAP behaviour): each method that implements a BDEF operation — signature, short description of logic, exception raised.\n"
        "     • ABAP Class (BAdI implementation): the BAdI interface name, the implemented method, parameter in/out types, logic summary, exception raised on abort.\n"
        "     • Custom DDIC Table: every field (field name, data element or type, key flag, short description); index fields if any.\n"
        "     • Service Definition / Binding: entity name mapped, binding type (OData V4/UI), endpoint path pattern.\n"
        "     • BAdI Enhancement Implementation: BAdI definition name, filter values if any, activation path in Custom Logic app.\n\n"
        "  ## 6. Released Objects — Inventory and Verdicts\n"
        "     Table: Object | Type | Verdict | Authoritative source URL.\n"
        "     Must cover every released SAP object consumed (CDS views, APIs, classes, BAdIs).\n\n"
        "  ## 7. Integration / API Specifications\n"
        "     One sub-section per external API or BAdI integration point:\n"
        "     • API: service name, communication scenario, key entity/operation used, request fields sent, response fields consumed, error handling on API failure.\n"
        "     • BAdI: BAdI definition name (or TODO if NOT_VERIFIED), interface + method, trigger event, fields received as parameters, action on mismatch.\n\n"
        "  ## 8. Data Flow Narrative\n"
        "     Numbered step-by-step table (Actor | Action) for the main user journey AND each secondary flow.\n\n"
        "  ## 9. Error Handling and Message Catalog\n"
        "     Table: Message ID | Class | Number | Text | Severity | Raised by | User action.\n"
        "     Include every message defined in the message class and every cx_ exception raised.\n\n"
        "  ## 10. Authorisation Concept\n"
        "      Table: Object/Feature | Auth approach | Recommended auth object | DCL predicate (if applicable).\n\n"
        "  ## 11. Non-Functional Requirements\n"
        "      Table: Concern (Performance/Scalability/Upgrade-safety/Error-handling/Idempotency) | Design decision.\n\n"
        "  ## 12. Unit Test Coverage Summary\n"
        "      Table: Test class | Method | AC covered | Positive/Negative | Test double used.\n"
        "      Source: 09-unit-tests.md — summarise, do not copy verbatim.\n\n"
        "  ## 13. Transport Plan\n"
        "      Table: Activation order | Object | Type | Layer.\n"
        "      Pre-transport checklist (each item must be ticked before QAS transport).\n\n"
        "  ## 14. Tenant Verification Checklist\n"
        "      Grouped by category (CDS Views / APIs / BAdIs / ABAP / Configuration / Fiori).\n"
        "      Each item: [ ] description — why it must be verified — how to verify.\n"
        "      Every NOT_VERIFIED object and every TODO from 06-build-corrected.md MUST appear here.\n\n"
        "  ## 15. Open Items and Go-Live Blockers\n"
        "      Table: ID | Severity | Description | Owner | Resolution path.\n"
        "      Source: findings[] in run.json and 08-lint.md (Step 11 peer-review findings not yet available — note 'pending peer review' and leave a placeholder row).\n\n"
        "  ## 16. Document Revision History\n"
        "      Table: Version | Date | Author (agent role) | Change summary.\n\n"
        "  ## 17. Approval\n"
        "      Table: Role | Name | Signature | Date  (leave Name/Signature/Date blank for human completion).\n"
        "      Rows: Technical Lead | Functional Lead | Quality Reviewer.\n\n"
        "Update run.json: step 10 → PASS.\n\n"
        "── STEP 11 · GATE 3: PEER REVIEW + CHALLENGER (Challenger — independent re-check) ──\n"
        "Set step 11 → RUNNING (gate=true). Full checklist over code, tests, AND TD:\n"
        "  • Clean core (released objects, no classical patterns, upgrade-safe).\n"
        "  • Security (auth, secret hygiene, input validation at boundaries).\n"
        "  • Test coverage (ACs mapped, negative tests present).\n"
        "  • TD accuracy (matches what was actually built).\n"
        "Verdict: SHIP | FIX | REDESIGN with specific findings. Compute quality_score:\n"
        "  100 − (Critical resolved×5, Major open×8, Major resolved×2, Minor open×3, Minor resolved×1).\n"
        "Write output/%(rid)s/11-gate3-review.md\n"
        "Update run.json: step 11 → PASS, findings[], quality_score, gates_passed.\n"
        "gate_results entry schema: {\"name\":\"Peer Review\",\"status\":\"PASS|CONDITIONAL_PASS|FAIL\",\"detail\":\"<one line>\"}\n\n"
        "── CHECKPOINT 3 / QUALITY GATE ──────────────────────────────────────────────\n"
        "Compute quality_score (formula: 100 minus Critical-resolved×5, Major-open×8,\n"
        "Major-resolved×2, Minor-open×3, Minor-resolved×1; floor 0).\n\n"
        "Count open_critical = findings where severity=Critical AND status=Open.\n"
        "Count open_major   = findings where severity=Major   AND status=Open.\n\n"
        "RULE — findings review checkpoint fires if ANY of these is true:\n"
        "  • open_critical > 0  (Critical findings ALWAYS block — no exceptions)\n"
        "  • open_major > 0     (Major findings ALWAYS require developer sign-off)\n"
        "  • quality_score < 70 (score too low regardless of severity mix)\n\n"
        "IF none of the above (open_critical=0 AND open_major=0 AND score≥70):\n"
        "  Write standard CP3:\n"
        "  status → awaiting_approval, step 11 → AWAITING_APPROVAL\n"
        '  checkpoint_request: {"checkpoint":"CP3 · Acceptance","summary":"<verdict + score, note any open Minor findings>",\n'
        '  "options":["approve","adjust","reject"],"material":"11-gate3-review.md"}\n'
        "  THEN EXIT.\n\n"
        "IF findings review fires:\n"
        "  Write findings review checkpoint to output/%(rid)s/run.json:\n"
        "  status → awaiting_approval, step 11 → AWAITING_APPROVAL\n"
        '  checkpoint_request: {\n'
        '    "checkpoint": "CP3 · Findings Review (quality score too low)",\n'
        '    "summary": "Quality score: <X>/100. Open Critical: <n>. Open Major: <n>. Each Critical and Major finding requires a decision (fix or accept) before the pipeline can proceed. Minor findings are advisory.",\n'
        '    "options": ["approve","adjust","reject"],\n'
        '    "material": "11-gate3-review.md",\n'
        '    "findings_review": [   ← include ALL open Critical + Major findings; include open Minor as advisory\n'
        '      {\n'
        '        "id": "F-01",\n'
        '        "severity": "Critical|Major|Minor",\n'
        '        "what_is_wrong": "<plain English — one sentence>",\n'
        '        "what_to_do": "<numbered steps to fix>",\n'
        '        "how_to_verify": "<one sentence>",\n'
        '        "action": null\n'
        '      }\n'
        '    ]\n'
        "  }\n"
        "  THEN EXIT. Do NOT continue to step 12.\n"
    ) % {"rid": rid, "fd": fd_path, "decision": decision, "notes": notes or "—",
         "fc_txt": fc_txt, "cp2_slug": cp2_slug, "catalog": _CATALOG_FALLBACK,
         "plain_english": _PLAIN_ENGLISH_RULE + _FINDINGS_SCHEMA}


def _phase_d_prompt(rid, fd_path, decision, notes, cp3_slug, selected_is_btp=False, selected_is_sbpa=False):
    """Step 12 + optional step 13 (BTP prereq check). Starts from accepted peer review."""
    _sbpa_branch = (_SBPA_PHASE_D_INSTRUCTIONS % {"rid": rid}) if selected_is_sbpa else None
    if selected_is_sbpa:
        return (
            "HEADLESS PIPELINE — Phase D (Step 12 · SBPA Configuration Handover) for run %(rid)s.\n\n"
            "Checkpoint 3 ('CP3 · Acceptance') was %(decision)s by the developer.\n"
            "Developer notes: %(notes)s\n\n"
            "Do NOT read SKILL.md — this prompt replaces it.\n"
            "CLAUDE.md platform rules apply in full.\n\n"
            "FILES TO READ (nothing else):\n"
            "  output/%(rid)s/run.json\n"
            "  output/%(rid)s/02-solution-proposal.md\n"
            "  output/%(rid)s/06-sbpa-design.md\n"
            "  output/%(rid)s/08-test-scenarios.md\n"
            "  output/%(rid)s/09-review.md\n"
            "  output/%(rid)s/decisions/%(cp3_slug)s.json\n"
            "  %(fd)s\n\n"
            "%(plain_english)s\n\n"
            "Start by appending the CP3 decision to run.json.human_approvals and clearing checkpoint_request.\n\n"
        ) % {"rid": rid, "fd": fd_path, "decision": decision, "notes": notes or "—",
             "cp3_slug": cp3_slug, "plain_english": _PLAIN_ENGLISH_RULE} + _sbpa_branch
    _btp_branch = (
        "The approach selected at CP1 IS BTP (side-by-side). Add steps 13 and 14:\n"
        "  • Set run.json.workflow → 'RICEFW Pipeline (14 steps, incl. BTP deploy)' (ASCII only).\n"
        "  • Append step 13 (BTP Prerequisite Check, gate=true) and step 14 (Deploy to BTP) to run.json.steps.\n"
        "  • Write step 13 checkpoint_request with checklist of ALL in-tenant prerequisites.\n"
        "  • Write output/" + rid + "/13-btp-prereq-check.md.\n"
        "  • Set status → 'awaiting_approval', step 13 → AWAITING_APPROVAL. THEN EXIT.\n"
    ) if selected_is_btp else (
        "The approach selected at CP1 is NOT BTP (key_user or developer). No deploy steps.\n"
        "  • Keep run.json.workflow as 'RICEFW Pipeline (12 steps)'.\n"
        "  • Do NOT add steps 13 or 14.\n"
        "  • Set run.json.status → 'completed', gates_passed → '3/3'. THEN EXIT.\n"
    )
    return (
        "HEADLESS PIPELINE — Phase D (Step 12 + optional BTP deploy gate) for run %(rid)s.\n\n"
        "Checkpoint 3 ('CP3 · Acceptance') was %(decision)s by the developer.\n"
        "Developer notes: %(notes)s\n\n"
        "Do NOT read SKILL.md — this prompt replaces it.\n"
        "CLAUDE.md platform rules apply in full.\n\n"
        "FILES TO READ (nothing else):\n"
        "  output/%(rid)s/run.json               (check mode_split for side-by-side)\n"
        "  output/%(rid)s/02-solution-proposal.md  (mode split + naming contract)\n"
        "  output/%(rid)s/06-build-corrected.md  OR  06-build.md  OR  06-code.md  (Step 7B output if present, else Step 6 build)\n"
        "  output/%(rid)s/10-technical-design.md   (activation sequence + tenant checklist)\n"
        "  output/%(rid)s/11-gate3-review.md\n"
        "  output/%(rid)s/decisions/%(cp3_slug)s.json\n"
        "  %(fd)s                                 (for deployment guide)\n\n"
        "%(plain_english)s\n\n"
        "Start by appending the CP3 decision to run.json.human_approvals and clearing checkpoint_request.\n\n"
        "── STEP 12 · PACKAGE (Delivery Lead) ────────────────────────────────────────\n"
        "Set step 12 → RUNNING. Then:\n"
        "  • List all deliverables (01-12-*.md) with a one-line description each.\n"
        "  • Deployment guide (mode-specific: key-user tenant steps, developer git-push, BTP deploy).\n"
        "  • Tenant verification checklist (one item per NOT_VERIFIED object + authoritative URL).\n"
        "  • Call record_experience for anything non-obvious this run taught (source = run id).\n"
        "Write output/%(rid)s/12-package.md\n"
        "Update run.json: step 12 → PASS, deliverables[] complete.\n\n"
        "── STEP 12B · CREATE DEPLOY FOLDER ──────────────────────────────────────────\n"
        "Create output/%(rid)s/deploy/ and write three files:\n"
        "  1. output/%(rid)s/deploy/code-final.md\n"
        "     Copy of 06-build-corrected.md (or 06-build.md or 06-code.md — whichever is the latest build artifact) with this header:\n"
        "     <!-- Promoted from Step 7B · CP2-corrected build.\n"
        "          Authoritative code for DEV activation. Run: %(rid)s -->\n"
        "  2. output/%(rid)s/deploy/activation-sequence.md\n"
        "     Step-by-step ADT activation order from the Transport Plan\n"
        "     (TD section 13). Table: Step | Object | Type | Layer | Pre-condition.\n"
        "  3. output/%(rid)s/deploy/tenant-checklist.md\n"
        "     Complete tenant verification checklist from TD section 14, formatted\n"
        "     as a printable markdown checklist ready for the developer to work through.\n"
        "Update run.json deliverables with the three deploy/ files.\n\n"
        "── BRANCHING: based on the approach selected at CP1 ─────────────────────────\n"
    ) % {"rid": rid, "fd": fd_path, "decision": decision, "notes": notes or "—", "cp3_slug": cp3_slug,
         "plain_english": _PLAIN_ENGLISH_RULE} + \
    _btp_branch


def _phase_e_prompt(rid, decision, notes):
    """Step 14: BTP deploy only. Prereqs confirmed — actually deploy."""
    return (
        "HEADLESS PIPELINE — Phase E (Step 14 · Deploy to BTP) for run %(rid)s.\n\n"
        "The developer confirmed all BTP prerequisites at 'CP-Deploy · BTP prerequisites'.\n"
        "Decision: %(decision)s. Notes: %(notes)s\n\n"
        "Do NOT read SKILL.md — this prompt replaces it.\n\n"
        "FILES TO READ:\n"
        "  output/%(rid)s/run.json\n"
        "  output/%(rid)s/13-btp-prereq-check.md\n"
        "  output/%(rid)s/06-build-corrected.md\n\n"
        "%(plain_english)s\n\n"
        "Start by appending the CP-Deploy decision to run.json.human_approvals and clearing checkpoint_request.\n"
        "Set step 13 → PASS. Set step 14 → RUNNING.\n\n"
        "── STEP 14 · DEPLOY TO BTP (Developer) ──────────────────────────────────────\n"
        "  • Call the btp_deploy tool with prereqs_confirmed=true.\n"
        "    Target the dev/test space — never production.\n"
        "  • Record the deploy result (app URLs, errors if any).\n"
        "Write output/%(rid)s/14-deploy-report.md:\n"
        "  MTA modules deployed, app URLs, smoke-check result, promote-to-prod checklist.\n"
        "Update run.json: step 14 → PASS or FAIL, deliverables += '14-deploy-report.md'.\n"
        "Set run.json.status → 'completed' (or 'failed' if deploy errored).\n"
        "THEN EXIT.\n"
    ) % {"rid": rid, "decision": decision, "notes": notes or "—",
         "plain_english": _PLAIN_ENGLISH_RULE}


# Maps checkpoint name → which phase to spawn next.
# Unknown checkpoints fall back to the legacy generic-resume prompt for forward-compat.
_CHECKPOINT_PHASE = {
    "CP1 · Solution approval": "B",
    "CP2 · Code approval":     "C",
    "CP3 · Acceptance":        "D",
    "CP3 · Findings Review (quality score too low)": "D",
    "CP-Deploy · BTP prerequisites": "E",
}

def engine_binary():
    override = os.environ.get("S4PC_CLAUDE_BIN")
    if override:
        return override if os.path.isfile(override) else None
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".claude", "local", "claude"),
        os.path.join(home, ".local", "bin", "claude"),
        "/usr/local/bin/claude", "/opt/homebrew/bin/claude",
        os.path.join(home, ".claude", "local", "claude.exe"),
        os.path.join(home, "AppData", "Roaming", "npm", "claude.cmd"),
    ]
    for cand in candidates:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None

# ---------------------------------------------------------------------------
# Per-run token accounting. Each headless run is spawned with --output-format
# stream-json; its final {"type":"result"} event carries token usage + cost,
# which we aggregate per run into webapp/logs/usage.json for the Admin page.
# ---------------------------------------------------------------------------
USAGE_PATH = os.path.join(ENGINE_LOG_DIR, "usage.json")
USAGE_LOCK = threading.Lock()
# USD per 1M tokens — Claude Sonnet 4.6 rates (default). Override via env for Opus.
# Sonnet: input=$3, output=$15 | Opus: input=$15, output=$75
COST_RATES = {
    "input": float(os.environ.get("S4PC_RATE_IN", "3")),
    "output": float(os.environ.get("S4PC_RATE_OUT", "15")),
    "cache_read": float(os.environ.get("S4PC_RATE_CACHE_READ", "0.30")),
    "cache_write": float(os.environ.get("S4PC_RATE_CACHE_WRITE", "3.75")),
}

def _est_cost(inp, out, cread, cwrite):
    return round(inp / 1e6 * COST_RATES["input"] + out / 1e6 * COST_RATES["output"]
                 + cread / 1e6 * COST_RATES["cache_read"] + cwrite / 1e6 * COST_RATES["cache_write"], 4)

def _load_usage():
    return read_json(USAGE_PATH, {"runs": {}}) or {"runs": {}}

def _parse_result_event(log_path):
    """Return the last stream-json {"type":"result",...} event from a headless run log."""
    res = None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "result":
                    res = obj
    except Exception:
        pass
    return res

def _record_run_usage(run_id, job_id, kind, log_path, fd):
    """Parse a finished job's log for token usage and fold it into the run's usage record."""
    if not run_id:
        return
    res = _parse_result_event(log_path)
    if not res:
        return
    u = res.get("usage", {}) or {}
    job = {
        "job": job_id, "kind": kind,
        "input": int(u.get("input_tokens", 0) or 0),
        "output": int(u.get("output_tokens", 0) or 0),
        "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
        "cache_creation": int(u.get("cache_creation_input_tokens", 0) or 0),
        "cost_usd": float(res.get("total_cost_usd", 0) or 0),
        "num_turns": int(res.get("num_turns", 0) or 0),
        "duration_ms": int(res.get("duration_ms", 0) or 0),
        "model": next(iter(res.get("modelUsage", {}) or {}), None),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with USAGE_LOCK:
        data = _load_usage()
        rec = data["runs"].get(run_id) or {"run": run_id, "fd": fd, "jobs": []}
        rec["fd"] = fd or rec.get("fd")
        rec["jobs"] = [j for j in rec.get("jobs", []) if j.get("job") != job_id] + [job]
        agg = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
               "cost_usd": 0.0, "duration_ms": 0, "num_turns": 0}
        for j in rec["jobs"]:
            for k in ("input", "output", "cache_read", "cache_creation", "num_turns", "duration_ms"):
                agg[k] += j.get(k, 0)
            agg["cost_usd"] += j.get("cost_usd", 0)
        agg["total_tokens"] = agg["input"] + agg["output"] + agg["cache_read"] + agg["cache_creation"]
        agg["est_cost_usd"] = _est_cost(agg["input"], agg["output"], agg["cache_read"], agg["cache_creation"])
        rec.update(agg)
        rec["last_updated"] = job["ts"]
        rec["model"] = job.get("model") or rec.get("model")
        data["runs"][run_id] = rec
        try:
            with open(USAGE_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception:
            pass

# ------------------------------------------------------ BTP deploy connections (UI-editable) ---
# Multi-connection store. Non-secret fields (endpoint, org, space, user, label, enabled) are
# persisted to webapp/data/btp-connections.json; passwords and tokens are held in memory only —
# never written to disk, git, or logs — and injected into the deploy subprocess environment.
BTP_CONN_PATH = os.path.join(APP_DIR, "data", "btp-connection.json")    # kept for migration
BTP_CONNS_PATH = os.path.join(APP_DIR, "data", "btp-connections.json")  # new multi-conn store
_BTP_SECRETS = {}   # {conn_id: {"password": ..., "token": ..., "cf_passcode": ...}} — in-memory only
_BTP_DEPLOY_JOBS = {}  # {job_id: {"run_id":..,"status":"running"|"done","ok":None|bool,"steps":[],"error":""}}
_BTP_DEPLOY_LOCK = threading.Lock()

# Pin CF_HOME to the interactive default (~/.cf) so every `cf` subprocess we spawn reads the
# SAME config the user's `cf login --sso` writes to.
os.environ.setdefault("CF_HOME", os.path.expanduser(os.path.join("~", ".cf")))

def _cf_bin():
    import shutil as _sh
    return _sh.which("cf"), _sh.which("mbt")

def _sso_passcode_url(api_ep):
    """Compute the CF SSO one-time passcode URL from a CF API endpoint.
    https://api.cf.eu10-005.hana.ondemand.com  ->  https://login.cf.eu10-005.hana.ondemand.com/passcode
    """
    try:
        parsed = urllib.parse.urlparse((api_ep or "").strip())
        host = parsed.hostname or ""
        new_host = host.replace("api.cf.", "login.cf.", 1)
        if new_host == host:
            new_host = "login." + host  # fallback for non-standard endpoints
        scheme = parsed.scheme or "https"
        return f"{scheme}://{new_host}/passcode"
    except Exception:
        return ""

def _conns_load():
    data = read_json(BTP_CONNS_PATH)
    if isinstance(data, list):
        return data
    # Migrate from legacy single-connection file
    legacy = read_json(BTP_CONN_PATH) or {}
    if legacy.get("api"):
        return [{"id": "conn-legacy",
                 "label": legacy.get("api", ""),
                 "api": legacy.get("api", ""),
                 "org": legacy.get("org", ""),
                 "space": legacy.get("space", ""),
                 "user": "",
                 "enabled": bool(legacy.get("allow_deploy"))}]
    return []

def _conns_save(conns):
    os.makedirs(os.path.dirname(BTP_CONNS_PATH), exist_ok=True)
    with open(BTP_CONNS_PATH, "w", encoding="utf-8") as fh:
        json.dump(conns, fh, indent=2)

def _btp_env():
    """Build env vars for the first enabled BTP connection (used by _spawn_claude)."""
    conns = _conns_load()
    enabled = next((c for c in conns if c.get("enabled")), None)
    if not enabled:
        return {}
    e = {}
    if enabled.get("api"):   e["CF_API"] = enabled["api"]
    if enabled.get("org"):   e["CF_ORG"] = enabled["org"]
    if enabled.get("space"): e["CF_SPACE"] = enabled["space"]
    if enabled.get("user"):  e["CF_USER"] = enabled["user"]
    sec = _BTP_SECRETS.get(enabled.get("id") or "", {})
    if sec.get("password"):  e["CF_PASSWORD"] = sec["password"]
    if sec.get("token"):     e["CF_ACCESS_TOKEN"] = sec["token"]
    e["S4PC_ALLOW_DEPLOY"] = "true"
    return e

def _cf_run(cf, cmd, token="", display=None):
    import subprocess as _sp
    env = dict(os.environ)
    if token: env["CF_ACCESS_TOKEN"] = token
    try:
        p = _sp.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        return {"cmd": display or " ".join(str(x) for x in cmd),
                "code": p.returncode, "out": (p.stdout or "")[-400:], "err": (p.stderr or "")[-400:]}
    except Exception as exc:
        return {"cmd": display or str(cmd[0]), "code": -1, "out": "", "err": str(exc)}

def _parse_cf_list(output):
    """Parse cf orgs / cf spaces output — skip header lines, return name list."""
    names = []
    past_header = False
    for line in (output or "").strip().splitlines():
        line = line.strip()
        if not line or line.startswith("Getting ") or line.startswith("OK"):
            continue
        if line == "name":
            past_header = True
            continue
        if past_header and not line.startswith("---"):
            names.append(line)
    return names

def btp_connections_get():
    conns = _conns_load()
    cf, mbt = _cf_bin()
    return {
        "connections": [
            {"id": c.get("id"), "label": c.get("label") or c.get("api", ""),
             "api": c.get("api", ""), "org": c.get("org", ""),
             "space": c.get("space", ""), "user": c.get("user", ""),
             "enabled": bool(c.get("enabled")),
             "has_creds": bool((_BTP_SECRETS.get(c.get("id") or "") or {}).get("password") or
                               (_BTP_SECRETS.get(c.get("id") or "") or {}).get("token") or
                               (_BTP_SECRETS.get(c.get("id") or "") or {}).get("cf_passcode")),
             "has_token": bool((_BTP_SECRETS.get(c.get("id") or "") or {}).get("token")),
             "has_password": bool((_BTP_SECRETS.get(c.get("id") or "") or {}).get("password")),
             "has_passcode": bool((_BTP_SECRETS.get(c.get("id") or "") or {}).get("cf_passcode")),
             "passcode_url": _sso_passcode_url(c.get("api", ""))}
            for c in conns
        ],
        "cf_installed": bool(cf), "mbt_installed": bool(mbt),
    }

def btp_connection_add(body):
    conns = _conns_load()
    conn_id = "conn-" + uuid.uuid4().hex[:8]
    conns.append({"id": conn_id,
                  "label": (body.get("label") or body.get("api") or "").strip(),
                  "api": (body.get("api") or "").strip(),
                  "org": (body.get("org") or "").strip(),
                  "space": (body.get("space") or "").strip(),
                  "user": (body.get("user") or "").strip(),
                  "enabled": bool(body.get("enabled"))})
    _conns_save(conns)
    sec = {}
    if body.get("password"):    sec["password"]    = body["password"]
    if body.get("token"):       sec["token"]       = body["token"]
    if body.get("cf_passcode"): sec["cf_passcode"] = body["cf_passcode"]
    if sec: _BTP_SECRETS[conn_id] = sec
    return {"ok": True, "id": conn_id}, 200

def btp_connection_update_one(body):
    conn_id = (body.get("id") or "").strip()
    if not conn_id:
        return {"error": "id required"}, 400
    conns = _conns_load()
    for c in conns:
        if c.get("id") == conn_id:
            for k in ("label", "api", "org", "space", "user"):
                if body.get(k) is not None: c[k] = (body[k] or "").strip()
            if "enabled" in body: c["enabled"] = bool(body["enabled"])
            _conns_save(conns)
            if body.get("clear_creds"):
                _BTP_SECRETS.pop(conn_id, None)
            else:
                sec = _BTP_SECRETS.get(conn_id) or {}
                if body.get("password"):    sec["password"]    = body["password"]
                if body.get("token"):       sec["token"]       = body["token"]
                if body.get("cf_passcode"): sec["cf_passcode"] = body["cf_passcode"]
                if sec: _BTP_SECRETS[conn_id] = sec
            return {"ok": True}, 200
    return {"error": "Connection not found"}, 404

def btp_connection_delete_one(body):
    conn_id = (body.get("id") or "").strip()
    if not conn_id:
        return {"error": "id required"}, 400
    conns = _conns_load()
    new_conns = [c for c in conns if c.get("id") != conn_id]
    if len(new_conns) == len(conns):
        return {"error": "Connection not found"}, 404
    _conns_save(new_conns)
    _BTP_SECRETS.pop(conn_id, None)
    return {"ok": True}, 200

def _cf_http(api_ep, path, token):
    """Call the CF REST API with a Bearer token — no cf CLI needed. Returns (data, error)."""
    import urllib.request as _ureq, urllib.error as _uerr
    url = api_ep.rstrip("/") + path
    req = _ureq.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with _ureq.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except _uerr.HTTPError as exc:
        body = ""
        try: body = exc.read().decode("utf-8", "replace")[:300]
        except Exception: pass
        return None, "HTTP %d %s%s" % (exc.code, exc.reason, (": " + body) if body else "")
    except Exception as exc:
        return None, str(exc)

def btp_connection_test_one(body):
    conn_id = (body.get("id") or "").strip()
    conns = _conns_load()
    conn = next((c for c in conns if c.get("id") == conn_id), None)
    if not conn:
        return {"error": "Connection not found"}, 404
    api_ep = conn.get("api", "")
    org = conn.get("org", "")
    space = conn.get("space", "")
    user = conn.get("user", "")
    sec = _BTP_SECRETS.get(conn_id) or {}
    pwd = sec.get("password", "")
    token = sec.get("token", "")
    if any(b in (space or "").lower() for b in ("prod", "prd", "production")):
        return {"connected": False,
                "reason": "Target space '%s' looks like production — dev/test only." % space}, 200
    # Token path: verify via CF REST API directly — works without cf CLI installed
    if token:
        q = urllib.parse.quote(org, safe="")
        step1 = {"cmd": "GET /v3/organizations?names=" + org, "code": -1, "out": "", "err": ""}
        data, err = _cf_http(api_ep, "/v3/organizations?names=" + q + "&per_page=50", token)
        if err:
            step1.update({"code": 1, "err": err})
            return {"connected": False, "steps": [step1],
                    "reason": "CF API call failed — check endpoint and token. " + err}, 200
        resources = (data or {}).get("resources", [])
        step1.update({"code": 0, "out": "Found %d org(s) matching '%s'" % (len(resources), org)})
        if not resources:
            return {"connected": False, "steps": [step1],
                    "reason": "Org '%s' not found or not accessible with this token." % org}, 200
        org_guid = resources[0]["guid"]
        sq = urllib.parse.quote(space, safe="")
        step2 = {"cmd": "GET /v3/spaces?names=" + space, "code": -1, "out": "", "err": ""}
        data2, err2 = _cf_http(api_ep,
            "/v3/spaces?organization_guids=%s&names=%s&per_page=50" % (org_guid, sq), token)
        if err2:
            step2.update({"code": 1, "err": err2})
            return {"connected": False, "steps": [step1, step2],
                    "reason": "Space lookup failed: " + err2}, 200
        spaces = (data2 or {}).get("resources", [])
        step2.update({"code": 0, "out": "Found %d space(s) matching '%s'" % (len(spaces), space)})
        if not spaces:
            return {"connected": False, "steps": [step1, step2],
                    "reason": "Space '%s' not found in org '%s'." % (space, org)}, 200
        return {"connected": True, "steps": [step1, step2]}, 200
    # CF CLI path (session reuse or password)
    cf, _ = _cf_bin()
    if not cf:
        return {"connected": False,
                "reason": "No token stored and cf CLI not installed. "
                          "Add a UAA bearer token when editing this system, or install cf CLI and run cf login."}, 200
    probe = _cf_run(cf, [cf, "target"], display="cf target")
    steps = [probe]
    logged_in = (probe["code"] == 0 and api_ep.rstrip("/") in (probe["out"] or "")
                 and "Not logged in" not in (probe["out"] or ""))
    if not logged_in:
        if user and pwd:
            steps.append(_cf_run(cf, [cf, "api", api_ep]))
            steps.append(_cf_run(cf, [cf, "auth", user, pwd], display="cf auth ****"))
        else:
            return {"connected": False, "steps": steps,
                    "reason": "No active CF session for %s and no credentials. "
                    "Run `cf login -a %s` in a terminal (add --sso for SSO/trial)." % (api_ep, api_ep)}, 200
    if org and space:
        steps.append(_cf_run(cf, [cf, "target", "-o", org, "-s", space]))
    ok = all(s["code"] == 0 for s in steps)
    return {"connected": ok, "steps": steps,
            "reason": "" if ok else "A cf command failed — check endpoint, credentials, org/space."}, 200

def btp_discover_orgs(body):
    api_ep = (body.get("api") or "").strip()
    user = (body.get("user") or "").strip()
    pwd = body.get("password") or ""
    token = body.get("token") or ""
    # Edit mode: reuse stored credentials from memory when form fields are blank
    conn_id = (body.get("conn_id") or "").strip()
    if conn_id and not token and not pwd:
        sec = _BTP_SECRETS.get(conn_id) or {}
        token = sec.get("token", "")
        pwd = pwd or sec.get("password", "")
    if not api_ep:
        return {"error": "CF API endpoint is required"}, 400
    # Token path: call CF REST API directly — no cf CLI needed
    if token:
        data, err = _cf_http(api_ep, "/v3/organizations?per_page=200", token)
        if err:
            return {"error": "CF API call failed — check endpoint and token: " + err}, 200
        orgs = [r["name"] for r in (data or {}).get("resources", [])]
        return {"orgs": orgs}, 200
    # CF CLI path — check for an existing session FIRST (never call cf api speculatively,
    # it resets the stored token and logs out an active SSO session).
    cf, _ = _cf_bin()
    if not cf:
        return {"error": "No token provided and cf CLI not installed. "
                         "Enter a UAA bearer token to load orgs without the cf CLI."}, 200
    probe = _cf_run(cf, [cf, "target"], display="cf target")
    logged_in = (probe["code"] == 0
                 and api_ep.rstrip("/") in (probe["out"] or "")
                 and "Not logged in" not in (probe["out"] or ""))
    if logged_in:
        r3 = _cf_run(cf, [cf, "orgs"])
        if r3["code"] != 0:
            return {"error": "cf orgs failed: " + (r3["err"] or r3["out"])[-300:]}, 200
        return {"orgs": _parse_cf_list(r3["out"])}, 200
    # No active session — try user+password if provided
    if user and pwd:
        r = _cf_run(cf, [cf, "api", api_ep])
        if r["code"] != 0:
            return {"error": "cf api failed: " + (r["err"] or r["out"])[-300:]}, 200
        r2 = _cf_run(cf, [cf, "auth", user, pwd], display="cf auth ****")
        if r2["code"] != 0:
            return {"error": "Authentication failed: " + (r2["err"] or r2["out"])[-300:]}, 200
        r3 = _cf_run(cf, [cf, "orgs"])
        if r3["code"] != 0:
            return {"error": "cf orgs failed: " + (r3["err"] or r3["out"])[-300:]}, 200
        return {"orgs": _parse_cf_list(r3["out"])}, 200
    return {"error": "Not logged in to %s. Run `cf login -a %s` in a terminal first, "
                     "or enter your password, or paste a UAA bearer token." % (api_ep, api_ep)}, 200

def btp_discover_spaces(body):
    api_ep = (body.get("api") or "").strip()
    org = (body.get("org") or "").strip()
    token = body.get("token") or ""
    conn_id = (body.get("conn_id") or "").strip()
    if conn_id and not token:
        token = (_BTP_SECRETS.get(conn_id) or {}).get("token", "")
    if not org:
        return {"error": "Org is required"}, 400
    # Token path: CF REST API — no cf CLI needed
    if token and api_ep:
        q = urllib.parse.quote(org, safe="")
        data, err = _cf_http(api_ep, "/v3/organizations?names=" + q + "&per_page=10", token)
        if err:
            return {"error": "Org lookup failed: " + err}, 200
        resources = (data or {}).get("resources", [])
        if not resources:
            return {"error": "Org '%s' not found or not accessible with this token." % org}, 200
        org_guid = resources[0]["guid"]
        data2, err2 = _cf_http(api_ep,
            "/v3/spaces?organization_guids=%s&per_page=200" % org_guid, token)
        if err2:
            return {"error": "Spaces lookup failed: " + err2}, 200
        spaces = [r["name"] for r in (data2 or {}).get("resources", [])]
        return {"spaces": spaces}, 200
    # CF CLI path
    cf, _ = _cf_bin()
    if not cf:
        return {"error": "No token/endpoint provided and cf CLI not installed."}, 200
    r = _cf_run(cf, [cf, "target", "-o", org])
    if r["code"] != 0:
        return {"error": "cf target -o failed: " + (r["err"] or r["out"])[-300:]}, 200
    r2 = _cf_run(cf, [cf, "spaces"])
    if r2["code"] != 0:
        return {"error": "cf spaces failed: " + (r2["err"] or r2["out"])[-300:]}, 200
    return {"spaces": _parse_cf_list(r2["out"])}, 200

def btp_test_credentials(body):
    """Test a connection using form fields — reuses stored credentials in edit mode."""
    api_ep = (body.get("api") or "").strip()
    org = (body.get("org") or "").strip()
    space = (body.get("space") or "").strip()
    user = (body.get("user") or "").strip()
    conn_id = (body.get("conn_id") or "").strip()
    if conn_id:
        sec = _BTP_SECRETS.get(conn_id) or {}
        if not body.get("token"):   body["token"]   = sec.get("token", "")
        if not body.get("password"): body["password"] = sec.get("password", "")
    pwd = body.get("password") or ""
    token = body.get("token") or ""
    if not api_ep:
        return {"connected": False, "reason": "CF API endpoint is required"}, 200
    if not org:
        return {"connected": False, "reason": "Load orgs and select an org first"}, 200
    if any(b in (space or "").lower() for b in ("prod", "prd", "production")):
        return {"connected": False, "reason": "Space '%s' looks like production — dev/test only." % space}, 200
    # Token path: CF REST API (no cf CLI)
    if token:
        q = urllib.parse.quote(org, safe="")
        data, err = _cf_http(api_ep, "/v3/organizations?names=" + q + "&per_page=10", token)
        if err:
            return {"connected": False, "steps": [{"cmd": "GET /v3/organizations", "code": 1, "out": "", "err": err}],
                    "reason": "CF API call failed — check endpoint and token: " + err}, 200
        resources = (data or {}).get("resources", [])
        steps = [{"cmd": "GET /v3/organizations?names=" + org, "code": 0 if resources else 1,
                  "out": ("Found org '%s'" % org) if resources else "", "err": "" if resources else "Not found"}]
        if not resources:
            return {"connected": False, "steps": steps,
                    "reason": "Org '%s' not found or not accessible with this token." % org}, 200
        org_guid = resources[0]["guid"]
        if space:
            sq = urllib.parse.quote(space, safe="")
            data2, err2 = _cf_http(api_ep,
                "/v3/spaces?organization_guids=%s&names=%s&per_page=10" % (org_guid, sq), token)
            sp_resources = (data2 or {}).get("resources", []) if not err2 else []
            steps.append({"cmd": "GET /v3/spaces?names=" + space,
                          "code": 0 if sp_resources else 1,
                          "out": ("Found space '%s'" % space) if sp_resources else "",
                          "err": err2 or ("" if sp_resources else "Not found")})
            if err2 or not sp_resources:
                return {"connected": False, "steps": steps,
                        "reason": (err2 or "Space '%s' not found in org '%s'." % (space, org))}, 200
        return {"connected": True, "steps": steps}, 200
    # CF CLI path
    cf, _ = _cf_bin()
    if not cf:
        return {"connected": False,
                "reason": "No token provided and cf CLI not installed. Enter a UAA bearer token to test."}, 200
    probe = _cf_run(cf, [cf, "target"], display="cf target")
    steps = [probe]
    logged_in = (probe["code"] == 0 and api_ep.rstrip("/") in (probe["out"] or "")
                 and "Not logged in" not in (probe["out"] or ""))
    if not logged_in:
        if user and pwd:
            steps.append(_cf_run(cf, [cf, "api", api_ep]))
            steps.append(_cf_run(cf, [cf, "auth", user, pwd], display="cf auth ****"))
        else:
            return {"connected": False, "steps": steps,
                    "reason": "No active CF session. Run `cf login -a %s` or enter credentials." % api_ep}, 200
    if org and space:
        steps.append(_cf_run(cf, [cf, "target", "-o", org, "-s", space]))
    ok = all(s["code"] == 0 for s in steps)
    return {"connected": ok, "steps": steps,
            "reason": "" if ok else "A cf command failed."}, 200

# Backward-compat wrappers (old single-connection API — kept so existing code doesn't break)
def btp_connection():
    conns = _conns_load()
    cf, mbt = _cf_bin()
    enabled = next((c for c in conns if c.get("enabled")), conns[0] if conns else {})
    conn_id = enabled.get("id", "")
    sec = _BTP_SECRETS.get(conn_id) or {}
    return {"api": enabled.get("api", ""), "org": enabled.get("org", ""),
            "space": enabled.get("space", ""), "allow_deploy": bool(enabled.get("enabled")),
            "creds_in_memory": bool(sec.get("password") or sec.get("token")),
            "cf_installed": bool(cf), "mbt_installed": bool(mbt)}

def btp_connection_set(body):
    conns = _conns_load()
    if not conns:
        return btp_connection_add(body)
    c = conns[0]
    body["id"] = c["id"]
    return btp_connection_update_one(body)

def btp_test():
    conns = _conns_load()
    if not conns:
        return {"connected": False, "reason": "No BTP system configured yet."}, 200
    enabled = next((c for c in conns if c.get("enabled")), conns[0])
    return btp_connection_test_one({"id": enabled.get("id")})

def pipeline_btp_deploy(run_id, force_rebuild=False):
    """Start an async BTP deploy job.  Returns immediately with a job_id; caller polls
    /api/btp/deploy-status?job=<id> for progress.

    Design principles:
    - Isolated CF_HOME per deploy: a fresh tempdir so no stale session from the user's
      terminal (~/.cf) or a previous deploy to a different system can bleed in.
    - Credentials come exclusively from _BTP_SECRETS (in-memory, session-only).
    - Smart build skip: if a fresh .mtar (< 2 h old) already exists, npm install and
      mbt build are skipped — only cf target + cf deploy run (takes 5-10 min, not 20+).
    - The temp CF_HOME is deleted after deploy regardless of outcome.
    """
    import subprocess as _sp
    import shutil as _sh
    import tempfile as _tmp

    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400

    # ── locate source ────────────────────────────────────────────────────────
    src_root = os.path.join(ROOT_DIR, "output", run_id, "src")
    if not os.path.isdir(src_root):
        return {"error": "No src/ directory for this run — ensure Step 6 (Build) completed."}, 404
    subdirs = [d for d in os.listdir(src_root) if os.path.isdir(os.path.join(src_root, d))]
    if not subdirs:
        return {"error": "src/ is empty"}, 404
    proj_dir = os.path.join(src_root, subdirs[0])

    # ── locate toolchain ─────────────────────────────────────────────────────
    cf  = _sh.which("cf")  or _sh.which("cf.exe")
    mbt = _sh.which("mbt") or _sh.which("mbt.cmd") or _sh.which("mbt.exe")
    npm = _sh.which("npm") or _sh.which("npm.cmd") or _sh.which("npm.exe")
    if not cf:
        return {"error": "cf CLI not found on PATH — install from https://github.com/cloudfoundry/cli"}, 500
    if not mbt:
        return {"error": "mbt not found on PATH — run: npm i -g mbt"}, 500
    if not npm:
        return {"error": "npm not found on PATH"}, 500

    # ── resolve the enabled BTP connection ───────────────────────────────────
    conns   = _conns_load()
    enabled = next((c for c in conns if c.get("enabled")), None)
    if not enabled:
        return {"error": "No BTP connection is marked 'Enable for pipeline deploy'. "
                         "Configure one in the BTP Connections panel."}, 400
    conn_id = enabled.get("id", "")
    api_ep  = (enabled.get("api")   or "").strip()
    org     = (enabled.get("org")   or "").strip()
    space   = (enabled.get("space") or "").strip()
    user    = (enabled.get("user")  or "").strip()
    if not api_ep:
        return {"error": "Enabled BTP connection has no CF API endpoint."}, 400
    if not org or not space:
        return {"error": "Enabled BTP connection has no org/space — click Load orgs, pick a space, Save."}, 400

    # ── read credentials from in-memory store only ───────────────────────────
    sec = _BTP_SECRETS.get(conn_id, {})
    tok      = (sec.get("token")       or "").strip()
    pwd      = (sec.get("password")    or "").strip()
    passcode = (sec.get("cf_passcode") or "").strip()
    if not tok and not pwd and not passcode:
        return {"error": "No credentials for the enabled BTP connection. "
                         "The webapp was restarted and the token/password was cleared (security design). "
                         "Re-enter credentials in the BTP Connections panel and try again. "
                         "For deployments, use 'CF SSO Passcode' (recommended) — it creates a proper "
                         "session that survives the 15-20 min build time."}, 400

    # ── check if a job is already running for this run ────────────────────────
    with _BTP_DEPLOY_LOCK:
        for jid, jstate in _BTP_DEPLOY_JOBS.items():
            if jstate.get("run_id") == run_id and jstate.get("status") == "running":
                return {"job": jid, "status": "already_running",
                        "message": "A deploy job is already running for this run. Poll /api/btp/deploy-status?job=" + jid}, 200

    # ── detect fresh .mtar — skip build if < 2 h old (unless force_rebuild) ──
    mtar_dir  = os.path.join(proj_dir, "mta_archives")
    fresh_mtar = None
    if not force_rebuild and os.path.isdir(mtar_dir):
        candidates = [f for f in os.listdir(mtar_dir) if f.endswith(".mtar")]
        if candidates:
            newest = os.path.join(mtar_dir, sorted(candidates)[-1])
            age_s  = time.time() - os.path.getmtime(newest)
            if age_s < 7200:   # 2 h
                fresh_mtar = newest
    if force_rebuild and os.path.isdir(mtar_dir):
        # Delete all stale mtars so mbt build produces a clean new one
        for f in os.listdir(mtar_dir):
            if f.endswith(".mtar"):
                try:
                    os.remove(os.path.join(mtar_dir, f))
                except OSError:
                    pass

    # ── snapshot credentials now (before background thread) ──────────────────
    # The background thread must not re-read _BTP_SECRETS (race with clear-on-restart)
    tok_snap      = tok
    pwd_snap      = pwd
    passcode_snap = passcode

    # ── create job record and launch background thread ────────────────────────
    job_id = uuid.uuid4().hex[:12]
    auth_mode = "passcode" if passcode_snap else ("token" if tok_snap else "password")
    job = {"run_id": run_id, "status": "running", "ok": None, "steps": [], "error": "",
           "mtar": None, "target": {"api": api_ep, "org": org, "space": space},
           "started": time.strftime("%H:%M:%S"), "finished": None,
           "skipped_build": fresh_mtar is not None, "auth_mode": auth_mode}
    with _BTP_DEPLOY_LOCK:
        _BTP_DEPLOY_JOBS[job_id] = job

    def _run_deploy():
        cf_home = _tmp.mkdtemp(prefix="s4pc_cf_deploy_")
        steps   = job["steps"]   # shared list — thread appends, poller reads

        def _run(cmd_list, cwd=None, timeout=300, label=None):
            if sys.platform == "win32":
                parts = []
                for x in cmd_list:
                    s = str(x)
                    parts.append('"' + s.replace('"', '') + '"' if (" " in s or "(" in s or "\\" in s) else s)
                cmd  = " ".join(parts)
                shell = True
            else:
                cmd   = cmd_list
                shell = False
            try:
                p = _sp.run(cmd, capture_output=True, text=True, timeout=timeout,
                            env=env, cwd=cwd, shell=shell)
                steps.append({"cmd":  label or (cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd_list)),
                              "code": p.returncode,
                              "out":  (p.stdout or "")[-2000:],
                              "err":  (p.stderr or "")[-800:]})
                return p.returncode == 0
            except _sp.TimeoutExpired:
                steps.append({"cmd": label or str(cmd_list), "code": -1, "out": "", "err": "timed out"})
                return False
            except Exception as exc:
                steps.append({"cmd": label or str(cmd_list), "code": -2, "out": "", "err": str(exc)})
                return False

        try:
            env = {}
            env["PATH"]        = os.environ.get("PATH", "")
            env["TEMP"]        = os.environ.get("TEMP", "")
            env["TMP"]         = os.environ.get("TMP",  "")
            env["USERPROFILE"] = os.environ.get("USERPROFILE", "")
            env["HOME"]        = os.environ.get("HOME", "")
            env["SystemRoot"]  = os.environ.get("SystemRoot", "")
            env["APPDATA"]     = os.environ.get("APPDATA", "")
            env["CF_HOME"]     = cf_home
            env["S4PC_ALLOW_DEPLOY"] = "true"
            if tok_snap:
                env["CF_ACCESS_TOKEN"] = ("bearer " + tok_snap) if not tok_snap.lower().startswith("bearer ") else tok_snap
            if sys.platform == "win32":
                gnuwin32 = r"C:\Program Files (x86)\GnuWin32\bin"
                if os.path.isdir(gnuwin32) and gnuwin32 not in env["PATH"]:
                    env["PATH"] = env["PATH"] + ";" + gnuwin32

            # ── Copy CF plugins from real home into isolated CF_HOME ──────────
            # CF plugins are stored in <CF_HOME>/.cf/plugins/ — they must be
            # present in the isolated dir or `cf deploy` reports "not a registered
            # command" even though the multiapps plugin is installed for the user.
            real_cf_home = os.path.join(os.path.expanduser("~"), ".cf")
            real_plugins = os.path.join(real_cf_home, "plugins")
            iso_cf_dir   = os.path.join(cf_home, ".cf")
            os.makedirs(iso_cf_dir, exist_ok=True)
            if os.path.isdir(real_plugins):
                _sh.copytree(real_plugins, os.path.join(iso_cf_dir, "plugins"))

            # ── STEP 1: CF login ──────────────────────────────────────────────
            # SSO passcode creates a proper OAuth session (access + refresh token) so the
            # multiapps plugin can refresh the token during long staging operations.
            # Bearer-token injection has no refresh token and fails on staging for long builds.
            if passcode_snap:
                # Preferred: one-time SSO passcode — proper OAuth session
                ok = _run([cf, "login", "-a", api_ep, "--sso-passcode", passcode_snap,
                           "-o", org, "-s", space],
                          label="cf login (SSO passcode)", timeout=60)
                if not ok:
                    job["ok"]    = False
                    job["error"] = ("CF SSO login failed — the passcode may have expired (valid for ~5 min). "
                                    "Get a fresh passcode from the BTP Connections panel and try again.")
                    return
            else:
                # Fallback: bearer token injection (works for short operations; long staging may fail)
                _run([cf, "api", api_ep], label="cf api " + api_ep, timeout=30)
                if tok_snap:
                    bearer   = ("bearer " + tok_snap) if not tok_snap.lower().startswith("bearer ") else tok_snap
                    cfg_path = os.path.join(cf_home, ".cf", "config.json")
                    try:
                        cfg = {}
                        if os.path.isfile(cfg_path):
                            with open(cfg_path, encoding="utf-8-sig") as fh:
                                cfg = json.load(fh)
                        cfg["AccessToken"]  = bearer
                        cfg["RefreshToken"] = ""
                        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                        with open(cfg_path, "w", encoding="utf-8") as fh:
                            json.dump(cfg, fh, indent=2)
                        steps.append({"cmd": "inject token into CF config", "code": 0,
                                       "out": "bearer token written to isolated CF_HOME "
                                              "(⚠ no refresh token — staging may fail if build > 60 min)", "err": ""})
                    except Exception as exc:
                        steps.append({"cmd": "inject token into CF config", "code": -2,
                                       "out": "", "err": str(exc)})
                elif pwd_snap and user:
                    _run([cf, "auth", user, pwd_snap], label="cf auth (password)", timeout=30)

                _run([cf, "target", "-o", org, "-s", space], label="cf target", timeout=30)

                if steps and steps[-1]["code"] != 0:
                    job["ok"]    = False
                    job["error"] = ("CF authentication failed — token may be expired. "
                                    "Switch to SSO Passcode auth in the BTP Connections panel for reliable deployments.")
                    return

            if fresh_mtar:
                # Skip npm install + mbt build — reuse the recent archive
                steps.append({"cmd": "mbt build (skipped)", "code": 0,
                               "out": "Reusing .mtar from " + os.path.basename(fresh_mtar) +
                                      " (< 2 h old — set fresh_mtar=None to force rebuild)", "err": ""})
                mtar = fresh_mtar
            else:
                # ── STEP 2: npm install ───────────────────────────────────────
                _run([npm, "install", "--prefer-offline"], cwd=proj_dir, timeout=300, label="npm install")
                app_dir = os.path.join(proj_dir, "app")
                if os.path.isfile(os.path.join(app_dir, "package.json")):
                    _run([npm, "install", "--prefer-offline"], cwd=app_dir, timeout=180, label="npm install (app)")

                # ── STEP 3: mbt build ─────────────────────────────────────────
                if not _run([mbt, "build"], cwd=proj_dir, timeout=2400, label="mbt build"):
                    job["ok"]    = False
                    job["error"] = "mbt build failed — see steps for detail"
                    return

                # ── STEP 4: locate .mtar ──────────────────────────────────────
                mtars = [f for f in (os.listdir(mtar_dir) if os.path.isdir(mtar_dir) else []) if f.endswith(".mtar")]
                if not mtars:
                    job["ok"]    = False
                    job["error"] = "No .mtar produced — check mbt build output"
                    return
                mtar = os.path.join(mtar_dir, sorted(mtars)[-1])

            job["mtar"] = os.path.basename(mtar)

            # ── STEP 5: cf deploy ─────────────────────────────────────────────
            _run([cf, "deploy", mtar, "-f"], cwd=proj_dir, timeout=1200,
                 label="cf deploy " + os.path.basename(mtar))

            job["ok"] = bool(steps and steps[-1]["code"] == 0)

        except Exception as exc:
            job["ok"]    = False
            job["error"] = str(exc)
        finally:
            _sh.rmtree(cf_home, ignore_errors=True)
            job["status"]   = "done"
            job["finished"] = time.strftime("%H:%M:%S")

    t = threading.Thread(target=_run_deploy, daemon=True, name="btp-deploy-" + job_id)
    t.start()

    return {"job": job_id, "status": "running",
            "skipped_build": fresh_mtar is not None,
            "auth_mode": auth_mode,
            "target": {"api": api_ep, "org": org, "space": space},
            "message": "Deploy started. Poll /api/btp/deploy-status?job=" + job_id}, 200


def btp_deploy_status(job_id):
    """Return current status of a background deploy job."""
    if not job_id:
        return {"error": "job parameter required"}, 400
    job = _BTP_DEPLOY_JOBS.get(job_id)
    if not job:
        return {"error": "Unknown job id"}, 404
    return {
        "job":          job_id,
        "run_id":       job.get("run_id"),
        "status":       job.get("status"),
        "ok":           job.get("ok"),
        "error":        job.get("error") or "",
        "steps":        job.get("steps") or [],
        "mtar":         job.get("mtar"),
        "target":       job.get("target") or {},
        "started":      job.get("started"),
        "finished":     job.get("finished"),
        "skipped_build": job.get("skipped_build", False),
    }, 200


def btp_cf_run(run_id, cf_args, label=None):
    """Run a CF CLI command in an isolated CF_HOME using the enabled connection credentials.
    Returns {"ok": bool, "out": str, "err": str}.  Used for log fetching, aborting ops, etc.
    """
    import subprocess as _sp
    import shutil as _sh
    import tempfile as _tmp

    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400

    cf = _sh.which("cf") or _sh.which("cf.exe")
    if not cf:
        return {"error": "cf CLI not found"}, 500

    conns   = _conns_load()
    enabled = next((c for c in conns if c.get("enabled")), None)
    if not enabled:
        return {"error": "No enabled BTP connection"}, 400

    conn_id = enabled.get("id", "")
    api_ep  = (enabled.get("api")   or "").strip()
    org     = (enabled.get("org")   or "").strip()
    space   = (enabled.get("space") or "").strip()
    sec     = _BTP_SECRETS.get(conn_id, {})
    tok     = (sec.get("token") or "").strip()
    pwd     = (sec.get("password") or "").strip()
    user    = (enabled.get("user") or "").strip()

    if not tok and not pwd:
        return {"error": "No credentials — re-enter token in BTP panel"}, 400

    cf_home = _tmp.mkdtemp(prefix="s4pc_cf_cmd_")
    try:
        env = {k: os.environ.get(k, "") for k in
               ("PATH", "TEMP", "TMP", "USERPROFILE", "HOME", "SystemRoot", "APPDATA")}
        env["CF_HOME"] = cf_home

        # Copy plugins
        real_plugins = os.path.join(os.path.expanduser("~"), ".cf", "plugins")
        iso_cf_dir   = os.path.join(cf_home, ".cf")
        os.makedirs(iso_cf_dir, exist_ok=True)
        if os.path.isdir(real_plugins):
            _sh.copytree(real_plugins, os.path.join(iso_cf_dir, "plugins"))

        def _r(args, timeout=30):
            cmd = " ".join('"' + str(x).replace('"', '') + '"' if (" " in str(x) or "(" in str(x) or "\\" in str(x)) else str(x) for x in args) if sys.platform == "win32" else args
            return _sp.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, shell=(sys.platform == "win32"))

        _r([cf, "api", api_ep])

        if tok:
            bearer   = ("bearer " + tok) if not tok.lower().startswith("bearer ") else tok
            cfg_path = os.path.join(cf_home, ".cf", "config.json")
            cfg = {}
            if os.path.isfile(cfg_path):
                with open(cfg_path, encoding="utf-8-sig") as fh:
                    cfg = json.load(fh)
            cfg["AccessToken"] = bearer
            cfg["RefreshToken"] = ""
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
        elif pwd and user:
            _r([cf, "auth", user, pwd])

        t = _r([cf, "target", "-o", org, "-s", space])
        if t.returncode != 0:
            return {"error": "CF auth failed — token expired?", "err": t.stderr}, 401

        p = _r([cf] + list(cf_args), timeout=120)
        return {"ok": p.returncode == 0,
                "out": (p.stdout or "")[-4000:],
                "err": (p.stderr or "")[-1000:]}, 200
    finally:
        _sh.rmtree(cf_home, ignore_errors=True)


def _spawn_claude(prompt, fd, kind, run_id=None):
    exe = engine_binary()
    if not exe:
        return None, ("Claude Code CLI not found on PATH. Install/log in to Claude Code on this "
                      "machine, or set S4PC_CLAUDE_BIN to its full path. (Fallback: copy the "
                      "pipeline command from the FD card and run it in interactive Claude Code.)")
    job_id = uuid.uuid4().hex[:10]
    log_path = os.path.join(ENGINE_LOG_DIR, "pipeline-%s.log" % job_id)
    log_fh = open(log_path, "w", encoding="utf-8")
    log_fh.write("[engine] %s\n[engine] kind=%s fd=%s\n[engine] prompt:\n%s\n%s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), kind, fd, prompt, "-" * 70))
    log_fh.flush()
    allowed_tools = list(HEADLESS_ALLOWED_TOOLS)
    if _run_wants_web(run_id) and "WebFetch" not in allowed_tools:
        allowed_tools += ["WebFetch", "WebSearch"]
        log_fh.write("[engine] side-by-side (BTP) run -> WebFetch/WebSearch enabled for developer-doc grounding\n")
        log_fh.flush()
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--mcp-config", ".mcp.json", "--strict-mcp-config",   # load ONLY the s4pc governance server
           "--permission-mode", "acceptEdits", "--allowedTools"] + allowed_tools
    try:
        _env = dict(os.environ, **_btp_env())
        _env.pop("ANTHROPIC_API_KEY", None)   # always use the logged-in claude session
        # On Windows use DETACHED_PROCESS so the Claude subprocess survives a webapp restart.
        _cflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
        proc = subprocess.Popen(cmd, cwd=ROOT_DIR, stdout=log_fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=_env,
                                creationflags=_cflags)
    except OSError as exc:
        log_fh.write("[engine] spawn failed: %s\n" % exc)
        log_fh.close()
        return None, "Failed to start Claude Code CLI: %s" % exc
    with JOBS_LOCK:
        JOBS[job_id] = {"proc": proc, "log": log_path, "fd": fd, "kind": kind, "run": run_id,
                        "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "log_fh": log_fh}
    MCP.audit("pipeline_job_started", {
        "job": job_id, "kind": kind, "fd": fd,
        "run": run_id or "", "phase": kind,
        "cmd": "claude -p ... --strict-mcp-config --permission-mode acceptEdits",
        "model": "claude (logged-in session)"
    })
    _phase_started = time.time()
    def _await_and_record():                      # capture token usage when the job finishes
        try:
            proc.wait()
        except Exception:
            pass
        exit_code = proc.poll()
        duration_s = int(time.time() - _phase_started)
        MCP.audit("pipeline_phase_completed", {
            "job": job_id, "run": run_id or "", "kind": kind, "fd": fd,
            "exit_code": exit_code, "duration_s": duration_s,
            "ok": exit_code == 0
        })
        _record_run_usage(run_id, job_id, kind, log_path, fd)
        # Rebuild the vector index when a pipeline run completes so new experience
        # entries and run data are immediately searchable by the Digital Brain.
        if exit_code == 0 and run_id:
            try:
                manifest = os.path.join(ROOT_DIR, "output", run_id, "run.json")
                data = read_json(manifest) or {}
                if data.get("status") in ("complete", "packaged", "done"):
                    _rebuild_index_bg("run %s complete" % run_id)
                    threading.Timer(60.0, lambda: _rebuild_graph_bg("run %s complete" % run_id)).start()
            except Exception:
                pass
        # Detect fast-exit failures (billing error, auth error, CLI crash) and immediately
        # flip run.json to error so the Workflow Explorer doesn't stay "stuck on step 1".
        exit_code = proc.poll()
        if exit_code != 0 and run_id:
            try:
                log_tail = ""
                with open(log_path, "r", encoding="utf-8", errors="replace") as _lf:
                    log_tail = _lf.read()[-2000:]
            except Exception:
                pass
            if log_tail:
                is_billing = "Credit balance is too low" in log_tail or "billing_error" in log_tail
                is_auth    = "authentication" in log_tail.lower() or "ANTHROPIC_API_KEY" in log_tail
                is_crash   = exit_code != 0 and len(log_tail) < 500   # exited almost immediately
                if is_billing or is_auth or is_crash:
                    manifest = os.path.join(ROOT_DIR, "output", run_id, "run.json")
                    try:
                        data = read_json(manifest) or {}
                        if data.get("status") in ("running", "in_progress"):
                            if is_billing:
                                note = "Pipeline stopped: Claude API credit balance is too low. Top up at console.anthropic.com/billing and re-run."
                            elif is_auth:
                                note = "Pipeline stopped: Claude API authentication error. Check your login with `claude auth` and re-run."
                            else:
                                note = "Pipeline process exited unexpectedly (exit %d). Check the engine log for details." % exit_code
                            data["status"] = "error"
                            data["engine_note"] = note
                            for s in data.get("steps", []):
                                if s.get("status") == "RUNNING":
                                    s["status"] = "FAIL"
                                    s["detail"] = note
                            with open(manifest, "w", encoding="utf-8") as fh:
                                json.dump(data, fh, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
    threading.Thread(target=_await_and_record, daemon=True).start()
    return job_id, None

# Canonical 12-step pipeline (mirrors .claude/skills/s4pc-ricefw-pipeline/SKILL.md) — used to
# seed a run skeleton the instant a pipeline starts, so the Workflow Explorer shows all steps
# (with step 1 executing) immediately instead of a blank screen while the engine boots.
PIPELINE_STEPS = [
    (1,  "Intake",                         "Orchestrator",           False),
    (2,  "Solution Proposal",              "Extensibility Architect", False),
    (3,  "Object Inventory Verdicts",      "Extensibility Architect", False),
    (4,  "Gate 1 · Release Check",         "Clean-Core Reviewer",    True),
    (5,  "Checkpoint 1 · Solution Approval","Human",                 False),
    (6,  "Build",                          "Developer",              False),
    (7,  "Gate 2 · Code Review + Checkpoint 2","Clean-Core Reviewer", True),
    ("7B","Apply CP2 Corrections",          "Developer",              False),
    (8,  "Lint",                           "Clean-Core Reviewer",    False),
    (9,  "Unit Test Design",               "Test Agent",             False),
    (10, "FD Analysis + Technical Design", "Developer",              False),
    (11, "Gate 3 · Peer Review + Checkpoint 3","Challenger Agent",   True),
    (12, "Package",                        "Delivery Lead",          False),
]
# Steps 13 (BTP prerequisite check) and 14 (Deploy to BTP) are NOT seeded here — the engine
# appends them to run.json only when the solution's mode split includes a side-by-side (BTP)
# capability, so the deploy stage appears only for BTP solutions.

def _run_base_and_version(fd_path):
    """Compute the run id for a (re-)run. Every run of an FD is preserved: the first run
    uses the base id; each subsequent run gets a new '-R<n>' version so previous runs and
    their generated files are never overwritten. Returns (base_id, version, previous_id)."""
    existing = [r for r in list_runs()["runs"] if r.get("fd_source") == fd_path]
    if not existing:
        base = re.sub(r"(?i)^fd[-_ ]*", "", os.path.splitext(os.path.basename(fd_path))[0])
        base = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").upper()[:40] or "RUN"
        return base, 1, None
    def ver_of(rid):
        m = re.search(r"-R(\d+)$", rid or "")
        return int(m.group(1)) if m else 1
    base = sorted(re.sub(r"-R\d+$", "", (r.get("id") or "")) for r in existing)[0]
    prev = max(existing, key=lambda r: ver_of(r.get("id")))
    return base, ver_of(prev.get("id")) + 1, prev.get("id")

def _seed_run_skeleton(fd_path):
    """Create output/<ID>/run.json immediately (status running, step 1 RUNNING) so the
    Explorer is populated before the engine writes anything. Each call creates a NEW run
    (versioned) — previous runs of the same FD are preserved. Returns the run id."""
    base, ver, prev = _run_base_and_version(fd_path)
    rid = base if ver == 1 else "%s-R%d" % (base, ver)
    title = os.path.splitext(os.path.basename(fd_path))[0]
    try:
        with open(os.path.join(ROOT_DIR, fd_path), "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
        m = re.search(r"^#\s*(.+)$", head, re.M)
        if m:
            title = re.sub(r"^FD\s*[—:-]\s*", "", m.group(1).strip())[:120]
    except Exception:
        pass
    steps = [{"n": n, "name": nm, "agent": ag, "gate": g,
              "status": "RUNNING" if n == 1 else "PENDING",
              "score": None, "iterations": 0,
              "detail": "The pipeline engine is starting and reading the FD…" if n == 1 else ""}
             for (n, nm, ag, g) in PIPELINE_STEPS]
    skeleton = {
        "id": rid, "title": title, "type": "Classifying (pipeline just started)",
        "workflow": "RICEFW Pipeline (12 steps)", "fd_source": fd_path,
        "created": time.strftime("%Y-%m-%d"), "status": "running",
        "version": ver, "previous_run": prev,
        "quality_score": None, "gates_passed": "0/3", "auto_corrections": 0,
        "extensibility_mode": None, "mode_split": [], "steps": steps,
        "human_approvals": [], "gate_results": [], "findings": [], "deliverables": [],
        "engine_note": "Seeded by the webapp; the engine is initializing and will fill this in live.",
    }
    run_dir = os.path.join(ROOT_DIR, "output", rid)
    os.makedirs(run_dir, exist_ok=True)                  # fresh folder per version — nothing is wiped
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(skeleton, fh, indent=2, ensure_ascii=False)
    return rid

def pipeline_start(fd_path):
    known = [i["path"] for i in list_inputs()["inputs"]]
    if fd_path not in known:
        return {"error": "Unknown FD: %s (upload it first)" % fd_path}, 400
    with JOBS_LOCK:
        for j in JOBS.values():
            if j["fd"] == fd_path and j["proc"].poll() is None:
                return {"error": "A pipeline job for this FD is already running."}, 409
    try:
        with open(os.path.join(ROOT_DIR, fd_path), encoding="utf-8", errors="replace") as _fh:
            _head = _fh.read(2000)
        if len(_head.strip()) < 30:
            return {"error": "FD file is empty or too short — add your Functional Design content first."}, 400
    except OSError as _e:
        return {"error": "Cannot read FD file: %s" % _e}, 400
    try:
        rid = _seed_run_skeleton(fd_path)
    except Exception:
        rid = "<ID>"
    prompt = _phase_a_prompt(fd_path, rid)
    job_id, err = _spawn_claude(prompt, fd_path, "start", run_id=rid)
    if err:
        return {"error": err}, 503
    return {"ok": True, "job": job_id, "run": rid,
            "message": "Pipeline started — steps are showing in the Workflow Explorer now."}, 200

_NAMESPACE_RE = re.compile(r'^(YY1_|Z|Y)[A-Za-z0-9_]{2,}$')      # ABAP developer / key-user objects
_BTP_NAME_RE  = re.compile(r'^[A-Za-z][A-Za-z0-9_./-]{1,}$')      # side-by-side: CAP service/entity, destination, UI5 module
def _naming_ok(n, created_in=""):
    v = (n or "").strip()
    # Side-by-side (BTP/CAP) objects don't use ABAP Z/Y/YY1_ namespaces — validate them with CAP-style rules.
    if (created_in or "").strip().lower() in ("side_by_side", "side-by-side", "btp", "cap"):
        return bool(_BTP_NAME_RE.match(v))
    return bool(_NAMESPACE_RE.match(v))

def pipeline_naming(run_id, names, contract=None, selected_approach=None):
    """Persist edited custom-object names into the run's checkpoint_request.naming_contract, so a
    developer's names survive refresh/restart (like the prerequisite checklist). Approval later
    re-validates them.

    When the developer selects a different approach at CP1 (e.g. a client-mandated BTP over the
    recommended RAP), the UI sends that approach's full `contract` (replace, not merge) plus the
    chosen `selected_approach` id — both are persisted so the swapped name grid and the radio
    survive the 3-second poll re-render and drive Build."""
    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400
    manifest = os.path.join(ROOT_DIR, "output", run_id, "run.json")
    if not os.path.isfile(manifest):
        return {"error": "Run not found"}, 404
    data = read_json(manifest) or {}
    cp = data.get("checkpoint_request") or {}
    # Full-contract swap (approach change) — replace the active contract wholesale.
    # An empty list is a deliberate clear (override to an approach with no inline naming grid).
    if isinstance(contract, list):
        nc = [{"id": str(i.get("id", "")), "object": str(i.get("object", "")),
               "type": str(i.get("type", "")), "created_in": str(i.get("created_in", "")),
               "name": str(i.get("name", "")).strip()} for i in contract if isinstance(i, dict)]
        cp["naming_contract"] = nc
    else:
        nc = cp.get("naming_contract") or []
        if not nc:
            return {"error": "No naming contract on this run"}, 409
        names = names or {}
        for item in nc:
            if item.get("id") in names:
                item["name"] = str(names[item["id"]]).strip()
        cp["naming_contract"] = nc
    if selected_approach is not None:
        cp["selected_approach"] = str(selected_approach)
    data["checkpoint_request"] = cp
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return {"ok": True, "valid": sum(1 for i in nc if _naming_ok(i.get("name"), i.get("created_in"))), "total": len(nc)}, 200

def pipeline_comments(run_id, comments):
    """Persist per-file code-review comments into checkpoint_request.code_files[].comment (Gate 2 /
    CP2), so a developer's per-file notes survive refresh and are recorded with the decision."""
    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400
    manifest = os.path.join(ROOT_DIR, "output", run_id, "run.json")
    if not os.path.isfile(manifest):
        return {"error": "Run not found"}, 404
    data = read_json(manifest) or {}
    cp = data.get("checkpoint_request") or {}
    cf = cp.get("code_files") or []
    if not cf:
        return {"error": "No code files on this run"}, 409
    comments = comments or {}
    for item in cf:
        if item.get("id") in comments:
            item["comment"] = str(comments[item["id"]])[:4000]
    cp["code_files"] = cf
    data["checkpoint_request"] = cp
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return {"ok": True, "commented": sum(1 for f in cf if (f.get("comment") or "").strip()), "total": len(cf)}, 200

def pipeline_checklist(run_id, checked):
    """Persist partial checklist progress (which prerequisite items are ticked) into the run's
    checkpoint_request, so a developer's ticks survive refreshes/restarts across a multi-day
    in-tenant build. Does NOT approve — approval is a separate step once all items are ticked."""
    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400
    manifest = os.path.join(ROOT_DIR, "output", run_id, "run.json")
    if not os.path.isfile(manifest):
        return {"error": "Run not found"}, 404
    data = read_json(manifest) or {}
    cp = data.get("checkpoint_request") or {}
    n = len(cp.get("checklist") or [])
    if not n:
        return {"error": "No checklist on this run"}, 409
    state = [bool(x) for x in (checked or [])][:n]
    while len(state) < n:
        state.append(False)
    cp["checked"] = state
    data["checkpoint_request"] = cp
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return {"ok": True, "checked": sum(1 for x in state if x), "total": n}, 200

def pipeline_findings_review(run_id, findings_actions):
    """Persist developer choices (fix/accept) for each finding in the findings_review checkpoint,
    similar to pipeline_checklist. Does NOT approve — that is a separate step."""
    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400
    manifest = os.path.join(ROOT_DIR, "output", run_id, "run.json")
    if not os.path.isfile(manifest):
        return {"error": "Run not found"}, 404
    data = read_json(manifest) or {}
    cp = data.get("checkpoint_request") or {}
    fr = cp.get("findings_review") or []
    if not fr:
        return {"error": "No findings review on this run"}, 409
    findings_actions = findings_actions or []
    actions_map = {a.get("id"): a for a in findings_actions if a.get("id")}
    for item in fr:
        if item.get("id") in actions_map:
            act = actions_map[item["id"]]
            if act.get("action") in ("fix", "accept"):
                item["action"] = act["action"]
            if act.get("notes"):
                item["notes"] = str(act["notes"])[:2000]
    cp["findings_review"] = fr
    # Sync action decisions → run.json findings[] status so Findings Inventory reflects CP3 choices
    _action_map = {item.get("id"): item.get("action")
                   for item in fr if item.get("id") and item.get("action")}
    for finding in (data.get("findings") or []):
        fid = finding.get("id")
        if fid in _action_map:
            if _action_map[fid] == "accept":
                finding["status"] = "Accepted"
            elif _action_map[fid] == "fix":
                finding["status"] = "Pending Fix"
    # Recalculate quality_score to match updated statuses.
    # Formula (mirrors Phase C prompt): Critical-resolved×5, Major-open×8, Major-resolved×2,
    # Minor-open×3, Minor-resolved×1. "Accepted" counts as resolved; "Pending Fix" as open.
    _resolved = {"Resolved", "Accepted"}
    _qs = 100
    for _f in (data.get("findings") or []):
        _sev = (_f.get("severity") or "").strip().lower()
        _done = (_f.get("status") or "Open") in _resolved
        if _sev == "critical":
            if _done:
                _qs -= 5          # Critical-resolved ×5
        elif _sev == "major":
            _qs -= 2 if _done else 8
        elif _sev == "minor":
            _qs -= 1 if _done else 3
    data["quality_score"] = max(0, _qs)
    data["auto_corrections"] = data.get("auto_corrections", 0)  # preserve existing field
    data["checkpoint_request"] = cp
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return {"ok": True,
            "actioned": sum(1 for f in fr if f.get("action")),
            "total": len(fr)}, 200

def pipeline_decision(run_id, checkpoint, decision, notes, checklist_confirmed=False, selected_approach=""):
    if not SAFE_NAME.match(run_id or "") or decision not in ("approved", "adjusted", "rejected"):
        return {"error": "Invalid run id or decision"}, 400
    run_dir = os.path.join(ROOT_DIR, "output", run_id)
    if not os.path.isfile(os.path.join(run_dir, "run.json")):
        return {"error": "Run not found"}, 404
    # A checklist checkpoint (e.g. BTP prerequisites) cannot be approved until every item is ticked.
    _cpreq = (read_json(os.path.join(run_dir, "run.json")) or {}).get("checkpoint_request") or {}
    # Belt-and-suspenders: if the developer selected an approach that carries its OWN naming contract
    # but the persisted active contract doesn't match that selection (e.g. a swap POST was missed),
    # adopt the selected approach's contract so validation + Build lock the RIGHT mode's names.
    if selected_approach and (_cpreq.get("approach_options") or []):
        _so = next((o for o in _cpreq["approach_options"] if o.get("id") == selected_approach), None)
        _so_nc = (_so or {}).get("naming_contract") or []
        if _so_nc and _cpreq.get("selected_approach") != selected_approach:
            _cpreq["naming_contract"] = _so_nc
        elif _so is not None and not _so.get("recommended") and not _so_nc:
            # Override to an approach with no inline naming contract — don't lock the default mode's
            # names for it; clear so Build takes the names from the solution proposal instead.
            _cpreq["naming_contract"] = []
    if decision == "approved" and (_cpreq.get("checklist") or []) and not checklist_confirmed:
        return {"error": "Complete the pre-req checklist first to proceed."}, 409
    if decision == "approved" and (_cpreq.get("naming_contract") or []):
        if any(not _naming_ok(i.get("name"), i.get("created_in")) for i in _cpreq["naming_contract"]):
            return {"error": "Confirm a valid namespaced name for every custom object first."}, 409
    # Gate 2 Major enforcement — CP2 approval requires fix comments when Gate 2 found Majors
    if decision == "approved" and "CP2" in (checkpoint or ""):
        _g2_path = os.path.join(run_dir, "07-gate2-review.md")
        if os.path.isfile(_g2_path):
            with open(_g2_path, "r", encoding="utf-8") as _gf:
                _g2_content = _gf.read().lower()
            _g2_major_count = (
                _g2_content.count("| major |")
                + _g2_content.count("**major**")
                + _g2_content.count("severity: major")
            )
            if _g2_major_count > 0:
                _fc_check = [f for f in (_cpreq.get("code_files") or [])
                             if (f.get("comment") or "").strip()]
                if not _fc_check:
                    return {
                        "error": (
                            f"Gate 2 code review identified {_g2_major_count} Major finding(s). "
                            "Open 07-gate2-review.md, then add a fix comment for each Major "
                            "finding using the code files panel before approving CP2."
                        )
                    }, 409
    dec_dir = os.path.join(run_dir, "decisions")
    os.makedirs(dec_dir, exist_ok=True)
    cp_slug = re.sub(r"[^A-Za-z0-9]+", "_", checkpoint or "CP").strip("_") or "CP"
    _fc = [{"file": (f.get("file") or f.get("id") or ""), "comment": (f.get("comment") or "").strip()}
           for f in (_cpreq.get("code_files") or []) if (f.get("comment") or "").strip()]
    # Resolve selected approach details from the checkpoint_request's approach_options
    _approach_opts = _cpreq.get("approach_options") or []
    _sel_opt = next((o for o in _approach_opts if o.get("id") == selected_approach), None)
    selected_approach_label = (_sel_opt or {}).get("label", "")
    selected_is_btp = bool((_sel_opt or {}).get("is_btp", False))
    selected_is_sbpa = bool((_sel_opt or {}).get("is_sbpa", False))
    # Client-mandate / override audit — the developer may select a NON-recommended option (a valid
    # business case, e.g. the client mandates BTP even though RAP is the clean-core recommendation).
    # We record the override explicitly so run.json carries the trade-off, not just the outcome.
    _rec_opt = next((o for o in _approach_opts if o.get("recommended")), None)
    _mode_override = None
    if (_sel_opt and _rec_opt and not _sel_opt.get("recommended")
            and _sel_opt.get("id") != _rec_opt.get("id")):
        _mode_override = {
            "original_recommendation": _rec_opt.get("label", ""),
            "original_mode": _rec_opt.get("mode", ""),
            "selected_mode": _sel_opt.get("mode", ""),
            "selected_label": selected_approach_label,
            "mandated": bool(_sel_opt.get("mandated", False)),
            "cost_disclosure_required": selected_is_btp,
            "override_at": checkpoint,
            "by": "developer (webapp)",
        }
    record = {"checkpoint": checkpoint, "decision": decision, "notes": notes or "",
              "file_comments": _fc,
              "selected_approach": selected_approach,
              "selected_approach_label": selected_approach_label,
              "selected_is_btp": selected_is_btp,
              "selected_is_sbpa": selected_is_sbpa,
              "mode_override": _mode_override,
              # The human's locked custom-object names for the SELECTED approach — captured here so
              # Build reads them verbatim from the decision file (checkpoint_request is cleared on approval).
              "naming_contract": _cpreq.get("naming_contract") or [],
              "by": "developer (webapp)", "date": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(dec_dir, cp_slug + ".json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    _rj = read_json(os.path.join(run_dir, "run.json")) or {}
    MCP.audit("pipeline_decision", {
        "run": run_id, "checkpoint": checkpoint, "decision": decision,
        "notes": (notes or "")[:200],
        "selected_approach": selected_approach_label or selected_approach or "",
        "is_btp": selected_is_btp,
        "mode_override": bool(_mode_override),
        "file_comments": len(_fc),
        "quality_score": _rj.get("quality_score"),
        "by": "developer (webapp)"
    })
    if decision == "rejected" and not (notes or "").strip():
        return {"ok": True, "resumed": False,
                "message": "Rejection recorded. Add notes and resume manually when ready."}, 200
    # Optimistic update: reflect the decision in run.json immediately so the Workflow
    # Explorer clears the checkpoint panel and advances the moment the developer submits.
    # The headless engine then continues and overwrites with the real step results.
    if decision in ("approved", "adjusted"):
        try:
            manifest = os.path.join(run_dir, "run.json")
            data = read_json(manifest) or {}
            if not any(a.get("checkpoint") == checkpoint and a.get("date") == record["date"]
                       for a in data.get("human_approvals", [])):
                data.setdefault("human_approvals", []).append(record)
            if _mode_override:
                data["mode_override"] = _mode_override
            data["checkpoint_request"] = None
            for s in data.get("steps", []):
                if s.get("status") == "AWAITING_APPROVAL":
                    s["status"] = "PASS"
                    nt = ("Developer %s (%s). %s" % (decision, record["date"], notes or "")).strip()
                    s["detail"] = ((s.get("detail") or "") + " | " + nt).strip(" |")
                    break
            data["status"] = "in_progress"
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass
    _fc_txt = ("Per-file change requests from the code review:\n" + "\n".join(
        "- %s: %s" % (c["file"], c["comment"]) for c in _fc) + "\n") if _fc else ""
    # Resolve the FD path from the run manifest so phase prompts can reference it.
    try:
        _run_data = read_json(os.path.join(run_dir, "run.json")) or {}
        fd_path = _run_data.get("fd_source") or ("run:" + run_id)
    except Exception:
        fd_path = "run:" + run_id
    # Dispatch to the scoped phase prompt for this checkpoint. Each phase spawns a fresh
    # process with minimal context — only reading what that phase actually needs.
    phase = _CHECKPOINT_PHASE.get(checkpoint)
    if phase == "B":
        prompt = _phase_b_prompt(run_id, fd_path, decision, notes or "", _fc_txt, cp_slug,
                                 selected_approach, selected_approach_label, selected_is_btp,
                                 selected_is_sbpa)
    elif phase == "C":
        cp1_slug = re.sub(r"[^A-Za-z0-9]+", "_", "CP1 · Solution approval").strip("_")
        cp1_dec = read_json(os.path.join(run_dir, "decisions", cp1_slug + ".json")) or {}
        _c_is_sbpa = cp1_dec.get("selected_is_sbpa", False)
        prompt = _phase_c_prompt(run_id, fd_path, decision, notes or "", _fc_txt, cp_slug,
                                 is_sbpa=_c_is_sbpa)
    elif phase == "D":
        # Read CP1 decision to get the selected approach's BTP and SBPA flags
        cp1_slug = re.sub(r"[^A-Za-z0-9]+", "_", "CP1 · Solution approval").strip("_")
        cp1_dec = read_json(os.path.join(run_dir, "decisions", cp1_slug + ".json")) or {}
        _d_is_btp = cp1_dec.get("selected_is_btp", False)
        _d_is_sbpa = cp1_dec.get("selected_is_sbpa", False)
        prompt = _phase_d_prompt(run_id, fd_path, decision, notes or "", cp_slug,
                                 _d_is_btp, _d_is_sbpa)
    elif phase == "E":
        prompt = _phase_e_prompt(run_id, decision, notes or "")
    else:
        # Fallback for any unrecognised checkpoint (forward-compat with future checkpoints).
        prompt = (
            "HEADLESS PIPELINE RESUME for run %s (triggered from the S4PC Catalyst webapp).\n"
            "The developer decided '%s' at checkpoint '%s'. Notes: %s\n%s"
            "The decision is saved at output/%s/decisions/%s.json. "
            "Read output/%s/run.json and the deliverables written so far, append this decision "
            "to human_approvals (skip if an identical entry already exists), clear "
            "checkpoint_request, apply any requested adjustments, and continue the pipeline "
            "from the step AFTER that checkpoint — honoring later checkpoints the same headless "
            "way. Do not redo completed steps." % (
                run_id, decision, checkpoint, (notes or "—"), _fc_txt,
                run_id, cp_slug, run_id))
    job_id, err = _spawn_claude(prompt, "run:" + run_id, "resume", run_id=run_id)
    if err:
        return {"error": err}, 503
    return {"ok": True, "resumed": True, "job": job_id,
            "message": "Decision recorded — pipeline resuming."}, 200

def pipeline_jobs():
    out = []
    with JOBS_LOCK:
        items = list(JOBS.items())
    for job_id, j in items:
        code = j["proc"].poll()
        tail = ""
        try:
            with open(j["log"], "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-3000:]
        except Exception:
            pass
        out.append({"job": job_id, "kind": j["kind"], "fd": j["fd"], "started": j["started"],
                    "status": "running" if code is None else ("done" if code == 0 else "failed (exit %s)" % code),
                    "log_tail": tail})
    return {"engine": engine_binary(), "jobs": sorted(out, key=lambda x: x["started"], reverse=True)}

def pipeline_log(job_id):
    """Return the full log file for a given job as plain text."""
    if not re.match(r'^[A-Za-z0-9]{1,40}$', job_id or ''):
        return None, 404
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return None, 404
    try:
        with open(job['log'], 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read(), 200
    except OSError:
        return '', 200

def pipeline_delete(run_id):
    """Delete a run folder (and its usage record). Refuses while the run is still executing."""
    if not SAFE_NAME.match(run_id or ""):
        return {"error": "Invalid run id"}, 400
    run_dir = os.path.join(ROOT_DIR, "output", run_id)
    if not os.path.isdir(run_dir):
        return {"error": "Run not found"}, 404
    data = read_json(os.path.join(run_dir, "run.json")) or {}
    if data.get("status") in ("running", "in_progress"):
        return {"error": "This run is still executing — wait for it to finish or reach a checkpoint before deleting."}, 409
    try:
        shutil.rmtree(run_dir)
    except OSError as exc:
        return {"error": "Could not delete: %s" % exc}, 500
    try:                                            # drop its token-usage record too
        with USAGE_LOCK:
            u = _load_usage()
            if run_id in u.get("runs", {}):
                del u["runs"][run_id]
                with open(USAGE_PATH, "w", encoding="utf-8") as fh:
                    json.dump(u, fh, indent=2)
    except Exception:
        pass
    MCP.audit("run_deleted", {"run": run_id})
    return {"ok": True, "deleted": run_id}, 200


def experience_export():
    """Export net-new experience entries from catalog.db back to experience_db.json seed.

    Net-new = entries in catalog.db whose ID is not already in the JSON seed.
    Flow: one developer records lessons during pipeline runs → they click Export →
    review the diff → git commit + push → teammates pull → their next DB migration
    picks up the shared lessons automatically.
    """
    seed_path = os.path.join(MCP_DIR, "catalog", "experience_db.json")
    live_entries = _catalog_db.load_experience().get("entries", [])
    try:
        with open(seed_path, encoding="utf-8") as fh:
            seed = json.load(fh)
    except FileNotFoundError:
        seed = {"_meta": {}, "entries": []}
    existing_ids = {e.get("id") for e in seed.get("entries", [])}
    new_entries = [e for e in live_entries if e.get("id") and e["id"] not in existing_ids]
    if not new_entries:
        return {"ok": True, "exported": 0, "entries": [],
                "message": "No new entries — seed is already up to date with your local DB."}, 200
    seed.setdefault("entries", []).extend(new_entries)
    with open(seed_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(seed, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {
        "ok": True,
        "exported": len(new_entries),
        "entries": new_entries,
        "message": "%d new lesson(s) written to experience_db.json. Review then: git add mcp-server/catalog/experience_db.json && git commit -m 'Export %d experience entries' && git push" % (len(new_entries), len(new_entries)),
    }, 200


_INDEX_REBUILD_LOCK = threading.Lock()

def _rebuild_index_bg(reason=""):
    """Rebuild the Digital Brain vector index in a background thread (non-blocking).
    Uses a lock so concurrent triggers (e.g. two phases finishing close together)
    collapse into one rebuild instead of running in parallel.
    """
    def _run():
        if not _INDEX_REBUILD_LOCK.acquire(blocking=False):
            return  # another rebuild already running — skip
        try:
            build_script = os.path.join(MCP_DIR, "vector", "build_index.py")
            if not os.path.isfile(build_script):
                return
            result = subprocess.run(
                [sys.executable, build_script],
                cwd=ROOT_DIR, capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                print("[brain] Vector index rebuilt%s" % (" (%s)" % reason if reason else ""))
            else:
                print("[brain] Index rebuild failed: %s" % (result.stderr or "")[:200])
        except Exception as exc:
            print("[brain] Index rebuild error: %s" % exc)
        finally:
            _INDEX_REBUILD_LOCK.release()
    threading.Thread(target=_run, daemon=True).start()


_GRAPH_REBUILD_LOCK = threading.Lock()

def _rebuild_graph_bg(reason=""):
    """Rebuild the Digital Brain object graph in a background thread (non-blocking)."""
    def _run():
        if not _GRAPH_REBUILD_LOCK.acquire(blocking=False):
            return
        try:
            build_script = os.path.join(MCP_DIR, "graph", "build_graph.py")
            if not os.path.isfile(build_script):
                return
            result = subprocess.run(
                [sys.executable, build_script],
                cwd=ROOT_DIR, capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                print("[brain] Object graph rebuilt%s" % (" (%s)" % reason if reason else ""))
            else:
                print("[brain] Graph rebuild failed: %s" % (result.stderr or "")[:200])
        except Exception as exc:
            print("[brain] Graph rebuild error: %s" % exc)
        finally:
            _GRAPH_REBUILD_LOCK.release()
    threading.Thread(target=_run, daemon=True).start()


def brain_rebuild():
    """Trigger immediate background rebuild of both vector index and object graph."""
    _rebuild_index_bg("manual rebuild from UI")
    threading.Timer(2.0, lambda: _rebuild_graph_bg("manual rebuild from UI")).start()
    return {"ok": True, "message": "Rebuild started — vector index and object graph queuing in background. Refresh status in ~60s."}, 200


def catalog_sync(hub_api_key, dry_run=False, rebuild=False):
    """Run sync_hub.py with the API key passed only as an env var — never written to disk or logged."""
    key = (hub_api_key or "").strip()
    if not key:
        return {"error": "API key is required."}, 400
    sync_script = os.path.join(MCP_DIR, "catalog", "sync_hub.py")
    if not os.path.isfile(sync_script):
        return {"error": "sync_hub.py not found at %s" % sync_script}, 500
    env = os.environ.copy()
    env["SAP_HUB_API_KEY"] = key          # key lives only in this subprocess env
    cmd = [sys.executable, sync_script]
    if dry_run:
        cmd.append("--dry-run")
    elif rebuild:
        cmd.append("--rebuild")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        output = result.stdout
        if result.returncode != 0:
            output += ("\n" + result.stderr) if result.stderr else ""
        return {"ok": result.returncode == 0, "output": output.strip()}, 200
    except subprocess.TimeoutExpired:
        return {"error": "Sync timed out after 3 minutes."}, 500
    except Exception as exc:
        return {"error": str(exc)}, 500


# Playground: which MCP tools the UI may invoke directly
UI_ALLOWED_TOOLS = {
    # ── clean-core gates ──────────────────────────────────────────────────────
    "check_object_release_state", "abap_cloud_lint", "extensibility_advisor",
    # ── catalog search ────────────────────────────────────────────────────────
    "search_released_apis", "search_released_badis",
    # ── observability / connectivity ──────────────────────────────────────────
    "guardrails_status", "observability_snapshot",
    "sap_connection_test", "odata_get_metadata", "odata_query",
    # ── Digital Brain: Layer 1 (Live Object Graph) ────────────────────────────
    "get_object_graph", "get_area_map", "sync_object_graph",
    # ── Digital Brain: Layer 2+3 (Knowledge Vectors + Experience Graph) ───────
    "semantic_search", "find_similar_delivery", "rebuild_vector_index",
}

def run_tool(name, arguments):
    if name not in UI_ALLOWED_TOOLS or name not in MCP.TOOLS:
        return {"error": "Tool not available from the UI: %s" % name}, 400
    with _LOCK:
        UI_STATS["tool_runs"][name] = UI_STATS["tool_runs"].get(name, 0) + 1
    started = time.time()
    try:
        payload = MCP.TOOLS[name]["handler"](arguments or {})
        ok = True
    except MCP.GuardrailViolation as exc:
        payload = {"guardrail_blocked": True, "reason": str(exc)}
        ok = False
    except Exception as exc:
        payload = {"error": str(exc)}
        ok = False
    duration = (time.time() - started) * 1000
    MCP.audit("ui_tool_call", {"tool": name, "arguments": arguments, "ok": ok, "duration_ms": int(duration)})
    MCP.record_call(name, duration, ok)
    return {"tool": name, "ok": ok, "duration_ms": int(duration), "result": payload}, 200

# ------------------------------------------------------------------ server ---

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript",
        ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}

class Handler(BaseHTTPRequestHandler):
    server_version = "S4PC-Catalyst/1.0"

    def log_message(self, fmt, *args):  # quiet console; audit has the details
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not ACCESS_PASSWORD:
            return True                        # no password set → open (local single-user mode)
        import base64, hmac
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(hdr[6:]).decode("utf-8", "replace").partition(":")
                if hmac.compare_digest(user, ACCESS_USER) and hmac.compare_digest(pw, ACCESS_PASSWORD):
                    return True
            except Exception:
                pass
        return False

    def _auth_challenge(self):
        body = b'{"error":"Authentication required"}'
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="S4PC Catalyst"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if self.path.split("?")[0] == "/favicon.ico":   # silence the browser's automatic favicon 404
            self.send_response(204)
            self.end_headers()
            return
        if not self._authorized():
            return self._auth_challenge()
        with _LOCK:
            UI_STATS["requests"] += 1
        path = self.path.split("?")[0]
        try:
            if path.startswith("/api/"):
                return self._api_get(path)
            return self._static(path)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def do_POST(self):
        if not self._authorized():
            return self._auth_challenge()
        with _LOCK:
            UI_STATS["requests"] += 1
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        try:
            if path == "/api/tool":
                payload, code = run_tool(body.get("tool", ""), body.get("arguments") or {})
                return self._send(code, payload)
            if path == "/api/fd-upload":
                if body.get("data_b64") is not None:       # binary upload → extract text
                    payload, code = save_input_document(body.get("name", ""), body.get("data_b64", ""))
                else:                                       # pasted/typed text
                    payload, code = save_input(body.get("name", ""), body.get("content", ""))
                return self._send(code, payload)
            if path == "/api/pipeline/start":
                payload, code = pipeline_start(body.get("fd", ""))
                return self._send(code, payload)
            if path == "/api/pipeline/decision":
                payload, code = pipeline_decision(body.get("run", ""), body.get("checkpoint", ""),
                                                  body.get("decision", ""), body.get("notes", ""),
                                                  body.get("checklist_confirmed", False),
                                                  body.get("selected_approach", ""))
                return self._send(code, payload)
            if path == "/api/pipeline/checklist":
                payload, code = pipeline_checklist(body.get("run", ""), body.get("checked", []))
                return self._send(code, payload)
            if path == "/api/pipeline/findings-review":
                payload, code = pipeline_findings_review(body.get("run", ""), body.get("findings", []))
                return self._send(code, payload)
            if path == "/api/pipeline/naming":
                payload, code = pipeline_naming(body.get("run", ""), body.get("names", {}),
                                                body.get("contract"), body.get("selected_approach"))
                return self._send(code, payload)
            if path == "/api/pipeline/comments":
                payload, code = pipeline_comments(body.get("run", ""), body.get("comments", {}))
                return self._send(code, payload)
            if path == "/api/pipeline/delete":
                payload, code = pipeline_delete(body.get("run", ""))
                return self._send(code, payload)
            if path == "/api/btp/connections":
                payload, code = btp_connection_add(body)
                return self._send(code, payload)
            if path == "/api/btp/connections/update":
                payload, code = btp_connection_update_one(body)
                return self._send(code, payload)
            if path == "/api/btp/connections/delete":
                payload, code = btp_connection_delete_one(body)
                return self._send(code, payload)
            if path == "/api/btp/connection/test":
                payload, code = btp_connection_test_one(body)
                return self._send(code, payload)
            if path == "/api/btp/run-deploy":
                payload, code = pipeline_btp_deploy(body.get("run") or "",
                                                    force_rebuild=bool(body.get("force_rebuild", False)))
                return self._send(code, payload)
            if path == "/api/btp/cf-cmd":
                payload, code = btp_cf_run(body.get("run") or "",
                                            body.get("args") or [])
                return self._send(code, payload)
            if path == "/api/btp/test-credentials":
                payload, code = btp_test_credentials(body)
                return self._send(code, payload)
            if path == "/api/btp/orgs":
                payload, code = btp_discover_orgs(body)
                return self._send(code, payload)
            if path == "/api/btp/spaces":
                payload, code = btp_discover_spaces(body)
                return self._send(code, payload)
            if path == "/api/btp/connection":
                payload, code = btp_connection_set(body)
                return self._send(code, payload)
            if path == "/api/btp/test":
                payload, code = btp_test()
                return self._send(code, payload)
            if path == "/api/brain/rebuild":
                payload, code = brain_rebuild()
                return self._send(code, payload)
            if path == "/api/experience/export":
                payload, code = experience_export()
                return self._send(code, payload)
            if path == "/api/catalog/sync":
                payload, code = catalog_sync(
                    body.get("hub_api_key", ""),
                    dry_run=bool(body.get("dry_run", False)),
                    rebuild=bool(body.get("rebuild", False)),
                )
                return self._send(code, payload)
            if path == "/api/shutdown":
                self._send(200, {"ok": True, "message": "Shutting down"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._send(404, {"error": "Unknown endpoint"})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def _api_get(self, path):
        if path == "/api/run-file":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            payload, code = run_file((qs.get("run") or [""])[0], (qs.get("file") or [""])[0])
            return self._send(code, payload)
        if path == "/api/btp/deploy-status":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            payload, code = btp_deploy_status((qs.get("job") or [""])[0])
            return self._send(code, payload)
        if path == "/api/btp/cf-logs":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            run  = (qs.get("run")  or [""])[0]
            app  = (qs.get("app")  or ["po-viewer-srv"])[0]
            payload, code = btp_cf_run(run, ["logs", app, "--recent"])
            return self._send(code, payload)
        if path.startswith("/api/pipeline/log/"):
            job_id = path[len("/api/pipeline/log/"):]
            content, code = pipeline_log(job_id)
            if code == 404:
                return self._send(404, {"error": "Job not found"})
            return self._send(200, content or "", "text/plain")
        routes = {
            "/api/brain/status": brain_status,
            "/api/runs": list_runs,
            "/api/inputs": list_inputs,
            "/api/pipeline/jobs": pipeline_jobs,
            "/api/summary": summary,
            "/api/usage": usage_data,
            "/api/skills": lambda: {"skills": list_skills()},
            "/api/workflows": lambda: {"workflows": list_workflows()},
            "/api/mcp": mcp_inventory,
            "/api/agents": lambda: read_json(os.path.join(APP_DIR, "data", "agents.json"), {"agents": []}),
            "/api/admin": admin_data,
            "/api/settings": settings_data,
            "/api/btp/connections": btp_connections_get,
            "/api/btp/connection": btp_connection,
            "/api/catalog/apis":       _catalog_db.load_apis,
            "/api/catalog/badis":      _catalog_db.load_badis,
            "/api/catalog/cds":        _catalog_db.load_cds_views,
            "/api/catalog/lint":       _catalog_db.load_lint_rules,
            "/api/catalog/experience": _catalog_db.load_experience,
        }
        fn = routes.get(path)
        if not fn:
            return self._send(404, {"error": "Unknown endpoint"})
        return self._send(200, fn())

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        # path traversal protection
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            return self._send(403, {"error": "Forbidden"})
        full = os.path.join(UI_DIR, rel)
        if not os.path.isfile(full):
            return self._send(404, "Not found", "text/plain")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            data = fh.read()
        self._send(200, data, MIME.get(ext, "application/octet-stream"))

def _watchdog():
    """Daemon thread: marks stalled runs (running/in_progress with no active job > 10 min) as error."""
    while True:
        time.sleep(60)
        try:
            out_dir = os.path.join(ROOT_DIR, "output")
            if not os.path.isdir(out_dir):
                continue
            now = time.time()
            for name in os.listdir(out_dir):
                manifest = os.path.join(out_dir, name, "run.json")
                data = read_json(manifest)
                if not data:
                    continue
                status = data.get("status", "")
                if status not in ("running", "in_progress"):
                    continue
                run_id = data.get("id") or name
                with JOBS_LOCK:
                    has_active = any(
                        j.get("run") == run_id and j["proc"].poll() is None
                        for j in JOBS.values()
                    )
                if has_active:
                    continue
                # If the job IS known to us and already exited, flip immediately.
                # If it's unknown (server restarted mid-run), wait 10 min to be safe.
                with JOBS_LOCK:
                    job_known_and_dead = any(
                        j.get("run") == run_id and j["proc"].poll() is not None
                        for j in JOBS.values()
                    )
                if not job_known_and_dead:
                    try:
                        mtime = os.path.getmtime(manifest)
                        if now - mtime < 600:
                            continue
                    except OSError:
                        continue
                data["status"] = "error"
                data["engine_note"] = "Run stalled — no active engine process. Re-run or investigate the logs."
                try:
                    with open(manifest, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2, ensure_ascii=False)
                except Exception:
                    pass
        except Exception:
            pass

def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d" % (HOST, PORT)
    print("S4PC Catalyst running at %s  (mode=%s, python=%s, %s)" % (
        url, MCP.MODE, sys.version.split()[0], sys.platform))
    print("Stop with Ctrl+C, SHUTDOWN.cmd (Windows), or POST /api/shutdown")
    if os.environ.get("S4PC_UI_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # Rebuild vector index on startup (catches any runs/experience added since last session).
    threading.Timer(3.0,  lambda: _rebuild_index_bg("startup")).start()
    threading.Timer(15.0, lambda: _rebuild_graph_bg("startup")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    print("Stopped.")

if __name__ == "__main__":
    threading.Thread(target=_watchdog, daemon=True).start()
    main()
