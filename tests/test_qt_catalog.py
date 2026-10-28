"""Desktop Catalog page: bridge messages, the link cart and where outside links go."""

import json
import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtWebEngineWidgets")

from noveldb.core.catalog.urls import NOVELDB_NEXT_URL  # noqa: E402
from noveldb.core.settings.model import DEFAULT_CATALOG_URL  # noqa: E402
from noveldb.qt.catalog import web  # noqa: E402


def test_parse_message_accepts_bridge_messages():
    assert web.parse_message('{"type": "ready"}') == {"type": "ready"}
    message = web.parse_message(json.dumps({"type": "cartAdd", "url": "https://x.test/n/1", "title": "T", "source": "s"}))
    assert message["url"] == "https://x.test/n/1" and message["title"] == "T"


@pytest.mark.parametrize("text", [
    "not json", "[]", '{"type": "eval"}', '{"type": "open"}', '{"type": "open", "url": "javascript:alert(1)"}',
    '{"type": "cartAdd", "url": 5}', json.dumps({"type": "open", "url": "https://x.test/" + "a" * 5000}),
])
def test_parse_message_rejects_everything_else(text):
    assert web.parse_message(text) is None


def test_parse_message_blanks_odd_titles():
    message = web.parse_message('{"type": "open", "url": "https://x.test/1", "title": {"x": 1}}')
    assert message["title"] == ""


def test_bridge_script_bundles_channel_shim_and_bridge(qapp):
    source = web.bridge_script().sourceCode()
    assert "QWebChannel" in source and "window.NovelDBQt" in source and "window.NovelDBApp" in source
    assert source.index("window.NovelDBQt =") < source.index("window.NovelDBApp =")


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


def test_cart_messages_update_the_link_cart(catalog):
    catalog._message({"type": "cartAdd", "url": "https://x.test/novel/7", "title": "Seven"})
    assert catalog.context.cart.urls == ["https://x.test/novel/7"]
    assert catalog.cart_button.text().endswith("(1)")
    catalog._message({"type": "cartRemove", "url": "https://x.test/novel/7"})
    assert catalog.context.cart.urls == []


def test_open_on_noveldb_goes_to_the_browser(catalog):
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, url=DEFAULT_CATALOG_URL)))
    catalog._message({"type": "open", "url": "https://x.test/novel/9", "title": "Nine"})
    assert catalog.opened == ["https://x.test/novel/9"]
    assert catalog.context.cart.urls == []


def test_outside_links_on_other_sites_go_to_the_cart_unless_turned_off(catalog):
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, url=NOVELDB_NEXT_URL, cart_links=True)))
    catalog.leave("https://x.test/novel/3", "Three")
    assert catalog.context.cart.urls == ["https://x.test/novel/3"]
    assert catalog.opened == []
    catalog.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, cart_links=False)))
    catalog.leave("https://x.test/novel/4")
    assert catalog.opened == ["https://x.test/novel/4"]


def test_cart_dialog_opens_quickly_with_thousands_of_links(catalog, qtbot):
    import time

    from noveldb.qt.cart_dialog import CartDialog

    catalog.context.cart.add_many(web.CartItem(f"https://x.test/novel/{i}") for i in range(9_600))
    started = time.perf_counter()
    dialog = CartDialog(catalog.context)
    qtbot.addWidget(dialog)
    assert dialog.list.count() == 9_600
    assert time.perf_counter() - started < 3.0
