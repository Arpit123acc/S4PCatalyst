#!/usr/bin/env python3
"""The masking verifier must find real leaks, stay quiet on clean text, and not leak.

WHY THIS EXISTS
    verify_masking.py is the check on a safety property, so it has to be right in both
    directions. A verifier that misses a leak is worse than none -- it converts "we
    never checked" into "we checked and it was clean". And a verifier that PRINTS what
    it finds turns the report into the leak.

WHAT IT PINS
    * a chunk carrying an unmasked e-mail / employee id / credential is reported;
    * a properly masked chunk produces nothing;
    * placeholders are counted, because an empty corpus-wide placeholder count is the
      signature of masking never having run -- and every negative check would pass;
    * output is REDACTED by default and only --show-samples discloses;
    * the heuristic name/org rules are NOT re-applied. "Material Master Data" and
      "Purchase Order Header" are ordinary SAP prose that those rules match; if a
      future change adds them to STRUCTURAL, this test fails rather than the report
      filling with noise.

Usage:
    python brain-tests/test_verify_masking.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "verify_masking.py"

FAILS = []


def check(label, got, want):
    ok = got == want
    print("  %-4s %-56s got=%s" % ("ok" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append("%s: got %r, want %r" % (label, got, want))


def _corpus(chunks):
    tmp = Path(tempfile.mkdtemp(prefix="s4pc-mask-"))
    d = tmp / "chunks" / "Explore" / "general"
    d.mkdir(parents=True)
    for i, (source, text) in enumerate(chunks):
        (d / ("c%02d_0000.json" % i)).write_text(
            json.dumps({"id": "c%02d" % i, "source": source, "text": text}),
            encoding="utf-8")
    return tmp / "chunks"


def _run(chunks_dir, *extra):
    # Decoded explicitly as UTF-8: with text=True subprocess uses the locale codepage,
    # and on Windows that turns the script's em-dashes into mojibake, so assertions
    # written against the real message fail for a reason that has nothing to do with
    # masking. errors="replace" keeps a mangled byte from raising instead.
    p = subprocess.run([sys.executable, str(SCRIPT), "--chunks", str(chunks_dir),
                        *extra], capture_output=True, cwd=str(REPO))
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    return p.returncode, out


def main():
    print("a clean, properly masked corpus passes")
    clean = _corpus([
        ("FD_Report.docx",
         "Contact [EMAIL] regarding [CLIENT] purchase orders. Owner [PERSON], "
         "employee [EMP_ID]. System [LOGICAL_SYSTEM]. Uses I_PurchaseOrderAPI01 "
         "and API_CLFN_PRODUCT_SRV per the Material Master Data design."),
    ])
    rc, out = _run(clean)
    check("exit 0", rc, 0)
    check("reports no residual PII", "none — every structural rule" in out, True)
    check("and confirms masking ran", "[EMAIL]" in out and "[PERSON]" in out, True)

    print("\nresidual PII is found")
    leaky = _corpus([
        ("Spec.docx", "Mail arpit.srivastava@example.com about the order."),
        ("Notes.docx", "Raised by I1234567 and approved by C9876543."),
        ("Runbook.docx", "password: hunter2correct is stored in the vault."),
    ])
    rc, out = _run(leaky)
    check("exit 1", rc, 1)
    check("email reported", "[EMAIL]" in out and "occurrence(s)" in out, True)
    check("employee id reported", "[EMP_ID]" in out, True)
    check("credential reported", "[CREDENTIAL]" in out, True)

    print("\nthe report does NOT disclose by default")
    # The whole value of the tool disappears if running it copies PII into a log.
    check("the address is not printed", "arpit.srivastava@example.com" in out, False)
    check("nor the employee id", "I1234567" in out, False)
    check("nor the password", "hunter2correct" in out, False)
    check("a shape is printed instead", "chars)" in out, True)
    check("and it says values are redacted", "Values are redacted" in out, True)

    print("\n--show-samples discloses, deliberately")
    rc, out = _run(leaky, "--show-samples")
    check("now the address appears", "arpit.srivastava@example.com" in out, True)
    check("source filenames still withheld", "Spec.docx" in out, False)
    rc, out = _run(leaky, "--show-sources")
    check("--show-sources names the file", "Spec.docx" in out, True)

    print("\nan UNMASKED corpus is caught by the placeholder count, not just the rules")
    # The dangerous silent case: masking never ran. Text with no structural PII would
    # otherwise pass every negative check.
    never = _corpus([("Plan.docx",
                      "Cutover plan for the Material Master Data load. "
                      "Owner is Jane Doe of Contoso Industries.")])
    rc, out = _run(never)
    check("no placeholders found", "NONE." in out, True)
    check("and it says what that means",
          "never masked" in out, True)

    print("\nheuristic rules are NOT re-applied")
    # If [PERSON]/[CLIENT] were added to STRUCTURAL, this ordinary SAP prose would be
    # reported as a leak and the report would become useless.
    prose = _corpus([("TD.docx",
                      "The Material Master Data and Purchase Order Header views "
                      "feed the Sales Order Item report. [EMAIL] owns it.")])
    rc, out = _run(prose)
    check("ordinary SAP prose is not a finding", rc, 0)

    print("\ndegradation")
    rc, out = _run(Path(tempfile.mkdtemp()) / "nope")
    check("absent chunk tree exits 2, not 0", rc, 2)
    check("and says where to run it", "run this on the host" in out, True)

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
