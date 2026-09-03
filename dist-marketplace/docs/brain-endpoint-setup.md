# Publishing the Brain as an authenticated HTTPS endpoint (interim path)

**Status:** draft for review — nothing here has been provisioned.
**Purpose:** remove the SSH-tunnel dependency so any MCP-capable agent, in any environment, can
reach the brain. This is the interim path that does **not** wait on the Lambda/Aurora migration in
[infra-request.md](infra-request.md).

---

## 1. The gap, stated precisely

| | Today | Needed |
|---|---|---|
| Address | `10.35.20.84:3002` — private RFC1918 | public DNS name |
| Transport | plain HTTP over an SSH tunnel | TLS |
| Reachability | Accenture VPN + `plink` on each client machine | any internet client |
| Authentication | **none** — anyone who reaches the port gets all 25 tools | per-caller credential |
| Rate limiting | none | per-caller quota |
| Audit | tool calls logged locally | caller identity in the log |

The single blocking item is the first row. But note row four: **the endpoint has no
authentication today.** The SSH tunnel is currently doing double duty as the network path *and*
the access control. Publishing the endpoint without adding auth first would remove the only
control that exists.

## 2. The key structural decision: split auth from networking

These two halves are independent, and conflating them is what makes this look blocked:

- **Authentication is application-layer.** It needs no AWS resources, no cloud-team ticket, and no
  approval to start. It also travels — the same code protects the endpoint behind a tunnel today,
  behind an ALB next month, and inside a client's own AWS account later.
- **Network reachability needs the cloud team**, whichever front door is chosen.

**So do §3 now, and raise §4 in parallel.** §3 is also worth doing on its own merits: it closes an
open hole regardless of whether the endpoint is ever published.

---

## 3. Step 1 — application-layer authentication — **IMPLEMENTED 2026-09-03**

Three changes landed in `mcp-server/server.py`, all verified (10/10 test cases) before deploy.

### 3.1 API-key authentication

Set `S4PC_API_KEYS` to enable. **Unset means auth is disabled**, which keeps the loopback +
tunnel setup and the pipeline's stdio path working untouched — so this merged without a cutover.

```
S4PC_API_KEYS = "name:secret[:tool,tool,...];name2:secret2"
```

Entries `;`-separated, fields `:`-separated, the optional tool allowlist `,`-separated. Accepts
`x-api-key: <secret>` or `Authorization: Bearer <secret>`; `401` + `WWW-Authenticate: Bearer` on
failure. Comparison is `hmac.compare_digest` against every entry with no early exit, so timing
does not leak which key was close.

### 3.2 Per-key tool allowlist

Omitting the allowlist grants all 25 tools, so **a client-facing key should always carry one** —
several tools read files or reach SAP/BTP. A restricted key gets `403` on a tool outside its list,
and `tools/list` is filtered so it never even sees `btp_deploy`.

### 3.3 Caller identity in the audit trail

`audit()` now records a `caller` field on every event, resolved from the key via a thread-local
(the HTTP transport is threaded). stdio leaves it unset and it reads `local`, which is accurate —
that is a same-host process, not a network caller.

### Still to settle before a client key is issued

| Decision | Recommendation |
|---|---|
| Key storage | **AWS Secrets Manager** via the existing IAM role. Env var is acceptable interim; never a file in the repo. |
| Key format | 32+ bytes from `secrets.token_urlsafe(32)`, prefixed per tenant (`s4pc_acn_…`) so a leaked key is traceable. |
| Rotation | Two entries per tenant during overlap — the format already allows it. |
| Tenant scoping | **Not built.** Map each key to a `source_system` / namespace filter injected into every search. Prerequisite before a second client touches the endpoint. |

## 3b. Path containment — **IMPLEMENTED 2026-09-03**

`file_probe` and `extract_docx` took a caller-supplied path, ran `os.path.abspath()` with no
containment, and returned file contents — `extract_docx` returns full text. Both now resolve
through `_safe_read_path()`, which `realpath()`s first (so a symlink inside a root cannot point
out of it, and `..` cannot climb out) and confines reads to `input/` and `output/`. Widen
deliberately with `S4PC_FILE_ROOTS` (`os.pathsep`-separated); denials are audited as `path_denied`.

This is deliberately **not** the repo root: `brain/` holds the SharePoint OAuth token cache, and a
containment boundary that includes it is not a boundary worth having.

**Order matters:** containment had to land with — not after — the key layer, or an issued key
would have granted arbitrary file read.

## 3c. Current production posture

Auth is **implemented but disabled** on the delivery host, because 3002 is loopback-only and the
pipeline reaches the server over stdio. Enabling keys there today would add no protection and
would break the local `context7` registration until it was re-registered with a header.

To make the dangerous combination impossible to miss, the server now logs a boxed warning and
writes an `insecure_bind` audit event when it binds a non-loopback address with no keys set.
**Set `S4PC_API_KEYS` in the same change that moves the bind off loopback — never after.**

## 4. Step 2 — the front door

### Option A — API Gateway HTTP API + VPC Link ← recommended

```
agent ──HTTPS──▶ API Gateway HTTP API ──VPC Link──▶ internal ALB ──▶ EC2:3002
                 (throttling, access logs)                          (auth in-app, §3)
```

**Why this one:** API Gateway issues a working HTTPS URL immediately —
`https://{api-id}.execute-api.us-east-1.amazonaws.com` — with an AWS-managed certificate. **No ACM
certificate, no Route 53 record, no domain request.** Certificate and DNS are usually the longest
poles in an enterprise network ticket, and this removes both. A branded name can be added later
without changing anything an agent is configured with beyond the URL.

Throttling and access logging come built in, so §3 only has to carry identity, not rate limits.

Ask the cloud team for:

| # | Item | Why |
|---|---|---|
| 1 | Two subnet IDs the EC2 instance already sits in | VPC Link placement |
| 2 | A security group for the VPC Link ENIs | source for the EC2 ingress rule |
| 3 | Ingress on EC2's SG: TCP 3002 **from item 2 only** | keeps 3002 closed to everything else |
| 4 | Permission to create an **internal** ALB in those subnets | HTTP API private integration target |

This is a materially smaller ask than the Lambda migration — no new VPC, no NAT, no database, no
secret ARN — and items 1–3 are facts about infrastructure that already exists.

### Option B — API Gateway REST API + NLB

Choose this only if native API-key + usage-plan management is a hard requirement and the
application-layer scheme in §3 is rejected. REST API VPC Link requires a **Network** Load Balancer
specifically. More moving parts, and its 29-second integration timeout is tighter than HTTP API's
30 — see §6.

### Option C — public ALB + ACM certificate + custom domain

The right **end state** for a branded, client-facing URL (`https://brain.accenture.com/mcp`), and
the natural home for WAF rules and OIDC. Not the interim path: it needs public subnets, a
certificate request, a DNS record and a WAF policy — four separate cloud-team items.

### Option D — Cloudflare Tunnel / ngrok

Fastest to stand up, and **not appropriate here.** It routes masked client delivery content
through a third party that is not covered by the engagement's data-processing terms. Raise it with
security before considering it, not after.

---

## 5. Step 3 — connecting agents

Once the endpoint is live, every consumer is a configuration change:

```jsonc
// Claude Code — .mcp.json or `claude mcp add`
{ "mcpServers": { "s4pc-brain": {
    "type": "http",
    "url": "https://{api-id}.execute-api.us-east-1.amazonaws.com/mcp",
    "headers": { "x-api-key": "s4pc_acn_…" } } } }
```

The delivery host keeps using the local stdio path — it is on the same box as the index, so
routing it through the internet would add latency and a failure mode for no gain.

**Verify before promising claude.ai support.** Remote MCP servers added through claude.ai's
connector UI are expected to advertise OAuth 2.1 authorization; a static header may not be
configurable there. Claude Code and programmatic SDK clients accept custom headers today. Confirm
the current claude.ai connector requirements against Anthropic's docs before this is committed to
a client — and if OAuth is required, that is a scoped addition to §3, not a redesign.

## 6. Constraints to design against

| Constraint | Impact | Handling |
|---|---|---|
| API Gateway integration timeout: 30s (HTTP API) / 29s (REST) | `search_brain` runs ~1–3s (Titan embed + FAISS), well inside. Heavier tools may not be. | Measure each of the 25 tools; keep long-running ones off the public endpoint. |
| MCP Streamable HTTP responses | The server already returns a single buffered SSE event and closes — no long-lived stream to proxy. | No change; do not add real streaming without re-testing through the gateway. |
| FAISS index is a file on EBS | One node, one writer. Fine for reads at this scale; a second node cannot share it. | Switch `BRAIN_BACKEND=pgvector` when horizontal scale or a shared store is needed — `scripts/vectorstore.py` already implements it. |
| Corpus contains masked client content | Masking removes PII, not commercial context. | Per-key tenant scoping (§3) before a second tenant is admitted. Not optional. |
| Public endpoint + no quota | Bedrock embedding cost is per call. | API Gateway throttling per key, plus a usage alarm. |

## 7. Sequence

1. ~~Implement §3 auth behind the opt-in default.~~ **Done 2026-09-03.**
2. ~~Per-key tool allowlist.~~ **Done.** Tenant scoping still open — required before any client key exists.
3. Raise the four items in §4 Option A with the cloud team.
4. Provision VPC Link + internal ALB + HTTP API; enable throttling and access logs.
5. Issue a first key to one internal consumer; run for a week; check the audit log attributes calls correctly.
6. Move `context7` off the SSH tunnel to the HTTPS URL; retire the startup shortcut.
7. Revisit Option C for a branded domain once the pattern is proven.

## 8. What this does not solve

- **Client-hosted deployment.** A client that wants the brain inside their own account needs the
  stack packaged as IaC (Terraform/CDK) with their own ingest connectors — a separate piece of
  work. This endpoint serves the "client's agents call our brain" model only.
- **Multi-tenant data isolation.** §3 tenant scoping is a filter, not a boundary. One index still
  holds every tenant's content. A client requiring genuine isolation needs a separate index, which
  is an argument for the pgvector backend and per-tenant tables.
- **The Lambda/Aurora migration.** Still the right end state for elastic scale and for removing the
  single EC2 as a point of failure. This shortens the wait; it does not replace it.
