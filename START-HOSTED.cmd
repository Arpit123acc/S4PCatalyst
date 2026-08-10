@echo off
rem ============================================================
rem  S4PC Catalyst - SHARED single-instance (one team machine)
rem  Binds to the network AND requires a password, so teammates
rem  can use it in the browser without a local copy of the code.
rem     Others open:  http://THIS-MACHINE-IP:8321
rem     Login:        team / <the password you set>
rem ============================================================
setlocal
cd /d "%~dp0"

if "%S4PC_ACCESS_PASSWORD%"=="" (
  echo [ERROR] Set an access password first, then re-run this script. Example:
  echo.
  echo         set S4PC_ACCESS_PASSWORD=ChooseAStrongPassword
  echo         START-HOSTED.cmd
  echo.
  pause
  exit /b 1
)
if "%S4PC_ACCESS_USER%"=="" set "S4PC_ACCESS_USER=team"

set "S4PC_UI_HOST=0.0.0.0"
set "S4PC_UI_NO_BROWSER=1"

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD ( where python >nul 2>nul && set "PYCMD=python" )
if not defined PYCMD (
  echo [ERROR] Python 3 not found. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)

echo Starting S4PC Catalyst (SHARED / password-protected) on port 8321 ...
echo Share with the team:  http://%COMPUTERNAME%:8321   (or http://THIS-MACHINE-IP:8321)
echo Login user: %S4PC_ACCESS_USER%    Password: (the one you set)
echo Keep this window open; close it or press Ctrl+C to stop.
echo.
%PYCMD% "webapp\app.py"
echo.
echo Catalyst stopped.
pause
