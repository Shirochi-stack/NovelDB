@echo off
setlocal EnableDelayedExpansion
for /f %%A in ('python -c "import app_version; print(app_version.APP_NAME)"') do set APP_NAME=%%A
for /f %%A in ('python -c "import app_version; print(app_version.APP_NAME_LITE)"') do set APP_NAME_LITE=%%A
echo ========================================
echo   Building NovelpiaDownloader
echo ========================================
echo.

echo [1/2] Building !APP_NAME! (Full + Playwright)...
echo.
echo Installing Playwright Chromium browsers for bundling...
python -m playwright install chromium
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo Playwright browser install failed. Cannot build the full executable.
    set FULL_RESULT=!ERRORLEVEL!
) else (
pyinstaller NovelpiaDownloader.spec --clean
set FULL_RESULT=!ERRORLEVEL!
)

echo.
echo [2/2] Building !APP_NAME_LITE! (No Playwright)...
echo.
pyinstaller NovelpiaDownloader_Lite.spec --clean
set LITE_RESULT=!ERRORLEVEL!

echo.
echo ========================================
echo   Build Results
echo ========================================
if !FULL_RESULT! EQU 0 (
    echo   !APP_NAME!.exe       : OK  ^(dist\!APP_NAME!.exe^)
) else (
    echo   !APP_NAME!.exe       : FAILED
)
if !LITE_RESULT! EQU 0 (
    echo   !APP_NAME_LITE!.exe  : OK  ^(dist\!APP_NAME_LITE!.exe^)
) else (
    echo   !APP_NAME_LITE!.exe  : FAILED
)
echo ========================================
echo.
pause
