@echo off
:: ===========================================================================
:: S4PC brain tunnel WITH AUTO-RECONNECT (template).
::
:: Fill in KEY and TARGET below, save a COPY OUTSIDE THIS REPO (e.g.
:: %USERPROFILE%\s4pc-tunnel.bat), and point a Startup-folder shortcut at that
:: copy. Do NOT commit your filled-in version -- the EC2 address and key path
:: stay local.
::
:: Why the retry loop: plink has no reconnect of its own. A transient network
:: blip ("FATAL ERROR: Network error: Software caused connection abort") kills
:: the tunnel permanently and SILENTLY, so the governance MCP tools simply stop
:: being available mid-session. The loop turns that into a five-second gap.
:: Proven on 2026-09-03: survived a ~5 minute VPN outage and restored all three
:: ports on attempt 10 with no manual intervention.
::
:: TWO THINGS THAT WILL BREAK THIS IF YOU EDIT IT:
::   1. Wait with `ping`, not `timeout`. timeout.exe needs a console, and when
::      this file is launched from a Git Bash PATH it resolves to the UNIX
::      timeout instead ("invalid time interval '/t'").
::   2. Keep CRLF line endings. With LF, cmd.exe cannot find the `:loop` label
::      ("The system cannot find the batch label specified"). .gitattributes
::      pins *.bat to eol=crlf so a clone gets this right.
::
:: For headless pipeline runs this is belt-and-braces: webapp/app.py's
:: mcp_preflight() already refuses to start a run when the MCP server is not
:: reachable, so a dead tunnel cannot silently produce an ungoverned
:: deliverable. The loop protects INTERACTIVE sessions, which have no such gate.
::
:: Ports forwarded:
::   3002  s4pc-mcp      25 governance + brain tools; register as `context7`
::   8321  s4pc-webapp   pipeline UI
::   8400  brain-ui      Brain Explorer (read-only brain visualisation)
::
:: One-time registration once the tunnel is up:
::   claude mcp add --transport http -s user context7 http://localhost:3002/mcp
::
:: Requires the corporate VPN if the host is on a private IP.
:: Stop with Ctrl+C, then answer Y to "Terminate batch job".
:: ===========================================================================

title S4PC Brain Tunnel (auto-reconnect)

set "PLINK=C:\Program Files\PuTTY\plink.exe"
set "KEY=%USERPROFILE%\.ssh\<YOUR-KEY-FILE>.ppk"
set "TARGET=ec2-user@<YOUR-EC2-HOST>"
set "FWD=-L 3002:localhost:3002 -L 8321:localhost:8321 -L 8400:localhost:8400"

if not exist "%PLINK%" (
  echo [S4PC] FATAL: plink not found at %PLINK%
  pause
  exit /b 1
)
if not exist "%KEY%" (
  echo [S4PC] FATAL: key not found at %KEY% -- edit this file first.
  pause
  exit /b 1
)

set /a ATTEMPT=0

:loop
set /a ATTEMPT+=1
echo.
echo [S4PC] %DATE% %TIME% - connecting (attempt %ATTEMPT%) ... 3002 / 8321 / 8400
"%PLINK%" -i "%KEY%" -N -batch %FWD% %TARGET%

echo [S4PC] %DATE% %TIME% - tunnel dropped. Reconnecting in 5s.
echo [S4PC] If this loops rapidly, check the VPN first.
ping -n 6 127.0.0.1 >nul
goto loop
