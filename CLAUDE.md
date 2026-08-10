# S/4HANA Cloud Public Edition — Agentic Delivery Workspace

This workspace delivers extensions for **S/4HANA Cloud, Public Edition** (3-system landscape).
Clean core is not a preference here — it is a hard platform constraint.

## Non-negotiable platform rules

- **No classical ABAP.** No BAPIs, no function modules (except the rare C1-released ones), no
  `SUBMIT`/`CALL TRANSACTION`, no dynpros/module pools, no FORM/PERFORM, no classical reports,
  no SAPscript/Smart Forms, no user exits, no implicit/explicit enhancements.
- **Only released objects.** Every API, BAdI, CDS view, or class referenced in a design or code
  must carry a release contract (C1 "Use in Cloud Development" for developer extensibility) or be
  listed in the tenant's Custom Logic app (key user). Nothing else compiles or survives upgrades.
- **Three extensibility modes only:**
  1. **Key user (in-app):** Custom Fields and Logic, Adapt UI, Custom CDS Views, Custom
     Analytical Queries, form templates, flexible workflow.
  2. **Developer (ABAP Cloud / Embedded Steampunk):** RAP, CDS, released BAdIs, Application
     Jobs, ABAP language version "ABAP for Cloud Development", via ADT.
  3. **Side-by-side (BTP):** CAP or ABAP Environment consuming released OData/SOAP APIs and
     business events; integrations via SAP Integration Suite.
- **Namespace:** all custom objects use the tenant customer namespace conventions
  (`Z*`/`Y*` per project standard, `YY1_` for key-user artifacts).

## Mandatory agent workflow gates (anti-hallucination)

The `s4pc` MCP server (offline-first, zero-dependency, see [mcp-server/](mcp-server/)) is the
governance layer. Use it as follows — these are gates, not suggestions:

1. **Never name an SAP object from memory.** Before referencing any API/BAdI/table/CDS view, call
   `check_object_release_state`. Read the verdict precisely: `NOT_AVAILABLE` (BAPI, classical
   table, enhancement point) is the only blocker → redesign. `LIKELY_RELEASED` (seed hit or a
   standard released name — **CDS views** I_/C_/A_/R_/E_, or **APIs** API_*/*_SRV/SOAP *_IN/_OUT/
   events CE_*) is **released** → record it as such and add a "confirm on the Released CDS Views
   list / SAP Business Accelerator Hub / ADT" note. `NOT_VERIFIED` means the offline server can't
   confirm it — look it up on the authoritative list for its type and label it "to verify in
   tenant"; it does **not** mean "unreleased". Never downgrade a standard released CDS view (e.g.
   `I_MaterialStock`) or a released API (e.g. `API_CLFN_PRODUCT_SRV`) to "not verified" —
   finalise APIs from the SAP Business Accelerator Hub (api.sap.com).
2. **Lint every ABAP snippet** with `abap_cloud_lint` before showing it. A `FAIL` verdict means
   redesign, not apology text.
3. **Every extensibility decision** goes through `extensibility_advisor` and is documented with
   the decision path and the objects' release verdicts.
4. **Catalog answers are seeds, not truth.** Always repeat the authoritative source in deliverables:
   SAP Business Accelerator Hub (api.sap.com), the tenant's Custom Logic app, ADT Released Objects.
5. In live mode, ground designs with `odata_get_metadata` / `odata_query` (read-only, allowlisted)
   instead of assuming field names.
6. **Human-in-the-loop is mandatory.** Pipeline runs stop at three checkpoints (solution
   approval, code approval, acceptance) and wait for an explicit developer decision — never
   assume approval, never continue on silence. Record every decision in `run.json.human_approvals`.
7. **Mixed extensibility is normal.** Decompose the FD into capabilities and choose the mode
   per capability (key user / developer / side-by-side); don't force one mode onto a
   requirement that spans several. When the solution CREATES custom objects, the proposal includes
   a **Custom-Object Naming Contract** (each object's namespaced technical name); the human locks
   these at solution approval, and the **build — which runs first, from the approved proposal (the
   TD is written afterward, as documentation of the built + tested solution) — uses them verbatim**.
8. **Rate every solution option** on Feasibility / Approach fit / Cost. Key user + developer
   extensibility are included in the subscription; every BTP service has consumption cost —
   link its SAP Discovery Center page and name the pricing metric.
9. **Cite authoritative sources** (from `get_reference_links`): released **CDS views** → SAP Help
   Released CDS Views list
   (help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/5418de55938d1d22e10000000a44147b.html);
   **BAdIs** → SAP Help List of BAdIs
   (help.sap.com/docs/SAP_S4HANA_CLOUD/a630d57fc5004c6383e7a81efee7a8bb/7364d84e76e745df91f1413339a7e293.html);
   **APIs** → SAP Business Accelerator Hub (api.sap.com); **configuration objects / released
   applications / any other released objects** → S/4HANA Cloud docs root
   (help.sap.com/docs/SAP_S4HANA_CLOUD); BTP services + pricing → SAP Discovery Center; standard
   apps → Fiori Apps Library; **developer docs** for side-by-side / UI code → CAP
   (cap.cloud.sap/docs), SAP UI5 (ui5.sap.com), Node.js (nodejs.org/docs/latest/api), JavaScript
   (w3schools.com/js), npm (npmjs.com), SAP Community (community.sap.com). Solutions may contain
   **released** APIs and CDS views only — never suggest unreleased objects.
10. **Use the experience database:** `query_experience` at intake/proposal (cite EXP-ids),
    `record_experience` at package time. Fixed domain facts: Adobe Forms only (no Smart Forms);
    key user = released BAdIs + custom fields + Adapt UI + analytical queries + Flexible
    Workflow; developer = RAP on Eclipse ADT; side-by-side = SAP BTP (CAP/UI5, Integration
    Suite, SBPA).

## Deliverable standards

- FD/TD documents state the extensibility mode, the released objects used (with verdicts), and an
  explicit "Tenant verification checklist" section.
- Generated ABAP follows RAP patterns, OO-only, English-only, no hard-coding, pretty-printed.
- Every pipeline run ends with: lint verdict, release-state table, open verification items.

## Security & connectivity

- No LLM API keys anywhere — Claude Code is the runtime.
- SAP credentials only via environment variables (communication user), never in files or logs.
- MCP server default mode is `offline`; live mode is read-only, allowlisted, rate-limited, audited
  (see `mcp-server/logs/audit.jsonl`).

## Steering (persistent project context)

The following steering documents are always in scope — read them for product intent, the
technology constraints, and how the project is laid out:

@.claude/steering/product.md
@.claude/steering/tech.md
@.claude/steering/structure.md
