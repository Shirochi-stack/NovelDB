"""ScribbleHub's protected documents and complete, oldest-first chapter list."""

from concurrent.futures import Future
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOK = "https://www.scribblehub.com/series/123/example/"
CHAPTER = "https://www.scribblehub.com/read/123-example/chapter/101/"
BUNDLES = ["lnreader/lnreader-lib.js", "android/app/src/main/assets/catalog/lnreader-lib.js",
           "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog/lnreader-lib.js",
           "android/sources/src/main/resources/com/shirochi/npia/sources/reader/lnreader/lnreader-lib.js",
           "ios/NpiaKit/Sources/NpiaSources/Reader/Resources/lnreader/lnreader-lib.js"]
TEXT = "A synthetic chapter with its original paragraph spacing. " * 20
HTML = """<html><head><title>Example</title></head><body>
<div class="fic_title">Example</div><span class="auth_name_fic">Author</span>
<input id="mypostid" value="123">
<div class="wi_fic_desc"><p>Example description.</p></div>
<div class="toc_w"><a class="toc_a" href="/read/123-example/chapter/103/">Chapter 3</a></div>
</body></html>"""
TOC = "".join(f'<li class="toc_w" order="{i}"><a class="toc_a" href="https://www.scribblehub.com/read/123-example/chapter/{100 + i}/">'
              f'Chapter {i}</a><span class="fic_date_pub" title="Nov 9, 2026">Nov 9</span></li>'
              for i in (3, 2, 1))


@pytest.fixture(scope="module")
def browser():
    api = pytest.importorskip("playwright.sync_api")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.mark.parametrize("bundle", BUNDLES)
@pytest.mark.parametrize("on_source", [False, True])
def test_catalogue_and_downloader_read_full_toc_and_chapter(bundle, on_source):
    from playwright.async_api import async_playwright

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                await read_book(browser, bundle, on_source)
            finally:
                await browser.close()

    asyncio.run(scenario())


async def read_book(browser, bundle, on_source, blocked_toc=False):
    context = await browser.new_context()
    requests, windows = [], []

    async def route(request):
        path = urlsplit(request.request.url).path
        if path == "/wp-admin/admin-ajax.php":
            requests.append(request.request)
            assert request.request.method == "POST"
            assert request.request.headers["content-type"].startswith("application/x-www-form-urlencoded")
            assert request.request.headers["x-requested-with"] == "XMLHttpRequest"
            assert parse_qs(request.request.post_data) == {
                "action": ["wi_getreleases_pagination"], "pagenum": ["-1"], "mypostid": ["123"]}
            if blocked_toc:
                await request.fulfill(status=403, body="Attention Required!", content_type="text/html")
                return
            await request.fulfill(body=TOC, content_type="text/html")
        elif "/read/" in path:
            await request.fulfill(body=f'<html><body><div class="chp_raw"><p>{TEXT}</p><p>Last paragraph.</p></div></body></html>',
                            content_type="text/html")
        else:
            await request.fulfill(body=HTML, content_type="text/html")

    await context.route("**/*", route)
    tab = await context.new_page()
    await tab.goto(BOOK if on_source else "https://catalog.example/")

    async def native(_source, request):
        assert request["op"] == "window"
        windows.append(request["url"])
        page = await context.new_page()
        try:
            await page.goto(request["url"])
            return {"value": await page.evaluate(request["script"])}
        finally:
            await page.close()

    try:
        await tab.expose_binding("nativeWindow", native)
        await tab.evaluate("() => {window.__LNR_transport = request => window.nativeWindow(request);}")
        await tab.evaluate((ROOT / bundle).read_text(encoding="utf-8"))
        book = json.loads(await tab.evaluate("url => window.__LNR_parseBook(url)", BOOK))
        if blocked_toc:
            assert book.get("error") and not book.get("chapters")
            return
        assert book["bookname"] == "Example"
        assert [chapter["name"] for chapter in book["chapters"]] == ["Chapter 1", "Chapter 2", "Chapter 3"]
        assert book["chapters"][0]["url"] == CHAPTER
        chapter = json.loads(await tab.evaluate("chapter => window.__LNR_parseChapter(JSON.stringify(chapter))", book["chapters"][0]))
        assert TEXT in chapter["contentHtml"] and "Last paragraph." in chapter["contentHtml"]
        assert len(requests) == 1
        assert len(windows) == 3
    finally:
        await context.close()


def test_failed_full_toc_cannot_be_replaced_by_the_latest_chapter_preview():
    from playwright.async_api import async_playwright

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                await read_book(browser, BUNDLES[0], False, blocked_toc=True)
            finally:
                await browser.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reply", [{"error": "browser unavailable"},
                                 {"value": {"status": 403, "responseText": "Attention Required!"}}])
def test_blocked_browser_does_not_become_an_empty_successful_book(browser, reply):
    tab = browser.new_page()
    try:
        tab.goto("about:blank")
        tab.evaluate("reply => {window.__LNR_transport = async () => reply;}", reply)
        tab.evaluate((ROOT / BUNDLES[0]).read_text(encoding="utf-8"))
        book = json.loads(tab.evaluate("url => window.__LNR_parseBook(url)", BOOK))
        assert book.get("error") and not book.get("chapters")
    finally:
        tab.close()


def test_desktop_catalogue_routes_window_requests_through_saved_browser():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog.generic import GenericCatalog

    seen, answers = [], []

    class Scraper:
        _rbooks_chrome = False

        def _start_rbooks_browser(self, url, site):
            seen.append((url, site))
            self._rbooks_chrome = True
            return True

        def _window_script(self, request):
            return {"value": {"status": 200, "responseText": request["script"]}}

    scraper = Scraper()
    catalog = type("Catalog", (), {})()

    def submit(operation):
        future = Future()
        future.set_result(operation(scraper))
        return future

    catalog.downloader = submit
    catalog._reader_answer = lambda *args: answers.append(args)
    for request_id in (1, 2):
        GenericCatalog._transport(catalog, request_id, {"op": "window", "url": BOOK, "script": "example"})
    assert seen == [(BOOK, "ScribbleHub")]
    assert answers == [(i, {"value": {"status": 200, "responseText": "example"}}) for i in (1, 2)]


def test_desktop_downloader_queues_window_transport_outside_the_binding():
    from noveldb.core.engines.scraper_mixin import SiteEnginesMixin

    scraper = SiteEnginesMixin()
    result = scraper._engine_transport({"page": "engine-page"}, {"op": "window", "url": BOOK, "script": "example"})
    assert result == {"pending": 1}
    assert scraper._engine_jobs == [(1, "window", {"op": "window", "url": BOOK, "script": "example"}, "engine-page")]


@pytest.mark.parametrize("url", [BOOK, "https://www.scribblehub.com/series/123/"])
def test_desktop_reuses_the_verified_book_document_for_its_toc(url):
    from noveldb.core.engines.scraper_mixin import SiteEnginesMixin

    opened = SimpleNamespace(url=BOOK, locator=lambda _selector: SimpleNamespace(count=lambda: 1),
                             title=lambda: "Example", wait_for_timeout=lambda _delay: None)
    session = SimpleNamespace(send=lambda *_args: {"result": {"value": {"status": 200}}})
    scraper = SiteEnginesMixin()
    scraper._context = SimpleNamespace(pages=[opened], new_cdp_session=lambda page: session)
    assert scraper._window_script({"url": url, "script": "example"}) == {"value": {"status": 200}}


@pytest.mark.parametrize("url", [BOOK, "https://another.example/book/"])
def test_switching_books_keeps_the_saved_browser_context(url):
    from external_scraper import ExternalScraper

    closed = []
    runtime = SimpleNamespace(close=lambda: closed.append(True))
    document = SimpleNamespace(goto=lambda *_args, **_kwargs: object())
    saved = SimpleNamespace(new_page=lambda: document)
    scraper = ExternalScraper()
    scraper._rbooks_chrome = True
    scraper._context = saved
    scraper._page = SimpleNamespace(context=runtime)
    scraper._wait_past_bot_check = lambda *_args: True
    assert scraper._open_book_page(url)
    assert closed == ([] if url == BOOK else [True])
    assert scraper._context is saved
    if url != BOOK:
        assert scraper._page is document


def test_legacy_multipart_toc_is_sent_using_the_sites_urlencoded_form(browser):
    tab = browser.new_page()
    try:
        tab.goto("about:blank")
        tab.evaluate("""() => {
            window.__LNR_transport = async request => {
                window.lastRequest = request;
                return {value: {status: 200, responseText: 'OK'}};
            };
        }""")
        tab.evaluate((ROOT / BUNDLES[0]).read_text(encoding="utf-8"))
        result = tab.evaluate("""async () => {
            const body = new FormData();
            body.append('action', 'wi_getreleases_pagination');
            body.append('pagenum', '-1');
            body.append('mypostid', '123');
            const response = await window.__LNR_fetch('https://www.scribblehub.com/wp-admin/admin-ajax.php', {method: 'POST', body});
            return {text: await response.text(), request: window.lastRequest};
        }""")
        assert result["text"] == "OK"
        assert result["request"]["url"] == "https://www.scribblehub.com/series/123/"
        assert '"data":"action=wi_getreleases_pagination&pagenum=-1&mypostid=123"' in result["request"]["script"]
        assert "dataBase64" not in result["request"]["script"]
    finally:
        tab.close()
