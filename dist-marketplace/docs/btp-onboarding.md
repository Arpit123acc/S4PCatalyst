# Teammate onboarding — connect the webapp to *your* BTP account

A one-pager to get the **BTP deploy connection** (Settings → Connectivity) working on **your own
machine** with **your own** BTP subaccount (personal trial or a client subaccount). Each person is
fully isolated — your login lives in your own `~/.cf`, so teammates never collide.

> **You never paste a token.** Connection is by reusing your `cf login` session. Leave the
> Password / Client-Secret fields blank unless your tenant specifically needs them (see step 4).

---

## Step 1 — Install the tools (once per machine)

You need **Cloud Foundry CLI v8**, the **multiapps** plugin, and **mbt**.

**Windows:**
```powershell
winget install --id CloudFoundry.CLI.v8
cf install-plugin multiapps -f
npm install -g mbt
```
**macOS:**
```bash
brew install cloudfoundry/tap/cf-cli@8
cf install-plugin multiapps -f
npm install -g mbt
```

Then **open a new terminal** so `cf` is on your PATH. Verify:
```
cf version      # should print 8.x
mbt --version
```

> ⚠️ If the webapp was already running before you installed `cf`, **restart it from a fresh
> terminal** — a process started earlier has a stale PATH and will report `✕ cf CLI`.

---

## Step 2 — Find your CF endpoint / org / space

In the **SAP BTP Cockpit** → your subaccount → **Cloud Foundry Environment**:
- **API endpoint** — e.g. `https://api.cf.<region>.hana.ondemand.com` (regions: `ap21`, `eu10`,
  `us10`, …). Yours may differ from a teammate's — use **your** subaccount's value.
- **Org** — your CF org name.
- **Space** — a **dev/test** space (production names are refused by the tool).

---

## Step 3 — Log in with your own account (in a terminal)

```
cf login --sso -a <YOUR-API-ENDPOINT>
```
- It prints a passcode URL on the `login.cf.<region>…/passcode` host. Open it → **Sign in with
  default identity provider** → copy the **Temporary Authentication Code**.
- Paste it at the terminal's `One Time Code` prompt (input stays hidden — normal) → Enter.
- Pick **your** org and space.

Verify:
```
cf target
```
You should see your user / org / space. (Trial SSO tokens are short-lived — if it ever says
"Not logged in", just run `cf login --sso` again.)

> Not on SSO? If your tenant uses a plain CF **username + password**, run
> `cf login -a <endpoint>` instead — or skip this and use the panel fields in step 4.

---

## Step 4 — Fill the panel and Test

Open the webapp → **Settings → Connectivity → BTP deploy connection**:

1. Enter **your** **API endpoint**, **Org**, **Space**.
2. Pick **one** authentication method:
   - **Reuse `cf login` session (recommended):** you did step 3 → leave **all** secret fields blank.
   - **User + password:** fill **CF user** + **Password** (only if your tenant allows password login).
   - **Service key (client / CI):** fill **Client ID** + **Client Secret** (an admin creates an
     OAuth2 client-credentials service key bound to your CF space).
3. Tick **Enable BTP deploy** → **Save connection** → **Test connection**.

✅ **`✓ Connected`** — you're done. Nothing else to enter.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Panel shows `✕ cf CLI` but `cf` works in your terminal | Webapp started with a stale PATH — restart it from a fresh terminal. |
| `cf target` works in your terminal but panel says "No active session" | Make sure you ran `cf login` as the **same OS user** that runs the webapp. |
| "No active session" right after logging in (trial) | Trial SSO tokens expire in minutes — re-run `cf login --sso` and Test immediately. |
| Passcode page shows only "choose identity provider" | Click **Sign in with default identity provider** (trials use the default SAP ID service). |

---

## Security notes

- Connection by session reuse means **no secret is ever typed into the app**.
- Any password / client secret you *do* enter is **memory-only** — never written to disk, git, or
  logs, and wiped on restart or via **Clear session creds**.
- **Never paste a real secret into an AI chat** — type it into the browser panel only.
- Service keys are **revocable and rotatable** by your admin; scope them minimally.

Full deploy runbook: [btp-deploy.md](btp-deploy.md).
