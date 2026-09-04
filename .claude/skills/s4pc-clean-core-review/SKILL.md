---
name: s4pc-clean-core-review
description: Runs an adversarial clean-core compliance review of ABAP code or a technical design for S/4HANA Cloud Public Edition, flagging any non-released or classical-ABAP constructs and returning a clear SHIP / FIX / REDESIGN verdict. Use when the user asks to review, check, or validate code or designs for Public Cloud readiness.
---

# Clean-Core Review (S/4HANA Cloud Public Edition)

Adversarial review of code or designs against Public Cloud constraints. Verdict-driven: end with
SHIP, FIX (itemized), or REDESIGN.

## Procedure

1. **Deterministic pass first** — for code: run MCP `abap_cloud_lint` on every source unit; for
   designs: extract every SAP object named and run `check_object_release_state` on each.
   Tool findings are non-negotiable facts; your own findings are opinions ranked below them.
2. **Structural review** —
   - RAP correctness: behavior definition matches CDS, draft handling, numbering, authorization
     master/dependent, late numbering pitfalls.
   - No logic in places that break upgrades (e.g. misuse of key-user BAdIs for heavy processing).
   - Error handling: messages via released message classes/objects, no silent CX_ROOT swallowing.
   - Performance: no SELECT in loops, proper CDS associations instead of nested reads, pagination
     on API consumption.
   - Security: released authorization objects checked, no hard-coded users/orgs, no secrets,
     input validation on all external payloads.
   - Side-by-side (CAP / UI5 / Node / JS): review against the authoritative docs. **Read UI5,
     Fiori Elements, CAP and Node from the brain** — `search_brain(query="<API or pattern>",
     source_system="developer_docs")`, no `phase` filter (vendor docs are phase-independent and a
     phase filter hides them). ui5.sap.com cannot be fetched at all: it is an SPA whose every topic
     URL is a `#/topic/...` fragment, so a fetch returns a ~2 KB shell and *succeeds* while
     grounding nothing. Cite for humans: CAP https://cap.cloud.sap/docs/, SAP UI5
     https://ui5.sap.com/, Node.js https://nodejs.org/docs/latest/api/, JavaScript
     https://www.w3schools.com/js/default.asp, npm https://www.npmjs.com/package/npm,
     SAP Community https://community.sap.com/.
3. **Challenge round** — ask of every component: "What breaks on the next release upgrade?"
   "What happens if the BAdI/API is deprecated?" "Is this testable with ABAP Unit test doubles?"
4. **Verdict** — table of findings (ID, severity Critical/Major/Minor, source: lint|release-check|
   reviewer, description, required fix), then the verdict. Criticals or any lint FAIL → never SHIP.

## Severity rules

- Critical: unreleased object usage, forbidden statement, security hole, data-loss risk.
- Major: upgrade-risk patterns, missing error handling, untestable design, performance traps.
- Minor: naming, style, documentation gaps.
