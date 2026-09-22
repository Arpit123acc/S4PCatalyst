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

    capabilities_by_scope.json
                        The four-level capability taxonomy per scope item,
                        produced by sapbp_fetch_capabilities.py from
                        BcmOccurrenceWithSolutionProcessFlat. MAY COME FROM AN
                        EARLIER RELEASE than the rest of the index: SAP had
                        published no capability rows for 2608, so the fetcher
                        walks back and records which release it used. The join
                        here is on the scope item, never the process GUID, which
                        is what makes that possible.

                        NOT capabilities.json, which is the raw
                        SolutionCapabilityHierarchy dump and the wrong entity --
                        one row per process of bcmType "LOB", 13 distinct names,
                        restating businessProcessGroupName and nothing more.

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
CAPABILITIES = RAW / "capabilities_by_scope.json"
SCOPE_CATALOG = BASE_DIR / "mcp-server" / "catalog" / "scope_items.json"

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


def scope_catalog():
    """{scope_item_id: description} across active AND retired items, 822 of them.

    Wider than the 657 processes on purpose. The process list is per COUNTRY, so
    a scope item with no German process is absent from it while its diagrams,
    its accelerators and its catalog entry all still exist. Keying the diagram
    join on the process list therefore threw away real content: 74 diagrams
    across 18 scope items and 406 steps, 1WQ "Bill of Exchange" alone holding
    203 of them.
    """
    try:
        d = json.loads(SCOPE_CATALOG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for key in ("scope_items", "retired_scope_items"):
        for i in d.get(key) or []:
            sid = (i.get("scope_item_id") or "").strip().upper()
            if sid:
                out[sid] = i.get("description")
    return out


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
            "has_country_process": True,
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

    # diagram steps, joined on the scope-item prefix of the diagram name.
    # Falls back to the 822-row scope catalog when the code names a scope item
    # this COUNTRY has no process for -- see scope_catalog() for why that is not
    # the same as the scope item not existing.
    catalog = scope_catalog()
    diags = _load(DIAGRAMS)
    mine = [d for d in diags if not scenario or d.get("scenario_id") == scenario]
    matched, from_catalog, unmatched = 0, 0, []
    for d in mine:
        m = SCOPE_PREFIX.match(d.get("name") or "")
        ext = m.group(1) if m else None
        if not ext:
            unmatched.append(d.get("name"))
            continue
        if ext not in by_scope:
            if ext not in catalog:
                unmatched.append(d.get("name"))
                continue
            # Known scope item, no process in this country. Emit a partial row
            # rather than discarding its steps; has_country_process says which.
            by_scope[ext] = {
                "scope_item": ext,
                "name": catalog.get(ext),
                "lob": None,
                "change_category": None,
                "license_required": None,
                "target_release": None,
                "has_country_process": False,
                "process_ids": [],
                "applications": [],
                "steps": [],
                "roles": [],
                "diagrams": [],
            }
            from_catalog += 1
        row = by_scope[ext]
        row["steps"].extend(d.get("steps") or [])
        row["roles"].extend(d.get("roles") or [])
        row["diagrams"].append(d.get("name"))
        matched += 1

    # capabilities, already joined to scope items by sapbp_fetch_capabilities.py.
    # Keyed on the scope item and NOT the process GUID, because the capability
    # data may come from an earlier release than the rest of the index -- GUIDs
    # are per release, scope item codes are not. Every row carries the release it
    # came from so a consumer can see when they differ.
    capdoc = _load(CAPABILITIES, {}) or {}
    cap_src = (capdoc.get("_meta") or {}).get("source_release")
    cap_hits = 0
    for ext, entries in (capdoc.get("by_scope_item") or {}).items():
        row = by_scope.get(ext)
        if not row:
            continue
        row["capabilities"] = entries
        row["capability_source_release"] = cap_src
        cap_hits += len(entries)

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
        "scope_items_from_catalog_only": from_catalog,
        "diagrams_unmatched": len(unmatched),
        "scope_items_with_steps": sum(1 for r in by_scope.values() if r["steps"]),
        "capability_rows_used": cap_hits,
        "capability_source_release": cap_src,
        "scope_items_with_capabilities": sum(
            1 for r in by_scope.values() if r.get("capabilities")),
    }, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    scenario, release = fetched_scenario()
    rows, counts, unmatched = build(scenario)

    # The manifest only started recording target_release on 2026-09-22, so a
    # manifest written by an earlier fetch has none. The processes themselves
    # carry it, and reporting null at the top of a result whose every row says
    # 2608 reads like a defect. Derive it rather than show the contradiction.
    if not release:
        rel = Counter(r["target_release"] for r in rows.values() if r.get("target_release"))
        release = rel.most_common(1)[0][0] if rel else None
    print(f"== scenario {scenario}  release {release}")

    for k, v in counts.items():
        print(f"   {k:<24} {v}")

    if not CAPABILITIES.exists():
        print("   NOTE: no capabilities_by_scope.json — run "
              "sapbp_fetch_capabilities.py (needs SAPME_COOKIE).")
    elif counts.get("capability_source_release") and \
            counts["capability_source_release"] != release:
        print("   NOTE: capability data is from release %s, the rest of this index "
              "from %s." % (counts["capability_source_release"], release))
        print("         SAP had published no capability rows for %s; the fetcher "
              "walks back" % release)
        print("         until it finds a release that has them. Every row carries "
              "capability_source_release.")
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
