"""Site engine order: settings round trip and how parse_book falls back between engines."""
import pytest

import external_scraper
from external_scraper import BrowserUnavailableError, ExternalScraper
from noveldb.core.settings.desktop_config import export_config, import_config
from noveldb.core.settings.legacy_vars import external_values
from noveldb.core.settings.model import SITE_ENGINES, AppSettings, EngineSettings, engine_order

BOOK = {"bookname": "Book", "chapters": [{"url": "https://site.test/c1", "name": "One"}]}
EMPTY = {"bookname": "Book", "chapters": []}
REST = ("lncrawl", "lnreader", "fanficfare", "mangapeak", "tako", "haruneko", "gallerydl", "generic")


def test_engine_order_keeps_known_engines_and_appends_missing():
    assert engine_order(["webtoepub", "bogus", "native"]) == ("webtoepub", "native", "novel_downloader") + REST
    assert engine_order(None) == SITE_ENGINES
    assert engine_order(SITE_ENGINES, ["novel_downloader"]) == ("native", "webtoepub") + REST
    assert SITE_ENGINES[-1] == "generic"  # the generic fallback comes last


def test_engine_settings_round_trip_through_config():
    settings = import_config({
        "source_engine_order": ["webtoepub", "native", "novel_downloader"],
        "source_engines_off": ["novel_downloader"],
        "webtoepub_auto_update": True,
    })
    assert settings.engines == EngineSettings(
        order=("webtoepub", "native", "novel_downloader") + REST, disabled=("novel_downloader",),
        webtoepub_auto_update=True)
    config = export_config(settings)
    assert config["source_engine_order"] == ["webtoepub", "native", "novel_downloader", *REST]
    assert config["source_engines_off"] == ["novel_downloader"]
    assert config["webtoepub_auto_update"] is True
    assert import_config(config).engines == settings.engines


def test_retired_comic_entries_use_the_saved_built_in_setting():
    settings = import_config({
        "source_engine_order": ["comix", "webtoepub", "mangak", "native"],
        "source_engines_off": ["comix", "native", "mangak"],
    })
    assert settings.engines.order == ("webtoepub", "native", "novel_downloader") + REST
    assert settings.engines.disabled == ("native",)
    assert "native" not in settings.engines.enabled()


def test_defaults_and_never_all_off():
    assert AppSettings().engines.enabled() == SITE_ENGINES
    assert AppSettings().engines.webtoepub_auto_update is True
    assert AppSettings().engines.nd_auto_update is True
    assert all(AppSettings().engines.auto_update(name) for name in ("lnreader", "lncrawl", "haruneko"))
    settings = import_config({"source_engines_off": list(SITE_ENGINES)})
    assert settings.engines.enabled() == SITE_ENGINES


def test_jobs_pass_the_enabled_order_to_the_scraper():
    settings = AppSettings()
    settings = settings.__class__(**{**settings.__dict__, "engines": EngineSettings(
        order=("webtoepub", "native", "novel_downloader"), disabled=("native",))})
    values = external_values(settings, "https://site.test/book", "reader", None)
    assert values["_var_engine_order"].get() == ["webtoepub", "novel_downloader", *REST]


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

    def _script_parse_book(self, engine, url):
        self.calls.append(engine)
        return self.results.get(engine)

    def _python_parse_book(self, engine, url):
        self.calls.append(engine)
        return self.results.get(engine)


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
    # With dynamic priority off, sites without a built-in scraper try every engine (page-based WebToEpub matches).
    s = Recorder({"webtoepub": BOOK}, native=False, knows=())
    s.dynamic_priority = False
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "novel_downloader", "webtoepub"]


def test_site_lists_come_from_the_bundles():
    s = ExternalScraper(logger=None)
    assert s._engine_knows_site("novel_downloader", "https://ncode.syosetu.com/n1234ab/")
    assert not s._engine_knows_site("novel_downloader", "https://novelfull.com/book.html")
    assert s._engine_knows_site("webtoepub", "https://www.novelfull.com/book.html")
    assert not s._engine_knows_site("webtoepub", "https://unknown.example/book")
    assert s._engine_knows_site("lnreader", "https://novelfire.net/book/shadow-slave")
    assert s._engine_knows_site("lncrawl", "https://www.novelupdates.com/series/x/")
    assert s._engine_knows_site("haruneko", "https://anikiga.com/manga/x/")
    assert s._engine_knows_site("fanficfare", "https://archiveofourown.org/works/1")
    assert s._engine_knows_site("gallerydl", "https://www.mangatown.com/manga/some_title/")
    assert not s._engine_knows_site("gallerydl", "https://www.royalroad.com/fiction/1")
    assert not s._engine_knows_site("lnreader", "https://unknown.example/book")
    # The generic fallback lists no site: it never follows a failed built-in scraper.
    assert not s._engine_knows_site("generic", "https://novelfire.net/book/shadow-slave")


def test_the_rule_sets_and_the_generic_fallback_come_after_webtoepub():
    s = Recorder({"generic": BOOK}, native=False, knows=())
    s.dynamic_priority = False
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "novel_downloader", "webtoepub", *REST]

    # The Python engines still run when the page did not load.
    s = Recorder({"lncrawl": BOOK}, native=False, page_ok=False, knows=())
    s.dynamic_priority = False
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["open", "lncrawl"]


def test_engine_books_fetch_chapters_through_their_engine(monkeypatch):
    s = ExternalScraper(logger=lambda _m: None)
    s._book_data = {"_lnreader": True}
    monkeypatch.setattr(s, "_script_parse_chapter", lambda script, index, chapter, **o: {"engine": script.engine})
    assert s.parse_chapter(0, {"url": "https://site.test/c1"})["engine"] == "lnreader"
    s._book_data = {"_generic": True}
    monkeypatch.setattr(s, "_python_parse_chapter", lambda engine, index, chapter, **o: {"engine": engine})
    assert s.parse_chapter(0, {"url": "https://site.test/c1"})["engine"] == "generic"


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


def test_dynamic_priority_skips_the_engines_that_do_not_list_the_site():
    s = Recorder({"lncrawl": None, "generic": BOOK}, native=False, knows=("lncrawl", "fanficfare"))
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["lncrawl", "fanficfare", "generic"]
    assert any("Dynamic priority" in line for line in s.lines)
    # No engine lists it: straight to the generic fallback.
    s = Recorder({"generic": BOOK}, native=False, knows=())
    assert s.parse_book("https://site.test/book") is BOOK
    assert s.calls == ["generic"]
    assert EngineSettings().dynamic_priority


def test_a_chapter_link_reads_the_book_above_it():
    # ncode-style /n1234ab/1/: the engine reads the chapter as a book of one; the page above it is the book.
    chapter, book = "https://site.test/n1234ab/1/", "https://site.test/n1234ab/"
    s = Recorder({}, native=False, knows=("webtoepub",))
    s.dynamic_priority = False
    one = {"bookname": "Prologue", "chapters": [{"url": chapter, "name": "Prologue"}]}
    full = {"bookname": "Book", "chapters": [{"url": chapter, "name": "1"}, {"url": book + "2/", "name": "2"}]}
    s._parse_book_engines = lambda url: full if url == book else one
    assert s.parse_book(chapter) is full
    assert any("trying the page above it: " + book in line for line in s.lines)


def test_a_one_chapter_book_keeps_its_own_page_when_nothing_above_is_longer():
    link = "https://site.test/works/9/"
    s = Recorder({}, native=False)
    one = {"bookname": "One-shot", "chapters": [{"url": link, "name": "One-shot"}]}
    asked = []

    def engines(url):
        asked.append(url)
        return one if url == link else None
    s._parse_book_engines = engines
    assert s.parse_book(link) is one
    assert asked == [link, "https://site.test/works/", link]  # read again last: the engine's state is the link's


def test_a_failed_link_that_is_no_chapter_does_not_climb():
    s = Recorder({}, native=False)
    asked = []
    s._parse_book_engines = lambda url: asked.append(url)
    assert s.parse_book("https://site.test/fiction/21220/some-book") is None
    assert asked == ["https://site.test/fiction/21220/some-book"]


def test_fanficfare_requests_carry_the_cookies_its_adapters_set():
    import http.cookiejar
    import time

    from noveldb.core.engines import fanficfare_engine

    sent = []
    fetcher = fanficfare_engine._fetcher_class()(
        lambda *_a, **_k: None, lambda *_a, **_k: [],
        lambda url, **options: sent.append(options["headers"]) or {"status": 200, "url": url, "content": b""})
    fetcher.get_cookiejar().set_cookie(http.cookiejar.Cookie(
        0, "toc_sorder", "asc", None, False, "www.example.com", False, False, "/", True, False, time.time() + 100,
        False, None, None, {}, False))
    fetcher.request("POST", "https://www.example.com/wp-admin/admin-ajax.php", parameters={"page": 1})
    assert sent[-1]["Cookie"] == "toc_sorder=asc"


def test_a_requests_cookie_header_goes_into_the_browser():
    from noveldb.core.engines.scraper_mixin import SiteEnginesMixin

    added = []

    class Context:
        def add_cookies(self, cookies):
            added.extend(cookies)

    class Page:
        context = Context()

    SiteEnginesMixin._give_browser_cookies(Page(), "https://www.example.com/x", {"Cookie": "toc_sorder=asc; a=b"})
    assert added == [{"name": "toc_sorder", "value": "asc", "url": "https://www.example.com/x"},
                     {"name": "a", "value": "b", "url": "https://www.example.com/x"}]


def test_the_generic_engine_goes_first_on_the_sites_it_reads_best():
    """fanfox, mangahere and webtoons: gallery-dl and HaruNeko list them, but their pages come up missing there."""
    for url in ("https://fanfox.net/manga/one_piece/", "https://www.webtoons.com/en/fantasy/tower-of-god/list?title_no=95"):
        s = Recorder({"gallerydl": BOOK, "haruneko": BOOK, "generic": BOOK}, native=False, knows=("haruneko", "gallerydl"))
        assert s.parse_book(url) is BOOK
        assert set(s.calls) == {"generic"}  # (the address and the ones above it, each by the generic engine)
        # It reads nothing: the others follow, as before.
        s = Recorder({"generic": None, "haruneko": BOOK}, native=False, knows=("haruneko", "gallerydl"))
        assert s.parse_book(url) is BOOK
        assert s.calls[0] == "generic" and "haruneko" in s.calls
