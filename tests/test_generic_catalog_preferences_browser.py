"""Default catalogue filtering and persistent preferred sites through the shared page UI."""

from pathlib import Path
import json

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
    tab.click("#tagsButton")
    assert tab.locator('#tagLists [data-tag="BL"], #tagLists [data-tag="Josei"]').count() == 0
    tab.locator("#tagFilter").fill("josei")
    assert tab.locator('#tagLists [data-tag="Josei"]').count() == 1
    tab.locator("#tagFilter").press("Escape")
    tab.fill("#query", "Novel")
    tab.locator("#form").dispatch_event("submit")
    book = card(9, "chrysanthemumgarden.com")
    book.update(via="name", tags=["Josei", "BL"])
    add(tab, [book])
    assert titles(tab) == ["Novel 9"]


def test_mangak_top_filters_tags_age_badges_and_unclassified_cards(catalogue):
    tab, _ = catalogue
    tab.select_option("#kind", "manhwa")
    cards = [card(n, "mangak.io", comic=True) for n in range(1, 6)]
    cards[1]["tags"] = ["Boys Love"]
    cards[2]["tags"] = ["18+"]
    cards[3]["description"] = "HOT 18+ Chapter 1"
    cards[4]["tags"] = []
    add(tab, cards)
    assert titles(tab) == ["Novel 1"]
    tab.evaluate("card => NovelDBGeneric.details(card)", dict(cards[0], tags=["Yaoi"]))
    assert tab.locator("#grid .card").count() == 0
    tab.fill("#query", "Novel 2")
    tab.locator("#form").dispatch_event("submit")
    add(tab, [dict(cards[1], via="name")])
    assert titles(tab) == ["Novel 2"]


def test_free_hosts_join_the_first_english_web_novel_page(catalogue):
    tab, _ = catalogue
    profile = json.loads((ROOT / "noveldb/core/catalog/search_profile.json").read_text(encoding="utf-8"))
    assert len(profile["freeTop"]) <= 16
    assert len(profile["quickSearch"]["all"]) <= 12
    tab.evaluate("""profile => {
        NovelDBGeneric.init({sites:[], comic:[], lnreader:[], responseProfile:profile});
        document.querySelector('#form').dispatchEvent(new Event('submit', {cancelable:true}));
    }""", profile)
    request = tab.evaluate("__sent.filter(m=>m.type==='genericTop' && m.kind==='webnovel').at(-1)")
    hosts = ["novelbuddy.me", "novelbin.com", "scribblehub.com", "royalroad.com", "asurascans.com"]
    assert set(hosts) <= set(request["sites"])
    assert not tab.evaluate("__sent.some(m=>m.type==='genericNdbTop')")
    index = [card(n, "books.test", noveldb={"source": "sfc", "id": str(n)}) for n in range(12)]
    add(tab, index)
    assert not titles(tab)
    add(tab, [card(n + 20, host) for n, host in enumerate(hosts)])
    assert len(titles(tab)) == len(hosts)
    assert set(hosts) <= set(tab.locator("#grid .site").all_text_contents())


def test_preferred_site_uses_native_index_and_persists_without_narrowing_search(catalogue):
    tab, script = catalogue
    tab.click("#settingsButton")
    tab.select_option("#language", "ko")
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


def test_new_requests_the_publisher_and_ignores_the_previous_index_reply(catalogue):
    tab, _ = catalogue
    tab.click("#settingsButton")
    tab.select_option("#language", "ko")
    tab.select_option("#preferredSite", NPIA_HOST)
    before = tab.evaluate("__sent.filter(m=>m.type==='genericNdbTop').at(-1)")
    tab.select_option("#sort", "new")
    after = tab.evaluate("__sent.filter(m=>m.type==='genericNdbTop').at(-1)")
    assert after["id"] != before["id"]
    assert after["sources"] == ["npia"] and after["sort"] == "new" and after["page"] == 0
    tab.evaluate("""([id, old, cards])=>{
        NovelDBGeneric.add([old], id);
        NovelDBGeneric.add(cards);
    }""", [before["id"], card(23, NPIA_HOST, noveldb={"source": "npia", "id": "23"}),
           [card(1, NPIA_HOST, published="2026-10-02"), card(2, NPIA_HOST, published="2026-10-03")]])
    tab.wait_for_function("document.querySelectorAll('#grid .card').length === 2")
    assert tab.locator("#grid .card .title").all_text_contents() == ["Novel 1", "Novel 2"]
    tab.evaluate("id => NovelDBGeneric.ndbTopDone(id, {more:false})", after["id"])
    assert "New releases" in tab.locator("#status").inner_text()


def test_empty_publisher_falls_back_to_newest_index_and_shows_loading(catalogue):
    tab, _ = catalogue
    tab.click("#settingsButton")
    tab.select_option("#language", "ko")
    tab.select_option("#preferredSite", NPIA_HOST)
    tab.select_option("#sort", "new")
    direct = tab.evaluate("__sent.filter(m=>m.type==='genericNdbTop').at(-1)")
    assert direct["sort"] == "new"
    tab.evaluate("id => NovelDBGeneric.ndbTopDone(id, {more:false})", direct["id"])
    fallback = tab.evaluate("__sent.filter(m=>m.type==='genericNdbTop').at(-1)")
    assert fallback["sort"] == "index-new" and fallback["page"] == 0 and fallback["sources"] == ["npia"]
    assert tab.locator("#progressbar.indeterminate").is_visible()
    indexed = dict(card(1, NPIA_HOST, noveldb={"source": "npia", "id": "1"}), source="noveldb")
    add(tab, [indexed])
    assert titles(tab) == ["Novel 1"] and tab.locator("#progressbar").is_visible()
    tab.evaluate("id => NovelDBGeneric.ndbTopDone(id, {more:false})", fallback["id"])
    assert not tab.locator("#progressbar").is_visible()


def test_cached_top_sources_are_filtered_and_preference_can_be_cleared(catalogue):
    tab, script = catalogue
    cached = card(10)
    cached["more"] = [card(10, "chrysanthemumgarden.com")]
    excluded = card(11, "dummynovels.com")
    tab.evaluate("""items => localStorage.setItem('noveldb.generic.top.v9.webnovel.en', JSON.stringify({
        time: Date.now(), how: 'references-v1:0:100', items: items}))""", [cached, excluded])
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


def test_previous_hundred_card_cache_renders_twelve_then_grows_on_scroll(catalogue):
    tab, script = catalogue
    tab.evaluate("""items=>localStorage.setItem('noveldb.generic.top.v9.webnovel.en',JSON.stringify({
        time:Date.now(),how:'references-v1:0:100',items:items}))""", [card(n) for n in range(100)])
    tab.reload()
    tab.add_script_tag(content=script)
    tab.evaluate("NovelDBGeneric.init({sites:[],comic:[],lnreader:[]})")
    tab.wait_for_timeout(350)
    assert titles(tab) == [f'Novel {n}' for n in range(12)]
    tab.evaluate("window.scrollTo(0,document.body.scrollHeight);window.dispatchEvent(new Event('scroll'))")
    tab.wait_for_function("document.querySelectorAll('#grid .card .title').length>=36")
    assert titles(tab)[:12] == [f'Novel {n}' for n in range(12)]
    assert len(titles(tab)) == 36


def test_native_first_page_stays_small_until_scrolling(catalogue):
    tab, _ = catalogue
    tab.click('#settingsButton')
    tab.select_option('#language', 'ko')
    tab.select_option('#preferredSite', NPIA_HOST)
    native = tab.evaluate("__sent.filter(m=>m.type==='genericNdbTop').at(-1)")
    tab.evaluate("args=>NovelDBGeneric.add(args.cards,args.id)",
                 {'id': native['id'], 'cards': [card(n, NPIA_HOST) for n in range(40)]})
    tab.evaluate("id=>NovelDBGeneric.ndbTopDone(id,{more:true})", native['id'])
    tab.wait_for_timeout(350)
    assert len(titles(tab)) == 12
    tab.evaluate("window.scrollTo(0,document.body.scrollHeight);window.dispatchEvent(new Event('scroll'))")
    tab.wait_for_function("document.querySelectorAll('#grid .card .title').length>=36")
    assert len(titles(tab)) == 36
