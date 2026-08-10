# BTP Deploy Runbook — side-by-side (CAP + UI5)

Deploy the **side-by-side (BTP)** part of a solution — a CAP service + UI5 app — to a Cloud
Foundry **dev/test** space. Applies **only** to side-by-side capabilities; in-tenant ABAP Cloud/RAP
and key-user work deploy via ADT / in-app config, not this runbook.

> **Off by default.** Nothing deploys until you deliberately enable it (two switches below), and
> even then it **dry-runs** (build only) unless you pass `dry_run=false`. Production spaces are
> refused — promote to prod via your CI/CD, not this pipeline.

## 1. Prerequisites (on the machine that runs the deploy)
- **Cloud Foundry CLI v8** (`cf`) + **MultiApps** plugin — `cf install-plugin multiapps -f`
  - Windows: `winget install --id CloudFoundry.CLI.v8`. After install, **open a new terminal**
    so `cf` is on PATH; if the webapp was already running, **restart it from a fresh terminal**
    (a process started before the install has a stale PATH and reports `✕ cf CLI`).
- **Cloud MTA Build Tool** — `npm i -g mbt`
- **CAP dev kit** — `npm i -g @sap/cds-dk`
- A BTP subaccount with a **dev/test CF space**, HANA Cloud running, and entitlements for
  `xsuaa`, `destination`, `html5-apps-repo`.

> **Per user / per account.** Each OS user installs the CLIs once and logs in with **their own**
> BTP account (client subaccount, or a personal trial). Sessions are stored per-user under
> `~/.cf` (`CF_HOME`), so different users on the same machine — and testers on separate trial
> accounts — never collide. The webapp pins `CF_HOME` to the current user's `~/.cf` automatically,
> so the panel always reads the same session that user's `cf login` writes.

## 2. Enable it (both required)
1. `mcp-server/config.json` → `guardrails.deploy.allow_deploy: true`
2. Environment → `S4PC_ALLOW_DEPLOY=true`

Either one alone keeps it off.

## 3. Connecting — two ways (both work for client accounts and personal trials)

**A. Reuse your `cf login` session (recommended — universal).** Log in once in a terminal on the
machine that runs the webapp; the panel and the deploy reuse that session. Works with **any** auth
method — trial SSO, corporate-IdP SSO, or user+password — and any region:
```
cf login -a <CF_API>            # add --sso for trial / SSO tenants
```
Then in the panel: fill **API endpoint / Org / Space**, leave **Password and Token blank**, Save,
Test. The panel probes with the read-only `cf target` and never runs `cf api` (which would reset an
SSO session). Trial SSO tokens are short-lived — if Test/deploy later says *"No active session,"*
just re-run `cf login` and go again.

**B. Non-interactive credentials (automation / CI — env vars only, never in code).**
| Variable | Meaning |
|---|---|
| `CF_API` | e.g. `https://api.cf.eu10.hana.ondemand.com` |
| `CF_ORG` | your CF org |
| `CF_SPACE` | a **dev/test** space (prod names are refused) |
| `CF_CLIENT_ID` + `CF_CLIENT_SECRET` | **service key** (OAuth2 client-credentials) — preferred for CI; stable, non-expiring |
| `CF_USER` + `CF_PASSWORD` | technical/communication user (only where password login is allowed) |

The panel exposes the same two options (**Service-key Client ID + Secret**, or **CF user +
Password**). When a session isn't already active, the deploy runs `cf auth <id> <secret>
--client-credentials` (service key) or `cf auth <user> <password>`. A **raw bearer token can't be
injected into the `cf` CLI non-interactively** — use a service key, user+password, or option A.
For automation prefer the **service key**, not a human password.

## 4. Run it
- **Dry-run first (default)** — builds the MTA, deploys nothing:
  `btp_deploy { "project_dir": "<app>", "space": "<dev-space>" }`
- **Deploy** (only after the CP-Deploy approval):
  `btp_deploy { "project_dir": "<app>", "space": "<dev-space>", "dry_run": false }`

Equivalent by hand:
```
cf api $CF_API
cf auth "$CF_USER" "$CF_PASSWORD"        # or: cf login --sso
cf target -o $CF_ORG -s $CF_SPACE
mbt build -t ./mta_archives
cf deploy ./mta_archives/<app>_<ver>.mtar
```

## 5. The mta.yaml (what gets deployed)
Template: `mcp-server/templates/mta.yaml`.

| Piece | Type | Deploys |
|---|---|---|
| `<app>-srv` | nodejs | the CAP service |
| `<app>-db-deployer` | hdb | the data model into HANA Cloud (HDI container) |
| `<app>-ui-deployer` + `<ui5app>` | html5 content | the UI5 app into the HTML5 app repo |
| `<app>-auth` | xsuaa | authentication / scopes (`xs-security.json`) |
| `<app>-destination` | destination | connectivity to S/4HANA released APIs |
| `<app>-html5-repo-host` | html5-apps-repo | hosts the UI content |

## 6. Built-in guardrails
Two switches (config + env) · dry-run default · production-space refusal · env-only credentials
(the `cf auth` line is redacted in tool output) · every deploy audited to
`mcp-server/logs/audit.jsonl` · a human **CP-Deploy** before any push.

## 7. Promote to production
Out of scope here. Promote the tested `.mtar` through your **CI/CD** (SAP Continuous Integration &
Delivery service, or GitHub Actions / Jenkins with `cf`) under its own approvals.

## 8. Troubleshooting
- `deployed:false, prereqs:{cf:false, mbt:false}` → install the CLIs (step 1).
- **Panel shows `✕ cf CLI` but `cf` works in your terminal** → the webapp process started with a
  stale PATH (before `cf` was installed). Restart the webapp from a **fresh** terminal.
- **`cf target` works in your terminal but the panel says "No active session"** → the panel is
  reading a different `cf` config folder. The webapp pins `CF_HOME=~/.cf`; make sure you ran
  `cf login` as the **same OS user** that runs the webapp. (Set `CF_HOME` explicitly on both if you
  keep sessions elsewhere.)
- **"No active session" right after logging in (trial)** → trial SSO tokens expire in minutes;
  re-run `cf login --sso -a <CF_API>` and Test/deploy immediately.
- `guardrail_blocked` → enable both switches (step 2).
- `"looks like production"` → target a dev/test space; prod goes through CI/CD.
- `mbt build` fails → run `npx cds build --production` locally to see the CAP error.
