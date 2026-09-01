#!/usr/bin/env bash
# Build the S4PC unified-MCP Lambda package.
#
# RUN ON LINUX (your EC2 box) or via `sam build --use-container`, NOT on Windows —
# psycopg2-binary installs a platform-specific wheel and the Lambda runtime is
# manylinux x86_64. A Windows-built wheel will fail at import in Lambda.
#
# Produces lambda/build/ — point SAM's CodeUri at it (see template.yaml), or zip it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build"

echo "[build] cleaning $BUILD"
rm -rf "$BUILD"
mkdir -p "$BUILD/mcp-server" "$BUILD/scripts"

echo "[build] handler"
cp "$HERE/lambda_function.py" "$BUILD/"

echo "[build] ensure catalog.db exists (git-ignored; auto-migrated from the JSON seeds)"
# Lambda's filesystem is read-only, so catalog.db MUST be in the package — it cannot be
# migrated at runtime there. Build it now from the tracked JSON seeds if it is absent.
python3.11 "$ROOT/mcp-server/catalog/db.py" >/dev/null

echo "[build] server code (governance + brain)"
cp "$ROOT/mcp-server/server.py"       "$BUILD/mcp-server/"
cp "$ROOT/mcp-server/brain_server.py" "$BUILD/mcp-server/"
[ -f "$ROOT/mcp-server/config.json" ] && cp "$ROOT/mcp-server/config.json" "$BUILD/mcp-server/"
cp -r "$ROOT/mcp-server/catalog" "$BUILD/mcp-server/"   # includes the pre-built catalog.db (read-only at runtime)
# optional Digital-Brain layers (semantic_search / object graph) — safe if absent
[ -d "$ROOT/mcp-server/vector" ] && cp -r "$ROOT/mcp-server/vector" "$BUILD/mcp-server/"
[ -d "$ROOT/mcp-server/graph" ]  && cp -r "$ROOT/mcp-server/graph"  "$BUILD/mcp-server/"

echo "[build] brain search + pluggable vector store"
cp "$ROOT/scripts/brain_search.py" "$BUILD/scripts/"
cp "$ROOT/scripts/vectorstore.py"  "$BUILD/scripts/"

echo "[build] python deps (pgvector backend; no faiss)"
python3.11 -m pip install -r "$HERE/requirements.txt" -t "$BUILD" --upgrade --only-binary=:all:

echo "[build] stripping local-only cruft"
rm -rf "$BUILD/mcp-server/logs"
find "$BUILD" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name '*.dist-info' -prune -exec rm -rf {} + 2>/dev/null || true

echo "[build] done -> $BUILD"
echo "        deploy with: sam deploy --guided   (from lambda/)"
