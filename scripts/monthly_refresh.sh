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
# ORDER MATTERS
#   catalog sync first (cheap, independent), then the doc harvest (network, no
#   embedding cost on failure), then the rebuild (expensive), then the MCP restart
#   (the server caches the catalog and the index in memory -- without this, none of
#   the above is visible to a running agent), then the gate.
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

# 2. Vendor documentation (CAP / Node over HTTP, UI5 + Fiori Elements from GitHub).
#    Exits non-zero if it stored nothing, which is a real failure, not a no-op.
run "developer-doc harvest" $PY scripts/webdocs_ingest.py || true

# 3. Re-embed. The shrink guard refuses to publish a smaller index than the live
#    one, so a harvest that silently returned less cannot replace the brain.
run "vector rebuild (Bedrock Titan, 8 workers)" $PY scripts/embed_chunks.py || true

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
say "── retrieval regression gate"
if [ -n "$DRY" ]; then
  say "   DRY RUN, would run brain_regression.py"
else
  if $PY scripts/brain_regression.py; then
    say "   ok: regression gate passed"
  else
    say "   FAILED: RETRIEVAL REGRESSED — investigate before trusting this brain."
    say "   The previous index is still on disk as brain/index/faiss.index.prev"
    fail_steps="$fail_steps regression-gate"
  fi
fi

say "=== refresh finished $STAMP"
if [ -n "$fail_steps" ]; then
  say "=== ATTENTION — these steps need a look:$fail_steps"
  exit 1
fi
say "=== all steps clean"
