#!/usr/bin/env python3
"""
Bulk sync: SAP Business Accelerator Hub → catalog.db (SQLite)

Requires:
  SAP_HUB_API_KEY — free from https://api.sap.com  (log in → click your name → Show API Key)

Usage:
  python mcp-server/catalog/sync_hub.py              # fetch + merge + write to catalog.db
  python mcp-server/catalog/sync_hub.py --dry-run    # fetch + report only, no writes
  python mcp-server/catalog/sync_hub.py --rebuild    # also rebuild vector index + object graph
  python mcp-server/catalog/sync_hub.py --probe      # show a 3-item sample per type and exit

Endpoint (confirmed):
  GET https://api.sap.com/api/1.0/container/SAPS4HANACloud/artifacts
      ?containerType=product&$filter=Type eq '{TYPE}'&$top=200&$skip=N

Artifact type → SQLite table:
  API / Event  →  apis      (protocol = OData V2/V4/SOAP/REST/Event)
  CDSVIEW      →  cds_views
  BADI         →  badis
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Progress output contains non-ASCII characters; a Windows cp1252 console would raise
# UnicodeEncodeError and abort a sync that had otherwise succeeded.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE         = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import db  # noqa: E402  (after sys.path setup)
_VECTOR_BUILD = os.path.join(os.path.dirname(_HERE), "vector", "build_index.py")

HUB_BASE      = "https://api.sap.com"
PRODUCT_ID    = "SAPS4HANACloud"
ARTIFACT_PATH = "/api/1.0/container/%s/artifacts" % PRODUCT_ID
PAGE_SIZE     = 200

# Types to fetch — order matters only for display
ARTIFACT_TYPES = ["API", "Event", "CDSVIEW", "BADI"]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _build_url(path, params=None):
    """Build URL keeping $ literal in OData param names ($filter, $top, $skip)."""
    if not params:
        return HUB_BASE + path
    parts = []
    for k, v in params.items():
        ek = ("$" + urllib.parse.quote(k[1:], safe="")) if k.startswith("$") else urllib.parse.quote(k, safe="")
        parts.append("%s=%s" % (ek, urllib.parse.quote(str(v), safe="")))
    return HUB_BASE + path + "?" + "&".join(parts)


_SSL_CTX = None

def _ssl_context():
    """TLS context that works behind a corporate inspection proxy WITHOUT disabling verification.

    Two problems bite on a managed Windows laptop:
      1. Python 3.13+ turns on ssl.VERIFY_X509_STRICT by default. Many proxy CAs (Zscaler, BlueCoat,
         Netskope …) issue certificates whose basicConstraints extension is not marked critical, which
         strict mode rejects with 'Basic Constraints of CA cert not marked critical'. We clear ONLY
         that structural flag — chain building and hostname checking stay fully enabled.
      2. Python trusts its own bundled CA list, not the Windows certificate store where the corporate
         root actually lives. Load the Windows ROOT/CA stores too (stdlib, no dependency).

    Overrides: SSL_CERT_FILE=<pem> to pin a specific bundle. S4PC_SYNC_INSECURE=true disables
    verification entirely — last resort, prints a warning, never the default."""
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    import ssl
    if (os.environ.get("S4PC_SYNC_INSECURE", "").lower() == "true"):
        print("\n  [WARN] S4PC_SYNC_INSECURE=true — TLS certificate verification is DISABLED for this "
              "sync. Only acceptable on a trusted network; unset it as soon as the corporate CA is "
              "configured.", flush=True)
        ctx = ssl._create_unverified_context()
        _SSL_CTX = ctx
        return ctx
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT        # keep verification, drop the strict structure check
    if not os.environ.get("SSL_CERT_FILE"):
        for store in ("ROOT", "CA"):                    # trust the corporate root from the Windows store
            try:
                for cert, enc, _trust in ssl.enum_certificates(store):
                    if enc == "x509_asn":
                        try:
                            ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert))
                        except Exception:
                            pass
            except Exception:
                pass                                    # non-Windows, or store unavailable
    _SSL_CTX = ctx
    return ctx


def _get(path, api_key, params=None, timeout=30):
    url = _build_url(path, params)
    req = urllib.request.Request(url, headers={
        "APIKey": api_key,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            raw = resp.read()
            if not raw or not raw.strip():
                return None
            text = raw.decode("utf-8")
            if text.lstrip().startswith("<"):
                return None
            return json.loads(text)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        if e.code == 401:
            _die("HTTP 401 — API key rejected. Check SAP_HUB_API_KEY.\n  URL: %s" % url)
        if e.code == 403:
            _die("HTTP 403 — forbidden.\n  URL: %s" % url)
        if e.code == 404:
            return None
        _die("HTTP %d\n  URL: %s\n  %s" % (e.code, url, body))
    except json.JSONDecodeError as e:
        _die("Response is not JSON.\n  URL: %s\n  %s" % (url, e))
    except Exception as e:
        # Transient network/TLS hiccups are common through an inspection proxy and used to abort a
        # sync that had already downloaded thousands of rows. Retry with backoff before giving up.
        retries = int(os.environ.get("S4PC_SYNC_RETRIES", "3"))
        for attempt in range(1, retries + 1):
            time.sleep(2 * attempt)
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                    raw = resp.read()
                    if not raw or not raw.strip():
                        return None
                    text = raw.decode("utf-8")
                    if text.lstrip().startswith("<"):
                        return None
                    print(" [recovered after retry %d]" % attempt, end="", flush=True)
                    return json.loads(text)
            except Exception as retry_exc:
                e = retry_exc
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            hint = ("\n  This is a TLS trust problem, not an SAP problem — usually a corporate "
                    "inspection proxy.\n  Try: set SSL_CERT_FILE to your corporate root CA (.pem), or "
                    "as a last resort set S4PC_SYNC_INSECURE=true.")
        _die("Request failed after %d retries: %s\n  URL: %s%s" % (retries, e, url, hint))


def _die(msg):
    print("[ERROR]", msg)
    sys.exit(1)


def _items(data):
    if isinstance(data, list):
        return data
    for key in ("value", "content", "results", "items", "artifacts"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _total(data):
    if isinstance(data, dict):
        for key in ("@odata.count", "totalCount", "total", "count"):
            if isinstance(data.get(key), int):
                return data[key]
    return None


# ── Fetch ─────────────────────────────────────────────────────────────────────

# The Hub's state vocabulary differs BY ARTIFACT TYPE, which is easy to miss:
#   CDSVIEW / BADI  -> "RELEASED"
#   API / Event     -> "ACTIVE"   (no API or Event artifact is ever "RELEASED")
# Filtering on RELEASED alone therefore discarded every usable API (874 ACTIVE) and
# event (143 ACTIVE) while silently reporting "0 released", which is why the apis table
# never grew past its 74 seed rows and genuinely released APIs fell through to a
# naming_heuristic_only verdict. Excluded: DEPRECATED (not upgrade-safe) and BETA
# (no stable release contract, so it must not be presented as released).
USABLE_STATES  = {"RELEASED", "ACTIVE"}
EXCLUDED_STATES = {"DEPRECATED", "BETA"}


def fetch_type(api_key, artifact_type, dry_run=False):
    """Fetch all usable artifacts of artifact_type, paginated.

    "Usable" means State is RELEASED or ACTIVE — see USABLE_STATES for why both.
    """
    print("  %-8s ... " % artifact_type, end="", flush=True)
    all_items, skip = [], 0
    skipped = {}

    while True:
        data = _get(ARTIFACT_PATH, api_key, {
            "containerType": "product",
            "$filter": "Type eq '%s'" % artifact_type,
            "$top":    PAGE_SIZE,
            "$skip":   skip,
        })
        if data is None:
            break
        page = _items(data)
        if not page:
            break

        # Keep RELEASED or ACTIVE; count what was dropped so a state vocabulary we do
        # not recognise shows up in the output instead of vanishing as a silent zero.
        for e in page:
            st = (e.get("State") or "NONE").upper()
            if st in USABLE_STATES:
                all_items.append(e)
            else:
                skipped[st] = skipped.get(st, 0) + 1

        total = _total(data)
        if total and len(all_items) >= total:
            break
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
        if not dry_run:
            time.sleep(0.2)

    detail = ", ".join("%s %d" % (k, v) for k, v in sorted(skipped.items()))
    print("%d usable%s" % (len(all_items), (" (skipped: %s)" % detail) if detail else ""))
    if skipped and not (set(skipped) <= EXCLUDED_STATES):
        # An unfamiliar state means the Hub changed its vocabulary again — say so loudly
        # rather than under-reporting the catalog by an unknown amount.
        print("           WARNING: unrecognised state(s) %s — check whether these are usable"
              % ", ".join(sorted(set(skipped) - EXCLUDED_STATES)))
    return all_items


def fetch_all(api_key, dry_run=False):
    print("Fetching from SAP Business Accelerator Hub (%s) ...\n" % PRODUCT_ID)
    by_type = {}
    for t in ARTIFACT_TYPES:
        by_type[t] = fetch_type(api_key, t, dry_run=dry_run)
    return by_type


# ── Field helpers (Hub uses PascalCase field names) ───────────────────────────

def _f(entry, *keys):
    """Get first non-empty value from entry, trying each key (PascalCase and lower)."""
    for k in keys:
        v = entry.get(k) or entry.get(k.lower()) or entry.get(k.upper())
        if v:
            return str(v).strip()
    return ""


def _cds_technical_name(entry):
    """
    Extract the proper-cased CDS technical name from the Description field.
    Description format: "I_BusinessPartner (Basic)" or "/DCO/I_BizPrtn (Composite)"
    The Name field is UPPERCASE — Description has the correct casing.
    """
    desc = _f(entry, "Description")
    if desc and " (" in desc:
        return desc.split(" (")[0].strip()
    # fallback: use Name as-is (already uppercase, but better than nothing)
    return _f(entry, "Name")


def _api_hub_url(entry):
    name = _f(entry, "Name")
    explicit = _f(entry, "Url", "HubUrl", "DocumentationUrl")
    if explicit and explicit.startswith("http"):
        return explicit
    return ("https://api.sap.com/api/%s/overview" % name) if name else None


def _protocol(entry):
    sub = _f(entry, "SubType", "subType").upper()
    proto_map = {
        "ODATA":   "OData V2",
        "ODATAV4": "OData V4",
        "SOAP":    "SOAP",
        "REST":    "REST",
        "EVENT":   "Event",
    }
    return proto_map.get(sub, "OData V2")


# ── Map Hub artifact → local schema ──────────────────────────────────────────

def _to_api(e):
    name = _f(e, "Name")
    return {
        "name":                   name,
        "title":                  _f(e, "DisplayName") or name,
        "protocol":               _protocol(e),
        "area":                   None,
        "communication_scenario": None,
        "key_entities":           None,
        "operations":             None,
        "hub_url":                _api_hub_url(e),
        "notes":                  (_f(e, "Description") or None),
        "_source":                "hub_sync",
    }


def _to_event(e):
    entry = _to_api(e)
    entry["protocol"] = "Event"
    return entry


def _to_cds(e):
    return {
        "name":    _cds_technical_name(e),
        "replaces": None,
        "area":    None,
        "notes":   (_f(e, "DisplayName") or None),
        "_source": "hub_sync",
    }


def _to_badi(e):
    name = _f(e, "Name")
    return {
        "name":               name,
        "title":              _f(e, "DisplayName") or name,
        "area":               None,
        "extensibility_type": "key_user_custom_logic",
        "business_context":   None,
        "use_case":           (_f(e, "Description") or None),
        "verified_in_tenant": False,
        "_source":            "hub_sync",
    }


# ── Merge ─────────────────────────────────────────────────────────────────────

_API_BACKFILL  = ("hub_url", "title", "communication_scenario", "notes", "protocol", "area")
_CDS_BACKFILL  = ("notes",)
_BADI_BACKFILL = ("title", "use_case", "business_context", "area")


def _merge(existing, hub_entries, to_local_fn, backfill_fields):
    by_name = {a["name"]: a for a in existing if a.get("name")}
    added = updated = 0
    for raw in hub_entries:
        loc = to_local_fn(raw)
        if not loc.get("name"):
            continue
        if loc["name"] not in by_name:
            by_name[loc["name"]] = loc
            added += 1
        else:
            ex = by_name[loc["name"]]
            for f in backfill_fields:
                if not ex.get(f) and loc.get(f):
                    ex[f] = loc[f]
                    updated += 1
    return list(by_name.values()), added, updated


# ── Load helper (kept for --dry-run in-memory merge only) ────────────────────

def _load_from_db(loader_fn, key):
    try:
        return loader_fn().get(key, [])
    except Exception:
        return []


# ── Probe (diagnostic) ────────────────────────────────────────────────────────

def probe(api_key):
    print("Probe — fetching 3 sample artifacts per type from %s\n" % PRODUCT_ID)
    for t in ARTIFACT_TYPES:
        url = _build_url(ARTIFACT_PATH, {
            "containerType": "product",
            "$filter": "Type eq '%s'" % t,
            "$top": 3,
        })
        print("  Type: %s\n  URL : %s" % (t, url))
        data = _get(ARTIFACT_PATH, api_key, {
            "containerType": "product",
            "$filter": "Type eq '%s'" % t,
            "$top": 3,
        })
        if data is None:
            print("  Result: no response\n")
            continue
        items = _items(data)
        print("  Result: %d items" % len(items))
        for item in items[:2]:
            print("    Name=%-40s State=%s" % (
                item.get("Name", "?")[:40],
                item.get("State", "?"),
            ))
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync SAP Hub catalog -> catalog.db (SQLite)")
    parser.add_argument("--dry-run", action="store_true", help="Report only — do not write files")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild vector index after write")
    parser.add_argument("--probe",   action="store_true", help="Fetch 3 sample artifacts per type and exit")
    args = parser.parse_args()

    api_key = os.environ.get("SAP_HUB_API_KEY", "").strip()
    if not api_key:
        _die(
            "SAP_HUB_API_KEY is not set.\n"
            "  1. Go to https://api.sap.com and log in.\n"
            "  2. Click your name (top-right) → Show API Key.\n"
            "  3. Set the environment variable:\n"
            "       Windows:      set SAP_HUB_API_KEY=<your-key>\n"
            "       macOS/Linux:  export SAP_HUB_API_KEY=<your-key>\n"
            "  (Or use the UI: Settings → Catalyst → Catalog Sync.)"
        )

    if args.probe:
        probe(api_key)
        return

    print("S4PC Catalog Sync — SAP Business Accelerator Hub")
    print("  Product : SAP S/4HANA Cloud Public Edition (%s)" % PRODUCT_ID)
    print("  Types   : %s" % ", ".join(ARTIFACT_TYPES))
    if args.dry_run:
        print("  Mode    : DRY RUN (no writes)")
    print()

    by_type = fetch_all(api_key, dry_run=args.dry_run)

    print()
    if args.dry_run:
        # Compute stats in-memory without writing to the database
        existing_apis  = _load_from_db(db.load_apis,      "apis")
        existing_cds   = _load_from_db(db.load_cds_views, "views")
        existing_badis = _load_from_db(db.load_badis,     "badis")

        merged_a, added_a, bf_a = _merge(existing_apis,  by_type["API"],    _to_api,   _API_BACKFILL)
        merged_a, added_e, bf_e = _merge(merged_a,        by_type["Event"],  _to_event, _API_BACKFILL)
        merged_c, added_c, bf_c = _merge(existing_cds,   by_type["CDSVIEW"], _to_cds,  _CDS_BACKFILL)
        merged_b, added_b, bf_b = _merge(existing_badis, by_type["BADI"],    _to_badi, _BADI_BACKFILL)

        print("apis      existing=%-5d  new=%-5d  backfilled=%d  total=%d"
              % (len(existing_apis), added_a + added_e, bf_a + bf_e, len(merged_a)))
        print("cds_views existing=%-5d  new=%-5d  backfilled=%d  total=%d"
              % (len(existing_cds), added_c, bf_c, len(merged_c)))
        print("badis     existing=%-5d  new=%-5d  backfilled=%d  total=%d"
              % (len(existing_badis), added_b, bf_b, len(merged_b)))
        print()
        print("DRY RUN complete — nothing written.")
        return

    # ── Write to SQLite ────────────────────────────────────────────────────────
    hub_apis = [_to_api(e) for e in by_type["API"]] + [_to_event(e) for e in by_type["Event"]]
    ex_a, added_a, bf_a = db.merge_apis(hub_apis)
    print("apis      existing=%-5d  new=%-5d  backfilled=%d  total=%d"
          % (ex_a, added_a, bf_a, ex_a + added_a))

    hub_cds = [_to_cds(e) for e in by_type["CDSVIEW"]]
    ex_c, added_c, bf_c = db.merge_cds_views(hub_cds)
    print("cds_views existing=%-5d  new=%-5d  backfilled=%d  total=%d"
          % (ex_c, added_c, bf_c, ex_c + added_c))

    hub_badis = [_to_badi(e) for e in by_type["BADI"]]
    ex_b, added_b, bf_b = db.merge_badis(hub_badis)
    print("badis     existing=%-5d  new=%-5d  backfilled=%d  total=%d"
          % (ex_b, added_b, bf_b, ex_b + added_b))

    print()
    print("Saved to: %s" % db.DB_PATH)

    if args.rebuild:
        import subprocess
        _GRAPH_BUILD = os.path.join(os.path.dirname(_HERE), "graph", "build_graph.py")

        print()
        print("Rebuilding vector index ...")
        if os.path.isfile(_VECTOR_BUILD):
            r = subprocess.run([sys.executable, _VECTOR_BUILD], capture_output=True, text=True)
            print(r.stdout.strip())
            if r.returncode != 0:
                print("[WARN] Vector index rebuild failed:", r.stderr.strip())
        else:
            print("[WARN] %s not found — run it manually." % _VECTOR_BUILD)

        print()
        print("Rebuilding object graph ...")
        if os.path.isfile(_GRAPH_BUILD):
            r = subprocess.run([sys.executable, _GRAPH_BUILD], capture_output=True, text=True)
            print(r.stdout.strip())
            if r.returncode != 0:
                print("[WARN] Object graph rebuild failed:", r.stderr.strip())
        else:
            print("[WARN] %s not found — run it manually." % _GRAPH_BUILD)
        return

    print()
    print("Next steps:")
    print("  python mcp-server/vector/build_index.py   (semantic search)")
    print("  python mcp-server/graph/build_graph.py    (object graph)")


if __name__ == "__main__":
    main()
