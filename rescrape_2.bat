@echo off
echo ============================================
echo  A Certain site Rescrape
echo ============================================
echo.
echo  Requires: Korean VPN active
echo.

cd /d "%~dp0"

echo [1/3] Scraping KakaoPage novels via BFF API...
python scripts/scrape_kakao.py
if errorlevel 1 goto :error
echo.

echo [2/3] Extracting titles for translation...
python scripts/extract_titles.py kakao
if errorlevel 1 goto :error
echo.

echo [3/3] Extracting untranslated titles...
python scripts/extract_untranslated_kakao_titles.py
if errorlevel 1 goto :error
echo.

echo ============================================
echo  Done! Files ready to push:
echo.
echo    docs/data/kakao_novels.json
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
