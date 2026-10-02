@echo off
echo ============================================
echo  A Certain site Rescrape
echo ============================================
echo.
echo  Uses Kpage's public BFF API
echo.

cd /d "%~dp0"

echo [1/5] Scraping Kpage novels via BFF API...
python scripts/scrape_kpage.py
if errorlevel 1 goto :error
echo.

echo [2/5] Reconciling descriptions for translation...
python scripts/extract_kpage_descriptions.py
if errorlevel 1 goto :error
echo.

echo [3/5] Extracting titles for translation...
python scripts/extract_titles.py kpage
if errorlevel 1 goto :error
echo.

echo [4/5] Extracting untranslated titles...
python scripts/extract_untranslated_kpage_titles.py
if errorlevel 1 goto :error
echo.

echo [5/5] Extracting untranslated descriptions...
python scripts/extract_untranslated_kpage_descriptions.py
if errorlevel 1 goto :error
echo.

echo ============================================
echo  Done! Files ready to push:
echo.
echo    docs/data/kpage_novels.json
echo    docs/data/kpage_descriptions.txt
echo    docs/data/kpage_descriptions_untranslated.txt
echo    docs/data/kpage_titles_en.txt
echo    docs/data/kpage_titles_untranslated.txt
echo.
echo  Run: git add docs/data ^& git commit -m "rescrape kpage" ^& git push
echo ============================================
pause
exit /b 0

:error
echo.
echo ============================================
echo  ERROR: A step failed. See output above.
echo  Check your network connection and Kpage API availability.
echo ============================================
pause
exit /b 1
