# Novelpia Downloader Discord Bot Launcher

Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

Write-Host "Starting Novelpia Discord Bot..." -ForegroundColor Cyan
Write-Host ""

# Set your Discord bot token here (required)
# IMPORTANT: replace the placeholder before running
$env:DISCORD_TOKEN = "INSERT YOUR DISCORD TOKEN HERE"

# Per-user encrypted credentials master key (32+ chars recommended)
$env:USER_CFG_MASTER_KEY = "INSERT YOUR MASTER KEY HERE"

# Optional: Gofile token for uploads (recommended for higher limits)
# $env:GOFILE_TOKEN = "YOUR_GOFILE_TOKEN"

if ([string]::IsNullOrWhiteSpace($env:DISCORD_TOKEN) -or $env:DISCORD_TOKEN -eq "YOUR_DISCORD_BOT_TOKEN") {
    Write-Host "ERROR: Set DISCORD_TOKEN in run_discord_bot.ps1 before running." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Change to script directory (repository root)
Set-Location "$PSScriptRoot"

# Run the bot (uses bot.py)
python bot.py

# Keep window open after bot stops
Write-Host ""
Write-Host "Bot stopped. Press Enter to exit..." -ForegroundColor Yellow
Read-Host
