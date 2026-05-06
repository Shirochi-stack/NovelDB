# patch_rules 3.ps1 - Patch, build, and copy novel-downloader rules for bridge mode.
#
# Usage: .\patch_rules 3.ps1 [-NdDir "D:\Projects\novel-downloader-shirochi"]
# This local variant uses your existing checkout as-is. It does not fetch,
# pull, clone, or reset the novel-downloader repository.
param(
    [string]$NpiaDir = $PSScriptRoot,
    [string]$NdDir = "D:\Projects\novel-downloader-shirochi"
)

Write-Host ""
Write-Host "========================================"
Write-Host "  NpiaDownloader - Local Rules Builder"
Write-Host "========================================"
Write-Host ""
Write-Host "  Novel-Downloader: $NdDir"
Write-Host "  NpiaDownloader:   $NpiaDir"
Write-Host ""

$indexTs = Join-Path $NdDir "src\index.ts"
$webpackConfig = Join-Path $NdDir "webpack.config.js"
$outFile = Join-Path $NpiaDir "rules-lib.js"

# --- Step 1: Validate local checkout ---
$gitDir = Join-Path $NdDir ".git"
if (-not (Test-Path $gitDir)) {
    Write-Host "[1/4] ERROR: $NdDir is not a git checkout."
    Write-Host "  Point -NdDir at your local Shirochi fork checkout."
    exit 1
}
if (-not (Test-Path $indexTs)) {
    Write-Host "[1/4] ERROR: $indexTs not found."
    exit 1
}
if (-not (Test-Path $webpackConfig)) {
    Write-Host "[1/4] ERROR: $webpackConfig not found."
    exit 1
}

Write-Host "[1/4] Using local checkout without git refresh..."
Push-Location $NdDir
$branch = (git branch --show-current 2>$null)
$commit = (git rev-parse --short HEAD 2>$null)
if ($branch -or $commit) {
    Write-Host "  Branch: $branch"
    Write-Host "  Commit: $commit"
}
$dirty = (git status --short 2>$null)
if ($dirty) {
    Write-Host "  Local changes detected and preserved:"
    $dirty | ForEach-Object { Write-Host "    $_" }
}
Pop-Location

# --- Step 2: Apply bridge-mode patch ---
Write-Host "[2/4] Applying bridge-mode patch to src/index.ts..."
$content = Get-Content $indexTs -Raw

$bridgeImports = @(
    'import { getRule } from "./router/download";',
    'import { getHtmlDOM } from "./lib/http";',
    'import { cleanDOM } from "./lib/cleanDOM";'
)
foreach ($importLine in $bridgeImports) {
    if (-not $content.Contains($importLine)) {
        $content = $importLine + "`n" + $content
    }
}

if ($content -match '__ND_BRIDGE_MODE') {
    Set-Content $indexTs $content -NoNewline
    Write-Host "  Already patched - imports refreshed."
} else {
    $oldPattern = 'if \(document\.readyState === "loading"\) \{\s*document\.addEventListener\("DOMContentLoaded", main\);\s*\} else \{\s*// noinspection JSIgnoredPromiseFromCall\s*main\(\);\s*\}'

    $newCode = @'
// Bridge mode: skip full UI init, just expose internals for external callers.
// Exports:
//   __ND_getRule()    - returns the rule class/instance matching window.location
//   __ND_getHtmlDOM() - fetches a URL, returns { doc, charset } DOM object
//   __ND_cleanDOM()   - sanitises a DOM element for EPUB-safe XHTML output
if ((window as any).__ND_BRIDGE_MODE) {
  (window as any).__ND_getRule = getRule;
  (window as any).__ND_getHtmlDOM = getHtmlDOM;
  try { (window as any).__ND_cleanDOM = cleanDOM; } catch(e) {}
  (window as any).__ND_READY = true;
  log.info("[Init] Bridge mode - skipping UI init, getRule exposed.");
} else if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main);
} else {
  // noinspection JSIgnoredPromiseFromCall
  main();
}
'@

    $result = [regex]::Replace($content, $oldPattern, $newCode)
    if ($result -eq $content) {
        Write-Host "  WARNING: Pattern not found. index.ts structure may have changed."
        Write-Host "  Trying fallback patch..."

        $simpleOld = 'if (document.readyState === "loading") {'
        if ($content.Contains($simpleOld)) {
            $content = $content.Replace(
                'if (document.readyState === "loading") {' + "`n" +
                '  document.addEventListener("DOMContentLoaded", main);' + "`n" +
                '} else {' + "`n" +
                '  // noinspection JSIgnoredPromiseFromCall' + "`n" +
                '  main();' + "`n" +
                '}',
                $newCode
            )
            Set-Content $indexTs $content -NoNewline
            Write-Host "  Patch applied (fallback method)."
        } else {
            Write-Host "  ERROR: Could not patch. Manual patching required."
            exit 1
        }
    } else {
        Set-Content $indexTs $result -NoNewline
        Write-Host "  Patch applied successfully."
    }
}

Write-Host "  Patching webpack ts-loader for transpile-only builds..."
$webpackContent = Get-Content $webpackConfig -Raw
if ($webpackContent -notmatch 'transpileOnly:\s*true') {
    $webpackContent = $webpackContent.Replace(
        'use: ["ts-loader"],',
        'use: [{ loader: "ts-loader", options: { transpileOnly: true } }],'
    )
    Set-Content $webpackConfig $webpackContent -NoNewline
    Write-Host "  webpack.config.js patched."
} else {
    Write-Host "  webpack.config.js already patched."
}

# --- Step 3: Build ---
Write-Host "[3/4] Building rules bundle..."
Push-Location $NdDir

if (-not (Test-Path "node_modules")) {
    Write-Host "  Installing dependencies (first time only)..."
    npm.cmd install 2>&1 | ForEach-Object { Write-Host "  $_" }
}

Write-Host "  Running webpack..."
$webpackOutput = npx.cmd webpack --stats-error-details 2>&1
$webpackExit = $LASTEXITCODE
$webpackOutput | ForEach-Object { Write-Host "  $_" }
if ($webpackExit -ne 0) {
    Write-Host "  ERROR: Build failed!"
    Pop-Location
    exit 1
}
Pop-Location

# --- Step 4: Copy bundle ---
Write-Host "[4/4] Copying rules-lib.js..."
$bundlePath = Join-Path $NdDir "dist\bundle.user.js"
if (Test-Path $bundlePath) {
    Copy-Item $bundlePath $outFile -Force
    $size = [Math]::Round((Get-Item $outFile).Length / 1KB)
    Write-Host "  Done! ($size KB)"
} else {
    Write-Host "  ERROR: dist\bundle.user.js not found!"
    exit 1
}

Write-Host "  Verifying bridge files..."
$requiredBridgeFiles = @("gm_stubs.js", "bridge.js", "rules-lib.js")
$missingBridgeFiles = @()
foreach ($fileName in $requiredBridgeFiles) {
    $filePath = Join-Path $NpiaDir $fileName
    if (-not (Test-Path $filePath)) {
        $missingBridgeFiles += $filePath
    }
}
if ($missingBridgeFiles.Count -gt 0) {
    Write-Host "  ERROR: Missing required bridge file(s):"
    $missingBridgeFiles | ForEach-Object { Write-Host "    $_" }
    exit 1
}
Write-Host "  Bridge files OK."

Write-Host ""
Write-Host "========================================"
Write-Host "  Local rules built successfully!"
Write-Host "========================================"
Write-Host ""
