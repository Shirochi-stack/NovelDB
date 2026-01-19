@echo off
setlocal
REM Install dependencies from requirements.txt using venv if present
if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
    "%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
) else (
    python -m pip install --upgrade pip
    python -m pip install -r "%~dp0requirements.txt"
)
endlocal
