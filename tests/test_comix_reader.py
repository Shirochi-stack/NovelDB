import json
from pathlib import Path

import pytest

from noveldb.core.engines import comix

TITLE = "https://comix.to/title/example-example-title"
MIRROR = TITLE.replace(".to/", ".ws/")
CHAPTER = TITLE + "/9-chapter-1"


def test_comix_routes_and_mirror_preserve_the_title_and_query():
    from external_scraper import ExternalScraper
    from noveldb.core.catalog.sites import downloadable_page
    from noveldb.core.sources import detect_source

    assert comix.book_pages(CHAPTER + "?group_id=7#page") == (
        TITLE + "?group_id=7", MIRROR + "?group_id=7",
    )
    assert comix.book_pages(MIRROR) == (MIRROR, TITLE)
    assert detect_source(CHAPTER) == "comix"
    assert ExternalScraper._native_book_parser(ExternalScraper(), CHAPTER) is not None
    assert downloadable_page(CHAPTER, ["native"])
    assert not downloadable_page(CHAPTER, ["generic"])
    assert not comix.knows("https://comix.to.example/title/example")
    assert not comix.knows("https://comix.to/browse")


def test_desktop_engine_tries_mirror_when_the_connection_closes(monkeypatch):
    from external_scraper import ExternalScraper

    scraper = ExternalScraper(logger=None)
    scraper.engine_order = ["native", "generic"]
    visits = []
    monkeypatch.setattr(scraper, "_open_book_page", lambda url: visits.append(url) or url == MIRROR)
    monkeypatch.setattr(scraper, "_script_parse_book", lambda engine, url: {"chapters": [url], "engine": engine})
    assert scraper._parse_book_engines(CHAPTER) == {"chapters": [CHAPTER], "engine": "comix"}
    assert visits == [TITLE, MIRROR]


def test_comix_lists_chapters_and_reads_offscreen_pages_from_the_site_reader():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as api:
        options = {} if Path(api.chromium.executable_path).is_file() else {"channel": "chrome"}
        try:
            browser = api.chromium.launch(**options)
        except playwright.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        with browser:
            context = browser.new_context()
            page = context.new_page()
            meta = json.dumps({"queries": {'["manga","detail","example"]': {"title": "Example title"}}})
            book_html = f"""<script id="initial-data" type="application/json">{meta}</script>
                <div id="rows"><a class="mchap-row__primary" href="{TITLE}/13-chapter-2">Ch.2</a></div>
                <div class="npager">
                    <button class="npager__num is-active">1</button>
                    <button class="npager__num" onclick="
                        this.previousElementSibling.classList.remove('is-active');
                        this.classList.add('is-active');
                        document.querySelector('#rows').innerHTML =
                          '<a class=mchap-row__primary href={CHAPTER}>Ch.1</a>'; ">2</button>
                </div>"""
            chapter_html = """<div id="app-root"></div>
                <div class="rpage-progress__seg"></div><div class="rpage-progress__seg"></div>
                <div class="rpage-page" data-page="1"><img src="https://cdn.test/one"></div>
                <div class="rpage-page" data-page="2"></div>
                <script>
                document.getElementById('app-root').__reactContainer$test = {
                    memoizedProps: {client: {getQueryCache: () => ({
                        find({queryKey}) {
                            if (JSON.stringify(queryKey) !== '["chapters","detail",9]') return;
                            return {state: {data: {varyingKey: [
                                {differentKey: 'https://cdn.test/one'},
                                {differentKey: 'https://cdn.test/two?a=1&b=2'},
                            ]}}};
                        },
                    })}}
                };
                </script>"""
            context.route("**/*", lambda route: route.fulfill(
                content_type="text/html", body=book_html if route.request.url == TITLE else chapter_html,
            ))
            page.goto(TITLE)
            page.evaluate(comix.script())
            book = json.loads(page.evaluate("(url) => window.__COMIX_parseBook(url)", TITLE))
            assert book["chapterCount"] == 2
            assert [chapter["url"] for chapter in book["chapters"]] == [CHAPTER, TITLE + "/13-chapter-2"]
            chapter_page = context.new_page()
            chapter_page.goto(CHAPTER.replace(".to/", ".ws/"))
            page.evaluate("""item => {
                window.tries = [];
                window.__COMIX_transport = request => {
                    window.tries.push(request);
                    if (window.tries.length === 1) return {error: "net::ERR_CONNECTION_CLOSED"};
                    return new Promise(resolve => { window.answerMirror = resolve; });
                };
                window.result = window.__COMIX_parseChapter(JSON.stringify(item));
            }""", book["chapters"][0])
            page.wait_for_function("window.tries.length === 2")
            tries = page.evaluate("window.tries")
            assert [attempt["url"] for attempt in tries] == [CHAPTER, CHAPTER.replace(".to/", ".ws/")]
            value = chapter_page.evaluate("script => (0, eval)(script)", tries[1]["script"])
            result = json.loads(page.evaluate("value => { window.answerMirror({value}); return window.result; }", value))
            assert result["imageCount"] == 2
            assert 'src="https://cdn.test/one"' in result["contentHtml"]
            assert 'src="https://cdn.test/two?a=1&amp;b=2"' in result["contentHtml"]
