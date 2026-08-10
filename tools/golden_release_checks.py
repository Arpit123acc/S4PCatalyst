#!/usr/bin/env python3
"""
Golden-set eval for the S4PC governance layer (#5).

Runs known objects through `check_object_release_state` and asserts the expected
verdict, so a change to the catalogs or the check logic can't silently regress
clean-core behaviour. Deterministic — no LLM, no network. Exit 0 = all pass,
non-zero = a regression to investigate.

Run:  python tools/golden_release_checks.py
"""
import os, sys, json, subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "mcp-server", "server.py")

# (object_name, expected_verdict, why)
CASES = [
    ("I_MaterialStock",      "LIKELY_RELEASED", "released VDM interface view (in seed)"),
    ("I_Product",            "LIKELY_RELEASED", "released VDM interface view (in seed)"),
    ("API_CLFN_PRODUCT_SRV", "LIKELY_RELEASED", "released OData API naming (API_*/_SRV)"),
    ("BAPI_PO_CREATE1",      "NOT_AVAILABLE",   "BAPI — classical, not available in cloud"),
    ("MARA",                 "NOT_AVAILABLE",   "classical table — not accessible in cloud code"),
    ("ZZ1_MyRandomObject",   "NOT_VERIFIED",    "unknown custom object — must be verified"),
]
VERDICTS = ("NOT_AVAILABLE", "LIKELY_RELEASED", "NOT_VERIFIED")

def verdict_for(obj):
    r = subprocess.run(
        [sys.executable, SERVER, "--tool", "check_object_release_state",
         json.dumps({"object_name": obj})],
        capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    # prefer a parsed field if present, else fall back to a substring scan
    try:
        data = json.loads(r.stdout)
        for k in ("state", "verdict", "release_state", "status"):
            if isinstance(data, dict) and str(data.get(k, "")).upper() in VERDICTS:
                return data[k].upper(), out.strip()
    except Exception:
        pass
    for v in VERDICTS:
        if v in out:
            return v, out.strip()
    return "(none)", out.strip()

def main():
    fails = 0
    print("S4PC golden release-check eval\n" + "-" * 68)
    for obj, expected, why in CASES:
        got, _ = verdict_for(obj)
        ok = (got == expected)
        if not ok:
            fails += 1
        print("[%s] %-24s expected %-16s got %-16s (%s)"
              % ("PASS" if ok else "FAIL", obj, expected, got, why))
    print("-" * 68)
    print("%d/%d passed" % (len(CASES) - fails, len(CASES)))
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
