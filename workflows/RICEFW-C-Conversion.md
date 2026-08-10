# RICEFW-C — Conversion / Data Migration (S/4HANA Cloud Public Edition)

**Type:** RICEFW · Conversion
**Realization:** Load data with the Migrate Your Data app (migration cockpit with staging tables) or released APIs — direct table writes, LSMW, and custom batch input are not available in Public Cloud.

## Pipeline
1. Discovery → objects, volumes, source systems, cutover window, cleansing rules.
2. Check migration cockpit object coverage first (Migrate Your Data — Migration Objects list in tenant). Covered → cockpit; not covered → released API load via side-by-side script.
3. Object inventory + `check_object_release_state` for any API-based load.
4. **GATE:** load path is cockpit template or released API. No exceptions.
5. Build (from the approved proposal + the locked Custom-Object Naming Contract — use those exact names): mapping sheets / load scripts; mock data first.
6. Code review + human **Code approval (Checkpoint 2)** on the built load scripts/mappings.
7. Tests: sample load → full-volume rehearsal → reconciliation report (record counts + key financial totals).
8. Technical Design (documentation, authored after tests): field mapping spec (source → staging/API field), value mappings, validation rules, reconciliation counts, error handling + reprocessing.
9. Package incl. cutover runbook (sequence, owners, fallback).

## Anti-patterns to reject
- "INSERT into the table" / "use LSMW" — impossible.
- Loading without reconciliation totals.
- One-shot full load without rehearsal.
