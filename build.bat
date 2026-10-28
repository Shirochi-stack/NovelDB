@echo off
setlocal EnableDelayedExpansion
rem Builds the Windows desktop apps into dist-desktop\ (see scripts\build_desktop.py).
rem   build.bat            Full + Lite + TurboLite
rem   build.bat lite       one variant (full, lite or turbolite)
set VARIANTS=%*
if "%VARIANTS%"=="" set VARIANTS=full lite turbolite
set FAILED=
for %%V in (%VARIANTS%) do (
    echo ========================================
    echo   Building %%V
    echo ========================================
    set EXTRA=
    if /I "%%V"=="full" set EXTRA=--zip
    python scripts\build_desktop.py --variant %%V --package --verify --smoke !EXTRA!
    if !ERRORLEVEL! NEQ 0 set FAILED=!FAILED! %%V
)
echo.
if "%FAILED%"=="" (
    echo All builds finished. Files are in dist-desktop\
) else (
    echo Failed:%FAILED%
)
pause
