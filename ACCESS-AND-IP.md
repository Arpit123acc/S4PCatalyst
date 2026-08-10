# S4PC Catalyst — Access & IP / Security Brief

*S/4HANA Cloud Public Edition agentic delivery accelerator — one-page brief for leadership.*

## What it is
A **local developer accelerator, not a hosted SaaS.** It is a self-contained folder (a
zero-dependency Python webapp + Claude Code skills + a governance MCP server) that each developer
runs on **their own machine** at `http://127.0.0.1:8321`. There is **no central server, database,
or cloud backend** to provision or log into.

## How your team gets access
| Step | Detail |
|---|---|
| **1. Distribute** | Host the package in your **internal, access-controlled source** (Azure DevOps / Bitbucket / GitHub Enterprise) or a team share — **not a public repo**. |
| **2. Prerequisites (per developer)** | **Python 3.9+** (free; runs the webapp — no other dependencies) **and** a **Claude Code entitlement** (your org's Claude for Work / Enterprise seat), logged in — this is the AI runtime and the real access gate. |
| **3. Run** | Unzip → `START.cmd` (Windows) or `./start.sh` (macOS/Linux) → browser opens at `localhost:8321`. Stop with `SHUTDOWN.cmd`. |

Access is **per-developer and isolated** — there are no shared credentials or accounts to manage.

## IP & data protection

### Stays local by design
- Runs entirely on the developer's machine; the governance MCP server is **offline by default**
  (no network calls).
- FDs, generated ABAP, technical designs, and run outputs live **only on local disk / your repo**.
- **No API keys** are stored anywhere, and the toolkit never calls an external service in offline
  mode. SAP credentials (optional live mode) are **environment-variables only** and redacted from
  all logs.
- The accelerator and every deliverable it produces are **your team's IP**.

### The one dependency to govern: the AI runtime (Claude)
- Pipelines are executed by **Claude (Anthropic)** via Claude Code. Running a pipeline **sends the
  FD text and generated code to Claude** to process — this is inherent to using any LLM.
- Use **commercial / enterprise Claude** (Claude for Work / commercial API terms): under these,
  Anthropic **does not train on your business data by default**, and zero-data-retention options
  are available. Do **not** use personal/consumer Claude accounts for project work.
- **Action item:** confirm the exact data-processing terms (DPA, no-training, retention) for your
  plan with your Anthropic account owner or procurement before rollout.

### Fully-offline mode (default)
- Web verification (WebFetch / WebSearch) is **OFF by default**. Object release is confirmed
  against the **local catalog + SAP naming rules**, and deliverables **cite the authoritative SAP
  URLs** (Released CDS Views list, List of BAdIs, SAP Business Accelerator Hub) for manual/tenant
  confirmation — **no object names or content leave the machine.**
- To let the pipeline *also* confirm against `api.sap.com` / SAP Help during a run, set
  `S4PC_ALLOW_WEB=1` before launch. Leave it unset for maximum IP isolation.

## Recommended posture for the project
1. Host the toolkit in your **internal repo**; control who can clone it.
2. Standardize on your **enterprise Claude Code** entitlement; confirm the DPA / no-training terms.
3. Keep the MCP server **offline** (default); leave web tools **off** unless a run needs live SAP
   confirmation.
4. Treat `input/` and `output/` (FDs, code) as **project IP** under your normal source-control and
   access rules.
5. Never paste secrets/credentials into FDs; use live mode **read-only** with a dedicated
   communication user only when required.

---
*Prepared for internal use. The toolkit is offline-first and stores nothing externally; the only
data that reaches a third party is what Claude processes at runtime — governed by your enterprise
Claude Code agreement, which should be confirmed with Anthropic before rollout.*

---

## Rollout & access checklist

**Repository & access**
- [ ] Put the toolkit in a **private, access-controlled repo** (Azure DevOps / Bitbucket / GitHub Enterprise) — never public.
- [ ] Restrict access to a **named group**; grant least privilege; review membership periodically.
- [ ] Mark the repo **Internal / Confidential**; it is covered by your standard employee/contractor IP terms.
- [ ] Don't commit generated runs or secrets — `.gitignore` already excludes `logs/`, `output/`, `__pycache__/`, `*.code-workspace`. Decide whether project FDs in `input/` should be committed.

**Per-developer prerequisites**
- [ ] **Python 3.9+** installed.
- [ ] **Claude Code (enterprise) logged in** — confirm the DPA / no-training / retention terms with your Anthropic account owner.

**Onboarding a developer (local copy — recommended)**
- [ ] Clone the repo (or unzip the package).
- [ ] Run `START.cmd` (Windows) / `./start.sh` (macOS/Linux) → open `http://127.0.0.1:8321`.
- [ ] Upload an FD on FD Intake → **Run pipeline** → confirm a run appears in the Workflow Explorer.

**Optional — shared instance on one machine (no per-developer copy)**
- [ ] On one always-on machine: set `S4PC_ACCESS_PASSWORD`, then run `START-HOSTED.cmd` / `./start-hosted.sh`.
- [ ] Share `http://<host-ip>:8321` (login `team` / your password). Keep it on a trusted LAN/VPN; use HTTPS via a reverse proxy for wider exposure. See **`HOSTING.md`**.

**Offboarding**
- [ ] Remove repo access; delete local copies from machines that no longer need them.
