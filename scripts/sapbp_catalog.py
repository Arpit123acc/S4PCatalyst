#!/usr/bin/env python3
"""
Fetch the SAP Best Practices catalogue from Signavio Process Navigator's OData service.

WHY THIS EXISTS
    mcp-server/catalog/processes.json has held ONE hand-seeded process (J59) since
    it was written, and its own _meta says what to do about that: "Replace with a
    real export and set tier accordingly: 'declared' for SAP Best Practice content
    from Signavio Process Navigator". Until then every pipeline run calls
    lookup_scope_item, gets nothing, and writes scope_items: [] -- so L1 learns no
    scope->object edges, on every run. This is that export.

WHAT IT PRODUCES  (three files, three different consumers)
    brain/sapbp/raw/processes.json      657 solution processes + full HTML
                                        descriptions  -> L1 processes.json AND L4 text
    brain/sapbp/raw/bom_manifest.json   ~6.2k accelerators: name, type, access level,
                                        scope item, download URL -> the Tier C manifest
    brain/sapbp/manifest.json           run record: counts, etag, host split

DESIGN -- WHY FETCH IS SPLIT FROM PARSE AND DOWNLOAD
    Same reason webdocs_ingest.py splits them: the network step is the flaky one.
    Keeping it separate means a failed fetch costs no embedding, the run resumes,
    and the retrieved JSON stays inspectable when something looks wrong later.

THE FAILURE THIS GUARDS AGAINST
    me.sap.com answers an unauthenticated request with HTTP 200 and an HTML SAML
    form -- measured 2026-09-19 against a .xlsx asset on support.sap.com, which
    returned 200, content-type text/html, and a <form id="samlform"> body. Every
    naive check passes. So every response here is checked for JSON-ness and for
    SAML markers BEFORE it is written, and the run aborts rather than persisting a
    directory full of identical login pages.

    The service is SAP-internal, not a published API: 343 entity sets, several
    named *Test / *Temp. We pin the four stable ones and record @metadataEtag so a
    schema change is reported instead of silently returning less.

USAGE
    export SAPME_COOKIE='<Cookie header from a pr.alm.me.sap.com request>'
    python3.11 scripts/sapbp_catalog.py --dry-run     # counts only, writes nothing
    python3.11 scripts/sapbp_catalog.py               # fetch + write

    Then:  python3.11 scripts/sapbp_fetch.py          # Tier C downloads
           python3.11 scripts/sapbp_ingest.py         # chunk + tag
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "brain" / "sapbp"
RAW_DIR = OUT_DIR / "raw"
MANIFEST = OUT_DIR / "manifest.json"

SERVICE = "https://pr.alm.me.sap.com/ui/earl-pn-ui/v1/odata/v4/EAXService"

# EARL_SolS-013 = "SAP Best Practices for SAP S/4HANA Cloud Public Edition".
# DE because it carries the fullest process set; the country is part of the data's
# identity, not a detail -- a DE-localised process may not exist for another
# country, and a brain that forgets which locale it indexed is confidently wrong.
DEFAULT_SCENARIO = "5c293206-d436-4b73-af8b-55a6e80a79a3"
DEFAULT_COUNTRY = "DE"
DEFAULT_LANGUAGE = "EN"
DEFAULT_LANCODE = "en-US"

PAGE = 1000          # CAP caps a page well below the row counts here; we page regardless
RATE_LIMIT_S = 0.4   # polite against someone else's service, and these are cheap reads
TIMEOUT_S = 90       # the process list carries an HTML document per row and is slow

SAML_MARKERS = ("samlform", "accounts.sap.com/saml2", "login.support.html",
                "SAMLRequest", "j_security_check")

UA = "S4PC-Catalyst-brain-ingest/1.0 (internal delivery accelerator)"

# Corporate TLS interception (measured on an Accenture laptop, 2026-09-19): the
# proxy re-signs with a CA whose Basic Constraints extension is not marked
# critical, which Python 3.14's OpenSSL refuses --
#   CERTIFICATE_VERIFY_FAILED: Basic Constraints of CA cert not marked critical
# The Windows certificate store already trusts that CA and is more forgiving, so
# `truststore` fixes it by verifying against the OS store instead of certifi.
# Optional on purpose: EC2 has no interception and stays pure-stdlib, which is
# this repo's rule. Never reach for ssl._create_unverified_context() here -- this
# fetch carries a session cookie, and disabling verification hands it to whatever
# is terminating the connection.
try:
    import truststore                       # noqa: PLC0415

    truststore.inject_into_ssl()
except ImportError:
    pass


class NotAuthenticated(RuntimeError):
    """The service answered with a login page rather than data."""


def _get(url):
    cookie = os.environ.get("SAPME_COOKIE", "").strip()
    if not cookie:
        sys.exit("SAPME_COOKIE is unset. Copy the Cookie header from a "
                 "pr.alm.me.sap.com request in DevTools and export it.")
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("Accept", "application/json;odata.metadata=minimal")
    req.add_header("User-Agent", UA)
    req.add_header("odata-version", "4.0")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        if e.code in (401, 403):
            raise NotAuthenticated(f"HTTP {e.code} — session rejected. {detail}") from e
        raise RuntimeError(f"HTTP {e.code} for {url}\n{detail}") from e

    # 200 + HTML is the documented failure here, so check the body, not the status.
    low = body.lstrip()[:2000].lower()
    if any(m.lower() in low for m in SAML_MARKERS) or low.startswith("<"):
        raise NotAuthenticated(
            "got an HTML login page instead of JSON — the cookie is missing, "
            "expired, or scoped to a different host (pr.alm.me.sap.com is NOT "
            "the same origin as me.sap.com).")
    return json.loads(body)


def fetch_all(entity, params, label):
    """Page an entity set to exhaustion. Returns (rows, count, etag)."""
    rows, etag, total, skip = [], None, None, 0
    while True:
        q = dict(params)
        q["$top"] = PAGE
        q["$skip"] = skip
        if skip == 0:
            q["$count"] = "true"
        # quote_via=quote, NOT the default quote_plus. quote_plus renders a space
        # as "+" even when space is in `safe`, and CAP does not decode "+" inside
        # $filter -- so `country_ID eq 'DE'` arrived as `country_ID+eq+'DE'`, which
        # parses as a string rather than a comparison and the service rejects the
        # filter with "The types 'Edm.Boolean' and 'Edm.String' are not compatible".
        # Spaces must be %20 here.
        url = f"{SERVICE}/{entity}?" + urllib.parse.urlencode(
            q, safe="(),'", quote_via=urllib.parse.quote)
        data = _get(url)
        etag = etag or data.get("@metadataEtag")
        if total is None:
            total = data.get("@odata.count")
        batch = data.get("value", [])
        rows.extend(batch)
        print(f"   {label}: {len(rows)}"
              + (f" / {total}" if total is not None else "") + " rows", flush=True)
        if len(batch) < PAGE:
            break
        skip += PAGE
        time.sleep(RATE_LIMIT_S)
    return rows, total, etag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default=DEFAULT_SCENARIO)
    ap.add_argument("--country", default=DEFAULT_COUNTRY)
    ap.add_argument("--language", default=DEFAULT_LANGUAGE)
    ap.add_argument("--lancode", default=DEFAULT_LANCODE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c, lang = a.country, a.language
    print(f"== service  {SERVICE}")
    print(f"== scenario {a.scenario}  country {c}  language {lang}")

    # --- Tier A: solution processes, each carrying its full HTML description -----
    # Reached through the navigation property rather than the entity set directly:
    # that is the path the UI itself uses, so it is the one SAP keeps working.
    scen = f"SolutionScenarioTranslation(ID={a.scenario},lanCode='{a.lancode}')"
    procs, n_procs, etag = fetch_all(
        f"{scen}/solutionProcessTranslation",
        {"$filter": f"country_ID eq '{c}'", "$orderby": "name"},
        "processes")

    # --- Tier B: the accelerator inventory --------------------------------------
    boms, n_boms, _ = fetch_all(
        "BomItemURLsWithMultiLanguage",
        {"$filter": f"country_ID eq '{c}' and isValid eq true and isArchive eq false"},
        "bom items")

    # --- Tier C: the download URLs ----------------------------------------------
    # A SEPARATE entity on purpose. BomItemURLsWithMultiLanguage carries a `url`
    # column but no language in its key, so it cannot resolve one and returns ""
    # for every row -- which reads exactly like "no downloads exist". BomItemUrl is
    # keyed (bomItem_ID, country_ID, language_ID) and is the real link table.
    urls, n_urls, _ = fetch_all(
        "BomItemUrl",
        {"$filter": f"country_ID eq '{c}' and language_ID eq '{lang}'"},
        "urls")

    by_item = {u["bomItem_ID"]: u["url"] for u in urls if u.get("url")}

    # Scope item comes from the PROCESS, not from parsing a string. The process
    # rows carry `externalId` (657/657 populated, 657 distinct), so a BOM item
    # inherits it by joining parentEntityId -> solutionProcessId. The regex below
    # is only a fallback: "SP_OP_5I2_1" -> 5I2, or a trailing "(6IL)" in the name.
    # An item whose parent is a SCENARIO rather than a process (parentEntityGlobalId
    # starting "ERL-") legitimately has no scope item -- that is not a miss.
    proc_by_id = {p.get("solutionProcessId"): p for p in procs}
    scope_re = re.compile(r"^SP[_-](?:OP[_-])?([0-9A-Z]{3})(?:_|$)")

    manifest, hosts, access = [], Counter(), Counter()
    for b in boms:
        url = by_item.get(b["ID"], "")
        parent = proc_by_id.get(b.get("parentEntityId")) or {}
        scope = parent.get("externalId")
        if not scope:
            m = (scope_re.match(b.get("parentEntityGlobalId") or "")
                 or re.search(r"\(([0-9A-Z]{3})\)\s*$", b.get("name") or ""))
            scope = m.group(1) if m else None
        host = urllib.parse.urlparse(url).netloc if url else ""
        hosts[host or "(no url)"] += 1
        access[b.get("accessLevel") or "(none)"] += 1
        manifest.append({
            "id": b["ID"],
            "name": b.get("name"),
            "bom_type": b.get("bomType"),
            "access_level": b.get("accessLevel"),
            "scope_item": scope,
            "lob": parent.get("businessProcessGroupName"),
            "process_name": parent.get("name"),
            "parent_global_id": b.get("parentEntityGlobalId"),
            "parent_is_scenario": str(b.get("parentEntityGlobalId") or "").startswith("ERL-"),
            "group_id": b.get("bomItemGroup_ID"),
            "scenario_id": b.get("solutionScenario_ID"),
            "country": c,
            "language": lang,
            "url": url,
            "host": host,
            "ext": os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or None,
        })

    with_url = sum(1 for m in manifest if m["url"])
    with_scope = sum(1 for m in manifest if m["scope_item"])

    print(f"\n== processes   {len(procs)} (service reports {n_procs})")
    print(f"== bom items   {len(boms)} (service reports {n_boms})")
    print(f"== url rows    {len(urls)} (service reports {n_urls})")
    print(f"== joined      {with_url} of {len(manifest)} have a URL")
    print(f"== scope items {with_scope} of {len(manifest)} resolved")
    print("\n== hosts (decides which downloads need a session)")
    for h, n in hosts.most_common(10):
        note = "  <- SAML, needs auth" if "support.sap.com" in h else (
               "  <- public docs" if "help.sap.com" in h else "")
        print(f"   {n:>6}  {h}{note}")
    print("\n== access levels")
    for k, n in access.most_common():
        print(f"   {n:>6}  {k}")

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "processes.json").write_text(
        json.dumps(procs, ensure_ascii=False, indent=2), encoding="utf-8")
    (RAW_DIR / "bom_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    MANIFEST.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "service": SERVICE,
        "scenario_id": a.scenario,
        "country": c,
        "language": lang,
        # A changed etag means SAP altered the contract. 343 entity sets, several
        # named *Test/*Temp -- this is an internal service and may move without
        # notice, so record it and compare on the next run.
        "metadata_etag": etag,
        "counts": {"processes": len(procs), "bom_items": len(boms),
                   "url_rows": len(urls), "with_url": with_url,
                   "with_scope_item": with_scope},
        "hosts": dict(hosts),
        "access_levels": dict(access),
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {RAW_DIR}/processes.json, {RAW_DIR}/bom_manifest.json")
    print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotAuthenticated as exc:
        sys.exit(f"\nNOT AUTHENTICATED: {exc}")
