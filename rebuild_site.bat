@echo off
echo ============================================
echo  Rebuilding NovelDB Site Data
echo ============================================
echo.

cd /d "%~dp0"

echo [1/9] Merging unique novel sets...
python scripts/merge_unique_sets.py
if errorlevel 1 echo   (No novels_full.json found or merge skipped)
echo.

echo [2/9] Refreshing gzipped tag translations...
python scripts/merge_translated_tags.py --recompress-only
if errorlevel 1 goto :error
echo.

echo [3/9] Refreshing gzipped description files...
python scripts/gzip_text_files.py docs/data/descriptions.txt docs/data/sfacg_descriptions.txt docs/data/kakao_descriptions.txt
if errorlevel 1 goto :error
echo.

echo [4/9] Chunking description files...
python scripts/chunk_descriptions.py docs/data/descriptions.txt --prefix descriptions_chunk --output-dir docs/data -n 3
if errorlevel 1 goto :error
python scripts/chunk_descriptions.py docs/data/sfacg_descriptions.txt --prefix sfacg_descriptions_chunk --output-dir docs/data -n 10
if errorlevel 1 goto :error
python scripts/chunk_descriptions.py docs/data/kakao_descriptions.txt --prefix kakao_descriptions_chunk --output-dir docs/data -n 5
if errorlevel 1 goto :error
echo.

echo [5/9] Chunking Novelpia data...
python scripts/chunk_and_compress.py --input docs/data/novels.json --prefix novelpia_chunk --output-dir docs/data -n 5 --translations docs/data/titles_en.txt
if errorlevel 1 goto :error
echo.

echo [6/9] Building Novelpia top rankings...
python scripts/build_novelpia_top.py
if errorlevel 1 goto :error
echo.

echo [7/9] Chunking SFACG data...
python scripts/chunk_and_compress.py --input docs/data/sfacg_novels.json --prefix sfacg_chunk --output-dir docs/data -n 10 --translations docs/data/sfacg_titles_en.txt
if errorlevel 1 goto :error
echo.

echo [8/9] Building SFACG top rankings...
python scripts/build_sfacg_top.py
if errorlevel 1 goto :error
echo.

echo [9/9] Chunking Kakao data...
python scripts/chunk_and_compress.py --input docs/data/kakao_novels.json --prefix kakao_chunk --output-dir docs/data -n 3 --translations docs/data/kakao_titles_en.txt
if errorlevel 1 echo   (Kakao data not found or failed — skipping)
echo.

echo ============================================
echo  Done! Site data rebuilt successfully.
echo ============================================
if not "%NOPAUSE%"=="1" pause
exit /b 0

:error
echo.
echo ============================================
echo  ERROR: A step failed. See output above.
echo ============================================
if not "%NOPAUSE%"=="1" pause
exit /b 1
