# RICEFW-R — Report (S/4HANA Cloud Public Edition)

**Type:** RICEFW · Report
**Realization:** Deliver reporting via a Custom CDS View + Custom Analytical Query (key user) or CDS + RAP + Fiori Elements list report (developer) — classical reports (REPORT/ALV/SE38) do not exist.

## Pipeline
1. Discovery → data sources, filters, aggregations, audience, volumes.
2. Decision: analytics-only → **key user** (Custom CDS View + Analytical Query + KPI tile);
   interactive actions / complex logic / OData exposure → **developer** (RAP query or managed RAP read-only).
3. Object inventory: source released CDS views (`search_released_apis` / `check_object_release_state` for each I_* view; classical tables auto-map to their I_* replacement).
4. **GATE:** every data source released.
5. Build (from the approved proposal + the locked Custom-Object Naming Contract — use those exact names): CDS views + analytical query / RAP; lint; Fiori Elements preview.
6. Code review + human **Code approval (Checkpoint 2)** on the built code.
7. Tests: CDS test doubles (CDS unit tests), key-figure reconciliation script vs standard app numbers.
8. Technical Design (documentation, authored after tests): view hierarchy (basic → composite → consumption), annotations (@UI, @Analytics), authorizations (access control / DCL on released views).
9. Package incl. tile/catalog assignment steps.

## Anti-patterns to reject
- "Just write an ALV" — prohibited.
- SELECT on unreleased tables in the view — will not activate.
- Rebuilding a standard Fiori analytical app that already exists (fit-to-standard first).
