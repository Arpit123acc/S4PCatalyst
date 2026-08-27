#!/usr/bin/env python3
"""
SharePoint RAG Ingest — Delegated Permissions (Device Code Flow)

Connects to SharePoint via Microsoft Graph API using delegated auth.
First run: prints a URL + code for browser login (device code flow).
Subsequent runs: uses cached refresh token automatically (~90 days).

Usage:
    python3.11 scripts/sharepoint_ingest.py

Env vars (set in ~/.bashrc or EC2 environment):
    GRAPH_TENANT_ID       Azure AD Directory (tenant) ID
    GRAPH_CLIENT_ID       App registration Application (client) ID
    SHAREPOINT_SITE_URL   e.g. https://accenture.sharepoint.com/sites/S4HANADocs

Install dependencies first:
    pip3.11 install msal requests python-docx pymupdf python-pptx
"""

import os
import sys
import json
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime

try:
    import msal
    import requests
except ImportError:
    print("Missing dependencies. Run:")
    print("  pip3.11 install msal requests python-docx pymupdf python-pptx")
    sys.exit(1)

# ── CONFIG ───────────────────────────────────────────────────────────────────
TENANT_ID  = os.environ.get("GRAPH_TENANT_ID", "")
CLIENT_ID  = os.environ.get("GRAPH_CLIENT_ID", "")
SITE_URL   = os.environ.get("SHAREPOINT_SITE_URL", "")
LIBRARY    = os.environ.get("SHAREPOINT_LIBRARY", "Shared Documents")
SUBFOLDER  = os.environ.get("SHAREPOINT_SUBFOLDER", "")  # e.g. "Folder/SubFolder"

SCOPES     = ["Sites.Read.All", "Files.Read.All"]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

BASE_DIR        = Path(__file__).resolve().parent.parent
BRAIN_DIR       = BASE_DIR / "brain" / "sharepoint"
RAW_DIR         = BRAIN_DIR / "raw"
CHUNKS_DIR      = BRAIN_DIR / "chunks"
TOKEN_CACHE     = BASE_DIR / "brain" / ".token_cache.json"
LOG_FILE        = BASE_DIR / "brain" / "ingest.log"

CHUNK_WORDS     = 512
CHUNK_OVERLAP   = 64
SUPPORTED_EXT   = {".docx", ".pdf", ".pptx", ".txt", ".md"}

# ── LOGGING ──────────────────────────────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("sharepoint_ingest")

# ── TOKEN CACHE ───────────────────────────────────────────────────────────────
def _load_cache():
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE.exists():
        cache.deserialize(TOKEN_CACHE.read_text())
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(cache.serialize())
        TOKEN_CACHE.chmod(0o600)  # owner-only

# ── AUTHENTICATION ────────────────────────────────────────────────────────────
def get_token() -> str:
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
        print(flow["message"])   # prints URL + code for user to open
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")

    _save_cache(cache)
    log.info("Authenticated successfully.")
    return result["access_token"]

# ── GRAPH API ─────────────────────────────────────────────────────────────────
def _get(token, path, params=None):
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def get_site_id(token, site_url) -> str:
    hostname = site_url.split("/")[2]
    path     = "/".join(site_url.rstrip("/").split("/")[3:])
    data = _get(token, f"/sites/{hostname}:/{path}")
    return data["id"]

def list_files(token, site_id, library_name, subfolder="") -> tuple:
    drives = _get(token, f"/sites/{site_id}/drives")["value"]
    drive  = next((d for d in drives if d["name"] == library_name), drives[0])
    did    = drive["id"]
    # navigate into subfolder if specified
    if subfolder:
        folder_path = subfolder.strip("/")
        base_url = f"/drives/{did}/root:/{folder_path}:/children"
    else:
        base_url = f"/drives/{did}/root/children"
    items, url = [], base_url
    while url:
        data = _get(token, url, {"$top": 200})
        items.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink", "")
        url = next_link.replace(GRAPH_BASE, "") if next_link else None
    files = [i for i in items
             if "file" in i and Path(i["name"]).suffix.lower() in SUPPORTED_EXT]
    return did, files

def download(token, drive_id, item_id, dest: Path):
    resp = requests.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        allow_redirects=True,
        timeout=120,
    )
    resp.raise_for_status()
    dest.write_bytes(resp.content)

# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────
def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return path.read_text(errors="ignore")
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(path).paragraphs if p.text.strip())
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
        log.warning(f"Extraction failed [{path.name}]: {e}")
    return ""

# ── MASKING ───────────────────────────────────────────────────────────────────
_MASK_PATTERNS = [
    r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)* (?:AG|GmbH|Ltd|Inc|Corp|SE|NV|PLC|SA)\b",
    r"\bProject [A-Z][A-Za-z]+\b",
    r"\b[A-Z]{2,6}-\d{4,}\b",   # ticket IDs like PROJ-1234
]

def mask(text: str) -> str:
    for pat in _MASK_PATTERNS:
        text = re.sub(pat, "[CLIENT]", text)
    return text

# ── CHUNKING ──────────────────────────────────────────────────────────────────
def chunk(text: str) -> list[str]:
    words, chunks, i = text.split(), [], 0
    while i < len(words):
        c = " ".join(words[i:i + CHUNK_WORDS])
        if c.strip():
            chunks.append(c)
        i += CHUNK_WORDS - CHUNK_OVERLAP
    return chunks

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    missing = [v for v in ["GRAPH_TENANT_ID","GRAPH_CLIENT_ID","SHAREPOINT_SITE_URL"] if not os.environ.get(v)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        print("Set them in ~/.bashrc:\n  export GRAPH_TENANT_ID=...")
        sys.exit(1)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    token = get_token()

    log.info(f"Resolving site: {SITE_URL}")
    site_id = get_site_id(token, SITE_URL)

    log.info(f"Listing files in library: '{LIBRARY}' / subfolder: '{SUBFOLDER}'")
    drive_id, files = list_files(token, site_id, LIBRARY, SUBFOLDER)
    log.info(f"Found {len(files)} supported file(s)")

    total_chunks = 0
    for item in files:
        name = item["name"]
        dest = RAW_DIR / name
        log.info(f"  ↓ {name}")
        download(token, drive_id, item["id"], dest)

        text   = extract_text(dest)
        text   = mask(text)
        chunks = chunk(text)

        doc_id = hashlib.md5(name.encode()).hexdigest()[:8]
        for idx, c in enumerate(chunks):
            out = CHUNKS_DIR / f"{doc_id}_{idx:04d}.json"
            out.write_text(json.dumps({
                "id":          f"{doc_id}_{idx:04d}",
                "source":      name,
                "chunk_index": idx,
                "text":        c,
                "ingested_at": datetime.utcnow().isoformat() + "Z",
            }, ensure_ascii=False, indent=2))
        total_chunks += len(chunks)
        log.info(f"     → {len(chunks)} chunks")

    log.info(f"Done. {len(files)} files, {total_chunks} total chunks.")
    log.info(f"Chunks: {CHUNKS_DIR}")
    log.info("Next step: run embedding pipeline once IAM role (Bedrock Titan) is ready.")

if __name__ == "__main__":
    main()
