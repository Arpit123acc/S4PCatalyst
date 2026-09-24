# Fulcrum -- build.ps1
# Creates dist\Fulcrum-v1.1.0-win.zip ready to share.
# Run from the explore-accelerator folder:
#   powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
$VERSION  = "v1.1.0"
$DIST_DIR = "dist\Fulcrum-$VERSION-win"
$ZIP_OUT  = "dist\Fulcrum-$VERSION-win.zip"

Write-Host ""
Write-Host "=== Fulcrum build -- $VERSION ===" -ForegroundColor Cyan
Write-Host ""

# Clean and create dist folder
if (Test-Path $DIST_DIR) { Remove-Item $DIST_DIR -Recurse -Force }
if (Test-Path $ZIP_OUT)  { Remove-Item $ZIP_OUT  -Force }
New-Item -ItemType Directory -Path $DIST_DIR -Force | Out-Null

Write-Host "[1/5] Copying source files..." -ForegroundColor Yellow

# Root files
Copy-Item "server.js"     "$DIST_DIR\"
Copy-Item "package.json"  "$DIST_DIR\"
Copy-Item "START.bat"     "$DIST_DIR\"
Copy-Item "SETUP.bat"     "$DIST_DIR\"
Copy-Item "README-INSTALL.md" "$DIST_DIR\" -ErrorAction SilentlyContinue
Copy-Item "USER-GUIDE.md"    "$DIST_DIR\" -ErrorAction SilentlyContinue

# Sub-folders (source only, no node_modules)
Copy-Item "bdcq-agent"   "$DIST_DIR\bdcq-agent"   -Recurse
Copy-Item "bdcq-enricher" "$DIST_DIR\bdcq-enricher" -Recurse
Copy-Item "agents"        "$DIST_DIR\agents"        -Recurse
Copy-Item "public"        "$DIST_DIR\public"        -Recurse

# kdd-generator -- scripts + its own package.json (no node_modules)
New-Item -ItemType Directory -Path "$DIST_DIR\kdd-generator" -Force | Out-Null
Get-ChildItem "kdd-generator" -File | Copy-Item -Destination "$DIST_DIR\kdd-generator\"

Write-Host "[2/5] Copying SAP grounding data..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path "$DIST_DIR\bdcq\grounding" -Force | Out-Null
$groundingFile = "bdcq\grounding\BDCQ_S4HANA_PublicCloud_v4_AllCountries.json"
if (Test-Path $groundingFile) {
    Copy-Item $groundingFile "$DIST_DIR\bdcq\grounding\"
}

# Empty output folder
New-Item -ItemType Directory -Path "$DIST_DIR\output" -Force | Out-Null

Write-Host "[3/5] Zipping Chrome extension..." -ForegroundColor Yellow
$extZip = "$DIST_DIR\SAPDeckAgent-ChromeExtension.zip"
Compress-Archive -Path "SAPDeckAgent_v27\*" -DestinationPath $extZip

Write-Host "[4/5] Creating distribution zip..." -ForegroundColor Yellow
Compress-Archive -Path "$DIST_DIR\*" -DestinationPath $ZIP_OUT

Write-Host "[5/5] Done." -ForegroundColor Yellow

$zipSizeKB = [math]::Round((Get-Item $ZIP_OUT).Length / 1KB, 0)
$resolvedZip = (Resolve-Path $ZIP_OUT).Path

Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Green
Write-Host "Zip:  $resolvedZip" -ForegroundColor Green
Write-Host "Size: $zipSizeKB KB" -ForegroundColor Green
Write-Host ""
Write-Host "Share this zip. Recipients:" -ForegroundColor Cyan
Write-Host "  1. Unzip anywhere" -ForegroundColor Cyan
Write-Host "  2. Run SETUP.bat once (needs Node.js + internet)" -ForegroundColor Cyan
Write-Host "  3. Run START.bat to launch Fulcrum" -ForegroundColor Cyan
Write-Host ""
