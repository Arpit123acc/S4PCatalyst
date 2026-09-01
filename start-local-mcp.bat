@echo off
:: S4PC Governance MCP server — local HTTP mode (no tunnel needed, no network calls)
:: Run this bat file before starting Claude Code.
::
:: Register once (first time only):
::   claude mcp add s4pc --transport http http://localhost:3000/mcp
::
:: The server is offline-first: no internet access, no API keys, pure Python stdlib.

set PROJECT_DIR=%~dp0

echo.
echo [S4PC] Starting governance MCP server on http://localhost:3000/mcp
echo [S4PC] Keep this window open while using Claude Code.
echo [S4PC] Press Ctrl+C to stop.
echo.

python "%PROJECT_DIR%mcp-server\server.py" --http 3000

echo.
echo [S4PC] Server stopped.
pause
