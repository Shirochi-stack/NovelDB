from pathlib import Path


def test_mobile_catalogs_use_an_offscreen_webview_for_protected_chapters():
    root = Path(__file__).resolve().parents[1]
    scripts = (
        root / "android" / "app" / "src" / "main" / "assets" / "catalog" / "generic.js",
        root / "ios" / "NpiaKit" / "Sources" / "NpiaWebEngine" / "Resources" / "catalog" / "generic.js",
    )
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        chapter = source[
            source.index("function loadChapter(index)") : source.index("function openChapter(index, at)")
        ]

        assert "window.__noveldb_window_script" in chapter, script
        assert "document.querySelector('.reader')" in chapter, script
        assert "clone.querySelectorAll('.reader-nav,script,style')" in chapter, script
        assert "fucknovelpia\\.com" in chapter, script
        assert "promise = protectedChapter" in chapter, script


def test_every_catalog_reader_shows_chapter_list_progress():
    root = Path(__file__).resolve().parents[1]
    scripts = (
        root / "noveldb" / "qt" / "catalog" / "generic" / "generic.js",
        root / "android" / "app" / "src" / "main" / "assets" / "catalog" / "generic.js",
        root / "ios" / "NpiaKit" / "Sources" / "NpiaWebEngine" / "Resources" / "catalog" / "generic.js",
    )
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert "function drawBookProgress()" in source, script
        assert "requests finished" in source, script
        assert "page ' + bookProgress.page" in source, script
        assert "Reading the chapter list from ' + source.site + '…', 120" in source, script
