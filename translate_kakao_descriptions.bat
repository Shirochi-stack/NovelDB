@echo off
echo ============================================
echo  Translate KakaoPage Descriptions
echo ============================================
echo.

cd /d "%~dp0"

echo [1/3] Extracting untranslated Kakao descriptions...
python scripts/extract_untranslated_kakao_descriptions.py
if errorlevel 1 goto :error
echo.

echo [2/3] Translating with Grok...
python scripts/translate_with_grok.py docs/data/kakao_descriptions_untranslated.txt --lang korean --type descriptions
if errorlevel 1 goto :error
echo.

echo [3/3] Merging translated descriptions...
python scripts/merge_translated_kakao_descriptions.py
if errorlevel 1 goto :error
echo.

echo ============================================
echo  Done! Run rebuild_site.bat to embed updates.
echo ============================================
pause
exit /b 0

:error
echo.
echo ============================================
echo  ERROR: A step failed. Check GROK_API_KEY and output above.
echo ============================================
pause
exit /b 1
