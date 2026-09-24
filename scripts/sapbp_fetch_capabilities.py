#!/usr/bin/env python3
"""Fetch the business-capability taxonomy for each scope item.

WHAT THIS ADDS THAT processes.json DOES NOT
    A scope item's place in SAP's capability model, four levels deep:

        solution capability   Data Integration for S/4HANA (S/4 CLD Public)
        business capability   Data Integration for S/4HANA
        business area         Enterprise Information Management
        line of business      Database and Data Management

    Only the last of those is already in processes.json, as
    businessProcessGroupName. The business area and capability levels exist
    nowhere else in this brain, and they are what a functional lead actually
    names when scoping ("we need Enterprise Information Management").

WHY NOT SolutionCapabilityHierarchy
    Because it is the wrong entity, which cost an afternoon to establish. It
    carries one row per process with bcmType "LOB" and 13 distinct names, so it
    only restates the line of business. BcmOccurrenceWithSolutionProcessFlat is
    the same join with all four levels populated. Both are keyed on
    solutionProcess_ID; only one is worth fetching.

WHY IT MAY USE AN OLDER RELEASE THAN THE REST OF THE BRAIN
    SAP had published no capability rows for 2608 as of 2026-09-22 -- zero,
    against 1,172 for 2602 in Germany, and zero from the flat entities too. The
    taxonomy is not release-critical the way an API contract is: a business area
    called "Enterprise Information Management" does not stop existing at a
    release boundary. So this walks BACKWARDS through the releases of the same
    stableId until it finds one with data, and records which release that was.

    That is deliberately not a hardcoded fallback to 2602. When SAP populates
    2608, this picks 2608 with no code change and the recorded release updates
    itself. Every row carries `source_release`, and the process index copies it
    through, so a consumer can always see whether the capability data is from
    the release it is looking at.

    The join to our scope items goes through externalId, NOT the process GUID:
    GUIDs are per release, scope item codes are not. That is the only reason a
    cross-release join is possible at all.

Usage:
    export SAPME_COOKIE='<Cookie header from a pr.alm.me.sap.com request>'
    python3.11 scripts/sapbp_fetch_capabilities.py
    python3.11 scripts/sapbp_fetch_capabilities.py --dry-run
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sapbp_catalog import (                                  # noqa: E402
    RAW_DIR, DEFAULT_STABLE_ID, DEFAULT_COUNTRY, DEFAULT_LANGUAGE, DEFAULT_LANCODE,
    fetch_all, releases_for, NotAuthenticated,
)

OUT = RAW_DIR / "capabilities_by_scope.json"
ENTITY = "BcmOccurrenceWithSolutionProcessFlat"


def capabilities_for(scenario_id, country):
    rows, _, _ = fetch_all(
        ENTITY,
        {"$filter": f"solutionScenario_ID eq {scenario_id} and country_ID eq '{country}'"},
        "capabilities", page=1000)
    return rows


def processes_for(scenario_id, country, lancode):
    """externalId per process GUID, for the release the capabilities came from."""
    scen = f"SolutionScenarioTranslation(ID={scenario_id},lanCode='{lancode}')"
    rows, _, _ = fetch_all(f"{scen}/solutionProcessTranslation",
                           {"$filter": f"country_ID eq '{country}'"},
                           "processes", page=1000)
    return {r.get("solutionProcessId"): (r.get("externalId") or "").strip().upper()
            for r in rows if r.get("externalId")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stable-id", dest="stable_id", default=DEFAULT_STABLE_ID)
    ap.add_argument("--country", default=DEFAULT_COUNTRY)
    ap.add_argument("--lancode", default=DEFAULT_LANCODE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    found, caps = None, []
    for r in releases_for(a.stable_id):
        rel, sid = r.get("targetRelease"), r.get("ID")
        caps = capabilities_for(sid, a.country)
        print(f"== release {rel}: {len(caps)} capability row(s)")
        if caps:
            found = r
            break
        print(f"   none published; trying the previous release")

    if not found:
        print("!! no release of %s has capability data for %s. Nothing written."
              % (a.stable_id, a.country))
        return 0

    rel = found.get("targetRelease")
    pid_scope = processes_for(found.get("ID"), a.country, a.lancode)

    by_scope, orphan = {}, 0
    for c in caps:
        ext = pid_scope.get(c.get("solutionProcess_ID"))
        if not ext:
            orphan += 1
            continue
        entry = {
            "solution_capability": c.get("scBcmName"),
            "business_capability": c.get("bcBcmName"),
            "business_area":       c.get("baBcmName"),
            "line_of_business":    c.get("lbBcmName"),
        }
        bucket = by_scope.setdefault(ext, [])
        if entry not in bucket:
            bucket.append(entry)

    print(f"== scope items with capabilities: {len(by_scope)}")
    print(f"   capability rows joined        : {len(caps) - orphan}")
    print(f"   rows whose process is unknown : {orphan}")
    print(f"   distinct business areas       : "
          f"{len({e['business_area'] for v in by_scope.values() for e in v})}")
    if rel:
        print(f"   source release                : {rel}")

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    OUT.write_text(json.dumps({
        "_meta": {
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entity": ENTITY,
            "source_release": rel,
            "scenario_id": found.get("ID"),
            "stable_id": a.stable_id,
            "country": a.country,
            "scope_items": len(by_scope),
        },
        "by_scope_item": by_scope,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n   wrote {OUT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NotAuthenticated as exc:
        sys.exit(f"\nNOT AUTHENTICATED: {exc}")
