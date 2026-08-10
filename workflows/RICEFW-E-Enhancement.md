# RICEFW-E — Enhancement (S/4HANA Cloud Public Edition)

**Type:** RICEFW · Enhancement
**Realization:** Extend standard behaviour with key-user custom logic (released BAdIs) or a developer BAdI implementation — enhancement points, user exits, and core modifications do not exist in Public Cloud.

## Pipeline
Follow the `s4pc-ricefw-pipeline` skill (12 steps, 3 hard gates) with these enhancement-specific points:

1. Discovery → identify the standard business object and the exact moment logic must run (check/modify/save).
2. `extensibility_advisor` → expect **key_user** (Custom Logic) for validations/defaults; **developer** if logic needs versioned code, reuse, or heavy processing.
3. `search_released_badis` for the business context → `check_object_release_state` for the chosen BAdI.
4. **GATE:** BAdI must exist in the tenant's Custom Logic app. Not there → it is NOT available; redesign (side-by-side event-based or change request to SAP).
5. Build (from the approved proposal + the locked Custom-Object Naming Contract — use those exact names): BAdI implementation (key-user editor or ADT); `abap_cloud_lint` every snippet.
6. Code review + human **Code approval (Checkpoint 2)** on the built BAdI code.
7. Tests: one positive + one negative per validation rule.
8. Technical Design (documentation, authored after tests): BAdI name, filter values, pseudo-logic, custom fields consumed (per the naming contract, YY1_*), messages.
9. Package with tenant verification checklist.

## Anti-patterns to reject
- "Enhance the include / add an implicit enhancement" — impossible.
- Heavy DB reads inside check BAdIs (performance on every save).
- Cross-object writes from a validation BAdI.
