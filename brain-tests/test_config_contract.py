#!/usr/bin/env python3
"""CP1 must collect the tenant CONFIGURATION values, and Build must still be able to read them.

WHY THIS EXISTS
    Some values the build needs are manual tenant configuration: a Communication Arrangement,
    a Communication Scenario, a destination, a logical system, a business role, a number range.
    On Public Cloud a key user creates these by hand in the Communication Management apps —
    nothing in ABAP creates one, and nothing should resolve one at runtime.

    With nothing collecting them the agent has two bad options: invent a plausible value, or go
    looking for one. Measured on SMART-SEARCH-FD-R2: the build took the second and called
    if_com_scenario_factory — an object with no release verdict and no prior use anywhere in the
    corpus. Gate 2 caught it, but the finding was created by the missing input, not by the code.

    So configuration is declared at the proposal, filled in by the human at CP1, locked with the
    object names, and used verbatim by Build. Same rule as names: never invent one.

WHAT IT PINS
    * a blank REQUIRED value blocks CP1 approval, and the error names the item;
    * "-" / "n/a" is an explicit "does not apply" and counts as answered — forcing a fake value
      would just teach people to type one;
    * required:false never blocks;
    * values persist stripped and survive a reload;
    * THE REGRESSION THIS WAS WRITTEN FOR: approval nulls checkpoint_request, so unless the
      values are copied into the decision file they are collected, gated on, and then destroyed
      one step before the build that needs them;
    * pipeline_config actually runs — it called an undefined `write_json` and would have raised
      NameError on every call, which no test caught because no UI ever called it;
    * a run with no config_contract is unaffected, and adjust/reject always pass.

Usage:
    python brain-tests/test_config_contract.py
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "webapp"))

import app                                                    # noqa: E402

FAILS = []
CP1 = "CP1 · Solution Approval"


def check(label, got, want):
    ok = got == want
    print("  %-4s %-56s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


TMP = Path(tempfile.mkdtemp(prefix="s4pc-cfg-"))
app.ROOT_DIR = str(TMP)
_N = [0]


def C(cid, value="", required=True):
    return {"id": cid, "item": "Communication Arrangement", "type": "communication_arrangement",
            "where": "Communication Arrangements (F1763)", "why": "outbound call to the BTP app",
            "required": required, "value": value}


def mkrun(contract, naming=None):
    """Write a run.json awaiting CP1 and return its id."""
    _N[0] += 1
    rid = "T-%d" % _N[0]
    d = TMP / "output" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "folder": rid, "status": "awaiting_approval",
        "steps": [{"n": 5, "name": "Solution", "status": "AWAITING_APPROVAL"}],
        "checkpoint_request": {"checkpoint": CP1, "options": ["approve", "adjust", "reject"],
                               "config_contract": contract, "naming_contract": naming or []},
    }), encoding="utf-8")
    return rid


def cpreq(rid):
    return (json.loads((TMP / "output" / rid / "run.json").read_text(encoding="utf-8"))
            .get("checkpoint_request") or {})


def decide(rid, decision="approved", notes="ok"):
    res = app.pipeline_decision(rid, CP1, decision, notes)
    return res if isinstance(res, tuple) else (res, 200)


def decision_file(rid):
    dd = TMP / "output" / rid / "decisions"
    return json.loads(sorted(dd.glob("*.json"))[0].read_text(encoding="utf-8"))


def main():
    print("a blank required value blocks the lock")
    rid = mkrun([C("CFG-01")])
    check("gap detected", app._config_gaps(cpreq(rid)), ["CFG-01"])
    body, code = decide(rid)
    check("approval refused", code, 409)
    check("and it names the item", "CFG-01" in str(body.get("error", "")), True)
    check("and says these are set up in the tenant",
          "manually in the tenant" in str(body.get("error", "")), True)

    print("\npipeline_config persists the value (it used to raise NameError)")
    rid = mkrun([C("CFG-01")])
    body, code = app.pipeline_config(rid, {"CFG-01": "  YY1_COMM_ARR_BTP  "})
    check("accepted", code, 200)
    check("stored stripped", body["config_contract"][0]["value"], "YY1_COMM_ARR_BTP")
    check("survives a reload", cpreq(rid)["config_contract"][0]["value"], "YY1_COMM_ARR_BTP")
    check("no gaps left", app._config_gaps(cpreq(rid)), [])
    check("and now it locks", decide(rid)[1], 200)

    print("\nthe values reach Build — approval nulls checkpoint_request")
    rid = mkrun([C("CFG-01")])
    app.pipeline_config(rid, {"CFG-01": "YY1_COMM_ARR_BTP"})
    decide(rid)
    check("checkpoint_request is gone", cpreq(rid), {})
    rec = decision_file(rid)
    check("decision file carries config_contract", bool(rec.get("config_contract")), True)
    check("with the human's value intact",
          rec["config_contract"][0]["value"], "YY1_COMM_ARR_BTP")

    print("\n'does not apply' is an answer, not a gap")
    for sentinel in ("-", "n/a"):
        rid = mkrun([C("CFG-01")])
        app.pipeline_config(rid, {"CFG-01": sentinel})
        check("%-4r counts as answered" % sentinel, app._config_gaps(cpreq(rid)), [])
        check("%-4r locks" % sentinel, decide(rid)[1], 200)

    print("\nwhat must NOT block")
    check("required:false left blank", app._config_gaps({"config_contract": [C("CFG-02", required=False)]}), [])
    check("and it locks", decide(mkrun([C("CFG-02", required=False)]))[1], 200)
    check("no config_contract at all", app._config_gaps({}), [])
    check("and it locks", decide(mkrun([]))[1], 200)
    check("a filled contract", app._config_gaps({"config_contract": [C("CFG-01", "YY1_X")]}), [])

    print("\nthe developer always has a way forward")
    check("adjust passes with a blank value", decide(mkrun([C("CFG-01")]), "adjusted", "fix the scope")[1], 200)
    check("reject passes with a blank value", decide(mkrun([C("CFG-01")]), "rejected", "wrong approach")[1], 200)

    print("\npipeline_config guards its inputs")
    check("unknown run", app.pipeline_config("T-does-not-exist", {"CFG-01": "x"})[1], 404)
    check("bad run id", app.pipeline_config("../../etc", {"CFG-01": "x"})[1], 400)
    check("run with no contract", app.pipeline_config(mkrun([]), {"CFG-01": "x"})[1], 409)

    print("\nBuild is told to use them verbatim")
    prompt = app._phase_a_prompt("input/x.md", "T-1")
    check("the proposal asks for a config_contract", "config_contract" in prompt, True)
    check("and forbids inventing one", "never invent" in prompt.lower(), True)

    print()
    if FAILS:
        print("== %d FAILED" % len(FAILS))
        for f in FAILS:
            print("   " + f)
        return 1
    print("== all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
