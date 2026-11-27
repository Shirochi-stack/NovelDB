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
    with pytest.raises(ValueError, match="no usable engine entry point"):
        custom_engines.describe("x = 1")
    with pytest.raises(ValueError, match="parse_chapter"):
        custom_engines.describe("class Engine:\n    def parse_book(self, url): pass\n")
    with pytest.raises(ValueError, match="plain value"):
        custom_engines.describe("class Engine:\n    label = make()\n    def parse_book(self, u): pass\n"
                                "    def parse_chapter(self, c, b): pass\n")


@pytest.mark.parametrize("source, expected_label", [
    ("""class ReaderEngine:
    label = 'Reader'
    sites = ['www.reader.test']
    def parse_book(self, url): return {'bookname': url, 'chapters': []}
    def parse_chapter(self, chapter, book): return {'contentText': 'ok'}
""", "Reader"),
    ("""LABEL = 'Functions'
SITES = 'fn.test'
def parse_book(url, transport):
    assert callable(transport)
    return {'bookname': url, 'chapters': []}
def parse_chapter(chapter, book, logger=None): return {'contentText': 'ok'}
""", "Functions"),
    ("""class CamelEngine:
    name = 'Camel'
    domains = ['camel.test']
    def parseBook(self, url): return {'bookname': url, 'chapters': []}
    def parseChapter(self, chapter, book): return {'contentText': 'ok'}
""", "Camel"),
])
def test_compatible_python_shapes_are_adapted_when_added(home, tmp_path, source, expected_label):
    from noveldb.core.engines import custom_engines

    candidate = tmp_path / f"{expected_label.lower()}.py"
    candidate.write_text(source, encoding="utf-8")
    name = custom_engines.add(str(candidate))
    installed = custom_engines.info(name)
    assert installed["label"] == expected_label
    assert "class Engine" in Path(installed["path"]).read_text(encoding="utf-8")
    engine = custom_engines.load(name, fetch=lambda *_a, **_k: None, log=lambda _line: None)
    assert engine.parse_book("https://example.invalid")["bookname"] == "https://example.invalid"


def test_constructor_type_errors_are_not_misread_as_an_old_constructor_signature(home, tmp_path):
    from noveldb.core.engines import custom_engines

    source = tmp_path / "broken.py"
    source.write_text("""class Engine:
    def __init__(self, fetch, log, render=None): raise TypeError('inside constructor')
    def parse_book(self, url): return {}
    def parse_chapter(self, chapter, book): return {}
""", encoding="utf-8")
    name = custom_engines.add(str(source))
    with pytest.raises(TypeError, match="inside constructor"):
        custom_engines.load(name, fetch=lambda *_a, **_k: None, log=lambda _line: None)


def test_common_book_and_chapter_result_shapes_are_normalised():
    from noveldb.core.engines import custom_engines

    class Compatible:
        label = "Compatible"

        @staticmethod
        def parse_book(url):
            return {"title": "A Book", "authors": ["One", "Two"],
                    "episodes": [{"link": url + "/1", "title": "First"}]}

        @staticmethod
        def parse_chapter(chapter, book):
            return ["https://img.test/1.jpg", "https://img.test/2.jpg"]

    runner = custom_engines._Runner("custom:test", Compatible(), [])
    book = runner.parse_book("https://reader.test/book")
    assert book["bookname"] == "A Book" and book["author"] == "One, Two"
    assert book["chapters"][0]["name"] == "First"
    chapter = runner.parse_chapter(book["chapters"][0], book)
    assert chapter["comic"] and chapter["imageCount"] == 2 and "<img" in chapter["contentHtml"]


def test_an_already_installed_mobile_api_engine_expands_its_public_subset():
    from noveldb.core.engines import custom_engines

    class Api:
        @staticmethod
        def titleInfo(**_kwargs):
            return {"titleInfo": {"totalEpisodeCount": 200}}

    class PreviouslyImported:
        label = "Imported API reader"
        api = Api()
        log = staticmethod(lambda _line: None)

        @staticmethod
        def parse_book(url):
            return {"bookname": "Example", "bookUrl": url, "_seriesNo": 1,
                    "chapters": [{"url": f"{url}#episode={number}", "name": str(number), "episodeNo": number}
                                 for number in range(1, 128)]}

    runner = custom_engines._Runner("custom:old", PreviouslyImported(), [])
    book = runner.parse_book("https://reader.test/list?title_no=1")
    assert len(book["chapters"]) == 200
    assert book["chapters"][127]["episodeNo"] == 128
    assert book["chapters"][127]["isAccessible"]


def test_a_standalone_api_downloader_application_is_converted_to_an_engine(monkeypatch):
    from noveldb.core.engines import custom_engines

    source = '''
import requests
import customtkinter as ctk

class ApiClient:
    def send_request(self): pass
    def get_signed_url(self): pass
    def __getattr__(self, name): return lambda **kwargs: {}

class DownloaderApp(ctk.CTk):
    def get_series_no(self): pass
    def _process_analysis(self): pass
    def _download_single_episode(self): pass
'''
    adapted = custom_engines.adapt(source)
    assert "customtkinter" not in adapted
    assert custom_engines.describe(adapted)["label"] == "Imported API reader"
    assert custom_engines.describe(adapted)["sites"]
    assert "the mobile API response contained no result" in adapted
    compile(adapted, "<standalone-downloader>", "exec")

    namespace = {}
    exec(adapted, namespace)
    engine = namespace["Engine"]()

    class FakeApi:
        bought = None
        calls = []

        def titleInfo(self, **_kwargs):
            return {"titleInfo": {"titleName": "Example", "totalEpisodeCount": 200}}

        def productRightList(self, **_kwargs):
            return {"rightList": []}

        def episodeList(self, **_kwargs):
            return {"episodeList": {"episode": [{"episodeNo": 1, "episodeTitle": "Public"}]}}

        def getProduct(self, episodeNo, **_kwargs):
            self.calls.append("getProduct")
            if episodeNo == 128:
                return {"product": {"episodeTitle": "Hidden", "saleUnitList": [
                    {"saleUnitType": "REWARD_AD", "productSaleUnitId": "returned-unit"}
                ]}}
            return None

        def productRight(self, **_kwargs):
            self.calls.append("productRight")
            return {"hasRight": False}

        def getImageSecureToken(self, **_kwargs):
            return {"token": "ok"}

        def buyProduct(self, **kwargs):
            self.bought = kwargs
            return {}

        def episodeInfoWithLogin(self, **_kwargs):
            return {"episodeInfo": {"episodeTitle": "Hidden", "imageInfo": [{"url": "https://img.test/128.jpg"}]}}

    engine.api = FakeApi()
    book = engine.parse_book("https://www.webtoons.com/en/example/list?title_no=1")
    assert len(book["chapters"]) == 200
    assert book["chapters"][127]["episodeNo"] == 128
    assert book["chapters"][127]["isAccessible"]
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    engine._session_ready = True
    result = engine.parse_chapter(book["chapters"][127], book)
    assert result["imageCount"] == 1
    assert engine.api.calls[-2:] == ["productRight", "getProduct"]
    assert engine.api.bought["productSaleUnitId"].endswith("-reward_ad-1")


def test_imported_mobile_flow_checks_a_live_right_before_product_options(monkeypatch):
    from noveldb.core.engines import custom_engines

    source = '''
class ApiClient:
    def send_request(self): pass
    def get_signed_url(self): pass
    def __getattr__(self, name): return lambda **kwargs: {}
class DownloaderApp:
    def get_series_no(self): pass
    def _process_analysis(self): pass
    def _download_single_episode(self): pass
'''
    namespace = {}
    exec(custom_engines.adapt(source), namespace)
    engine = namespace["Engine"]()

    class Api:
        def productRight(self, **_kwargs):
            return {"hasRight": True, "infinite": False}

        def getProduct(self, **_kwargs):
            raise AssertionError("getProduct must not run for an already acquired live right")

        def episodeInfoWithLogin(self, **_kwargs):
            return {"episodeInfo": {"episodeTitle": "Hidden", "imageInfo": [{"url": "https://img.test/128.jpg"}]}}

    engine.api = Api()
    result = engine.parse_chapter({"episodeNo": 128, "url": "#episode=128", "isFree": False}, {"_seriesNo": 1})
    assert result["imageCount"] == 1


def test_a_saved_mobile_session_is_loaded_validated_and_kept(tmp_path):
    from noveldb.core.engines import custom_engines

    path = tmp_path / "reader.py"
    path.write_text("", encoding="utf-8")
    custom_engines._save_session(path, {
        "neo_ses": "saved-session", "neo_chk": "saved-check", "device_key": "saved-device"
    })

    class Api:
        neo_ses = neo_chk = device_key = ""

        def load_session(self, ses, chk, device):
            self.neo_ses, self.neo_chk, self.device_key = ses, chk, device

        def getMemberInfo(self):
            return {"memberInfo": {"id": 1}}

    class Imported:
        api = Api()
        _session_ready = False

    lines = []
    custom_engines._prepare_legacy_session(path, Imported, lines.append)
    assert Imported._session_ready
    assert (Imported.api.neo_ses, Imported.api.neo_chk, Imported.api.device_key) == (
        "saved-session", "saved-check", "saved-device"
    )
    assert lines == ["Custom engine loaded its saved mobile API session."]


def test_app_logout_clears_cookie_and_imported_engine_session(home, tmp_path, monkeypatch):
    import external_scraper
    from external_scraper import ExternalScraper
    from noveldb.core.engines import custom_engines

    app_data = tmp_path / "app-data"
    base = tmp_path / "base"
    app_data.mkdir()
    base.mkdir()
    monkeypatch.setattr(external_scraper, "_get_app_data_dir", lambda: str(app_data))
    monkeypatch.setattr(external_scraper, "_get_base_dir", lambda: str(base))
    cookie = app_data / "webtoons_cookie.txt"
    cookie.write_text("NEO_SES=saved", encoding="utf-8")
    engine = custom_engines.folder() / "reader.py"
    engine.parent.mkdir(parents=True, exist_ok=True)
    custom_engines._save_session(engine, {"neo_ses": "saved", "neo_chk": "check", "device_key": "device"})

    scraper = ExternalScraper(logger=lambda _line: None)
    assert scraper.logout_webtoons_app()
    assert not cookie.exists()
    assert not engine.with_suffix(".session.json").exists()


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
