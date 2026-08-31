# S4PC Public Cloud Brain — Architecture

The **Public Cloud Brain** is the shared knowledge base behind the S4PC agent fleet:
harvested SAP S/4HANA Cloud Public Edition delivery knowledge (FDs, TDs, workshop
decks, test / cutover / change material) plus authoritative SAP reference data (the
scope-item catalog today; SAP Business Accelerator Hub and others next). It is
designed to grow into a **large, multi-source, multi-platform** knowledge base, so
every layer is pluggable and swappable.

```
┌── SOURCES (connectors) ──┐   ┌── PIPELINE ──────────────┐   ┌── STORE ────────┐   ┌── CONSUMERS ────────┐
│ SharePoint (Graph API)   │   │ ingest → MASK (PII/NER)  │   │ VectorStore     │   │ MCP: s4pc-brain     │
│ SAP scope catalog        │──▶│ → phase/agent classify   │──▶│  ├ FAISS (POC)   │──▶│ CLI: brain_search   │
│ SAP Accelerator Hub *    │   │ → chunk → Titan embed    │   │  ├ pgvector      │   │ import: search()    │
│ SAP Help / Discovery *   │   │   (Bedrock, IAM)         │   │  └ OpenSearch *  │   │ REST (future) *     │
│ …any new connector *     │   └──────────────────────────┘   └─────────────────┘   │ other platforms *   │
└──────────────────────────┘                                                        └─────────────────────┘
                                                        (* = pluggable extension points, ready but not yet wired)
```

## 1. Sources — a pluggable connector model

A **connector** turns some source into a stream of `(text, metadata)` records. Every
record is tagged with a `source_system` so many sources coexist in one brain and are
independently filterable. Connectors feed the *same* downstream pipeline.

| Connector | `source_system` | Status | Mechanism |
|---|---|---|---|
| SharePoint | `sharepoint` | **live** | Microsoft Graph API (delegated) or local upload — `scripts/sharepoint_ingest.py` |
| SAP scope catalog | `sap_scope_catalog` | **live** | Read directly from `mcp-server/catalog/scope_items.json` — `embed_chunks.load_scope_items()` |
| SAP Business Accelerator Hub | `accelerator_hub` | planned | api.sap.com released APIs/CDS/events (the governance server already seeds this catalog) |
| SAP Help / Best Practices | `sap_help` | planned | phase methodology, released-object docs |
| SAP Discovery Center | `discovery_center` | planned | BTP service + pricing reference |
| SAP Community / notes | `community` | planned | curated how-to knowledge |

**To add a source:** write a loader that yields `(text, {"source_system": "<name>", …})`
and register it in `embed_chunks.py`'s source list. No change to the store, search, or
MCP layers. PII masking (`sharepoint_ingest.mask()`) is reused by any connector that
carries client-derived text; authoritative SAP sources (Hub, Help) need no masking.

## 2. Pipeline — ingest, mask, classify, chunk, embed

1. **Extract** text (`.docx/.pdf/.pptx/.xlsx/.txt/.md`).
2. **Mask** — hybrid PII masking: regex for structured PII (credentials, email, IP,
   phone, transport, employee IDs, financials) + **spaCy NER** for person/unknown-org
   names, with an SAP business-vocabulary allowlist + never-mask list so SAP content is
   never destroyed. NER runs in memory-bounded windows for large documents.
3. **Classify** — SAP Activate **phase** (Discover/Prepare/Explore/Realize/Deploy/Run),
   **agent role** (PMO, Security, Solution Confirmation, Functional, Build, Data, QE,
   Change/Talent, Deployment, Run Support), **deliverable type**, **content type** —
   from folder structure or filename keywords.
4. **Chunk** — ~512-word windows with overlap.
5. **Embed** — Amazon Bedrock **Titan Text Embeddings v2** (1024-dim, L2-normalized) via
   the EC2 IAM instance profile — **no API keys**. Batched inserts scale to huge corpora.

## 3. Store — swappable vector backend

All embedding and search go through one interface (`scripts/vectorstore.py`), selected
by `BRAIN_BACKEND`. Nothing above the store changes when you swap it.

| Backend | When | Notes |
|---|---|---|
| **FAISS** (`faiss`) | POC / single node | File-based `IndexFlatIP` (exact cosine). Zero infra. Default. |
| **pgvector** (`pgvector`) | Production / shared / scale | Postgres/RDS + `vector` extension. SQL metadata filters; add an HNSW index for ANN at scale. Multi-writer, durable, backed up. Matches the AWS architecture (RDS pgvector). |
| **OpenSearch** *(planned)* | Very large / hybrid search | Managed ANN + keyword hybrid; slots in as another `VectorStore`. |

Switch with one env var — e.g. once RDS is provisioned:
`export BRAIN_BACKEND=pgvector PGVECTOR_DSN=postgresql://…` then re-run `embed_chunks.py`.

## 4. Consumers — pluggable into other platforms

The brain is exposed through stable interfaces so any platform can consume it:

| Interface | For | Entry point |
|---|---|---|
| **MCP tool** `search_brain` | Claude Code agents / the pipeline | `mcp-server/brain_server.py` (registered in `.mcp.json` as `s4pc-brain`) |
| **CLI** | humans, scripts, CI | `python3.11 scripts/brain_search.py "<query>" --phase … --source …` |
| **Python import** | in-process callers (webapp, other tools) | `from brain_search import search` |
| **REST** *(planned)* | external platforms / non-Python apps | thin HTTP wrapper over `search()` — the natural next plug-in point |

The governance server (`s4pc`) stays **offline / pure-stdlib** and adds deterministic
**scope-item** intelligence from the same catalog: `lookup_scope_item` and
`scope_item_dependencies` (with a retired-item guard). The brain server (`s4pc-brain`)
owns the network/embedding path. Clean separation, both registered side by side.

## 5. Scalability path

| Dimension | POC (now) | Production (ready to switch on) |
|---|---|---|
| Vector store | FAISS file on EBS | RDS **pgvector** (Multi-AZ), HNSW index |
| Documents | thousands of chunks | millions — batched embed, ANN index |
| Compute | one EC2 | ASG / per-client isolation; shared store |
| Sources | SharePoint + scope catalog | + Accelerator Hub, Help, Discovery, Community |
| Persistence | EBS (survives stop/start) | RDS + S3 (durable, shared, versioned) |
| Access | MCP + CLI | + REST for other platforms |

## 6. Security

- **PII masking at ingest** — client/person/contact/credential/infra data is masked
  before anything is embedded or stored.
- **No LLM/API keys** — Bedrock (Titan embeddings + Claude inference) via the EC2 IAM
  instance profile only.
- **Never published** — `brain/` (client raw docs, masked chunks, embeddings, ingest
  logs, SharePoint OAuth token cache) is git-ignored. Only the public SAP scope catalog
  (`mcp-server/catalog/scope_items.json`) is tracked.
- **Auditable** — the governance MCP server logs tool calls to `mcp-server/logs/`.
