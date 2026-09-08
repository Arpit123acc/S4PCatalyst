#!/usr/bin/env python3
"""CP3 must refuse approval while a Critical/Major finding has no decision.

WHY THIS EXISTS
    The enforcement used to run only `if _cpreq["findings_review"]` — an array the MODEL
    writes when it composes the checkpoint. Omit it and the condition is falsy and the
    whole gate silently does not run.

    Measured on SMART-SEARCH-FD-R2, 2026-09-08: CP3 fired correctly, a human approved it
    with empty notes, and afterwards 2 Critical and 7 Major findings were still
    `Pending Fix` with `action=None`. Nobody had decided fix-or-accept on any of them and
    the run was marked completed — a clean-core delivery record with two unaccepted
    Critical findings in it.

    The same shape is recorded one block below in app.py: a Gate 2 review filename
    mismatch "silently disabled this gate entirely (a run was approved with 6 open Majors
    and no fix comments)". A guard whose input can go missing is a guard that fails open.

WHAT IT PINS
    * an undecided Critical/Major blocks approval even when findings_review is ABSENT —
      the regression that shipped;
    * accepting one without a written justification blocks;
    * a decision recorded on the FINDING (an earlier round) counts, not only one on the
      checkpoint panel;
    * Resolved / Minor / Info never block;
    * adjust and reject always pass — the developer must always have a way forward.

Usage:
    python brain-tests/test_cp3_gate.py
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "webapp"))

import app                                                    # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-56s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


TMP = Path(tempfile.mkdtemp(prefix="s4pc-cp3-"))
app.ROOT_DIR = str(TMP)


def _run(findings, review=None, checkpoint="CP3 · Findings Review (quality score too low)"):
    """Write a run.json and return the (payload, status) of approving it."""
    rid = "T-%d" % len(list((TMP / "output").glob("*"))) if (TMP / "output").exists() else "T-0"
    d = TMP / "output" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "status": "awaiting_approval", "findings": findings,
        "steps": [{"n": 11, "name": "Gate 3", "status": "AWAITING_APPROVAL"}],
        "checkpoint_request": {"checkpoint": checkpoint, "options": ["approve", "adjust", "reject"],
                               "findings_review": review or []},
    }), encoding="utf-8")
    res = app.pipeline_decision(rid, checkpoint, "approved", "")
    return res if isinstance(res, tuple) else (res, 200)


def F(fid, sev, status, action=None, notes=""):
    f = {"id": fid, "severity": sev, "status": status}
    if action:
        f["action"] = action
    if notes:
        f["notes"] = notes
    return f


def main():
    print("the regression: findings_review ABSENT must not disable the gate")
    # Verbatim shape of the run that got through: Criticals and Majors Pending Fix,
    # action=None, and an empty findings_review on the checkpoint.
    body, code = _run([F("F-02", "Critical", "Pending Fix"),
                       F("F-19", "Critical", "Pending Fix"),
                       F("F-01", "Major", "Pending Fix")], review=[])
    check("approval refused", code, 409)
    check("and it names the undecided findings",
          all(x in str(body.get("error", "")) for x in ("F-02", "F-19", "F-01")), True)
    check("and says how many", "3 Critical/Major" in str(body.get("error", "")), True)

    print("\na decision on the checkpoint panel satisfies it")
    body, code = _run([F("F-02", "Critical", "Pending Fix"), F("F-01", "Major", "Pending Fix")],
                      review=[{"id": "F-02", "severity": "Critical", "action": "fix"},
                              {"id": "F-01", "severity": "Major", "action": "fix"}])
    check("approved", code, 200)

    print("\na decision already recorded on the FINDING also counts")
    # An earlier round may have actioned it; requiring the panel to restate it would
    # block a run for a decision that was already taken.
    body, code = _run([F("F-02", "Critical", "Pending Fix", action="fix")], review=[])
    check("approved", code, 200)

    print("\naccepting a Critical/Major needs a written justification")
    body, code = _run([F("F-02", "Critical", "Pending Fix")],
                      review=[{"id": "F-02", "severity": "Critical", "action": "accept"}])
    check("refused without notes", code, 409)
    check("and says why", "justification" in str(body.get("error", "")).lower(), True)
    body, code = _run([F("F-02", "Critical", "Pending Fix")],
                      review=[{"id": "F-02", "severity": "Critical", "action": "accept",
                               "notes": "Accepted: mitigated by the tenant's own auth policy."}])
    check("accepted with a justification", code, 200)

    print("\nwhat must NOT block")
    check("Resolved Critical", _run([F("F-09", "Critical", "Resolved")])[1], 200)
    check("Minor Pending Fix", _run([F("F-05", "Minor", "Pending Fix")])[1], 200)
    check("Info Open", _run([F("F-06", "Info", "Open")])[1], 200)
    check("no findings at all", _run([])[1], 200)

    print("\nother checkpoints are unaffected")
    check("CP1 with open Criticals still approves",
          _run([F("F-02", "Critical", "Pending Fix")],
               checkpoint="CP1 · Solution approval")[1], 200)

    print("\nthe developer always has a way forward")
    # Only 'approved' is gated — adjust/reject must never be blocked, or a run with
    # undecided findings could not be sent back either.
    rid = "T-adjust"
    d = TMP / "output" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "status": "awaiting_approval",
        "findings": [F("F-02", "Critical", "Pending Fix")],
        "steps": [{"n": 11, "name": "Gate 3", "status": "AWAITING_APPROVAL"}],
        "checkpoint_request": {"checkpoint": "CP3 · Findings Review", "findings_review": []},
    }), encoding="utf-8")
    for dec in ("adjusted", "rejected"):
        res = app.pipeline_decision(rid, "CP3 · Findings Review", dec, "sending back")
        code = res[1] if isinstance(res, tuple) else 200
        check("%s passes" % dec, code, 200)

    print("\nthe panel is derived when the checkpoint published none")
    got = app._derive_findings_review({"findings": [
        F("F-02", "Critical", "Pending Fix"), F("F-09", "Critical", "Resolved"),
        F("F-01", "Major", "Pending Fix"), F("F-05", "Minor", "Pending Fix"),
        F("F-06", "Info", "Open")]})
    check("only open Critical/Major", [e["id"] for e in got], ["F-02", "F-01"])
    check("empty run yields nothing", app._derive_findings_review({}), [])

    print("\nEND TO END: derive -> accept -> status -> score -> gate opens")
    # The loop that was broken. An empty findings_review meant no panel, so no decision,
    # so pipeline_findings_review 409'd, so nothing synced, so the gate blocked forever.
    rid = "T-e2e"
    d = TMP / "output" / rid
    d.mkdir(parents=True, exist_ok=True)
    ckpt = "CP3 · Findings Review (quality score too low)"
    (d / "run.json").write_text(json.dumps({
        "id": rid, "status": "awaiting_approval", "quality_score": 20,
        "findings": [F("F-02", "Critical", "Pending Fix"), F("F-01", "Major", "Pending Fix")],
        "steps": [{"n": 11, "name": "Gate 3", "status": "AWAITING_APPROVAL"}],
        "checkpoint_request": {"checkpoint": ckpt, "findings_review": []},   # the empty array
    }), encoding="utf-8")

    # 1. blocked while undecided
    res = app.pipeline_decision(rid, ckpt, "approved", "")
    check("blocked before any decision", res[1] if isinstance(res, tuple) else 200, 409)

    # 2. the developer records decisions against the DERIVED panel
    res = app.pipeline_findings_review(rid, [
        {"id": "F-02", "action": "accept", "notes": "Risk carried: write path is behind the "
                                                    "tenant's own authorisation check."},
        {"id": "F-01", "action": "accept", "notes": "Accepted for this release; tracked as TD-114."}])
    check("decisions persisted", (res[1] if isinstance(res, tuple) else 200), 200)

    _rj = json.loads((d / "run.json").read_text(encoding="utf-8"))
    _by = {f["id"]: f for f in _rj["findings"]}
    check("accepted finding is Accepted", _by["F-02"]["status"], "Accepted")
    check("score recalculated upward", _rj.get("quality_score") > 20, True)

    # 3. and now approval passes
    res = app.pipeline_decision(rid, ckpt, "approved", "")
    check("gate opens", res[1] if isinstance(res, tuple) else 200, 200)

    print("\n'fix' does NOT close it — work is still outstanding")
    rid = "T-fix"
    d = TMP / "output" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "status": "awaiting_approval",
        "findings": [F("F-02", "Critical", "Pending Fix")],
        "steps": [{"n": 11, "name": "Gate 3", "status": "AWAITING_APPROVAL"}],
        "checkpoint_request": {"checkpoint": ckpt, "findings_review": []},
    }), encoding="utf-8")
    app.pipeline_findings_review(rid, [{"id": "F-02", "action": "fix"}])
    _rj = json.loads((d / "run.json").read_text(encoding="utf-8"))
    check("stays Pending Fix", _rj["findings"][0]["status"], "Pending Fix")
    # The decision must survive on the FINDING. checkpoint_request is cleared on approval,
    # so a decision recorded only there vanishes exactly when it becomes binding.
    check("the decision is persisted on the finding", _rj["findings"][0].get("action"), "fix")
    # A decision was recorded, so the governance gate is satisfied — but the finding is
    # still open, so the SCORE must not close it. Those are different questions.
    _res = app.pipeline_decision(rid, ckpt, "approved", "")
    check("gate accepts the decision", _res[1] if isinstance(_res, tuple) else 200, 200)
    _rj = json.loads((d / "run.json").read_text(encoding="utf-8"))
    check("and it is STILL not closed after approval",
          _rj["findings"][0]["status"], "Pending Fix")

    print("\nCP3 routing: fix takes a correction lap, accept goes straight to Package")
    app.ROOT_DIR = str(TMP)

    def _mk(rid, findings):
        p = TMP / "output" / rid
        p.mkdir(parents=True, exist_ok=True)
        (p / "run.json").write_text(json.dumps({"id": rid, "findings": findings}),
                                    encoding="utf-8")
        return rid

    # accept closes the finding, so no lap — the run packages.
    r = _mk("T-acc", [dict(F("F-02", "Critical", "Accepted"), action="accept")])
    check("accepted finding is not pending", app._cp3_pending_fixes(r), [])
    out = app._phase_d_prompt(r, "input/x.md", "approved", "", "cp3")
    check("routes to Package", "CORRECTION LAP" not in out, True)

    # fix leaves work outstanding, so the lap runs.
    r = _mk("T-fx", [dict(F("F-02", "Critical", "Pending Fix"), action="fix"),
                     dict(F("F-09", "Critical", "Resolved"), action="fix"),
                     dict(F("F-01", "Major", "Accepted"), action="accept")])
    pend = app._cp3_pending_fixes(r)
    check("only the unfinished fix is pending", [f["id"] for f in pend], ["F-02"])
    out = app._phase_d_prompt(r, "input/x.md", "approved", "", "cp3")
    check("routes to the correction lap", "CORRECTION LAP" in out, True)
    check("names the finding", "F-02" in out, True)
    check("re-enters at 7B", "STEP 7B" in out, True)
    check("does not package", "Do not proceed to step 12" in out, True)

    # The requirement: every downstream deliverable must be rewritten, not left stale.
    for _needle, _label in (("lint report", "lint"), ("unit-test design", "unit tests"),
                            ("technical design", "TD"), ("Gate 3 peer review", "gate 3")):
        check("rewrites the %s" % _label, _needle in out, True)
    check("forbids appending to a stale file", "call it updated" in out, True)
    check("republishes findings_review", "findings_review" in out, True)

    # adjust/reject must not trigger a lap — they already send the run back.
    check("adjust does not lap",
          "CORRECTION LAP" not in app._phase_d_prompt(r, "input/x.md", "adjusted", "", "cp3"),
          True)

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
