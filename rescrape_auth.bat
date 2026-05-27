@echo off
echo ============================================
echo  Authenticated Novelpia Rescrape
echo ============================================
echo.
echo  This will scrape ALL Novelpia novels (including R19)
echo  using your loginkey from config.json, then extract
echo  descriptions for translation.
echo.

cd /d "%~dp0"

echo [1/8] Scraping Novelpia (authenticated)...
python scripts/scrape_npia.py
if errorlevel 1 goto :error
echo.

echo [2/8] Merging unique novel sets into novels.json...
python scripts/merge_unique_sets.py
if errorlevel 1 goto :error
echo.

echo [3/8] Extracting descriptions from novels_full.json...
python scripts/extract_descriptions.py
if errorlevel 1 goto :error
echo.

echo [4/8] Extracting untranslated descriptions...
python scripts/extract_untranslated_npia_descriptions.py
if errorlevel 1 goto :error
echo.

echo [5/8] Gzipping descriptions...
python scripts/gzip_text_files.py docs/data/descriptions.txt
if errorlevel 1 goto :error
echo.

echo [6/8] Chunking descriptions...
python scripts/chunk_descriptions.py docs/data/descriptions.txt --prefix descriptions_chunk --output-dir docs/data -n 3
if errorlevel 1 goto :error
echo.

echo [7/8] Rebuilding Novelpia chunks...
python scripts/chunk_and_compress.py --input docs/data/novels.json --prefix novelpia_chunk --output-dir docs/data -n 5 --translations docs/data/titles_en.txt
if errorlevel 1 goto :error
echo.

echo [8/8] Rebuilding Novelpia top data...
python scripts/build_novelpia_top.py
if errorlevel 1 goto :error
echo.

echo ============================================
echo  Done! Files ready to push:
echo.
echo    docs/data/novels.json
echo    docs/data/novels_full.json
echo    docs/data/descriptions.txt
echo    docs/data/descriptions.txt.gz
echo    docs/data/descriptions_chunk_*.txt.gz
echo    docs/data/descriptions_chunk_manifest.json
echo    docs/data/descriptions_untranslated.txt
echo    docs/data/novelpia_chunk_*.json.gz
echo    docs/data/novelpia_chunk_manifest.json
echo    docs/data/novelpia_top.json.gz
echo.
echo  Run: git add docs/data ^& git commit -m "rescrape" ^& git push
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
