# Connect your agent to the S4PC Brain

Attach the S4PC governance brain to any MCP-capable agent. No AWS account, no VPN, no
EC2 access, nothing to install.

You will be sent **two keys** out of band — never in email or chat alongside this document.

---

## 1. Add the server

```bash
claude mcp add --transport http -s user s4pc-brain \
  https://mwv19g5ykk.execute-api.us-east-1.amazonaws.com/Brain/mcp \
  --header "x-api-key: <GATEWAY_KEY>" \
  --header "Authorization: Bearer <BRAIN_KEY>"
```

Or in `.mcp.json`:

```jsonc
{ "mcpServers": { "s4pc-brain": {
    "type": "http",
    "url": "https://mwv19g5ykk.execute-api.us-east-1.amazonaws.com/Brain/mcp",
    "headers": {
      "x-api-key": "<GATEWAY_KEY>",
      "Authorization": "Bearer <BRAIN_KEY>"
    } } } }
```

Both headers are required and they are checked by different layers, which makes failures
easy to read:

| You see | Means |
|---|---|
| `403` | `x-api-key` wrong or missing — the gateway rejected you |
| `401` | `Authorization` wrong or missing — the brain rejected you |
| `429` | Rate limit or daily quota hit — the key is shared, so wait and retry |

## 2. Check it works

```bash
curl -s -X POST https://mwv19g5ykk.execute-api.us-east-1.amazonaws.com/Brain/mcp \
  -H "x-api-key: <GATEWAY_KEY>" \
  -H "Authorization: Bearer <BRAIN_KEY>" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expect a JSON list of 16 tools.

## 3. What you get

**Release governance** — `check_object_release_state`, `search_released_apis`,
`search_released_badis`, `abap_cloud_lint`, `extensibility_advisor`

**Search the corpus** — `semantic_search`, `get_object_graph`, `get_object_usage`,
`find_similar_delivery`, `query_experience`, `get_reference_links`

**Scope items** — `lookup_scope_item`, `scope_item_dependencies`, `get_area_map`

**Diagnostics** — `layer_health`, `guardrails_status`

Read-only. Tools that read files, reach live SAP/BTP systems, or modify the indexes are
not available on this key and will return *"not permitted for this key."* That is by
design, not a fault.

---

## 4. Read the `evidence` field, not just the verdict

**This is the part that matters.** A verdict without its evidence is not an answer, and
misreading it is how a fabricated object name ends up in a deliverable.

| Verdict | `evidence` | What it actually means |
|---|---|---|
| `LIKELY_RELEASED` | `catalog_hit` | **Released.** Backed by a real catalog entry. Cite it, and note "confirm in ADT / api.sap.com". |
| `LIKELY_RELEASED` | `naming_heuristic_only` | **Not established.** Matched a *name pattern* with nothing behind it — `API_TOTALLY_MADE_UP_THING` scores identically to a real API. Design placeholder only. |
| `NOT_AVAILABLE` | `rule` | **Blocker.** BAPI, classical table, enhancement point. Redesign. |
| `NOT_VERIFIED` | — | Offline check can't confirm. Look it up on the authoritative list. **Not** the same as "unreleased". |

For a `naming_heuristic_only` hit: cross-check with `search_released_apis` on the
*business keywords* rather than the name, write it in the deliverable as "name unconfirmed
— verify on api.sap.com", and say plainly if the cross-check returns nothing.

Two more traps:

- **`prior_usage` is not evidence of release state.** It shows this team used the object
  before. An object can appear in ten deliveries and still be unreleased or since
  deprecated. Use it to find precedent worth reading, never to upgrade a verdict.
- **Check `is_current` before quoting a document.** The corpus holds multiple revisions of
  the same artifact — one spec exists as v2.0 through v11.0. `is_current: false` means
  superseded; read the doc named in `superseded_by` instead.

Always name the authoritative source in anything you produce: SAP Business Accelerator Hub
(api.sap.com) for APIs, the SAP Help *Released CDS Views* list, the *List of BAdIs*, and
the tenant's own Custom Logic app.

## 5. Limits and etiquette

- **50 requests/second, burst 100, 10,000/day — shared by the whole POC group.** One key
  serves everyone, so a runaway loop throttles your colleagues too.
- `semantic_search` costs a Bedrock embedding call per request and takes ~5s. The catalog
  tools are instant and free — prefer them when you know the object name.
- Requests time out at 29 seconds.
- Every call is logged with your key's identity.

## 6. Handling the keys

Treat both as production secrets. Don't commit them, don't paste them in chat or tickets,
don't share them outside the POC group.

These are **shared keys** — the same pair for everyone during the POC. That means the
audit log cannot tell who made which call, and a leak means rotating for the whole team
at once. So it matters more, not less, that they stay inside the group. If one leaks, say
so immediately; rotation takes about a minute.

The corpus contains masked learning content. Masking removes PII, not commercial
context, so treat anything the brain returns as confidential.
