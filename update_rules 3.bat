@echo off
:: update_rules 3.bat - build rules from the local Shirochi fork checkout
:: Just double-click this. Does not fetch, pull, or reset novel-downloader.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch_rules 3.ps1"
pause
