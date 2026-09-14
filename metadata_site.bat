@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
set "METADATA_SOURCE=%~1"
set "METADATA_COMMAND=%~2"
if /i not "%METADATA_SOURCE%"=="naver" if /i not "%METADATA_SOURCE%"=="joara" if /i not "%METADATA_SOURCE%"=="munpia" if /i not "%METADATA_SOURCE%"=="ridi" if /i not "%METADATA_SOURCE%"=="naverseries" goto :usage
set "METADATA_OUTPUT=.cache\metadata-build\%METADATA_SOURCE%"
set "METADATA_STATE=metadata\state"
if /i "%METADATA_COMMAND%"=="catalog" goto :collect
if /i "%METADATA_COMMAND%"=="rankings" goto :collect
if /i "%METADATA_COMMAND%"=="resume" goto :resume
if /i "%METADATA_COMMAND%"=="prepare" goto :local
if /i "%METADATA_COMMAND%"=="translate" goto :local
if /i "%METADATA_COMMAND%"=="merge" goto :local
if /i "%METADATA_COMMAND%"=="build" goto :local
if /i "%METADATA_COMMAND%"=="promote" goto :local
goto :usage

:collect
python scripts/metadata_pipeline.py run --source "%METADATA_SOURCE%" --mode "%METADATA_COMMAND%" --output-dir "%METADATA_OUTPUT%" --state-dir "%METADATA_STATE%" --max-runtime 18000
exit /b %ERRORLEVEL%

:resume
python scripts/metadata_pipeline.py run --source "%METADATA_SOURCE%" --mode catalog --resume --output-dir "%METADATA_OUTPUT%" --state-dir "%METADATA_STATE%" --max-runtime 18000
exit /b %ERRORLEVEL%

:local
python scripts/metadata_pipeline.py "%METADATA_COMMAND%" --source "%METADATA_SOURCE%" --output-dir "%METADATA_OUTPUT%" --state-dir "%METADATA_STATE%"
exit /b %ERRORLEVEL%

:usage
echo Usage: metadata_site.bat naver^|joara^|munpia^|ridi^|naverseries catalog^|rankings^|resume^|prepare^|translate^|merge^|build^|promote
echo Collection uses anonymous metadata only; translate explicitly invokes the configured translation API.
echo Output is staged under .cache\metadata-build. Promotion validates source artifacts before copying them.
echo For explicit preview directories or inspected partial promotion, use scripts/metadata_pipeline.py directly.
exit /b 2
