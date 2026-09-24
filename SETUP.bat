@echo off
echo ============================================
echo  Fulcrum -- First-time setup
echo ============================================
echo.
echo Installing dependencies (needs internet, ~1 minute)...
echo.

call npm install
if errorlevel 1 (
    echo.
    echo ERROR: npm install failed.
    echo Make sure Node.js is installed: https://nodejs.org
    pause
    exit /b 1
)

cd kdd-generator
call npm install
cd ..

echo.
echo ============================================
echo  Setup complete. Run START.bat to launch.
echo ============================================
echo.
pause
