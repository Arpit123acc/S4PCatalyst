# Steering — Extensibility Taxonomy (S/4HANA Cloud Public Edition)

The authoritative model the **Extensibility Architect** uses to choose an approach, and the field
contract every downstream agent (TD, Developer Guide, Test Pack) branches on. Clean core is a hard
constraint for all of it. This complements the decision ladder in `s4pc-extensibility-decision`.

## 1. The three extensibility types (building blocks)

- **Key User (in-app, `key_user`)** — configuration in the Fiori launchpad by a Key User; no ABAP,
  no transport, no external tooling. Custom Fields & Logic (FLCL, restricted-ABAP BAdI logic),
  Custom Business Objects (CBO), Custom Analytical Queries, UI/Page adaptation, Output Parameter
  Determination, Flexible Workflow, Communication Arrangements. **Included in subscription.**
  *Cannot* do arbitrary table access, non-released FM calls, complex stateful orchestration, or
  arbitrary DB schema. Recommend for field/UI/simple-validation/output-config, no external system.
- **Developer (ABAP Cloud / RAP, `developer`)** — custom ABAP in-tenant via ADT, **C1-released
  objects only** (ATC Cloud-readiness). RAP Business Objects + Fiori Elements, Custom CDS views on
  released views, released-BAdI implementations, custom OData/SOAP APIs, Application Jobs, released
  output framework. In-system transports (not BTP). **Included in subscription; requires the
  Developer Extensibility entitlement on the tenant → flag `dev_entitlement_required`.** Recommend
  when logic/UI/CDS exceeds Key User capability or a released BAdI must be implemented.
- **Side-by-Side (BTP, `side_by_side`)** — apps/services/automation on SAP BTP, outside S/4HANA,
  talking to it via **released APIs + business events only**. CAP (Node/Java) + Fiori/UI5,
  Integration Suite (CPI/iFlows), SAP Build Process Automation (SBPA), SAP Build Apps, Event Mesh /
  Advanced Event Mesh, AI Core / Generative AI Hub, Work Zone, DMS. **Every BTP service has
  consumption cost — link its SAP Discovery Center page + pricing metric.** Recommend for external
  integration, cross-system orchestration, RPA/workflow beyond S/4, AI/ML, non-SAP UIs, high-volume
  async/event-driven work, or when a needed object has no C1 contract for in-tenant developer use.

All three are clean-core when the rules above are honoured. **Never** classical ABAP (SE80/CMOD/
classic BAdI/direct table access/CALL TRANSACTION/SUBMIT) — not available on Public Cloud.

## 2. Approach selection hierarchy (stop at the first rung that fits per capability, then combine)

1. Fully met by **Key User** tools alone? → `key_user`.
2. Needs custom ABAP logic, RAP UI, or custom CDS **inside** S/4HANA? → include `developer`.
3. Needs external integration / BTP services / event-driven async / a UI or app **outside** S/4HANA
   Fiori? → include `side_by_side`.
4. **Combine** per capability → one of the seven approaches:
   `KEY_USER`, `DEVELOPER`, `SIDE_BY_SIDE`, `KU_DEV`, `KU_SXS`, `DEV_SXS`, `KU_DEV_SXS`.

Decompose the FD into capabilities and pick the mode **per capability** (`mode_split`); the overall
`extensibility_approach` is the union of the modes present. Mixed is normal — never force one mode
onto a requirement that spans several, and never over-engineer (no Developer for a pure field add,
no BTP for pure in-system logic).

## 3. Field contract (Solution Designer sets these; all downstream agents branch on them)

| Field | Values | Notes |
|---|---|---|
| `extensibility_approach` | `KEY_USER`,`DEVELOPER`,`SIDE_BY_SIDE`,`KU_DEV`,`KU_SXS`,`DEV_SXS`,`KU_DEV_SXS` | Union of modes in `mode_split`. Primary downstream selector. |
| `extensibility_mode` | `key_user`,`developer`,`side_by_side`,`mixed` | Kept for back-compat/UI; `mixed` whenever ≥2 modes. |
| `mode_split` | `[{capability, mode}]` | Per-capability decomposition (source of truth). |
| `key_user_components` | `FLCL`,`CBO`,`OUTPUT_MGMT`,`WORKFLOW`,`UI_ADAPT`,`ANALYTICS`,`COMM_ARRANGEMENT` | Only if KU in scope. |
| `developer_components` | `RAP_BO`,`CDS_VIEW`,`BADI_IMPL`,`CUSTOM_API`,`APP_JOB` | Only if Developer in scope. |
| `btp_components` | `CAP_APP`,`INTEGRATION_SUITE`,`SBPA`,`EVENT_MESH`,`AI_CORE`,`BUILD_APPS`,`WORK_ZONE` | Only if SxS in scope. |
| `clean_core_validated` | `true`/`false` | Must be `true` for Public Cloud; flag any component that risks clean core. |
| `transport_type` | `IN_SYSTEM`,`BTP_MTAR`,`BOTH`,`NONE` | KU/Dev → IN_SYSTEM; SxS → BTP_MTAR; mixed with both → BOTH; KU-only config → NONE/IN_SYSTEM. |
| `dev_entitlement_required` | `true`/`false` | `true` whenever `developer` is in scope — a TD pre-condition. |
| `btp_services_required` | array of BTP service names | Drives the TD infrastructure checklist; each links a Discovery Center page + pricing metric. |
| `ricefw_type` | `R`,`I`,`C`,`E`,`F`,`W` | Approach must align (a Report rarely needs SxS; an Interface almost always needs SxS or ≥Developer). |

## 4. Downstream propagation (every agent aligns to `extensibility_approach`)

| Approach | TD Generator | Developer Guide | Test Pack |
|---|---|---|---|
| `KEY_USER` | Config steps only, no ABAP sections (Parameter Table / FLCL / CBO). | Config guide only. | Fiori config validation, field visibility, output rendering. |
| `DEVELOPER` | Full ABAP Cloud TD: RAP/BDEF, BAdI, CDS, transport. | RAP/ABAP Cloud guide, ATC, ADT, released APIs. | ABAP Unit, OData service tests, BAdI trigger tests. |
| `SIDE_BY_SIDE` | BTP TD: CAP project, CDS model, service bindings, destinations, CF/MTA deploy. | CAP/BTP guide: MTA, CF CLI, cockpit. | CAP integration, API-contract, event-flow tests. |
| `KU_DEV` | Both sections, clearly separated. | Config guide + ABAP Cloud guide. | Config tests + ABAP unit/integration. |
| `KU_SXS` | Config + BTP architecture (Comm Arrangement + Destination). | Config guide + CAP/BTP guide. | Config tests + BTP integration. |
| `DEV_SXS` | ABAP Cloud specs + BTP architecture; the S/4↔BTP API contract defined. | ABAP Cloud + CAP/BTP guide. | ABAP tests + BTP integration + API-contract tests. |
| `KU_DEV_SXS` | All three sections, layered Config → ABAP Cloud → BTP. | All three guides, sequenced KU → Dev → BTP. | All three test layers + an end-to-end scenario test. |

## 5. Guardrails
- Never classical ABAP; never Developer for what Key User can do; never Side-by-Side for pure
  in-system logic. Always keep `clean_core_validated = true`.
- Validate the approach aligns with `ricefw_type`; flag `dev_entitlement_required` as a TD
  pre-condition whenever Developer is in scope.
