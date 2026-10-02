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

echo [3/9] Selecting the newest description sources...
echo   (The shard builder automatically prefers a newer .gz corpus.)
echo.

echo [4/9] Building on-demand description shards...
python scripts/chunk_descriptions.py docs/data/descriptions.txt --prefix descriptions_shard --output-dir docs/data -n 128
if errorlevel 1 goto :error
python scripts/chunk_descriptions.py docs/data/sfc_descriptions.txt --prefix sfc_descriptions_shard --output-dir docs/data -n 128
if errorlevel 1 goto :error
python scripts/chunk_descriptions.py docs/data/kpage_descriptions.txt --prefix kpage_descriptions_shard --output-dir docs/data -n 128
if errorlevel 1 goto :error
echo.

echo [5/9] Chunking Npia data...
python scripts/chunk_and_compress.py --input docs/data/novels.json --prefix npia_chunk --output-dir docs/data -n 5 --translations docs/data/titles_en.txt
if errorlevel 1 goto :error
echo.

echo [6/9] Building Npia top rankings...
python scripts/build_npia_top.py
if errorlevel 1 goto :error
echo.

echo [7/9] Chunking SFC data...
python scripts/chunk_and_compress.py --input docs/data/sfc_novels.json --prefix sfc_chunk --output-dir docs/data -n 10 --translations docs/data/sfc_titles_en.txt
if errorlevel 1 goto :error
echo.

echo [8/9] Building SFC top rankings...
python scripts/build_sfc_top.py
if errorlevel 1 goto :error
echo.

echo [9/9] Chunking Kpage data...
python scripts/chunk_and_compress.py --input docs/data/kpage_novels.json --prefix kpage_chunk --output-dir docs/data -n 3 --translations docs/data/kpage_titles_en.txt
if errorlevel 1 echo   (Kpage data not found or failed — skipping)
echo.

echo Building available anonymous metadata source artifacts...
echo   New sources are staged and validated before promotion.
echo   Existing partial catalogs retain their coverage reports during this rebuild.
for %%S in (nweb jara mpia rbooks nseries) do (
    if exist "metadata\state\%%S.json.gz" (
        python scripts/metadata_pipeline.py build --source %%S --output-dir ".cache/metadata-build/%%S" --state-dir metadata/state
        if errorlevel 1 goto :error
        python scripts/metadata_pipeline.py promote --source %%S --output-dir ".cache/metadata-build/%%S" --target-dir docs/data --allow-partial --include-tags
        if errorlevel 1 goto :error
    )
)
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
