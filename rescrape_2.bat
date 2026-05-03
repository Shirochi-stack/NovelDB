@echo off
echo ============================================
echo  A Certain site Rescrape
echo ============================================
echo.
echo  Requires: Korean VPN active
echo.

cd /d "%~dp0"

echo [1/5] Scraping KakaoPage novels via BFF API...
python scripts/scrape_kakao.py
if errorlevel 1 goto :error
echo.

echo [2/5] Reconciling descriptions for translation...
python scripts/extract_kakao_descriptions.py
if errorlevel 1 goto :error
echo.

echo [3/5] Extracting titles for translation...
python scripts/extract_titles.py kakao
if errorlevel 1 goto :error
echo.

echo [4/5] Extracting untranslated titles...
python scripts/extract_untranslated_kakao_titles.py
if errorlevel 1 goto :error
echo.

echo [5/5] Extracting untranslated descriptions...
python scripts/extract_untranslated_kakao_descriptions.py
if errorlevel 1 goto :error
echo.

echo ============================================
echo  Done! Files ready to push:
echo.
echo    docs/data/kakao_novels.json
echo    docs/data/kakao_descriptions.txt
echo    docs/data/kakao_descriptions_untranslated.txt
echo    docs/data/kakao_titles_en.txt
echo    docs/data/kakao_titles_untranslated.txt
echo.
echo  Run: git add docs/data ^& git commit -m "rescrape kakao" ^& git push
echo ============================================
pause
exit /b 0

:error
echo.
echo ============================================
echo  ERROR: A step failed. See output above.
echo  Make sure Korean VPN is active!
echo ============================================
pause
exit /b 1
