#!/usr/bin/env python3
"""The CP2 release-verification gate must see ABAP classes, and must find the code file.

WHY THIS EXISTS
    _unverified_objects_in_code compares SAP objects referenced in the built code against
    the verdict table, and CP2 approval is refused when anything is unverified. It had
    three holes, and all three report identically to "nothing wrong":

    1. NO PATTERN FOR ABAP CLASSES OR INTERFACES. CDS views, API services and CE_
       functions were covered; CL_* and IF_* were not. What cannot be extracted cannot be
       compared.

       SMART-SEARCH-FD-R2, 2026-09-08, Gate 3 finding F-19 (Critical): "two ABAP classes
       on the outbound write path are used by the built code but are absent from the
       release-verdict inventory, so they passed through Gate 1 and Gate 2." The gate ran,
       returned nothing, and the run was approved.

       The regex carried a measurement -- "1 true positive, 0 false positives across the
       bundled example runs" -- which was true and whose corpus contained no
       standard-class reference. A gate validated on inputs that omit the case it misses
       reports clean.

    2. CASE SENSITIVITY. Even with the pattern added, CL_[A-Z0-9_]+ would still have
       missed F-19: ABAP source is conventionally lowercase, so the write path reads
       cl_abap_context_info=>get_user_technical_name( ). Only cl_/if_ are relaxed -- a
       blanket re.I would make C_/E_/R_/P_ match ordinary ABAP locals (c_max, e_result,
       r_value, p_param) and flood the gate with false positives.

    3. HARDCODED FILENAMES. The sibling helper _find_gate2_review globs as a fallback
       precisely because hardcoding one name "silently disabled this gate entirely (a run
       was approved with 6 open Majors)". The same lesson had not been applied here.

    The verdicts-side pattern was widened in the same change. Detection without
    recognition would make the gate UNBLOCKABLE: it would flag cl_abap_context_info, you
    would add the verdict, and it would flag it again forever. A gate you cannot satisfy
    gets worked around, which is worse than one that never fired.

Usage:
    python brain-tests/test_release_gate.py
"""

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


def _unverified(d):
    """Names only. The function returns (name, section) pairs so the CP2 blocker can say
    WHERE an object is; the section is asserted separately below."""
    return [name for name, _section in app._unverified_objects_in_code(d)]


def _run_dir(files):
    d = Path(tempfile.mkdtemp(prefix="s4pc-relgate-"))
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return str(d)


CODE = """# Build

```abap
METHOD assign_to_me.
  DATA(lv_user) = cl_abap_context_info=>get_user_technical_name( ).
  DATA(lo_api)  = NEW zcl_ss_assign_to_me( ).
  SELECT SINGLE * FROM I_EngmtProjectTeamMember INTO @DATA(ls_m).
  lo_api->call( iv_service = 'API_ENTERPRISE_PROJECT_SRV' ).
ENDMETHOD.
```
"""

VERDICTS_PARTIAL = """# Release verdicts
| Object | Verdict |
|---|---|
| I_EngmtProjectTeamMember | LIKELY_RELEASED (catalog_hit) |
| API_ENTERPRISE_PROJECT_SRV | LIKELY_RELEASED (catalog_hit) |
"""

VERDICTS_FULL = VERDICTS_PARTIAL + "| CL_ABAP_CONTEXT_INFO | LIKELY_RELEASED (catalog_hit) |\n"


def main():
    print("THE REGRESSION: an ABAP class in the code with no verdict must be caught")
    d = _run_dir({"06-build.md": CODE, "03-release-verdicts.md": VERDICTS_PARTIAL})
    got = _unverified(d)
    check("the class is flagged", [g.upper() for g in got], ["CL_ABAP_CONTEXT_INFO"])
    check("reported in the code's own spelling", got, ["cl_abap_context_info"])

    print("\nonce its verdict is recorded, the gate stops blocking")
    # Without this the gate would be unsatisfiable -- see the header.
    d = _run_dir({"06-build.md": CODE, "03-release-verdicts.md": VERDICTS_FULL})
    check("nothing unverified", _unverified(d), [])

    print("\ninterfaces too")
    d = _run_dir({"06-build.md": "```abap\nDATA lo TYPE REF TO if_oo_adt_classrun.\n```",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("interface flagged", _unverified(d), ["if_oo_adt_classrun"])

    print("\ncustom objects stay OUT - governed by the naming contract, not verdicts")
    # \\b means ZCL_/ZIF_ cannot match CL_/IF_: the preceding Z is a word character.
    d = _run_dir({"06-build.md": "```abap\nDATA lo TYPE REF TO zcl_ss_assign_to_me.\n"
                                 "DATA li TYPE REF TO zif_ss_handler.\n```",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("ZCL_/ZIF_ not flagged", _unverified(d), [])

    print("\nprose outside a code fence does not block")
    # A design that explicitly REJECTS an object still names it in prose.
    d = _run_dir({"06-build.md": "We rejected CL_GUI_FRONTEND_SERVICES as not cloud-ready.\n",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("unfenced mention ignored", _unverified(d), [])

    print("\nthe filename hole: a differently-named build file is still found")
    d = _run_dir({"06-build-v2.md": CODE, "03-release-verdicts.md": VERDICTS_PARTIAL})
    check("code file resolved by pattern",
          _unverified(d), ["cl_abap_context_info"])
    d = _run_dir({"06-build.md": CODE, "03-objects.md": VERDICTS_PARTIAL})
    check("verdict file resolved by pattern",
          _unverified(d), ["cl_abap_context_info"])

    print("\ngenuinely absent files still return empty (nothing to compare)")
    check("no code file",
          _unverified(_run_dir({"03-release-verdicts.md": "x"})), [])
    check("no verdict file",
          _unverified(_run_dir({"06-build.md": CODE})), [])
    check("empty dir", _unverified(_run_dir({})), [])

    print("\nthe original coverage still holds")
    # CDS views are CamelCase by SAP convention and stay case-SENSITIVE.
    d = _run_dir({"06-build.md": "```abap\nSELECT * FROM I_MaterialStock.\n```",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("CDS view still caught", _unverified(d), ["I_MaterialStock"])

    print("\ndiffering case between code and verdicts is not a missing verdict")
    d = _run_dir({"06-build.md": "```abap\nSELECT * FROM I_MaterialStock.\n```",
                  "03-release-verdicts.md": "| I_MATERIALSTOCK | LIKELY_RELEASED |\n"})
    check("matched case-insensitively", _unverified(d), [])

    print("\nABAP locals must NOT be flagged")
    # The reason cl_/if_ are relaxed but c_/e_/r_/p_ are not.
    d = _run_dir({"06-build.md": "```abap\nCONSTANTS c_max TYPE i VALUE 10.\n"
                                 "DATA e_result TYPE string.\nDATA r_value TYPE i.\n"
                                 "DATA p_param TYPE i.\n```",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("no false positives from locals", _unverified(d), [])

    print("\nthe section is captured, so the blocker can say WHERE the object is")
    # Without this the CP2 message named the object but told the reviewer to comment on
    # "the relevant file" without saying which -- unactionable on a build with 9 sections.
    d = _run_dir({"06-build.md": "# Build\n\n## 8. ZCL_STK_CLFN_CLIENT\n\n"
                                 "```abap\nINTERFACES if_t100_message.\n```\n",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("object reported with its section",
          app._unverified_objects_in_code(d),
          [("if_t100_message", "8. ZCL_STK_CLFN_CLIENT")])

    print("\nan object before any heading still reports, with an empty section")
    # The blocker must degrade to the old behaviour rather than crash on a build file
    # that opens with code.
    d = _run_dir({"06-build.md": "```abap\nINTERFACES if_t100_message.\n```\n",
                  "03-release-verdicts.md": "| Object |\n|---|\n"})
    check("no heading yet", app._unverified_objects_in_code(d),
          [("if_t100_message", "")])

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
