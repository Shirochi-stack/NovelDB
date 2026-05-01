# patch_rules.ps1 — Download and patch novel-downloader rules for bridge mode
param(
    [string]$NpiaDir = $PSScriptRoot
)

$bundleUrl = "https://github.com/yingziwu/novel-downloader/raw/gh-pages/bundle.user.js"
$tempFile  = Join-Path $NpiaDir "rules-lib.tmp.js"
$outFile   = Join-Path $NpiaDir "rules-lib.js"

Write-Host ""
Write-Host "========================================"
Write-Host "  NpiaDownloader - Rules Update Script"
Write-Host "========================================"
Write-Host ""

# Step 1: Download
Write-Host "[1/2] Downloading latest bundle from GitHub..."
try {
    Invoke-WebRequest -Uri $bundleUrl -OutFile $tempFile -UseBasicParsing
    Write-Host "  Downloaded OK."
} catch {
    Write-Host "  ERROR: Download failed! $_"
    exit 1
}

if (-not (Test-Path $tempFile)) {
    Write-Host "  ERROR: File not found after download!"
    exit 1
}

# Step 2: Patch
Write-Host "[2/2] Patching for bridge mode..."
$content = [System.IO.File]::ReadAllText($tempFile)

if ($content -match '__ND_BRIDGE_MODE') {
    Write-Host "  Already patched - skipping."
} else {
    # The stock bundle's entry point ends with something like:
    #   "loading"===document.readyState?document.addEventListener("DOMContentLoaded",X):X()
    # where X is the minified name of the main() function.
    # We wrap it in a bridge-mode check.
    $pattern = '"loading"===document\.readyState\?document\.addEventListener\("DOMContentLoaded",(\w+)\):\1\(\)'
    $m = [regex]::Match($content, $pattern)

    if ($m.Success) {
        $mainVar = $m.Groups[1].Value
        $oldCode = $m.Value
        $newCode = 'window.__ND_BRIDGE_MODE?(window.__ND_getRule=getRule,window.__ND_getHtmlDOM=getHtmlDOM,window.__ND_READY=!0,console.log("[Init] Bridge mode active")):("loading"===document.readyState?document.addEventListener("DOMContentLoaded",' + $mainVar + '):' + $mainVar + '())'
        $content = $content.Replace($oldCode, $newCode)
        Write-Host "  Patch applied successfully (main=$mainVar)."
    } else {
        Write-Host "  WARNING: Could not find main() pattern in bundle."
        Write-Host "  The bundle format may have changed. Using as-is."
    }
}

[System.IO.File]::WriteAllText($outFile, $content)
Write-Host "  Saved: $outFile"

# Cleanup
if (Test-Path $tempFile) { Remove-Item $tempFile }

Write-Host ""
Write-Host "========================================"
Write-Host "  Rules updated successfully!"
Write-Host "========================================"
Write-Host ""
