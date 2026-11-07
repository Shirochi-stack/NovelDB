from types import SimpleNamespace

import pytest

from noveldb.core.engines.generic import GenericEngine
from noveldb.core.engines.scraper_mixin import SiteEnginesMixin


@pytest.mark.parametrize("host, container", [
    ("comics.example", "em-reader-pages"),
    ("another.example", "chapter-images"),
])
def test_reader_override_returns_ordered_original_urls_without_fetching_the_images(host, container):
    address = f"https://{host}/book/chapter-1"
    html = f"""<img src="/advert.png">
    <div id="{container}">
        <img data-src="https://cdn.example/pages/2.webp?token=a&amp;v=2" src="data:,">
        <img src="/pages/1.png">
    </div>"""
    asked = []

    def fetch(url, **_options):
        asked.append(url)
        return {"status": 200, "url": url, "text": html, "content": html.encode()}

    result = GenericEngine(fetch=fetch).parse_chapter({"url": address, "name": "First"}, {}, listed_only=True)
    assert asked == [address]
    assert result["imageFallback"] and result["comic"] and not result["images"]
    assert result["contentHtml"] == (
        '<p><img src="https://cdn.example/pages/2.webp?token=a&amp;v=2" alt=""/></p>'
        f'<p><img src="https://{host}/pages/1.png" alt=""/></p>'
    )


@pytest.mark.parametrize("unsafe", ['<canvas></canvas>', '<img data-encrypted="true" src="/extra.jpg">'])
def test_reader_leaves_transformed_pages_to_the_site_engine(unsafe):
    address = "https://comics.example/chapter-1"
    html = f'<div class="reader-pages"><img src="/a.png"><img src="/b.png">{unsafe}</div>'
    engine = GenericEngine(fetch=lambda url, **_: {"status": 200, "url": url, "text": html, "content": html.encode()})
    assert engine.parse_chapter({"url": address}, {}, listed_only=True) is None


def test_reader_transport_retries_timeout_and_streams_the_original_bytes():
    reader = SiteEnginesMixin()
    reader.log = lambda _: None
    calls = []
    image = "iVBORw0KGgoAAA=="
    replies = iter([{"error": "connection timed out"}, {"status": 504}, {"status": 200, "responseBase64": image}])

    def fetch(_source, details, *, timeout_override):
        calls.append((details["url"], timeout_override))
        return next(replies)

    arrived = []
    reader._gm_xmlhttp_request = fetch
    reader.on_image = lambda url, data: arrived.append((url, data))
    url = "https://cdn.example/image.png"
    reply = reader._run_job("reader-request", {"url": url})
    assert calls == [(url, (5, 15))] * 3
    assert arrived == [(url, f"data:image/png;base64,{image}")]
    assert reply["responseBase64"] == image


def test_reader_cdn_timeout_does_not_open_the_cdn_homepage():
    reader = SiteEnginesMixin()
    reader.log = lambda _: None
    reader._gm_xmlhttp_request = lambda *_args, **_options: {"error": "ERR_CONNECTION_TIMED_OUT"}
    reader._browser_request = lambda _: pytest.fail("a timed-out image must not navigate to the CDN homepage")
    assert reader._run_job("reader-request", {"url": "https://cdn.example/page.webp"}) == {
        "error": "ERR_CONNECTION_TIMED_OUT",
    }


def test_reader_uses_original_bytes_even_for_large_images_without_a_mime_type(monkeypatch):
    import base64

    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog import generic

    raw = b"RIFF1234WEBP" + bytes(8 << 20)
    monkeypatch.setattr(generic, "default_fetch", lambda *_args, **_options: {"status": 200, "content": raw})
    emitted = []
    catalog = SimpleNamespace(logins=SimpleNamespace(header=lambda _: ""),
                              _fetched=SimpleNamespace(emit=lambda _id, value: emitted.append(value)))
    generic.GenericCatalog._image(catalog, 1, "https://cdn.example/page.webp", "", reader=True)
    generic.GenericCatalog._image(catalog, 2, "https://cdn.example/cover.webp", "")
    header, payload = emitted[0]["dataUrl"].split(",", 1)
    assert header == "data:image/webp;base64"
    assert base64.b64decode(payload) == raw
    assert emitted[1] is None


def test_reader_keeps_the_chapter_identity_when_switching_engines(monkeypatch):
    from external_scraper import ExternalScraper
    from noveldb.core.external.reader_scraper import reader_scraper
    from noveldb.core.settings.model import ENGINE_GENERIC, ENGINE_HN

    address = "https://comics.example/chapter-2"
    item = {"url": address, "name": "Second"}
    reader = reader_scraper(logger=lambda _: None)
    reader._book_data = {"_haruneko": True, "chapters": [item]}
    reader.engine_order = [ENGINE_HN, ENGINE_GENERIC]
    events = []

    def primary(self, index, chapter, **_):
        assert index == 1 and chapter is item
        events.append("primary")
        return None

    def generic(chapter, book, *, listed_only):
        assert chapter is item and book is reader._book_data
        events.append("override" if listed_only else "fallback")
        return None if listed_only else {"comic": True}

    monkeypatch.setattr(ExternalScraper, "parse_chapter", primary)
    monkeypatch.setattr(reader, "_python_engine", lambda _: SimpleNamespace(parse_chapter=generic))
    assert reader.parse_chapter(1, item) == {"comic": True}
    assert events == ["override", "primary", "fallback"]
    events.clear()
    assert reader.parse_chapter(1, item, retry_images=True) is None
    assert events == ["primary"]
