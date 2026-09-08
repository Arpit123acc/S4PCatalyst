"""L3 -> the run that produced a lesson. The provenance edge that actually exists.

WHY THIS EXISTS, AND WHY THE OBVIOUS VERSION DOES NOT WORK
    "Why did we learn this?" had no path out of a lesson. The first attempt linked a
    lesson to CORPUS documents sharing its SAP objects (object_usage.evidence_for_lesson),
    which was measured on 2026-09-08 and reached 1 lesson in 32:

        24 of 32 lessons name no SAP object at all -- they are method lessons, and
           there is nothing for an extractor to join on;
         7 of the 8 that do name objects cite objects the corpus never mentions
           (YY1_PriorityLevel_PDH, Z_SMART_SEARCH_BADI, I_PersonWorkAgreement).

    The second point is the structural one: L3's lessons come from S4PC PIPELINE RUNS,
    while L4 is the client's SharePoint archive. The two describe different bodies of
    work and overlap only by coincidence, so no amount of object matching will connect
    them. The corpus edge is kept -- it is correct when it fires (EXP-021 finds two
    genuine documents) -- but it is not the answer to provenance.

    The answer was already in the data. record_experience stores a `source`, and 12 of
    32 lessons name a run or an FD there:

        MM-RPT-0001 (3), pipeline run d4279543 (3),
        SMART-SEARCH-FD / -R2 / -R3 (6), and 20 seeds with no run.

    Nothing consumed it because the field is free text used three different ways: a run
    id, an FD name, and a prose sentence. This module reads all three.

MATCH TYPE IS REPORTED, NOT HIDDEN
    Resolution is ordered from exact to inferred, and every result says which rule
    fired. "SMART-SEARCH-FD-R2" has no directory of its own, so it resolves to the
    SMART-SEARCH-FD family -- a RELATED run, not the same one. Labelling that
    `revision_family` lets a reader discount it; silently returning it as the source
    run would be the kind of confident near-miss this project keeps finding.

Pure stdlib. Never raises: provenance is additive, and a missing output/ directory is
the normal state on a host that has not run the pipeline.
"""

import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
OUTPUT_DIR = os.path.join(REPO_DIR, "output")

# Trailing pipeline-revision suffix: SMART-SEARCH-FD-R2. Anchored, and requires
# digits, so a genuine name ending in "-R" or "-REPORT" is untouched.
_REVISION_RE = re.compile(r"-R\d+$", re.IGNORECASE)

# Prose that surrounds an id in a free-text source ("pipeline run d4279543").
_PROSE = {"pipeline", "run", "from", "the", "for", "delivery", "experience", "seed",
          "during", "of", "in", "and", "a"}

_CACHE = {"sig": None, "runs": None}


def _signature():
    """(name, mtime) per run.json -- exact, and cheap for a few dozen runs.

    Keying the cache on the output DIRECTORY's mtime alone would miss a run.json
    rewritten in place, which is exactly what a re-run does.
    """
    try:
        names = sorted(os.listdir(OUTPUT_DIR))
    except OSError:
        return ()
    sig = []
    for name in names:
        path = os.path.join(OUTPUT_DIR, name, "run.json")
        try:
            sig.append((name, os.path.getmtime(path)))
        except OSError:
            continue
    return tuple(sig)


def _index():
    """{directory name: run summary}. Cached against the signature above."""
    sig = _signature()
    if _CACHE["sig"] == sig and _CACHE["runs"] is not None:
        return _CACHE["runs"]
    runs = {}
    for name, _mtime in sig:
        path = os.path.join(OUTPUT_DIR, name, "run.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue                       # a half-written run must not break a query
        try:
            files = sorted(f for f in os.listdir(os.path.join(OUTPUT_DIR, name))
                           if f != "run.json")
        except OSError:
            files = []
        runs[name] = {
            "directory": name,
            "run_id": data.get("id"),
            "title": data.get("title"),
            "fd_source": data.get("fd_source"),
            "status": data.get("status"),
            "extensibility_mode": data.get("extensibility_mode"),
            "quality_score": data.get("quality_score"),
            "gates_passed": data.get("gates_passed"),
            "previous_run": data.get("previous_run"),
            "deliverables": files,
        }
    _CACHE.update(sig=sig, runs=runs)
    return runs


def _tokens(source):
    """Candidate identifiers inside a free-text source, prose words removed."""
    parts = re.split(r"[\s,;|]+", str(source or "").strip())
    return [p for p in parts if p and p.lower() not in _PROSE]


def resolve(source):
    """A lesson's `source` -> one run summary with a `match` label, or None.

    Rules in order, most exact first:
      exact_directory   the source IS a run folder name
      run_id            it matches run.json's id, or a unique prefix of it
      title_or_fd       it matches the run's title or fd_source
      revision_family   its revision suffix stripped, it matches a folder -- a
                        RELATED run, not the same one
    """
    runs = _index()
    if not runs or not str(source or "").strip():
        return None
    raw = str(source).strip()

    def _hit(name, match):
        out = dict(runs[name])
        out["match"] = match
        return out

    if raw in runs:
        return _hit(raw, "exact_directory")

    cands = [raw] + _tokens(raw)
    low = {k.lower(): k for k in runs}
    for c in cands:
        if c.lower() in low:
            return _hit(low[c.lower()], "exact_directory")
    # run.json id, exact then unique-prefix. A prefix that matches two runs is
    # ambiguous and therefore no answer at all.
    for c in cands:
        cl = c.lower()
        exact = [n for n, r in runs.items()
                 if str(r.get("run_id") or "").lower() == cl]
        if exact:
            return _hit(exact[0], "run_id")
        pre = [n for n, r in runs.items()
               if len(cl) >= 6 and str(r.get("run_id") or "").lower().startswith(cl)]
        if len(pre) == 1:
            return _hit(pre[0], "run_id")
    for c in cands:
        cl = c.lower()
        for n, r in runs.items():
            if cl in (str(r.get("title") or "").lower(),
                      str(r.get("fd_source") or "").lower()):
                return _hit(n, "title_or_fd")
    # Last, and deliberately last: a revision whose own folder is absent.
    for c in cands:
        base = _REVISION_RE.sub("", c)
        if base != c and base.lower() in low:
            return _hit(low[base.lower()], "revision_family")
    return None


def evidence(source):
    """resolve(), wrapped for a tool payload. None when nothing matched."""
    run = resolve(source)
    if not run:
        return None
    note = ("The pipeline run this lesson was recorded against. Its deliverables are "
            "the lesson's context: read 02-solution-proposal.md for the approach that "
            "was approved and 09-review.md for what the review caught.")
    if run["match"] == "revision_family":
        note = ("NOT the same run — this lesson names a revision (%s) whose own output "
                "folder is absent, so it resolved to the same FD's run family. Treat "
                "the deliverables as related context, not as the lesson's source."
                % source)
    return {"run_id": run.get("run_id"), "directory": run["directory"],
            "title": run.get("title"), "status": run.get("status"),
            "extensibility_mode": run.get("extensibility_mode"),
            "gates_passed": run.get("gates_passed"),
            "quality_score": run.get("quality_score"),
            "deliverables": run.get("deliverables"),
            "path": os.path.join("output", run["directory"]),
            "matched_by": run["match"], "note": note}


def coverage(entries):
    """How many lessons can reach a run. For layer_health and for honesty.

    A provenance edge that silently covers a third of the store looks identical to one
    that covers all of it, right up to the moment someone relies on it.
    """
    entries = entries or []
    resolved = sum(1 for e in entries if resolve(e.get("source")))
    return {"lessons": len(entries), "with_run_evidence": resolved,
            "runs_on_disk": len(_index())}
