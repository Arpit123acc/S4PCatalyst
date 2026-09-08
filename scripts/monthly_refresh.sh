#!/usr/bin/env bash
# Monthly refresh of the Public Cloud Brain.
#
# WHY THIS EXISTS
#   api.sap.com publishes new released APIs continuously, and SAP rewrites the UI5 /
#   CAP / Node documentation on its own cadence. A brain that was accurate at build
#   time drifts into being confidently wrong, which is this project's most expensive
#   failure mode -- an out-of-date release verdict reads exactly like a current one.
#
# WHY IT GATES ON THE REGRESSION SET
#   A refresh is a retrieval change, so it can silently make the brain worse. The
#   sequence below therefore ends by running brain-tests, and a FAIL is reported
#   loudly instead of being left for someone to notice in a deliverable weeks later.
#   The index publish is atomic and keeps a .prev, so a bad refresh is recoverable.
#
# WHY IT REBUILDS L1 AND L2 TOO
#   The catalog sync changes the released-object catalog, and the object graph (L1) and
#   the semantic index (L2) are DERIVED from it -- L2 also indexes the experience
#   lessons (L3). A refresh that updated only the brain corpus left those two behind,
#   which is how L2 came to be 3 lessons short of L3 with nothing reporting it. A
#   derived store nobody rebuilds is a store that is confidently wrong.
#
# ORDER MATTERS
#   catalog sync first (cheap, independent), then the two stores derived from it
#   (L1 graph, then L2 -- L2 indexes L1's objects), then the doc harvest (network, no
#   embedding cost on failure), then the vector rebuild (expensive), then the
#   keyword index (cheap, but must match the vector corpus), then the MCP restart
#   (the server caches the catalog and every index in memory -- without this, none
#   of the above is visible to a running agent), then the gates.
#
#   Both indexes publish atomically and keep a .prev, so a bad refresh is
#   recoverable: brain/index/faiss.index.prev + metadata.json.prev + keyword.db.prev.
#
# USAGE
#   bash scripts/monthly_refresh.sh              # full refresh
#   bash scripts/monthly_refresh.sh --dry-run    # show what would run, change nothing
#
#   Scheduled via PM2 (deploy/ecosystem.config.js, app "brain-refresh").
#   Log: logs/monthly_refresh.log

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"
PY=python3.11
LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="1"

say() { echo "[$(date -u +%H:%M:%S)] $*"; }
fail_steps=""

run() {                    # run <label> <cmd...>
  local label="$1"; shift
  say "── $label"
  if [ -n "$DRY" ]; then
    say "   DRY RUN, would execute: $*"
    return 0
  fi
  if "$@"; then
    say "   ok: $label"
  else
    say "   FAILED: $label (exit $?)"
    fail_steps="$fail_steps $label"
    return 1
  fi
}

say "=== brain monthly refresh $STAMP (repo: $REPO)"
[ -n "$DRY" ] && say "=== DRY RUN — nothing will be written"

# ── Resolve the Hub API key without ever writing it to disk ──────────────────
# A shell export cannot serve an unattended monthly job: it dies with the shell,
# and there is no login shell at 03:30 on the 3rd. Putting it in ~/.bashrc, an
# EnvironmentFile or the PM2 ecosystem file would all mean a secret in a file,
# which this project does not do.
#
# The instance role can already read SSM Parameter Store (verified 2026-09-04:
# ssm:GetParameter returns ParameterNotFound, not AccessDenied), so the key is
# fetched at run time over IAM and lives only in this process's memory -- the same
# posture as Bedrock, which uses the role and no key at all.
#
# Create the parameter ONCE, from somewhere with ssm:PutParameter (the EC2 role
# deliberately does not have it):
#   aws ssm put-parameter --name /s4pc/sap_hub_api_key --type SecureString \
#       --value '<key from api.sap.com>' --region us-east-1
#
# NOTE for SecureString: reading it also needs kms:Decrypt on alias/aws/ssm. If
# that is missing this step reports a skip rather than failing the refresh, so
# check the first run rather than discovering it a month later.
HUB_KEY_PARAM="${HUB_KEY_PARAM:-/s4pc/sap_hub_api_key}"
if [ -z "${SAP_HUB_API_KEY:-}" ]; then
  # stderr suppressed so a failure cannot echo the parameter path or value anywhere.
  SAP_HUB_API_KEY=$(aws ssm get-parameter --name "$HUB_KEY_PARAM" --with-decryption \
                      --query Parameter.Value --output text 2>/dev/null) || SAP_HUB_API_KEY=""
  [ "$SAP_HUB_API_KEY" = "None" ] && SAP_HUB_API_KEY=""
  export SAP_HUB_API_KEY
  [ -n "$SAP_HUB_API_KEY" ] && say "── hub key resolved from SSM $HUB_KEY_PARAM"
fi

# 1. Released-object catalog from the SAP Business Accelerator Hub.
#    Skipped rather than failed when the key is absent, because the doc refresh
#    below is still worth doing without it.
if [ -n "${SAP_HUB_API_KEY:-}" ]; then
  run "catalog sync (api.sap.com)" $PY mcp-server/catalog/sync_hub.py || true
else
  say "── catalog sync SKIPPED: no Hub API key."
  say "   Not in the environment, and SSM $HUB_KEY_PARAM did not return a value."
  say "   Create it once (needs ssm:PutParameter, which the EC2 role does not have):"
  say "     aws ssm put-parameter --name $HUB_KEY_PARAM --type SecureString \\"
  say "         --value '<key from api.sap.com>' --region \${AWS_REGION:-us-east-1}"
  fail_steps="$fail_steps catalog-sync-skipped"
fi

# 1b. Object graph (L1) — derived from the catalog the step above just changed.
#     Pure stdlib, no embeddings, seconds to run: there is no reason to let it drift.
run "object graph rebuild (L1)" $PY mcp-server/graph/build_graph.py || true

# 1c. Semantic index (L2) — indexes L1's objects PLUS L3's lessons, so it goes stale
#     from two directions: a catalog sync above, and every record_experience call in
#     between refreshes.
#
#     Safe to run unattended only because build_index.py now refuses to publish a
#     WEAKER backend than the live index. engine.backend() reports what this host is
#     configured for, not what the index was built with, so on a host missing
#     sentence-transformers/boto3 this step would otherwise have quietly replaced a
#     dense index with TF-IDF keyword overlap -- succeeding loudly and answering
#     worse. If it refuses here, fix the host deps; do not reach for
#     --allow-downgrade in an unattended job.
run "semantic index rebuild (L2)" $PY mcp-server/vector/build_index.py || true

# 2. Vendor documentation (CAP / Node over HTTP, UI5 + Fiori Elements from GitHub).
#    Exits non-zero if it stored nothing, which is a real failure, not a no-op.
run "developer-doc harvest" $PY scripts/webdocs_ingest.py || true

# 3. Re-embed. The shrink guard refuses to publish a smaller index than the live
#    one, so a harvest that silently returned less cannot replace the brain.
run "vector rebuild (Bedrock Titan, 8 workers)" $PY scripts/embed_chunks.py \
  && REBUILT=1 || REBUILT=""

# 3b. The BM25 half of hybrid retrieval. Cheap (~30 s, no Bedrock calls) but it must
#     cover the SAME corpus as the vectors: the two are joined on chunk id at query
#     time, so a keyword index built over a corpus the vector index does not share
#     yields hits with no similarity score. Hence it is skipped when the rebuild
#     above failed -- the vector publish is atomic, so on failure the LIVE vector
#     index is still the previous corpus, and publishing a new keyword index over
#     the new one would put the two halves out of step.
if [ -n "$REBUILT" ]; then
  run "keyword index (BM25 / FTS5)" $PY scripts/keyword_index.py || true
else
  say "── keyword index SKIPPED: the vector rebuild did not succeed, and the two"
  say "   indexes must cover the same corpus. The live pair is unchanged and"
  say "   consistent; fix the rebuild and re-run."
  fail_steps="$fail_steps keyword-index-skipped"
fi

# 4. The MCP server caches both the catalog and the index at import. Without this
#    restart the refresh is invisible to every running agent -- which has bitten
#    this project before.
if [ -z "$DRY" ]; then
  run "restart s4pc-mcp" pm2 restart s4pc-mcp || true
else
  say "── DRY RUN, would restart s4pc-mcp"
fi

# 5. The gate. Assertions failing means retrieval regressed; drift is reported for
#    a human. Deliberately last, and deliberately not silent.
# --max-drop is a CATASTROPHE FLOOR, not a quality bar. Assertions catch a named
# expectation breaking; they do not catch "retrieval is now broadly different", and
# some changes are only visible as drift -- swapping the fusion strategy to RRF
# passes all 40 assertions while churning a third of the known-good results (67%
# overlap against 86%). A monthly refresh legitimately moves results because it adds
# documents, so this is set loose enough not to cry wolf: a MEAN overlap below 0.4
# across the whole set is not corpus growth, it is a regression.
say "── retrieval regression gate"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain_regression.py --max-drop 0.4"
else
  if $PY scripts/brain_regression.py --max-drop 0.4; then
    say "   ok: regression gate passed"
  else
    say "   FAILED: RETRIEVAL REGRESSED — investigate before trusting this brain."
    say "   The previous index is still on disk as brain/index/faiss.index.prev"
    fail_steps="$fail_steps regression-gate"
  fi
fi

# 6. Cross-layer freshness. Step 5 proves RETRIEVAL still works; it says nothing about
#    whether the derived stores still match their sources. Those are different
#    failures: a stale L2 answers every query successfully and merely omits what it
#    has not indexed, which is indistinguishable from a correct empty result. This is
#    the check that would have caught L2 sitting 3 lessons behind L3.
say "── spreadsheet extraction + tabular chunking"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain-tests/test_table_chunking.py"
elif $PY brain-tests/test_table_chunking.py > /dev/null 2>&1; then
  say "   ok: sheet rows keep their columns, headers repeat per chunk"
else
  say "   FAILED: spreadsheet content is being mangled at ingest — mapping"
  say "   specs are the bulk of this corpus and would become ungroundable. Detail:"
  say "     $PY brain-tests/test_table_chunking.py"
  fail_steps="$fail_steps table-chunking"
fi

say "── document-lifecycle round-trip"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain-tests/test_doc_version.py"
elif $PY brain-tests/test_doc_version.py > /dev/null 2>&1; then
  say "   ok: version parsing and supersession round-trip"
else
  say "   FAILED: document-lifecycle detection is broken — superseded revisions"
  say "   will be reported as current. Detail:"
  say "     $PY brain-tests/test_doc_version.py"
  fail_steps="$fail_steps doc-lifecycle"
fi

say "── object-mention index round-trip"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain-tests/test_object_mentions.py"
elif $PY brain-tests/test_object_mentions.py > /dev/null 2>&1; then
  say "   ok: mention index round-trips in both directions"
else
  say "   FAILED: the object-mention index is broken — get_object_usage and the"
  say "   objects_mentioned annotation on search hits will be wrong. Detail:"
  say "     $PY brain-tests/test_object_mentions.py"
  fail_steps="$fail_steps object-mentions"
fi

say "── object graph briefs (L4/L3 → L1 edge)"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain-tests/test_graph_briefs.py"
elif $PY brain-tests/test_graph_briefs.py > /dev/null 2>&1; then
  say "   ok: mentions resolve to graph nodes, and unknown names stay unresolved"
else
  say "   FAILED: the document→graph edge is broken. The dangerous direction is a"
  say "   name resolving to the WRONG node — a fabricated business area reads"
  say "   exactly like a correct one. Detail:"
  say "     $PY brain-tests/test_graph_briefs.py"
  fail_steps="$fail_steps graph-briefs"
fi

say "── lesson provenance (L3 → run)"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain-tests/test_run_evidence.py"
elif $PY brain-tests/test_run_evidence.py > /dev/null 2>&1; then
  say "   ok: a lesson resolves to its run, and an inferred match says so"
else
  say "   FAILED: lesson provenance is broken. The dangerous direction is an"
  say "   INFERRED match reported as exact — pointing a reader at a different"
  say "   run's deliverables reads exactly like the right ones. Detail:"
  say "     $PY brain-tests/test_run_evidence.py"
  fail_steps="$fail_steps run-evidence"
fi

say "── cross-layer freshness"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run freshness.report() across L1-L4"
else
  if $PY -c "
import sys
sys.path.insert(0, 'mcp-server')
import freshness
r = freshness.report()
for c in r['consistency']:
    if c['status'] != 'OK':
        print('   %-8s %s — %s' % (c['status'], c['check'], c['detail']))
        if c.get('fix'):
            print('            fix: %s' % c['fix'])
sys.exit(0 if r['status'] == 'OK' else 3)
"; then
    say "   ok: all layers consistent with their sources"
  else
    say "   ATTENTION: a derived layer no longer matches its source (see above)."
    fail_steps="$fail_steps layer-freshness"
  fi
fi

say "=== refresh finished $STAMP"
if [ -n "$fail_steps" ]; then
  say "=== ATTENTION — these steps need a look:$fail_steps"
  exit 1
fi
say "=== all steps clean"
