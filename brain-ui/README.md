# Brain Explorer — a visualisation UI for the Public Cloud Brain

A **standalone**, read-only window onto the S4PC Public Cloud Brain: what is in the corpus, how it
is classified, and what a `search_brain` retrieval actually returns. Built to be shown to a client
or a leadership audience without exposing any pipeline control surface.

```
python3.11 brain-ui/server.py            # http://127.0.0.1:8400
python3.11 brain-ui/server.py --port 9000
```

## Why it is not part of `webapp/`

`webapp/app.py` owns the delivery pipeline, its three human checkpoints and the MCP preflight
gate. Bolting a demo surface onto it would put a read-only viewer on the same process as the
approval workflow — one crash, one blast radius, and anyone shown the brain would also be shown a
"start pipeline" button. This runs as its own PM2 service on its own port and shares nothing with
the pipeline but the index files on disk.

## What it shows

| Panel | Source |
|---|---|
| Documents · chunks · index size · scope items | aggregated from `brain/index/metadata.json` |
| Chunks by source system / phase / agent role / deliverable type | same metadata, faceted |
| **Ask the brain** — live semantic search | `scripts/brain_search.search()` — the same call path the `search_brain` MCP tool uses |
| How an agent connects | static; the MCP registration snippets |

The search panel is the honest part of the demo: it embeds the query with Bedrock Titan and runs
the real cosine search over the real index, so what an audience sees is exactly what an agent gets.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/` | the UI |
| `GET` | `/health` | liveness |
| `GET` | `/api/stats` | corpus composition; cached after first build |
| `POST` | `/api/search` | `{query, top_k?, phase?, agent_role?, deliverable_type?, source_system?, dedup?}` |

`/api/stats` needs only the metadata file, so the composition view still renders on a host without
`boto3`. Search needs `boto3` + the vector backend and returns a readable error without one.

## Running it on the delivery host

It is already registered with PM2 on the EC2 host:

```bash
pm2 start 'python3.11 brain-ui/server.py --port 8400' --name brain-ui --cwd /home/ec2-user/s4pc
pm2 save
```

The host is on a private IP, so reach it over the SSH tunnel:

```
plink -i <key>.ppk -N -batch -L 8400:localhost:8400 ec2-user@10.35.20.84
```

then open <http://localhost:8400>. To make 8400 come up with the other ports at login, add
`-L 8400:localhost:8400` to the `S4PC-Brain-Tunnel` shortcut in the Windows Startup folder.

## Before you expose it beyond localhost

It binds `127.0.0.1` by default and **has no authentication**. `--host 0.0.0.0` is for putting it
behind something that terminates TLS and authenticates — see
[docs/brain-endpoint-setup.md](../docs/brain-endpoint-setup.md). Two things to weigh first:

- Search results contain **masked client delivery content**. Masking removes PII, not commercial
  context — document titles and body text still identify programmes.
- There is no per-tenant filter yet. Anyone who can reach it can query the whole corpus. Namespace
  isolation is a prerequisite for showing it to more than one client.

## Design notes

Charts follow the project's data-visualisation rules: bar length carries magnitude so bars are a
single blue hue, the three source-system colours are categorical slots 1–3 (validated all-pairs,
both modes), every chart has a table view, and dark mode is a selected set of steps rather than an
inverted palette.
