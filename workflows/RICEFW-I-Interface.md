# RICEFW-I — Interface (S/4HANA Cloud Public Edition)

**Type:** RICEFW · Interface
**Realization:** Integrate through released OData/SOAP APIs and business events, orchestrated with SAP Integration Suite (CPI) or a BTP side-by-side service — custom RFC function modules and custom IDocs cannot be built.

## Pipeline
1. Discovery → direction (in/out), trigger (real-time / event / batch), payload, volumes, error strategy.
2. `extensibility_advisor` → expect **side_by_side**.
3. API selection: `search_released_apis` → `check_object_release_state`; confirm operations (read/create/update) cover the need; events via Event Mesh for async triggers.
4. **GATE:** API released + communication scenario exists. If no released API covers the object → check SAP roadmap / custom RAP service over released CDS (developer mode) as the exposure.
5. Build (from the approved proposal + the locked Custom-Object Naming Contract — use those exact names): iFlow / CAP service; mock-first, then tenant test with `odata_get_metadata` as field truth. For CAP/Node/JS follow the dev docs (cap.cloud.sap/docs, nodejs.org/docs/latest/api, w3schools.com/js, npmjs.com, community.sap.com).
6. Code review + human **Code approval (Checkpoint 2)** on the built service/iFlow.
7. Tests: happy path, duplicate delivery, partial failure, auth failure, volume test.
8. Technical Design (documentation, authored after tests): communication arrangement + user, iFlow design (mapping, error handling, retry, alerting), idempotency keys, monitoring plan (Message Dashboard / Integration Suite monitor).
9. Package incl. connectivity checklist (arrangement, certs, allowlists). A side-by-side interface deploys via steps 13-14 (`btp_deploy`) after the step-13 prerequisite checklist is confirmed.

## Anti-patterns to reject
- "Create a Z RFC / custom IDoc" — impossible in Public Cloud.
- Point-to-point with hardcoded hosts — use Integration Suite + destinations.
- Polling loops where a business event exists.
