# Design — <feature name>

## Extensibility decision (per capability)
| Capability | Mode (key user / developer / side-by-side) | Rationale |
|---|---|---|
| <capability 1> | <mode> | <why — clean-core, upgrade-safe, skillset> |

Option rating (Feasibility / Approach fit / Cost) and the recommended option go here.

## Released objects used (with verdicts)
| Object | Type (API / CDS / BAdI) | Verdict | Source to confirm |
|---|---|---|---|
| <e.g. I_MaterialStock> | CDS view | LIKELY_RELEASED | SAP Help Released CDS Views list / ADT |
| <e.g. API_CLFN_PRODUCT_SRV> | API | LIKELY_RELEASED | api.sap.com/api/<name>/overview |

*Only released objects (C1) may appear here. Verify each with `check_object_release_state`.*

## Technical design
<RAP artifacts (BOs, CDS, behavior defs) / released BAdI + custom-field spec / BTP service +
communication arrangements — per the chosen mode(s). OO-only, English-only, no hard-coding.>

## Tenant verification checklist
- [ ] <object/scenario to confirm in the tenant before build/transport>
