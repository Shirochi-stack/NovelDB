@echo off
setlocal
pushd "%~dp0"

REM Windows launcher for Novelpia Downloader GUI
python "%~dp0gui.py"

popd
endlocal
