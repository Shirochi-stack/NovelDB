@echo off
setlocal
pushd "%~dp0"

REM Windows launcher for the NovelDB desktop app (the old window still starts with: python gui.py)
python "%~dp0noveldb_app.py"

popd
endlocal
