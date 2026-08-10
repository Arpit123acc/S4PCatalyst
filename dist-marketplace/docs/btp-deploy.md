# BTP Deploy Runbook — side-by-side (CAP + UI5)

Deploy the **side-by-side (BTP)** part of a solution — a CAP service + UI5 app — to a Cloud
Foundry **dev/test** space. Applies **only** to side-by-side capabilities; in-tenant ABAP Cloud/RAP
and key-user work deploy via ADT / in-app config, not this runbook.

> **Off by default.** Nothing deploys until you deliberately enable it (two switches below), and
> even then it **dry-runs** (build only) unless you pass `dry_run=false`. Production spaces are
> refused — promote to prod via your CI/CD, not this pipeline.

## 1. Prerequisites (on the machine that runs the deploy)
- **Cloud Foundry CLI** (`cf`) + **MultiApps** plugin — `cf install-plugin multiapps`
- **Cloud MTA Build Tool** — `npm i -g mbt`
- **CAP dev kit** — `npm i -g @sap/cds-dk`
- A BTP subaccount with a **dev/test CF space**, HANA Cloud running, and entitlements for
  `xsuaa`, `destination`, `html5-apps-repo`.

## 2. Enable it (both required)
1. `mcp-server/config.json` → `guardrails.deploy.allow_deploy: true`
2. Environment → `S4PC_ALLOW_DEPLOY=true`

Either one alone keeps it off.

## 3. Credentials (environment variables only — never in code)
| Variable | Meaning |
|---|---|
| `CF_API` | e.g. `https://api.cf.eu10.hana.ondemand.com` |
| `CF_ORG` | your CF org |
| `CF_SPACE` | a **dev/test** space (prod names are refused) |
| `CF_USER` + `CF_PASSWORD` | technical/service user (preferred over a personal login) |
| `CF_TOKEN` | alternative: a pre-authenticated token (skip user/password) |

For automation use a **service key / technical user**, not a human password.

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
- `guardrail_blocked` → enable both switches (step 2).
- `"looks like production"` → target a dev/test space; prod goes through CI/CD.
- `mbt build` fails → run `npx cds build --production` locally to see the CAP error.
