#!/usr/bin/env python3
"""
Clean-core guard — Claude Code PostToolUse hook (matches Write|Edit).

Non-blocking and fail-safe by design: it logs every ABAP code write and flags classical /
non-clean-core patterns, but ALWAYS exits 0 so it can never interrupt a headless pipeline run
or an interactive edit. Findings are written to webapp/logs/clean-core-guard.log and surfaced
as an informational note.
"""
import sys, json, os, re, time

FORBIDDEN = [
    (r"\bCALL\s+FUNCTION\s+'BAPI_", "BAPI call (BAPIs are not released in Public Cloud)"),
    (r"\bBAPI_[A-Z0-9_]{3,}", "BAPI reference"),
    (r"(?m)^\s*REPORT\b", "classical REPORT program"),
    (r"\bSUBMIT\b", "SUBMIT (classical program execution)"),
    (r"\bCALL\s+TRANSACTION\b", "CALL TRANSACTION"),
    (r"(?m)^\s*PERFORM\b|\bFORM\b\s+\w+", "PERFORM/FORM subroutine"),
    (r"(?m)^\s*WRITE\b|\bWRITE\s*:\s*/", "classical WRITE list output"),
    (r"\bENHANCEMENT-POINT\b|\bENHANCEMENT-SECTION\b", "enhancement point/section"),
    (r"\bSMART\s*FORMS?\b|\bSAPSCRIPT\b", "Smart Forms / SAPscript"),
    (r"\bSELECT\b[\s\S]{0,120}?\bFROM\s+(MARA|MARC|MARD|MAKT|VBAK|VBAP|VBRK|VBRP|EKKO|EKPO|EBAN|"
     r"BKPF|BSEG|BSIS|ACDOCA|KNA1|LFA1|MKPF|MSEG|LIKP|LIPS|T001)\b",
     "direct read of a classical table (use the released CDS view instead)"),
]

def main():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    ti = (data.get("tool_input") or {})
    fpath = ti.get("file_path", "") or ""
    content = ti.get("content") or ti.get("new_string") or ""
    looks_abap = (
        fpath.lower().endswith(".abap")
        or os.path.basename(fpath).lower().startswith("06-code")
        or bool(re.search(r"\b(CLASS|METHOD|ENDCLASS|ENDMETHOD|ENDMODULE|DATA:)\b", content))
    )
    if not content or not looks_abap:
        return
    hits = sorted({label for pat, label in FORBIDDEN
                   if re.search(pat, content, re.IGNORECASE)})
    log_dir = os.path.join("webapp", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "clean-core-guard.log"), "a", encoding="utf-8") as fh:
            fh.write("%s | %s | %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                         fpath or "(inline)", "; ".join(hits) if hits else "clean"))
    except Exception:
        pass
    if hits:
        print("[clean-core-guard] Possible non-clean-core pattern(s) in %s: %s. "
              "S/4HANA Cloud Public Edition allows released objects only — run abap_cloud_lint and "
              "switch to released APIs / CDS views / BAdIs." % (os.path.basename(fpath) or "edited file",
                                                                ", ".join(hits)))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)   # fail-safe: never block a write or a pipeline run
