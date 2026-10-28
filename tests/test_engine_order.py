"""Site engine order: settings round trip and how parse_book falls back between engines."""
import pytest

import external_scraper
from external_scraper import BrowserUnavailableError, ExternalScraper
from noveldb.core.settings.desktop_config import export_config, import_config
from noveldb.core.settings.legacy_vars import external_values
from noveldb.core.settings.model import SITE_ENGINES, AppSettings, EngineSettings, engine_order

BOOK = {"bookname": "Book", "chapters": [{"url": "https://site.test/c1", "name": "One"}]}
EMPTY = {"bookname": "Book", "chapters": []}


def test_engine_order_keeps_known_engines_and_appends_missing():
    assert engine_order(["webtoepub", "bogus", "native"]) == ("webtoepub", "native", "novel_downloader")
    assert engine_order(None) == SITE_ENGINES
    assert engine_order(SITE_ENGINES, ["novel_downloader"]) == ("native", "webtoepub")


def test_engine_settings_round_trip_through_config():
    settings = import_config({
        "source_engine_order": ["webtoepub", "native", "novel_downloader"],
        "source_engines_off": ["novel_downloader"],
        "webtoepub_auto_update": True,
    })
    assert settings.engines == EngineSettings(
        order=("webtoepub", "native", "novel_downloader"), disabled=("novel_downloader",),
        webtoepub_auto_update=True)
    config = export_config(settings)
    assert config["source_engine_order"] == ["webtoepub", "native", "novel_downloader"]
    assert config["source_engines_off"] == ["novel_downloader"]
    assert config["webtoepub_auto_update"] is True
    assert import_config(config).engines == settings.engines


def test_defaults_and_never_all_off():
    assert AppSettings().engines.enabled() == ("native", "novel_downloader", "webtoepub")
    assert AppSettings().engines.webtoepub_auto_update is False
    settings = import_config({"source_engines_off": list(SITE_ENGINES)})
    assert settings.engines.enabled() == SITE_ENGINES


def test_jobs_pass_the_enabled_order_to_the_scraper():
    settings = AppSettings()
    settings = settings.__class__(**{**settings.__dict__, "engines": EngineSettings(
        order=("webtoepub", "native", "novel_downloader"), disabled=("native",))})
    values = external_values(settings, "https://site.test/book", "reader", None)
    assert values["_var_engine_order"].get() == ["webtoepub", "novel_downloader"]


class Recorder(ExternalScraper):
    """parse_book with every engine stubbed: results[engine] is what it returns."""

    def __init__(self, results, native=True, page_ok=True, browser=True, knows=("novel_downloader", "webtoepub")):
        super().__init__(logger=self._log_line)
        self.lines = []
        self.calls = []
        self.results = results
        self.native = native
        self.page_ok = page_ok
        self.browser = browser
        self.knows = knows

    def _engine_knows_site(self, engine, url):
        return engine in self.knows

    def _log_line(self, line):
        self.lines.append(line)

    def _native_book_parser(self, url):
        if not self.native:
            return None

        def parse(_url):
            self.calls.append("native")
            return self.results.get("native")
        return ("[Test] native", parse)

    def _open_book_page(self, url):
        self.calls.append("open")
        if not self.browser:
            raise BrowserUnavailableError("no browser")
        return self.page_ok

    def _nd_parse_book(self, url, navigate=True):
        self.calls.append("novel_downloader")
        return self.results.get("novel_downloader")

    def _w2e_parse_book(self, url):
        self.calls.append("webtoepub")
        return self.results.get("webtoepub")


def test_native_scraper_wins_without_touching_the_browser():
    s = Recorder({"native": BOOK})
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["native"]


def test_falls_back_to_webtoepub_after_native_and_rules_fail():
    s = Recorder({"native": None, "novel_downloader": EMPTY, "webtoepub": BOOK})
    assert s.parse_book("https://site.test/book") is BOOK
    # One page load serves both JS engines.
    assert s.calls == ["native", "open", "novel_downloader", "webtoepub"]
    assert any("trying WebToEpub parsers next" in line for line in s.lines)


def test_rule_engines_follow_a_built_in_scraper_only_for_sites_they_list():
    s = Recorder({"native": None, "webtoepub": BOOK}, knows=())
    assert s.parse_book("https://site.test/book") is None
    assert s.calls == ["native"]
    s = Recorder({"native": None, "novel_downloader": None, "webtoepub": BOOK}, knows=("webtoepub",))
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["native", "open", "webtoepub"]
    # Sites without a built-in scraper try every engine (page-based WebToEpub matches).
    s = Recorder({"webtoepub": BOOK}, native=False, knows=())
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "novel_downloader", "webtoepub"]


def test_site_lists_come_from_the_bundles():
    s = ExternalScraper(logger=None)
    assert s._engine_knows_site("novel_downloader", "https://ncode.syosetu.com/n1234ab/")
    assert not s._engine_knows_site("novel_downloader", "https://novelfull.com/book.html")
    assert s._engine_knows_site("webtoepub", "https://www.novelfull.com/book.html")
    assert not s._engine_knows_site("webtoepub", "https://unknown.example/book")


def test_user_order_and_switched_off_engines():
    s = Recorder({"webtoepub": BOOK, "native": BOOK})
    s.engine_order = ["webtoepub", "native"]
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "webtoepub"]

    s = Recorder({"native": BOOK, "webtoepub": None})
    s.engine_order = ["webtoepub"]
    assert s.parse_book("https://site.test/book") is None
    assert s.calls == ["open", "webtoepub"]


def test_no_engines_left():
    s = Recorder({"native": BOOK})
    s.engine_order = []
    assert s.parse_book("https://site.test/book") is None
    assert s.calls == []


def test_a_native_result_without_chapters_is_kept_when_nothing_else_works():
    s = Recorder({"native": EMPTY, "novel_downloader": None, "webtoepub": None})
    assert s.parse_book("https://site.test/book") is EMPTY


def test_abort_reason_stops_the_fallback():
    s = Recorder({})

    def native(_url):
        s.calls.append("native")
        s.abort_reason = "Finish the site's human check first."
        return None
    s._native_book_parser = lambda url: ("", native)
    assert s.parse_book("https://site.test/book") is None
    assert s.calls == ["native"]


def test_missing_browser_still_runs_the_built_in_scraper():
    s = Recorder({"native": BOOK}, browser=False)
    s.engine_order = ["webtoepub", "novel_downloader", "native"]
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "native"]

    s = Recorder({}, native=False, browser=False)
    with pytest.raises(BrowserUnavailableError):
        s.parse_book("https://site.test/book")


def test_failed_page_load_skips_both_js_engines():
    s = Recorder({"native": BOOK}, page_ok=False)
    s.engine_order = ["novel_downloader", "webtoepub", "native"]
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "native"]


def test_webtoepub_books_fetch_chapters_through_webtoepub(monkeypatch):
    s = ExternalScraper(logger=lambda _m: None)
    s._book_data = {"_webtoepub": True}
    seen = {}

    def fake(index, chapter, **options):
        seen["chapter"] = (index, chapter["url"])
        return {"chapterName": "One", "contentHtml": "<p>x</p>"}
    monkeypatch.setattr(s, "_w2e_parse_chapter", fake)
    monkeypatch.setattr(s, "_sfc_should_prefer_app_api", lambda: pytest.fail("sfc path used"))
    assert s.parse_chapter(0, {"url": "https://site.test/c1", "name": "One"})["chapterName"] == "One"
    assert seen["chapter"] == (0, "https://site.test/c1")


def test_scraper_defaults_to_every_engine():
    assert external_scraper.ExternalScraper(logger=None).engine_order == list(SITE_ENGINES)
