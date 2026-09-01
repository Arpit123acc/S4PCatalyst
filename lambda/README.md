# S4PC unified MCP → AWS Lambda (serverless, no tunnel)

The long-term home for the merged governance + brain MCP server: **API Gateway (HTTPS)
→ Lambda → Aurora (pgvector) + Bedrock**. No SSH tunnel, no always-on EC2 serving,
pay-per-use. Claude Code still registers **one** server under the enterprise-allowlisted
name `context7` — only the URL changes (policy checks the name, not the URL).

```
context7  ──HTTPS──►  API Gateway (HTTP API)  ──►  Lambda: lambda_function.lambda_handler
                                                     │  reuses server.handle_request()
                                                     ├─► Bedrock Titan   (IAM role, no keys)
                                                     ├─► Aurora + pgvector  (BRAIN_BACKEND=pgvector)
                                                     ├─► governance tools  (catalog.db, read-only)
                                                     └─► SAP tenant        (Secrets Manager, live mode)
```

**This does not block the POC.** Phases 1–2 run entirely alongside the live EC2 + tunnel
server; nothing is cut over until Phase 4 flips one registration URL, and rollback is one
command. Build the Lambda **on the EC2 (Linux) box** — `psycopg2-binary` ships a manylinux
wheel that must match the Lambda x86_64 runtime.

---

## Files here

| File | What it is |
|---|---|
| `lambda_function.py` | Thin adapter: API Gateway HTTP API v2 event → `server.handle_request()` → JSON/SSE. No tool logic duplicated. |
| `template.yaml` | SAM: Lambda + HTTP API + IAM + VPC wiring. (Aurora is provisioned separately — see Phase 1.) |
| `build.sh` | Assembles `build/` (handler + `mcp-server/` + `scripts/` + psycopg2). Run on Linux. |
| `requirements.txt` | `psycopg2-binary` only — **no faiss on Lambda**; boto3 is in the runtime. |

---

## Phase 1 — Aurora + pgvector (EC2 keeps serving)

1. **Aurora Serverless v2 (PostgreSQL)** in the same VPC as EC2. Min capacity 0.5 ACU
   (or enable scale-to-zero). Create in the private subnets; note the cluster endpoint.
2. **Security group:** allow inbound `5432` from the Lambda SG (created/known in Phase 2).
3. **Network egress for the Lambda subnets** — the function needs Bedrock + Secrets
   Manager while inside the VPC. Either a **NAT gateway**, or **VPC interface endpoints**
   for `com.amazonaws.<region>.bedrock-runtime` and `com.amazonaws.<region>.secretsmanager`
   (cheaper, no NAT). Without one of these the brain call times out.
4. **Enable pgvector** (once), then store the DSN as a secret:
   ```bash
   psql "host=<cluster-endpoint> dbname=postgres user=<admin>" -c "CREATE EXTENSION IF NOT EXISTS vector;"
   aws secretsmanager create-secret --name s4pc/pgvector-dsn \
     --secret-string '{"host":"<cluster-endpoint>","port":5432,"dbname":"postgres","username":"<user>","password":"<pw>"}'
   ```
5. **Load the vectors into Aurora** from the EC2 box (the existing FAISS index stays live
   in parallel — this only *adds* a second backend):
   ```bash
   BRAIN_BACKEND=pgvector PGVECTOR_DSN="host=<endpoint> dbname=postgres user=<user> password=<pw>" \
     python3.11 scripts/embed_chunks.py
   ```
6. **Parity check** — same query, both backends should return similar top hits:
   ```bash
   BRAIN_BACKEND=faiss    python3.11 scripts/brain_search.py "cutover plan" -k 5
   BRAIN_BACKEND=pgvector PGVECTOR_DSN="..." python3.11 scripts/brain_search.py "cutover plan" -k 5
   ```

## Phase 2 — Build & deploy the Lambda (EC2 still serving)

```bash
cd s4pc            # the repo on EC2
bash lambda/build.sh                         # -> lambda/build/  (Linux wheels)
cd lambda
sam deploy --guided \
  --parameter-overrides \
    VpcSubnetIds=subnet-aaa,subnet-bbb \
    LambdaSecurityGroupIds=sg-lambda \
    DbSecretArn=arn:aws:secretsmanager:...:secret:s4pc/pgvector-dsn-XXXX \
    BrainBackend=pgvector SapMode=offline
```
SAM prints **`McpUrl`** and a ready-to-run **`RegisterCommand`** as stack outputs.

> No Linux box handy? `sam build --use-container` builds the manylinux wheels in Docker,
> then `sam deploy --guided`.

## Phase 3 — Verify the deployed function (still not cut over)

```bash
curl https://<api-id>.execute-api.<region>.amazonaws.com/health         # {"status":"ok","tools":25}

# initialize + tools/list over the wire
curl -s -X POST https://<api-id>.execute-api.<region>.amazonaws.com/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head

# search_brain end-to-end (Bedrock + Aurora)
curl -s -X POST https://<api-id>.execute-api.<region>.amazonaws.com/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_brain","arguments":{"query":"cutover plan"}}}'
```

## Phase 4 — Cut over (the only disruptive step, ~10s, reversible)

```bash
claude mcp remove context7                                   # drop the tunnel registration
claude mcp add --transport http -s user context7 https://<api-id>.execute-api.<region>.amazonaws.com/mcp
```
Then stop the EC2 HTTP server and the SSH tunnel. **Rollback** = re-run the old
`claude mcp add ... http://localhost:3001/mcp` and restart the tunnel — the EC2 server
is unchanged.

## Phase 5 — EC2 as the ingest box (optional)

EC2 no longer serves MCP; keep it only to run `sharepoint_ingest.py` + `embed_chunks.py`
(with `BRAIN_BACKEND=pgvector`) on a schedule, writing new vectors into Aurora. Azure AD
`GRAPH_*` creds stay as EC2 env vars. Retiring EC2 entirely (scheduled-ingest Lambda/Batch)
is a later, separate step.

---

## `record_experience` write path — wired to Aurora

Lambda's filesystem is read-only, so the SQLite write path can't run there. The Lambda sets
`EXPERIENCE_BACKEND=postgres` (`catalog/experience_pg.py`), so `record_experience` writes to
an `experience` table in the **same Aurora DB** as the vectors (one datastore, same
`PGVECTOR_DSN`). On first use the table auto-backfills from the bundled SQLite seed, so full
delivery history is present from day one. `query_experience` reads the same table. The
local/EC2 POC is unchanged — it defaults to `EXPERIENCE_BACKEND=sqlite` and still dual-writes
`catalog.db` + the git seed.

**Keep the git-tracked `experience_db.json` current** with a nightly export (cron on the EC2
ingest box, or a scheduled Lambda), so teammates still get lessons on `git pull`:

```bash
# export Aurora experience -> the git-tracked seed, then commit
PGVECTOR_DSN="..." python3.11 - <<'PY'
import os, json, sys
sys.path.insert(0, "mcp-server/catalog")
import experience_pg
data = experience_pg.load_experience()
open("mcp-server/catalog/experience_db.json", "w", encoding="utf-8").write(
    json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print("exported", len(data["entries"]), "entries")
PY
```

## Security invariants (unchanged from CLAUDE.md)

- No LLM API keys anywhere — Bedrock via the function's IAM role.
- Postgres DSN and any SAP creds via **Secrets Manager**, fetched at cold start, never in
  the Lambda env config or logs.
- `S4PC_MODE=offline` by default; `live` requires wiring `SAP_*` from a secret + an
  allowlisted service, exactly as the EC2 server.
