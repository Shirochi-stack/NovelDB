"""Custom site engines: a user's Python file in the engine list and the download chain."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "custom_engine_example.py"

BOOK = """<html><body><h1>The Wandering Sword</h1><span class="author">Lan Yu</span>
<div class="synopsis">A swordsman walks the world.</div>
<ul class="chapter-list"><li><a href="/book/sword/1">Chapter 1: Ashes</a></li>
<li><a href="/book/sword/2">Chapter 2: Rain</a></li></ul></body></html>"""
CHAPTER = """<html><body><div id="chapter-content"><p>The village was still burning.</p>
<p>Advertisement</p><p>He walked on.</p></div></body></html>"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.core import paths

    paths.reset_cache()
    yield tmp_path
    paths.reset_cache()


def test_an_engine_file_is_read_for_its_label_and_sites_without_running_it(tmp_path):
    from noveldb.core.engines import custom_engines

    info = custom_engines.describe(EXAMPLE.read_text(encoding="utf-8"))
    assert info == {"label": "Example Novels", "sites": ["novels.example"]}
    with pytest.raises(ValueError, match="no class Engine"):
        custom_engines.describe("x = 1")
    with pytest.raises(ValueError, match="parse_chapter"):
        custom_engines.describe("class Engine:\n    def parse_book(self, url): pass\n")
    with pytest.raises(ValueError, match="plain value"):
        custom_engines.describe("class Engine:\n    label = make()\n    def parse_book(self, u): pass\n"
                                "    def parse_chapter(self, c, b): pass\n")


def test_an_added_engine_joins_the_order_before_the_generic_fallback_and_can_be_removed(home):
    from noveldb.core.engines import custom_engines
    from noveldb.core.settings.model import ENGINE_GENERIC, SITE_ENGINES, EngineSettings, engine_label

    name = custom_engines.add(str(EXAMPLE))
    assert name == "custom:custom_engine_example"
    order = EngineSettings().full_order()
    assert order.index(name) == order.index(ENGINE_GENERIC) - 1
    assert engine_label(name) == "Example Novels (custom)"
    assert custom_engines.knows_site(name, "https://www.novels.example/book/sword")
    assert not custom_engines.knows_site(name, "https://other.example/book/sword")
    # A saved order keeps it where the user put it, and switched off.
    settings = EngineSettings(order=(name,) + SITE_ENGINES, disabled=(name,))
    assert settings.full_order()[0] == name and name not in settings.enabled()
    custom_engines.remove(name)
    assert name not in EngineSettings().full_order()


def test_a_custom_engine_reads_a_book_and_its_chapters_through_the_download_chain(home):
    from external_scraper import ExternalScraper
    from noveldb.core.engines import custom_engines

    name = custom_engines.add(str(EXAMPLE))
    pages = {"https://novels.example/book/sword": BOOK, "https://novels.example/book/sword/1": CHAPTER}
    lines = []
    scraper = ExternalScraper(logger=lines.append)
    scraper.engine_order = [name, "lncrawl", "generic"]  # lightnovel-crawler does not list the site
    scraper.dynamic_priority = True
    scraper.engine_fetch = lambda url, **_o: (
        {"status": 200, "url": url, "text": pages[url], "content": pages[url].encode(), "headers": ""}
        if url in pages else {"status": 404, "url": url, "text": "", "content": b"", "headers": ""})
    try:
        book = scraper.parse_book("https://novels.example/book/sword")
        assert book["bookname"] == "The Wandering Sword" and book["author"] == "Lan Yu"
        assert [c["name"] for c in book["chapters"]] == ["Chapter 1: Ashes", "Chapter 2: Rain"]
        assert book["ruleName"] == "Example Novels (custom engine)"
        chapter = scraper.parse_chapter(0, book["chapters"][0], interval=0)
        assert chapter["contentText"] == "The village was still burning.\nHe walked on."
        assert chapter["contentHtml"] == "<p>The village was still burning.</p><p>He walked on.</p>"
        assert any("Example Novels (custom)" in line for line in lines)  # dynamic priority names it
    finally:
        scraper.cleanup()
