import json
from pathlib import Path

import pytest

import hard_sources_support as hs

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "noveldb/qt/catalog/generic"
MOBILE = (
    ROOT / "android/app/src/main/assets/catalog",
    ROOT / "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog",
)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as api:
        if not Path(api.chromium.executable_path).is_file():
            pytest.skip("Playwright's Chromium is not installed")
        with api.chromium.launch() as browser:
            yield browser


@pytest.mark.parametrize("assets", MOBILE, ids=["android", "ios"])
def test_mpia_reader_preserves_the_source_articles_line_breaks(browser, assets):
    origin = "https://m.\u006d\u0075\u006e\u0070\u0069\u0061.com"
    chapter_url = origin + "/novel/viewer/1/2"
    source_text = "\n  First line.\n\nSecond line.\nThird line.\n"
    with browser.new_context(viewport={"width": 390, "height": 800}) as context:
        source = context.new_page()
        source.add_init_script(hs.script_source())
        source.route(chapter_url, lambda route: route.fulfill(
            content_type="text/html",
            body=('<style>.content > article {white-space: pre-wrap}</style>'
                  f'<div class="content"><article>{source_text}</article></div>'),
        ))
        source.goto(chapter_url)
        article = source.locator("article")
        assert article.evaluate("(node) => getComputedStyle(node).whiteSpace") == "pre-wrap"
        result = source.evaluate("() => window.__npiaHard.mpia.extract({chapterIndex: 1})")
        source.close()

        page = context.new_page()

        def serve(route):
            name = route.request.url.rsplit("/", 1)[-1]
            if name in ("index.html", "catalog-start.js", "generic.js", "catalog-app.js", "generic-lib.js"):
                route.fulfill(path=assets / name)
            elif name.endswith(".json"):
                route.fulfill(json={})
            else:
                route.fulfill(body="")

        page.route("**/*", serve)
        page.add_init_script(f"window.fixtureChapter = {json.dumps(result)};" + """
            window.NovelDBTransport = (request) => Promise.resolve(
                request.op === 'book' ? {chapters: [{name: 'Chapter 1'}]} :
                request.op === 'chapter' ? window.fixtureChapter : {skip: true});
        """)
        page.goto("https://catalog.test/index.html")
        page.evaluate("""(url) => NovelDBGeneric.add([{title: 'Example book', url,
            site: 'mpia', source: 'site', verified: true}])""", origin + "/novel/detail/1")
        page.locator("#grid .card").first.click()
        page.locator("[data-act=read]").click()
        body = page.locator("#flow > :not(h2)").first
        playwright.expect(body).to_contain_text("Third line.")
        assert body.evaluate("(node) => getComputedStyle(node).whiteSpace") == "pre-wrap"
        assert body.text_content() == source_text
        assert body.locator("p, br").count() == 0


@pytest.mark.parametrize("assets", [DESKTOP, *MOBILE], ids=["desktop", "android", "ios"])
def test_read_starts_while_the_next_toc_page_is_still_pending(browser, assets):
    desktop = assets == DESKTOP
    with browser.new_context(viewport={"width": 1200 if desktop else 390, "height": 800}) as context:
        page = context.new_page()

        def serve(route):
            name = route.request.url.rsplit("/", 1)[-1]
            if name in ("index.html", "catalog-start.js", "generic.js", "catalog-app.js", "generic-lib.js"):
                route.fulfill(path=assets / name)
            elif name.endswith(".json"):
                route.fulfill(json={})
            else:
                route.fulfill(body="")

        page.route("**/*", serve)
        page.add_init_script("""
            window.calls = [];
            window.NovelDBTransport = function (request) {
                calls.push(request);
                if (request.op === 'book' && request.page === 0)
                    return Promise.resolve({chapters: [{name: 'Chapter 1'}], more: true});
                if (request.op === 'book' && request.page === 1)
                    return new Promise(resolve => { window.nextPage = resolve; });
                if (request.op === 'book' && request.page === 2)
                    return new Promise(resolve => { window.lastPage = resolve; });
                if (request.op === 'chapter')
                    return Promise.resolve({contentHtml: '<p>Chapter text ' + (request.index + 1) + '</p>'});
                return Promise.resolve({skip: true});
            };
        """)
        if desktop:
            page.add_init_script("""
                window.NovelDBQt = {postMessage(text) { calls.push(JSON.parse(text)); }};
            """)
        page.goto("https://catalog.test/index.html")
        if desktop:
            page.add_script_tag(path=assets / "generic.js")
        page.evaluate("""NovelDBGeneric.add([{title: 'A long book', url: 'https://novels.test/book/1',
                          site: 'Example', source: 'site', verified: true}])""")
        page.locator("#grid .card").first.click()
        page.locator("[data-act=read]").click()
        if desktop:
            page.evaluate("""
                window.bookRequest = calls.find(m => m.type === 'genericBook').id;
                NovelDBGeneric.readerChapters(bookRequest, [{name: 'Chapter 1'}]);
                NovelDBGeneric.fetched(calls.find(m => m.type === 'genericChapter').id,
                                       {html: '<p>Chapter text 1</p>'});
            """)
        else:
            page.wait_for_function("!!window.nextPage")
        playwright.expect(page.locator("#flow")).to_contain_text("Chapter text 1")
        page.locator("#rToc").click()
        playwright.expect(page.locator("#tocList")).to_contain_text("Loading more chapters")
        playwright.expect(page.locator("#tocList [data-chapter]")).to_have_count(1)

        if desktop:
            page.evaluate("NovelDBGeneric.readerChapters(bookRequest, [{name: 'Chapter 2'}])")
        else:
            page.evaluate("nextPage({chapters: [{name: 'Chapter 2'}], more: true})")
            page.wait_for_function("!!window.lastPage")
        playwright.expect(page.locator("#flow")).to_contain_text("Chapter text 1")
        page.locator('#tocList [data-chapter="1"]').click()
        if desktop:
            page.evaluate("""
                NovelDBGeneric.fetched(calls.find(m => m.type === 'genericChapter' && m.index === 1).id,
                                       {html: '<p>Chapter text 2</p>'});
                NovelDBGeneric.fetched(bookRequest, {chapters: [{name: 'Chapter 1'}, {name: 'Chapter 2'}]});
            """)
        else:
            page.evaluate("lastPage({chapters: [], more: false})")
        playwright.expect(page.locator("#flow")).to_contain_text("Chapter text 2")
        playwright.expect(page.locator("#rChapterNo")).to_have_text("Chapter 2 of 2")


def test_desktop_reader_pauses_the_image_queue_and_read_ahead_without_moving(browser):
    with browser.new_context(viewport={"width": 1100, "height": 700}) as context:
        page = context.new_page()
        page.route("**/*", lambda route: route.fulfill(path=DESKTOP / "index.html")
                   if route.request.url.endswith("/index.html") else route.fulfill(body=""))
        page.add_init_script("""
            window.calls = [];
            window.NovelDBQt = {postMessage(text) { calls.push(JSON.parse(text)); }};
            localStorage.setItem('noveldb.reader.look.v2', JSON.stringify({comicLayout: 'scroll'}));
        """)
        page.goto("https://catalog.test/index.html")
        page.add_script_tag(path=DESKTOP / "generic.js")
        page.add_style_tag(content="#flow img.slot { height: 350px !important; }")
        page.evaluate("""NovelDBGeneric.add([{title: 'A long comic', url: 'https://comics.test/book/1',
                          site: 'Example', source: 'site', comic: true, verified: true}])""")
        page.locator("#grid .card").first.click()
        page.locator("[data-act=read]").click()
        page.evaluate("""
            const book = calls.find(m => m.type === 'genericBook');
            NovelDBGeneric.fetched(book.id, {chapters: [{name: 'Chapter 1'}, {name: 'Chapter 2'}]});
        """)
        page.wait_for_function("calls.some(m => m.type === 'genericChapter' && m.index === 0)")
        page.evaluate("""
            const chapter = calls.find(m => m.type === 'genericChapter' && m.index === 0);
            const html = Array.from({length: 8}, (_, i) => '<img src="https://comics.test/img/' + i + '.png">').join('');
            NovelDBGeneric.fetched(chapter.id, {html, comic: true});
        """)
        page.wait_for_function("calls.filter(m => m.type === 'genericImage').length === 4")
        position = page.evaluate("""() => {
            const viewport = document.getElementById('viewport');
            viewport.scrollTop = 450;
            NovelDBGeneric.readerVisibility(false);
            calls.filter(m => m.type === 'genericImage').forEach(m => NovelDBGeneric.fetched(m.id, {
                dataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/2QAAAAASUVORK5CYII='
            }));
            return viewport.scrollTop;
        }""")
        assert position == 450
        playwright.expect(page.locator("#flow img.ready")).to_have_count(4)
        assert page.evaluate("calls.filter(m => m.type === 'genericImage').length") == 4
        assert page.evaluate("calls.filter(m => m.type === 'genericChapter').map(m => m.index)") == [0]

        page.evaluate("NovelDBGeneric.readerVisibility(true)")
        page.wait_for_function("calls.filter(m => m.type === 'genericImage').length === 8")
        page.wait_for_function("calls.some(m => m.type === 'genericChapter' && m.index === 1)")
        assert page.locator("#viewport").evaluate("element => element.scrollTop") == position
        playwright.expect(page.locator("#rChapterNo")).to_have_text("Chapter 1 of 2")
