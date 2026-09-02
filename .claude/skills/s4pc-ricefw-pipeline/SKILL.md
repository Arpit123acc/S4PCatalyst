---
name: s4pc-ricefw-pipeline
description: Delivers a RICEFW object end-to-end through a governed, role-based 12-step clean-core pipeline for S/4HANA Cloud Public Edition — the Delivery Lead orchestrates specialist roles (architect, developer, reviewer, tester, challenger) from a Functional Design (or requirement) to peer-reviewed, lint-passed code, with a human approval gate at every checkpoint. Use when the user wants to build an enhancement, report, interface, conversion, form, or workflow object end-to-end, or says "run the pipeline on <FD file>".
---

# S/4HANA Public Cloud RICEFW Pipeline (12 steps, human-in-the-loop)

A rigorous clean-core RICEFW delivery pipeline for S/4HANA Cloud Public Edition — every step is
clean-core and the developer stays in control: the pipeline NEVER moves past a checkpoint without
an explicit human decision.

## Input

The developer provides ONE of:
- **An FD document** — a file path (typically `input/<name>.md`, uploaded via the webapp's
  FD Intake page or dropped there manually). Read it fully before starting.
- **A raw requirement** in the chat. If it is too thin to design from, run the
  `s4pc-fd-creation` skill first (its open-questions step is itself human-in-the-loop).

Derive `<OBJECT-ID>` from the FD (or propose one like `MM-EXT-0002` and confirm at
Checkpoint 1). Record the FD path in `run.json` as `"fd_source"`.

## Clean-core solution shaping (step 2 — the heart of the pipeline)

Decompose the FD into **capabilities** (field, UI, validation/derivation logic, data
access/reporting, integration, scheduling/batch, output, workflow). For EACH capability pick
the extensibility mode — the decision comes entirely from the functional requirement, never
from habit:

- **Key user (in-app):** Custom Fields and Logic (released BAdIs), Adapt UI, Custom CDS
  Views, Custom Analytical Queries, form templates, flexible workflow.
- **Developer (ABAP Cloud / Embedded Steampunk):** RAP + CDS + Fiori Elements, developer BAdI
  implementations, Application Jobs — released (C1) objects only.
- **Side-by-side (BTP):** CAP / ABAP Environment / Integration Suite over released APIs +
  business events — external data, independent lifecycle, non-SAP consumers.
- **MIXED is normal:** one FD frequently lands in 2–3 modes (e.g. key-user field + developer
  RAP service + side-by-side iFlow). Present the split per capability; never force a single
  mode onto a requirement that spans modes. The overall approach is one of seven —
  `KEY_USER / DEVELOPER / SIDE_BY_SIDE / KU_DEV / KU_SXS / DEV_SXS / KU_DEV_SXS` — derived from the
  modes present. Set the **extensibility field contract** (`extensibility_approach`, the
  `key_user_/developer_/btp_components` arrays, `transport_type`, `dev_entitlement_required`,
  `btp_services_required`, `clean_core_validated`) per `steering/extensibility-taxonomy.md` §3;
  build, tests, and the TD all branch on it (§4).

Use `extensibility_advisor` for the first pass, then apply judgment. Every object the
proposal touches gets `check_object_release_state` — a NOT_AVAILABLE object means redesign
with the returned alternative BEFORE the proposal is shown to the human.

**Client-mandated mode (a valid business case).** If the FD carries a **Client Constraints** section
that mandates an extensibility mode (e.g. "side-by-side / BTP only") — even when it is not the
clean-core best fit — keep your **honest** technical recommendation AND present the mandated mode as a
**build-ready** option (full pros/cons, released objects, and for BTP the SAP Discovery Center link +
pricing metric). Flag the mandated option (`mandated: true`) so the human sees the trade-off; the
developer may select it at CP1 over the recommendation. The **only** hard gate is feasibility: if the
mandated mode genuinely cannot be built with released artifacts, say so explicitly and mark it
infeasible (`feasible: false` + the concrete blocker) — a client mandate never overrides platform
reality (no BAPIs, no unreleased objects, no classical ABAP). Give **each build-viable option its own
Custom-Object Naming Contract** so a review-time override to a different mode still builds cleanly. A
non-recommended selection at CP1 is recorded in `run.json.mode_override` (original recommendation vs.
selected mode, `cost_disclosure_required`).

**Domain facts (fixed — never contradict these):** Forms are **Adobe Forms only** (Maintain
Form Templates; no Smart Forms/SAPscript). Key user = released BAdIs (Custom Logic app), custom
fields, Adapt UI, Custom CDS Views/Analytical Queries, Flexible Workflow. Developer = **RAP on
Eclipse ADT** (git-based software components). Side-by-side = **SAP BTP** (CAP+UI5, Integration
Suite, Event Mesh, SBPA). Workflow ladder: Flexible Workflow (key user) → SBPA (BTP). Report
ladder: Custom Analytical Query (key user) → RAP + Fiori Elements (developer) → CAP+UI5 on BTP.

**Rate every option — Feasibility / Approach / Cost.** The solution proposal MUST contain a
rating table for the options considered (including runners-up):

| Option | Modes | Feasibility (1-5) | Approach fit (1-5) | Cost | Verdict |
|---|---|---|---|---|---|

Feasibility = buildable with released/covered artifacts (verified, not assumed). Approach fit =
clean-core alignment, upgrade-safety, team skillset, lifecycle. Cost = the mode's cost class:
key user and developer are **included in the subscription** (effort only); **every BTP service
costs money** — link its SAP Discovery Center page and name the pricing metric (e.g. CPI
message volume, SBPA process instances). Recommend the winner and say why the others lost.

**Experience database:** call `query_experience` at intake and before the proposal; cite the
EXP-ids you applied. At the package step, `record_experience` for anything non-obvious the run
taught (source = the run id) — this is how the pipeline compounds.

**Digital Brain — client document RAG (`search_brain`, Layer 4).** Beyond the catalog/graph/experience
layers, the brain holds the harvested **client documents** (SharePoint FDs, TDs, standards, prior
specs) as Bedrock+FAISS vectors. Use it at the steps where a client document is the best source of
truth — it degrades gracefully (a helpful message) when the brain host/index is absent, so always
call it and continue if offline:
- **Intake (step 1):** `search_brain(query=<requirement>)` for prior client context that informs scope.
- **Solution proposal (step 2):** `search_brain(query=<capability + APIs/events/BAdIs>,
  deliverable_type="functional_design")` to pull the released objects the client's OWN FDs reference,
  and `search_brain(deliverable_type="technical_design")` for a proven solution shape from a past TD.
- **Object inventory (step 3):** `search_brain(deliverable_type="functional_design")` to see whether a
  client FD already names the API/event/BAdI — often resolves a `NOT_VERIFIED` faster; still confirm
  the name with `check_object_release_state`.
- **Build (step 6):** `search_brain(deliverable_type="technical_design")` to reuse a proven
  implementation pattern from a past TD.
- **Technical design (step 10):** `search_brain(deliverable_type="technical_design")` to reuse a past
  TD's section structure and depth (never copy client-specific content).
Filters: `deliverable_type`, `source_system` (`sharepoint`/`sap_bpd`/`sap_scope_catalog`), `phase`
(`Discover`/`Prepare`/`Explore`/`Realize`/`Deploy`/`Run`), `agent_role`, `dedup` (default true —
over-fetches so a narrow filter still yields). The corpus (~49k chunks / ~2.5k docs) is tagged with
these `deliverable_type` values — target the family that fits the step:
- **Requirement / API / event / BAdI (FD family):** `functional_design`, `business_process_design`,
  `fit_to_standard`, `configuration`, `integration_iflow`, `wricef_inventory`.
- **Solution shape / implementation (TD family):** `technical_design`, `architecture_design`,
  `configuration`, `integration_iflow`, `ui_code`.
- **Also present:** `reference_document`, `data_migration`, `test_cases`, `cutover_plan`,
  `project_plan`, `raci_matrix`, `discovery_assessment`, `kdd`, `statement_of_work`.
Filtering is applied **after** the vector search, so a small slice (e.g. `technical_design` ≈ 29 chunks)
can return little — when it does, **use the unfiltered results** (they are ranked by relevance and a
same-topic doc still ranks high). Always cite the source document, and keep the released-object rules: a
brain hit is a lead to verify, never a substitute for `check_object_release_state`.

**Citations:** released **CDS views** → the SAP Help **Released CDS Views** list
(`https://help.sap.com/docs/SAP_S4HANA_CLOUD/c0c54048d35849128be8e872df5bea6d/5418de55938d1d22e10000000a44147b.html`)
+ ADT Released Objects / View Browser; **BAdIs** → the SAP Help **List of BAdIs**
(`https://help.sap.com/docs/SAP_S4HANA_CLOUD/a630d57fc5004c6383e7a81efee7a8bb/7364d84e76e745df91f1413339a7e293.html`)
+ Custom Logic app; **APIs** → SAP Business Accelerator Hub (api.sap.com); **any other released /
configuration / application object** → the S/4HANA Cloud docs root
(`https://help.sap.com/docs/SAP_S4HANA_CLOUD`); BTP services + pricing → SAP Discovery Center;
standard-app checks → Fiori Apps Library. `get_reference_links` returns these canonical URLs —
every deliverable's "Sources & verification" section uses them.

## Orchestration (roles)

You act as the **delivery-lead** orchestrator and own `run.json`, the three gates, and the human
checkpoints. Each step maps to a specialist **role** below.

**Execution mode — important:**
- **Headless / automated run (the webapp's "Run pipeline"):** the webapp spawns a separate
  `claude -p` process per pipeline **phase** (A → B → C → D → E), each with a scoped prompt
  containing only what that phase needs. If you receive a prompt that starts with
  `HEADLESS PIPELINE — Phase`, follow **that prompt's instructions directly** — do NOT read this
  SKILL.md (the phase prompt replaces it for that phase). Never spawn sub-agents with `Task`
  in a headless run. Update `run.json` after every step so the Workflow Explorer shows live
  progress.
- **Interactive session only:** you *may* delegate a step to the matching specialist agent in
  `.claude/agents/` via `Task` if you want isolated context — optional, never in headless.

**Step → role → deliverable:**

| Step | Role | Writes |
|---|---|---|
| 1 Intake | delivery-lead | 01-discovery.md |
| 2 Solution Proposal · 3 Object Inventory | extensibility-architect | 02-solution-proposal.md, 03-release-verdicts.md |
| 4 GATE 1 Release Check | clean-core-reviewer | gate verdict → run.json |
| 5 ✋ CP1 Solution approval | delivery-lead (human checkpoint) | checkpoint_request |
| 6 Build | developer | 06-code.md |
| 7 GATE 2 Code Review + ✋ CP2 Code approval | clean-core-reviewer → human checkpoint | gate verdict, checkpoint_request |
| 8 Lint | clean-core-reviewer (runs `abap_cloud_lint`) | 07-lint-report.md |
| 9 Unit Test Design | test-agent | 08-unit-tests.md |
| 10 FD Analysis + Technical Design (documentation) | extensibility-architect + developer | 04-fd-analysis.md, 05-technical-design.md |
| 11 GATE 3 Peer Review + Challenger + ✋ CP3 | clean-core-reviewer (incl. security) + challenger → human checkpoint | 09-review.md, checkpoint_request |
| 12 Package | delivery-lead | 10-package-summary.md |

The **developer** role builds per the mode(s) chosen at step 2 — developer extensibility
(RAP/CDS/BAdI/Fiori), key-user (Custom Fields & Logic, Adapt UI, CDS/analytics, forms, flexible
workflow), and/or side-by-side on BTP (CAP/ABAP Environment, Integration Suite); a mixed FD uses
several. Every role obeys clean core and verifies objects with the `s4pc` MCP tools.

**Graceful fallback:** if the `Task` tool is unavailable (a restricted session or allowlist),
execute each step inline yourself *in that agent's role* — the pipeline must still complete, with
identical deliverables. Delegation is how the work is done, not a hard dependency.

## Pipeline steps

Steps 4, 7, 11 are agent gates; ✋ marks the human checkpoints. At a checkpoint: present the
material compactly, ask for a decision (approve / adjust / reject — use the question tool if
available, otherwise ask in chat and STOP), and record it in `run.json.human_approvals`. Never
assume approval; never continue on silence.

1. **Intake** — read the FD, restate scope in ≤10 lines, classify RICEFW type, list open
   questions (unanswered questions go to the human at Checkpoint 1, not silently answered).
2. **Solution proposal** — capability decomposition + per-capability mode (mixed allowed),
   object inventory (released APIs/CDS/BAdIs only, each with its Business Accelerator Hub
   link), the **Feasibility/Approach/Cost rating table** for all options considered (BTP costs
   via Discovery Center), applied EXP-ids, effort/skill implications, upgrade-safety statement.
   **Custom-Object Naming Contract (MANDATORY whenever the solution CREATES objects):** a table of
   every custom object to be created — custom fields (key user), custom CDS views / RAP objects /
   tables / services (developer), CBOs — with its **exact intended technical name + namespace** and
   who creates it. This is the contract the Build step (6) uses **verbatim** and the developer
   creates in-tenant, so the code never guesses a name. Developer objects (`Z…`/`Y…`) are fully
   name-controlled; a key-user custom field's OData property name (`YY1_<label>_<suffix>`) is the
   intended name to build against and MUST be verified in `$metadata` after creation (a step-13
   checklist item).
3. **Object inventory verdicts** — establish the release state of EVERY object (API, CDS view,
   BAdI, table) via `check_object_release_state`, or the read-once catalog method (*Headless* §3)
   when the MCP is blocked. Read the verdict correctly — a seed miss is not a failure:
   - `NOT_AVAILABLE` → the object is categorically unusable (BAPI, classical table, enhancement
     point, Smart Form). Redesign with the returned alternative. **Only this verdict blocks.**
   - `LIKELY_RELEASED` + `evidence: catalog_hit` → an exact match in `catalog.db`. Record it as
     **released** and add a one-line "confirm on the Released CDS Views list / SAP Business
     Accelerator Hub / ADT" note — do NOT write it up as "not verified".
   - `LIKELY_RELEASED` + `evidence: naming_heuristic_only` → the name matches SAP's released
     convention (**CDS** I_/C_/A_/R_/E_, **APIs** API_*, *_SRV, SOAP *_IN/*_OUT, events CE_*) but
     **nothing in the catalog backs it**, so a fabricated name scores identically to a real one.
     Use it as a design placeholder, then: (a) cross-check with `search_released_apis` on the
     *business keywords* (not the name) / `semantic_search` / `get_object_graph`; (b) write it as
     "name unconfirmed — verify on api.sap.com / Released CDS Views list", **never** as released;
     (c) list it in the Tenant verification checklist. If the cross-check finds nothing, say the
     object may not exist and raise a Major finding.
   - **Always carry `evidence` into the Evidence column of `03-release-verdicts.md`.** A verdict
     without its evidence is not reviewable.
   - `NOT_VERIFIED` → genuinely unknown to this offline server. Do not guess: look it up on the
     authoritative list for its object type, then record it as released (with the citation) or
     as a tenant-verification item — never as "unreleased".
   For **CDS views** confirm against the SAP Help **Released CDS Views** list; for **BAdIs** the
   **List of BAdIs**; for **APIs** the **SAP Business Accelerator Hub**
   (`https://api.sap.com/products/SAPS4HANACloud/apis/all`) — this is the authoritative public
   list, so **finalise every API from it**: cite each API's overview page
   (`https://api.sap.com/api/<API_NAME>/overview`) and confirm its communication scenario. The
   `s4pc` MCP tools give the release verdict — **cite** these authoritative URLs in the deliverable;
   do **not** use `WebFetch`/`WebSearch` in a headless run (they are disabled). In live mode,
   `odata_get_metadata` is ground truth.
4. **GATE 1: Release check** — the gate FAILS only on a `NOT_AVAILABLE` object (→ redesign).
   `LIKELY_RELEASED` and `NOT_VERIFIED` objects PASS the gate but each gets a line in the
   deliverable's **Tenant verification checklist** with its authoritative source URL. Never
   report a `catalog_hit` standard CDS view (e.g. `I_MaterialStock`, `I_Product`, `I_SalesOrder`) as
   "not verified / not released" — that is the mistake this gate exists to prevent. The mirror-image
   mistake is just as bad: an object with `evidence: naming_heuristic_only` written up as
   "released" without a keyword/semantic cross-check is a **gate defect** — record a Major finding
   and correct the wording to "name unconfirmed".
5. ✋ **CHECKPOINT 1 — Solution approval.** Show: proposal table (capability → mode → objects →
   verdicts), the **Custom-Object Naming Contract** (exact names to be created), open questions,
   alternatives considered. The human approves the approach **and the object names** (possibly
   editing the mode split or the names) before ANY code work starts — the approved names are then
   fixed for Build. In `run.json.checkpoint_request`, write **`naming_contract`**: an array of
   `{id, object, type, created_in, name}` (name = your suggested technical name, e.g. `YY1_…`
   key-user / `Z…` developer). The webapp renders an editable name field per object; the developer
   edits + locks them on approval, and Build (step 6) uses the locked names verbatim.
6. **Build** — generate the code/artifacts **directly from the approved solution proposal**
   (there is NO prior TD; this workflow builds first and documents the design afterward, step 10).
   **Use the exact object names from the approved Custom-Object Naming Contract — never invent or
   guess a custom field / CDS / RAP / CBO / table name;** the code and the in-tenant objects the
   developer creates must share the same names (this is what keeps a mixed solution consistent).
   ABAP-for-Cloud / RAP / CDS for developer mode; Custom Fields & Logic etc. for key user; CAP +
   UI5 for side-by-side. For side-by-side, **READ (WebFetch) the official developer docs that match
   the object type being built** (WebFetch is auto-enabled for side-by-side runs) — via
   `get_reference_links` → `fetch_docs_by_object`: CAP/CAPM → CAP (cap.cloud.sap), Node.js
   (nodejs.org), JavaScript; UI5/Fiori → UI5 (ui5.sap.com), HTML, CSS, JavaScript; both → the union.
   For npm, fetch the JSON registry `registry.npmjs.org/<package>` (not the npmjs.com web page — it
   blocks bots). SAP Community (community.sap.com) is cite-only (anti-bot) — link it, don't fetch it.
   Ground the code in the fetched pages; if a fetch fails, cite the URL for manual verification and
   continue. Write `06-code.md`.
7. **GATE 2: Code review** — clean-core + security review of the generated code (released objects
   only, no classical ABAP; authorisations, secret hygiene, input validation); verdict
   SHIP/FIX/REDESIGN. Then ✋ **CHECKPOINT 2 — Code approval.** In `run.json.checkpoint_request`,
   write **`code_files`**: an array of `{id, file, summary, material}` — **one entry per file / code
   section you built** (`file` = the file or unit name, `summary` = one line on what it contains,
   `material` = the deliverable to open, e.g. `06-code.md`). The webapp renders a **per-file comment
   box** so the developer can request a change against each file individually. On resume, the
   decision carries those per-file comments (plus any overall note) — **apply each file's requested
   change to that specific file**, then re-review. If the developer approves with no comments,
   proceed unchanged.
8. **Lint** — `abap_cloud_lint` on every ABAP artifact; FAIL → fix and re-lint (max 3, then
   escalate to the human instead of looping).
9. **Unit test design** — ABAP Unit / test-mode scenarios incl. negative tests, mapped to ACs.
10. **FD analysis + Technical design (documentation)** — now that the solution is built and tested,
    produce the TD **documenting the delivered solution** (mode-specific per capability — key user:
    BAdI + custom-field spec; developer: RAP artifacts; side-by-side: BTP service + comm
    arrangements), reflecting what was actually built. Write `04-fd-analysis.md`, `05-technical-design.md`.
    The TD is a **client deliverable handed over for sign-off** — write it to be shared as-is:
    self-contained (never "see the FD"), no internal jargon (no phases/gates/agents/run ids outside the
    Document Control table), every acronym expanded on first use, no `TBD`/empty cells, and every
    section present. It opens with **Document Control** and a **Table of Contents** (anchor links to
    every numbered section, including one line per custom object in the object-specification section),
    and closes with **Assumptions & Dependencies**, **Glossary & Abbreviations**, **References**
    (canonical URLs from `get_reference_links`), **Revision History** and an **Approval & Sign-off**
    table with blank Name/Signature/Date rows for the client and the delivery leads.
11. **GATE 3: Peer review + challenger** — full checklist over the code, tests AND the TD; verdict SHIP/FIX/REDESIGN.
12. **Package** — deliverables + deployment guide + tenant verification checklist + at least
    one `record_experience` call if the run taught anything non-obvious, then
    ✋ **CHECKPOINT 3 — Acceptance.** Human accepts the package or sends specific items back
    (record rework rounds in `run.json`).

**Gate discipline (independent review).** At GATE 1, 2 and 3, do not rubber-stamp your own work.
Re-open the actual deliverable with fresh eyes — as a reviewer who did not write it and does not
trust the prior reasoning — and re-derive the verdict yourself: recompute each release verdict from
the catalogs/naming rules, re-check every ABAP snippet against the platform rules, and actively
hunt for the failure the gate guards against (an unreleased object at G1; an upgrade-unsafe or
clean-core-violating design at G2; a defect or wrong extensibility choice at G3). Record the
verdict as SHIP / FIX / REDESIGN with the specific findings that justify it; a bare "PASS" with no
evidence of a real re-check is itself a gate failure.

## Conditional · Deploy to BTP (only when a side-by-side capability exists)

Add these steps to `run.json` **whenever the mode split includes a side-by-side (BTP) capability** —
this includes a **MIXED** approach (`DEV_SXS` / `KU_SXS` / `KU_DEV_SXS`), where the developer and/or
key-user objects are built **in-tenant** and the CAP/UI5 part deploys to BTP: that solution IS a
14-step run, and step 13's checklist must include those in-tenant developer + key-user objects (see
below). For a pure key-user or developer (in-tenant) solution with no BTP part, do **not** add these
steps — the run stays 12 steps and no deploy stage appears. (SBPA is BTP-cost but produces
configuration handover, not a CAP deploy — it stays 12 steps.) When side-by-side IS chosen, append **two** steps after
Package (step 12) / CP3 acceptance and set the `workflow` label to **exactly**
`RICEFW Pipeline (14 steps, incl. BTP deploy)` — use this exact ASCII string, never invent a
variant or add non-ASCII punctuation (a pure in-tenant run keeps the seeded label
`RICEFW Pipeline (12 steps)`). The agent picks the variant automatically from the mode split; the
developer never chooses it:

The two extra steps are a **fixed contract — never rename them** (a renamed step 13 silently drops the
prerequisite gate). Write them exactly as:
`{"n":13,"name":"BTP Prerequisite Check","agent":"Human","gate":true}` and
`{"n":14,"name":"Deploy to BTP","agent":"Developer","gate":false}` — step 13 is the **human gate**, not a
build step, and `status` is always one of PASS|FAIL|RUNNING|AWAITING_APPROVAL|PENDING|SKIPPED (never "N/A").

**Step 13 · BTP prerequisite check (gate).** A side-by-side app fails at runtime if the in-tenant
objects it depends on are not deployed and active *first*. Before any deploy, enumerate those
dependencies from the design and confirm each is in place in the target tenant:
- **Key-user changes** — custom fields (Custom Fields & Logic), custom CDS views / analytical
  queries the BTP app reads.
- **Communication setup** — the **communication scenario** and an active **communication
  arrangement** for every released API / business event the BTP app consumes.
- **Developer changes** — any in-tenant RAP objects it depends on (custom CDS/tables, RAP
  service/behavior, released BAdI implementations).

This is a **human checkpoint**, and pausing it correctly is a **two-part write — the `run.json`
write is mandatory and is what the webapp reads. Writing only the `.md` leaves the run stuck with an
"awaiting" card and no panel to approve.**

1. **In `output/<ID>/run.json` (MANDATORY — this drives the checklist UI):** set
   `status: "awaiting_approval"`, set step 13 to `AWAITING_APPROVAL`, and set `checkpoint_request` to
   an object with a **`checklist`** array — one string per in-tenant prerequisite, **named exactly
   per the Custom-Object Naming Contract** (each custom field — and verify it appears in `$metadata`;
   the communication scenario + an arrangement for each consumed API/event; each RAP/CDS/CBO/table
   object) — then END the run. Exact shape:
   ```json
   "checkpoint_request": {
     "checkpoint": "CP-Deploy · BTP prerequisites",
     "summary": "<one line: what must be confirmed before deploy>",
     "material": "13-btp-prereq-check.md",
     "checklist": ["<prerequisite 1>", "<prerequisite 2>", "..."]
   }
   ```
   **Never leave step 13 `AWAITING_APPROVAL` without a non-empty `checkpoint_request.checklist`.**
2. **Also** write `13-btp-prereq-check.md` with the same items (human-readable detail) — this is
   secondary; the UI does not read it.

The webapp renders the checklist; the developer ticks each item they have deployed & activated, and
approval is **blocked until all items are ticked** (deploying without them means the BTP code will
not run). Only on that approval does Step 14 proceed, calling `btp_deploy` with
`prereqs_confirmed: true`. (In live mode you may pre-verify checkable items, e.g. `odata_get_metadata`.)

**Step 14 · Deploy to BTP (dev).** Runs **only** if Step 13 is READY:
1. ✋ **CP-Deploy** — explicit human approval to a named **dev/test** space (never on silence;
   production is out of scope — promote via CI/CD).
2. Build + deploy with the `btp_deploy` tool — **OFF by default** (`S4PC_ALLOW_DEPLOY` +
   `guardrails.deploy.allow_deploy`), **dry-run** unless `dry_run=false`, refuses production spaces,
   CF creds only from `CF_*` env vars, and it **requires `prereqs_confirmed: true`** (set only after
   Step 13 = READY) before it will deploy.
3. Write `14-deploy-report.md`: MTA modules, app URLs, smoke-check, promote-to-prod checklist.

A ready-to-fill descriptor is at `mcp-server/templates/mta.yaml` (CAP service + HANA + UI5 + XSUAA +
destination + HTML5 repo host). Set each step's `run.json` status so the run completes cleanly
(`PASS`/`FAIL`, or `NOT_READY`).

## RICEFW type → Public Cloud realization

| Classic type | Public Cloud realization |
|---|---|
| Report | Custom CDS View + Analytical Query (key user) or RAP + Fiori Elements (developer) |
| Interface | Released OData/SOAP APIs + business events + Integration Suite (side-by-side) — never custom RFC/IDoc |
| Conversion | Migrate Your Data app (staging) or released APIs |
| Enhancement | Key-user custom logic (released BAdI) or developer BAdI — never enhancement points/user exits |
| Form | Maintain Form Templates + Output Determination |
| Workflow | Flexible Workflow or SAP Build Process Automation (BTP) |

## Output structure

Write deliverables to `output/<OBJECT-ID>/`:
`01-discovery.md`, `02-solution-proposal.md`, `03-release-verdicts.md`, `04-fd-analysis.md`,
`05-technical-design.md`, `06-code.md`, `07-lint-report.md`, `08-unit-tests.md`, `09-review.md`,
`10-package-summary.md`.

Every document ends with a "Sources & verification" section separating (a) verified facts
(tool-checked) from (b) assumptions to verify in tenant. Never present (b) as (a).

## Run manifest (MANDATORY — powers the Workflow Explorer)

Maintain `output/<OBJECT-ID>/run.json`, updated after EVERY step and checkpoint so a run in
progress (or one waiting on a human) is visible in the Workflow Explorer UI. Schema:

```json
{
  "id": "MM-EXT-0001",
  "title": "PO completeness validation",
  "type": "Enhancement",
  "workflow": "RICEFW Pipeline (12 steps)",
  "fd_source": "input/FD-MM-EXT-0001.md",
  "created": "2026-07-18",
  "fd_name": "FD-MM-EXT-0001",
  "requirement_summary": "Block PO release when the custom priority field is empty.",
  "approved_approach": "Key user: custom field + released BAdI custom logic",
  "objects_used": ["I_PurchaseOrder", "MM_PUR_S4_PO_CHECK", "YY1_Priority_PDH"],
  "summary": "Key-user custom field + BAdI validation; no transport. Reuse the FLCL pattern.",
  "status": "completed",
  "quality_score": 81,
  "gates_passed": "3/3",
  "auto_corrections": 4,
  "extensibility_mode": "key_user",
  "mode_split": [{"capability": "Priority field", "mode": "key_user"}],
  "extensibility_approach": "KEY_USER",
  "key_user_components": ["FLCL"], "developer_components": [], "btp_components": [],
  "clean_core_validated": true, "transport_type": "IN_SYSTEM",
  "dev_entitlement_required": false, "btp_services_required": [],
  "steps": [
    {"n": 1, "name": "Intake", "agent": "Orchestrator", "status": "PASS",
     "gate": false, "score": null, "iterations": 1, "detail": "one line"}
  ],
  "human_approvals": [
    {"checkpoint": "CP1 · Solution approval", "decision": "approved",
     "by": "developer", "date": "2026-07-18", "notes": "mode split confirmed"}
  ],
  "gate_results": [{"name": "RELEASE_CHECK", "status": "PASS", "detail": "..."}],
  "findings": [{"id": "LNT-01", "severity": "Major", "source": "LINT",
    "description": "...", "status": "Resolved", "resolution": "..."}],
  "deliverables": ["01-discovery.md", "..."]
}
```

Rules: `steps[].status` ∈ PASS | FAIL | RUNNING | AWAITING_APPROVAL | PENDING | SKIPPED; gate
steps carry `"gate": true` and a `score`; while waiting at a checkpoint the run's `status` is
`"awaiting_approval"` and the relevant step is `AWAITING_APPROVAL`. `gate_results[].status` ∈
PASS | CONDITIONAL_PASS | FAIL (release / TD gates) or SHIP | FIX | REDESIGN (peer-review gate) —
the Explorer renders PASS/CONDITIONAL_PASS/SHIP/OK/APPROVED as **passing** and FAIL/FIX/REDESIGN
as **failing**; keep a gate's final status consistent with `gates_passed` and the resolved
findings (a gate that FAILED, was fixed, then re-passed ends as PASS/CONDITIONAL_PASS, not FAIL).
`human_approvals[].decision`
∈ approved | adjusted | rejected — with `notes` capturing what the human changed.
**Experience Graph fields (`fd_name`, `requirement_summary`, `approved_approach`, `objects_used`,
`summary`) are what `build_index.py` indexes past runs on** — a run that leaves them empty is invisible
to `find_similar_delivery` and teaches the pipeline nothing. Write `requirement_summary` at intake,
`approved_approach` at solution approval, and `objects_used` + `summary` at package time.

`mode_override` (present only when the human selected a **non-recommended** approach at CP1, e.g. a
client-mandated BTP build over a RAP recommendation) = `{original_recommendation, original_mode,
selected_mode, selected_label, mandated, cost_disclosure_required, override_at, by}` — the audit trail
for a deliberate deviation from the clean-core recommendation; absent/`null` on a normal run.
`findings[].severity` ∈ Critical | Major | Minor | Info; `findings[].source` ∈ RELEASE_CHECK |
LINT | TD_REVIEW | PEER_REVIEW | UNIT_TEST | CHALLENGER; `findings[].status` ∈ Resolved | Open.
An unresolved Critical, or a missing checkpoint approval, means the run CANNOT be
`"completed"`. `quality_score` = 100 minus deductions (Critical open: blocks completion;
Critical resolved −5, Major open −8, Major resolved −2, Minor open −3, Minor resolved −1,
Info 0), floor 0 — computed, never invented.

## Headless runs (webapp-triggered)

When the prompt says **HEADLESS PIPELINE RUN** or **HEADLESS PIPELINE RESUME** (the webapp's
"Run pipeline" button spawns `claude -p`), adapt as follows — the human is on the other side of
the webapp, not in this chat:

1. **Checkpoints:** you cannot ask questions. At each ✋ checkpoint, write into `run.json`
   `"status": "awaiting_approval"`, set that checkpoint's step to `AWAITING_APPROVAL`, and write a
   `"checkpoint_request"` object, then END the run cleanly. The webapp shows it to the developer and
   re-invokes you with their decision. **Base shape** —
   `{"checkpoint": "CP1 · Solution approval", "summary": "<what is being decided, ≤5 lines>",
   "options": ["approve", "adjust", "reject"], "material": "02-solution-proposal.md"}`.
   Two checkpoints carry **extra fields the webapp renders as interactive controls** — you MUST
   include them or the developer has nothing to act on:
   - **CP1, whenever the solution CREATES custom objects:** add `"naming_contract"` — an array of
     `{id, object, type, created_in, name}` (the exact contract from step 5). The webapp renders an
     editable, namespace-validated name field per object and **locks** the names on approval; Build
     then uses them verbatim.
   - **CP2 · Code approval:** add `"code_files"` — an array of `{id, file, summary, material}`, one
     per built file/section; the webapp renders a per-file comment box and the developer's per-file
     change requests come back with the decision for you to apply file-by-file.
   - **Step 13 · BTP prerequisite check (side-by-side solutions only):** add `"checklist"` — an
     array of prerequisite strings (see the *Conditional · Deploy to BTP* section). The webapp
     renders a tick-list and **blocks approval until every item is checked**.
   Never write an `awaiting_approval` step whose `checkpoint_request` is missing the field its
   checkpoint needs (`naming_contract` for CP1 with created objects; `code_files` for CP2;
   `checklist` for step 13).
2. **Resume:** on HEADLESS PIPELINE RESUME, read `output/<ID>/run.json`, the deliverables so
   far, and `output/<ID>/decisions/` (the webapp saves each decision as `CP<n>.json`). Append
   the decision to `human_approvals`, clear `checkpoint_request`, apply any requested
   adjustments, and continue from that exact point — do not redo completed steps.
3. **Governance without the MCP (mandatory fast path):** the `s4pc` MCP tools are usually blocked
   by enterprise policy. When they are unavailable, do the release checks by **reading the four
   bundled catalogs ONCE with the `Read` tool** (never a shell command) and evaluating every object
   in-context:
   `mcp-server/catalog/released_cds_views.json`, `mcp-server/catalog/released_badis.json`,
   `mcp-server/catalog/released_apis.json`, `mcp-server/catalog/forbidden_patterns.json`.
   **Note:** the live catalog is `mcp-server/catalog/catalog.db` (SQLite); the JSON files above are
   seed snapshots kept for Claude-readable fallback and may be stale if `sync_hub.py` has been run
   since initial setup — they are still reliable for offline release checks.
   Per object: matches a `forbidden_patterns.json` entry (BAPI / classical table / enhancement
   point / Smart Form) → `NOT_AVAILABLE` (`evidence: rule`); found **in** a catalog file →
   `LIKELY_RELEASED` (`evidence: catalog_hit`); **not** in a catalog but matches released naming
   (CDS `I_/C_/A_/R_/E_`, APIs `API_*`/`*_SRV`/SOAP `*_IN`/`*_OUT`, events `CE_*`) →
   `LIKELY_RELEASED` (`evidence: naming_heuristic_only` — name-only, so write "name unconfirmed",
   never "released"); otherwise → `NOT_VERIFIED`. **Do NOT run the `check_object_release_state` CLI once per object,
   and do NOT probe for a Python interpreter (`where python`, `py --version`, `cd`, etc.)** — the
   JSON seeds are on disk; a few `Read`s replace all of it. The only tool you may
   shell out to is `abap_cloud_lint`, and only at the Lint step (step 9).
4. **Progress:** update `run.json` after every step (status RUNNING on the active step) — the
   webapp polls it live. Never leave the manifest stale for more than one step.

## Anti-patterns (auto-reject)

- Proceeding past a checkpoint without an explicit human decision.
- Forcing one mode on a multi-mode requirement (or side-by-side for a 20-line BAdI rule).
- Any BAPI / classical table / enhancement point / Smart Form anywhere in the design.
- Proposing an unreleased API or CDS view — solutions contain released objects ONLY.
- A BTP service in the proposal without its Discovery Center link and cost line.
- A proposal without the Feasibility/Approach/Cost rating table.
- Presenting seed-catalog hits as tenant-verified facts.
- Reporting a standard released CDS view (I_/C_/A_ …) as "not verified / not released" just
  because it is not in the offline seed — recognise the released-VDM naming, record it as
  released, and cite the Released CDS Views list for confirmation.
- Reporting an API-named service (API_*/*_SRV, e.g. `API_CLFN_PRODUCT_SRV`) as "not verified"
  because it is not in the offline seed — it is a released S/4HANA Cloud API; record it as
  released and finalise/cite it from the SAP Business Accelerator Hub
  (api.sap.com/api/<API_NAME>/overview).
