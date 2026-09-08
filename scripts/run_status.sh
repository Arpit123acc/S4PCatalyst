#!/usr/bin/env bash
# Where is a pipeline run, and what is it waiting for?
#
#   bash scripts/run_status.sh              # all runs, newest first
#   bash scripts/run_status.sh <RUN-ID>     # full detail for one run
#   bash scripts/run_status.sh --watch      # refresh the list every 20s
#
# The run id is the folder name under output/, derived from the FD filename:
# input/Smart Search FD.md -> SMART-SEARCH-FD, then -R2, -R3 for later runs.
#
# WHY THE DETAIL VIEW SHOWS WHAT IT SHOWS
#   A checkpoint is only readable if you can see three things at once: the finding
#   severities, whether each carries a DECISION, and whether the review panel was
#   actually published. On 2026-09-08 a run reported "19 findings (17 resolved)" while
#   2 Critical and 7 Major sat at Pending Fix with action=None and the panel was empty --
#   every one of those facts was available and none of them was on screen together.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Resolve the interpreter rather than hardcoding one. The delivery host has python3.11
# and a laptop checkout usually has plain `python`; a hardcoded name turns a read-only
# status script into "command not found" on half the machines that want to run it.
#
# Each candidate is EXECUTED, not just located. Windows ships an App Execution Alias for
# `python3` that sits on PATH and only prints a Microsoft Store advert -- command -v finds
# it and it is not an interpreter, so presence is not evidence it works.
PY=""
for c in python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "" >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "FATAL: no python on PATH (tried python3.11, python3, python)"; exit 1; }

RUN_ID=""
WATCH=0
for arg in "$@"; do
  case "$arg" in
    --watch) WATCH=1 ;;
    -*)      echo "unknown flag: $arg" >&2; exit 2 ;;
    *)       RUN_ID="$arg" ;;
  esac
done

_list() {
$PY - <<'PY'
import glob, json, os
rows = sorted(glob.glob("output/*/run.json"), key=os.path.getmtime, reverse=True)
if not rows:
    print("  no runs under output/")
print("  %-30s %-18s %-6s %-7s %s" % ("RUN", "STATUS", "GATES", "QUALITY", "CURRENT STEP"))
for p in rows:
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as exc:
        print("  %-30s unreadable: %s" % (os.path.basename(os.path.dirname(p)), exc))
        continue
    cur = [s.get("name") for s in (d.get("steps") or [])
           if s.get("status") in ("RUNNING", "AWAITING_APPROVAL")]
    q = d.get("quality_score")
    print("  %-30s %-18s %-6s %-7s %s" % (
        d.get("id") or "?", d.get("status") or "?", d.get("gates_passed") or "-",
        ("%s/100" % q) if q is not None else "-", cur[0] if cur else "-"))
PY
}

_detail() {
RUN_ID="$1" $PY - <<'PY'
import collections, json, os, sys
rid = os.environ["RUN_ID"]
path = os.path.join("output", rid, "run.json")
if not os.path.isfile(path):
    sys.exit("no run at %s" % path)
d = json.load(open(path, encoding="utf-8"))

print("%s   status=%s   gates=%s   quality=%s" % (
    d.get("id"), d.get("status"), d.get("gates_passed"),
    d.get("quality_score") if d.get("quality_score") is not None else "-"))

print("\nsteps")
for s in (d.get("steps") or []):
    mark = ">>" if s.get("status") in ("RUNNING", "AWAITING_APPROVAL") else "  "
    print("  %s %-4s %-42s %s" % (mark, s.get("n") or s.get("id"), s.get("name"), s.get("status")))

f = d.get("findings") or []

# TWO SCHEMAS EXIST. Newer runs carry severity Critical|Major|Minor|Info with a separate
# status. Older ones fold both into severity -- critical_open, major_resolved,
# fix_applied, minor_open -- with no status at all. The UI normalises these
# (webapp/ui/index.html, normalizeFinding); a status tool that did not would report an
# already-fixed finding as outstanding, which is the same confusion this file exists to
# clear up. Mirrors the UI's map deliberately.
_SEV = {"blocker_for_build": "Critical", "critical_open": "Critical",
        "critical_resolved": "Critical", "major_open": "Major",
        "major_resolved": "Major", "open_question": "Major",
        "minor_open": "Minor", "minor_resolved": "Minor",
        "gate2_fix_applied": "Minor", "fix_applied": "Minor"}


def _norm(x):
    raw = (x.get("severity") or "").strip()
    sev = _SEV.get(raw.lower(), raw.title() if raw else "")
    st = (x.get("status") or "").strip()
    if not st:                       # old schema: the outcome is inside the severity
        st = "Resolved" if ("resolved" in raw.lower() or "fix_applied" in raw.lower()) else "Open"
    return sev, st


# Outstanding is what decides shippability; "how many were fixed" is not a safety signal.
outstanding = [x for x in f if _norm(x)[1].lower() not in ("resolved", "accepted", "closed")]
blocking = [x for x in outstanding if _norm(x)[0].lower() in ("critical", "major")]
undecided = [x for x in blocking
             if (x.get("action") or "").strip().lower() not in ("fix", "accept")]
print("\nfindings: %d total, %d outstanding, %d blocking (Critical/Major), %d undecided"
      % (len(f), len(outstanding), len(blocking), len(undecided)))
for k, n in sorted(collections.Counter(
        (_norm(x)[0], _norm(x)[1],
         (x.get("action") or "")[:12] if (x.get("action") or "").strip().lower()
         in ("fix", "accept") else "-") for x in f).items(),
        key=lambda kv: str(kv[0])):
    print("   %-9s %-12s action=%-7s %d" % (k[0] or "?", k[1], k[2], n))
if undecided:
    print("   -> CP3 approval is BLOCKED until each of these has fix or accept:")
    print("      " + ", ".join(str(x.get("id")) for x in undecided))

cp = d.get("checkpoint_request") or {}
fr = cp.get("findings_review") or []
print("\ncheckpoint: %s" % (cp.get("checkpoint") or "(none - not waiting on a human)"))
if cp:
    print("panel     : %d entry(s)%s" % (
        len(fr), "   [DERIVED by the webapp - the reviewer published none]"
        if cp.get("findings_review_derived") else ""))
    for e in fr[:10]:
        print("   %-6s %-9s action=%-7s %s" % (
            e.get("id"), e.get("severity"), e.get("action"),
            (e.get("what_is_wrong") or "")[:60]))

ha = d.get("human_approvals") or []
if ha:
    print("\napprovals")
    for a in ha:
        note = (a.get("notes") or "").replace("\n", " ")[:50]
        print("   %-42s %-9s %s  %s" % (a.get("checkpoint"), a.get("decision"),
                                        a.get("date") or "", note or "(no notes)"))
PY
}

if [ -n "$RUN_ID" ]; then
  _detail "$RUN_ID"
elif [ "$WATCH" = "1" ]; then
  while true; do
    clear; date -u +"%H:%M:%SZ"; _list; sleep 20
  done
else
  _list
fi
