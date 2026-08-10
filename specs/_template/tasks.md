# Tasks — <feature name>

The 12 RICEFW pipeline steps (✋ = human checkpoint, ⛨ = quality gate). When you run
`/run-pipeline`, these are tracked live in `output/<RUN-ID>/run.json` / the Workflow Explorer.

- [ ] 1. Intake — restate scope, classify RICEFW type, list open questions
- [ ] 2. Solution Proposal — capability → mode split, object inventory, Feasibility/Approach/Cost
- [ ] 3. Object Inventory Verdicts — `check_object_release_state` on every object
- [ ] 4. ⛨ Gate 1 · Release Check
- [ ] 5. ✋ Checkpoint 1 · Solution approval
- [ ] 6. FD Analysis + Technical Design
- [ ] 7. ⛨ Gate 2 · TD Review + ✋ Checkpoint 2 · Design approval
- [ ] 8. Build (ABAP for Cloud / released objects only)
- [ ] 9. Lint — `abap_cloud_lint` must PASS
- [ ] 10. Unit Test Design — positive + negative, mapped to acceptance criteria
- [ ] 11. ⛨ Gate 3 · Peer Review + Challenger + ✋ Checkpoint 3 · Acceptance
- [ ] 12. Package — deliverables, deployment guide, tenant verification checklist
