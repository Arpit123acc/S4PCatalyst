#!/usr/bin/env python3
"""
Retrieval regression harness for the Public Cloud Brain.

WHY THIS EXISTS
    The brain is shared infrastructure and the S4PC pipeline is only its first
    consumer. "The pipeline still ran" does not validate a retrieval change --- a
    silently worse index still produces a confident deliverable, which is the most
    expensive failure mode this project has. Two incidents already made the point:
    a --limit smoke test published a 200-vector index over a 49,438-vector brain,
    and a WebFetch that returns a JS shell reads as success. Both were invisible to
    every check that existed at the time.

    Run this before and after ANY change to the index, embedding model, chunker, or
    metadata filters.

WHAT IT MEASURES
    assertions  hand-written intent ("a UI5 sorting question must reach
                developer_docs"). Keyed on `source`, so they survive a re-index.
    drift       overlap between today's top-k and a recorded baseline, per query.
                Catches the regressions nobody thought to assert.

    Drift is reported, not judged. A rebuild that ADDS a source legitimately moves
    results; the report shows what moved so a human decides. Only assertions fail
    the run (plus --max-drop, for CI).

USAGE
    python3.11 scripts/brain_regression.py --baseline    # record current retrieval
    python3.11 scripts/brain_regression.py               # assertions + drift vs baseline
    python3.11 scripts/brain_regression.py --only R-001  # single case, verbose
    python3.11 scripts/brain_regression.py --max-drop 0.4  # fail if mean overlap drops

    Runs where the index lives (EC2). Needs boto3 + faiss-cpu + numpy.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = BASE_DIR / "brain-tests"
QUERIES = TESTS_DIR / "queries.json"
BASELINE = TESTS_DIR / "baseline.json"

TOP_K = 10


def load_cases(only=None):
    cfg = json.loads(QUERIES.read_text(encoding="utf-8"))
    cases = cfg.get("cases", [])
    if only:
        cases = [c for c in cases if c["id"] in only]
        if not cases:
            sys.exit("No case matched %s" % only)
    return cases


def run_case(case, k=TOP_K):
    """Execute one case. Returns (hits, error_or_None)."""
    from brain_search import search
    f = case.get("filters") or {}
    try:
        hits = search(case["query"], k=k,
                      phase=f.get("phase"), agent_role=f.get("agent_role"),
                      deliverable_type=f.get("deliverable_type"),
                      source_system=f.get("source_system"))
        return hits, None
    except SystemExit as e:            # brain_search sys.exit()s on missing deps/index
        return [], str(e)
    except Exception as e:
        return [], "%s: %s" % (type(e).__name__, e)


def check_assertions(case, hits):
    """Return a list of failure strings (empty == passed)."""
    fails = []
    if case.get("expect_no_hits"):
        if hits:
            # Name the source SYSTEM, never the document. Failure strings are stored
            # in baseline.json, so a raw `source` here would put a client filename in
            # git by the back door -- the same leak fingerprint() redacts.
            fails.append("expected no hits, got %d (top source_system: %s)"
                         % (len(hits), hits[0].get("source_system") or "?"))
        return fails

    want_min = case.get("expect_min_hits")
    if want_min is not None and len(hits) < want_min:
        fails.append("expected >=%d hits, got %d" % (want_min, len(hits)))

    want_sys = case.get("expect_source_system")
    if want_sys:
        got = {h.get("source_system") for h in hits}
        if want_sys not in got:
            fails.append("no hit with source_system=%s (saw: %s)"
                         % (want_sys, ", ".join(sorted(str(g) for g in got)) or "none"))

    # Negative form. Needed because "this filter hides developer_docs" is NOT the
    # same claim as "this filter returns nothing" -- the SharePoint corpus answers
    # almost any phase filter, so the absence has to be asserted per source.
    absent_sys = case.get("expect_absent_source_system")
    if absent_sys:
        n = sum(1 for h in hits if h.get("source_system") == absent_sys)
        if n:
            fails.append("expected NO hits with source_system=%s, got %d"
                         % (absent_sys, n))

    want_src = case.get("expect_source_contains")
    if want_src:
        low = want_src.lower()
        if not any(low in str(h.get("source", "")).lower() for h in hits):
            fails.append("no hit whose source contains %r" % want_src)
    return fails


# Source systems whose document names are public by construction and therefore safe
# to write in clear: vendor documentation (CAP/UI5/Node) and SAP's own scope-item
# catalog, which is already committed under mcp-server/catalog/.
#
# Everything else is treated as client material and redacted. brain/ is gitignored
# exactly so client documents never reach git, and this repo is public -- a baseline
# that recorded `source` verbatim would have carried names like
# "<client> Cutover Plan.xlsx" and "... Role Mapping - Production <person>.xlsx"
# straight past that boundary. Hashing keeps drift detection exact (overlap is
# computed on identity, not on readability) while carrying no client data. The
# allowlist is deliberately closed: an unrecognised new source is redacted, not
# exposed.
PUBLIC_SOURCE_SYSTEMS = {"developer_docs", "sap_scope_catalog"}


def _ident(value, public):
    """Stable identity for a hit: readable when public, hashed when not."""
    if value is None:
        return None
    if public:
        return value
    return "redacted:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def fingerprint(hits):
    """Identity of a result set, for drift comparison. Client names never stored."""
    out = []
    for h in hits:
        ss = h.get("source_system")
        pub = ss in PUBLIC_SOURCE_SYSTEMS
        out.append({"chunk_file": _ident(h.get("chunk_file"), pub),
                    "source": _ident(h.get("source"), pub),
                    "source_system": ss,          # a category, not a document
                    "score": h.get("score")})
    return out


def overlap(base, now):
    """Fraction of the baseline's chunks still present. 1.0 == unchanged set."""
    b = [h["chunk_file"] for h in base if h.get("chunk_file")]
    n = {h["chunk_file"] for h in now if h.get("chunk_file")}
    if not b:
        return 1.0 if not n else 0.0
    return sum(1 for c in b if c in n) / float(len(b))


def main():
    ap = argparse.ArgumentParser(description="Brain retrieval regression harness")
    ap.add_argument("--baseline", action="store_true",
                    help="Record current retrieval as the new baseline")
    ap.add_argument("--only", nargs="*", help="Run only these case ids")
    ap.add_argument("-k", type=int, default=TOP_K, help="top-k per query (default %d)" % TOP_K)
    ap.add_argument("--max-drop", type=float, default=None,
                    help="Fail if mean overlap with baseline falls below this (0-1)")
    args = ap.parse_args()

    cases = load_cases(args.only)
    prior = {}
    if BASELINE.exists() and not args.baseline:
        try:
            prior = {c["id"]: c for c in
                     json.loads(BASELINE.read_text(encoding="utf-8")).get("cases", [])}
        except Exception:
            prior = {}

    results, n_fail, n_pending_fail, overlaps = [], 0, 0, []
    print("== %d cases, top_k=%d%s" % (len(cases), args.k,
                                       "  (RECORDING BASELINE)" if args.baseline else ""))

    for case in cases:
        hits, err = run_case(case, args.k)
        if err:
            sys.exit("FATAL: %s\n  (this harness runs where the index lives — EC2)" % err)

        fails = check_assertions(case, hits)
        pending = case.get("pending")
        # A `pending` case is a red test by design: it fails until the named ingest
        # has run. Counted separately so it never masks a real regression.
        if fails and pending:
            n_pending_fail += 1
            status = "PENDING"
        elif fails:
            n_fail += 1
            status = "FAIL"
        else:
            status = "ok"

        line = "  %-7s %-6s %-2d hits" % (status, case["id"], len(hits))
        if case["id"] in prior:
            ov = overlap(prior[case["id"]].get("hits", []), fingerprint(hits))
            overlaps.append(ov)
            line += "  overlap=%.0f%%" % (ov * 100)
        top = hits[0].get("source") if hits else None
        if top:
            line += "  top=%s" % str(top)[:52]
        print(line)
        for f in fails:
            print("          %s %s" % ("(pending %s)" % pending if pending else "->", f))

        results.append({"id": case["id"], "query": case["query"],
                        "filters": case.get("filters"), "pending": pending,
                        "failures": fails, "hits": fingerprint(hits)})

    if args.baseline:
        TESTS_DIR.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({
            "recorded_utc": datetime.now(timezone.utc).isoformat(),
            "top_k": args.k, "cases": results,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\n== baseline written: %s (%d cases)" % (BASELINE, len(results)))
        print("== client document names are redacted to hashes — safe to commit")
        return

    print("\n== %d passed, %d FAILED, %d pending (%d cases)"
          % (len(cases) - n_fail - n_pending_fail, n_fail, n_pending_fail, len(cases)))
    if overlaps:
        mean = sum(overlaps) / len(overlaps)
        print("== mean overlap with baseline: %.0f%%  (%d/%d cases compared)"
              % (mean * 100, len(overlaps), len(cases)))
        moved = [r["id"] for r, o in zip(results, overlaps) if o < 0.7]
        if moved:
            print("== moved most: %s" % ", ".join(moved[:12]))
        if args.max_drop is not None and mean < args.max_drop:
            sys.exit("FAIL: mean overlap %.2f below --max-drop %.2f" % (mean, args.max_drop))
    elif not args.baseline:
        print("== no baseline yet — run with --baseline to record one")
    if n_pending_fail:
        print("== pending failures are expected until their ingest has run")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
