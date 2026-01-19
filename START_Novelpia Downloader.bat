@echo off
setlocal
pushd "%~dp0"

REM Windows launcher for Novelpia Downloader Discord Bot
REM Calls the PowerShell helper that sets DISCORD_TOKEN and runs bot.py

powershell -NoLogo -ExecutionPolicy Bypass -File "%~dp0run_discord_bot.ps1"

popd
endlocal
