@echo off
:: update_rules.bat - rebuild rules-lib.js from the official 404-novel-project repository.
:: Just double-click this. Requires git, Node.js (npm) and Python.
:: (patch_rules.ps1 predates upstream's bootstrap/top.ts layout and no longer applies.)
python "%~dp0scripts\update_nd_rules.py" --validate
pause
