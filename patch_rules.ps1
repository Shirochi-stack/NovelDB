# patch_rules.ps1 — Patch, build, and copy novel-downloader rules for bridge mode
#
# Usage: .\patch_rules.ps1 [-NdDir "D:\Projects\novel-downloader"]
# If no NdDir given, defaults to D:\Projects\novel-downloader
param(
    [string]$NpiaDir = $PSScriptRoot,
    [string]$NdDir = "D:\Projects\novel-downloader"
)

Write-Host ""
Write-Host "========================================"
Write-Host "  NpiaDownloader - Rules Update Script"
Write-Host "========================================"
Write-Host ""
Write-Host "  Novel-Downloader: $NdDir"
Write-Host "  NpiaDownloader:   $NpiaDir"
Write-Host ""

$repoUrl = "https://github.com/404-novel-project/novel-downloader.git"
$indexTs = Join-Path $NdDir "src\index.ts"
$outFile = Join-Path $NpiaDir "rules-lib.js"

# --- Step 1: Clone or Pull ---
if (Test-Path $indexTs) {
    Write-Host "[1/4] Pulling latest novel-downloader..."
    Push-Location $NdDir
    git pull 2>&1 | ForEach-Object { Write-Host "  $_" }
    Pop-Location
} else {
    Write-Host "[1/4] Cloning novel-downloader from GitHub..."
    git clone $repoUrl $NdDir 2>&1 | ForEach-Object { Write-Host "  $_" }
    if (-not (Test-Path $indexTs)) {
        Write-Host "  ERROR: Clone failed!"
        exit 1
    }
}

# --- Step 2: Apply bridge-mode patch ---
Write-Host "[2/4] Applying bridge-mode patch to src/index.ts..."
$content = Get-Content $indexTs -Raw

if ($content -match '__ND_BRIDGE_MODE') {
    Write-Host "  Already patched - skipping."
} else {
    # The stock index.ts ends with:
    #   if (document.readyState === "loading") {
    #     document.addEventListener("DOMContentLoaded", main);
    #   } else {
    #     main();
    #   }
    # We replace it with bridge-mode aware version.
    $oldPattern = 'if \(document\.readyState === "loading"\) \{\s*document\.addEventListener\("DOMContentLoaded", main\);\s*\} else \{\s*// noinspection JSIgnoredPromiseFromCall\s*main\(\);\s*\}'

    $newCode = @'
// Bridge mode: skip full UI init, just expose internals for external callers.
// Exports:
//   __ND_getRule()    — returns the rule class/instance matching window.location
//   __ND_getHtmlDOM() — fetches a URL, returns { doc, charset } DOM object
//   __ND_cleanDOM()   — sanitises a DOM element for EPUB-safe XHTML output
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
        Write-Host "  Trying to append bridge mode at end of file..."

        # Fallback: just replace the simple if/else at the bottom
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

# --- Step 3: Build ---
Write-Host "[3/4] Building rules bundle..."
Push-Location $NdDir

if (-not (Test-Path "node_modules")) {
    Write-Host "  Installing dependencies (first time only)..."
    npm install 2>&1 | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" }
}

Write-Host "  Running webpack..."
npx webpack 2>&1 | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) {
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

Write-Host ""
Write-Host "========================================"
Write-Host "  Rules updated successfully!"
Write-Host "========================================"
Write-Host ""
