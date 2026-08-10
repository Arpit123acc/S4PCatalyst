@echo off
rem ============================================================
rem  S4PC Catalyst - S/4HANA Public Cloud (Windows)
rem  Zero dependencies - needs only Python 3.9+ from python.org
rem  Starts the webapp at http://127.0.0.1:8321 and opens browser
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD ( where python >nul 2>nul && set "PYCMD=python" )
if not defined PYCMD (
  echo [ERROR] Python 3 not found. Install it from https://www.python.org/downloads/
  echo         ^(tick "Add python.exe to PATH" during install^)
  pause
  exit /b 1
)

echo Starting S4PC Catalyst (this window must stay open)...
%PYCMD% "webapp\app.py"
echo.
echo Catalyst stopped.
pause
