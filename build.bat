@echo off
echo Building NovelpiaDownloader...
echo.

pyinstaller NovelpiaDownloader.spec

echo.
if %ERRORLEVEL% EQU 0 (
    echo Build completed successfully!
    echo Executable location: dist\NovelpiaDownloader.exe
) else (
    echo Build failed with error code %ERRORLEVEL%
)
echo.
pause
