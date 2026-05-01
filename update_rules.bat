@echo off
setlocal enabledelayedexpansion

:: ============================================================================
::  update_rules.bat — Clone/pull, patch, build, and copy novel-downloader rules
::
::  Usage:  update_rules.bat [path_to_novel_downloader]
::  Example: update_rules.bat D:\Projects\novel-downloader
::
::  If no path is given, it clones/uses "novel-downloader" next to this script.
:: ============================================================================

set "NPIA_DIR=%~dp0"
set "ND_DIR=%~1"
if "%ND_DIR%"=="" set "ND_DIR=%NPIA_DIR%novel-downloader"
set "REPO_URL=https://github.com/404-novel-project/novel-downloader.git"

echo.
echo ========================================
echo   NpiaDownloader — Rules Update Script
echo ========================================
echo.
echo  Novel-Downloader: %ND_DIR%
echo  NpiaDownloader:   %NPIA_DIR%
echo.

:: --- Step 1: Clone or pull ---
if exist "%ND_DIR%\src\index.ts" (
    echo [1/4] Pulling latest novel-downloader...
    pushd "%ND_DIR%"
    git pull
    if errorlevel 1 (
        echo WARNING: git pull failed. Continuing with existing code...
    )
    popd
) else (
    echo [1/4] Cloning novel-downloader from GitHub...
    git clone "%REPO_URL%" "%ND_DIR%"
    if errorlevel 1 (
        echo ERROR: git clone failed!
        pause
        exit /b 1
    )
)

:: --- Step 2: Apply bridge-mode patch ---
echo [2/4] Applying bridge-mode patch to src/index.ts...

powershell -NoProfile -Command ^
  "$f = '%ND_DIR%\src\index.ts';" ^
  "$c = Get-Content $f -Raw;" ^
  "if ($c -match '__ND_BRIDGE_MODE') {" ^
  "  Write-Host '  Already patched - skipping.';" ^
  "} else {" ^
  "  $old = 'if \(document\.readyState === \"loading\"\) \{[\s\S]*?main\(\);\s*\}';" ^
  "  $patch = @(" ^
  "    '// Bridge mode: skip full UI init, just expose getRule for external callers.'," ^
  "    'if ((window as any).__ND_BRIDGE_MODE) {'," ^
  "    '  (window as any).__ND_getRule = getRule;'," ^
  "    '  (window as any).__ND_getHtmlDOM = getHtmlDOM;'," ^
  "    '  (window as any).__ND_READY = true;'," ^
  "    '  log.info(\"[Init] Bridge mode - skipping UI init, getRule exposed.\");'," ^
  "    '} else if (document.readyState === \"loading\") {'," ^
  "    '  document.addEventListener(\"DOMContentLoaded\", main);'," ^
  "    '} else {'," ^
  "    '  // noinspection JSIgnoredPromiseFromCall'," ^
  "    '  main();'," ^
  "    '}'" ^
  "  ) -join \"`r`n\";" ^
  "  $result = [regex]::Replace($c, $old, $patch);" ^
  "  if ($result -eq $c) {" ^
  "    Write-Host '  WARNING: Pattern not found. index.ts may have changed.';" ^
  "    Write-Host '  See EXTERNAL_NOVEL_SETUP.txt for manual patching.';" ^
  "  } else {" ^
  "    Set-Content $f $result -NoNewline;" ^
  "    Write-Host '  Patch applied successfully.';" ^
  "  }" ^
  "}"

:: --- Step 3: Build ---
echo [3/4] Building rules bundle...
pushd "%ND_DIR%"

if not exist "node_modules" (
    echo   Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo   Trying yarn instead...
        call yarn install
    )
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
