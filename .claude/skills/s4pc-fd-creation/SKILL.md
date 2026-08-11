---
name: s4pc-fd-creation
description: Produces a structured, review-ready Functional Design (FD) document for an S/4HANA Cloud Public Edition extension, turning raw business inputs — user stories, process descriptions, or meeting notes — into a clear specification. Use when the user asks to write, draft, or create an FD.
---

# FD Creation (S/4HANA Cloud Public Edition)

Six-step FD creation, adapted from the team's FD Creation workflow, with Public Cloud guardrails.

## Steps

1. **Intake** — collect business inputs; list open questions instead of inventing answers.
2. **Process context** — affected end-to-end process, Fiori apps involved (name apps by ID only
   if verifiable — otherwise describe the app and mark the ID "to verify"), roles, volumes.
3. **Requirement specification** — numbered functional requirements, each testable; explicit
   in-scope / out-of-scope; acceptance criteria per requirement.
4. **Solution outline (functional level)** — run the s4pc-extensibility-decision skill inline:
   extensibility mode per requirement + object inventory with MCP release verdicts. The FD must
   NOT promise anything that failed the release gate. List every custom object the solution would
   CREATE (with a proposed namespaced name) so the downstream proposal can build the
   **Custom-Object Naming Contract** the human locks at solution approval. If the client mandates a
   specific build mode, record it in the **Client Constraints** block (see template) — it is honoured
   as a build-ready option downstream even when it is not the clean-core best fit; only genuine
   infeasibility overrides it.
5. **Data & integration view** — entities (released CDS/API names where verified), field lists
   (source: `odata_get_metadata` in live mode, otherwise marked as assumption), error handling,
   authorization concept (business catalogs/roles, not S_TCODE).
6. **Assembly & review** — assemble the FD, then self-review against the checklist below; output
   `FD-<ID>.md` plus `FD-<ID>-open-items.md`.

## FD document template

1. Header (ID, title, author, date, status, extensibility mode) — plus an optional **Client
   Constraints** block when the client mandates a build mode (see below)
2. Business context & problem statement
3. Process flow (as-is / to-be)
4. Functional requirements (numbered, testable)
5. Solution outline per requirement (mode + released objects + release verdicts)
6. Data model & integration
7. Authorizations
8. Error handling & edge cases
9. Test scenarios (mapped 1:1 to requirements)
10. Assumptions & open items
11. Sources & verification (tool-verified facts vs tenant-to-verify items — keep separated)

### Client Constraints (optional)

When the client dictates *how* a requirement must be built (not just *what*), capture it verbatim in
the FD so the downstream pipeline honours it at solution approval:

```
## Client Constraints
- Preferred / mandated extensibility mode: side-by-side (BTP)   ← key user | developer | side-by-side
- Reason / mandate: <e.g. client enterprise-architecture policy — all new integrations on BTP>
- Overrides recommendation: yes                                 ← yes | no
```

The solution proposal keeps its **honest** clean-core recommendation **and** presents the mandated mode
as a **build-ready** option (for BTP, with its SAP Discovery Center link + pricing metric); the
developer selects it at solution approval. A mandate cannot make an infeasible design feasible (no
BAPIs / unreleased objects / classical ABAP) — genuine infeasibility is flagged and wins.

## Review checklist (step 6)

- Every requirement has acceptance criteria and at least one test scenario.
- No classical-ABAP or Private-Cloud concepts leaked in (BAPI, user exit, SE38, SM37, custom RFC...).
- Every named SAP object has a release verdict from the MCP server.
- Open questions are listed, not silently answered.
