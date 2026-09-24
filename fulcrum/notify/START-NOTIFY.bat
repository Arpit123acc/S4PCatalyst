@echo off
title Fulcrum Notify
echo.
echo  ⚡ Fulcrum Notify Server
echo  ─────────────────────────────────────────────
echo  Detecting your Outlook email...
echo  Watching output/ folder for KDD completions
echo  Listening on http://127.0.0.1:8323/notify
echo.
echo  Keep this window open alongside Fulcrum.
echo  Press Ctrl+C to stop.
echo.
node "%~dp0notify.js" --server
pause
