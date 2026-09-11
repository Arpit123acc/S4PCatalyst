# Giving the team the dashboard — migration plan

**Goal:** teammates open a URL on the Accenture network, log in, and run the pipeline. No
Claude Code install, no Python, no keys, no AWS access.

**Scope of work: one load balancer and one config change.** There is no rebuild. The pipeline
already runs server-side against Bedrock, and the dashboard already has authentication. This
document exists because the *sequencing* has a trap in it (§3).

---

## 1. Why there is nothing to port

The dependency on each teammate's own Claude Code was removed before this was ever asked for.
`webapp/app.py` runs the pipeline by spawning `claude -p` per phase **on the host**, and that
process resolves inference through the EC2 instance profile to Bedrock — `_spawn_claude()`
explicitly does `env.pop("ANTHROPIC_API_KEY", None)`, and the host has no Anthropic account.

So a teammate clicking *Run* is not using their Claude. They never were going to be. The
compute has always been server-side; the only thing missing is a way to reach it.

| Requirement | Already there |
|---|---|
| Runs server-side | `webapp/app.py` + `claude -p`, PM2-supervised |
| No LLM API keys | Bedrock via IAM instance profile |
| Authentication | HTTP Basic, `S4PC_ACCESS_PASSWORD` (app.py §"shared-instance access control") |
| Safe concurrent writes | `write_json_atomic` under `ThreadingHTTPServer` |
| Brain access | MCP on 3002, in-VPC |
| **Reachable from a laptop** | **No — binds 127.0.0.1. This is the gap.** |

This also dissolves the Claude Code enterprise-policy blocker entirely. That policy governs
what a *teammate's* Claude Code may attach to. If no teammate runs Claude Code, the allowlist
is not on the critical path. Keep the request open for people who want the brain in their own
editor, but the dashboard does not wait on it.

## 2. Console steps (account 269204395522, us-east-1)

Do them in this order. Steps 1–4 are additive and change nothing about the running system;
only step 5 alters behaviour, and it is the one to sequence carefully.

### Step 1 — Resize the instance

The host is 3.7 GB and already carries the FAISS index plus two web services. Resize before
inviting anyone, not after the first demo falls over.

1. **EC2 → Instances →** `i-0eb18c668bc9bbb33` **→ Instance state → Stop instance**
2. Wait for `stopped`, then **Actions → Instance settings → Change instance type**
3. Pick **`t3.large`** (8 GB) — or `t3.xlarge` (16 GB) if several people will run at once
4. **Instance state → Start instance**

The private IP `10.35.20.84` survives a stop/start (it belongs to the ENI), so the existing
NLB target group and the brain endpoint keep working untouched. PM2 restores all processes
via `pm2-ec2-user.service` — confirm with `pm2 list` before moving on.

**The brain endpoint is down for the duration.** Teammates already using it will get errors,
so pick a window and tell them.

### Step 2 — Target group

**EC2 → Target groups → Create target group**

| Field | Value |
|---|---|
| Target type | Instances |
| Protocol / port | **HTTP : 8321** |
| VPC | the instance's VPC |
| Health check protocol / path | HTTP `/` |
| **Advanced → Success codes** | **`200,401`** ← see §2.1 |

Register `i-0eb18c668bc9bbb33` on port **8321**.

### Step 3 — Internal ALB

**EC2 → Load balancers → Create → Application Load Balancer**

- **Scheme: Internal** (not internet-facing)
- VPC: the same one; select **two private subnets in different AZs**
- Security group: create a new one, e.g. `s4pc-dashboard-alb-sg`, inbound **443** (or 80 —
  see the note below) from the Accenture VPN CIDR
- Listener: forward to the target group from step 2

**Listener protocol — decided: HTTP for the POC.** HTTPS needs a certificate for a name you
control, and ACM public certs need DNS validation, which at Accenture is a ticket and a wait
rather than a console step.

What HTTP actually costs here is smaller than it looks, because **the ALB→EC2 leg is HTTP in
both designs** — the target group is HTTP:8321, so TLS terminated at the ALB would not protect
the password on that hop anyway. The only leg that differs is laptop→ALB, and that already
sits inside the VPN tunnel. Encrypting the internal hop as well would mean a certificate on
the EC2 too, which is a different size of job.

So the password is the entire control. Generate a real one — `openssl rand -base64 24` — and
distribute it out of band, the same way the brain keys were handled.

**Switch to HTTPS when either of these happens**, which are the same change:

- the URL reaches anyone outside the immediate POC group, or
- you want to know *who* approved a checkpoint.

The second is the forcing function: ALB **`authenticate-oidc` requires an HTTPS listener**, so
the certificate arrives together with the Entra ID work already listed in §5 rather than being
a separate chore. The upgrade itself is genuinely a listener change — add the certificate, add
a 443 listener, redirect 80 → 443, no application change.

You do **not** need Route 53. An internal ALB's DNS name is published in public DNS and
resolves to its private IPs, so teammates on the VPN can use
`internal-xxxx.us-east-1.elb.amazonaws.com` directly. Add a friendly name later.

### Step 4 — Security group on the instance

On the **instance's** security group, add an inbound rule:

| Type | Port | Source |
|---|---|---|
| Custom TCP | **8321** | **the ALB's security group** (`s4pc-dashboard-alb-sg`) — *not* a CIDR |

This is what makes step 5 safe. A CIDR source here would leave the dashboard reachable
directly from any VPN client, with the ALB bypassed entirely — which is exactly the gap that
still exists on port 3002 today.

### Step 5 — The bind change

Only after steps 2–4 exist. See §3 — this is the one with the trap in it.

### 2.1 The health check will fail unless the matcher accepts 401

**The webapp has no unauthenticated health endpoint.** `_authorized()` runs ahead of routing on
every GET except `/favicon.ico`, so once `S4PC_ACCESS_PASSWORD` is set, `/` returns **401** to
an anonymous caller — including the ALB's health checker. The default matcher expects `200`,
so the target goes **unhealthy**, the ALB refuses to forward, and the symptom is a dead URL
with a perfectly healthy application behind it.

This is the `DigitBrain` target-group failure again in a different costume. It cost a day
there; it is one field here.

**Set the target group's success codes to `200,401`.** A 401 is a *positive* health signal: it
proves the process is accepting connections, the handler is running, and authentication is
engaged. No code change, and it fails loudly if auth is ever accidentally switched off — the
check would start seeing 200 from an open app.

If a true health endpoint is preferred later, mirror the MCP server, which deliberately keeps
`/health` open for exactly this reason. That is a code change and adds an unauthenticated
route, so it is not the place to start.

## 3. The config change — and why it is one change, not two

Once the ALB and the SG rule exist, `deploy/ecosystem.config.js` needs the webapp to stop
binding loopback:

```javascript
env: {
  // Exception to the loopback rule, in the same family as s4pc-mcp below but NOT
  // identical — see docs/dashboard-access-migration.md §3.1. Valid ONLY while all
  // three hold: an INTERNAL-scheme ALB is the only route in (no internet path), the
  // SG admits 8321 from that ALB's SG alone, and S4PC_ACCESS_PASSWORD is set so every
  // route 401s without credentials. Remove any one and this goes back to 127.0.0.1.
  // S4PC_ACCESS_PASSWORD is a secret, supplied out-of-band, never in this file.
  S4PC_UI_HOST: '0.0.0.0',
},
```

### 3.1 This deviates from the loopback rule as written — knowingly

`server.py`'s rule is: override the loopback bind only behind something that **terminates TLS
and authenticates**. The POC meets the authentication half and, with an HTTP listener, does
not meet the TLS half. Say that out loud rather than let the comment imply otherwise — a
security invariant that has quietly stopped being true is worse than one that was never
claimed.

What carries the weight instead is the ALB's **internal scheme**: there is no internet path to
it at all, so the exposure is bounded to the VPC and whoever the VPN admits. That is a
genuinely different risk from the `s4pc-mcp` case, where API Gateway *is* internet-facing and
TLS is doing real work.

This deviation closes when the HTTPS listener goes in (§2, step 3), which is the same change
as Entra OIDC. Until then it is a POC-scoped compromise with a named trigger, not a new
standard — do not cite it as precedent for binding anything else off loopback.

**Do not make this change before the SG rule is in place, and never without
`S4PC_ACCESS_PASSWORD` set in the same change.** This is the pipeline's *approval* surface —
CP1/CP2/CP3 live here. A wildcard bind with auth off does not merely expose a demo; it lets
anything routing to the box approve a checkpoint. That is the 2026-09-03 exposure with a worse
blast radius.

Restarting to pick it up has the same trap as the MCP server: `pm2 restart s4pc-webapp
--update-env` only carries `S4PC_ACCESS_PASSWORD` through if it is exported in *that* shell.
If it is not, the restart strips it from the running process and leaves a wildcard bind with
no password. Export it first, or restart without `--update-env`.

## 4. Verify before telling anyone the URL

```bash
# on the host — must return 401, not 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8321/

# and with credentials — 200
curl -s -o /dev/null -w '%{http_code}\n' -u "team:$S4PC_ACCESS_PASSWORD" http://127.0.0.1:8321/

# from a laptop on the VPN, NOT through the ALB — must fail/timeout once the SG is scoped
curl -m 5 http://10.35.20.84:8321/
```

The third is the one that proves the SG rule works. Run it and confirm it is refused before
sharing the hostname — if it succeeds, the ALB is decorative and the password is the only
control.

## 5. Known gaps to close after it is up

- **Basic auth is a shared credential.** Everyone logs in as `team`, so
  `run.json.human_approvals` cannot say *who* approved a checkpoint. For a POC that is
  acceptable; for anything audited it is not. The fix is ALB **authenticate-oidc** against
  Entra ID, which injects `x-amzn-oidc-identity` for the app to record. That needs an Entra
  app registration — usually the organisationally slow item, which is why it is not in §2.
- **Concurrency is capped, not tuned.** `pipeline_start()` now refuses a new run with **429**
  once `S4PC_MAX_CONCURRENT_RUNS` (default **2**) runs are in flight. That bounds the `claude -p`
  processes competing with the resident FAISS index, but 2 is an estimate, not a measurement —
  raise it via the env var after resizing and watch `pm2 list` for memory restarts under real
  load. Checkpoint continuations are intentionally exempt: a run in flight is committed work.
  Three refusal codes now distinguish themselves — **409** same FD already running, **429** host
  at capacity, **503** MCP governance server unavailable.
- **Per-agent brain keys.** `S4PC_API_KEYS` supports `name:secret:tools`, but one shared
  `poc-shared` key is in use, so the audit log cannot attribute calls. Issue one key per
  caller before onboarding a second agent.
