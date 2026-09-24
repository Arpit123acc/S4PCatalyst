@echo off
echo ============================================
echo  Fulcrum v1.1.0
echo  SAP S/4HANA Cloud PE Accelerator
echo ============================================
echo.

if not exist node_modules (
    echo node_modules not found. Running setup first...
    echo.
    call SETUP.bat
)

echo Starting Fulcrum server...
echo.

:: Open browser after 3-second delay (in background)
start /min cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8321"

:: Start server in foreground -- close this window to stop Fulcrum
node server.js

echo.
echo Fulcrum stopped.
pause
