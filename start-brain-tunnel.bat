@echo off
:: S4PC Brain — SSH tunnel  localhost:3001 -> EC2:3001
:: Run this bat file before starting Claude Code so the brain MCP server is reachable.
::
:: Fill in EC2_HOST and KEY_FILE below, then save (do NOT commit — EC2 details stay local).
:: After first-time setup, register the server once:
::   claude mcp add s4pc-brain --transport http http://localhost:3001/mcp
::
:: On EC2 the brain HTTP server must already be running:
::   nohup python3.11 mcp-server/brain_server.py --http 3001 > brain/http.out 2>&1 &

set EC2_HOST=ubuntu@<YOUR-EC2-IP-HERE>
set KEY_FILE=%USERPROFILE%\.ssh\<YOUR-KEY-FILE>.pem

echo.
echo [S4PC] Starting SSH tunnel: localhost:3001 -^> %EC2_HOST%:3001
echo [S4PC] Keep this window open while using Claude Code.
echo [S4PC] Press Ctrl+C to stop the tunnel.
echo.

ssh -N -L 3001:localhost:3001 -i "%KEY_FILE%" %EC2_HOST%

echo.
echo [S4PC] Tunnel stopped.
pause
