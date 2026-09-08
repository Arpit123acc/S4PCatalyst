#!/usr/bin/env python3
"""L3 -> run provenance: resolving a lesson's free-text `source` to the run that taught it.

WHY THIS EXISTS
    The first attempt at lesson provenance linked a lesson to CORPUS documents sharing
    its SAP objects, and measured 1 lesson in 32 on the real store: 24 of 32 name no
    object, and 7 of the remaining 8 cite objects the client corpus never mentions
    because L3's lessons come from PIPELINE RUNS while L4 is the client's archive.

    The provenance that does exist was already in `source`, unread, in three different
    conventions — a run id, an FD name, and prose. This pins all three, and pins the
    thing that makes such a resolver dangerous: reporting an INFERRED match as an exact
    one. "SMART-SEARCH-FD-R2" resolving to the SMART-SEARCH-FD folder is a related run,
    not the source, and it must say so.

Builds a synthetic output/ tree in a temp directory; never reads the real one.

Usage:
    python brain-tests/test_run_evidence.py
"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "mcp-server"))

import run_evidence as re_mod                                 # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="s4pc-runs-"))
re_mod.OUTPUT_DIR = str(TMP)

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-50s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def _run(name, run_id, title, fd_source, files=("02-solution-proposal.md",)):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": run_id, "title": title, "fd_source": fd_source,
        "status": "packaged", "extensibility_mode": "developer",
        "gates_passed": 3, "quality_score": 92, "previous_run": None,
    }), encoding="utf-8")
    for f in files:
        (d / f).write_text("x", encoding="utf-8")


def main():
    _run("SMART-SEARCH-FD", "d4279543ab", "Smart Search", "smart-search-fd.md",
         files=("02-solution-proposal.md", "09-review.md"))
    _run("MM-RPT-0001", "99aa11bb22", "MM Report", "mm-rpt-0001.md")
    re_mod._CACHE.update(sig=None, runs=None)

    print("exact and case-insensitive directory match")
    e = re_mod.evidence("SMART-SEARCH-FD")
    check("resolves", e and e["directory"], "SMART-SEARCH-FD")
    check("as an exact match", e["matched_by"], "exact_directory")
    check("and carries the run id", e["run_id"], "d4279543ab")
    check("with the deliverables to read",
          e["deliverables"], ["02-solution-proposal.md", "09-review.md"])
    check("path is repo-relative", e["path"].replace("\\", "/"),
          "output/SMART-SEARCH-FD")
    check("case-insensitive",
          re_mod.evidence("smart-search-fd")["matched_by"], "exact_directory")

    print("\nprose source ('pipeline run <id>')")
    e = re_mod.evidence("pipeline run d4279543ab")
    check("prose words are stripped and the id matches", e and e["matched_by"], "run_id")
    check("to the right run", e["directory"], "SMART-SEARCH-FD")
    e = re_mod.evidence("pipeline run d4279543")
    check("a unique id PREFIX resolves", e and e["matched_by"], "run_id")

    print("\ntitle / fd_source")
    check("matches the run title",
          re_mod.evidence("MM Report")["matched_by"], "title_or_fd")
    check("matches the fd_source filename",
          re_mod.evidence("mm-rpt-0001.md")["matched_by"], "title_or_fd")

    print("\nan INFERRED match must say it is inferred")
    # The dangerous case: -R2's own folder is absent. Returning the family silently
    # would report a DIFFERENT run as the lesson's source.
    e = re_mod.evidence("SMART-SEARCH-FD-R2")
    check("revision resolves to the family", e and e["directory"], "SMART-SEARCH-FD")
    check("labelled as such", e["matched_by"], "revision_family")
    check("and the note says it is NOT the same run",
          "NOT the same run" in e["note"], True)
    # But when the revision HAS its own folder, that wins outright.
    _run("SMART-SEARCH-FD-R2", "ff00ff00ff", "Smart Search R2", "smart-search-fd-r2.md")
    re_mod._CACHE.update(sig=None, runs=None)
    e = re_mod.evidence("SMART-SEARCH-FD-R2")
    check("its own folder beats the family", e["matched_by"], "exact_directory")
    check("and points at itself", e["run_id"], "ff00ff00ff")

    print("\nno match is None, never a guess")
    check("unknown source", re_mod.evidence("NOT-A-RUN-AT-ALL"), None)
    check("the seed sentence resolves to nothing",
          re_mod.evidence("delivery experience — seed"), None)
    check("empty", re_mod.evidence(""), None)
    check("None", re_mod.evidence(None), None)
    # An ambiguous id prefix is no answer. Two runs sharing a prefix must not
    # arbitrarily pick one.
    _run("AMBIG-A", "abcdef1111", "A", "a.md")
    _run("AMBIG-B", "abcdef2222", "B", "b.md")
    re_mod._CACHE.update(sig=None, runs=None)
    check("an ambiguous id prefix resolves to nothing",
          re_mod.evidence("abcdef"), None)
    # Short fragments must not prefix-match at all.
    check("a 3-char fragment is not a prefix match", re_mod.evidence("abc"), None)

    print("\ncoverage reporting")
    cov = re_mod.coverage([{"source": "SMART-SEARCH-FD"}, {"source": "MM-RPT-0001"},
                           {"source": "delivery experience — seed"}, {"source": None}])
    check("counts what can reach a run", cov["with_run_evidence"], 2)
    check("out of the whole store", cov["lessons"], 4)
    check("and says how many runs exist", cov["runs_on_disk"], 5)

    print("\ndegradation")
    re_mod.OUTPUT_DIR = str(TMP / "does-not-exist")
    re_mod._CACHE.update(sig=None, runs=None)
    check("no output/ dir -> None", re_mod.evidence("SMART-SEARCH-FD"), None)
    check("and coverage reports zero runs",
          re_mod.coverage([{"source": "x"}])["runs_on_disk"], 0)
    # A half-written run.json must not take down a query for every other run.
    re_mod.OUTPUT_DIR = str(TMP)
    broken = TMP / "BROKEN-RUN"
    broken.mkdir(exist_ok=True)
    (broken / "run.json").write_text("{not json", encoding="utf-8")
    re_mod._CACHE.update(sig=None, runs=None)
    check("a corrupt run.json is skipped, not fatal",
          re_mod.evidence("SMART-SEARCH-FD")["directory"], "SMART-SEARCH-FD")
    check("and the corrupt one simply does not resolve",
          re_mod.evidence("BROKEN-RUN"), None)

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
