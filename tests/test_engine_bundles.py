"""The LNReader, lightnovel-crawler, FanFicFare and HaruNeko engines, offline.

The two page bundles load in Chromium (skipped without Playwright's browser); the Python engines run
against stand-in transports.
"""
import json

import pytest

from noveldb.core import engine_updates


def _chromium():
    sync = pytest.importorskip("playwright.sync_api")
    try:
        manager = sync.sync_playwright().start()
        browser = manager.chromium.launch()
    except Exception as e:  # no browser installed
        pytest.skip(f"Chromium is not available: {e}")
    return manager, browser


@pytest.fixture(scope="module")
def page():
    manager, browser = _chromium()
    tab = browser.new_page()
    tab.goto("about:blank")
    yield tab
    browser.close()
    manager.stop()


def test_lnreader_bundle_loads_and_reads_a_book_through_the_transport(page):
    bundle = engine_updates.current(engine_updates.LNREADER)
    assert bundle is not None and bundle.manifest["plugins"] > 300
    page.evaluate(bundle.read())
    assert page.evaluate("window.__LNR_READY === true && !window.__LNR_LOAD_ERROR")
    sites = json.loads(page.evaluate("window.__LNR_sites()"))
    assert any(site["id"] == "novelfire" for site in sites)
    missing = json.loads(page.evaluate("window.__LNR_parseBook('https://unknown.example/book')"))
    assert missing.get("noParser")
    # A plugin's requests go to the transport; one that needs a browser tab answers {pending} first.
    seen, text = page.evaluate("""async () => {
        const seen = [];
        window.__noveldb_async = {wait: async (id) => ({status: 200, responseText: 'later ' + id})};
        window.__LNR_transport = async (request) => { seen.push(request.url); return {pending: 7}; };
        const response = await window.__LNR_fetch('https://api.example.net/x', {headers: {Referer: 'https://a.example/'}});
        return [seen, await response.text()];
    }""")
    assert seen == ["https://api.example.net/x"] and text == "later 7"


def test_haruneko_bundle_loads_and_knows_its_websites(page):
    bundle = engine_updates.current(engine_updates.HARUNEKO)
    assert bundle is not None and bundle.manifest["websites"] > 700
    page.evaluate(bundle.read())
    assert page.evaluate("window.__HN_READY === true")
    accepted = json.loads(page.evaluate("window.__HN_check('https://anikiga.com/manga/ano-ko-ni-kubittake/')"))
    assert [found["id"] for found in accepted if found.get("accepts")] == ["Anikiga"]
    assert json.loads(page.evaluate("window.__HN_parseBook('https://unknown.example/x')")).get("noParser")


def test_lncrawl_runtime_loads_crawlers_and_sends_requests_through_the_app():
    from noveldb.core.engines import lncrawl_engine

    assert lncrawl_engine.knows("https://www.novelupdates.com/series/x/")
    entry = next(e for e in lncrawl_engine.entries_for("https://allnovelfull.net/x.html"))
    asked = []

    def fetch(url, **options):
        asked.append((options.get("method"), url))
        html = "<html><head><title>T</title></head><body><h3 class='title'>Book</h3></body></html>"
        return {"status": 200, "url": url, "content": html.encode(), "text": html, "headers": ""}

    engine = lncrawl_engine.LncrawlEngine(fetch=fetch)
    crawler = engine._new_crawler(entry, "https://allnovelfull.net/x.html")
    soup = crawler.scraper.get_soup("https://allnovelfull.net/x.html")
    assert soup.select_one("h3.title").text == "Book"
    assert asked == [("GET", "https://allnovelfull.net/x.html")]


def test_fanficfare_requests_go_through_the_app_and_refusals_raise():
    pytest.importorskip("fanficfare")
    from fanficfare import exceptions

    from noveldb.core.engines import fanficfare_engine

    assert fanficfare_engine.knows("https://archiveofourown.org/works/1")
    assert not fanficfare_engine.knows("https://unknown.example/works/1")
    statuses = iter([200, 403])
    fetcher = fanficfare_engine._fetcher_class()(lambda *a, **k: None, lambda *a, **k: [], lambda url, **o: {
        "status": next(statuses), "url": url, "content": b"<html>ok</html>", "text": "<html>ok</html>", "headers": ""})
    assert fetcher.request("GET", "https://archiveofourown.org/works/1").content == b"<html>ok</html>"
    with pytest.raises(exceptions.HTTPErrorFFF):
        fetcher.request("GET", "https://archiveofourown.org/works/1")
