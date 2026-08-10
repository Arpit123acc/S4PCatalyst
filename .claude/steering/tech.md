# Steering — Technology & Constraints

**Platform:** SAP S/4HANA Cloud, **Public Edition** (3-system landscape). Clean core is a hard
platform constraint, not a preference.

**Three extensibility modes (only):**
- **Key user (in-app):** Custom Fields and Logic (released BAdIs), Adapt UI, Custom CDS Views,
  Custom Analytical Queries, Maintain Form Templates (Adobe Forms), Flexible Workflow.
- **Developer (ABAP Cloud / Embedded Steampunk):** RAP, CDS, released BAdIs, Application Jobs in
  "ABAP for Cloud Development" via ADT; ships as git-based software components.
- **Side-by-side (BTP):** CAP / ABAP Environment / Integration Suite / SBPA over released APIs and
  business events. Every BTP service carries consumption cost (SAP Discovery Center).

**Only released objects.** Every API / BAdI / CDS view / class must carry a C1 release contract or
be listed in the tenant's Custom Logic app. Verify with `check_object_release_state`; confirm on the
authoritative public sources: SAP Business Accelerator Hub (APIs), the SAP Help "Released CDS Views"
list, and the "List of BAdIs".

**Forbidden:** BAPIs, function modules (except rare C1-released), classical tables, SUBMIT / CALL
TRANSACTION, dynpros / module pools, FORM/PERFORM, classical reports, SAPscript / Smart Forms, user
exits, implicit/explicit enhancements.

**Runtime:** Claude Code is the LLM runtime (no API keys). The `s4pc` MCP server is pure-stdlib,
offline-first governance tooling and never calls an LLM. Full rules live in [CLAUDE.md](../../CLAUDE.md).
