---
name: s4pc-extensibility-decision
description: Recommends the right extensibility approach for an S/4HANA Cloud Public Edition requirement — Key User (in-app), Developer (ABAP Cloud), or Side-by-Side (BTP) — with a defensible, clean-core rationale and a Feasibility / Approach / Cost rating. Use when the user asks "how should we build X" or "which extensibility should we use".
---

# Extensibility Decision (S/4HANA Cloud Public Edition)

Produce a defensible, clean-core extensibility decision. Never decide from memory alone.

## Procedure

1. Call MCP `extensibility_advisor` with the requirement.
2. Decompose the requirement into capabilities (field, logic, UI, data access, integration,
   scheduling, output). One requirement often needs MULTIPLE modes — say so explicitly.
3. For each capability, identify candidate SAP objects via MCP `search_released_apis` /
   `search_released_badis`, then gate each with `check_object_release_state`.
4. Apply the decision ladder (stop at the first rung that fits):
   1. Fit-to-standard / configuration — no extension at all.
   2. Key user in-app — custom fields, Adapt UI, custom logic in a released BAdI, custom CDS
      views/analytical queries, form templates, flexible workflow.
   3. Developer extensibility (ABAP Cloud) — real code on in-tenant data, RAP services, developer
      BAdI implementations, application jobs. Requires ADT + developer access to the dev system.
   4. Side-by-side (BTP) — external data, independent lifecycle/scaling, non-ABAP stack, UIs for
      non-SAP users, complex orchestration, **or when a required object is not released for
      in-tenant developer use** (e.g. the CDS view/object has no C1 contract) — reach the data via
      a released OData/SOAP API on BTP instead. Uses released APIs + events only. When you build
      side-by-side, follow the authoritative developer docs: CAP https://cap.cloud.sap/docs/,
      SAP UI5 https://ui5.sap.com/, Node.js https://nodejs.org/docs/latest/api/, JavaScript
      https://www.w3schools.com/js/default.asp, npm https://www.npmjs.com/package/npm, and
      SAP Community https://community.sap.com/.
5. Rate every option on **Feasibility (1-5) / Approach fit (1-5) / Cost** (rating model comes
   back from `extensibility_advisor`). Key user and developer extensibility are included in the
   subscription; **every BTP service has consumption cost** — link its SAP Discovery Center
   page (https://discovery-center.cloud.sap/viewServices) and name the pricing metric.
6. Consult `query_experience` and cite the EXP-ids that informed the decision.
7. Deliver a decision document:
   - Decision summary (mode(s) + one-paragraph rationale)
   - Capability-to-mode table (mixed is normal)
   - **Custom-Object Naming Contract** — a table of every custom object the solution will CREATE
     (custom field, CDS view, CBO/RAP BO, table, CAP/UI5 artifact) with a proposed namespaced
     technical name (`YY1_…` key-user, `Z…`/`Y…` developer). The human locks these names at
     solution approval and the build uses them verbatim.
   - **Option rating table** (Feasibility / Approach / Cost, incl. runners-up and why they lost)
   - Object inventory with release verdicts — released APIs/CDS views ONLY. Read
     `check_object_release_state` correctly: `NOT_AVAILABLE` blocks (redesign); `LIKELY_RELEASED`
     (seed hit or a standard released-VDM name — I_/C_/A_ CDS views) counts as released — record
     it and cite the source, do NOT call it "not verified". Link **CDS views** to the SAP Help
     Released CDS Views list
     (https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/5418de55938d1d22e10000000a44147b.html),
     **BAdIs** to the List of BAdIs
     (https://help.sap.com/docs/SAP_S4HANA_CLOUD/a630d57fc5004c6383e7a81efee7a8bb/7364d84e76e745df91f1413339a7e293.html),
     and **APIs** to the SAP Business Accelerator Hub (https://api.sap.com/products/SAPS4HANACloud/apis/all).
   - Upgrade-safety statement
   - Effort/skill implications (key user = functional, developer = RAP on Eclipse ADT, S×S = BTP dev)
   - Tenant verification checklist

## Red lines (auto-reject in any proposal)

- Any BAPI, function module call, user exit, enhancement point, or unreleased table access.
- "We'll wrap it in an RFC" — impossible in Public Cloud.
- Key-user artifacts for logic that clearly needs versioned, testable code (put it in developer mode).
- Side-by-side for something a released BAdI does in 20 lines (unnecessary TCO).
