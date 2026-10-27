@echo off
setlocal
REM Install everything needed to run from source (Full app + bot), using venv if present
if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
    "%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-full.txt" -r "%~dp0requirements-bot.txt"
) else (
    python -m pip install --upgrade pip
    python -m pip install -r "%~dp0requirements-full.txt" -r "%~dp0requirements-bot.txt"
)
endlocal
