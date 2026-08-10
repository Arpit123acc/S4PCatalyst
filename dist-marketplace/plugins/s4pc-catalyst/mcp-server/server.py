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
import time
import base64
import hashlib
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
EXPERIENCE    = _catalog_db.load_experience()

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

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
AUDIT_PATH = os.path.join(BASE_DIR, CONFIG.get("observability", {}).get("audit_log", "logs/audit.jsonl"))
METRICS_PATH = os.path.join(BASE_DIR, CONFIG.get("observability", {}).get("metrics_file", "logs/metrics.json"))

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

def audit(event, detail):
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "mode": MODE,
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
        "source": "seed catalog (mcp-server/catalog/released_apis.json), curated 2026-07-18",
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
        "source": "seed catalog (mcp-server/catalog/released_badis.json)",
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
    mapping = {}
    for view in CATALOG_CDS.get("views", []):
        for tab in view.get("replaces", []):
            mapping.setdefault(tab.upper(), []).append(view["name"])
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
            reason="BAPIs are not released in S/4HANA Cloud Public Edition. No exceptions.",
            alternative="Search released APIs for the same business object (search_released_apis).",
            source="SAP clean-core rule: only released APIs/BAdIs are consumable in Public Cloud")
        return result
    # Categorical clean-core NO — classical tables
    tmap = _table_map()
    if name in tmap:
        result.update(verdict="NOT_AVAILABLE", verified=True,
            reason="Classical SAP table %s is not released for Public Cloud custom code." % name,
            alternative="Use released CDS view(s): %s (confirm C1 on the Released CDS Views list / ADT)." % ", ".join(tmap[name]))
        return result
    # Seed-catalog hits
    for api in CATALOG_APIS.get("apis", []):
        if api["name"].upper() == name:
            result.update(verdict="LIKELY_RELEASED", reason="Found in seed catalog of released APIs; confirm on the SAP Business Accelerator Hub.", details=api)
            return result
    for badi in CATALOG_BADIS.get("badis", []):
        if badi["name"].upper() == name:
            result.update(verdict="LIKELY_RELEASED", reason="Found in seed catalog of released BAdIs — availability still depends on your release/scope; confirm on the List of BAdIs.", details=badi)
            return result
    for view in CATALOG_CDS.get("views", []):
        if view["name"].upper() == name:
            result.update(verdict="LIKELY_RELEASED", reason="Found in seed catalog of released CDS views; confirm C1 on the Released CDS Views list / ADT Released Objects.", details=view)
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
            reason=("Not in the offline seed, but the name matches SAP's RELEASED VDM CDS-view convention "
                    "(I_ interface / C_ consumption / A_ / R_ / E_ views) — the standard clean-core way to read "
                    "S/4HANA data. Treat as released for design purposes and CONFIRM the exact view's C1 release "
                    "on the Released CDS Views list / ADT Released Objects / View Browser before finalizing."),
            note=("A seed miss is NOT 'unreleased'. Only NOT_AVAILABLE (BAPIs, classical tables, enhancement "
                  "points, Smart Forms) forces a redesign — a CDS view marked LIKELY_RELEASED is a "
                  "confirm-in-tenant item, not a blocker."))
        return result
    # API-shaped names (OData / SOAP / event services) — confirm on the SAP Business Accelerator Hub.
    if obj_type in ("api", "odata", "soap", "service", "event") \
       or name.startswith(("API_", "CE_")) or name.endswith(("_SRV", "_IN", "_OUT")):
        result.update(verdict="LIKELY_RELEASED", verified=False,
            reason=("Not in the offline seed, but the name matches SAP's released OData/SOAP/event API naming "
                    "(API_*/*_SRV, SOAP *_IN/*_OUT, events CE_*). Released S/4HANA Cloud APIs are published on "
                    "the SAP Business Accelerator Hub — treat as released for design and CONFIRM the exact "
                    "service and its communication scenario on the Hub + the tenant Communication Arrangements "
                    "app before use."),
            hub_overview_url="https://api.sap.com/api/%s/overview" % name,
            hub_all_apis_url=REFERENCE_LINKS["sap_business_accelerator_hub"]["url"],
            note=("A seed miss is NOT 'unreleased'. Finalise every API against the SAP Business Accelerator Hub "
                  "(api.sap.com) — the authoritative public list of released S/4HANA Cloud APIs."))
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
    _catalog_db.append_experience(entry)

def tool_query_experience(args):
    query = (args.get("query") or "").strip().lower()
    category = (args.get("category") or "").strip().lower()
    hits = []
    for e in EXPERIENCE.get("entries", []):
        if category and e.get("category", "") != category:
            continue
        hay = " ".join([e.get("topic", ""), e.get("lesson", ""), e.get("category", ""),
                        " ".join(e.get("tags", []))]).lower()
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
        "usage_rules": [
            "Released CDS views -> SAP Help 'Released CDS Views' list (released_cds_views_list) + ADT Released Objects / View Browser; cite the list for every CDS view's release (C1) state.",
            "BAdIs -> SAP Help 'List of BAdIs' (released_badis_list) + Custom Logic app; cite it for every BAdI.",
            "APIs / integration content -> SAP Business Accelerator Hub; link each API's overview page in deliverables.",
            "BTP services + PRICING -> SAP Discovery Center; every side-by-side proposal links each service's page and names its pricing metric.",
            "Configuration objects, released applications, release notes, any other released objects -> S/4HANA Cloud docs root (sap_help_s4hana_cloud).",
            "Standard app check (fit-to-standard) -> Fiori Apps Library.",
            "SIDE-BY-SIDE (BTP) BUILDS: READ (WebFetch) the developer docs matching the object type you are building, per 'fetch_docs_by_object' — CAP/CAPM -> [cap_docs, nodejs_docs, npm_registry, javascript_ref]; UI5/Fiori -> [ui5_docs, javascript_ref, html_ref, css_ref, npm_registry]; both -> the union. For npm_registry, fetch its 'fetch_url' (https://registry.npmjs.org/<package> — JSON), not the npmjs.com web page. sap_community is CITE-ONLY (anti-bot blocks automated fetch) — link it for humans, do NOT fetch it. Ground the code in the fetched pages. If a fetch fails (e.g. proxy or site blocks it), fall back to citing the URL for manual verification — never block the build.",
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
                        "Call this for EVERY object referenced in a technical design."),
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
    file_path = args.get("file_path", "")
    if not file_path:
        file_path = os.path.join(BASE_DIR, "..", "input", "FD Test AI Stock Monitoring.docx.md")
    file_path = os.path.abspath(file_path)
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
    file_path = args.get("file_path", "")
    if not file_path:
        file_path = os.path.join(BASE_DIR, "..", "input", "FD Test AI Stock Monitoring.docx.md")
    file_path = os.path.abspath(file_path)
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
    user, pwd, token = os.environ.get("CF_USER", ""), os.environ.get("CF_PASSWORD", ""), os.environ.get("CF_TOKEN", "")
    if not (api and org):
        raise GuardrailViolation("Live deploy needs CF_API and CF_ORG (env or args).")
    if not ((user and pwd) or token):
        raise GuardrailViolation("Live deploy needs CF_USER+CF_PASSWORD or CF_TOKEN in the environment (never in code/args).")
    steps.append(run(["cf", "api", api]))
    if not token:
        steps.append(run(["cf", "auth", user, pwd], display="cf auth ****"))  # credentials redacted from output
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

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--tool":
        cli()
    main()
