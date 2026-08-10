# RICEFW-W — Workflow (S/4HANA Cloud Public Edition)

**Type:** RICEFW · Workflow
**Realization:** Automate approvals with Flexible Workflow (Manage Workflows app) for standard scenarios, or SAP Build Process Automation (BTP) for complex orchestration — classical (SWDD) workflows cannot be built.

## Pipeline
1. Discovery → business object, trigger, approval levels, agent determination rules, escalation/deadline, notifications.
2. Check flexible workflow scenario coverage in tenant (Manage Workflows — scenario list). Covered → key user configuration; not covered → SAP Build Process Automation via API/event triggers (side-by-side).
3. Object inventory: preconditions/agent rules may need custom fields or a released BAdI for agent determination → `search_released_badis` + `check_object_release_state`.
4. **GATE:** scenario exists (flexible workflow) or released trigger event/API exists (SBPA).
5. Build (from the approved proposal + the locked Custom-Object Naming Contract — use those exact names): workflow configuration or SBPA process; version and document every rule.
6. Code review + human **Code approval (Checkpoint 2)** on the built workflow/SBPA process.
7. Tests: each approval path, rejection + rework, deadline escalation, substitute/delegation.
8. Technical Design (documentation, authored after tests): step sequence, conditions, agent rules (role/responsibility-based, never hardcoded users), deadlines, rework loops, notification templates.
9. Package incl. role/business-catalog mapping for approvers.

## Anti-patterns to reject
- Hardcoded user IDs as agents.
- Rebuilding a delivered flexible workflow scenario in SBPA "for flexibility".
- Approval logic hidden in custom code instead of visible workflow conditions.
