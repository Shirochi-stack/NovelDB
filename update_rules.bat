@echo off
:: update_rules.bat — Download and patch novel-downloader rules
:: Just double-click this. No git, no npm, no webpack needed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_rules.ps1"
pause
