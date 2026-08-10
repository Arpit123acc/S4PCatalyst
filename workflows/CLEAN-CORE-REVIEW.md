# CLEAN-CORE-REVIEW — Code & Design Review

**Type:** Process
**Skill:** `s4pc-clean-core-review`
**Realization:** An adversarial clean-core review of ABAP code or a technical design that flags any non-released or classical-ABAP constructs and returns a clear SHIP / FIX / REDESIGN verdict.

Adversarial clean-core review for S/4HANA Cloud Public Edition. Verdict-driven: SHIP / FIX / REDESIGN.

## Steps
1. **Deterministic pass** — `abap_cloud_lint` on all code; `check_object_release_state` on every referenced object. Tool findings outrank reviewer opinions.
2. **Structural review** — RAP correctness, error handling, performance (no SELECT in loops), security (released auth objects, no secrets/hardcoding), testability.
3. **Challenge round** — "what breaks on the next upgrade?", "what if this BAdI/API is deprecated?", "how is this unit-tested?"
4. **Verdict** — findings table (severity Critical/Major/Minor, source lint|release-check|reviewer) + verdict. Any Critical or lint FAIL → never SHIP.
