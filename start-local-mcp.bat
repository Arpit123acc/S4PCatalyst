@echo off
:: S4PC unified MCP server — LOCAL HTTP mode (no tunnel, offline governance).
:: Run this bat file before starting Claude Code.
::
:: This is the OFFLINE alternative to start-brain-tunnel.bat. server.py serves the
:: full governance tool set locally (pure Python stdlib, no network, no API keys).
:: The brain (search_brain) needs Bedrock + FAISS on the EC2 host, so run locally it
:: DEGRADES GRACEFULLY — it returns "Brain unavailable" while every governance tool
:: works. For the brain too, use start-brain-tunnel.bat (EC2) instead.
::
:: Register once under the enterprise-allowlisted name (pick ONE deployment: this
:: local one on :3000, OR the EC2 tunnel on :3001 — not both under the same name):
::   claude mcp add --transport http -s user context7 http://localhost:3000/mcp

set PROJECT_DIR=%~dp0

echo.
echo [S4PC] Starting unified governance MCP server on http://localhost:3000/mcp
echo [S4PC] Governance tools: full. Brain (search_brain): degrades gracefully (no Bedrock locally).
echo [S4PC] Keep this window open while using Claude Code.
echo [S4PC] Press Ctrl+C to stop.
echo.

python "%PROJECT_DIR%mcp-server\server.py" --http 3000

echo.
echo [S4PC] Server stopped.
pause
