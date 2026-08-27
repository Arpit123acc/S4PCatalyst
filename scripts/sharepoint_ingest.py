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
SUPPORTED_EXT = {".docx", ".pdf", ".pptx", ".txt", ".md"}

# ── PHASES ────────────────────────────────────────────────────────────────────
PHASES = ["prepare", "explore", "realize", "deploy", "run"]

def detect_phase(path_str: str) -> str:
    p = path_str.lower()
    for phase in PHASES:
        if phase in p:
            return phase.capitalize()
    return "General"

# ── CLIENT NAMES ─────────────────────────────────────────────────────────────
# Known client names — add more as needed
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
    # Known client names (exact match, case-insensitive)
    (re.compile(rf"\b(?:{_client_alternatives})\b", re.IGNORECASE), "[CLIENT]"),

    # Generic company names (Siemens AG, Bosch GmbH, etc.)
    (re.compile(
        r"\b[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+)*\s+"
        r"(?:AG|GmbH|Ltd|Inc|Corp|SE|NV|PLC|SA|LLC|LLP|BV|SAS|SpA)\b"
    ), "[CLIENT]"),

    # Person names with titles
    (re.compile(
        r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Eng)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"
    ), "[PERSON]"),

    # Person names in author/by/contact fields
    (re.compile(
        r"(?i)(?:author|by|prepared by|created by|contact|owner|lead)\s*:\s*"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"
    ), r"\1".replace(r"\1", "[PERSON]")),  # replace full match

    # Email addresses (may contain person names)
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),

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

        # Detect phase and client from folder structure
        rel_path  = str(f.relative_to(RAW_DIR))
        phase     = detect_phase(rel_path)
        client    = detect_client_from_path(rel_path)

        log.info("Processing: %s [phase=%s, client=%s]", f.name, phase, client)

        text   = extract_text(f)
        text   = mask(text)
        chunks = chunk(text)

        doc_id = hashlib.md5(rel_path.encode()).hexdigest()[:8]

        # Save chunks in phase-organised subfolders
        phase_dir = CHUNKS_DIR / phase
        phase_dir.mkdir(parents=True, exist_ok=True)

        for idx, c in enumerate(chunks):
            out = phase_dir / f"{doc_id}_{idx:04d}.json"
            out.write_text(json.dumps({
                "id":          f"{doc_id}_{idx:04d}",
                "source":      f.name,
                "relative_path": rel_path,
                "phase":       phase,
                "client":      "[CLIENT]",   # never store actual client name
                "chunk_index": idx,
                "total_chunks": len(chunks),
                "text":        c,
                "ingested_at": datetime.utcnow().isoformat() + "Z",
            }, ensure_ascii=False, indent=2))

        total_chunks += len(chunks)
        total_files  += 1
        log.info("  -> %d chunks saved to chunks/%s/", len(chunks), phase)

    log.info("Done. %d files, %d chunks across phases:", total_files, total_chunks)
    for phase_dir in sorted(CHUNKS_DIR.iterdir()):
        if phase_dir.is_dir():
            count = len(list(phase_dir.glob("*.json")))
            log.info("  %-12s %d chunks", phase_dir.name + ":", count)
    log.info("Next: run Bedrock Titan embeddings once IAM role is ready.")

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
        dest        = RAW_DIR / name

        log.info("Downloading: %s [phase=%s]", name, phase)
        download(token, did, item["id"], dest)

        text   = extract_text(dest)
        text   = mask(text)
        chunks = chunk(text)

        doc_id    = hashlib.md5(f"{folder_path}/{name}".encode()).hexdigest()[:8]
        phase_dir = CHUNKS_DIR / phase
        phase_dir.mkdir(parents=True, exist_ok=True)

        for idx, c in enumerate(chunks):
            out = phase_dir / f"{doc_id}_{idx:04d}.json"
            out.write_text(json.dumps({
                "id":           f"{doc_id}_{idx:04d}",
                "source":       name,
                "folder_path":  folder_path,
                "phase":        phase,
                "client":       "[CLIENT]",
                "chunk_index":  idx,
                "total_chunks": len(chunks),
                "text":         c,
                "ingested_at":  datetime.utcnow().isoformat() + "Z",
            }, ensure_ascii=False, indent=2))

        total_chunks += len(chunks)
        total_files  += 1
        log.info("  -> %d chunks [phase=%s]", len(chunks), phase)

    log.info("Done. %d files, %d total chunks.", total_files, total_chunks)
    for phase_dir in sorted(CHUNKS_DIR.iterdir()):
        if phase_dir.is_dir():
            count = len(list(phase_dir.glob("*.json")))
            log.info("  %-12s %d chunks", phase_dir.name + ":", count)

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
