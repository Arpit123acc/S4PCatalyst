#!/usr/bin/env python3
"""
SharePoint RAG Ingest — Delegated Permissions (Device Code Flow)

Connects to SharePoint via Microsoft Graph API using delegated auth.
First run: prints a URL + code for browser login (device code flow).
Subsequent runs: uses cached refresh token automatically (~90 days).

Features:
- Client name masking (specific + pattern-based)
- Person name masking
- Phase-aware chunking (Prepare / Explore / Realize / Deploy / Run)
- Recursive subfolder traversal
- Structured chunk metadata (phase, client, source)

Usage:
    python3.11 scripts/sharepoint_ingest.py           # Graph API mode
    python3.11 scripts/sharepoint_ingest.py --local   # local raw/ folder mode (POC)

Env vars:
    GRAPH_TENANT_ID       Azure AD Directory (tenant) ID
    GRAPH_CLIENT_ID       App registration Application (client) ID
    GRAPH_CLIENT_SECRET   Client secret value
    SHAREPOINT_SITE_URL   e.g. https://ts.accenture.com/sites/S4_HANA_POD_Harvesting
    SHAREPOINT_LIBRARY    Document library name (default: Shared Documents)
    SHAREPOINT_SUBFOLDER  Subfolder path within the library

Install:
    pip3.11 install msal requests python-docx pymupdf python-pptx
"""

import os
import sys
import json
import hashlib
import logging
import re
import argparse
from pathlib import Path
from datetime import datetime

# ── CONFIG ───────────────────────────────────────────────────────────────────
TENANT_ID  = os.environ.get("GRAPH_TENANT_ID", "")
CLIENT_ID  = os.environ.get("GRAPH_CLIENT_ID", "")
SITE_URL   = os.environ.get("SHAREPOINT_SITE_URL", "")
LIBRARY    = os.environ.get("SHAREPOINT_LIBRARY", "Shared Documents")
SUBFOLDER  = os.environ.get("SHAREPOINT_SUBFOLDER", "")

SCOPES     = ["Sites.Read.All", "Files.Read.All"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

BASE_DIR   = Path(__file__).resolve().parent.parent
BRAIN_DIR  = BASE_DIR / "brain" / "sharepoint"
RAW_DIR    = BRAIN_DIR / "raw"
CHUNKS_DIR = BRAIN_DIR / "chunks"
TOKEN_CACHE = BASE_DIR / "brain" / ".token_cache.json"
LOG_FILE   = BASE_DIR / "brain" / "ingest.log"

CHUNK_WORDS   = 512
CHUNK_OVERLAP = 64
SUPPORTED_EXT = {".docx", ".pdf", ".pptx", ".txt", ".md", ".xlsx"}

# ── PHASES ────────────────────────────────────────────────────────────────────
PHASES = ["prepare", "explore", "realize", "deploy", "run"]

def detect_phase(path_str: str) -> str:
    p = path_str.lower()
    for phase in PHASES:
        if phase in p:
            return phase.capitalize()
    return "General"

# ── AGENT ROLES ───────────────────────────────────────────────────────────────
_AGENT_ROLE_MAP = [
    ("pmo_agent",                   "PMO_Agent"),
    ("security_agent",              "Security_Agent"),
    ("solution_confirmation_agent", "Solution_Confirmation_Agent"),
    ("functional_agent",            "Functional_Agent"),
    ("build_agent",                 "Build_Agent"),
    ("data_agent",                  "Data_Agent"),
    ("qe_agent",                    "QE_Agent"),
    ("change_talent_agent",         "Change_Talent_Agent"),
    ("deployment_agent",            "Deployment_Agent"),
    ("run_support_agent",           "Run_Support_Agent"),
]

def detect_agent_role(path_str: str) -> str:
    p = path_str.lower()
    for role_key, _ in _AGENT_ROLE_MAP:
        if role_key in p:
            return role_key
    return "general"

# ── DELIVERABLE TYPES ─────────────────────────────────────────────────────────
_DELIVERABLE_MAP = [
    ("project_charter",         "project_charter"),
    ("ko_deck",                 "kickoff_deck"),
    ("l4_plan",                 "project_plan"),
    ("onboarding",              "onboarding_kit"),
    ("raci",                    "raci_matrix"),
    ("sow",                     "statement_of_work"),
    ("roles",                   "roles_authorization"),
    ("business_process",        "business_process_design"),
    ("fit_to_standard",         "fit_to_standard"),
    ("kdd",                     "kdd"),
    ("workshop",                "workshop_analysis"),
    ("wricef",                  "wricef_inventory"),
    ("functional_design",       "functional_design"),
    ("fd",                      "functional_design"),
    ("config",                  "configuration"),
    ("technical_design",        "technical_design"),
    ("td",                      "technical_design"),
    ("rap_code",                "rap_code"),
    ("cap_code",                "cap_code"),
    ("ui_code",                 "ui_code"),
    ("form_wizard",             "form_wizard"),
    ("interface",               "interface_spec"),
    ("iflow",                   "integration_iflow"),
    ("data_strategy",           "data_strategy"),
    ("data_migration",          "data_migration"),
    ("data_profiler",           "data_profiler"),
    ("test_strategy",           "test_strategy"),
    ("test_case",               "test_cases"),
    ("test_data",               "test_data"),
    ("change_impact",           "change_impact"),
    ("change_strategy",         "change_strategy"),
    ("training",                "training_material"),
    ("communication",           "communication_template"),
    ("cutover",                 "cutover_plan"),
    ("copy_reference",          "copy_reference"),
    ("defect",                  "defect_resolution"),
    ("knowledge",               "knowledge_base"),
    ("incident",                "incident_resolution"),
]

def detect_deliverable_type(path_str: str) -> str:
    p = path_str.lower()
    for keyword, deliverable in _DELIVERABLE_MAP:
        if keyword in p:
            return deliverable
    return "reference_document"

# ── CONTENT TYPE ──────────────────────────────────────────────────────────────
_CONTENT_TYPE_KEYWORDS = {
    "template": ["template", "templ", "blank", "format"],
    "example":  ["sample", "example", "demo", "ver 1", "ver1"],
    "reference": ["reference", "ref ", "guide", "handbook", "playbook"],
    "methodology": ["approach", "strategy", "framework", "methodology", "process"],
}

def detect_content_type(filename: str) -> str:
    f = filename.lower()
    for ctype, keywords in _CONTENT_TYPE_KEYWORDS.items():
        if any(kw in f for kw in keywords):
            return ctype
    return "document"

# ── CLIENT NAMES ─────────────────────────────────────────────────────────────
KNOWN_CLIENTS = [
    "BOBST", "CAMPARI", "CUMMINS", "BUMA", "MARS",
    "Altor Damas", "CDI", "AXA",
]

def detect_client_from_path(path_str: str) -> str:
    for client in KNOWN_CLIENTS:
        if client.lower() in path_str.lower():
            return client
    return "Unknown"

# ── LOGGING ───────────────────────────────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("sharepoint_ingest")

# ── MASKING ───────────────────────────────────────────────────────────────────
# Build client name pattern from known list
_client_alternatives = "|".join(re.escape(c) for c in KNOWN_CLIENTS)

_MASK_RULES = [
    # ── CREDENTIALS (highest risk — run first) ────────────────────────────────
    # password / key / token / secret followed by a value
    (re.compile(
        r"(?i)(?:password|passwd|api[_\s]?key|access[_\s]?key|secret[_\s]?key"
        r"|token|bearer|credential|auth[_\s]?key)\s*[:=]\s*\S+",
    ), "[CREDENTIAL]"),

    # ── CLIENT NAMES ──────────────────────────────────────────────────────────
    # Known client names (exact match, case-insensitive)
    (re.compile(rf"\b(?:{_client_alternatives})\b", re.IGNORECASE), "[CLIENT]"),

    # Generic company names (Siemens AG, Bosch GmbH, etc.)
    (re.compile(
        r"\b[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+)*\s+"
        r"(?:AG|GmbH|Ltd|Inc|Corp|SE|NV|PLC|SA|LLC|LLP|BV|SAS|SpA)\b"
    ), "[CLIENT]"),

    # SAP namespace objects named after client (ZBOBST_, ZCDI_, etc.)
    (re.compile(rf"\bZ(?:{_client_alternatives})[_A-Z0-9]*\b", re.IGNORECASE), "[CLIENT_OBJECT]"),

    # ── PERSON NAMES ──────────────────────────────────────────────────────────
    # Titled names (Mr/Mrs/Dr/Prof etc.)
    (re.compile(
        r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Eng)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"
    ), "[PERSON]"),

    # Author/contact/owner fields
    (re.compile(
        r"(?i)(?:author|by|prepared by|created by|modified by|contact|owner|lead"
        r"|reviewed by|approved by|assigned to)\s*:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"
    ), "[PERSON]"),

    # Standalone Firstname Lastname (two+ capitalised words)
    (re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b"), "[PERSON]"),

    # Employee IDs (Accenture I/C format: I123456, C123456)
    (re.compile(r"\b[IC]\d{6,7}\b"), "[EMP_ID]"),

    # ── CONTACT DETAILS ───────────────────────────────────────────────────────
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),

    # Phone numbers (international and local formats)
    (re.compile(
        r"(?:\+\d{1,3}[\s\-.]?)?"
        r"(?:\(?\d{2,4}\)?[\s\-.]?)?"
        r"\d{3,4}[\s\-.]?\d{3,4}[\s\-.]?\d{0,4}"
        r"(?=\s|$|[,;])"
    ), "[PHONE]"),

    # ── FINANCIAL ─────────────────────────────────────────────────────────────
    # Budget/cost figures with currency (€500K, $1.2M, USD 250,000)
    (re.compile(
        r"(?:USD|EUR|GBP|CHF|€|\$|£)\s?\d[\d,\.]*\s?(?:K|M|B|thousand|million)?\b",
        re.IGNORECASE
    ), "[AMOUNT]"),

    # Day rates (e.g. $1,800/day, €950 per day)
    (re.compile(
        r"(?:USD|EUR|GBP|€|\$|£)\s?\d[\d,\.]+\s?(?:/\s?day|per\s+day)",
        re.IGNORECASE
    ), "[RATE]"),

    # PO / contract numbers
    (re.compile(r"\b(?:PO|CONTRACT|ORDER)[-\s]?\d{4,}\b", re.IGNORECASE), "[CONTRACT_REF]"),

    # ── TECHNICAL / INFRASTRUCTURE ────────────────────────────────────────────
    # IP addresses (v4)
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[IP_ADDRESS]"),

    # Internal/client URLs and hostnames (not public SAP docs)
    (re.compile(
        r"https?://(?!help\.sap\.com|api\.sap\.com|cap\.cloud\.sap|ui5\.sap\.com"
        r"|www\.sap\.com|discovery\.sap\.com)[^\s\"'<>]+"
    ), "[INTERNAL_URL]"),

    # SAP tenant URLs (myXXXXXX.s4hana.ondemand.com)
    (re.compile(r"\bmy[A-Za-z0-9]+\.s4hana\.ondemand\.com\b"), "[SAP_TENANT_URL]"),

    # SAP logical system names (often contain client abbreviation + CLNT + number)
    (re.compile(r"\b[A-Z]{2,10}CLNT\d{3}\b"), "[LOGICAL_SYSTEM]"),

    # SAP transport request numbers (e.g. NPLK900123)
    (re.compile(r"\b[A-Z]{3}[KO]\d{6}\b"), "[TRANSPORT]"),

    # ── PROJECT REFERENCES ────────────────────────────────────────────────────
    # Project ticket IDs (PROJ-1234)
    (re.compile(r"\b[A-Z]{2,6}-\d{3,}\b"), "[TICKET]"),

    # Project codenames
    (re.compile(r"\bProject\s+[A-Z][A-Za-z]+\b"), "[PROJECT]"),
]

def mask(text: str) -> str:
    for pattern, replacement in _MASK_RULES:
        if callable(replacement):
            text = pattern.sub(replacement, text)
        else:
            text = pattern.sub(replacement, text)
    return text

# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────
def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return path.read_text(errors="ignore")
        if ext == ".docx":
            import docx
            doc = docx.Document(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if ext == ".pdf":
            import fitz
            doc = fitz.open(path)
            return "\n".join(page.get_text() for page in doc)
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(path)
            parts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        parts.append(shape.text)
            return "\n".join(parts)
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets:
                parts.append(f"[Sheet: {sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    line = "\t".join(str(c) for c in row if c is not None)
                    if line.strip():
                        parts.append(line)
            return "\n".join(parts)
    except Exception as e:
        log.warning("Extraction failed [%s]: %s", path.name, e)
    return ""

# ── CHUNKING ──────────────────────────────────────────────────────────────────
def chunk(text: str) -> list:
    words, chunks, i = text.split(), [], 0
    while i < len(words):
        c = " ".join(words[i:i + CHUNK_WORDS])
        if c.strip():
            chunks.append(c)
        i += CHUNK_WORDS - CHUNK_OVERLAP
    return chunks

# ── LOCAL MODE (POC — files already on EC2) ───────────────────────────────────
def process_local():
    """Process files already in brain/sharepoint/raw/ — no Graph API needed."""
    if not RAW_DIR.exists() or not any(RAW_DIR.iterdir()):
        log.error("No files found in %s — upload documents first via SCP", RAW_DIR)
        sys.exit(1)

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    total_chunks, total_files = 0, 0

    for f in sorted(RAW_DIR.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXT:
            continue

        rel_path       = str(f.relative_to(RAW_DIR))
        phase          = detect_phase(rel_path)
        agent_role     = detect_agent_role(rel_path)
        deliverable    = detect_deliverable_type(rel_path)
        content_type   = detect_content_type(f.name)

        log.info("Processing: %s [phase=%s, agent=%s, deliverable=%s]",
                 f.name, phase, agent_role, deliverable)

        text   = extract_text(f)
        text   = mask(text)
        chunks = chunk(text)

        doc_id    = hashlib.md5(rel_path.encode()).hexdigest()[:8]
        chunk_dir = CHUNKS_DIR / phase / agent_role
        chunk_dir.mkdir(parents=True, exist_ok=True)

        for idx, c in enumerate(chunks):
            out = chunk_dir / f"{doc_id}_{idx:04d}.json"
            out.write_text(json.dumps({
                "id":              f"{doc_id}_{idx:04d}",
                "source":          f.name,
                "relative_path":   rel_path,
                "phase":           phase,
                "agent_role":      agent_role,
                "deliverable_type": deliverable,
                "content_type":    content_type,
                "client":          "[CLIENT]",
                "chunk_index":     idx,
                "total_chunks":    len(chunks),
                "text":            c,
                "ingested_at":     datetime.utcnow().isoformat() + "Z",
            }, ensure_ascii=False, indent=2))

        total_chunks += len(chunks)
        total_files  += 1
        log.info("  -> %d chunks saved to chunks/%s/%s/", len(chunks), phase, agent_role)

    log.info("Done. %d files, %d chunks across phases:", total_files, total_chunks)
    for p_dir in sorted(CHUNKS_DIR.rglob("*.json")):
        pass  # counted below
    for p_dir in sorted(CHUNKS_DIR.iterdir()):
        if p_dir.is_dir():
            count = len(list(p_dir.rglob("*.json")))
            log.info("  %-12s %d chunks", p_dir.name + ":", count)
    log.info("Next: run Bedrock Titan embeddings.")

# ── TOKEN CACHE ───────────────────────────────────────────────────────────────
def _load_cache():
    try:
        import msal
    except ImportError:
        print("Run: pip3.11 install msal requests python-docx pymupdf python-pptx")
        sys.exit(1)
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE.exists():
        cache.deserialize(TOKEN_CACHE.read_text())
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(cache.serialize())
        TOKEN_CACHE.chmod(0o600)

# ── AUTH ──────────────────────────────────────────────────────────────────────
def get_token() -> str:
    import msal
    cache = _load_cache()
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )
    result = None
    accounts = app.get_accounts()
    if accounts:
        log.info("Using cached token...")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        log.info("No cached token — starting device code flow...")
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow error: {flow}")
        print("\n" + "=" * 60)
        print(flow["message"])
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")
    _save_cache(cache)
    log.info("Authenticated successfully.")
    return result["access_token"]

# ── GRAPH API ─────────────────────────────────────────────────────────────────
def _get(token, path, params=None):
    import requests
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def get_site_id(token, site_url) -> str:
    hostname = site_url.split("/")[2]
    path     = "/".join(site_url.rstrip("/").split("/")[3:])
    return _get(token, f"/sites/{hostname}:/{path}")["id"]

def list_files_recursive(token, drive_id, folder_path="") -> list:
    if folder_path:
        url = f"/drives/{drive_id}/root:/{folder_path.strip('/')}:/children"
    else:
        url = f"/drives/{drive_id}/root/children"
    items = []
    while url:
        data = _get(token, url, {"$top": 200})
        for item in data.get("value", []):
            if "folder" in item:
                sub = f"{folder_path}/{item['name']}".strip("/")
                items.extend(list_files_recursive(token, drive_id, sub))
            elif "file" in item and Path(item["name"]).suffix.lower() in SUPPORTED_EXT:
                item["_folder_path"] = folder_path
                items.append(item)
        next_link = data.get("@odata.nextLink", "")
        url = next_link.replace(GRAPH_BASE, "") if next_link else None
    return items

def download(token, drive_id, item_id, dest: Path):
    import requests
    resp = requests.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        allow_redirects=True, timeout=120,
    )
    resp.raise_for_status()
    dest.write_bytes(resp.content)

# ── GRAPH API MODE ────────────────────────────────────────────────────────────
def process_graph():
    missing = [v for v in ["GRAPH_TENANT_ID","GRAPH_CLIENT_ID","SHAREPOINT_SITE_URL"]
               if not os.environ.get(v)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    token   = get_token()
    site_id = get_site_id(token, SITE_URL)

    drives = _get(token, f"/sites/{site_id}/drives")["value"]
    drive  = next((d for d in drives if d["name"] == LIBRARY), drives[0])
    did    = drive["id"]

    log.info("Listing files recursively from: %s / %s", LIBRARY, SUBFOLDER)
    files = list_files_recursive(token, did, SUBFOLDER)
    log.info("Found %d supported files", len(files))

    total_chunks, total_files = 0, 0
    for item in files:
        name        = item["name"]
        folder_path = item.get("_folder_path", "")
        phase       = detect_phase(folder_path)
        agent_role  = detect_agent_role(folder_path)
        deliverable = detect_deliverable_type(folder_path)
        content_type = detect_content_type(name)
        dest        = RAW_DIR / name

        log.info("Downloading: %s [phase=%s, agent=%s, deliverable=%s]",
                 name, phase, agent_role, deliverable)
        download(token, did, item["id"], dest)

        text   = extract_text(dest)
        text   = mask(text)
        chunks = chunk(text)

        doc_id    = hashlib.md5(f"{folder_path}/{name}".encode()).hexdigest()[:8]
        chunk_dir = CHUNKS_DIR / phase / agent_role
        chunk_dir.mkdir(parents=True, exist_ok=True)

        for idx, c in enumerate(chunks):
            out = chunk_dir / f"{doc_id}_{idx:04d}.json"
            out.write_text(json.dumps({
                "id":              f"{doc_id}_{idx:04d}",
                "source":          name,
                "folder_path":     folder_path,
                "phase":           phase,
                "agent_role":      agent_role,
                "deliverable_type": deliverable,
                "content_type":    content_type,
                "client":          "[CLIENT]",
                "chunk_index":     idx,
                "total_chunks":    len(chunks),
                "text":            c,
                "ingested_at":     datetime.utcnow().isoformat() + "Z",
            }, ensure_ascii=False, indent=2))

        total_chunks += len(chunks)
        total_files  += 1
        log.info("  -> %d chunks [phase=%s, agent=%s]", len(chunks), phase, agent_role)

    log.info("Done. %d files, %d total chunks.", total_files, total_chunks)
    for p_dir in sorted(CHUNKS_DIR.iterdir()):
        if p_dir.is_dir():
            count = len(list(p_dir.rglob("*.json")))
            log.info("  %-12s %d chunks", p_dir.name + ":", count)

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true",
                        help="Process files already in brain/sharepoint/raw/ (POC mode)")
    args = parser.parse_args()

    if args.local:
        log.info("Running in LOCAL mode — processing files from %s", RAW_DIR)
        process_local()
    else:
        log.info("Running in GRAPH API mode")
        process_graph()

if __name__ == "__main__":
    main()
