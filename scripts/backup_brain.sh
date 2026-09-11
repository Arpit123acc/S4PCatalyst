#!/usr/bin/env bash
# Back up the Public Cloud Brain to S3. Uses the host IAM role — no keys.
#
# brain/ is git-ignored and is NOT restored by deploy/bootstrap.sh, so a lost EBS volume
# means re-running the SharePoint harvest and a full re-embed. This covers that gap.
#
#   bash scripts/backup_brain.sh              # tier 1: index + masked chunks (~500 MB)
#   bash scripts/backup_brain.sh --with-raw   # also the 7.2 GB of RAW learning documents
#   bash scripts/backup_brain.sh --dry-run    # show what would transfer, write nothing
#   bash scripts/backup_brain.sh --allow-shrink   # prune even though the corpus shrank
#
# THE BACKUP MUST MIRROR, NOT ACCUMULATE
#   `aws s3 sync` without --delete only ever adds. Chunk files are named
#   {doc_id}_{idx}.json, so a re-ingest that produces FEWER chunks for a document
#   leaves its high-index files behind -- locally sharepoint_ingest deletes them
#   (_stale_chunks), but S3 never learned. Measured 2026-09-08: 57,764 objects in S3
#   against 35,814 local files, i.e. 21,950 orphans from three different corpora, and
#   MANIFEST.txt reported chunk_count: 34501 -- a number describing neither. A restore
#   would have rebuilt all 57,764, the embedder would have indexed the orphans as real
#   documents, and the one artifact meant to make a restore verifiable would have
#   passed while being wrong in both directions.
#
#   So the sync now prunes. Pruning is the dangerous direction, though: run this
#   mid-ingest, when the local chunk tree is half-built, and --delete would destroy
#   good S3 objects to match it. It is therefore gated on the SAME shrink guard that
#   embed_chunks and keyword_index use -- refuse to publish something markedly smaller
#   than what is already there -- reading the previous run's chunk_count out of the
#   manifest in S3. Below the floor it syncs ADDITIVELY and says so loudly; the backup
#   stays a superset rather than becoming a truncated one.
#
# TIERS — deliberate, not arbitrary:
#   tier 1  brain/index      expensive to rebuild (tens of thousands of Bedrock calls)
#           brain/sharepoint/chunks   PII-MASKED text; needs spaCy NER to regenerate
#   tier 2  brain/sharepoint/raw      RAW, PRE-MASKING learning documents. SharePoint is the
#           system of record, so this is re-harvestable. Opt in ONLY when you have
#           confirmed that unmasked learning content may live in the target bucket.
set -euo pipefail

BUCKET="${BRAIN_BACKUP_BUCKET:-digitalbrain-knowledge-us-east-1}"
PREFIX="${BRAIN_BACKUP_PREFIX:-s4pc-brain-backup/$(hostname -s)}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

WITH_RAW=0
DRY=""
ALLOW_SHRINK=0
for arg in "$@"; do
  case "$arg" in
    --with-raw)     WITH_RAW=1 ;;
    --dry-run)      DRY="--dryrun" ;;
    --allow-shrink) ALLOW_SHRINK=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# Fraction of the previous chunk count below which pruning is refused. 0.9 tolerates
# the ordinary case -- a re-chunk that legitimately yields a few percent fewer files --
# while catching the one that matters, a partial tree mid-ingest.
SHRINK_FLOOR="${BRAIN_BACKUP_SHRINK_FLOOR:-0.9}"

[ -d brain ] || { echo "FATAL: no brain/ directory in $REPO"; exit 1; }
command -v aws >/dev/null || { echo "FATAL: aws CLI not on PATH"; exit 1; }

echo "== target: s3://$BUCKET/$PREFIX ${DRY:+(DRY RUN)}"
aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1 \
  || { echo "FATAL: cannot reach s3://$BUCKET (check the role's bucket policy)"; exit 1; }

# ── Decide whether this run may PRUNE ────────────────────────────────────────
# The floor is read from the manifest already in S3, so the check needs no local
# state and survives a host rebuild. No manifest means no baseline to compare
# against, and pruning on a guess is exactly what this guard exists to prevent.
LOCAL_CHUNKS=$(find brain/sharepoint/chunks -type f 2>/dev/null | wc -l)
PREV_CHUNKS=$(aws s3 cp "s3://$BUCKET/$PREFIX/MANIFEST.txt" - 2>/dev/null \
              | sed -n 's/^chunk_count: *//p' | head -1)
PREV_CHUNKS="${PREV_CHUNKS:-0}"
PRUNE="--delete"
PRUNE_NOTE="mirroring (orphans in S3 will be removed)"

if [ "$ALLOW_SHRINK" = "1" ]; then
  PRUNE_NOTE="mirroring, shrink guard overridden by --allow-shrink"
elif [ "$PREV_CHUNKS" -eq 0 ]; then
  # First backup, or a manifest that predates chunk_count. Additive: there is no
  # baseline, and a bucket whose contents we cannot vouch for must not be pruned.
  PRUNE=""
  PRUNE_NOTE="ADDITIVE — no previous chunk_count in the manifest, so no baseline to
   check a shrink against. Re-run once this backup has written one."
elif [ "$LOCAL_CHUNKS" -lt "$(awk "BEGIN{printf \"%d\", $PREV_CHUNKS * $SHRINK_FLOOR}")" ]; then
  PRUNE=""
  PRUNE_NOTE="ADDITIVE — local chunk tree is $LOCAL_CHUNKS files against $PREV_CHUNKS
   last time, below the ${SHRINK_FLOOR}x floor. That is what a backup running
   mid-ingest looks like, and pruning to match a half-built tree would destroy the
   good copy. If the corpus really did shrink, re-run with --allow-shrink."
fi

echo "== chunk files: $LOCAL_CHUNKS local, $PREV_CHUNKS at last backup"
echo "   $PRUNE_NOTE"

sync_one() {                       # $1 = local path, $2 = remote suffix
  [ -e "$1" ] || { echo "   skip  $1 (absent)"; return 0; }
  echo "   sync  $1  ->  s3://$BUCKET/$PREFIX/$2"
  aws s3 sync "$1" "s3://$BUCKET/$PREFIX/$2" --only-show-errors $PRUNE $DRY
}

echo "== tier 1: expensive-to-rebuild artifacts"
sync_one brain/index                  index
sync_one brain/sharepoint/chunks      sharepoint/chunks

if [ "$WITH_RAW" = "1" ]; then
  echo "== tier 2: RAW learning documents (pre-masking) — 7+ GB"
  sync_one brain/sharepoint/raw       sharepoint/raw
else
  echo "== tier 2 skipped (raw learning documents). Add --with-raw to include them,"
  echo "   but confirm first that unmasked learning content may reside in $BUCKET."
fi

# A manifest makes a restore verifiable instead of hopeful -- but only if it describes
# what is IN S3. It used to record the local chunk count alone, which is how it came to
# read 34,501 beside a bucket holding 57,764 objects: a restore check against it would
# have passed while being wrong in both directions. Both numbers are recorded now, and
# a mismatch is stated in the file rather than left for arithmetic.
if [ -z "$DRY" ]; then
  S3_CHUNKS=$(aws s3 ls "s3://$BUCKET/$PREFIX/sharepoint/chunks/" --recursive --summarize \
              2>/dev/null | sed -n 's/^ *Total Objects: *//p' | head -1)
  S3_CHUNKS="${S3_CHUNKS:-unknown}"
  MANIFEST="$(mktemp)"
  {
    echo "backed_up_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host: $(hostname -f)"
    echo "git_commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "with_raw: $WITH_RAW"
    echo "pruned: $([ -n "$PRUNE" ] && echo yes || echo "no (additive)")"
    echo "sizes:"
    du -sh brain/index brain/sharepoint/chunks 2>/dev/null | sed 's/^/  /'
    [ "$WITH_RAW" = "1" ] && du -sh brain/sharepoint/raw 2>/dev/null | sed 's/^/  /'
    echo "chunk_count: $LOCAL_CHUNKS"
    echo "s3_chunk_objects: $S3_CHUNKS"
    if [ "$S3_CHUNKS" != "unknown" ] && [ "$S3_CHUNKS" -ne "$LOCAL_CHUNKS" ] 2>/dev/null; then
      echo "WARNING: S3 holds $S3_CHUNKS chunk objects but the local tree has"
      echo "  $LOCAL_CHUNKS. A restore would reconstruct the LARGER set, including"
      echo "  orphans from earlier corpora, and the embedder indexes those as real"
      echo "  documents. Re-run the backup with pruning enabled before relying on it."
    fi
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
