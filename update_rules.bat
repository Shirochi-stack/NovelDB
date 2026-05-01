@echo off
setlocal enabledelayedexpansion

:: ============================================================================
::  update_rules.bat — Patch, build, and copy novel-downloader rules
::
::  Usage:  update_rules.bat [path_to_novel_downloader]
::  Example: update_rules.bat D:\Projects\novel-downloader
::
::  If no path is given, it defaults to D:\Projects\novel-downloader
:: ============================================================================

set "NPIA_DIR=%~dp0"
set "ND_DIR=%~1"
if "%ND_DIR%"=="" set "ND_DIR=D:\Projects\novel-downloader"

echo.
echo ========================================
echo   NpiaDownloader — Rules Update Script
echo ========================================
echo.
echo  Novel-Downloader: %ND_DIR%
echo  NpiaDownloader:   %NPIA_DIR%
echo.

:: --- Check novel-downloader exists ---
if not exist "%ND_DIR%\src\index.ts" (
    echo ERROR: Cannot find %ND_DIR%\src\index.ts
    echo Make sure novel-downloader is cloned there, or pass the path as argument.
    echo.
    echo   git clone https://github.com/404-novel-project/novel-downloader.git "%ND_DIR%"
    echo.
    pause
    exit /b 1
)

:: --- Step 1: Pull latest ---
echo [1/4] Pulling latest novel-downloader...
pushd "%ND_DIR%"
git pull
if errorlevel 1 (
    echo WARNING: git pull failed. Continuing with existing code...
)
popd

:: --- Step 2: Apply bridge-mode patch ---
echo [2/4] Applying bridge-mode patch to src/index.ts...

:: Use PowerShell for reliable text replacement
powershell -NoProfile -Command ^
  "$f = '%ND_DIR%\src\index.ts';" ^
  "$c = Get-Content $f -Raw;" ^
  "" ^
  "# Check if already patched" ^
  "if ($c -match '__ND_BRIDGE_MODE') {" ^
  "  Write-Host '  Already patched — skipping.';" ^
  "} else {" ^
  "  # Find the bottom section: the if/else block that calls main()" ^
  "  # Replace it with bridge-mode aware version" ^
  "  $old = 'if \(document\.readyState === \"loading\"\) \{[\s\S]*?main\(\);\s*\}';" ^
  "  $new = @'" ^
  "// Bridge mode: skip full UI init, just expose getRule for external callers.`r`n" ^
  "if ((window as any).__ND_BRIDGE_MODE) {`r`n" ^
  "  (window as any).__ND_getRule = getRule;`r`n" ^
  "  (window as any).__ND_getHtmlDOM = getHtmlDOM;`r`n" ^
  "  (window as any).__ND_READY = true;`r`n" ^
  "  log.info(\"\"[Init] Bridge mode — skipping UI init, getRule exposed.\"\");`r`n" ^
  "} else if (document.readyState === \"\"loading\"\") {`r`n" ^
  "  document.addEventListener(\"\"DOMContentLoaded\"\", main);`r`n" ^
  "} else {`r`n" ^
  "  // noinspection JSIgnoredPromiseFromCall`r`n" ^
  "  main();`r`n" ^
  "}" ^
  "'@;" ^
  "  $result = $c -replace $old, $new;" ^
  "  if ($result -eq $c) {" ^
  "    Write-Host '  WARNING: Pattern not found. index.ts may have changed structure.';" ^
  "    Write-Host '  Manual patching may be required — see EXTERNAL_NOVEL_SETUP.txt';" ^
  "  } else {" ^
  "    Set-Content $f $result -NoNewline;" ^
  "    Write-Host '  Patch applied successfully.';" ^
  "  }" ^
  "}"

:: --- Step 3: Build ---
echo [3/4] Building rules bundle...
pushd "%ND_DIR%"

:: Install deps if node_modules missing
if not exist "node_modules" (
    echo   Installing dependencies...
    call npm install
)

call npx webpack
if errorlevel 1 (
    echo ERROR: Webpack build failed!
    popd
    pause
    exit /b 1
)
popd

:: --- Step 4: Copy bundle ---
echo [4/4] Copying rules-lib.js to NpiaDownloader...

if exist "%ND_DIR%\dist\bundle.user.js" (
    copy /Y "%ND_DIR%\dist\bundle.user.js" "%NPIA_DIR%rules-lib.js" >nul
    echo   Done!
) else (
    echo ERROR: dist\bundle.user.js not found. Build may have failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Rules updated successfully!
echo ========================================
echo.
pause
