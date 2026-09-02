#!/usr/bin/env bash
# Bring the S4PC Catalyst delivery host to a running state. Idempotent — safe to re-run.
# See deploy/README.md for what each step is for.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="$HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"

echo "== 1. Claude Code -> Bedrock routing =="
mkdir -p "$CLAUDE_DIR"
if [ -f "$SETTINGS" ] && ! cmp -s "$REPO/deploy/claude-settings.json" "$SETTINGS"; then
  # Never clobber a divergent live config without keeping a copy — it may hold
  # local changes made on the box that are not yet reflected in the repo.
  cp -p "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
  echo "   existing settings.json differed -> backed up alongside it"
fi
cp "$REPO/deploy/claude-settings.json" "$SETTINGS"
echo "   installed $SETTINGS"

echo "== 2. sanity: python3.11 and its deps =="
command -v python3.11 >/dev/null || { echo "   FATAL: python3.11 not on PATH"; exit 1; }
python3.11 -c 'import boto3, faiss, numpy' 2>/dev/null \
  && echo "   boto3 + faiss + numpy present" \
  || echo "   WARN: brain deps missing -> search_brain will degrade. pip3.11 install boto3 faiss-cpu numpy"

echo "== 3. sanity: the MCP server actually starts =="
# The failure this guards against is silent: if the server cannot start, claude -p runs
# with --strict-mcp-config and NO governance tools, and the pipeline still reports PASS.
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"bootstrap","version":"1"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | (cd "$REPO" && timeout 60 python3.11 mcp-server/server.py 2>/dev/null) \
  | python3.11 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if m.get("id") == 2:
        names = [t["name"] for t in m["result"]["tools"]]
        print("   tools: %d | search_brain: %s" % (len(names), "search_brain" in names))
        sys.exit(0 if len(names) >= 20 else 1)
sys.exit(1)' || { echo "   FATAL: MCP server did not return a usable tool list"; exit 1; }

echo "== 4. PM2 services =="
command -v pm2 >/dev/null || { echo "   FATAL: pm2 not installed (npm install -g pm2)"; exit 1; }
pm2 start "$REPO/deploy/ecosystem.config.js" --update-env
pm2 save
# Make PM2 itself come back after a reboot. Prints a sudo command to run if not yet set up.
systemctl is-enabled pm2-"$USER".service >/dev/null 2>&1 \
  && echo "   pm2 boot service already enabled" \
  || { echo "   pm2 boot service NOT enabled -> run:"; pm2 startup | tail -2; }

echo "== 5. verify =="
pm2 list
for p in 3002 8321; do
  ss -tln 2>/dev/null | grep -q ":$p " && echo "   port $p listening" || echo "   WARN: port $p not listening"
done
echo
echo "Done. Confirm Bedrock routing with:"
echo "  claude -p 'Reply with exactly: BEDROCK_OK' --strict-mcp-config --mcp-config /dev/null"
