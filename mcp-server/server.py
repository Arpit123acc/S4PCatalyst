#!/usr/bin/env python3
"""
s4pc-mcp — S/4HANA Cloud Public Edition clean-core MCP server.

Zero-dependency (Python 3.9+ stdlib only) MCP server over stdio.
Designed for locked-down corporate machines: no pip installs, no API keys.
The LLM runtime is Claude Code itself; this server only provides governed,
provenance-tagged SAP knowledge and (optionally) read-only tenant access.

Anti-hallucination design:
  * Every tool response carries "source" and "verified" fields.
  * Catalog data is a SEED — responses always name the authoritative source
    (Business Accelerator Hub, Custom Logic app, ADT Released Objects).
  * Unknown objects return verdict NOT_VERIFIED, never a guess.

Guardrails:
  * offline mode by default — no network at all.
  * live mode: GET-only OData against an allowlist, capped $top, rate limit,
    TLS always verified, credentials only from environment variables.
  * Audit log (JSONL) + metrics for every tool call; secrets redacted.
"""

import json
import os
import re
import sys
import hmac
import time
import base64
import hashlib
import threading
import traceback
import urllib.request
import urllib.error
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROTOCOL_VERSION = "2024-11-05"

# ---------------------------------------------------------------- config ---

def _load_json(rel_path, default=None):
    path = os.path.join(BASE_DIR, rel_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}

CONFIG = _load_json("config.json")

# Load catalogs from SQLite (auto-migrates from JSON on first run)
_CATALOG_DIR = os.path.join(BASE_DIR, "catalog")
sys.path.insert(0, _CATALOG_DIR)
import db as _catalog_db  # noqa: E402  (after sys.path setup)
CATALOG_APIS = _catalog_db.load_apis()
CATALOG_BADIS = _catalog_db.load_badis()
CATALOG_CDS   = _catalog_db.load_cds_views()
LINT_RULES    = _catalog_db.load_lint_rules()

# Experience store backend: sqlite (default — local/EC2, writes catalog.db + git seed) or
# postgres (serverless — writes the shared Aurora DB, same PGVECTOR_DSN as the brain, so
# record_experience works on Lambda's read-only filesystem). Default keeps the local/EC2 POC
# byte-for-byte; postgres is opt-in via EXPERIENCE_BACKEND. Falls back to the sqlite seed if
# postgres can't be reached, so the server always boots. (log_stderr isn't defined yet here.)
_EXP_BACKEND = os.environ.get("EXPERIENCE_BACKEND", "sqlite").lower()
_exp_store = _catalog_db
if _EXP_BACKEND == "postgres":
    try:
        import experience_pg as _exp_store          # catalog/ already on sys.path
        EXPERIENCE = _exp_store.load_experience()
    except Exception as _eexc:
        sys.stderr.write("[s4pc-mcp] EXPERIENCE_BACKEND=postgres unavailable (%s) — "
                         "falling back to sqlite seed (read-mostly)\n" % _eexc)
        sys.stderr.flush()
        _EXP_BACKEND, _exp_store = "sqlite", _catalog_db
        EXPERIENCE = _catalog_db.load_experience()
else:
    EXPERIENCE = _catalog_db.load_experience()

# Authoritative documentation sources — cite these in every solution.
REFERENCE_LINKS = {
    "sap_business_accelerator_hub": {
        "url": "https://api.sap.com/products/SAPS4HANACloud/apis/all",
        "use_for": "Released APIs (OData/SOAP/events), CDS views, integration content — the technical reference for everything consumed from S/4HANA Cloud Public Edition."},
    "sap_discovery_center": {
        "url": "https://discovery-center.cloud.sap/viewServices",
        "use_for": "SAP BTP services: capabilities, service plans and PRICING/COSTING. Every side-by-side proposal must link the Discovery Center page of each BTP service it uses."},
    "sap_help_s4hana_cloud": {
        "url": "https://help.sap.com/docs/SAP_S4HANA_CLOUD",
        "use_for": "S/4HANA Cloud Public Edition docs root — released objects, configuration objects, released applications and all other released-object catalogs, key-user extensibility guides, release notes (What's New)."},
    "released_cds_views_list": {
        "url": "https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/5418de55938d1d22e10000000a44147b.html",
        "use_for": "AUTHORITATIVE list of RELEASED CDS views (VDM) for S/4HANA Cloud Public Edition. This is where a CDS view's release state (C1 'Use in Cloud Development') is confirmed — cite this page for every CDS view in a deliverable (alongside ADT Released Objects / View Browser in-tenant)."},
    "released_badis_list": {
        "url": "https://help.sap.com/docs/SAP_S4HANA_CLOUD/a630d57fc5004c6383e7a81efee7a8bb/7364d84e76e745df91f1413339a7e293.html",
        "use_for": "AUTHORITATIVE List of BAdIs (Business Add-Ins) released for S/4HANA Cloud Public Edition. Confirm a BAdI's availability here (alongside the Custom Logic app / ADT Released Objects in-tenant)."},
    "fiori_apps_library": {
        "url": "https://fioriappslibrary.hana.ondemand.com",
        "use_for": "Standard Fiori apps — check before building anything (fit-to-standard first)."},
    "extensibility_explorer": {
        "url": "https://extensibilityexplorer.cfapps.eu10.hana.ondemand.com/",
        "use_for": "SAP's interactive guide for choosing the extensibility pattern."},
    "cap_docs": {
        "url": "https://cap.cloud.sap/docs/",
        "fetch_for": ["cap"],
        "use_for": "SAP Cloud Application Programming Model (CAP/CAPM) — authoritative docs, best practices and APIs for the CAP service in a side-by-side solution. MUST follow for CAP data models, services, handlers and deployment."},
    "ui5_docs": {
        "url": "https://ui5.sap.com/",
        "fetch_for": ["ui5"],
        "use_for": "SAPUI5 — authoritative docs, controls and best practices for the Fiori/UI5 app in a side-by-side solution. MUST follow for UI5 views, controllers and app structure."},
    "nodejs_docs": {
        "url": "https://nodejs.org/docs/latest/api/",
        "fetch_for": ["cap"],
        "use_for": "Node.js API reference — follow for the CAP (Node.js) runtime and any Node code."},
    "javascript_ref": {
        "url": "https://www.w3schools.com/js/default.asp",
        "fetch_for": ["ui5", "cap"],
        "use_for": "JavaScript reference for UI5/CAP client- and server-side code."},
    "html_ref": {
        "url": "https://www.w3schools.com/html/",
        "fetch_for": ["ui5"],
        "use_for": "HTML reference for UI5/Fiori view markup and any custom HTML."},
    "css_ref": {
        "url": "https://www.w3schools.com/css/",
        "fetch_for": ["ui5"],
        "use_for": "CSS reference for UI5/Fiori styling and any custom CSS."},
    "npm_registry": {
        "url": "https://www.npmjs.com/package/npm",
        "fetch_url": "https://registry.npmjs.org/",
        "fetch_for": ["cap", "ui5"],
        "use_for": "npm packages — verify names/versions used in CAP/UI5 builds. FETCH the JSON registry at https://registry.npmjs.org/<package> (NOT the npmjs.com web page, which blocks automated requests); cite the npmjs.com page for humans."},
    "sap_community": {
        "url": "https://community.sap.com/",
        "cite_only": True,
        "use_for": "SAP Community — latest releases, blogs and discussions on CAP/UI5/BTP/ABAP Cloud. CITE-ONLY: the site blocks automated fetches (anti-bot), so link it for humans — do NOT WebFetch it."},
}

# Object-type -> which REFERENCE_LINKS to READ (WebFetch) when building side-by-side code.
# The developer fetches the set matching what it is building (CAP service, UI5 app, or both).
# NOTE: for npm_registry, fetch its 'fetch_url' (registry.npmjs.org JSON), not the web 'url'.
# sap_community is intentionally NOT here — it is cite-only (anti-bot blocks automated fetch).
FETCH_DOCS_BY_OBJECT = {
    "cap":  ["cap_docs", "nodejs_docs", "npm_registry", "javascript_ref"],
    "ui5":  ["ui5_docs", "javascript_ref", "html_ref", "css_ref", "npm_registry"],
    "capm": ["cap_docs", "nodejs_docs", "npm_registry", "javascript_ref"],
}

# Doc sets mirrored into the Public Cloud Brain by scripts/webdocs_ingest.py, and
# therefore searchable offline via search_brain(source_system="developer_docs") --
# these key names are also the brain's `deliverable_type`, so they can be filtered
# on directly.
#
# Search the brain FIRST for anything listed here. Fetching ui5.sap.com cannot work:
# every topic URL is a '#/topic/...' fragment, and a fragment is never sent to the
# server, so all 1000+ pages return the same ~2 KB JavaScript shell. The fetch
# SUCCEEDS and grounds nothing, so the usual "if the fetch fails, cite the URL"
# fallback never triggers. That is how finding F-17 (OData apostrophe quoting)
# reached a deliverable. It also matters on Bedrock, where web tools are unavailable.
#
# Anything NOT listed here still needs a live fetch (or is cite-only).
BRAIN_MIRRORED_DOCS = {
    "ui5_docs":    "SAP UI5 + Fiori Elements — brain only; ui5.sap.com is an SPA and cannot be fetched",
    "cap_docs":    "CAP (cap.cloud.sap) — mirrored; fetch only for content newer than the last harvest",
    "nodejs_docs": "Node.js API docs — mirrored; fetch only for content newer than the last harvest",
}

# Deterministic per-mode facts: tooling, skillset, cost class, doc links.
MODE_PROFILES = {
    "key_user": {
        "label": "Key User Extensibility (in-app)",
        "tooling": "Custom Fields app, Custom Logic app (released BAdIs), Adapt UI, Custom CDS Views, Custom Analytical Queries, Maintain Form Templates (Adobe Forms), Manage Workflows (Flexible Workflow)",
        "skillset": "Functional consultant / key user — no IDE",
        "cost_class": "INCLUDED — part of the S/4HANA Cloud subscription, no additional license or infrastructure",
        "docs": ["sap_help_s4hana_cloud", "extensibility_explorer"],
        "typical_feasibility": "High for fields, validations/defaults on released BAdIs, simple analytics, forms, delivered workflow scenarios; NOT feasible for complex/reusable logic or non-covered scenarios."},
    "developer": {
        "label": "Developer Extensibility (ABAP Cloud / Embedded Steampunk)",
        "tooling": "Eclipse with ADT against the dev tenant: RAP, CDS, developer BAdI implementations, Application Jobs, ABAP Unit; ships via git-based software components (Manage Software Components)",
        "skillset": "ABAP Cloud developer (RAP/CDS)",
        "cost_class": "INCLUDED — part of the subscription (3-system landscape); cost is developer effort only",
        "docs": ["sap_business_accelerator_hub", "sap_help_s4hana_cloud"],
        "typical_feasibility": "High for custom business objects/apps on in-tenant data and complex logic — but ONLY over C1-released objects; not feasible if required objects are unreleased."},
    "side_by_side": {
        "label": "Side-by-Side Extensibility (SAP BTP)",
        "tooling": "CAP (Node/Java) or ABAP Environment, UI5/Fiori via Build Work Zone, Integration Suite (CPI), Event Mesh / Advanced Event Mesh, SAP Build Process Automation — consuming released APIs + business events",
        "skillset": "BTP developer (CAP/UI5/integration)",
        "cost_class": "BTP CONSUMPTION COST — every service has its own pricing; get plans and estimates from SAP Discovery Center and put the cost line in the proposal",
        "docs": ["sap_discovery_center", "sap_business_accelerator_hub", "cap_docs", "ui5_docs",
                 "nodejs_docs", "javascript_ref", "html_ref", "css_ref", "npm_registry", "sap_community"],
        "typical_feasibility": "High for integrations, external users, independent lifecycle, non-covered workflow (SBPA), custom UX (CAP+UI5); overkill for logic a released BAdI does in 20 lines."},
}

GUARD = CONFIG.get("guardrails", {})
MODE = os.environ.get("S4PC_MODE", CONFIG.get("mode", {}).get("default", "offline")).lower()
ALLOW_WRITES = os.environ.get("S4PC_ALLOW_WRITES", "").lower() == "true" and GUARD.get("allow_writes", False)

# On AWS Lambda the deployment package (BASE_DIR) is read-only — observability must go to
# /tmp, the only writable path. Activates ONLY when AWS_LAMBDA_FUNCTION_NAME is set, so the
# EC2 / local / stdio POC paths keep their existing logs/ location byte-for-byte.
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    LOG_DIR = "/tmp/s4pc-logs"
    AUDIT_PATH = os.path.join(LOG_DIR, "audit.jsonl")
    METRICS_PATH = os.path.join(LOG_DIR, "metrics.json")
else:
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    AUDIT_PATH = os.path.join(BASE_DIR, CONFIG.get("observability", {}).get("audit_log", "logs/audit.jsonl"))
    METRICS_PATH = os.path.join(BASE_DIR, CONFIG.get("observability", {}).get("metrics_file", "logs/metrics.json"))
os.makedirs(LOG_DIR, exist_ok=True)

# --------------------------------------------------------- observability ---

_METRICS = {"started_at": None, "calls": {}, "errors": {}, "rate_limited": 0}

REDACT_KEYS = set(k.lower() for k in GUARD.get("redact_fields", []))

def _redact(obj):
    """Recursively mask secret-looking fields before anything is logged."""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if k.lower() in REDACT_KEYS else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj

# Identity of the HTTP caller being served on this thread, for the audit trail.
# Thread-local because the HTTP transport is threaded. stdio leaves it unset, which
# correctly reads as a same-host process rather than a network caller.
_CALLER = threading.local()

def _current_caller():
    return getattr(_CALLER, "name", None) or "local"

def audit(event, detail):
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "mode": MODE,
        "caller": _current_caller(),
        "detail": _redact(detail),
    }
    try:
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # observability must never break the server

def _flush_metrics():
    try:
        with open(METRICS_PATH, "w", encoding="utf-8") as fh:
            json.dump(_METRICS, fh, indent=2)
    except Exception:
        pass

def record_call(tool, duration_ms, ok):
    m = _METRICS["calls"].setdefault(tool, {"count": 0, "errors": 0, "total_ms": 0})
    m["count"] += 1
    m["total_ms"] += int(duration_ms)
    if not ok:
        m["errors"] += 1
        _METRICS["errors"][tool] = _METRICS["errors"].get(tool, 0) + 1
    _flush_metrics()

def log_stderr(msg):
    sys.stderr.write("[s4pc-mcp] %s\n" % msg)
    sys.stderr.flush()

# ------------------------------------------------------------- guardrails ---

_RATE_WINDOW = []

def rate_limit_ok():
    limit = GUARD.get("max_requests_per_minute", 30)
    now = time.time()
    while _RATE_WINDOW and now - _RATE_WINDOW[0] > 60:
        _RATE_WINDOW.pop(0)
    if len(_RATE_WINDOW) >= limit:
        _METRICS["rate_limited"] += 1
        return False
    _RATE_WINDOW.append(now)
    return True

class GuardrailViolation(Exception):
    pass


# ------------------------------------------------------- HTTP authentication ---
# The HTTP transport is unauthenticated unless S4PC_API_KEYS is set. Unset means
# "auth disabled", so the loopback + SSH-tunnel setup and the stdio pipeline path
# keep working untouched — but anything reachable by more than the tunnel MUST set
# it. See docs/brain-endpoint-setup.md.
#
#   S4PC_API_KEYS = "name:secret[:tool,tool,...];name2:secret2"
#
# Entries are ';'-separated, fields ':'-separated, the optional tool allowlist
# ','-separated. Omitting the allowlist grants every tool, so a client-facing key
# should always carry one — several tools read files or reach SAP/BTP.

def _parse_api_keys():
    entries = []
    for chunk in os.environ.get("S4PC_API_KEYS", "").split(";"):
        parts = [p.strip() for p in chunk.strip().split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        tools = None
        if len(parts) > 2 and parts[2]:
            tools = {t.strip() for t in parts[2].split(",") if t.strip()}
        entries.append((parts[0], parts[1], tools))
    return entries

def _authenticate(headers):
    """-> (ok, caller_name, allowed_tools|None). No keys configured -> auth disabled."""
    entries = _parse_api_keys()
    if not entries:
        return True, "anonymous", None
    presented = (headers.get("x-api-key") or "").strip()
    if not presented:
        auth = headers.get("Authorization", "")
        if auth[:7].lower() == "bearer ":
            presented = auth[7:].strip()
    if not presented:
        return False, None, None
    for name, secret, tools in entries:
        # compare_digest on every entry — no early exit, so timing does not leak
        # which key prefix was close.
        if hmac.compare_digest(presented, secret):
            return True, name, tools
    return False, None, None


# ------------------------------------------------------------ path containment ---
# file_probe and extract_docx take a caller-supplied path and return file contents.
# They exist to read pipeline inputs and deliverables, so they are confined to those
# directories: uncontained, they are arbitrary file read for anyone who can reach the
# transport — including, via /proc/<pid>/environ, the credentials the security model
# deliberately keeps out of files.
_REPO_ROOT = os.path.dirname(BASE_DIR)

def _file_roots():
    override = os.environ.get("S4PC_FILE_ROOTS", "")
    roots = override.split(os.pathsep) if override else [
        os.path.join(_REPO_ROOT, "input"), os.path.join(_REPO_ROOT, "output")]
    return [os.path.realpath(r) for r in roots if r.strip()]

def _safe_read_path(file_path, default_rel):
    """Resolve a caller-supplied path and confine it to the permitted roots.

    realpath() before the check, so a symlink planted inside a root cannot point out
    of it, and '..' cannot climb out.
    """
    if not file_path:
        file_path = os.path.join(_REPO_ROOT, *default_rel)
    resolved = os.path.realpath(file_path)
    for root in _file_roots():
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    audit("path_denied", {"requested": str(file_path)[:200]})
    raise GuardrailViolation(
        "Path is outside the permitted roots (%s). These tools read pipeline inputs "
        "and deliverables only; widen deliberately with S4PC_FILE_ROOTS."
        % os.pathsep.join(_file_roots()))


def require_live():
    if MODE != "live":
        raise GuardrailViolation(
            "Server is in OFFLINE mode — no network access. Set S4PC_MODE=live and the "
            "SAP_BASE_URL / SAP_COMM_USER / SAP_COMM_PASSWORD environment variables to enable "
            "read-only tenant access."
        )

def check_service_allowlisted(service):
    allow = GUARD.get("odata_service_allowlist", [])
    if service not in allow:
        raise GuardrailViolation(
            "Service '%s' is not on the allowlist. Allowed: %s. "
            "Edit mcp-server/config.json (guardrails.odata_service_allowlist) to extend — this is a "
            "deliberate human-in-the-loop step." % (service, ", ".join(allow))
        )

# --------------------------------------------------------------- SAP HTTP ---

def sap_get(path, query=None):
    """GET against the tenant with the communication user. Read-only by design."""
    require_live()
    if not rate_limit_ok():
        raise GuardrailViolation("Rate limit exceeded (max %s requests/min)." % GUARD.get("max_requests_per_minute", 30))
    base = os.environ.get("SAP_BASE_URL", "").rstrip("/")
    user = os.environ.get("SAP_COMM_USER", "")
    pwd = os.environ.get("SAP_COMM_PASSWORD", "")
    if not (base and user and pwd):
        raise GuardrailViolation("Live mode needs SAP_BASE_URL, SAP_COMM_USER, SAP_COMM_PASSWORD env vars.")
    if not base.startswith("https://"):
        raise GuardrailViolation("SAP_BASE_URL must be https:// — plaintext connections are blocked.")
    for blocked in GUARD.get("blocked_url_patterns", []):
        if blocked.lower() in path.lower():
            raise GuardrailViolation("URL pattern '%s' is blocked by guardrails." % blocked)
    url = base + path
    if query:
        url += ("&" if "?" in path else "?") + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, method="GET")
    token = base64.b64encode(("%s:%s" % (user, pwd)).encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    req.add_header("Accept", "application/json")
    audit("sap_http_get", {"url": url})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=GUARD.get("http_timeout_seconds", 30)) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "body": body, "elapsed_ms": int((time.time() - started) * 1000)}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")[:2000],
                "elapsed_ms": int((time.time() - started) * 1000)}

# ------------------------------------------------------------------ tools ---

def _search(items, text_fields, query, area=None):
    query = (query or "").strip().lower()
    out = []
    for item in items:
        if area and area.lower() not in (item.get("area") or "").lower():
            continue
        haystack = " ".join(str(item.get(f, "") or "") for f in text_fields).lower()
        if not query or all(tok in haystack for tok in query.split()):
            out.append(item)
    return out

def tool_search_released_apis(args):
    hits = _search(CATALOG_APIS.get("apis", []),
                   ["name", "title", "area", "notes", "key_entities"],
                   args.get("query", ""), args.get("area"))
    return {
        "verified": False,
        "source": "seed catalog (mcp-server/catalog/catalog.db · apis table), Hub-synced",
        "authoritative_source": CATALOG_APIS.get("_meta", {}).get("authoritative_sources"),
        "instruction_to_model": ("Treat these as candidates, not facts. Before putting an API into a design "
                                 "document, verify it on the SAP Business Accelerator Hub and confirm the "
                                 "communication scenario in the tenant. Where a field is null, say 'to be "
                                 "verified in tenant' — do NOT fill it from memory."),
        "count": len(hits),
        "results": hits,
    }

def tool_search_released_badis(args):
    hits = _search(CATALOG_BADIS.get("badis", []),
                   ["name", "title", "area", "business_context", "use_case"],
                   args.get("query", ""), args.get("area"))
    return {
        "verified": False,
        "source": "seed catalog (mcp-server/catalog/catalog.db · badis table), Hub-synced",
        "authoritative_source": CATALOG_BADIS.get("_meta", {}).get("authoritative_sources"),
        "instruction_to_model": ("BAdI availability varies by release and activated scope items. The ONLY proof a "
                                 "BAdI exists for this tenant is seeing it in the Custom Logic app or ADT Released "
                                 "Objects. If it is not there, tell the user it is not available — never invent "
                                 "BAdI names or parameters."),
        "count": len(hits),
        "results": hits,
    }

# classical table -> released replacement, built from the CDS catalog
def _table_map():
    # NOTE: `.get(key, default)` returns the STORED value when the key exists — and most synced
    # catalog entries carry "replaces": null — so the default is NOT applied and iteration explodes.
    # Always coerce with `or []`. This crash disabled check_object_release_state for every object.
    mapping = {}
    for view in (CATALOG_CDS.get("views") or []):
        for tab in (view.get("replaces") or []):
            if not tab:
                continue
            mapping.setdefault(str(tab).upper(), []).append(view.get("name"))
    return mapping

def tool_check_object_release_state(args):
    name = (args.get("object_name") or "").strip().upper()
    obj_type = (args.get("object_type") or "auto").lower()
    refs = {
        "released_cds_views_list": REFERENCE_LINKS["released_cds_views_list"]["url"],
        "released_badis_list": REFERENCE_LINKS["released_badis_list"]["url"],
        "s4hana_cloud_docs": REFERENCE_LINKS["sap_help_s4hana_cloud"]["url"],
        "business_accelerator_hub": REFERENCE_LINKS["sap_business_accelerator_hub"]["url"],
    }
    result = {
        "object_name": name,
        "verdict": "NOT_VERIFIED",
        "verified": False,
        # How the verdict was reached. A verdict is only as good as its evidence:
        #   rule                  — categorical clean-core rule (BAPI, classical table). Certain.
        #   catalog_hit           — exact match in catalog.db (Hub-synced). Strong.
        #   naming_heuristic_only — the NAME matches a released-object pattern and nothing else.
        #                           Zero catalog backing, so it cannot distinguish a real released
        #                           object missing from the catalog from a name that does not exist.
        #   none                  — nothing matched.
        "evidence": "none",
        "requires_tenant_confirmation": True,
        "source": "seed catalogs + released-VDM naming heuristic — offline check",
        "released_objects_reference": refs,
        "how_to_verify": [
            "CDS views: SAP Help 'Released CDS Views' list (%s), the View Browser app, or ADT > Released Objects (release contract C1 'Use in Cloud Development')." % refs["released_cds_views_list"],
            "BAdIs: SAP Help 'List of BAdIs' (%s), the Custom Logic app, or ADT Released Objects." % refs["released_badis_list"],
            "APIs: SAP Business Accelerator Hub (%s) + Communication Arrangements app." % refs["business_accelerator_hub"],
            "Any released / configuration / application object: S/4HANA Cloud docs root (%s)." % refs["s4hana_cloud_docs"],
        ],
    }
    # Categorical clean-core NO — BAPIs
    if name.startswith("BAPI_") or obj_type == "bapi":
        result.update(verdict="NOT_AVAILABLE", verified=True,
            evidence="rule", requires_tenant_confirmation=False,
            reason="BAPIs are not released in S/4HANA Cloud Public Edition. No exceptions.",
            alternative="Search released APIs for the same business object (search_released_apis).",
            source="SAP clean-core rule: only released APIs/BAdIs are consumable in Public Cloud")
        return result
    # Categorical clean-core NO — classical tables
    tmap = _table_map()
    if name in tmap:
        result.update(verdict="NOT_AVAILABLE", verified=True,
            evidence="rule", requires_tenant_confirmation=False,
            reason="Classical SAP table %s is not released for Public Cloud custom code." % name,
            alternative="Use released CDS view(s): %s (confirm C1 on the Released CDS Views list / ADT)." % ", ".join(tmap[name]))
        return result
    # Seed-catalog hits
    for api in (CATALOG_APIS.get("apis") or []):
        if (api.get("name") or "").upper() == name:
            result.update(verdict="LIKELY_RELEASED", evidence="catalog_hit", reason="Found in seed catalog of released APIs; confirm on the SAP Business Accelerator Hub.", details=api)
            return result
    for badi in (CATALOG_BADIS.get("badis") or []):
        if (badi.get("name") or "").upper() == name:
            result.update(verdict="LIKELY_RELEASED", evidence="catalog_hit", reason="Found in seed catalog of released BAdIs — availability still depends on your release/scope; confirm on the List of BAdIs.", details=badi)
            return result
    for view in (CATALOG_CDS.get("views") or []):
        if (view.get("name") or "").upper() == name:
            result.update(verdict="LIKELY_RELEASED", evidence="catalog_hit", reason="Found in seed catalog of released CDS views; confirm C1 on the Released CDS Views list / ADT Released Objects.", details=view)
            return result
    # Naming-convention heuristic — do NOT dead-end standard released VDM views as NOT_VERIFIED.
    # Private views (P_*) are generally not released; interface/consumption views are.
    if name.startswith("P_"):
        result.update(verdict="NOT_VERIFIED", verified=False,
            reason=("Name matches SAP's PRIVATE VDM view convention (P_*). Private views are generally NOT "
                    "released for cloud development — do not consume directly. Find the released interface "
                    "(I_*) or consumption (C_*) equivalent and confirm it on the Released CDS Views list."))
        return result
    if obj_type in ("cds", "cds_view", "view") or re.match(r"^[ICARE]_[A-Z0-9][A-Z0-9_]{2,}$", name):
        result.update(verdict="LIKELY_RELEASED", verified=False,
            evidence="naming_heuristic_only",
            reason=("Not in the catalog, but the name matches SAP's RELEASED VDM CDS-view convention "
                    "(I_ interface / C_ consumption / A_ / R_ / E_ views) — the standard clean-core way to read "
                    "S/4HANA data. Usable as a design placeholder, but this verdict rests on the NAME ALONE."),
            evidence_warning=("NAME-PATTERN MATCH ONLY — no catalog entry backs this. The same verdict is "
                              "returned for a view that does not exist, so it cannot be reported as 'released'. "
                              "Cross-check with semantic_search / get_object_graph: if neither returns this view "
                              "or a near neighbour, treat the NAME ITSELF as unconfirmed and say so explicitly "
                              "in the deliverable."),
            note=("A catalog miss is NOT 'unreleased'. Only NOT_AVAILABLE (BAPIs, classical tables, enhancement "
                  "points, Smart Forms) forces a redesign — but a heuristic-only LIKELY_RELEASED is a "
                  "MUST-confirm-in-tenant item and must appear in the tenant verification checklist."))
        return result
    # API-shaped names (OData / SOAP / event services) — confirm on the SAP Business Accelerator Hub.
    if obj_type in ("api", "odata", "soap", "service", "event") \
       or name.startswith(("API_", "CE_")) or name.endswith(("_SRV", "_IN", "_OUT")):
        result.update(verdict="LIKELY_RELEASED", verified=False,
            evidence="naming_heuristic_only",
            reason=("Not in the catalog, but the name matches SAP's released OData/SOAP/event API naming "
                    "(API_*/*_SRV, SOAP *_IN/*_OUT, events CE_*). Usable as a design placeholder, but this "
                    "verdict rests on the NAME ALONE."),
            evidence_warning=("NAME-PATTERN MATCH ONLY — no catalog entry backs this. Any well-formed API_* "
                              "string gets this verdict, including one that does not exist, so it cannot be "
                              "reported as 'released'. Cross-check with search_released_apis on the business "
                              "keywords (not the name): if that returns count 0, the NAME ITSELF is unconfirmed "
                              "— state that in the deliverable and keep it as an open verification item rather "
                              "than presenting it as a released object."),
            hub_overview_url="https://api.sap.com/api/%s/overview" % name,
            hub_all_apis_url=REFERENCE_LINKS["sap_business_accelerator_hub"]["url"],
            note=("A catalog miss is NOT 'unreleased'. Finalise every API against the SAP Business Accelerator "
                  "Hub (api.sap.com) — the authoritative public list of released S/4HANA Cloud APIs. A "
                  "heuristic-only verdict MUST appear in the tenant verification checklist."))
        return result
    # BAdI-shaped enhancement names not in the seed
    if obj_type == "badi" or re.match(r"^[A-Z]{2,}_[A-Z0-9_]{3,}$", name):
        result.update(verdict="NOT_VERIFIED", verified=False,
            reason=("Not in the offline seed. If this is a BAdI, confirm it on the SAP Help 'List of BAdIs' or "
                    "the tenant Custom Logic app — if it is not listed there for your release/scope it does not "
                    "exist for your tenant."))
        return result
    # Truly unknown
    result.update(reason=("Object not in the seed catalogs and no released-object naming convention matched. This "
                          "does NOT necessarily mean it doesn't exist — this offline server cannot confirm it. Look "
                          "it up on the authoritative lists above (Released CDS Views / List of BAdIs / S/4HANA "
                          "Cloud docs) or in the tenant, and label it 'to verify in tenant' in the deliverable."))
    return result

ADVISOR_RULES = [
    # (keywords, mode, rationale)
    (["field", "custom field", "extra field", "additional field", "screen field"],
     "key_user",
     "Adding fields to standard business objects/UIs is Key User Extensibility: Custom Fields app + UI adaptation (Adapt UI). Upgrade-safe, no code."),
    (["validation", "check", "mandatory", "block", "prevent save", "determination", "default value", "derive"],
     "key_user",
     "Validations/determinations on standard documents are Key User custom logic implemented in a RELEASED BAdI (Custom Logic app). Verify the BAdI exists for your business context."),
    (["report", "list", "analytics", "kpi", "dashboard", "query"],
     "key_user_or_developer_or_side_by_side",
     "Reports have three ladders in Public Cloud: (1) key user — Custom CDS Views + Custom Analytical Query (analytical report/query, zero cost); (2) developer — RAP + CDS + Fiori Elements list report in Eclipse ADT (interactive/actions); (3) side-by-side — CAP + UI5 on BTP only when UX exceeds both (adds BTP cost — Discovery Center). Check the Fiori Apps Library for a standard app first."),
    (["fiori app", "new app", "custom app", "new transaction", "ui5"],
     "developer_or_side_by_side",
     "New apps on in-tenant data: developer extensibility (RAP + Fiori Elements, ABAP Cloud). Apps needing non-SAP data, custom auth flows, or independent lifecycle: side-by-side on BTP (CAP or ABAP Environment)."),
    (["interface", "integration", "middleware", "third party", "external system", "api call", "webhook", "idoc", "rfc"],
     "side_by_side",
     "Integrations use released OData/SOAP APIs + business events, orchestrated via SAP Integration Suite (CPI) or a BTP side-by-side service. Custom RFC/IDoc endpoints cannot be built in Public Cloud."),
    (["form", "smartform", "smart form", "adobe form", "output", "print", "email template"],
     "key_user",
     "Output forms are ADOBE FORMS ONLY in Public Cloud (Maintain Form Templates app + Output Parameter Determination). Smart Forms / SAPscript / print programs do not exist. XDP editing needs Adobe LiveCycle Designer (licensing lead time)."),
    (["workflow", "approval", "release strategy"],
     "key_user_or_side_by_side",
     "Workflow has two ladders: Flexible Workflow (key user, Manage Workflows app) for SAP-delivered scenarios — check the tenant scenario list first; beyond that, SAP Build Process Automation (SBPA) on BTP, which has consumption cost (SAP Discovery Center)."),
    (["migration", "conversion", "data load", "upload"],
     "key_user",
     "Data loads use Migrate Your Data app (migration cockpit) with staging tables, or released APIs — never direct table writes."),
    (["batch", "background", "job", "scheduled"],
     "developer",
     "Background processing: ABAP Cloud class + Application Jobs (job catalog entry/template) — SUBMIT/SM36 style jobs do not exist."),
    (["event", "asynchronous", "notify", "trigger on change"],
     "side_by_side",
     "Business events from S/4HANA Cloud via Event Mesh/Advanced Event Mesh consumed by a BTP handler."),
]

def tool_extensibility_advisor(args):
    req = (args.get("requirement") or "").lower()
    matches = []
    for keywords, mode, rationale in ADVISOR_RULES:
        score = sum(1 for kw in keywords if kw in req)
        if score:
            matches.append({"mode": mode, "score": score, "rationale": rationale})
    matches.sort(key=lambda m: -m["score"])
    decision_path = [
        "1. Can standard config / fit-to-standard cover it? -> no development.",
        "2. Field/UI/simple logic on a standard object? -> Key User (in-app): Custom Fields and Logic, Adapt UI, Custom CDS Views, Custom Analytical Queries.",
        "3. Needs real code on in-tenant data with released objects? -> Developer extensibility (ABAP Cloud / Embedded Steampunk): RAP, CDS, released BAdIs, Application Jobs.",
        "4. Needs external data, independent lifecycle, non-ABAP stack, or unreleased-object access? -> Side-by-side on BTP via released APIs + events.",
        "5. Whatever the mode: EVERY consumed object must be released (C1) — gate this with check_object_release_state.",
    ]
    return {
        "verified": False,
        "source": "rule-based advisor (deterministic, no LLM) — final decision needs a human architect",
        "requirement_analyzed": args.get("requirement"),
        "candidate_modes": matches if matches else [
            {"mode": "unclassified", "score": 0,
             "rationale": "No keyword rule matched. Walk the decision path manually and consult the SAP Extensibility Explorer (https://extensibilityexplorer.cfapps.eu10.hana.ondemand.com/)."}],
        "mode_profiles": MODE_PROFILES,
        "rating_model": {
            "instruction": ("Rate EVERY solution option on three dimensions and show the table in the proposal: "
                            "Feasibility (1-5: can it be built with released objects / covered scenarios — verify, don't assume), "
                            "Approach fit (1-5: clean-core alignment, upgrade-safety, team skillset, lifecycle fit), "
                            "Cost (cost_class from mode_profiles + one-line estimate; for ANY BTP service link its "
                            "SAP Discovery Center page and name the pricing metric). Recommend the highest-rated option "
                            "and say why the runners-up lost."),
            "cost_reference": REFERENCE_LINKS["sap_discovery_center"]["url"],
            "api_reference": REFERENCE_LINKS["sap_business_accelerator_hub"]["url"],
        },
        "decision_path": decision_path,
        "hard_constraints": [
            "No BAPIs, no classical ABAP, no implicit/explicit enhancements, no SAP GUI artifacts.",
            "Only SAP-released APIs and CDS views (SAP Business Accelerator Hub) and released BAdIs (Custom Logic app) may appear in a solution.",
            "Forms: Adobe Forms only (Maintain Form Templates) — no Smart Forms/SAPscript.",
            "Developer extensibility = RAP on Eclipse ADT; side-by-side = SAP BTP (cost via Discovery Center).",
            "3-system landscape with ABAP Cloud + ATC — all custom code must pass cloud ATC checks.",
        ],
        "reference_links": REFERENCE_LINKS,
    }

def _save_experience(entry):
    if _EXP_BACKEND == "postgres":
        _exp_store.append_experience(entry)         # Aurora is the store; git seed exported nightly
        return
    _catalog_db.append_experience(entry)
    _catalog_db.sync_experience_to_seed(entry)  # auto-sync seed so git diff is always ready

def tool_query_experience(args):
    query = (args.get("query") or "").strip().lower()
    category = (args.get("category") or "").strip().lower()
    hits = []
    for e in (EXPERIENCE.get("entries") or []):
        if category and (e.get("category") or "") != category:
            continue
        # `or ""`/`or []` (not .get defaults): a stored null bypasses the default and breaks join()
        hay = " ".join([e.get("topic") or "", e.get("lesson") or "", e.get("category") or "",
                        " ".join(str(t) for t in (e.get("tags") or []))]).lower()
        if not query or any(tok in hay for tok in query.split()):
            hits.append(e)
    return {
        "verified": True,
        "source": "experience database (mcp-server/catalog/catalog.db · experience table) — team delivery lessons, grows with every run",
        "count": len(hits),
        "results": hits,
        "instruction_to_model": ("Consult this at intake and solution-proposal time; cite the EXP-ids you applied "
                                 "in the proposal. At the package step, record at least one new lesson via "
                                 "record_experience if the run taught anything non-obvious."),
        "reference_links": REFERENCE_LINKS,
    }

def _reject_client_identifiers(args, topic, lesson):
    """Keep client identifiers out of the lesson TEXT.

    db._seed_safe() already hashes the structured `source` before it reaches the git-tracked
    experience_db.json, but the free text is published verbatim — so a lesson that names the
    engagement ('in TEST-LOGISTIC-EXT-ID71-SID296 we found …') would still leak it. Rather than
    silently redacting (which mangles the lesson) we REJECT with the offending token named, so the
    caller rewrites it generically. Lessons are meant to be reusable across clients anyway.

    Deliberately conservative to avoid false positives: only the run id itself, its
    punctuation-variants, and its digit-bearing tokens (ID71, SID296) are treated as identifying.
    Ordinary words from a project name ('logistic', 'supplier') are NOT flagged."""
    source = (args.get("source") or "").strip()
    if not source or source.lower() in ("pipeline run", "delivery experience — seed",
                                        "delivery experience - seed"):
        return
    haystack = " ".join([topic, lesson, (args.get("impact") or ""),
                         " ".join(args.get("tags") or [])]).lower()
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    hits = []
    if norm(source) and norm(source) in norm(haystack):
        hits.append(source)
    for tok in re.split(r"[^A-Za-z0-9]+", source):          # ID71 / SID296 style identifiers
        if len(tok) >= 4 and any(c.isdigit() for c in tok) and tok.lower() in haystack:
            hits.append(tok)
    if hits:
        raise GuardrailViolation(
            "The lesson names this engagement (%s). experience_db.json is shared in git, so lessons "
            "must be client-neutral. Rewrite it as a reusable rule — describe the SAP object, the "
            "pattern and the fix, not the project. The run is already linked automatically."
            % ", ".join(sorted(set(hits))[:3]))


def tool_record_experience(args):
    topic = (args.get("topic") or "").strip()
    lesson = (args.get("lesson") or "").strip()
    category = (args.get("category") or "general").strip().lower()
    if not topic or not lesson:
        raise GuardrailViolation("record_experience needs both 'topic' and 'lesson'.")
    if len(topic) > 160 or len(lesson) > 1200:
        raise GuardrailViolation("Keep it distilled: topic <= 160 chars, lesson <= 1200 chars.")
    allowed_cat = {"general", "enhancement", "report", "interface", "conversion", "form",
                   "workflow", "developer", "key_user", "side_by_side"}
    if category not in allowed_cat:
        raise GuardrailViolation("category must be one of: %s" % ", ".join(sorted(allowed_cat)))
    _reject_client_identifiers(args, topic, lesson)
    next_id = "EXP-%03d" % (max([int(e["id"].split("-")[1]) for e in EXPERIENCE["entries"]
                                 if re.match(r"^EXP-\d+$", e.get("id", ""))] or [0]) + 1)
    entry = {"id": next_id, "category": category, "topic": topic, "lesson": lesson,
             "impact": (args.get("impact") or "").strip()[:200],
             "tags": [t.strip()[:30] for t in (args.get("tags") or [])][:8],
             "added": time.strftime("%Y-%m-%d"),
             "source": (args.get("source") or "pipeline run").strip()[:80]}
    EXPERIENCE["entries"].append(entry)
    _save_experience(entry)
    return {"verified": True, "source": "experience database (persisted)", "recorded": entry,
            "total_entries": len(EXPERIENCE["entries"])}

def tool_get_reference_links(args):
    return {
        "verified": True,
        "source": "curated authoritative SAP documentation sources",
        "links": REFERENCE_LINKS,
        "fetch_docs_by_object": FETCH_DOCS_BY_OBJECT,
        "brain_mirrored_docs": BRAIN_MIRRORED_DOCS,
        "usage_rules": [
            "Released CDS views -> SAP Help 'Released CDS Views' list (released_cds_views_list) + ADT Released Objects / View Browser; cite the list for every CDS view's release (C1) state.",
            "BAdIs -> SAP Help 'List of BAdIs' (released_badis_list) + Custom Logic app; cite it for every BAdI.",
            "APIs / integration content -> SAP Business Accelerator Hub; link each API's overview page in deliverables.",
            "BTP services + PRICING -> SAP Discovery Center; every side-by-side proposal links each service's page and names its pricing metric.",
            "Configuration objects, released applications, release notes, any other released objects -> S/4HANA Cloud docs root (sap_help_s4hana_cloud).",
            "Standard app check (fit-to-standard) -> Fiori Apps Library.",
            "SIDE-BY-SIDE (BTP) BUILDS: ground the code in the developer docs matching the object type you are building, per 'fetch_docs_by_object' — CAP/CAPM -> [cap_docs, nodejs_docs, npm_registry, javascript_ref]; UI5/Fiori -> [ui5_docs, javascript_ref, html_ref, css_ref, npm_registry]; both -> the union.",
            "SEARCH THE BRAIN FIRST for any doc set listed in 'brain_mirrored_docs' (ui5_docs, cap_docs, nodejs_docs): search_brain(query=\"<the API/pattern you need>\", source_system=\"developer_docs\") — or narrow with deliverable_type=\"ui5_docs\". Do NOT put a phase filter on that call: vendor docs are phase-independent, are tagged Realize, and a phase filter will silently hide them.",
            "UI5 MUST come from the brain. ui5.sap.com is a single-page app — every topic URL is a '#/topic/...' fragment, a fragment is never sent to the server, and all 1000+ pages return the same ~2 KB JavaScript shell. The fetch SUCCEEDS and grounds nothing, so the 'if the fetch fails, cite the URL' fallback never fires. That is how finding F-17 (OData apostrophe quoting) reached a deliverable.",
            "WebFetch only what the brain does NOT mirror (javascript_ref, html_ref, css_ref, npm_registry), or when you specifically need content newer than the brain's last harvest. For npm_registry, fetch its 'fetch_url' (https://registry.npmjs.org/<package> — JSON), not the npmjs.com web page. sap_community is CITE-ONLY (anti-bot blocks automated fetch) — link it for humans, do NOT fetch it. If a fetch fails, fall back to citing the URL for manual verification — never block the build.",
            "On Bedrock, web tools may be unavailable entirely — the brain is then the ONLY grounding route, which is why it comes first rather than as a fallback.",
        ],
    }

def tool_abap_cloud_lint(args):
    code = args.get("code") or ""
    findings = []
    lines = code.splitlines()
    for rule in LINT_RULES.get("rules", []):
        try:
            rx = re.compile(rule["pattern"], re.MULTILINE)
        except re.error:
            continue
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                findings.append({
                    "rule": rule["id"],
                    "severity": rule["severity"],
                    "line": i,
                    "code": line.strip()[:160],
                    "message": rule["message"],
                    "alternative": rule.get("alternative"),
                })
    errors = [f for f in findings if f["severity"] == "error"]
    return {
        "verified": True,
        "source": "deterministic regex lint (catalog/forbidden_patterns.json) — mirrors ABAP Cloud ATC intent, does not replace tenant ATC",
        "verdict": "FAIL" if errors else ("WARN" if findings else "PASS"),
        "error_count": len(errors),
        "warning_count": len(findings) - len(errors),
        "findings": findings,
        "next_gate": "Run ATC in ADT with the cloud check variant before transport — this lint is only the fast local pre-gate.",
    }

def tool_odata_query(args):
    service = args.get("service") or ""
    entity = args.get("entity_set") or ""
    check_service_allowlisted(service)
    if not re.match(r"^[A-Za-z0-9_]+$", service) or not re.match(r"^[A-Za-z0-9_]+$", entity):
        raise GuardrailViolation("Service and entity_set must be plain identifiers.")
    top = min(int(args.get("top") or 10), GUARD.get("odata_max_top", 50))
    query = {"$top": str(top), "$format": "json"}
    if args.get("filter"):
        if re.search(r"[;#]|--", args["filter"]):
            raise GuardrailViolation("Suspicious characters in $filter.")
        query["$filter"] = args["filter"]
    if args.get("select"):
        query["$select"] = args["select"]
    resp = sap_get("/sap/opu/odata/sap/%s/%s" % (service, entity), query)
    body = resp["body"]
    try:
        body = json.loads(body)
    except Exception:
        body = body[:4000]
    return {
        "verified": True,
        "source": "live tenant response (%s), HTTP %s, %s ms" % (service, resp["status"], resp["elapsed_ms"]),
        "read_only": True,
        "data": body,
    }

def tool_odata_get_metadata(args):
    service = args.get("service") or ""
    check_service_allowlisted(service)
    resp = sap_get("/sap/opu/odata/sap/%s/$metadata" % service)
    return {
        "verified": True,
        "source": "live tenant $metadata (HTTP %s)" % resp["status"],
        "metadata_xml": resp["body"][:60000],
        "truncated": len(resp["body"]) > 60000,
    }

def tool_sap_connection_test(args):
    require_live()
    resp = sap_get("/sap/opu/odata/iwfnd/catalogservice;v=2/ServiceCollection", {"$top": "1", "$format": "json"})
    ok = resp["status"] == 200
    return {
        "verified": True,
        "source": "live tenant gateway catalog service",
        "connected": ok,
        "http_status": resp["status"],
        "hint": None if ok else "401/403 -> check communication user + arrangement; 404 -> catalog service path differs; check SAP_BASE_URL points to the -api host.",
    }

def tool_guardrails_status(args):
    return {
        "verified": True,
        "source": "server configuration",
        "mode": MODE,
        "writes_allowed": ALLOW_WRITES,
        "odata_allowlist": GUARD.get("odata_service_allowlist"),
        "rate_limit_per_minute": GUARD.get("max_requests_per_minute"),
        "odata_max_top": GUARD.get("odata_max_top"),
        "tls_verification": "always on",
        "credential_policy": "env vars only (SAP_COMM_USER / SAP_COMM_PASSWORD); never stored, never logged",
        "llm_api_keys": "none — LLM runtime is the connecting Claude Code client",
    }

def tool_observability_snapshot(args):
    tail = []
    try:
        with open(AUDIT_PATH, "r", encoding="utf-8") as fh:
            tail = fh.readlines()[-int(args.get("audit_tail") or 10):]
    except Exception:
        pass
    return {
        "verified": True,
        "source": "server metrics + audit log",
        "metrics": _METRICS,
        "audit_tail": [json.loads(l) for l in tail if l.strip()],
        "audit_log_path": AUDIT_PATH,
    }

# ── Digital Brain: Layer 2 (SAP Knowledge Vectors) + Layer 3 (Experience Graph) ──

_VECTOR_DIR = os.path.join(BASE_DIR, "vector")
_VEC_ENG    = None  # cached after first successful import

def _load_vector_engine():
    global _VEC_ENG
    if _VEC_ENG is not None:
        return _VEC_ENG, None
    try:
        if _VECTOR_DIR not in sys.path:
            sys.path.insert(0, _VECTOR_DIR)
        import engine as _eng
        _VEC_ENG = _eng
        return _VEC_ENG, None
    except Exception as exc:
        return None, str(exc)

def tool_semantic_search(args):
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    eng, err = _load_vector_engine()
    if eng is None:
        return {"error": "Layer 2 unavailable (%s). Run: python mcp-server/vector/build_index.py" % err}
    top_k       = int(args.get("top_k") or 5)
    filter_type = args.get("object_type")
    results     = eng.search(query, top_k=top_k, filter_type=filter_type)
    if isinstance(results, dict) and "error" in results:
        return {"error": results["error"],
                "hint": "Run: python mcp-server/vector/build_index.py to build the index."}
    return {
        "verified": False,
        "source":   "S4PC TF-IDF index (catalog seed). Confirm on SAP Business Accelerator Hub / Custom Logic app / ADT.",
        "query":    query,
        "filter":   filter_type,
        "results":  results,
        "note":     "Scores are TF-IDF cosine similarity [0-1]. Higher = more relevant. Re-verify objects before use in designs.",
    }

def tool_find_similar_delivery(args):
    description = (args.get("description") or "").strip()
    if not description:
        return {"error": "description is required"}
    eng, err = _load_vector_engine()
    if eng is None:
        return {"error": "Layer 3 unavailable (%s). Run: python mcp-server/vector/build_index.py" % err}
    top_k   = int(args.get("top_k") or 3)
    results = eng.search(description, top_k=top_k, filter_type="delivery")
    if isinstance(results, dict) and "error" in results:
        return {"error": results["error"],
                "hint": "Run: python mcp-server/vector/build_index.py. No delivery history yet if output/ is empty."}
    return {
        "verified": True,
        "source":   "S4PC Experience Graph (output/<RUN-ID>/run.json files)",
        "description": description,
        "similar_deliveries": results,
        "note":     ("Matched by TF-IDF similarity on FD name, approved approach, objects used, and run summary. "
                     "Open the run.json for full context. Experience Graph grows with every completed pipeline run."),
    }

def tool_rebuild_vector_index(args):
    global _VEC_ENG
    build_script = os.path.join(_VECTOR_DIR, "build_index.py")
    engine_file  = os.path.join(_VECTOR_DIR, "engine.py")
    if not os.path.exists(engine_file):
        return {"error": "engine.py not found at %s" % _VECTOR_DIR}
    if not os.path.exists(build_script):
        return {"error": "build_index.py not found at %s" % _VECTOR_DIR}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("s4pc_build_index", build_script)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        docs = mod.build_documents()
        eng, err = _load_vector_engine()
        if eng is None:
            return {"error": "engine import failed: " + str(err)}
        count   = eng.build_and_save(docs)
        _VEC_ENG = None  # force reload on next search so new index is picked up
        by_type: dict = {}
        for d in docs:
            by_type[d["type"]] = by_type.get(d["type"], 0) + 1
        return {
            "success":    True,
            "indexed":    count,
            "by_type":    by_type,
            "index_path": os.path.join(_VECTOR_DIR, "index.json"),
            "note":       "Index rebuilt. semantic_search and find_similar_delivery now use the updated index.",
        }
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc()[:800]}

# ── Digital Brain: Layer 1 (Live Object Graph) ────────────────────────────────────

_GRAPH_DIR = os.path.join(BASE_DIR, "graph")
_GRAPH_ENG = None  # cached after first successful import

def _load_graph_engine():
    global _GRAPH_ENG
    if _GRAPH_ENG is not None:
        return _GRAPH_ENG, None
    try:
        if _GRAPH_DIR not in sys.path:
            sys.path.insert(0, _GRAPH_DIR)
        import graph_engine as _ge
        _GRAPH_ENG = _ge
        return _GRAPH_ENG, None
    except Exception as exc:
        return None, str(exc)

def tool_get_object_graph(args):
    object_name = (args.get("object_name") or "").strip()
    if not object_name:
        return {"error": "object_name is required"}
    ge, err = _load_graph_engine()
    if ge is None:
        return {"error": "Layer 1 unavailable (%s). Run: python mcp-server/graph/build_graph.py" % err}
    depth = int(args.get("depth") or 1)
    result = ge.get_object_graph(object_name, depth=depth)
    if "error" not in result:
        result["verified"] = False
        result["source"]   = "S4PC Live Object Graph (catalog seed). Confirm on SAP Business Accelerator Hub / Custom Logic app / ADT."
    return result

def tool_get_area_map(args):
    area = (args.get("area") or "").strip()
    if not area:
        ge, err = _load_graph_engine()
        if ge is None:
            return {"error": "Layer 1 unavailable (%s). Run: python mcp-server/graph/build_graph.py" % err}
        graph, err2 = ge._load()
        if graph is None:
            return {"error": err2}
        al = ge.list_areas(graph)
        al["hint"] = "Call get_area_map with one of the available_areas keys above."
        return al
    ge, err = _load_graph_engine()
    if ge is None:
        return {"error": "Layer 1 unavailable (%s). Run: python mcp-server/graph/build_graph.py" % err}
    result = ge.get_area_map(area)
    if "error" not in result:
        result["verified"] = False
        result["source"]   = "S4PC Live Object Graph (catalog seed). Confirm on SAP Business Accelerator Hub / Custom Logic app / ADT."
    return result

def tool_sync_object_graph(args):
    global _GRAPH_ENG
    build_script  = os.path.join(_GRAPH_DIR, "build_graph.py")
    engine_file   = os.path.join(_GRAPH_DIR, "graph_engine.py")
    live_enrich   = bool(args.get("live_enrich", False))

    if not os.path.exists(engine_file):
        return {"error": "graph_engine.py not found at %s" % _GRAPH_DIR}
    if not os.path.exists(build_script):
        return {"error": "build_graph.py not found at %s" % _GRAPH_DIR}

    try:
        # Load fresh from SQLite so sync_hub changes are picked up without a restart
        apis      = _catalog_db.load_apis().get("apis", [])
        cds_views = _catalog_db.load_cds_views().get("views", [])
        badis     = _catalog_db.load_badis().get("badis", [])

        ge, err = _load_graph_engine()
        if ge is None:
            return {"error": "graph_engine import failed: " + str(err)}

        graph = ge.build_graph(apis, cds_views, badis)

        # ── live enrichment: attach entity names from tenant $metadata ──────────
        live_log = []
        if live_enrich and MODE == "live":
            allowlist = GUARD.get("odata_service_allowlist", [])
            enriched  = 0
            for api_name in list(graph["nodes"].keys())[:10]:  # cap at 10 services
                if graph["nodes"][api_name]["type"] != "api":
                    continue
                svc = api_name.replace("_SRV", "").replace("_PROCESS", "")
                if svc not in allowlist and api_name not in allowlist:
                    continue
                try:
                    meta_result = tool_odata_get_metadata({"service": api_name})
                    if "content" in meta_result:
                        raw = meta_result["content"][0]["text"]
                        payload = json.loads(raw)
                        entities = payload.get("entity_types", payload.get("entities", []))
                        if entities:
                            graph["nodes"][api_name]["live_entities"] = entities
                            enriched += 1
                except Exception as exc:
                    live_log.append("WARN %s: %s" % (api_name, exc))
            live_log.insert(0, "Live enrichment: %d services enriched with $metadata entity types." % enriched)
        elif live_enrich and MODE != "live":
            live_log.append("live_enrich=True ignored: server is in offline mode. Set SAP_MODE=live to enable.")

        stats = ge.save_graph(graph)
        _GRAPH_ENG = None  # force reload on next call

        return {
            "success":     True,
            "nodes":       stats["nodes"],
            "edges":       stats["edges"],
            "areas":       stats["areas"],
            "by_type":     stats["by_type"],
            "graph_path":  ge.GRAPH_PATH,
            "live_log":    live_log,
            "note":        "Graph rebuilt. get_object_graph and get_area_map now use the updated graph.",
        }
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc()[:800]}

TOOLS = {
    "search_released_apis": {
        "description": ("Search the curated catalog of SAP-released APIs (OData/SOAP/events) for S/4HANA Cloud "
                        "Public Edition. Returns candidates with provenance; results must be re-verified on "
                        "api.sap.com before use in designs. Use this INSTEAD of recalling API names from memory."),
        "schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Keywords, e.g. 'sales order' or 'journal entry'"},
            "area": {"type": "string", "description": "Optional area filter: Sales, Procurement, Finance, Master Data, Inventory, Logistics, Asset Management"}},
            "required": ["query"]},
        "handler": tool_search_released_apis,
    },
    "search_released_badis": {
        "description": ("Search the curated catalog of key-user BAdIs (Custom Logic app) for S/4HANA Cloud Public "
                        "Edition. Availability MUST be confirmed in the tenant's Custom Logic app — this tool says so "
                        "in every response. Use INSTEAD of recalling BAdI names from memory."),
        "schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "area": {"type": "string"}},
            "required": ["query"]},
        "handler": tool_search_released_badis,
    },
    "check_object_release_state": {
        "description": ("Clean-core gate: check whether an object (BAPI, table, API, BAdI, CDS view) is usable in "
                        "S/4HANA Cloud Public Edition custom code. Returns NOT_AVAILABLE for BAPIs/classical tables "
                        "with the released alternative, LIKELY_RELEASED for catalog hits, NOT_VERIFIED otherwise. "
                        "ALWAYS read the 'evidence' field alongside the verdict: 'catalog_hit' means a real entry "
                        "backs it; 'naming_heuristic_only' means ONLY the name pattern matched, so a fabricated "
                        "name returns the same verdict — cross-check it and report it as 'name unconfirmed', never "
                        "as released. Call this for EVERY object referenced in a technical design."),
        "schema": {"type": "object", "properties": {
            "object_name": {"type": "string", "description": "e.g. BAPI_SALESORDER_CREATEFROMDAT2, VBAK, I_SalesDocument, API_BUSINESS_PARTNER"},
            "object_type": {"type": "string", "description": "Optional: bapi | table | api | badi | cds_view | auto"}},
            "required": ["object_name"]},
        "handler": tool_check_object_release_state,
    },
    "extensibility_advisor": {
        "description": ("Deterministic rule-based advisor: given a requirement in plain language, recommends the "
                        "extensibility mode for S/4HANA Cloud Public Edition — key user (in-app), developer "
                        "(ABAP Cloud / Embedded Steampunk), or side-by-side (BTP) — with the clean-core decision path."),
        "schema": {"type": "object", "properties": {
            "requirement": {"type": "string", "description": "The business/technical requirement, 1-5 sentences"}},
            "required": ["requirement"]},
        "handler": tool_extensibility_advisor,
    },
    "abap_cloud_lint": {
        "description": ("Static clean-core lint for ABAP code: flags classical-ABAP constructs that will not compile "
                        "or pass ATC in S/4HANA Cloud Public Edition (BAPI calls, SUBMIT, FORM/PERFORM, dynpro, "
                        "unreleased tables, enhancements...) and names the released alternative. Run on EVERY "
                        "generated ABAP snippet before presenting it to the user."),
        "schema": {"type": "object", "properties": {
            "code": {"type": "string", "description": "ABAP source code to check"}},
            "required": ["code"]},
        "handler": tool_abap_cloud_lint,
    },
    "odata_query": {
        "description": ("LIVE mode only. Read-only OData GET against an allowlisted released API of the connected "
                        "tenant (communication user, basic auth, TLS). Capped $top, rate-limited, fully audited. "
                        "Use to ground designs in real tenant data instead of assumptions."),
        "schema": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Allowlisted service, e.g. API_BUSINESS_PARTNER"},
            "entity_set": {"type": "string", "description": "e.g. A_BusinessPartner"},
            "filter": {"type": "string", "description": "Optional $filter"},
            "select": {"type": "string", "description": "Optional $select (comma-separated)"},
            "top": {"type": "integer", "description": "Max rows (server-capped)"}},
            "required": ["service", "entity_set"]},
        "handler": tool_odata_query,
    },
    "odata_get_metadata": {
        "description": "LIVE mode only. Fetch $metadata of an allowlisted OData service — the ground truth for entity/field names.",
        "schema": {"type": "object", "properties": {
            "service": {"type": "string"}},
            "required": ["service"]},
        "handler": tool_odata_get_metadata,
    },
    "sap_connection_test": {
        "description": "LIVE mode only. Verify tenant connectivity + communication user credentials via the gateway catalog service.",
        "schema": {"type": "object", "properties": {}},
        "handler": tool_sap_connection_test,
    },
    "query_experience": {
        "description": ("Search the team's S/4HANA Public Cloud experience database (delivery lessons, gotchas, "
                        "cost heuristics per RICEFW type and extensibility mode). Consult at intake and solution-"
                        "proposal time; cite applied EXP-ids in deliverables."),
        "schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Keywords, e.g. 'badi validation' or 'btp cost'"},
            "category": {"type": "string", "description": "Optional: general | enhancement | report | interface | conversion | form | workflow | developer | key_user | side_by_side"}},
            "required": []},
        "handler": tool_query_experience,
    },
    "record_experience": {
        "description": ("Persist a new delivery lesson into the experience database (compounding knowledge). Call at "
                        "the package step of a pipeline run when the run taught something non-obvious. Keep it "
                        "distilled and Public-Cloud-specific."),
        "schema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "Short headline (<=160 chars)"},
            "lesson": {"type": "string", "description": "The distilled lesson (<=1200 chars)"},
            "category": {"type": "string", "description": "general | enhancement | report | interface | conversion | form | workflow | developer | key_user | side_by_side"},
            "impact": {"type": "string", "description": "One line: why it matters"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string", "description": "e.g. 'run MM-EXT-0002'"}},
            "required": ["topic", "lesson"]},
        "handler": tool_record_experience,
    },
    "get_reference_links": {
        "description": ("Authoritative SAP documentation sources and when to use each: Business Accelerator Hub "
                        "(released APIs/CDS/integration content), Discovery Center (BTP services + PRICING), Help "
                        "Portal, Fiori Apps Library. Cite these in every solution deliverable."),
        "schema": {"type": "object", "properties": {}},
        "handler": tool_get_reference_links,
    },
    "guardrails_status": {
        "description": "Show active guardrails: mode, allowlist, rate limits, write policy, credential policy.",
        "schema": {"type": "object", "properties": {}},
        "handler": tool_guardrails_status,
    },
    "observability_snapshot": {
        "description": "Show server metrics (per-tool call counts, errors, latency) and the audit log tail.",
        "schema": {"type": "object", "properties": {
            "audit_tail": {"type": "integer", "description": "How many audit records to return (default 10)"}},
            "required": []},
        "handler": tool_observability_snapshot,
    },
    "semantic_search": {
        "description": ("Layer 2 — SAP Knowledge Vectors: TF-IDF semantic search across the full released-object "
                        "catalog (APIs, CDS views, BAdIs) and past experience entries. Use when keyword search "
                        "misses or the requirement is vague — e.g. 'goods movement validation' finds the right "
                        "BAdI even if the exact name is unknown. Scores are cosine-similarity; always re-verify "
                        "hits on authoritative sources (api.sap.com, Custom Logic app, ADT)."),
        "schema": {"type": "object", "properties": {
            "query":       {"type": "string",
                            "description": "Natural-language query, e.g. 'supplier invoice posting validation'"},
            "object_type": {"type": "string",
                            "description": "Optional filter: api | cds_view | badi | experience | delivery"},
            "top_k":       {"type": "integer", "description": "Max results to return (default 5)"}},
            "required": ["query"]},
        "handler": tool_semantic_search,
    },
    "find_similar_delivery": {
        "description": ("Layer 3 — Experience Graph: find the most similar past S4PC pipeline runs to a new "
                        "requirement. Returns matching run IDs with approved approach, extensibility mode, and "
                        "objects used — reuse proven patterns and avoid repeated mistakes. Call this at intake "
                        "alongside query_experience. Grows richer with every completed pipeline run."),
        "schema": {"type": "object", "properties": {
            "description": {"type": "string",
                            "description": "Requirement summary, e.g. 'goods receipt email notification to supplier'"},
            "top_k":       {"type": "integer", "description": "Max past runs to return (default 3)"}},
            "required": ["description"]},
        "handler": tool_find_similar_delivery,
    },
    "rebuild_vector_index": {
        "description": ("Rebuild the Digital Brain TF-IDF index from the current catalog files + all completed "
                        "pipeline run.json files. Run after adding new catalog entries or completing a pipeline "
                        "run to keep semantic_search and find_similar_delivery up to date. Also callable via: "
                        "python mcp-server/vector/build_index.py"),
        "schema": {"type": "object", "properties": {}},
        "handler": tool_rebuild_vector_index,
    },
    "get_object_graph": {
        "description": ("Layer 1 — Live Object Graph: given a single released SAP object name (API, CDS view, "
                        "or BAdI), return all directly related objects across types — e.g. the CDS views and "
                        "BAdIs that share the same business concept as an API. Uses name-fragment matching; "
                        "falls back to area-mates when no name-match edges exist. Use to discover the full "
                        "released-object landscape around a requirement before coding."),
        "schema": {"type": "object", "properties": {
            "object_name": {"type": "string",
                            "description": "A released object name, e.g. I_PurchaseOrder or API_BUSINESS_PARTNER"},
            "depth":       {"type": "integer",
                            "description": "BFS hop depth (1=direct neighbours, 2=neighbours of neighbours; default 1)"}},
            "required": ["object_name"]},
        "handler": tool_get_object_graph,
    },
    "get_area_map": {
        "description": ("Layer 1 — Live Object Graph: return ALL released APIs, CDS views, and BAdIs for a "
                        "given SAP business area (e.g. 'Procurement', 'Finance', 'Sales'). The complete "
                        "released-object map for a module in one call. Call without area to list all available "
                        "areas. Use at requirement intake to understand the full extensibility landscape."),
        "schema": {"type": "object", "properties": {
            "area": {"type": "string",
                     "description": "Business area, e.g. 'Procurement', 'Finance', 'Sales', 'Master Data'. "
                                    "Omit to list all available areas."}},
            "required": []},
        "handler": tool_get_area_map,
    },
    "sync_object_graph": {
        "description": ("Layer 1 — Live Object Graph: rebuild graph.json from the current catalog files. "
                        "In live mode with live_enrich=true, also fetches OData $metadata from the connected "
                        "tenant to add actual entity+field names to API nodes — the 'live' part of the Live "
                        "Object Graph. Run after any catalog change. Also callable via: "
                        "python mcp-server/graph/build_graph.py"),
        "schema": {"type": "object", "properties": {
            "live_enrich": {"type": "boolean",
                            "description": "Enrich API nodes with live $metadata from the tenant (live mode only)"}},
            "required": []},
        "handler": tool_sync_object_graph,
    },
}

SERVER_INSTRUCTIONS = """S/4HANA Cloud PUBLIC Edition clean-core server. Rules for the model:
1. NEVER cite a BAPI, function module, classical table, enhancement point, or user exit — they do not exist here.
2. Before referencing ANY SAP object in a design or code, call check_object_release_state. If the verdict is
   NOT_VERIFIED, say so explicitly to the user and mark it 'to be verified in tenant' — do not present it as fact.
3. Run abap_cloud_lint on every ABAP snippet before showing it.
4. Extensibility decisions go through extensibility_advisor and must land on: key user (in-app: released
   BAdIs, custom fields, Adapt UI, analytical queries, Adobe Forms, Flexible Workflow), developer (RAP on
   Eclipse ADT), or side-by-side (SAP BTP incl. SBPA, CAP+UI5, Integration Suite) — or a MIX per capability.
   Released objects only, always.
5. Rate every solution option on Feasibility / Approach fit / Cost. BTP services cost money: link each
   service's SAP Discovery Center page and name the pricing metric. Key-user and developer extensibility are
   included in the subscription.
6. Cite sources: APIs/CDS -> SAP Business Accelerator Hub (api.sap.com); BTP services + pricing -> SAP
   Discovery Center; guides -> SAP Help Portal; standard apps -> Fiori Apps Library.
7. Consult query_experience at intake/proposal time (cite EXP-ids); record new lessons with record_experience
   at the package step.
8. Seed catalogs are candidates, not truth: authoritative sources are api.sap.com, the Custom Logic app, and
   ADT Released Objects. Repeat this caveat in deliverables.
9. Use semantic_search (Layer 2) when keyword search misses or the requirement is vague — it searches the
   full catalog by meaning, not token match. Filter by object_type=api/cds_view/badi/experience as needed.
10. Use find_similar_delivery (Layer 3) at intake to surface prior pipeline runs with similar requirements;
    reuse proven extensibility approaches. Call rebuild_vector_index after each completed run to keep the
    Experience Graph current.
11. Use get_object_graph to discover all released objects related to a single known object (APIs, CDS views,
    BAdIs linked by shared business concept). Use get_area_map to see the complete released-object landscape
    for a business area. Call sync_object_graph (offline) or sync_object_graph+live_enrich (live mode) after
    catalog changes to keep the graph current.
12. Use search_brain (semantic RAG over the harvested SharePoint delivery knowledge + SAP scope catalog,
    Bedrock Titan embeddings) to GROUND a deliverable in prior delivery experience. It is a reference layer,
    NOT an authoritative object source — every SAP object name it surfaces still goes through
    check_object_release_state and is re-verified on api.sap.com / the Custom Logic app / ADT. If it is
    unavailable (deps/index absent on this host), continue with the governance tools — they are independent.
"""

PROMPTS = {
    "extensibility-decision": {
        "description": "Guided clean-core extensibility decision for a requirement (Public Cloud)",
        "arguments": [{"name": "requirement", "description": "The requirement text", "required": True}],
        "template": ("Requirement: {requirement}\n\n"
                     "Steps: 1) call extensibility_advisor with the requirement; 2) for every SAP object you would "
                     "touch, call check_object_release_state; 3) produce a decision table: mode, objects (with "
                     "release verdicts), effort, risks, upgrade-safety; 4) list what must still be verified in the "
                     "tenant (Custom Logic app / ADT / api.sap.com). Never invent object names."),
    },
    "clean-core-code-review": {
        "description": "Review ABAP code for Public Cloud clean-core compliance",
        "arguments": [{"name": "code", "description": "ABAP source", "required": True}],
        "template": ("Review this ABAP for S/4HANA Cloud Public Edition:\n\n{code}\n\n"
                     "1) run abap_cloud_lint; 2) for each referenced object run check_object_release_state; "
                     "3) report verdict (SHIP/FIX/REDESIGN) with findings table and released alternatives."),
    },
}

# ------------------------------------------------------------- MCP plumbing ---

def make_result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}

def handle_tools_call(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in TOOLS:
        return {"content": [{"type": "text", "text": "Unknown tool: %s" % name}], "isError": True}
    started = time.time()
    ok = True
    try:
        payload = TOOLS[name]["handler"](args)
        result = make_result(payload)
    except GuardrailViolation as exc:
        ok = False
        result = {"content": [{"type": "text", "text": json.dumps({
            "guardrail_blocked": True, "reason": str(exc)}, indent=2)}], "isError": True}
    except Exception as exc:
        ok = False
        result = {"content": [{"type": "text", "text": "Tool error: %s" % exc}], "isError": True}
        log_stderr("tool %s failed: %s" % (name, traceback.format_exc()))
    duration = (time.time() - started) * 1000
    audit("tool_call", {"tool": name, "arguments": args, "ok": ok, "duration_ms": int(duration)})
    record_call(name, duration, ok)
    return result

def handle_request(msg):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        client_proto = params.get("protocolVersion") or PROTOCOL_VERSION
        return {
            "protocolVersion": client_proto,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": CONFIG.get("server", {}).get("name", "s4pc-mcp"),
                           "version": CONFIG.get("server", {}).get("version", "1.0.0")},
            "instructions": SERVER_INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [
            {"name": name, "description": t["description"], "inputSchema": t["schema"]}
            for name, t in TOOLS.items()]}
    if method == "tools/call":
        return handle_tools_call(params)
    if method == "resources/list":
        return {"resources": [
            {"uri": "s4pc://catalog/released-apis", "name": "Released APIs seed catalog", "mimeType": "application/json"},
            {"uri": "s4pc://catalog/released-badis", "name": "Released BAdIs seed catalog", "mimeType": "application/json"},
            {"uri": "s4pc://catalog/released-cds-views", "name": "Released CDS views seed catalog", "mimeType": "application/json"},
            {"uri": "s4pc://catalog/lint-rules", "name": "ABAP Cloud lint rules", "mimeType": "application/json"},
            {"uri": "s4pc://catalog/experience", "name": "Experience database (delivery lessons)", "mimeType": "application/json"},
            {"uri": "s4pc://guardrails", "name": "Active guardrails", "mimeType": "application/json"},
        ]}
    if method == "resources/read":
        uri = params.get("uri", "")
        data = {
            "s4pc://catalog/released-apis": CATALOG_APIS,
            "s4pc://catalog/released-badis": CATALOG_BADIS,
            "s4pc://catalog/released-cds-views": CATALOG_CDS,
            "s4pc://catalog/lint-rules": LINT_RULES,
            "s4pc://catalog/experience": EXPERIENCE,
            "s4pc://guardrails": tool_guardrails_status({}),
        }.get(uri)
        if data is None:
            raise ValueError("Unknown resource: %s" % uri)
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": json.dumps(data, indent=2, ensure_ascii=False)}]}
    if method == "prompts/list":
        return {"prompts": [
            {"name": name, "description": p["description"], "arguments": p["arguments"]}
            for name, p in PROMPTS.items()]}
    if method == "prompts/get":
        p = PROMPTS.get(params.get("name", ""))
        if not p:
            raise ValueError("Unknown prompt")
        args = params.get("arguments") or {}
        text = p["template"]
        for key, val in args.items():
            text = text.replace("{%s}" % key, str(val))
        return {"description": p["description"],
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
    raise ValueError("Method not found: %s" % method)

def _read_zip_with_bad_eocd_v2(raw):
    """
    Scan central directory entries (PK\\x01\\x02) to map filenames to local header offsets.
    Then use those offsets to find and decompress the data.
    The local header offsets stored in the CD may also be wrong (text-mode CRLF damage),
    so we do a secondary scan: find the filename string in the local header area.
    """
    import struct, zlib as _zlib
    CD_SIG = b"\x50\x4b\x01\x02"
    LF_SIG = b"\x50\x4b\x03\x04"

    # Build a map from filename -> (lf_offset_in_cd, comp_method, comp_size)
    cd_entries = {}
    pos = 0
    _cd_hits = raw.count(CD_SIG)
    import sys as _sys2
    _sys2.stderr.write("[extract_debug] raw len=%d cd_sig_count=%d\n" % (len(raw), _cd_hits))
    _sys2.stderr.flush()
    while True:
        idx = raw.find(CD_SIG, pos)
        if idx == -1:
            break
        try:
            comp_method = struct.unpack_from("<H", raw, idx + 10)[0]
            comp_size   = struct.unpack_from("<I", raw, idx + 20)[0]
            uncomp_size = struct.unpack_from("<I", raw, idx + 24)[0]
            fname_len   = struct.unpack_from("<H", raw, idx + 28)[0]
            extra_len   = struct.unpack_from("<H", raw, idx + 30)[0]
            comment_len = struct.unpack_from("<H", raw, idx + 32)[0]
            lf_offset   = struct.unpack_from("<I", raw, idx + 42)[0]
            _sys2.stderr.write("[cd_entry] idx=%d fname_len=%d extra_len=%d comment_len=%d lf_offset=%d\n" % (
                idx, fname_len, extra_len, comment_len, lf_offset))
            _sys2.stderr.flush()
            if 0 < fname_len < 512 and extra_len < 65535 and comment_len < 65535:
                fname = raw[idx + 46: idx + 46 + fname_len].decode("utf-8", errors="replace")
                cd_entries[fname] = {
                    "lf_offset": lf_offset,
                    "comp_method": comp_method,
                    "comp_size": comp_size,
                    "uncomp_size": uncomp_size,
                }
                entry_size = 46 + fname_len + extra_len + comment_len
                pos = idx + max(entry_size, 4)
            else:
                # Invalid or corrupt CD entry — skip 4 bytes and keep scanning
                pos = idx + 4
        except Exception as _ce:
            _sys2.stderr.write("[cd_entry_err] idx=%d err=%s\n" % (idx, _ce))
            _sys2.stderr.flush()
            pos = idx + 4

    files = {}
    for fname, info in cd_entries.items():
        fname_bytes = fname.encode("utf-8")
        # Try lf_offset first
        lf_pos = info["lf_offset"]
        data = None
        if lf_pos < len(raw) and raw[lf_pos:lf_pos+4] == LF_SIG:
            lf_fname_len = struct.unpack_from("<H", raw, lf_pos + 26)[0]
            lf_extra_len = struct.unpack_from("<H", raw, lf_pos + 28)[0]
            data_start = lf_pos + 30 + lf_fname_len + lf_extra_len
        else:
            # lf_offset is wrong; find the local header by searching for fname bytes
            search_start = 0
            while True:
                fi = raw.find(fname_bytes, search_start)
                if fi == -1:
                    break
                # Check if there's a LF_SIG 30 bytes before
                candidate = fi - 30
                if candidate >= 0 and raw[candidate:candidate+4] == LF_SIG:
                    lf_fname_len = struct.unpack_from("<H", raw, candidate + 26)[0]
                    if lf_fname_len == len(fname_bytes):
                        lf_extra_len = struct.unpack_from("<H", raw, candidate + 28)[0]
                        data_start = candidate + 30 + lf_fname_len + lf_extra_len
                        lf_pos = candidate
                        break
                search_start = fi + 1
            else:
                continue  # couldn't find local header
        comp_method = info["comp_method"]
        comp_size   = info["comp_size"]
        uncomp_size = info["uncomp_size"]
        try:
            data = raw[data_start: data_start + comp_size]
            if comp_method == 8:
                data = _zlib.decompress(data, -15)
            elif comp_method != 0:
                continue
            files[fname] = data
        except Exception:
            pass

    return files, cd_entries

def _read_zip_with_bad_eocd(raw):
    """
    When the EOCD has a corrupt central-directory offset, locate the actual
    CD by scanning for PK\\x01\\x02 signatures, parse each entry to find the
    local-file-header offset, then extract compressed data from there.
    Returns a dict mapping filename -> uncompressed bytes.
    """
    import struct, zlib as _zlib
    CD_SIG = b"\x50\x4b\x01\x02"
    LF_SIG = b"\x50\x4b\x03\x04"
    files = {}
    pos = 0
    while True:
        idx = raw.find(CD_SIG, pos)
        if idx == -1:
            break
        try:
            # Central directory entry layout:
            # 0  4  signature
            # 4  2  version made by
            # 6  2  version needed
            # 8  2  general purpose bit flag
            # 10 2  compression method
            # 12 2  last mod file time
            # 14 2  last mod file date
            # 16 4  crc-32
            # 20 4  compressed size
            # 24 4  uncompressed size
            # 28 2  file name length
            # 30 2  extra field length
            # 32 2  file comment length
            # 34 2  disk number start
            # 36 2  internal file attributes
            # 38 4  external file attributes
            # 42 4  relative offset of local header
            # 46 fn  file name
            comp_method = struct.unpack_from("<H", raw, idx + 10)[0]
            comp_size   = struct.unpack_from("<I", raw, idx + 20)[0]
            uncomp_size = struct.unpack_from("<I", raw, idx + 24)[0]
            fname_len   = struct.unpack_from("<H", raw, idx + 28)[0]
            extra_len   = struct.unpack_from("<H", raw, idx + 30)[0]
            comment_len = struct.unpack_from("<H", raw, idx + 32)[0]
            lf_offset   = struct.unpack_from("<I", raw, idx + 42)[0]
            if 0 < fname_len < 512:
                fname = raw[idx + 46: idx + 46 + fname_len].decode("utf-8", errors="replace")
                # Read local file header at lf_offset to get actual data start
                if lf_offset < len(raw) and raw[lf_offset:lf_offset+4] == LF_SIG:
                    lf_fname_len  = struct.unpack_from("<H", raw, lf_offset + 26)[0]
                    lf_extra_len  = struct.unpack_from("<H", raw, lf_offset + 28)[0]
                    data_start    = lf_offset + 30 + lf_fname_len + lf_extra_len
                    # Handle zip64: comp_size == 0xFFFFFFFF means size is in extra
                    actual_comp_size = comp_size
                    if comp_size == 0xFFFFFFFF or uncomp_size == 0xFFFFFFFF:
                        # Parse zip64 extra from local header extra field
                        ep = lf_offset + 30 + lf_fname_len
                        ep_end = ep + lf_extra_len
                        while ep < ep_end - 3:
                            eid = struct.unpack_from("<H", raw, ep)[0]
                            esz = struct.unpack_from("<H", raw, ep+2)[0]
                            if eid == 0x0001:  # zip64
                                if uncomp_size == 0xFFFFFFFF and ep+4+8 <= ep_end:
                                    uncomp_size = struct.unpack_from("<Q", raw, ep+4)[0]
                                if comp_size == 0xFFFFFFFF and ep+4+16 <= ep_end:
                                    actual_comp_size = struct.unpack_from("<Q", raw, ep+12)[0]
                                break
                            ep += 4 + esz
                    data = raw[data_start: data_start + actual_comp_size]
                    if comp_method == 8:
                        data = _zlib.decompress(data, -15)
                    files[fname] = data
            entry_size = 46 + fname_len + extra_len + comment_len
            pos = idx + entry_size
        except Exception:
            pos = idx + 4
    return files

def tool_extract_docx(args):
    """Pipeline helper: extract plain text from a .docx (ZIP+XML) or text/md file."""
    import zipfile as _zf, io as _io
    file_path = _safe_read_path(args.get("file_path", ""),
                                ("input", "FD Test AI Stock Monitoring.docx.md"))
    with open(file_path, "rb") as fh:
        raw_bytes = fh.read()
    header = raw_bytes[:4]
    mode = "unknown"
    text = ""
    xml_content = None
    if header[:2] == b"PK":
        # Try standard ZIP first
        try:
            with _zf.ZipFile(_io.BytesIO(raw_bytes), "r") as z:
                with z.open("word/document.xml") as f:
                    xml_content = f.read().decode("utf-8")
            mode = "docx_standard"
        except Exception as std_err:
            # Strategy: patch the EOCD cd_offset to point to where PK\x01\x02 actually is,
            # then retry with zipfile on a BytesIO of the patched data.
            import struct as _struct, io as _io2
            CD_SIG = b"\x50\x4b\x01\x02"
            EOCD_SIG = b"\x50\x4b\x05\x06"
            patched = bytearray(raw_bytes)
            eocd_pos = raw_bytes.rfind(EOCD_SIG)
            actual_cd_pos = raw_bytes.find(CD_SIG)  # first PK\x01\x02
            patched_ok = False
            if eocd_pos >= 0 and actual_cd_pos >= 0:
                _struct.pack_into("<I", patched, eocd_pos + 16, actual_cd_pos)
                # Also patch the CD size to the space between CD start and EOCD
                actual_cd_size = eocd_pos - actual_cd_pos
                if actual_cd_size > 0:
                    _struct.pack_into("<I", patched, eocd_pos + 12, actual_cd_size)
                try:
                    with _zf.ZipFile(_io2.BytesIO(bytes(patched)), "r") as z:
                        if "word/document.xml" in z.namelist():
                            with z.open("word/document.xml") as f:
                                xml_content = f.read().decode("utf-8", errors="replace")
                            mode = "docx_eocd_patched"
                            patched_ok = True
                except Exception as patch_err:
                    pass  # fall through to manual scan
            if not patched_ok:
                files, cd_entries = _read_zip_with_bad_eocd_v2(raw_bytes)
            if xml_content is None and "word/document.xml" in (files if not patched_ok else {}):
                xml_content = files["word/document.xml"].decode("utf-8", errors="replace")
                mode = "docx_cd_scan"
            else:
                mode = "zip_error"
                text = "std_err=%s; patched_ok=%s; cd_entries=%d; files=%s" % (
                    std_err, patched_ok, len(cd_entries) if not patched_ok else -1,
                    list(files.keys())[:10] if not patched_ok else [])
        if xml_content:
            text = re.sub(r"<[^>]+>", " ", xml_content)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n\s*\n", "\n\n", text)
            text = text.strip()
    else:
        # Plain text / markdown
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        mode = "plain_text"
    return {"file_path": file_path, "mode": mode, "extracted_text": text, "char_count": len(text)}

def tool_file_probe(args):
    """Probe a file: size, header bytes, search for key strings."""
    import struct
    file_path = _safe_read_path(args.get("file_path", ""),
                                ("input", "FD Test AI Stock Monitoring.docx.md"))
    with open(file_path, "rb") as fh:
        raw = fh.read()
    size = len(raw)
    header = raw[:16].hex()
    # Search for key strings
    searches = {
        "PK_local": raw.count(b"\x50\x4b\x03\x04"),
        "PK_central": raw.count(b"\x50\x4b\x01\x02"),
        "word/document.xml": raw.count(b"word/document.xml"),
        "word/": raw.count(b"word/"),
        "Content_Types": raw.count(b"Content_Types"),
        "<?xml": raw.count(b"<?xml"),
        "w:body": raw.count(b"w:body"),
        "w:t>": raw.count(b"w:t>"),
    }
    # Find first occurrence of word/
    word_pos = raw.find(b"word/")
    word_ctx = raw[max(0,word_pos-10):word_pos+50].hex() if word_pos >= 0 else "not found"
    # Find first PK\x03\x04 and show surrounding bytes
    pk_pos = raw.find(b"\x50\x4b\x03\x04")
    pk_ctx = raw[pk_pos:pk_pos+40].hex() if pk_pos >= 0 else "not found"
    # Analyze end-of-central-directory (EOCD) at tail
    eocd_sig = b"\x50\x4b\x05\x06"
    eocd_pos = raw.rfind(eocd_sig)
    eocd_info = {}
    if eocd_pos >= 0:
        cd_offset = struct.unpack_from("<I", raw, eocd_pos + 16)[0]
        cd_size   = struct.unpack_from("<I", raw, eocd_pos + 12)[0]
        num_entries = struct.unpack_from("<H", raw, eocd_pos + 10)[0]
        eocd_info = {
            "eocd_pos": eocd_pos,
            "cd_offset_in_eocd": cd_offset,
            "cd_size": cd_size,
            "num_entries": num_entries,
            "file_size": size,
            "expected_cd_pos": size - 22 - cd_size,  # approx
        }
        # Check if CD is at expected offset
        if cd_offset < size:
            cd_first4 = raw[cd_offset:cd_offset+4].hex()
            eocd_info["bytes_at_cd_offset"] = cd_first4
        else:
            eocd_info["cd_offset_beyond_eof"] = True
    # Find actual first central dir entry
    cd_sig = b"\x50\x4b\x01\x02"
    first_cd_pos = raw.find(cd_sig)
    eocd_info["actual_first_cd_pos"] = first_cd_pos
    eocd_info["actual_first_cd_hex"] = raw[first_cd_pos:first_cd_pos+8].hex() if first_cd_pos >= 0 else "none"
    return {
        "file_path": file_path,
        "size_bytes": size,
        "header_hex": header,
        "search_counts": searches,
        "first_word_slash_pos": word_pos,
        "first_word_slash_ctx_hex": word_ctx,
        "first_pk_local_pos": pk_pos,
        "first_pk_local_ctx_hex": pk_ctx,
        "eocd_analysis": eocd_info,
    }

# --------------------------------------------------------- BTP deploy ---
# Side-by-side (BTP) deploy helper. OFF by default: needs BOTH S4PC_ALLOW_DEPLOY=true and
# guardrails.deploy.allow_deploy=true. Dry-run (build only) by default; production spaces are
# blocked; Cloud Foundry credentials come only from CF_* environment variables.
DEPLOY = GUARD.get("deploy", {})
ALLOW_DEPLOY = os.environ.get("S4PC_ALLOW_DEPLOY", "").lower() == "true" and DEPLOY.get("allow_deploy", False)

def require_deploy():
    if not ALLOW_DEPLOY:
        raise GuardrailViolation(
            "Deployment is disabled. It is OFF by default and must be enabled deliberately: set "
            "S4PC_ALLOW_DEPLOY=true AND guardrails.deploy.allow_deploy=true in config.json. Even then it "
            "targets a dev/test space only, defaults to dry-run, and needs a human deploy-approval.")

def _space_ok(space):
    s = (space or "").lower()
    for bad in DEPLOY.get("blocked_space_patterns", ["prod", "prd", "production"]):
        if bad in s:
            raise GuardrailViolation(
                "Refusing to deploy: space '%s' looks like production (blocked pattern '%s'). This "
                "pipeline deploys to dev/test only; promote to prod via CI/CD." % (space, bad))
    allow = DEPLOY.get("space_allowlist", [])
    if allow and space not in allow:
        raise GuardrailViolation(
            "Space '%s' is not on guardrails.deploy.space_allowlist %s — add it deliberately." % (space, allow))

def tool_btp_deploy(args):
    """Build (mbt) and optionally deploy (cf) a BTP MTA — CAP + HANA + UI5 — to a Cloud Foundry
    DEV/TEST space. Dry-run builds only unless dry_run=false is passed explicitly."""
    require_deploy()
    import shutil as _sh, subprocess as _sp, glob as _glob
    project = args.get("project_dir") or "."
    space = args.get("space") or os.environ.get("CF_SPACE", "")
    org = args.get("org") or os.environ.get("CF_ORG", "")
    api = args.get("api") or os.environ.get("CF_API", "")
    dry_run = bool(args.get("dry_run", DEPLOY.get("dry_run_default", True)))
    if not space:
        raise GuardrailViolation("No target space — pass 'space' or set CF_SPACE (a dev/test space).")
    _space_ok(space)
    audit("btp_deploy_request", {"project": project, "space": space, "dry_run": dry_run})

    runbook = [
        "cf api %s" % (api or "https://api.cf.<region>.hana.ondemand.com"),
        'cf auth "$CF_USER" "$CF_PASSWORD"   # or: cf login --sso / a service key',
        "cf target -o %s -s %s" % (org or "<org>", space),
        "mbt build -t ./mta_archives",
        "cf deploy ./mta_archives/<app>_<ver>.mtar",
    ]
    have_cf, have_mbt = _sh.which("cf"), _sh.which("mbt")
    if not (have_cf and have_mbt):
        return {"deployed": False,
                "reason": "cf CLI and/or Cloud MTA Build Tool (mbt) not found on this runner.",
                "prereqs": {"cf": bool(have_cf), "mbt": bool(have_mbt)},
                "manual_runbook": runbook,
                "note": "Install the Cloud Foundry CLI (+ MultiApps plugin) and mbt, then re-run — "
                        "or run the runbook yourself in your BTP dev space."}

    timeout = DEPLOY.get("timeout_seconds", 1800)
    def run(cmd, display=None):
        p = _sp.run(cmd, cwd=project, capture_output=True, text=True, timeout=timeout)
        return {"cmd": display or " ".join(cmd), "code": p.returncode,
                "out": (p.stdout or "")[-1500:], "err": (p.stderr or "")[-800:]}

    steps = [run(["mbt", "build", "-t", os.path.join(project, "mta_archives")])]
    if dry_run or steps[-1]["code"] != 0:
        return {"deployed": False, "dry_run": True, "steps": steps,
                "planned_deploy": "cf deploy <mtar> -> space '%s'" % space,
                "note": "Dry-run default: MTA built, NOT deployed. Re-run with dry_run=false plus a "
                        "human deploy-approval to deploy to '%s'." % space}

    if args.get("prereqs_confirmed") is not True:
        raise GuardrailViolation(
            "BTP prerequisite check not confirmed. A side-by-side app fails at runtime unless its "
            "in-tenant dependencies are deployed & active FIRST — custom fields (key-user), the "
            "communication scenario + arrangement for every consumed API/event, and any in-tenant "
            "RAP/CDS objects. Complete the Step 13 prerequisite gate, then pass prereqs_confirmed=true.")
    user, pwd = os.environ.get("CF_USER", ""), os.environ.get("CF_PASSWORD", "")
    cid, csecret = os.environ.get("CF_CLIENT_ID", ""), os.environ.get("CF_CLIENT_SECRET", "")
    if not (api and org):
        raise GuardrailViolation("Live deploy needs CF_API and CF_ORG (env or args).")
    # Reuse an active `cf login` session when one exists for THIS endpoint — works for every auth
    # method (trial SSO, corporate-IdP SSO, or user+password) and every region/account. `cf target`
    # is read-only; `cf api` RESETS the session and is fatal for SSO logins (no non-interactive
    # re-auth), so only fall back to api+auth when nothing is already logged in. The endpoint match
    # guards against reusing a session pointed at a different account.
    probe = run(["cf", "target"], display="cf target")
    steps.append(probe)
    logged_in = (probe["code"] == 0
                 and api.rstrip("/") in (probe["out"] or "")
                 and "Not logged in" not in (probe["out"] or ""))
    if not logged_in:
        # Non-interactive fallbacks (priority): service key (client-credentials) → user+password.
        if cid and csecret:
            steps.append(run(["cf", "api", api]))
            steps.append(run(["cf", "auth", cid, csecret, "--client-credentials"],
                             display="cf auth **** --client-credentials"))  # secret redacted
        elif user and pwd:
            steps.append(run(["cf", "api", api]))
            steps.append(run(["cf", "auth", user, pwd], display="cf auth ****"))  # credentials redacted
        else:
            raise GuardrailViolation(
                "No active Cloud Foundry session for %s and no credentials. Choose one: run "
                "`cf login -a %s` (add --sso for SSO/trial) on this runner before deploying — the "
                "deploy reuses that session — or set CF_CLIENT_ID+CF_CLIENT_SECRET (service key, "
                "best for CI) or CF_USER+CF_PASSWORD (technical/communication user)." % (api, api))
    steps.append(run(["cf", "target", "-o", org, "-s", space]))
    mtars = sorted(_glob.glob(os.path.join(project, "mta_archives", "*.mtar")))
    if not mtars:
        return {"deployed": False, "steps": steps, "reason": "mbt build produced no .mtar"}
    steps.append(run(["cf", "deploy", mtars[-1], "-f"]))
    ok = steps[-1]["code"] == 0
    apps = run(["cf", "apps"])
    audit("btp_deploy_result", {"space": space, "ok": ok})
    return {"deployed": ok, "space": space, "mtar": os.path.basename(mtars[-1]), "steps": steps,
            "apps": apps.get("out", ""),
            "note": "Deployed to dev/test space '%s'. Production promotion is out of scope — use a CI/CD promotion." % space}

TOOLS["btp_deploy"] = {
    "description": ("Side-by-side deploy helper: build (mbt) and optionally deploy (cf deploy) a BTP MTA — "
                    "CAP service + HANA + UI5 — to a Cloud Foundry DEV/TEST space. OFF by default "
                    "(needs S4PC_ALLOW_DEPLOY=true + guardrails.deploy.allow_deploy); dry-run builds only unless "
                    "dry_run=false; production-looking spaces are blocked; credentials come only from CF_* env vars."),
    "schema": {"type": "object", "properties": {
        "project_dir": {"type": "string", "description": "Path to the MTA project (contains mta.yaml)"},
        "space": {"type": "string", "description": "Target Cloud Foundry space — dev/test only"},
        "org": {"type": "string", "description": "CF org (or CF_ORG env)"},
        "api": {"type": "string", "description": "CF API endpoint (or CF_API env)"},
        "dry_run": {"type": "boolean", "description": "Default true — build the MTA but do not deploy"},
        "prereqs_confirmed": {"type": "boolean", "description": "Set true ONLY after the Step 13 BTP prerequisite gate confirms in-tenant dependencies (custom fields, communication scenario/arrangement, RAP/CDS objects) are deployed & active"}},
        "required": []},
    "handler": tool_btp_deploy,
}

TOOLS["file_probe"] = {
    "description": "Pipeline helper: probe a file's structure.",
    "schema": {"type": "object", "properties": {
        "file_path": {"type": "string"}}, "required": []},
    "handler": tool_file_probe,
}

TOOLS["extract_docx"] = {
    "description": "Pipeline helper: extract plain text from a .docx file (ZIP+XML parsing).",
    "schema": {"type": "object", "properties": {
        "file_path": {"type": "string", "description": "Absolute path to the .docx file"}},
        "required": []},
    "handler": tool_extract_docx,
}

# ── SAP Scope Item Catalog (offline governance: lookup + dependency graph) ─────
SCOPE_CATALOG  = _load_json("catalog/scope_items.json",
                            default={"scope_items": [], "retired_scope_items": []})
_SCOPE_BY_ID   = {s["scope_item_id"]: s for s in SCOPE_CATALOG.get("scope_items", [])}
_RETIRED_BY_ID = {s["scope_item_id"]: s for s in SCOPE_CATALOG.get("retired_scope_items", [])}
_SCOPE_SOURCE  = ("SAP Scope Item Catalog (mcp-server/catalog/scope_items.json). Confirm current "
                  "availability in the tenant's SAP Central Business Configuration and the SAP "
                  "Best Practices Explorer (help.sap.com/docs/SAP_S4HANA_CLOUD).")

def tool_lookup_scope_item(args):
    sid = (args.get("scope_item_id") or "").strip().upper()
    if not sid:
        return {"error": "scope_item_id is required (e.g. J58, 1NT, BD9)"}
    item = _SCOPE_BY_ID.get(sid)
    if item:
        lobs = sorted({c["lob"] for c in item.get("classifications", []) if c.get("lob")})
        return {
            "found": True, "verified": True, "retired": False,
            "scope_item_id": sid,
            "description":   item.get("description"),
            "lines_of_business": lobs,
            "business_areas": sorted({c["business_area"] for c in item.get("classifications", [])
                                      if c.get("business_area")}),
            "component":     item.get("component"),
            "provisioning":  item.get("provisioning"),
            "required_scope_items": [e["to"] for e in item.get("required_scope_items", [])],
            "required_master_data": item.get("required_master_data", []),
            "available_country_count": item.get("available_country_count"),
            "source": _SCOPE_SOURCE,
        }
    if sid in _RETIRED_BY_ID:
        return {
            "found": True, "retired": True,
            "scope_item_id": sid,
            "description": _RETIRED_BY_ID[sid].get("description"),
            "warning": "This scope item is RETIRED by SAP — do NOT use it in new designs. "
                       "Find the current successor in SAP Best Practices Explorer.",
            "source": _SCOPE_SOURCE,
        }
    return {
        "found": False, "scope_item_id": sid,
        "note": "Not in the catalog seed. Verify in SAP Central Business Configuration / "
                "SAP Best Practices Explorer before using it.",
        "source": _SCOPE_SOURCE,
    }

def tool_scope_item_dependencies(args):
    sid = (args.get("scope_item_id") or "").strip().upper()
    if not sid:
        return {"error": "scope_item_id is required (e.g. J58, 1NT, BD9)"}
    item = _SCOPE_BY_ID.get(sid)
    if not item:
        if sid in _RETIRED_BY_ID:
            return {"scope_item_id": sid, "retired": True,
                    "warning": "Retired scope item — do not use.", "source": _SCOPE_SOURCE}
        return {"found": False, "scope_item_id": sid, "source": _SCOPE_SOURCE}
    requires = [{
        "scope_item_id": e["to"],
        "conditional":   e.get("conditional", False),
        "description":   (_SCOPE_BY_ID.get(e["to"], {}) or {}).get("description"),
        "retired":       e["to"] in _RETIRED_BY_ID,
    } for e in item.get("required_scope_items", [])]
    required_by = [{
        "scope_item_id": s["scope_item_id"], "description": s.get("description"),
    } for s in SCOPE_CATALOG.get("scope_items", [])
        if any(e["to"] == sid for e in s.get("required_scope_items", []))]
    return {
        "found": True, "scope_item_id": sid, "description": item.get("description"),
        "requires": requires,
        "required_by": required_by,
        "requires_count": len(requires), "required_by_count": len(required_by),
        "note": "Conditional (business-condition) dependencies are flagged. A retired "
                "prerequisite means the design needs review.",
        "source": _SCOPE_SOURCE,
    }

TOOLS["lookup_scope_item"] = {
    "description": ("Resolve an SAP S/4HANA Cloud Public Edition scope item ID (e.g. J58, 1NT, BD9 — "
                    "also the prefix of BPD file names) to its business meaning: description, line(s) of "
                    "business, business area, application component, provisioning (Default/Optional), "
                    "required master data, and country coverage. Flags RETIRED scope items as do-not-use. "
                    "Use INSTEAD of guessing what a scope item covers."),
    "schema": {"type": "object", "properties": {
        "scope_item_id": {"type": "string", "description": "3-char scope item ID, e.g. J58, 1NT, BD9"}},
        "required": ["scope_item_id"]},
    "handler": tool_lookup_scope_item,
}

TOOLS["scope_item_dependencies"] = {
    "description": ("Return the dependency graph for an SAP scope item: the scope items it REQUIRES "
                    "(hard vs conditional business-condition dependencies) and the scope items that "
                    "require IT (reverse dependents). Use to assess scope impact and prerequisites before "
                    "committing a solution to a scope item."),
    "schema": {"type": "object", "properties": {
        "scope_item_id": {"type": "string", "description": "3-char scope item ID, e.g. J58, 1NT, BD9"}},
        "required": ["scope_item_id"]},
    "handler": tool_scope_item_dependencies,
}

# ── Digital Brain (semantic RAG): merge the brain_server.py tool(s) ────────────
# One unified MCP server so a SINGLE enterprise-allowlisted registration exposes
# BOTH the offline governance tools above AND the Bedrock+FAISS brain search. The
# brain's handlers share this server's contract (handler(args) -> dict, wrapped by
# make_result), so they slot straight into TOOLS. Degrades gracefully: if the brain
# deps/index are absent (e.g. running locally, not on the EC2/Bedrock host), the
# tool returns a helpful message — the governance tools are unaffected.
try:
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    import brain_server as _brain_mod
    for _bname, _bspec in _brain_mod.TOOLS.items():
        TOOLS.setdefault(_bname, _bspec)
    log_stderr("brain tools registered: %s" % ", ".join(sorted(_brain_mod.TOOLS)))

    # ── Entity linking ────────────────────────────────────────────────────────
    # A retrieved delivery document names SAP objects, but a 2024 FD citing API_X says
    # nothing about whether API_X is released TODAY — the reader has to notice the name
    # and check it separately, which is the step that gets skipped. Annotate every hit
    # with a CURRENT verdict for the objects its text mentions.
    #
    # Done here rather than in brain_server.py on purpose: this module owns the catalog,
    # so brain_server stays pure retrieval. Wrapping also means the annotation cannot
    # change what was retrieved or how it was ranked — it only adds a field.
    def _wrap_search_brain(_inner):
        def _handler(args):
            payload = _inner(args)
            if not isinstance(payload, dict) or not payload.get("results"):
                return payload
            try:
                import entity_link                       # noqa: PLC0415
                import brain_search                      # scripts/ is on sys.path via brain_server
            except Exception:
                return payload                           # annotation is additive; never fatal
            memo = {}
            def _resolve(name):
                if name not in memo:
                    memo[name] = tool_check_object_release_state({"object_name": name})
                return memo[name]
            flagged = 0
            for hit in payload["results"]:
                try:
                    text = brain_search._read_chunk_text(hit.get("chunk_file"))
                except Exception:
                    continue
                if not text:
                    continue
                found = entity_link.annotate(text, _resolve)
                if found:
                    hit["objects_mentioned"] = found
                    flagged += sum(1 for f in found
                                   if f.get("verdict") == "NOT_AVAILABLE"
                                   or f.get("evidence") == "naming_heuristic_only")
            payload["objects_note"] = (
                "objects_mentioned lists SAP object names found IN the retrieved text, each "
                "with its verdict from the live catalog as of now — not as of when the "
                "document was written. Treat it as a lead, not as the document's own claim: "
                "%d mentioned object(s) are NOT_AVAILABLE or name-unconfirmed. Re-verify on "
                "api.sap.com / the Released CDS Views list before using any of them."
                % flagged)
            return payload
        return _handler

    if "search_brain" in TOOLS:
        TOOLS["search_brain"] = dict(TOOLS["search_brain"],
                                     handler=_wrap_search_brain(TOOLS["search_brain"]["handler"]))
        log_stderr("entity linking active on search_brain")
except Exception as _bexc:
    log_stderr("brain tools NOT registered (%s) — governance tools unaffected" % _bexc)

def main():
    _METRICS["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    audit("server_start", {"mode": MODE, "python": sys.version.split()[0]})
    log_stderr("started (mode=%s)" % MODE)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg_id = msg.get("id")
        if "method" in msg and msg_id is None:
            continue  # notification (e.g. notifications/initialized) — no response
        if "method" not in msg:
            continue
        try:
            result = handle_request(msg)
            reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except ValueError as exc:
            reply = {"jsonrpc": "2.0", "id": msg_id,
                     "error": {"code": -32601, "message": str(exc)}}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": msg_id,
                     "error": {"code": -32603, "message": "Internal error: %s" % exc}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    audit("server_stop", {})

def cli():
    """CLI fallback so headless pipeline runs can use the tools without MCP wiring:
    python3 mcp-server/server.py --tool <name> ['<json-args>']"""
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    raw = sys.argv[3] if len(sys.argv) > 3 else "{}"
    if name not in TOOLS:
        print(json.dumps({"error": "unknown tool", "tools": sorted(TOOLS)}))
        sys.exit(2)
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": "bad json args: %s" % exc}))
        sys.exit(2)
    started = time.time()
    try:
        payload = TOOLS[name]["handler"](args)
        ok = True
    except GuardrailViolation as exc:
        payload = {"guardrail_blocked": True, "reason": str(exc)}
        ok = False
    duration = (time.time() - started) * 1000
    audit("cli_tool_call", {"tool": name, "arguments": args, "ok": ok, "duration_ms": int(duration)})
    record_call(name, duration, ok)
    out_path = os.environ.get("S4PC_CLI_OUT", "")
    if out_path:
        with open(out_path, "w", encoding="utf-8") as _fh:
            _fh.write(json.dumps(payload, indent=2, ensure_ascii=False))
    out = json.dumps(payload, indent=2, ensure_ascii=True)
    sys.stdout.buffer.write(out.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    sys.exit(0 if ok else 1)

def http_server(port=3000):
    """Streamable-HTTP MCP transport — for enterprise environments that block stdio servers.
    Run locally: python mcp-server/server.py --http [port]
    Then register: claude mcp add s4pc --transport http http://localhost:<port>/mcp
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    import uuid

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress per-request logs; audit() captures what matters

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
                body = json.dumps({"status": "ok", "server": "s4pc", "mode": MODE}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in ("/mcp", "/"):
                self.send_response(405)
                self.send_header("Allow", "POST, OPTIONS")
                self._cors()
                self.end_headers()
            else:
                self.send_error(404)

        def _reject(self, code, payload, extra=None):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path not in ("/mcp", "/"):
                self.send_error(404)
                return

            ok, caller, allowed_tools = _authenticate(self.headers)
            if not ok:
                audit("auth_denied", {"peer": self.client_address[0], "path": self.path})
                return self._reject(401, {"error": "unauthorized"},
                                    {"WWW-Authenticate": 'Bearer realm="s4pc"'})
            _CALLER.name = caller

            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                msg = json.loads(raw)
            except Exception as exc:
                self.send_error(400, "Bad request: %s" % exc)
                return

            # A restricted key must not be able to invoke a tool outside its allowlist.
            if allowed_tools is not None and msg.get("method") == "tools/call":
                requested = (msg.get("params") or {}).get("name") or ""
                if requested not in allowed_tools:
                    audit("tool_denied", {"tool": requested})
                    return self._reject(403, {
                        "jsonrpc": "2.0", "id": msg.get("id"),
                        "error": {"code": -32000,
                                  "message": "Tool %r is not permitted for this key." % requested}})

            msg_id = msg.get("id")
            if msg_id is None:
                self.send_response(202)
                self._cors()
                self.end_headers()
                return

            try:
                result = handle_request(msg)
                # Don't advertise tools the key cannot call — a restricted client should
                # not see btp_deploy in its tool list at all.
                if (allowed_tools is not None and msg.get("method") == "tools/list"
                        and isinstance(result, dict)):
                    result["tools"] = [t for t in result.get("tools") or []
                                       if t.get("name") in allowed_tools]
                reply = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            except ValueError as exc:
                reply = {"jsonrpc": "2.0", "id": msg_id,
                         "error": {"code": -32601, "message": str(exc)}}
            except Exception as exc:
                reply = {"jsonrpc": "2.0", "id": msg_id,
                         "error": {"code": -32603, "message": "Internal error: %s" % exc}}

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

    # Loopback by default. This transport has NO authentication, and several tools
    # (file_probe, extract_docx) read caller-supplied paths, so a 0.0.0.0 bind hands
    # anything that can route to this host an unauthenticated read of everything the
    # service user can read. An SSH tunnel does NOT require a wildcard bind — the
    # forward's target is resolved on this side, so 127.0.0.1 serves it fine.
    # Override only when something in front of it terminates TLS and authenticates
    # (see docs/brain-endpoint-setup.md).
    host = os.environ.get("S4PC_MCP_HOST", "127.0.0.1")
    _METRICS["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    audit("server_start", {"mode": MODE, "transport": "http", "port": port, "host": host})
    log_stderr("HTTP MCP server started on %s:%d (mode=%s)" % (host, port, MODE))
    if host not in ("127.0.0.1", "localhost", "::1"):
        if _parse_api_keys():
            log_stderr("NOTE: bound to %s with API-key auth enabled." % host)
        else:
            # The combination that caused the 2026-09-03 exposure: reachable off-box
            # AND no credential required. Make it impossible to miss in the logs.
            log_stderr("*" * 78)
            log_stderr("WARNING: bound to %s with NO authentication (S4PC_API_KEYS unset)." % host)
            log_stderr("Every tool is callable by anything that can route to this host.")
            log_stderr("Set S4PC_API_KEYS or bind 127.0.0.1 — see docs/brain-endpoint-setup.md")
            log_stderr("*" * 78)
        audit("insecure_bind", {"host": host, "authenticated": bool(_parse_api_keys())})
    log_stderr("Register with: claude mcp add s4pc --transport http http://localhost:%d/mcp" % port)
    srv = _ThreadedServer((host, port), _Handler)
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--tool":
        cli()
    elif len(sys.argv) > 1 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
        http_server(port)
    else:
        main()
