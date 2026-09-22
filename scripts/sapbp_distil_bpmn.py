#!/usr/bin/env python3
"""
Distil the BPMN diagram dump into the part the brain can actually use.

WHY THIS EXISTS
    sapbp_catalog.py --l1-only pulls SolutionProcessFlowDiagram with
    diagramContentBpmn, and that lands at 477 MB across 14,931 diagrams --
    larger than the entire FAISS index the brain runs on. Almost none of it is
    content: a measured example carried 14,153 characters of XML whose useful
    labels totalled 274, i.e. 1.9%. The rest is geometry (Bounds, waypoints,
    BPMNShape), Signavio metadata and font declarations.

WHAT IS WORTH KEEPING
    Three things, and they map to different questions an agent asks:
      lanes  -> the business ROLES a process involves (Accounts Receivable
                Manager, Accounts Receivable Accountant)
      tasks  -> the ordered STEPS, many of which name a Fiori app directly
                ("Manage Customer Master Data")
      events -> the state transitions between them
    Names are taken only from elements that carry semantics. A bare
    name="..." sweep also collects "Arial" from every BPMNLabel, which is how
    a font ends up looking like a process step.

LAYERS
    Output is L1: a diagram's step list is an exact lookup, not a similarity
    question. The rendered prose form feeds L4 so "which process approves a
    purchase requisition" still works semantically -- but the raw XML is
    embedded nowhere. Embedding markup pollutes the vectors with tag names.

USAGE
    python3.11 scripts/sapbp_distil_bpmn.py            # all scenarios
    python3.11 scripts/sapbp_distil_bpmn.py --ours     # only our scenario
    python3.11 scripts/sapbp_distil_bpmn.py --drop-raw # delete the 477 MB after
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RAW = BASE_DIR / "brain" / "sapbp" / "raw"
SRC = RAW / "diagrams.json"
OUT = RAW / "diagram_steps.json"

# Second copy of a per-release GUID, and that is the problem it now guards
# against. sapbp_catalog resolves its scenario at runtime from stableId, because
# the GUID changes every release; this file pinned its own literal, so after the
# next release --ours would filter freshly fetched diagrams against the PREVIOUS
# scenario and keep none of them. Empty output, no error.
#
# manifest.json records the scenario the diagrams were actually fetched with, so
# that is the source of truth here -- not a literal, and not a second network
# lookup that could disagree with what is on disk. The literal survives only for
# a manifest written before the field existed.
FALLBACK_SCENARIO = "5c293206-d436-4b73-af8b-55a6e80a79a3"
MANIFEST = RAW.parent / "manifest.json"


def fetched_scenario():
    """The scenario id recorded by the fetch that produced diagrams.json."""
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return FALLBACK_SCENARIO
    return m.get("scenario_id") or FALLBACK_SCENARIO

# Only elements that mean something. Geometry, labels and Signavio metadata are
# excluded by omission rather than by filtering, so a new noise element cannot
# leak in the way "Arial" did with a bare name= sweep.
SEMANTIC = re.compile(
    r"<(?:\w+:)?(lane|participant|task|userTask|serviceTask|manualTask|"
    r"scriptTask|sendTask|receiveTask|businessRuleTask|callActivity|subProcess|"
    r"startEvent|endEvent|intermediateThrowEvent|intermediateCatchEvent|"
    r"exclusiveGateway|parallelGateway|inclusiveGateway|eventBasedGateway)"
    r"\b[^>]*?\sname=\"([^\"]{2,120})\"")

ROLE_KINDS = {"lane", "participant"}
STEP_KINDS = {"task", "userTask", "serviceTask", "manualTask", "scriptTask",
              "sendTask", "receiveTask", "businessRuleTask", "callActivity",
              "subProcess"}


def records(path):
    """Stream a pretty-printed JSON array without loading 477 MB."""
    buf = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.rstrip() == "  {":
                buf = [line]
            elif buf:
                buf.append(line)
                if line.rstrip() in ("  }", "  },"):
                    try:
                        yield json.loads("".join(buf).rstrip().rstrip(","))
                    except Exception:                    # noqa: BLE001
                        pass
                    buf = []


def distil(bpmn):
    roles, steps, events, seen = [], [], [], set()
    for kind, name in SEMANTIC.findall(bpmn or ""):
        name = name.strip()
        key = (kind, name)
        if not name or key in seen:
            continue
        seen.add(key)
        if kind in ROLE_KINDS:
            roles.append(name)
        elif kind in STEP_KINDS:
            steps.append(name)
        else:
            events.append(name)
    return roles, steps, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", action="store_true",
                    help="keep only the scenario manifest.json records for this fetch")
    ap.add_argument("--scenario", default=None,
                    help="Override; defaults to manifest.json's scenario_id")
    ap.add_argument("--drop-raw", action="store_true",
                    help="delete diagrams.json once distilled")
    a = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"{SRC} missing — run sapbp_catalog.py --l1-only first")

    scenario = a.scenario or fetched_scenario()
    if a.ours:
        print(f"== keeping only scenario {scenario}")

    src_mb = SRC.stat().st_size / 1048576
    out, tally = [], Counter()
    for rec in records(SRC):
        tally["read"] += 1
        if a.ours and rec.get("ss_ID") != scenario:
            tally["other_scenario"] += 1
            continue
        roles, steps, events = distil(rec.get("diagramContentBpmn"))
        if not (roles or steps):
            tally["empty"] += 1
            continue
        out.append({
            "diagram_id": rec.get("ID"),
            "name": rec.get("name"),
            "stable_id": rec.get("stableId"),
            "business_id": rec.get("businessId"),
            "scenario_id": rec.get("ss_ID"),
            "flow_id": rec.get("spf_ID"),
            "roles": roles,
            "steps": steps,
            "events": events,
        })
        tally["kept"] += 1
        tally["steps"] += len(steps)
        tally["roles"] += len(roles)

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    out_mb = OUT.stat().st_size / 1048576
    for k, v in sorted(tally.items()):
        print(f"   {v:>7}  {k}")
    print(f"\n   {src_mb:.1f} MB -> {out_mb:.1f} MB  ({100 * out_mb / src_mb:.1f}%)")
    print(f"   wrote {OUT}")

    if a.drop_raw:
        SRC.unlink()
        print(f"   removed {SRC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
