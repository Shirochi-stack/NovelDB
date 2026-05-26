@echo off
echo ============================================
echo  Authenticated Novelpia Rescrape
echo ============================================
echo.
echo  This will scrape ALL Novelpia novels (including R19)
echo  using your loginkey from config.json, then extract
echo  descriptions for translation.
echo.

cd /d "%~dp0"

echo [1/4] Scraping Novelpia (authenticated)...
python scripts/scrape_npia.py
if errorlevel 1 goto :error
echo.

echo [2/4] Merging unique novel sets into novels.json...
python scripts/merge_unique_sets.py
if errorlevel 1 goto :error
echo.

echo [3/4] Extracting descriptions from novels_full.json...
python scripts/extract_descriptions.py
if errorlevel 1 goto :error
echo.

echo [4/4] Extracting untranslated descriptions...
python scripts/extract_untranslated_npia_descriptions.py
if errorlevel 1 goto :error
echo.

echo ============================================
echo  Done! Files ready to push:
echo.
echo    docs/data/novels.json
echo    docs/data/novels_full.json
echo    docs/data/descriptions.txt
echo    docs/data/descriptions_untranslated.txt
echo.
echo  Run: git add docs/data ^& git commit -m "rescrape" ^& git push
echo ============================================
pause
exit /b 0

:error
echo.
echo ============================================
echo  ERROR: A step failed. See output above.
echo ============================================
pause
exit /b 1
