# S4PC Catalyst on SAP BTP Work Zone — Feasibility Assessment

**Status:** For team decision · **Date:** 2026-08-13 · **Author:** Delivery team
**Question:** What is required to run the S4PC Catalyst pipeline from SAP Build Work Zone?

---

## 1. Executive summary

Work Zone is a **launchpad/portal** — it surfaces HTML5 apps and Fiori tiles through destinations.
It does **not** execute application code. So "deploy on Work Zone" means: Work Zone is the entry
point, and the application runs on **Cloud Foundry or Kyma** behind it.

The UI, the MCP governance server, and the catalogs port to BTP without difficulty. **One component
does not: the pipeline engine.** Today it is the Claude Code CLI (`claude -p`) spawned as a detached
local subprocess under an interactive login. That model has no equivalent in a BTP container.

Two viable paths exist. **Path A** (Work Zone tile → destination → the existing hosted app) delivers
the Work Zone experience in days and preserves the current no-API-key rule. **Path B** (full
BTP-native re-platform) is a genuine product build measured in weeks-to-months and requires
overturning that rule. Recommendation: **Path A first**, Path B only if adoption justifies it.

---

## 2. What we actually measured

From `webapp/logs/usage.json` — three real pipeline runs, not estimates:

| Run | Phases | Cost (USD) | Engine time | Output tokens |
|---|---|---|---|---|
| SMART-SEARCH-FD | 4 | $2.03 | 21.7 min | 66,919 |
| SUPPLIER-PO-STATUS-VIEWER-FD | 5 | $2.51 | 25.0 min | 81,125 |
| TEST-LOGISTIC-EXT-… | 1 (partial) | $0.73 | 7.7 min | 26,092 |
| **Total** | **10** | **$5.27** | **54.4 min** | **174,136** |

**Average per full run: ~$2.25, ~23 minutes of engine time, 4–5 phases.** Model: `claude-sonnet-4-6`.
Cache reads dominate (1.4–1.6 M tokens/run), which is why cost stays low relative to volume — any
re-platform must preserve prompt caching or cost rises sharply.

Local state that must move off the filesystem:

| Artefact | Size | Nature |
|---|---|---|
| `graph.json` | 9.2 MB | regenerable (~90 s) |
| `index.json` | 2.0 MB | regenerable (~3 s) |
| `catalog.db` | 1.5 MB | regenerable from JSON seeds |
| `output/<RUN>/` | ~10 files/run | **not** regenerable — the deliverables |

---

## 3. The engine decision (the blocker)

| Option | Works on BTP? | Billing | Conflict with `CLAUDE.md` |
|---|---|---|---|
| **Claude Code CLI** (today) | ❌ No — needs interactive login, writable FS, detached processes | Per Claude Code seat | None |
| **Claude API (Anthropic SDK)** | ✅ Yes | Shared API account, ~$2.25/run measured | ❌ Breaks *"No LLM API keys anywhere"* |
| **Claude via AWS Bedrock / GCP Vertex** | ✅ Yes | Cloud marketplace billing | ❌ Same key/credential issue |

`CLAUDE.md` states: *"No LLM API keys anywhere — Claude Code is the runtime."* Any server-side
execution overturns this. It is an explicit team decision, not an implementation detail, and it also
changes the cost model: today runs bill to each developer's Claude Code seat; via API they bill to
one shared account (~$2.25/run → **~$225/month at 100 runs**, plus BTP infrastructure).

Re-implementation is not a thin wrapper. The engine currently relies on Claude Code's agentic loop:
tool use, file editing, MCP integration, session resume across five phases. On the API that becomes
an orchestration layer we own and maintain.

---

## 4. BTP services required (Path B)

Each links its SAP Discovery Center page; confirm entitlements and current pricing metric with your
BTP account team before committing — figures below are metrics, not quotes.

| Service | Why | Pricing metric |
|---|---|---|
| [SAP Build Work Zone, standard edition](https://discovery-center.cloud.sap/serviceCatalog/sap-build-work-zone-standard-edition) | Launchpad, tile, SSO entry point | Per user/month |
| [Cloud Foundry Runtime](https://discovery-center.cloud.sap/serviceCatalog/cloud-foundry-runtime) *or* [Kyma](https://discovery-center.cloud.sap/serviceCatalog/kyma-runtime) | Runs the Python app + MCP server | GB memory/hour |
| [SAP HANA Cloud](https://discovery-center.cloud.sap/serviceCatalog/sap-hana-cloud) *or* PostgreSQL, hyperscaler option | Catalog + run manifests + findings | Capacity units / GB |
| [Object Store](https://discovery-center.cloud.sap/serviceCatalog/object-store) | Deliverables (`output/<RUN>/`) | GB stored + requests |
| [Authorization and Trust Management (XSUAA)](https://discovery-center.cloud.sap/serviceCatalog/authorization-and-trust-management-service) | Auth, role collections, per-user isolation | Included with runtime |
| [HTML5 Application Repository](https://discovery-center.cloud.sap/serviceCatalog/html5-application-repository-service) | Hosts the UI for Work Zone | Included / per app |
| [Destination](https://discovery-center.cloud.sap/serviceCatalog/destination) | Connectivity (also required for **Path A**) | Free tier typically sufficient |
| [Connectivity / Cloud Connector](https://discovery-center.cloud.sap/serviceCatalog/connectivity-service) | Only if reaching an on-prem/VM engine (Path A) | Included |

**Path A needs only:** Work Zone + Destination + HTML5 App Repo (+ Cloud Connector if the engine
host is on a private network).

---

## 5. Gap analysis

| # | Area | Today | Required | Effort |
|---|---|---|---|---|
| 1 | **Engine** | `claude -p` subprocess, detached, local FS | Claude API orchestration: 5 phases, tool loop, MCP, resume | **L** |
| 2 | **Run state** | `output/` on disk; `JOBS` dict in memory | Object Store + DB; durable job table (survives restart/scale) | **M** |
| 3 | **Catalog/brain** | `catalog.db`, `index.json`, `graph.json` on disk | DB-backed or rebuilt into container volume on boot | **M** |
| 4 | **Auth** | One shared password (HTTP Basic) | XSUAA + role collections; per-user run visibility | **M** |
| 5 | **Concurrency** | Explicitly one run at a time | Per-user isolation, queueing, quota | **M** |
| 6 | **UI** | Served by Python | Static HTML5 app → HTML5 App Repo → Work Zone tile | **S** |
| 7 | **MCP server** | stdio subprocess | Unchanged — pure stdlib, containerises cleanly | **XS** |
| 8 | **Long jobs** | ~23 min/run; UI already polls | Keep async + polling; avoid HTTP router timeouts | **S** |
| 9 | **Secrets** | Env vars on one machine | Destination service / credential store | **S** |

Effort key: XS < 1 day · S ≈ 1–3 days · M ≈ 1–2 weeks · L ≈ 4+ weeks.
**Path A** touches only rows 6, 9 (+ a destination). **Path B** touches all nine.

---

## 6. Recommendation

**Start with Path A.** It gives the team the Work Zone entry point and portal SSO within days,
keeps the Claude Code seat model (no API-key decision, no `CLAUDE.md` conflict), and requires no
rewrite of the engine. Its honest limitation: the engine still runs on one always-on host, so the
tool remains effectively single-tenant and that host must be reachable from BTP.

**Defer Path B** until real usage justifies it. It is a re-platform, not a deployment: the engine
rewrite alone (row 1) is the dominant cost, and it permanently changes the licensing and support
model. Revisit when there is sustained multi-team demand.

---

## 7. Decisions required from the team

1. **Do we accept LLM API keys server-side?** Blocks Path B entirely; `CLAUDE.md` currently forbids it.
2. **Who owns the LLM spend** if it moves from individual seats to a shared account (~$2.25/run)?
3. **Is single-tenant acceptable** for the first release (Path A), or is multi-user isolation
   mandatory from day one?
4. **Where does the engine host live** for Path A — an internal VM (needs Cloud Connector) or a BTP-
   hosted VM?
5. **Data residency:** deliverables contain client requirements and generated code. Which BTP region,
   and is client material permitted there under the engagement's terms?
6. **Who operates it** — patching, Claude Code login renewal, catalog syncs, incident response?

---

## 8. Sources

- SAP Discovery Center — service catalogue and pricing metrics: https://discovery-center.cloud.sap/viewServices
- SAP Build Work Zone documentation: https://help.sap.com/docs/build-work-zone-standard-edition
- SAP BTP Cloud Foundry environment: https://help.sap.com/docs/btp/sap-business-technology-platform/cloud-foundry-environment
- Measured figures: `webapp/logs/usage.json` (3 runs, 10 phases, 2026-07-31 → 2026-08-13)

> Pricing metrics must be confirmed with your BTP account team — Discovery Center lists metrics, not
> your contracted rates.
