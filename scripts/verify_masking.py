#!/usr/bin/env python3
"""Did PII masking actually work on the corpus that is on disk?

WHY THIS EXISTS
    Masking runs once, at ingest, through spaCy NER plus the regex rules in
    sharepoint_ingest._MASK_RULES. Nothing has ever checked the RESULT. So the claim
    "the corpus is PII-masked" rested on the masker having been called -- not on the
    corpus being clean -- and those are different claims. The corpus is now mirrored
    to S3 and is intended to face a client engagement, so the difference matters.

HOW IT CHECKS, AND WHY THIS WAY
    It re-applies the masker's OWN high-confidence rules. If a rule that should have
    replaced something still matches text in a chunk, masking did not run on that
    chunk -- or ran before that rule existed. Reusing the real rules rather than a
    second copy means the check cannot drift away from the thing it is checking.

    Only the STRUCTURAL rules are re-applied: credentials, e-mail, employee ids,
    internal URLs, IP addresses, phone numbers, logical systems. The name and
    organisation rules are deliberately excluded -- they are heuristics guarded by a
    business-vocabulary list, they match ordinary SAP prose like "Material Master",
    and re-running them would drown a real finding in false positives. Structural PII
    is unambiguous; deciding whether two capitalised words are a person is not, and
    re-running the same judgement is circular anyway.

    It also counts placeholders, which is the check nobody thinks to write: zero
    "[EMAIL]" across 36,912 chunks would mean masking silently did nothing, and every
    negative check above would pass triumphantly.

OUTPUT IS REDACTED BY DEFAULT
    A tool that hunts for leaked PII must not print it, or the report becomes the
    leak -- into a terminal, a CI log, a chat window. Matches are summarised as a
    rule name, a count and a shape (first two characters and a length). --show-samples
    prints the real matches and exists for someone triaging a confirmed finding on a
    trusted machine; it says so when used.

    Source FILENAMES are withheld for the same reason. They are not masked -- masking
    applies to document text -- and this corpus has client names in its filenames.

Usage:
    python3.11 scripts/verify_masking.py                 # summary, redacted
    python3.11 scripts/verify_masking.py --show-samples  # real matches (careful)
    python3.11 scripts/verify_masking.py --show-sources  # which files (careful)
    python3.11 scripts/verify_masking.py --limit 500     # sample, for a quick pass

Exit code is 1 when a structural leak is found, so it can gate a refresh.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

CHUNKS_DIR = os.path.join(BASE_DIR, "brain", "sharepoint", "chunks")

# The placeholders whose rules are safe to re-apply. Everything absent from this set
# is a heuristic (see the module docstring) and is checked only by its placeholder
# COUNT, not by re-matching.
STRUCTURAL = {
    "[CREDENTIAL]", "[EMAIL]", "[INTERNAL_URL]", "[SAP_TENANT_URL]",
    "[IP_ADDRESS]", "[LOGICAL_SYSTEM]", "[EMP_ID]", "[PHONE]",
}

# Every placeholder, for the positive check.
ALL_PLACEHOLDERS = STRUCTURAL | {
    "[TRANSPORT]", "[TICKET]", "[CONTRACT_REF]", "[CLIENT_OBJECT]", "[CLIENT]",
    "[RATE]", "[AMOUNT]", "[PROJECT]", "[PERSON]",
}


def shape(text):
    """A non-disclosing fingerprint: enough to recognise a pattern, not to read it."""
    text = str(text)
    return "%s… (%d chars)" % (text[:2], len(text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=CHUNKS_DIR)
    ap.add_argument("--limit", type=int, default=0,
                    help="scan at most N chunks (0 = all)")
    ap.add_argument("--show-samples", action="store_true",
                    help="print the actual matched text — this is PII; trusted host only")
    ap.add_argument("--show-sources", action="store_true",
                    help="print the source filenames — these are NOT masked")
    args = ap.parse_args()

    import sharepoint_ingest as si                            # noqa: PLC0415
    rules = [(rx, label) for rx, label in si._MASK_RULES if label in STRUCTURAL]
    if not rules:
        print("FATAL: no structural rules matched %s — the placeholder labels in "
              "sharepoint_ingest have changed and STRUCTURAL needs updating."
              % sorted(STRUCTURAL))
        return 2
    print("re-applying %d structural rule(s): %s"
          % (len(rules), ", ".join(sorted({l for _, l in rules}))))

    if not os.path.isdir(args.chunks):
        print("no chunk tree at %s — run this on the host that has the corpus."
              % args.chunks)
        return 2

    hits = defaultdict(list)          # label -> [matched text]
    sources = defaultdict(set)        # label -> {source}
    placeholders = Counter()
    scanned = chars = 0

    for root, _dirs, names in os.walk(args.chunks):
        for name in names:
            if not name.endswith(".json"):
                continue
            if args.limit and scanned >= args.limit:
                break
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except Exception:
                continue
            text = rec.get("text") or ""
            scanned += 1
            chars += len(text)
            for ph in ALL_PLACEHOLDERS:
                n = text.count(ph)
                if n:
                    placeholders[ph] += n
            for rx, label in rules:
                for m in rx.finditer(text):
                    hits[label].append(m.group(0))
                    sources[label].add(rec.get("source") or "?")

    print("\nscanned %d chunk(s), %.1f MB of masked text" % (scanned, chars / 1e6))

    print("\n-- placeholders present (proves masking RAN)")
    if not placeholders:
        print("   NONE. Across %d chunks that is not a clean corpus, it is a corpus "
              "that was never masked." % scanned)
    for ph, n in placeholders.most_common():
        print("   %-18s %d" % (ph, n))

    print("\n-- residual structural PII (masking MISSED these)")
    if not hits:
        print("   none — every structural rule found nothing left to mask")
    for label in sorted(hits):
        found = hits[label]
        uniq = sorted(set(found))
        print("   %-18s %d occurrence(s), %d distinct, in %d file(s)"
              % (label, len(found), len(uniq), len(sources[label])))
        for value in uniq[:5]:
            print("        %s" % (value if args.show_samples else shape(value)))
        if len(uniq) > 5:
            print("        … and %d more" % (len(uniq) - 5))
        if args.show_sources:
            for s in sorted(sources[label])[:5]:
                print("        in: %s" % s)

    if hits and not args.show_samples:
        print("\n   Values are redacted. --show-samples prints them; that output IS "
              "the PII, so keep it off shared terminals and CI logs.")

    total = sum(len(v) for v in hits.values())
    print("\n== %d residual structural match(es) across %d chunk(s)" % (total, scanned))
    if total:
        print("   Masking did not cover these. Decide per rule whether it is a rule "
              "that post-dates the ingest (re-ingest fixes it) or a rule that does "
              "not match the shape in this corpus (the rule needs widening).")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
