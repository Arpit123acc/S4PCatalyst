#!/usr/bin/env python3
"""Join the L1 Process Navigator files into one compact index, keyed by scope item.

WHY A BUILD STEP RATHER THAN JOINING AT QUERY TIME
    The inputs are large and mostly irrelevant: capabilities.json alone is 77 MB
    and applications.json covers every solution scenario SAP publishes, while
    this brain needs one. Loading them per query would cost hundreds of MB on a
    host whose scaling wall is already RAM. The join is also stable between
    fetches, so doing it once per refresh is the right place for it -- the same
    reasoning as sapbp_distil_bpmn.py, which takes 477 MB of BPMN to 1.6 MB.

    Output is one row per scope item: name, LoB, Fiori applications, process
    steps and roles. Roughly 1-2 MB, cheap to hold open.

WHAT FEEDS IT, AND WHAT DOES NOT

    processes.json      657 rows for this scenario+country. The spine. Carries
                        externalId (the scope item, "2LH") and solutionProcessId
                        (the GUID everything else joins on).
    applications.json   4,602 of its 14,417 rows belong to our processes and
                        cover 537 of 657 (82%), 1,420 distinct Fiori apps. This
                        is the process -> application edge fetch_l1 was written
                        for, and the only route to it: the test-case workbooks
                        ship those columns blank.
    diagram_steps.json  1,670 diagrams for our scenario, joined by the scope-item
                        code that PREFIXES the diagram name ("2LH - 01 - ..."),
                        because the diagrams carry no externalId and their
                        business_id is null. 1,512 of 1,670 match (90%), covering
                        574 scope items; the rest are codes absent from this
                        country's process list, or untitled. Reported, not hidden.

    capabilities.json   EXCLUDED, because SAP publishes none for this release.
                        Measured 2026-09-22 against the live service:
                        SolutionCapabilityHierarchy/$count is 175,802 overall,
                        53,067 for the 2602 scenario and ZERO for 2608. Wiring it
                        in would add a field that is empty for every scope item
                        the brain holds.

                        It is still checked at build time rather than assumed
                        away -- if SAP publishes the hierarchy for a later
                        release, this picks it up with no code change and says so.
                        Do NOT substitute the 2602 rows: process GUIDs are
                        per-release, so the join would have to go through the
                        scope item, and the result would be a superseded
                        release's capability model presented as current.

Usage:
    python3.11 scripts/sapbp_build_process_index.py
    python3.11 scripts/sapbp_build_process_index.py --dry-run
"""

import re
import json
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RAW      = BASE_DIR / "brain" / "sapbp" / "raw"
MANIFEST = RAW.parent / "manifest.json"
OUT      = RAW / "process_index.json"

PROCESSES    = RAW / "processes.json"
APPLICATIONS = RAW / "applications.json"
DIAGRAMS     = RAW / "diagram_steps.json"
CAPABILITIES = RAW / "capabilities.json"

# A scope item is three alphanumerics at the start of a diagram name.
SCOPE_PREFIX = re.compile(r"^\s*([0-9A-Z]{3})\b")


def split_apps(name):
    """SAP comma-joins several Fiori apps into one applicationName. Split them.

    67 of 2,298 distinct names (2.9%) contain a comma, and they are lists rather
    than names: one reads "Buckets, Checklist Items, Classification Hierarchies,
    Collections, ..." across 21 apps. The decisive evidence is that 27 of the 181
    split parts appear INDEPENDENTLY as their own applicationName elsewhere in the
    same file, so the comma is a separator SAP chose and not punctuation inside a
    name.

    Leaving them joined undercounts every affected scope item and makes the app
    unfindable by name: 2LH reports 3 applications when it has 4, and a search for
    "Create Purchase Order" misses the row reading
    "Create Purchase Order, Create Purchase Order - Advanced".

    Third comma-joined multi-value field found in this service in one day, after
    `phase` and changeCategory's "Addition,Upgrade". Assume any SAP string field
    may be a list.

    A genuine app name containing a comma would be split wrongly. Nothing among
    the 2,298 looks like that, and applications.json keeps the original, so the
    mistake would be visible and reversible rather than baked in.
    """
    return [part.strip() for part in (name or "").split(",") if part.strip()]


def _load(path, default=None):
    if not path.exists():
        return default if default is not None else []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"!! {path.name} unreadable: {exc}")
        return default if default is not None else []


def fetched_scenario():
    """The scenario the fetch recorded — one source, same as the BPMN distiller."""
    m = _load(MANIFEST, {})
    return (m or {}).get("scenario_id"), (m or {}).get("target_release")


def build(scenario):
    procs = _load(PROCESSES)
    if not procs:
        raise SystemExit(f"{PROCESSES} missing — run sapbp_catalog.py first")

    # Spine. Keyed by scope item; a scope item can own several processes.
    by_scope = {}
    pid_scope = {}
    for p in procs:
        ext = (p.get("externalId") or "").strip().upper()
        if not ext:
            continue
        pid_scope[p.get("solutionProcessId")] = ext
        row = by_scope.setdefault(ext, {
            "scope_item": ext,
            "name": p.get("enName") or p.get("name"),
            "lob": p.get("businessProcessGroupName"),
            "change_category": p.get("changeCategory"),
            "license_required": p.get("licenseRequired"),
            "target_release": p.get("solutionScenarioTargetRelease"),
            "process_ids": [],
            "applications": [],
            "steps": [],
            "roles": [],
            "diagrams": [],
        })
        row["process_ids"].append(p.get("solutionProcessId"))

    # process -> Fiori applications
    apps = _load(APPLICATIONS)
    app_hits = 0
    for a in apps:
        ext = pid_scope.get(a.get("solutionProcessID"))
        if not ext:
            continue
        names = split_apps(a.get("applicationName"))
        by_scope[ext]["applications"].extend(names)
        if names:
            app_hits += 1

    # diagram steps, joined on the scope-item prefix of the diagram name
    diags = _load(DIAGRAMS)
    mine = [d for d in diags if not scenario or d.get("scenario_id") == scenario]
    matched, unmatched = 0, []
    for d in mine:
        m = SCOPE_PREFIX.match(d.get("name") or "")
        ext = m.group(1) if m else None
        if not ext or ext not in by_scope:
            unmatched.append(d.get("name"))
            continue
        row = by_scope[ext]
        row["steps"].extend(d.get("steps") or [])
        row["roles"].extend(d.get("roles") or [])
        row["diagrams"].append(d.get("name"))
        matched += 1

    # capabilities — checked, not assumed. See the module docstring.
    caps = _load(CAPABILITIES)
    cap_hits = 0
    for c in caps:
        ext = pid_scope.get(c.get("solutionProcess_ID"))
        if not ext:
            continue
        nm, ty = c.get("bcmName"), c.get("bcmType")
        if nm:
            by_scope[ext].setdefault("capabilities", []).append({"name": nm, "type": ty})
            cap_hits += 1

    # De-duplicate, preserving first-seen order so output is deterministic.
    def uniq(seq):
        seen, out = set(), []
        for v in seq:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    for row in by_scope.values():
        for f in ("applications", "steps", "roles", "diagrams", "process_ids"):
            row[f] = uniq(row[f])

    return by_scope, {
        "processes": len(procs),
        "scope_items": len(by_scope),
        "application_rows_used": app_hits,
        "scope_items_with_apps": sum(1 for r in by_scope.values() if r["applications"]),
        "distinct_applications": len({a for r in by_scope.values() for a in r["applications"]}),
        "diagrams_for_scenario": len(mine),
        "diagrams_matched": matched,
        "diagrams_unmatched": len(unmatched),
        "scope_items_with_steps": sum(1 for r in by_scope.values() if r["steps"]),
        "capability_rows_used": cap_hits,
    }, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    scenario, release = fetched_scenario()
    print(f"== scenario {scenario}  release {release}")
    rows, counts, unmatched = build(scenario)

    for k, v in counts.items():
        print(f"   {k:<24} {v}")

    if counts["capability_rows_used"] == 0 and CAPABILITIES.exists():
        print("   NOTE: capabilities.json holds no rows for this scenario. SAP had not")
        print("         published SolutionCapabilityHierarchy for 2608 as of 2026-09-22")
        print("         (0 rows, against 53,067 for 2602). Not an error.")
    if unmatched:
        print(f"   {len(unmatched)} diagram(s) matched no scope item, e.g.:")
        for n in unmatched[:3]:
            print(f"      {n}")

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    payload = {
        "_meta": {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scenario_id": scenario,
            "target_release": release,
            "counts": counts,
        },
        "scope_items": sorted(rows.values(), key=lambda r: r["scope_item"]),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n   wrote {OUT}  ({OUT.stat().st_size / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
