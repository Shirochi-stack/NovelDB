"""Desktop Catalog page: bridge messages, the link cart and where outside links go."""

import json
import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")

from PySide6.QtCore import QEvent, Qt, QUrl  # noqa: E402

from noveldb.core.catalog.urls import NOVELDB_NEXT_URL  # noqa: E402
from noveldb.core.settings.model import NOVELDB_CATALOG_URL  # noqa: E402
from noveldb.qt.catalog import web  # noqa: E402


def test_parse_message_accepts_bridge_messages():
    assert web.parse_message('{"type": "ready"}') == {"type": "ready"}
    message = web.parse_message(json.dumps({"type": "cartAdd", "url": "https://x.test/n/1", "title": "T", "source": "s"}))
    assert message["url"] == "https://x.test/n/1" and message["title"] == "T"


def test_local_open_keeps_loading_visible_until_reader_opened(catalog, qtbot, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget
    catalog.view = QWidget(catalog)
    catalog.stack.addWidget(catalog.view)
    seen = []
    refreshed = []
    catalog.generic = SimpleNamespace(read_local=lambda *args: seen.append(args), handle=lambda *_: None,
                                      refresh_local=refreshed.append)
    catalog._generic_ready = True
    monkeypatch.setattr(catalog, "_on_generic_page", lambda: True)
    catalog.read_local(tmp_path / "Book.epub", "Book")
    catalog._reveal_when_ready(100)
    assert catalog.stack.currentWidget() is catalog.placeholder
    assert seen and catalog.placeholder.text() == "Opening book…"
    assert web.parse_message('{"type":"genericReaderOpened"}')
    catalog._message({"type": "genericReaderOpened"})
    assert catalog.stack.currentWidget() is catalog.view
    catalog.context.hub.jobSaved.emit("update", str(tmp_path / "Book.epub"))
    assert refreshed == [tmp_path / "Book.epub"]


@pytest.mark.parametrize("text", [
    "not json", "[]", '{"type": "eval"}', '{"type": "open"}', '{"type": "open", "url": "javascript:alert(1)"}',
    '{"type": "cartAdd", "url": 5}', json.dumps({"type": "open", "url": "https://x.test/" + "a" * 5000}),
])
def test_parse_message_rejects_everything_else(text):
    assert web.parse_message(text) is None


def test_parse_message_accepts_the_page_address():
    message = web.parse_message(json.dumps({"type": "location", "url": "https://x.test/novel/7", "title": "Seven"}))
    assert message == {"type": "location", "url": "https://x.test/novel/7", "title": "Seven"}
    assert web.parse_message(json.dumps({"type": "location", "url": "https://x.test/", "title": 5}))["title"] == ""
    assert web.parse_message('{"type": "location"}') is None
    assert web.parse_message('{"type": "location", "url": "about:blank"}') is None


def test_parse_message_blanks_odd_titles():
    message = web.parse_message('{"type": "open", "url": "https://x.test/1", "title": {"x": 1}}')
    assert message["title"] == ""


def test_bridge_script_bundles_channel_shim_and_bridge(qapp):
    source = web.bridge_script().sourceCode()
    assert "QWebChannel" in source and "window.NovelDBQt" in source and "window.NovelDBApp" in source
    assert source.index("window.NovelDBQt =") < source.index("window.NovelDBApp =")


def test_leaving_the_catalog_pauses_the_same_reader_session(catalog, qtbot):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QStackedWidget, QWidget

    changes = []
    catalog.view = QWidget(catalog)
    catalog.generic = SimpleNamespace(set_reader_visible=changes.append)
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    stack.addWidget(catalog)
    stack.addWidget(QWidget())
    stack.show()
    assert changes == [True]
    stack.setCurrentIndex(1)
    stack.setCurrentIndex(0)
    assert changes == [True, False, True]


@pytest.fixture
def catalog(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext

    ctx = AppContext(start_workers=False)
    page = web.CatalogPage(ctx)  # never shown: no web view is created
    opened = []
    monkeypatch.setattr(web.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    page.opened = opened
    yield page
    ctx._save_timer.stop()  # a pending save must not outlive the test's NOVELDB_HOME


def test_zoom_moves_in_browser_steps():
    assert web.next_zoom(1.0, -1) == 0.9
    assert web.next_zoom(0.9, -1) == 0.8
    assert web.next_zoom(0.8, -1) == 0.75
    assert web.next_zoom(0.25, -1) == 0.25
    assert web.next_zoom(1.0, 1) == 1.1
    assert web.next_zoom(0.85, 1) == 0.9  # an older setting snaps to the next step
    assert web.next_zoom(3.0, 1) == 3.0


def test_zoom_gestures_inside_the_page_zoom_instead_of_scrolling(qapp):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QKeyEvent, QWheelEvent

    view = web.CatalogView()
    steps = []
    view.zoomRequested.connect(steps.append)

    def wheel(dy, modifiers):
        return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                           modifiers, Qt.ScrollPhase.NoScrollPhase, False)

    ctrl = Qt.KeyboardModifier.ControlModifier
    assert view.eventFilter(view, wheel(-120, ctrl)) is True  # consumed: the page does not scroll
    assert view.eventFilter(view, wheel(120, ctrl)) is True
    for _ in range(3):  # a touchpad's small deltas add up to one step
        view.eventFilter(view, wheel(-40, ctrl))
    assert view.eventFilter(view, wheel(-120, Qt.KeyboardModifier.NoModifier)) is False  # plain scrolling
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Minus, ctrl)
    assert view.eventFilter(view, override) is True and override.isAccepted()
    view.eventFilter(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Minus, ctrl))
    view.eventFilter(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_0, ctrl))
    assert steps == [-1, 1, -1, -1, 0]
    view.deleteLater()


def test_zoom_steps_update_the_setting_and_the_label(catalog):
    catalog.zoom_step(-1)
    catalog.zoom_step(-1)
    assert catalog.context.settings.catalog.zoom == 0.8
    catalog._show_zoom()
    assert catalog.zoom_label.text() == "80%"
    catalog.set_zoom(0.1)
    assert catalog.context.settings.catalog.zoom == 0.25


def test_cart_messages_update_the_link_cart(catalog):
    catalog._message({"type": "cartAdd", "url": "https://x.test/novel/7", "title": "Seven"})
    assert catalog.context.cart.urls == ["https://x.test/novel/7"]
    assert catalog.cart_button.text().endswith("(1)")
    catalog._message({"type": "cartRemove", "url": "https://x.test/novel/7"})
    assert catalog.context.cart.urls == []


def test_open_on_noveldb_goes_to_the_browser(catalog):
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, url=NOVELDB_CATALOG_URL)))
    catalog._message({"type": "open", "url": "https://x.test/novel/9", "title": "Nine"})
    assert catalog.opened == ["https://x.test/novel/9"]
    assert catalog.context.cart.urls == []


def test_outside_links_on_unknown_sites_go_to_the_cart_unless_turned_off(catalog):
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, url="https://catalog.example/", cart_links=True)))
    catalog.leave("https://x.test/novel/3", "Three")
    assert catalog.context.cart.urls == ["https://x.test/novel/3"]
    assert catalog.opened == []
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, cart_links=False)))
    catalog.leave("https://x.test/novel/4")
    assert catalog.opened == ["https://x.test/novel/4"]


def test_outside_links_open_in_the_browser_on_noveldb_next_and_supported_sites(catalog):
    """The setting only applies to sites that are not NovelDB, NovelDB Next or a site NovelDB downloads from."""
    supported = "https://global." + "novel" + "pia.com/"
    for site in (NOVELDB_NEXT_URL, NOVELDB_CATALOG_URL, supported):
        catalog.context.update_settings(lambda s, site=site: replace(s, catalog=replace(s.catalog, url=site, cart_links=True)))
        catalog.opened.clear()
        catalog.leave("https://x.test/novel/5", "Five")
        assert catalog.opened == ["https://x.test/novel/5"], site
        assert catalog.context.cart.urls == []


class FakePage:
    """Stands in for the web page: records what the app tells the bridge."""

    def __init__(self):
        self.scripts = []

    def runJavaScript(self, code, world=None):
        self.scripts.append(code)

    def setBackgroundColor(self, color):
        pass

    def mode(self):
        """The last setMode({...}) the page was sent, as a dict."""
        code = self.scripts[-1]
        start = code.index("NovelDBApp.setMode(") + len("NovelDBApp.setMode(")
        return json.loads(code[start:code.rindex("}))") + 1])


def test_the_download_button_is_offered_for_downloadable_pages_of_supported_sites(catalog):
    host = "global." + "novel" + "pia.com"
    catalog.page = FakePage()
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, url=f"https://{host}/", cart_links=True)))

    catalog._message({"type": "location", "url": f"https://{host}/", "title": "Home"})
    assert catalog.page.mode()["download"] is None and catalog.page.mode()["cartLinks"] is False

    novel = f"https://{host}/novel/4321"
    catalog._message({"type": "location", "url": novel, "title": "Solo Leveling - Site"})
    assert catalog.page.mode()["download"] == {"url": novel, "title": "Solo Leveling - Site"}
    assert catalog.page.mode()["cartLinks"] is False

    # The page moves on without the bridge having reported yet: the old button goes away.
    catalog._url_changed(QUrl(f"https://{host}/ranking"))
    assert catalog.page.mode()["download"] is None

    catalog._message({"type": "cartAdd", "url": novel, "title": "Solo Leveling"})
    assert catalog.context.cart.urls == [novel]


def test_noveldb_and_unknown_sites_never_get_the_button(catalog):
    catalog.page = FakePage()
    for site, cart_links in ((NOVELDB_NEXT_URL, False), (NOVELDB_CATALOG_URL, False), ("https://catalog.example/", True)):
        catalog.context.update_settings(lambda s, site=site: replace(s, catalog=replace(s.catalog, url=site, cart_links=True)))
        catalog._page_url = site
        catalog._message({"type": "location", "url": site + "novel/1", "title": "One"})
        assert catalog.page.mode()["download"] is None
        assert catalog.page.mode()["cartLinks"] is cart_links, site


def test_a_page_cannot_report_another_sites_address(catalog):
    """Page scripts share the channel: a location for a different host than the view shows is ignored."""
    class FakeView:
        def url(self):
            return QUrl("https://real.example/page")

    catalog.page = FakePage()
    catalog.view = FakeView()
    catalog._message({"type": "location", "url": "https://other.example/novel/1", "title": "x"})
    assert catalog.page.scripts == [] and catalog._reported is None
    catalog._message({"type": "location", "url": "https://www.real.example/novel/1", "title": "x"})
    assert catalog._reported == ("https://www.real.example/novel/1", "x")


def test_cart_dialog_opens_quickly_with_thousands_of_links(catalog, qtbot):
    import time

    from noveldb.qt.cart_dialog import CartDialog

    catalog.context.cart.add_many(web.CartItem(f"https://x.test/novel/{i}") for i in range(9_600))
    started = time.perf_counter()
    dialog = CartDialog(catalog.context)
    qtbot.addWidget(dialog)
    assert dialog.list.count() == 9_600
    assert time.perf_counter() - started < 3.0


def test_google_translate_falls_back_to_another_address_and_logs_why(monkeypatch, caplog):
    from noveldb.qt.catalog import generic as generic_page

    asked = []

    def plain(url, method, body, headers):
        asked.append(url)
        if "translate.googleapis.com" in url:
            return {"status": 429, "url": url, "content": b"Sorry...", "headers": ""}
        return {"status": 200, "url": url, "content": b"[[[\"Hello\",\"x\"]]]", "headers": ""}

    monkeypatch.setattr(generic_page, "_plain_request", plain)
    with caplog.at_level("WARNING"):
        result = generic_page._translate("https://translate.googleapis.com/translate_a/single?tl=en", "POST", "q=x", {})

    assert result["status"] == 200
    assert asked[1].startswith("https://translate.google.com/")
    assert "429" in caplog.text
    # The next request starts with the way that answered (the refused host is not waited on again).
    asked.clear()
    generic_page._translate("https://translate.googleapis.com/translate_a/t?tl=en", "POST", "q=x", {})
    assert len(asked) == 1 and asked[0].startswith("https://translate.google.com/")
    generic_page._translate_first[0] = 0


def test_a_downloaded_chapter_shows_the_pages_the_engine_already_fetched():
    from noveldb.qt.catalog.generic import _with_page_data

    html = '<div><img src="https://x.test/a?k=1&amp;t=2" /></div><div><img src="page-2" /></div>'
    result = {"contentHtml": html, "images": [{"url": "https://x.test/a?k=1&t=2", "data": "data:image/jpeg;base64,AAA"},
                                              {"url": "other", "data": "data:image/png;base64,BBB"}]}
    swapped = _with_page_data(result)
    assert 'src="data:image/jpeg;base64,AAA"' in swapped
    assert 'src="data:image/png;base64,BBB"' in swapped  # by place when the address differs
    assert _with_page_data({"contentHtml": html}) == html


def test_the_reader_downloader_gets_the_settings_engine_order(qtbot):
    from types import SimpleNamespace

    from noveldb.core.settings.model import EngineSettings
    from noveldb.qt.catalog.generic import GenericCatalog

    catalog = GenericCatalog.__new__(GenericCatalog)
    catalog.engine_settings = lambda: EngineSettings(order=("gallerydl", "haruneko", "generic"), dynamic_priority=False)
    scraper = SimpleNamespace(engine_order=["haruneko"], dynamic_priority=True, _book_url="https://x.test/")
    catalog._apply_engine_settings(scraper)
    assert scraper.engine_order[:4] == ["gallerydl", "mangapeak", "tako", "haruneko"]
    assert scraper.dynamic_priority is False
    assert scraper._book_url is None


def test_mpia_catalog_reader_uses_the_built_in_scraper_first():
    from noveldb.qt.catalog.generic import GenericCatalog

    catalog = GenericCatalog.__new__(GenericCatalog)
    catalog._downloader_books = {}
    catalog.downloader = object()
    catalog.engine_settings = lambda: None
    replies = []
    catalog._reader_answer = lambda fetch_id, result: replies.append((fetch_id, result))
    catalog._downloader_read = lambda url: {"url": url, "chapters": [{"name": "Free chapter"}]}
    url = "https://m.\u006d\u0075\u006e\u0070\u0069\u0061.com/novel/detail/1"
    catalog._read(1, lambda *_: pytest.fail("generic reader opened the mpia book"), url)
    assert replies == [(1, {"url": url, "chapters": [{"name": "Free chapter"}]})]


@pytest.mark.parametrize("extension", ["epub", "txt"])
def test_queue_open_uses_the_reader_for_the_preferred_format(tmp_path, extension):
    from types import SimpleNamespace

    from noveldb.qt.pages.queue import QueuePage

    txt = tmp_path / "Book.txt"
    txt.touch()
    files = [str(txt)]
    expected = tmp_path / f"Book.{extension}"
    if extension == "epub":
        expected.touch()
        files.append(str(expected))
    opened = []
    queue = SimpleNamespace(
        _selected_jobs=lambda: [object()],
        _existing_outputs=lambda _job: files,
        readOutput=SimpleNamespace(emit=opened.append),
    )
    QueuePage._open(queue)
    assert opened == [expected]


def test_a_library_book_opens_where_the_library_left_it_and_its_place_goes_back(tmp_path, monkeypatch):
    from noveldb.qt.catalog import generic as generic_page
    from noveldb.qt.catalog.generic import GenericCatalog
    from noveldb.qt.pages import library_reader

    places = tmp_path / "library-reading.json"
    monkeypatch.setattr(library_reader, "_places_path", lambda: places)
    book = tmp_path / "Book.epub"
    book.write_bytes(b"")
    library_reader._save_place(str(book), 4, 0.5)

    catalog = GenericCatalog.__new__(GenericCatalog)
    catalog._local_paths = {}
    catalog.library_folder = lambda: tmp_path
    called = []
    catalog._call = lambda name, *args: called.append((name, args))
    catalog.read_local(book, "Book")
    name, (sent,) = called[0]
    assert name == "readLocal" and sent["chapter"] == 4 and sent["at"] == 0.5
    catalog.read_local(book, "Book", 2)
    assert called[1][1][0]["chapter"] == 2 and called[1][1][0]["at"] == 0.0

    message = generic_page.parse_message({"type": "genericLocalPlace", "url": sent["url"], "chapter": 7, "at": 0.25})
    assert message is not None
    catalog.handle(message)
    assert library_reader._places()[str(book)]["chapter"] == 7
