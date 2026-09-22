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
# The GUID above is PER RELEASE, not per solution. stableId is what survives:
# LatestSolutionScenarioIds shows EARL_SolS-013 as 5c293206 for targetRelease
# 2608 (seq 1) and cfbe71c4 for 2602 (seq 2). Pinning the GUID therefore pins the
# RELEASE, so once 2611 ships this script would keep fetching 2608 -- successfully,
# with no error and no empty result, which is the failure shape this codebase
# keeps meeting. The GUID stays only as the offline fallback.
DEFAULT_STABLE_ID = "EARL_SolS-013"
DEFAULT_COUNTRY = "DE"
# DE is the spine because it carries the most solution processes (657). It is not
# everything: the 2608 release adds country-specific scope items for localization
# -- Brazilian master-data tax fields, Spanish/Canary SII, Thai tax invoicing,
# Italian CIG/CUP -- and those processes exist only under their own country.
#
# Measured 2026-09-22 across 20 countries: the union beyond DE is 22 scope items,
# and DE + BR + ES + US covers all 679 active ones with nothing left over. The
# other 56 countries add no scope item DE does not already have, so fetching them
# would multiply the catalog for nothing.
SECONDARY_COUNTRIES = ["BR", "ES", "US"]
DEFAULT_LANGUAGE = "EN"
DEFAULT_LANCODE = "en-US"

# SAP files cross-country material under the pseudo-country XX, and the whole
# Accelerators panel lives there: "Availability and dependencies of solution
# processes", the 16 "Highlights of ..." decks, set-up instructions, task
# tutorials, product assistance. Filtering Tier B/C on the real country alone
# dropped 762 rows for this scenario -- not a country subset but an entire
# CATEGORY of scenario-level documentation, which is why nothing looked missing:
# every scope item still had its test scripts.
GENERIC_COUNTRY = "XX"


def country_clause(countries):
    """OData `or` chain. Not `in (...)`: this CAP service has already shown it
    handles some operators unexpectedly, and an or-chain works everywhere."""
    return "(" + " or ".join("country_ID eq '%s'" % c for c in countries) + ")"

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


def fetch_all(entity, params, label, page=PAGE):
    """Page an entity set to exhaustion. Returns (rows, count, etag).

    `page` is tunable because row SIZE varies by orders of magnitude here. A
    thousand BOM items is a small response; a thousand flow diagrams carrying
    BPMN XML is ~35 MB, and the server closed the connection mid-body
    (IncompleteRead after 35,412,558 bytes). Page size has to follow the payload,
    not the row count.
    """
    rows, etag, total, skip = [], None, None, 0
    while True:
        q = dict(params)
        q["$top"] = page
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
        if len(batch) < page:
            break
        skip += page
        time.sleep(RATE_LIMIT_S)
    return rows, total, etag


def resolve_scenario(stable_id, explicit=None):
    """Newest scenario GUID for a stableId, with its release.

    Returns (guid, target_release, internal_version). `explicit` short-circuits
    the lookup so --scenario still pins a specific release deliberately; the
    point is that pinning should be a choice, not the default.

    seq orders the releases, 1 being current. internalVersion moves WITHIN a
    release too (11.4 against 10.8), so it is worth recording: content can change
    without targetRelease changing, and a refresh that only watched the release
    number would sit on a stale corpus until the next quarter.

    Falls back to DEFAULT_SCENARIO if the lookup fails, and says so. A fetch that
    silently used a stale GUID is exactly what this function exists to prevent, so
    it must not fail silently itself.
    """
    if explicit:
        return explicit, None, None
    try:
        rows, _, _ = fetch_all(
            "LatestSolutionScenarioIds",
            {"$filter": f"stableId eq '{stable_id}'", "$orderby": "seq"},
            "scenario versions", page=50)
    except Exception as exc:
        print(f"!! could not resolve {stable_id}: {exc}")
        print(f"!! falling back to the pinned GUID {DEFAULT_SCENARIO}")
        return DEFAULT_SCENARIO, None, None
    if not rows:
        print(f"!! {stable_id} returned no versions; falling back to {DEFAULT_SCENARIO}")
        return DEFAULT_SCENARIO, None, None
    top = min(rows, key=lambda r: r.get("seq") or 99)
    guid = top.get("ID") or DEFAULT_SCENARIO
    rel, ver = top.get("targetRelease"), top.get("internalVersion")
    others = ", ".join("%s(seq %s)" % (r.get("targetRelease"), r.get("seq"))
                       for r in rows if r.get("ID") != guid)
    print(f"== resolved {stable_id} -> release {rel} v{ver}  [{guid}]")
    if others:
        print(f"==   earlier: {others}")
    if guid != DEFAULT_SCENARIO:
        print(f"!! NOTE: newer than the pinned DEFAULT_SCENARIO ({DEFAULT_SCENARIO}).")
        print( "!!   A new release has shipped. Update the constant, and expect a")
        print( "!!   full re-fetch -- see docs/delta-refresh-design.md.")
    return guid, rel, ver


def mark_downloads(manifest, procs, primary):
    """Flag which rows are worth downloading, and say why the rest are not.

    Decided HERE rather than in sapme_fetch because this is where the inputs
    are: the country of each row and the set of scope items the primary country
    already covers. A downloader given only titles cannot work it out, and a
    rule split across two scripts is a rule that drifts.

    WHY NOT SIMPLY DOWNLOAD EVERYTHING
        The secondary countries exist for the 22 localized scope items DE lacks
        (see SECONDARY_COUNTRIES). For every OTHER scope item they carry a
        near-duplicate test script differing only in locale --
        2LH_S4CLD2608_BPD_EN_BR.docx against ..._EN_DE.docx. Test scripts are
        already 75% of the brain's corpus and needed a ranking penalty to stop
        them crowding out everything else; tripling them would undo that for
        almost no new content.

        So: everything under XX (the accelerators and scenario-level
        documentation, all of it new), everything under the primary country, and
        from the secondary countries only the scope items the primary one does
        not have.

    The skipped rows stay in the manifest with a reason. They are real
    artifacts, lookup_accelerator should still find them and report their URL,
    and a human may well want one -- not downloading is a corpus decision, not a
    claim the document does not exist.
    """
    home_items = {(p.get("externalId") or "").strip().upper()
                  for p in procs
                  if p.get("country_ID") == primary and p.get("externalId")}
    tally = Counter()
    for m in manifest:
        ctry = m.get("country")
        if ctry in (GENERIC_COUNTRY, primary):
            m["download"], m["skip_reason"] = True, None
        elif (m.get("scope_item") or "").upper() not in home_items:
            m["download"], m["skip_reason"] = True, None
        else:
            m["download"] = False
            m["skip_reason"] = ("duplicate of %s; localized copy of a scope "
                                "item the primary country already covers" % primary)
        tally[(ctry, m["download"])] += 1

    keep = sum(1 for m in manifest if m["download"])
    print("\n== download selection: %d of %d rows" % (keep, len(manifest)))
    for (ctry, dl), n in sorted(tally.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        print(f"   {n:>6}  {ctry or '(none)':<4} {'download' if dl else 'skip'}")


def fetch_l1(scenario, dry):
    """The three structured entities that make processes.json a graph.

    These are L1, not L4, and the distinction is load-bearing: "which apps does
    scope item 7TC use" is an exact lookup, and an exact lookup cannot invent a
    plausible-but-wrong app name the way a nearest-neighbour search can. That
    matters more here than in an ordinary RAG system, because this project's
    whole gate model exists to stop confident fabrication.

    The test-case workbooks were the other candidate source for the
    process -> application edge and turned out to be import templates with those
    columns blank (see sapme_ingest.xlsx_text). This is the only route.
    """
    out = {}

    def keep(name, rows):
        out[name] = rows
        if not dry:
            p = RAW_DIR / f"{name}.json"
            p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   wrote {len(rows)} -> {p}")

    # process -> Fiori application. Three columns, no prose: the clearest L1 case.
    apps, n_apps, _ = fetch_all(
        "SolutionProcessDiagramApplicationFilter", {}, "applications")
    keep("applications", apps)

    # The Business Capability Model: line of business -> business area ->
    # business capability -> solution capability, per process. Richer than the
    # single businessProcessGroupName the process rows carry.
    # Unfiltered on purpose. Filtering by solutionScenario_ID returned zero rows
    # for EARL_SolS-013 even though the entity is populated for other scenarios,
    # so the scenario is joined afterwards on solutionProcess_ID -- the same way
    # applications are, since that entity also spans every scenario (14,417 rows
    # over 1,966 processes against our 657). Filtering server-side here trades a
    # smaller download for silently losing rows, which is the wrong trade.
    caps, n_caps, _ = fetch_all("SolutionCapabilityHierarchy", {}, "capabilities")
    keep("capabilities", caps)

    # BPMN, so steps can be parsed rather than guessed. $select is deliberate --
    # the entity also carries SVG and JSON renderings of the same diagram, and
    # pulling all three for ~1,500 diagrams would multiply the payload for
    # pictures nothing downstream can read.
    diag, n_diag, _ = fetch_all(
        "SolutionProcessFlowDiagram",
        {"$select": "ID,name,stableId,businessId,diagramContentBpmn"}, "diagrams",
        page=100)
    keep("diagrams", diag)

    print(f"\n== applications {len(apps)}   capabilities {len(caps)}   diagrams {len(diag)}")
    if apps:
        per = Counter(r.get("solutionProcessID") for r in apps)
        print(f"   {len(per)} processes carry an application; "
              f"busiest has {max(per.values()) if per else 0}")
    if diag:
        withb = sum(1 for d in diag if d.get("diagramContentBpmn"))
        print(f"   {withb} of {len(diag)} diagrams carry BPMN")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default=None,
                    help="Pin a specific scenario GUID. Default: resolve the "
                         "newest for --stable-id.")
    ap.add_argument("--stable-id", dest="stable_id", default=DEFAULT_STABLE_ID)
    ap.add_argument("--country", default=DEFAULT_COUNTRY,
                    help="Primary country; its process row wins when several exist")
    ap.add_argument("--secondary", default=SECONDARY_COUNTRIES,
                    type=lambda v: [x.strip().upper() for x in v.split(",") if x.strip()],
                    help="Extra countries fetched for their localized scope items")
    ap.add_argument("--language", default=DEFAULT_LANGUAGE)
    ap.add_argument("--lancode", default=DEFAULT_LANCODE)
    ap.add_argument("--l1-only", action="store_true",
                    help="fetch only the structured L1 entities")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.l1_only:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        print(f"== service  {SERVICE}")
        guid, _, _ = resolve_scenario(a.stable_id, explicit=a.scenario)
        fetch_l1(guid, a.dry_run)
        return 0

    c, lang = a.country, a.language
    process_countries = [c] + [x for x in a.secondary if x and x != c]
    countries = process_countries + [GENERIC_COUNTRY]
    print(f"== service  {SERVICE}")
    scenario, target_release, internal_version = resolve_scenario(
        a.stable_id, explicit=a.scenario)
    print(f"== scenario {scenario}  country {c}  language {lang}")

    # --- Tier A: solution processes, each carrying its full HTML description -----
    # Reached through the navigation property rather than the entity set directly:
    # that is the path the UI itself uses, so it is the one SAP keeps working.
    scen = f"SolutionScenarioTranslation(ID={scenario},lanCode='{a.lancode}')"
    procs, n_procs, etag = fetch_all(
        f"{scen}/solutionProcessTranslation",
        {"$filter": country_clause(process_countries), "$orderby": "name"},
        "processes")

    # --- Tier B: the accelerator inventory --------------------------------------
    boms, n_boms, _ = fetch_all(
        "BomItemURLsWithMultiLanguage",
        {"$filter": f"{country_clause(countries)} "
                    f"and isValid eq true and isArchive eq false"},
        "bom items")

    # --- Tier C: the download URLs ----------------------------------------------
    # A SEPARATE entity on purpose. BomItemURLsWithMultiLanguage carries a `url`
    # column but no language in its key, so it cannot resolve one and returns ""
    # for every row -- which reads exactly like "no downloads exist". BomItemUrl is
    # keyed (bomItem_ID, country_ID, language_ID) and is the real link table.
    urls, n_urls, _ = fetch_all(
        "BomItemUrl",
        {"$filter": f"{country_clause(countries)} and language_ID eq '{lang}'"},
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
            # The ROW's country, not the requested one. These were identical
            # while the fetch was single-country, so stamping `c` looked right
            # and was; it became wrong the moment the filter covered DE, BR, ES,
            # US and XX at once, and every row then claimed to be German.
            "country": b.get("country_ID") or c,
            "language": lang,
            "url": url,
            "host": host,
            "ext": os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or None,
        })

    mark_downloads(manifest, procs, c)

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
        "scenario_id": scenario,
        "stable_id": a.stable_id,
        # Recorded so a later run can tell "same release, new content" from
        # "new release" -- internalVersion moves without targetRelease moving.
        "target_release": target_release,
        "internal_version": internal_version,
        "country": c,
        "countries": countries,
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
