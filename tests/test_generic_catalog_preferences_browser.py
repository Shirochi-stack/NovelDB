"""Default catalogue filtering and persistent preferred sites through the shared page UI."""

from pathlib import Path

import pytest

from tests.test_generic_page_merge_browser import browser


ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "noveldb/qt/catalog/generic",
    ROOT / "android/app/src/main/assets/catalog",
    ROOT / "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog",
]
NPIA_HOST = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"


@pytest.fixture(params=PAGES, ids=["desktop", "android", "ios"])
def catalogue(browser, request):
    context = browser.new_context()
    tab = context.new_page()
    html = (PAGES[0] / "index.html").read_text(encoding="utf-8")
    tab.route("http://catalog.test/**", lambda route: route.fulfill(body=html, content_type="text/html"))
    tab.route("https://**/*", lambda route: route.fulfill(body="", content_type="text/plain"))
    tab.add_init_script("""window.__sent = [];
        window.NovelDBQt = {postMessage: m => window.__sent.push(JSON.parse(m))};
        window.__LNR_sites = () => JSON.stringify([
            {id: 'general', site: 'https://books.test', lang: 'en', name: 'Books'},
            {id: 'excluded', site: 'https://chrysanthemumgarden.com', lang: 'en', name: 'Translations'}]);
        window.__popular = [];
        window.__LNR_popular = (id, page) => { window.__popular.push([id, page]); return Promise.resolve('[]'); };
    """)
    tab.goto("http://catalog.test/")
    script = (request.param / "generic.js").read_text(encoding="utf-8")
    tab.add_script_tag(content=script)
    tab.evaluate("""() => window.NovelDBGeneric.init({sites: ['books.test', 'chrysanthemumgarden.com'],
        comic: [], lnreader: [{id: 'general', site: 'books.test'},
                             {id: 'excluded', site: 'chrysanthemumgarden.com'}]})""")
    yield tab, script
    context.close()


def card(number, site="books.test", **extra):
    return dict(url=f"https://{site}/book/{number}", title=f"Novel {number}", site=site,
                source="site", description="A story about an adventure.", tags=["Action"],
                verified=True, via="top", **extra)


def add(tab, cards):
    tab.evaluate("cards => window.NovelDBGeneric.add(cards)", cards)
    tab.wait_for_timeout(350)


def titles(tab):
    return tab.locator("#grid .card .title").all_text_contents()


def test_default_and_scrolled_cards_exclude_specialist_hosts_and_genres(catalogue):
    tab, _ = catalogue
    assert tab.evaluate("() => window.__popular.every(p => p[0] !== 'excluded')")
    add(tab, [card(1), card(2, "m.chrysanthemumgarden.com"), card(3, "josei.test"), card(4)])
    # A second incoming batch uses the same route as endless scrolling.
    josei, danmei = card(5), card(6)
    josei["tags"], danmei["tags"] = ["Josei"], ["Danmei"]
    add(tab, [josei, danmei, card(7, "blnovels.net"), card(8)])
    assert set(titles(tab)) == {"Novel 1", "Novel 4", "Novel 8"}
    tab.evaluate("card => window.NovelDBGeneric.details(card)", dict(card(8), tags=["Josei"]))
    tab.wait_for_timeout(350)
    assert "Novel 8" not in titles(tab)


def test_explicit_search_includes_excluded_sites_and_tags(catalogue):
    tab, _ = catalogue
    tab.fill("#query", "Novel")
    tab.locator("#form").dispatch_event("submit")
    book = card(9, "chrysanthemumgarden.com")
    book.update(via="name", tags=["Josei", "BL"])
    add(tab, [book])
    assert titles(tab) == ["Novel 9"]


def test_preferred_site_uses_native_index_and_persists_without_narrowing_search(catalogue):
    tab, script = catalogue
    tab.click("#settingsButton")
    tab.select_option("#preferredSite", NPIA_HOST)
    messages = tab.evaluate("() => window.__sent")
    native = [m for m in messages if m["type"] == "genericNdbTop"][-1]
    assert native["sources"] == ["npia"] and native["page"] == 0
    assert tab.locator("#preferredSite option:checked").inner_text() == "npia"
    assert tab.locator('#preferredSite option[value="chrysanthemumgarden.com"]').count() == 0
    tab.reload()
    tab.add_script_tag(content=script)
    tab.evaluate("() => window.NovelDBGeneric.init({sites: [], comic: [], lnreader: []})")
    native = tab.evaluate("() => window.__sent.filter(m => m.type === 'genericNdbTop').pop()")
    assert native["sources"] == ["npia"]
    assert tab.input_value("#preferredSite") == NPIA_HOST
    add(tab, [card(20, NPIA_HOST), card(21)])
    assert titles(tab) == ["Novel 20"]
    tab.evaluate("id => window.NovelDBGeneric.ndbTopDone(id, {more: true})", native["id"])
    tab.evaluate("() => window.dispatchEvent(new Event('scroll'))")
    tab.wait_for_function("window.__sent.some(m => m.type === 'genericNdbTop' && m.page === 1)")
    following = tab.evaluate("() => window.__sent.filter(m => m.type === 'genericNdbTop').pop()")
    assert following["sources"] == ["npia"]
    josei = card(22, NPIA_HOST)
    josei["tags"] = ["Josei"]
    add(tab, [josei, card(23, NPIA_HOST)])
    assert set(titles(tab)) == {"Novel 20", "Novel 23"}
    tab.select_option("#kind", "manga")
    assert tab.input_value("#preferredSite") == ""
    tab.select_option("#kind", "webnovel")
    assert tab.input_value("#preferredSite") == NPIA_HOST
    tab.fill("#query", "Novel")
    tab.locator("#form").dispatch_event("submit")
    query = tab.evaluate("() => window.__sent.filter(m => m.type === 'genericSearch').pop()")
    assert all(not q.get("only") for q in query["queries"])


def test_cached_top_sources_are_filtered_and_preference_can_be_cleared(catalogue):
    tab, script = catalogue
    cached = card(10)
    cached["more"] = [card(10, "chrysanthemumgarden.com")]
    excluded = card(11, "dummynovels.com")
    tab.evaluate("""items => localStorage.setItem('noveldb.generic.top.v9.webnovel.en', JSON.stringify({
        time: Date.now(), how: 'sites:0:100', items: items}))""", [cached, excluded])
    tab.reload()
    tab.add_script_tag(content=script)
    tab.evaluate("() => window.NovelDBGeneric.init({sites: [], comic: [], lnreader: []})")
    tab.wait_for_timeout(350)
    assert titles(tab) == ["Novel 10"]
    assert "chrysanthemumgarden" not in tab.locator("#grid").inner_text()
    tab.click("#settingsButton")
    tab.select_option("#preferredSite", NPIA_HOST)
    tab.select_option("#preferredSite", "")
    assert tab.input_value("#preferredSite") == ""
    assert tab.locator("#heading").inner_text() == "Top web novels · English"
