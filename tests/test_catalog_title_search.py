import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from noveldb.core.catalog import generic_search
from noveldb.core.catalog.title_search import direct_titles


TITLE = "Move Aside, I'll Be the Magical Girl!"
SLUG = "move-aside-ill-be-the-magical-girl"
ADDRESS = "https://mangak.io/" + SLUG
QUERIES = ("Move aside, I'll become the magical girl", "step aside i'll be the magical girl")
MANGAK = {"site": "mangak.io", "host": "mangak.io", "comic": True}
DEX = {"site": "mangadex.org", "host": "mangadex.org", "comic": False}
FIXTURE = {"slug": SLUG, "name": TITLE, "altNames": [{"name": "Step Aside, I'm the One Becoming the Magical Girl"}],
           "cover": "https://covers.example/magical.webp", "stats": {"chaptersCount": 65}}


def mangak_html(rows):
    data = {"props": {"pageProps": {"ssrItems": rows}}}
    return '<script type="application/json" id="__NEXT_DATA__">' + json.dumps(data) + '</script>'


def response(url, body, status=200):
    return {"url": url, "status": status, "content": body.encode(), "text": body}


@pytest.mark.parametrize("query", QUERIES)
def test_selected_catalog_retries_a_misremembered_title_with_fewer_words(query, monkeypatch):
    requests = []

    def fetch(url, **kwargs):
        parts = urlsplit(url)
        requests.append(url)
        if parts.hostname != "mangak.io":
            return response(url, "", 403)
        assert parts.path == "/search" and kwargs["quick"]
        text = parse_qs(parts.query)["q"][0]
        if text == query:
            return response(url, mangak_html([]))
        assert text in {"move aside magical girl", "step aside magical girl"}
        return response(url, mangak_html([{"slug": "wrong-book", "name": "Magical Girl Raising Project"}, FIXTURE]))

    monkeypatch.setattr(generic_search.GenericSearch, "_lncrawl_entries", lambda _: [])
    engine = generic_search.GenericSearch(fetch=fetch, sites=[MANGAK, DEX], workers=8)
    engine.only = {MANGAK["site"]}
    emitted = []
    engine.run(query, "name", emitted.extend, threading.Event(), deadline=5)
    assert [(card["url"], card["title"], card["chapters"]) for card in emitted] == [(ADDRESS, TITLE, 65)]
    assert len(requests) == 2 and all(url.startswith("https://mangak.io/search?") for url in requests)


def test_mangadex_matches_an_alternate_title_and_returns_the_series_address():
    ident = "944f9245-f92e-43bf-bc9f-9425b1c1d4c2"
    asked = []

    def fetch(url, **kwargs):
        params = parse_qs(urlsplit(url).query)
        assert url.startswith("https://api.mangadex.org/manga?")
        asked.extend(params["title"])
        assert params["includes[]"] == ["cover_art", "author"] and kwargs["timeout"] == 5
        return response(url, json.dumps({"result": "ok", "data": [{
            "id": ident, "attributes": {"title": {"en": "A different localized title"}, "altTitles": [{"en": TITLE}]},
            "relationships": [{"type": "cover_art", "attributes": {"fileName": "cover.jpg"}}],
        }]}))

    cards = direct_titles("mangadex.org", QUERIES[1], fetch, lambda: True)
    assert asked == [QUERIES[1], "step aside magical girl"]
    assert [card["url"] for card in cards] == [f"https://mangadex.org/title/{ident}"]
    assert cards[0]["alternateTitles"] == [TITLE]


def test_web_hits_stream_without_waiting_for_the_other_provider(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(generic_search, "duckduckgo_results", lambda *_: release.wait(5) and [])
    monkeypatch.setattr(generic_search, "bing_results", lambda *_: [{"title": TITLE, "url": ADDRESS}])
    stream = generic_search.web_search(QUERIES[0], lambda _: None)
    with ThreadPoolExecutor(1) as pool:
        hit = pool.submit(next, stream)
        try:
            assert hit.result(2)["url"] == ADDRESS
            assert not release.is_set()
        finally:
            release.set()
    assert list(stream) == []


def test_comix_catalog_lists_the_source_and_uses_the_mirror_when_browse_fails():
    engine = generic_search.GenericSearch()
    assert engine.site_for("https://comix.ws/title/n0225-example")["site"] == "comix.to"
    asked = []

    def render(url, **kwargs):
        parts = urlsplit(url)
        asked.append(parts.hostname)
        assert parts.path == "/browse" and parse_qs(parts.query)["order[relevance]"] == ["desc"]
        return ('<div class="lrow"><a class="lrow__title-link" href="/title/n0225-example">'
                + TITLE + '</a></div>') if parts.hostname == "comix.ws" else ""

    cards = direct_titles("comix.to", TITLE, None, lambda: True, render)
    assert asked == ["comix.to", "comix.ws"]
    assert [(card["title"], card["url"]) for card in cards] == [(TITLE, "https://comix.ws/title/n0225-example")]


def test_android_catalog_finds_comix_and_uses_its_engine_for_chapter_one():
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).resolve().parents[1] / "android/app/src/main/assets/catalog"
    with playwright.sync_playwright() as api:
        options = {} if Path(api.chromium.executable_path).is_file() else {"channel": "chrome"}
        try:
            browser = api.chromium.launch(**options)
        except playwright.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        with browser:
            page = browser.new_page(viewport={"width": 390, "height": 800})

            def serve(route):
                name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
                if name in {"index.html", "generic.js", "catalog-app.js", "generic-lib.js", "generic_sites.json"}:
                    route.fulfill(path=assets / name)
                elif name.endswith(".json"):
                    route.fulfill(json={})
                else:
                    route.fulfill(body="")

            page.route("**/*", serve)
            page.add_init_script("""
                window.requests = [];
                window.NovelDBTransport = async function (request) {
                    requests.push(request);
                    const url = new URL(request.url || 'https://invalid.test/');
                    if (request.op === 'window' && url.hostname === 'comix.to' && url.pathname === '/browse') {
                        return {value: [{url: 'https://comix.to/title/n0225-example', title: "Move Aside, I'll Be the Magical Girl!",
                                        alternateTitles: [], cover: ''}]};
                    }
                    if (request.op === 'book' && request.engines && url.hostname === 'comix.to') {
                        return {bookname: "Move Aside, I'll Be the Magical Girl!", engine: 'Comix reader',
                                chapters: [{name: 'Ch. 1', url: url.href + '/123-chapter-1'}]};
                    }
                    if (request.op === 'chapter' && request.engines && url.hostname === 'comix.to') {
                        return {chapterName: 'Ch. 1', contentHtml: '<p><img src="https://cdn.test/p1.webp"/></p>', comic: true};
                    }
                    if (request.op === 'book') return {skip: true};
                    return {status: 403};
                };
            """)
            page.goto("https://catalog.test/index.html")
            page.locator("#query").fill("Step aside ill be a magical girl")
            page.locator("#query").press("Enter")
            playwright.expect(page.locator("#grid .card").first).to_contain_text(TITLE, timeout=10000)
            page.locator("#sitesButton").click()
            page.locator("#siteFind").fill("Comix")
            playwright.expect(page.locator('[data-filter-site="comix.to"]')).to_be_visible()
            page.locator("#sitesButton").click()
            page.evaluate("() => { window.__GEN_parseChapter = () => { throw new Error('the generic parser was called first'); }; }")
            page.locator("#grid .card").first.click()
            page.locator("[data-act=read]").click()
            page.wait_for_function("requests.some(r => r.op === 'chapter' && r.engines && r.index === 0)")


@pytest.mark.parametrize("platform,query", zip(("android", "ios"), QUERIES))
def test_mobile_catalog_shows_a_direct_match_while_web_search_waits(platform, query):
    playwright = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    assets = root / {"android": "android/app/src/main/assets/catalog",
                     "ios": "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog"}[platform]
    with playwright.sync_playwright() as api:
        if not Path(api.chromium.executable_path).is_file():
            pytest.skip("Playwright Chromium is not installed")
        with api.chromium.launch() as browser:
            page = browser.new_page(viewport={"width": 390, "height": 800})

            def serve(route):
                name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
                if name == "generic_sites.json":
                    route.fulfill(json={"sites": [MANGAK, DEX]})
                elif name in ("index.html", "generic.js", "catalog-app.js", "generic-lib.js"):
                    route.fulfill(path=assets / name)
                elif name.endswith(".json"):
                    route.fulfill(json={})
                else:
                    route.fulfill(body="")

            page.route("**/*", serve)
            first = json.dumps(mangak_html([]))
            found = json.dumps(mangak_html([FIXTURE]))
            page.add_init_script(rf"""
                window.requests = [];
                window.NovelDBTransport = function (request) {{
                    const url = new URL(request.url || 'https://invalid.test/');
                    requests.push(url.href);
                    if (url.hostname === 'mangak.io' && url.pathname === '/search') {{
                        const q = url.searchParams.get('q');
                        return Promise.resolve({{status: 200, finalUrl: url.href,
                            responseText: /^(move|step) aside magical girl$/.test(q) ? {found} : {first}}});
                    }}
                    if (/duckduckgo|bing\.com/.test(url.hostname)) return new Promise(() => {{}});
                    return Promise.resolve({{status: 403, finalUrl: url.href}});
                }};
            """)
            page.goto("https://catalog.test/index.html")
            page.locator("#query").fill(query)
            page.locator("#query").press("Enter")
            playwright.expect(page.locator("#grid .card")).to_contain_text(TITLE, timeout=5000)
            assert page.evaluate("requests.some(url => url.startsWith('https://html.duckduckgo.com/'))")
            assert page.evaluate("requests.filter(url => url.startsWith('https://mangak.io/search?')).length") == 2
