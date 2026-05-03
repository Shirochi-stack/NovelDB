@echo off
:: update_rules 2.bat - update rules from Shirochi-stack fork
:: Just double-click this. Requires git, npm, and webpack via npx.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_rules 2.ps1"
pause
