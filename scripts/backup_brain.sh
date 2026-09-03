#!/usr/bin/env bash
# Back up the Public Cloud Brain to S3. Uses the host IAM role — no keys.
#
# brain/ is git-ignored and is NOT restored by deploy/bootstrap.sh, so a lost EBS volume
# means re-running the SharePoint harvest and a full re-embed. This covers that gap.
#
#   bash scripts/backup_brain.sh              # tier 1: index + masked chunks (~500 MB)
#   bash scripts/backup_brain.sh --with-raw   # also the 7.2 GB of RAW client documents
#   bash scripts/backup_brain.sh --dry-run    # show what would transfer, write nothing
#
# TIERS — deliberate, not arbitrary:
#   tier 1  brain/index      expensive to rebuild (tens of thousands of Bedrock calls)
#           brain/sharepoint/chunks   PII-MASKED text; needs spaCy NER to regenerate
#   tier 2  brain/sharepoint/raw      RAW, PRE-MASKING client documents. SharePoint is the
#           system of record, so this is re-harvestable. Opt in ONLY when you have
#           confirmed that unmasked client content may live in the target bucket.
set -euo pipefail

BUCKET="${BRAIN_BACKUP_BUCKET:-digitalbrain-knowledge-us-east-1}"
PREFIX="${BRAIN_BACKUP_PREFIX:-s4pc-brain-backup/$(hostname -s)}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

WITH_RAW=0
DRY=""
for arg in "$@"; do
  case "$arg" in
    --with-raw) WITH_RAW=1 ;;
    --dry-run)  DRY="--dryrun" ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

[ -d brain ] || { echo "FATAL: no brain/ directory in $REPO"; exit 1; }
command -v aws >/dev/null || { echo "FATAL: aws CLI not on PATH"; exit 1; }

echo "== target: s3://$BUCKET/$PREFIX ${DRY:+(DRY RUN)}"
aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1 \
  || { echo "FATAL: cannot reach s3://$BUCKET (check the role's bucket policy)"; exit 1; }

sync_one() {                       # $1 = local path, $2 = remote suffix
  [ -e "$1" ] || { echo "   skip  $1 (absent)"; return 0; }
  echo "   sync  $1  ->  s3://$BUCKET/$PREFIX/$2"
  aws s3 sync "$1" "s3://$BUCKET/$PREFIX/$2" --only-show-errors $DRY
}

echo "== tier 1: expensive-to-rebuild artifacts"
sync_one brain/index                  index
sync_one brain/sharepoint/chunks      sharepoint/chunks

if [ "$WITH_RAW" = "1" ]; then
  echo "== tier 2: RAW client documents (pre-masking) — 7+ GB"
  sync_one brain/sharepoint/raw       sharepoint/raw
else
  echo "== tier 2 skipped (raw client documents). Add --with-raw to include them,"
  echo "   but confirm first that unmasked client content may reside in $BUCKET."
fi

# A manifest makes a restore verifiable instead of hopeful.
if [ -z "$DRY" ]; then
  MANIFEST="$(mktemp)"
  {
    echo "backed_up_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host: $(hostname -f)"
    echo "git_commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "with_raw: $WITH_RAW"
    echo "sizes:"
    du -sh brain/index brain/sharepoint/chunks 2>/dev/null | sed 's/^/  /'
    [ "$WITH_RAW" = "1" ] && du -sh brain/sharepoint/raw 2>/dev/null | sed 's/^/  /'
    echo "chunk_count: $(find brain/sharepoint/chunks -type f 2>/dev/null | wc -l)"
  } > "$MANIFEST"
  aws s3 cp "$MANIFEST" "s3://$BUCKET/$PREFIX/MANIFEST.txt" --only-show-errors
  rm -f "$MANIFEST"
  echo "== manifest written to s3://$BUCKET/$PREFIX/MANIFEST.txt"

  # Success marker, read by brain-ui so a STALE backup is visible in the UI rather
  # than being discovered when a restore is already needed. Written last, only on
  # success — `set -e` means a failed sync never reaches this line, so an old
  # timestamp is a truthful signal that the last run failed.
  date -u +%Y-%m-%dT%H:%M:%SZ > brain/.last_backup
fi

echo "== done"
