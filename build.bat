@echo off
echo Building NpiaDownloader...
echo.

pyinstaller NpiaDownloader.spec

echo.
if %ERRORLEVEL% EQU 0 (
    echo Build completed successfully!
    echo Executable location: dist\NpiaDownloader.exe
) else (
    echo Build failed with error code %ERRORLEVEL%
)
echo.
pause

