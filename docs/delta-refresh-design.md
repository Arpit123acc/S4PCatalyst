# Delta refresh after an SAP release

How the brain picks up a new S/4HANA Cloud release without re-fetching everything.
Design only — nothing here is built yet. Step 0 must be run before the rest is
committed to, because two field names in it are unverified.

## The problem

`sapbp_catalog.py` fetches all three tiers in full on every run: ~1.9k processes,
~6.2k BOM items, and the URL link table. `sapme_fetch.py` then downloads ~6,650
artifacts from `support.sap.com`, which is behind SAML and needs a browser session
pasted in by a human. A full refresh is therefore not something a cron job can do
unattended — the download is the part that needs a person, and it is also the part
that takes hours.

SAP ships a release roughly quarterly (2602, 2608, 2611). Most artifacts do not
change between them. Re-downloading 6,650 files to pick up a few hundred changed
ones is the whole cost of the refresh for almost none of the value.

## The shape: delta the fetch, keep the embed whole

**Only the fetch becomes incremental. The embed stays a full rebuild.**

This is the central decision and it is worth being explicit about, because the
tempting design — incrementally patching the FAISS index — is materially worse:

- `embed_chunks.py` rebuilds from every chunk file on disk. If the delta fetch
  only rewrites the chunk files whose artifacts changed, the untouched files are
  still there, so a full rebuild sees the complete corpus. No index surgery, no
  id bookkeeping, no partial-state failure mode.
- **Supersession stays correct.** `_superseded_bpd_scope_items()` recomputes the
  superseded set on every run from whatever `sap_best_practices` chunks are
  present. If a delta run ever left a *partial* Best Practices set in place, that
  set would shrink and the 7,052 stale `sap_bpd` chunks it currently suppresses
  would silently return. Keeping the embed whole is what prevents that. Anyone
  later tempted to "optimise" by pruning unchanged chunk files should read this
  paragraph first.
- The per-source publish guard in `embed_chunks.py` keeps working. A delta that
  wrote a partial corpus would trip it, which is correct, but it would trip on
  every run and quickly be disabled with `--allow-shrink`. Guards that cry wolf
  get turned off.

Embedding 179,482 chunks takes ~30 unattended minutes on the delivery host. That
is an acceptable price for eliminating every consistency hazard above. Skipping
re-embedding of unchanged chunks by content hash is a worthwhile later
optimisation, not a starting requirement — measure the Bedrock cost of a full
rebuild first and decide with the number in hand.

## Step 0 — verify the two fields this design rests on (do this first)

`changeCategory` and `LatestSolutionScenarioIds` are named from earlier
exploration of the Process Navigator UI, not from the service contract. If either
is absent or means something else, the design below changes. Confirm against
`$metadata` before building anything:

```bash
curl -s -H "Cookie: $PR_ALM_COOKIE" \
  "https://pr.alm.me.sap.com/ui/earl-pn-ui/v1/odata/v4/EAXService/\$metadata" \
  > /tmp/eax-metadata.xml

grep -o 'EntitySet Name="[^"]*"' /tmp/eax-metadata.xml | sort -u | grep -i -E "latest|scenario"
grep -o 'Property Name="[^"]*"' /tmp/eax-metadata.xml | sort -u | grep -i -E "change|valid|version|release|deprecat"
```

Record what comes back in this file. If `changeCategory` does not exist on the
entities we actually query, fall back to **content hashing**: keep the hash of
each artifact's bytes in the manifest and re-download only when the BOM row's URL
or its `isValid`/`isArchive` flags move. That is weaker (it cannot detect a
same-URL content change without downloading) but it is honest and needs no field
SAP has not promised.

## Step 1 — release detection (the cron trigger)

`DEFAULT_SCENARIO = "5c293206-…"` is hardcoded in `sapbp_catalog.py` and is the
release anchor: a new release should surface as a new scenario id.

```
query LatestSolutionScenarioIds
  → compare against manifest.json["scenario_id"]
     same     → no new release. Exit 0, log one line, do nothing.
     changed  → a release shipped. Proceed to step 2 and record the new id.
```

One cheap request. This is what the stopped `brain-refresh` pm2 job should run on
a schedule, and it is the only part that is safe to run fully unattended — it
reads, compares and reports. It must **not** start a download, because the
download needs a human session anyway.

Also compare `metadata_etag`, which `sapbp_catalog.py` already records. A changed
etag means SAP altered the service contract, and a delta run should then **refuse
and ask for a human**, not carry on: field semantics may have shifted underneath
the filters, and a delta built on a changed contract fails quietly rather than
loudly. The manifest comment already says the service is internal and "may move
without notice" — this is what that warning is for.

## Step 2 — artifact delta

With a confirmed new scenario, re-fetch the three tiers for the new scenario id
(Tier A/B/C are cheap OData calls, not downloads) and diff BOM rows against the
previous `bom_manifest.json`:

| change | action |
|---|---|
| new `id` | download |
| same `id`, changed `url` | download, replace chunks |
| same `id`, `changeCategory` = Update | download, replace chunks |
| same `id`, `changeCategory` = In_Deprecation | **keep, flag `deprecated: true`** |
| absent from the new manifest | **mark deprecated, do not delete** |
| otherwise | skip — this is the saving |

**Deprecation is not deletion, and neither is it nothing.** A delta that only ever
adds leaves the brain serving withdrawn content indefinitely, which is the exact
failure `check_object_release_state` treats as a blocker: per CLAUDE.md a retired
catalog hit means the *opposite* of released. But deleting outright loses the only
record of how something used to work, which the corpus is often the last place to
hold. So mark, keep, and let retrieval demote — the same call the existing
supersession logic makes, and `_demote_superseded` already has the machinery.

Carry the flag into chunk metadata so `search_brain` can surface it the way it
surfaces `is_current`, and so `lookup_accelerator` can report it next to
`scope_item_retired`.

## Step 3 — download, with a human in the loop

Unchanged: `sapme_fetch.py` with a pasted session cookie, now given only the delta
list via its existing `--match` flag. This is the step that cannot be automated,
because `support.sap.com` is behind SAML and the project holds no service
credentials by design.

Expect a few hundred files rather than 6,650 — minutes instead of hours, which is
what makes the refresh something a person will actually do on release day.

## Step 4 — ingest, embed, verify

```bash
python3.11 scripts/sapme_ingest.py                     # changed artifacts only
python3.11 scripts/embed_chunks.py                     # FULL rebuild, see above
python3.11 scripts/brain_regression.py                 # 43/43 expected
```

Read the per-source table `embed_chunks.py` now prints. A source that moved
unexpectedly is the signal to stop — that table exists because a third of the
SharePoint source went missing for two weeks without anyone noticing.

Then re-record the baseline if the gate moved for a reason you can name, and
`pm2 restart s4pc-mcp`.

## What is deliberately not automated

The download needs a human SAML session, so "one-click refresh from the UI" cannot
mean unattended. What the UI button can honestly do is **step 1**: check whether a
new release exists and report it. The human then supplies a cookie and runs the
rest. Anything promising more would either need stored SAP credentials — which the
project explicitly does not hold — or would silently fail on the day the session
expires, which is the worst possible failure for a job nobody is watching.
