@echo off
echo ============================================
echo  Rebuilding NovelDB Site Data
echo ============================================
echo.

cd /d "%~dp0"

echo [1/4] Chunking Novelpia data...
python scripts/chunk_and_compress.py --input docs/data/novels.json --prefix novelpia_chunk --output-dir docs/data -n 5 --translations docs/data/titles_en.txt --descriptions docs/data/descriptions.txt
if errorlevel 1 goto :error
echo.

echo [2/4] Building Novelpia top rankings...
python scripts/build_novelpia_top.py
if errorlevel 1 goto :error
echo.

echo [3/4] Chunking SFACG data...
python scripts/chunk_and_compress.py --input docs/data/sfacg_novels.json --prefix sfacg_chunk --output-dir docs/data -n 10 --translations docs/data/sfacg_titles_en.txt --descriptions docs/data/sfacg_descriptions.txt
if errorlevel 1 goto :error
echo.

echo [4/4] Chunking Kakao data...
python scripts/chunk_and_compress.py --input docs/data/kakao_novels.json --prefix kakao_chunk --output-dir docs/data -n 3 --translations docs/data/kakao_titles_en.txt
if errorlevel 1 echo   (Kakao data not found or failed — skipping)
echo.

echo ============================================
echo  Done! Site data rebuilt successfully.
echo ============================================
pause
exit /b 0

:error
echo.
echo ============================================
echo  ERROR: A step failed. See output above.
echo ============================================
pause
exit /b 1
