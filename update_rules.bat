@echo off
:: update_rules.bat - update rules from official 404-novel-project repo
:: Just double-click this. Requires git, npm, and webpack via npx.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_rules.ps1"
pause
