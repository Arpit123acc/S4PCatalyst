# Delta refresh after an SAP release

How the brain picks up a new S/4HANA Cloud release without re-fetching everything.

**Built 2026-09-24** as `scripts/sapbp_delta.py`, against a service contract
verified on the live EAXService rather than inferred. This document now describes
what exists; the runbook is at the end.

## The problem

`sapbp_catalog.py` fetches all three tiers in full on every run: ~657 processes,
6,204 BOM items and 6,234 URL rows. `sapme_fetch.py` then downloads ~6,650
artifacts from `support.sap.com`, which is behind SAML and needs a browser session
pasted in by a human. A full refresh is therefore not something a cron job can do
unattended — the download is the part that needs a person, and also the part that
takes hours.

SAP ships roughly quarterly. **77% of solution processes are `No Change` between
releases** (measured: 614 of 800 sampled), so most of that cost buys nothing.

## The shape: delta the fetch, keep the embed whole

**Only the fetch becomes incremental. The embed stays a full rebuild.**

The tempting alternative — incrementally patching the FAISS index — is materially
worse, for three reasons that are all load-bearing:

- `embed_chunks.py` rebuilds from every chunk file on disk. If the delta fetch only
  rewrites the chunk files whose artifacts changed, untouched files are still
  there, so a full rebuild sees the whole corpus. No index surgery, no id
  bookkeeping, no partial-state failure mode.
- **Supersession stays correct.** `_superseded_bpd_scope_items()` recomputes the
  superseded set every run from whatever `sap_best_practices` chunks are present.
  A delta that left a *partial* Best Practices set would shrink that set and
  silently resurrect the 7,052 stale `sap_bpd` chunks it currently suppresses.
  Keeping the embed whole is what prevents that. Anyone later tempted to "optimise"
  by pruning unchanged chunk files should read this paragraph first.
- The per-source publish guard keeps working. A delta writing a partial corpus
  would trip it on every run, and a guard that cries wolf gets disabled.

Embedding 179,482 chunks takes ~30 unattended minutes. That is an acceptable price
for removing every consistency hazard above. Skipping unchanged chunks by content
hash is a later optimisation — measure the Bedrock cost of a full rebuild first.

## The service contract (verified 2026-09-22)

`GET {SERVICE}/$metadata` → 200, 1.3 MB, 343 entity sets, 480 distinct properties.

### Release identity — `LatestSolutionScenarioIds`

```json
{"ID": "5c293206-…", "targetRelease": "2608", "stableId": "EARL_SolS-013",
 "internalVersion": "11.4", "seq": 1}
{"ID": "cfbe71c4-…", "targetRelease": "2602", "stableId": "EARL_SolS-013",
 "internalVersion": "10.8", "seq": 2}
```

`stableId` survives releases; **the GUID is per release**. `seq` orders them, 1
being current. This is why `DEFAULT_SCENARIO` being a hardcoded GUID was a live
defect and not merely a delta concern: once 2611 ships, the pinned GUID keeps
fetching 2608 successfully, with no error and no empty result. `resolve_scenario()`
now looks the GUID up by `stableId` at runtime, keeping the constant as an offline
fallback and printing loudly when the resolved GUID differs from it.

Watch `internalVersion` as well as `targetRelease`: it moves *within* a release
(11.4 against 10.8), so content can change without the release number changing. A
refresh watching only the release would sit on a stale corpus until the next
quarter. Both are now recorded in `manifest.json`.

### Process-level change — `SolutionProcess.changeCategory`

Present on `SolutionProcess` and 25 related types. **Six values, not three:**

| value | share of an 800-row sample |
|---|---|
| `No Change` | 614 |
| `Update` | 113 |
| `New` | 44 |
| `In_Deprecation` | 20 |
| `Addition,Upgrade` | 5 |
| `Retired` | 4 |

Two traps here, both found only by looking:

- **`Addition,Upgrade` is comma-compound.** `$filter=changeCategory eq 'Update'`
  misses it — the same multi-value defect fixed in the phase filter on the same
  day. Treat the field as a list.
- **Filter negatively.** Use `changeCategory ne 'No Change'`, never a positive
  enumeration of the five. An enumeration built from the three categories this
  document originally assumed would have silently dropped `Retired` and
  `Addition,Upgrade` — and `Retired` is precisely the one that must not be missed,
  because it is the signal to stop serving something. A negative filter also
  survives SAP adding a seventh value.

### Accelerator-level change — `BomItemUrl.contentReleaseVersion_ID`

`changeCategory` is **not** on any BomItem type, so accelerators have no change
category. What they have is better: `BomItemUrl` carries
`contentReleaseVersion_ID`, a stable id per release of the artifact.

```json
{"bomItem_ID": "0003f2ed-…", "country_ID": "AE", "language_ID": "EN",
 "url": "…/5I2_S4HANA2025-FPS0_BPD_EN_AE.xlsx",
 "contentReleaseVersion_ID": "86d390c4-…"}
```

That is the accelerator delta key: a BOM item whose `contentReleaseVersion_ID`
differs from the recorded one has new content behind the same row. It is also
strictly better than `accelerator_lookup._release()`, which parses the release out
of the filename with a regex — keep the regex for display, prefer this for change
detection.

## Step 1 — release detection (the cron trigger)

```
query LatestSolutionScenarioIds?$filter=stableId eq 'EARL_SolS-013'&$orderby=seq
  take seq == 1, compare targetRelease + internalVersion against manifest.json
     both same → nothing to do. Log one line, exit 0.
     changed   → proceed to step 2.
```

One cheap request, and the only part safe to run fully unattended. It must **not**
start a download: that needs a human session.

Compare `metadata_etag` too, which `sapbp_catalog.py` already records. A changed
etag means SAP altered the contract, and a delta run should then **refuse and ask
for a human** — field semantics may have shifted under the filters, and a delta on
a changed contract fails quietly rather than loudly.

## Step 2 — artifact delta, as built

The delta is computed by **diffing two manifest snapshots**, not by filtering at
the OData layer. `sapbp_catalog.py` keeps the previous run as
`bom_manifest.prev.json`; `sapbp_delta.py` compares it to the current one.

That choice matters. Filtering server-side would have meant trusting
`changeCategory` semantics we had only just discovered — including the
comma-compound `Addition,Upgrade` — on a service whose own manifest comment
says it "may move without notice". Diffing snapshots needs no such trust: a
field changed or it did not.

| class | what the fetcher does | needs `--apply`? |
|---|---|---|
| **new** — id absent from the previous run | fetched anyway; absent from the state | no |
| **content_changed** — same URL, new `contentReleaseVersion_ID` | **skipped forever** unless its state entry is cleared | **yes** |
| **url_changed** — new URL for a known id | fetched anyway; the new URL is absent from the state | no |
| **retired** — id gone from the catalogue | flagged, never deleted | n/a |
| unchanged | skipped — the saving | no |

Only `content_changed` needs intervention, and it is the entire reason this
exists: the URL is stable across releases while the file behind it changes, so
the fetch state reports "already have it" and the corpus holds the old release.
Clearing the whole state would also work, and would re-download 13,600 files.

**Deprecation is not deletion, and neither is it nothing.** A delta that only adds
leaves the brain serving withdrawn content indefinitely, which CLAUDE.md treats as
a blocker: a retired catalog hit means the *opposite* of released. But deleting
outright loses the only record of how something used to work, which the corpus is
often the last place to hold. So mark, keep, and let retrieval demote — the call
the supersession logic already makes, and `_demote_superseded` has the machinery.

Carry the flag into chunk metadata so `search_brain` surfaces it the way it
surfaces `is_current`, and so `lookup_accelerator` can report it next to
`scope_item_retired`.

## Step 3 — download, with a human in the loop

Unchanged: `sapme_fetch.py` with a pasted session cookie, given only the delta list
via its existing `--match` flag. Cannot be automated — `support.sap.com` is behind
SAML and the project holds no service credentials by design.

Expect a few hundred files rather than 6,650: minutes instead of hours, which is
what makes this something a person will actually do on release day.

Note the two sessions are **different**: `connect.sid` for `pr.alm.me.sap.com`
(steps 1–2) and `SUPPORT_IDS_PROD` for `support.sap.com` (step 3). A `me.sap.com`
portal cookie authenticates neither and returns a clean 401 on the OData service.

## Step 4 — ingest, embed, verify

```bash
python3.11 scripts/sapme_ingest.py                     # changed artifacts only
python3.11 scripts/embed_chunks.py                     # FULL rebuild, see above
python3.11 scripts/brain_regression.py                 # 43/43 expected
```

Read the per-source table `embed_chunks.py` prints. A source that moved
unexpectedly is the signal to stop — that table exists because a third of the
SharePoint source went missing for two weeks without anyone noticing.

Re-record the baseline only if the gate moved for a reason you can name, then
`pm2 restart s4pc-mcp`.

## Runbook

```bash
# 1. has SAP shipped anything? one request, safe unattended (needs pr.alm cookie)
python3.11 scripts/sapbp_delta.py --check        # exit 0 = no change, 1 = something moved

# 2. if it moved — refresh the catalogue (keeps the previous snapshot automatically)
python3.11 scripts/sapbp_catalog.py

# 3. see what changed, offline, no cookie
python3.11 scripts/sapbp_delta.py

# 4. queue the republished documents for re-download
python3.11 scripts/sapbp_delta.py --apply

# 5. fetch (support.sap.com cookie), then rebuild
python3.11 scripts/sapme_fetch.py --source sapbp
python3.11 scripts/sapme_ingest.py
python3.11 scripts/sapbp_build_process_index.py
python3.11 scripts/sapbp_build_flow_chunks.py
python3.11 scripts/embed_chunks.py
python3.11 scripts/brain_regression.py
pm2 restart s4pc-mcp
```

Step 1 is the only one a cron should run. It reports; it never starts a download
that needs a human session.

## What is deliberately not automated

The download needs a human SAML session, so "one-click refresh" cannot mean
unattended. What a UI button can honestly do is **step 1**: check whether a new
release exists and report it. The human then supplies a cookie and runs the rest.
Anything promising more would need stored SAP credentials — which this project
explicitly does not hold — or would fail silently the first time a session expired,
on a job nobody is watching.
