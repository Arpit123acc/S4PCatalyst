# S4PC Catalyst — S/4HANA Cloud Public Edition Agentic Delivery Pipeline

An agentic delivery accelerator for **S/4HANA Cloud, Public Edition**: a standalone MCP server +
Claude Code skills & a 6-agent orchestration + **a Catalyst-style demo webapp** that let Claude
deliver clean-core RICEFW objects — with governance, observability, and anti-hallucination gates
built in.

## Quick start (the demo UI)

| Platform | How |
|---|---|
| **Windows** | Double-click **`START.cmd`** (needs only Python 3.9+ from python.org — no pip, no Node, no admin). Stop with `SHUTDOWN.cmd` or Settings → Catalyst → Shut down. |
| **macOS / Linux** | `./start.sh` (uses the built-in system `python3`). |

The browser opens at `http://127.0.0.1:8321` with the full Catalyst UI: **Home**, **Workflows**
(RICEFW library), **Workflow Explorer** (interactive 12-step pipeline run visualization —
quality score, gates, findings inventory, deliverables), **Playground** (run the clean-core
gates live — release check, ABAP lint, extensibility advisor, API/BAdI search), **Catalyst**
(three extensibility modes), **Agent Workflow**, **Reference** (Skills / Workflows / MCP
Servers / Agents / Catalogs), **Settings** (Connectivity + Catalyst), **Admin** (real usage
metrics + audit trail), **Help/FAQ**.

**How a developer uses the pipeline (human-in-the-loop, fully automatic):**
1. **FD Intake** page → upload/paste the Functional Design (saved to `input/`).
2. Click **▶ Run pipeline**. The webapp spawns `claude -p` headlessly (your logged-in Claude
   Code session — still no API keys) and the whole pipeline executes, updating
   `output/<ID>/run.json` after every step so the Workflow Explorer shows live progress.
3. The pipeline proposes a **clean-core solution per capability** — key user, developer
   (ABAP Cloud), side-by-side (BTP), **or a mix**, driven entirely by the requirement — and
   **pauses at three checkpoints** (solution approval → design approval → acceptance). The
   Explorer shows an amber decision panel: approve / adjust / reject + notes. Submitting the
   decision resumes the pipeline automatically; every decision lands in
   `output/<ID>/decisions/` and `run.json.human_approvals`.

**Pipeline engine requirements:** the machine needs the Claude Code **CLI** (`claude` on PATH,
or set `S4PC_CLAUDE_BIN` to its full path). Without it, the "Run pipeline" button explains the
fallback: copy the pipeline command from the FD card into interactive Claude Code — identical
behavior, checkpoints are then answered in the chat instead of the UI. Headless runs use a
restricted tool allowlist (file tools + `python3 mcp-server/server.py --tool …` + the s4pc MCP
server) and are fully logged to `webapp/logs/pipeline-*.log` (viewable on the FD Intake page).

**How pipeline runs appear in the Explorer:** the `s4pc-ricefw-pipeline` skill writes
`output/<OBJECT-ID>/run.json` (schema documented in the skill) plus the ten deliverable
documents on every run. The Explorer simply reads `output/` — run the pipeline in Claude Code,
refresh the page, and the run is there. One bundled example (`MM-EXT-0001`, marked as such) is
included so the page demos well before your first real run; its lint verdicts are genuine tool
outputs.

Everything in the UI is real, not mocked: the Playground executes the same tool code Claude
uses via MCP, and Admin reads the actual audit log. Great for showcasing to leads.

**Sharing with the team:** zip the whole folder and send it. Teammates need only Python 3.9+
installed — nothing else. On Windows, if teammates use Claude Code too, change `"command"` in
[.mcp.json](.mcp.json) from `python3` to `python`.

**Design constraints honored:**
- **No API keys.** Claude Code is the LLM runtime; the MCP server never calls any LLM API.
- **Zero dependencies.** Pure Python 3.9+ stdlib (works with macOS system Python — no pip,
  no Node, no installs). One file: [mcp-server/server.py](mcp-server/server.py).
- **Public Cloud only.** No BAPIs, no classical ABAP — released BAdIs/APIs/CDS views only,
  enforced by tooling, not by hoping the model remembers.

## Architecture

```
Browser (demo UI)                     Claude Code (LLM runtime, no API key —
   │  http://127.0.0.1:8321                 your Claude session)
   ▼                                     │  stdio (JSON-RPC / MCP)
webapp/app.py (stdlib HTTP server)       │
   │  imports the same tool code         ▼
   └────────────────────────────► mcp-server/server.py   ← governance layer
   ├── Guardrails   offline-first, GET-only, service allowlist, rate limit,
   │                TLS enforced, secret redaction, blocked URL patterns
   ├── Observability  logs/audit.jsonl (every call), logs/metrics.json,
   │                  observability_snapshot tool
   ├── Anti-hallucination  every response carries verified/source fields;
   │                  unknown objects → NOT_VERIFIED, never a guess
   ├── catalog/      seed data: released APIs, BAdIs, CDS views, lint rules
   └── (live mode)   read-only OData to the tenant via communication user
```

### Tools exposed to Claude

| Tool | Purpose | Network |
|---|---|---|
| `search_released_apis` | Find released OData/SOAP/event APIs | no |
| `search_released_badis` | Find key-user BAdIs (Custom Logic) | no |
| `check_object_release_state` | Clean-core gate: BAPI/table → NOT_AVAILABLE + alternative | no |
| `extensibility_advisor` | Key user vs developer vs side-by-side (or mixed) with mode profiles, Feasibility/Approach/Cost rating model, doc links | no |
| `query_experience` | Search the delivery-lessons database (20+ Public Cloud lessons, grows per run) | no |
| `record_experience` | Persist a new lesson (called at the package step — compounding knowledge) | no |
| `get_reference_links` | Authoritative sources: Business Accelerator Hub (APIs/CDS), Discovery Center (BTP + pricing), Help Portal, Fiori Apps Library | no |
| `abap_cloud_lint` | Static ABAP-for-Cloud lint (20 rules) | no |
| `odata_get_metadata` | $metadata of allowlisted service (ground truth for fields) | live only |
| `odata_query` | Read-only, capped, allowlisted entity query | live only |
| `sap_connection_test` | Tenant connectivity check | live only |
| `guardrails_status` | Show active guardrails | no |
| `observability_snapshot` | Metrics + audit tail | no |

### Skills (Claude Code, in `.claude/skills/`)

- **s4pc-ricefw-pipeline** — multi-agent 12-step build pipeline (the Delivery Lead orchestrates specialist subagents) with 3 hard gates (release check, TD review, peer review)
- **s4pc-extensibility-decision** — defensible mode decision with object release verdicts
- **s4pc-fd-creation** — 6-step FD creation with Public Cloud template
- **s4pc-clean-core-review** — adversarial code/design review, verdict-driven

## Project components (Claude Code)

Everything the agent uses ships in the repo — inspect it all in VS Code under `.claude/` (plus
`specs/` and `.mcp.json`). Nothing is hidden or hosted.

```
.claude/
├── steering/     persistent project context (product / tech / structure)
├── agents/       6 subagent roles (Delivery Lead, Extensibility Architect, Developer, …)
├── skills/       4 skills — the playbooks the agents run
├── commands/     slash commands (/run-pipeline, /create-fd, /extensibility, /clean-core-review)
├── hooks/        clean_core_guard.py — non-blocking clean-core guard
├── settings.json hook configuration
└── launch.json   webapp launch config
.mcp.json         registers the s4pc MCP governance server
CLAUDE.md         non-negotiable clean-core rules (imports the steering docs)
specs/            spec templates (requirements / design / tasks) → specs/README.md
```

| Component | Location | What it is |
|---|---|---|
| **Steering** | `.claude/steering/` | Product / tech / structure context, auto-loaded via `@imports` in [CLAUDE.md](CLAUDE.md). |
| **Agents** (6) | `.claude/agents/` | Delivery Lead + 5 specialists (Extensibility Architect, Developer, Clean-Core Reviewer, Test Agent, Challenger) as Claude Code subagents, each delegating to a skill + the `s4pc` MCP tools. |
| **Skills** (4) | `.claude/skills/` | The playbooks: ricefw-pipeline, extensibility-decision, fd-creation, clean-core-review. |
| **Slash commands** (4) | `.claude/commands/` | `/run-pipeline`, `/create-fd`, `/extensibility`, `/clean-core-review`. |
| **Hooks** | `.claude/settings.json` + `.claude/hooks/` | Non-blocking clean-core guard on Write/Edit — flags classical/non-released ABAP, logs to `webapp/logs/`, never blocks a run. |
| **MCP server** | `.mcp.json` → `mcp-server/` | The `s4pc` governance server (release checks, lint, advisor, experience) — offline-first, zero-dependency. |
| **Specs** | `specs/` | Spec-driven scaffold (requirements / design / tasks) mapped to the FD → design → pipeline flow. |

## Setup

Nothing to install. The server is registered via [.mcp.json](.mcp.json) — restart Claude Code in
this folder and approve the `s4pc` server when prompted. Verify with: *"call guardrails_status"*.

To test the server manually:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}}' | python3 mcp-server/server.py
```

## Live mode (optional, later)

Offline mode (default) needs no connectivity at all. When your organization provides a
**communication user** (this is basic auth to your own tenant — not an LLM API key):

```bash
export S4PC_MODE=live
export SAP_BASE_URL="https://myXXXXXX-api.s4hana.cloud.sap"
export SAP_COMM_USER="CC_CLAUDE"           # communication user
export SAP_COMM_PASSWORD="..."             # never lands in any file or log
```

Then update `.mcp.json`'s `env` block (or export in the shell that launches Claude Code).
Live mode is **read-only**: GET only, allowlisted services (edit
`guardrails.odata_service_allowlist` in [mcp-server/config.json](mcp-server/config.json)),
`$top` capped at 50, 30 requests/min, HTTPS enforced, everything audited.

## Security posture

- Credentials: environment variables only; redacted from all logs.
- Writes: disabled twice (config flag AND env var must both be true — currently neither is).
- URL guardrails: blocked patterns (`$batch`, ADT system paths), identifier validation on
  service/entity names, `$filter` sanitization.
- Audit: every tool call → `mcp-server/logs/audit.jsonl` (timestamp, tool, redacted args,
  duration, outcome). Metrics per tool → `mcp-server/logs/metrics.json`.

## Anti-hallucination model

1. Seed catalogs are labeled as seeds; every response names the authoritative source
   (api.sap.com, Custom Logic app, ADT Released Objects).
2. `check_object_release_state` answers NOT_VERIFIED for anything unknown — the model is
   instructed (via server `instructions` + CLAUDE.md) to surface that to you verbatim.
3. Deterministic tools (lint, release gate, advisor rules) produce facts the model cannot argue
   with; deliverables must separate "verified" from "to verify in tenant".
4. In live mode, field names come from `$metadata`, not memory.

## Extending

- Add APIs/BAdIs/CDS views: edit the JSON files in `mcp-server/catalog/` (keep the
  `verified_in_tenant`/null-field discipline).
- Add lint rules: `mcp-server/catalog/forbidden_patterns.json`.
- Allowlist more OData services: `mcp-server/config.json`.
- New pipeline skills: add folders under `.claude/skills/`.
