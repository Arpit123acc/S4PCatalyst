#!/usr/bin/env python3
"""PROBE: how many "authenticated" documents actually need a session?

Needs no credentials, no browser, no cookie. That is the point.

WHY THIS EXISTS
    sapme_fetch does not measure whether a document needs authentication. It
    INFERS it from the host:

        needs_auth = host not in PUBLIC_HOSTS

    Everything on support.sap.com is therefore assumed private. That assumption
    drives the whole human-in-the-loop design: a person opens DevTools, pastes
    a cookie that dies in minutes, and babysits a download of thousands of
    files.

    On 2026-09-24 a negative control in probe_sap_login fetched three of those
    "authenticated" rows with a deliberately junk cookie and got real documents
    back. If that generalises, most of the ceremony is unnecessary and the
    download can run unattended with no credentials at all -- which is a much
    better answer than storing an SAP password.

    So measure it instead of assuming either way. The inference could be wrong
    in the expensive direction (needless gatekeeping) or the dangerous one
    (rows marked public that are not); only a sample tells us which.

METHOD
    Take a stratified sample of rows the manifest marks needs_auth, fetch each
    with NO cookie at all, and classify the response with sapme_fetch.classify
    -- the same function the fetcher uses, so a "login" here means exactly what
    it means in a real run. Stratify by host and by whether the path is a /dam/
    asset, because those are the two properties most likely to decide it, and
    an unstratified sample of a list sorted by scope item would just measure
    whichever host happens to come first.

WHAT THE ANSWER MEANS
    all public      the cookie step is theatre; drop needs_auth to a measured
                    flag and the download becomes a cron job with no secrets.
    all private     the inference is right, and automating the download needs a
                    stored credential. Decide on a technical user first.
    mixed           the useful case: fetch the public majority unattended and
                    keep the human session for the remainder, which is a far
                    smaller ask than babysitting the whole corpus.

Usage:
    python3.11 scripts/probe_public_access.py              # 40 rows
    python3.11 scripts/probe_public_access.py -n 120       # tighter bound
    python3.11 scripts/probe_public_access.py --all-hosts  # include public rows too
"""

import sys
import time
import random
import argparse
import collections
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Be a good citizen: this hits SAP's CDN, so keep the sample small and paced.
PAUSE = 0.4


def stratified(rows, n, seed=7):
    """A sample spread across strata, not the first n of a sorted list.

    The manifest is ordered by scope item, so rows near each other share a
    host, a folder and a document type. Taking the head would measure one
    corner of the corpus and report it as the whole -- the same mistake as
    judging a session from a single probe.
    """
    buckets = collections.defaultdict(list)
    for r in rows:
        host = urllib.parse.urlparse(r["url"]).netloc
        dam = "/dam/" in r["url"]
        buckets[(host, dam)].append(r)

    rng = random.Random(seed)
    for v in buckets.values():
        rng.shuffle(v)

    # Round-robin across strata so a small n still touches every one of them.
    out, keys = [], sorted(buckets, key=lambda k: (-len(buckets[k]), str(k)))
    while len(out) < n and any(buckets[k] for k in keys):
        for k in keys:
            if buckets[k] and len(out) < n:
                out.append((k, buckets[k].pop()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=40, help="sample size (default 40)")
    ap.add_argument("--source", default="sapbp")
    ap.add_argument("--all-hosts", action="store_true",
                    help="also sample rows already marked public")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    from sapme_fetch import load_rows, fetch, classify          # noqa: PLC0415

    rows = load_rows(a.source, False)
    pool = [r for r in rows if r.get("url")]
    if not a.all_hosts:
        pool = [r for r in pool if r.get("needs_auth")]
    if not pool:
        return "no rows to sample"

    sample = stratified(pool, a.n, a.seed)
    print("== sampling %d of %d rows that the manifest marks needs_auth=%s"
          % (len(sample), len(pool), "any" if a.all_hosts else "True"))
    print("   fetching each with NO cookie; classify() decides what came back")
    print("")

    tally = collections.defaultdict(collections.Counter)
    failures = []
    for i, (stratum, r) in enumerate(sample, 1):
        host, dam = stratum
        label = "%s%s" % (host, " /dam/" if dam else "")
        try:
            body, _ctype, _final = fetch(r["url"], "", attempts=1)
            kind = classify(body, r["url"])
        except Exception as exc:
            kind = "error:%s" % type(exc).__name__
            failures.append((r["url"], str(exc)[:70]))
        tally[label][kind] += 1
        print("   %3d/%d  %-34s %-6s %s"
              % (i, len(sample), label[:34], kind, r["url"].rsplit("/", 1)[-1][:46]))
        time.sleep(PAUSE)

    print("")
    print("== by stratum")
    doc_kinds = ("zip", "pdf")
    tot_public = tot_login = tot_other = 0
    for label in sorted(tally):
        c = tally[label]
        pub = sum(c[k] for k in doc_kinds)
        log = c["login"]
        oth = sum(c.values()) - pub - log
        tot_public += pub
        tot_login += log
        tot_other += oth
        print("   %-38s %3d served  %3d login  %3d other" % (label[:38], pub, log, oth))

    n = tot_public + tot_login + tot_other
    print("")
    print("== result over %d sampled rows" % n)
    print("   served a real document with no cookie : %d" % tot_public)
    print("   returned a login page                 : %d" % tot_login)
    print("   neither (html/empty/error)            : %d" % tot_other)
    if failures:
        print("")
        print("   %d transport failure(s) -- these say nothing about auth:" % len(failures))
        for u, e in failures[:4]:
            print("      %s  %s" % (u.rsplit("/", 1)[-1][:40], e))

    print("")
    if n and tot_login == 0 and tot_public:
        print("   VERDICT: nothing in this sample needed a session. needs_auth is")
        print("   inferred from the host and looks WRONG for these rows. Before")
        print("   acting on that, widen the sample (-n 200) -- absence of evidence")
        print("   over 40 rows is a weak claim about 1,350.")
        print("   If it holds, the download needs no credential and no human, and")
        print("   the right fix is to MEASURE needs_auth rather than infer it.")
    elif tot_login and tot_public:
        print("   VERDICT: mixed. %d of %d need a session. Fetch the rest"
              % (tot_login, n))
        print("   unattended and keep the human session for that remainder.")
    elif tot_login and not tot_public:
        print("   VERDICT: the inference is right -- these genuinely need a session.")
        print("   Automating the download therefore needs a stored credential;")
        print("   settle the technical-user question before building it.")
    else:
        print("   VERDICT: inconclusive -- too few clean responses to judge.")
        print("   Re-run with a larger -n, or check connectivity from this host.")
    return None


if __name__ == "__main__":
    err = main()
    if err:
        sys.exit("\nPROBE INCONCLUSIVE: %s" % err)
