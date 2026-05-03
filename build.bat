@echo off
setlocal EnableDelayedExpansion
echo ========================================
echo   Building NovelpiaDownloader
echo ========================================
echo.

echo [1/2] Building ND42 (Full + Playwright)...
echo.
pyinstaller NovelpiaDownloader.spec --clean
set FULL_RESULT=!ERRORLEVEL!

echo.
echo [2/2] Building ND42_Lite (No Playwright)...
echo.
pyinstaller NovelpiaDownloader_Lite.spec --clean
set LITE_RESULT=!ERRORLEVEL!

echo.
echo ========================================
echo   Build Results
echo ========================================
if !FULL_RESULT! EQU 0 (
    echo   ND42.exe       : OK  ^(dist\ND42.exe^)
) else (
    echo   ND42.exe       : FAILED
)
if !LITE_RESULT! EQU 0 (
    echo   ND42_Lite.exe  : OK  ^(dist\ND42_Lite.exe^)
) else (
    echo   ND42_Lite.exe  : FAILED
)
echo ========================================
echo.
pause
