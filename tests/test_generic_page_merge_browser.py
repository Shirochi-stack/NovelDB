"""The generic catalogue page (noveldb/qt/catalog/generic/generic.js, the desktop's and the phones') in a
real browser: a site's card of a book NovelDB has joins NovelDB's card under its English title."""

import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PAGE = Path(__file__).resolve().parents[1] / "noveldb" / "qt" / "catalog" / "generic"
MPIA = "https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com/novel/detail/573666"
NSERIES = "https://series.\u006e\u0061\u0076\u0065\u0072.com/novel/detail.series?productNo=14672427"
RAW = "군필 여대생의 게임방송"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            instance = p.chromium.launch(headless=True)
        except Exception as error:  # no browser installed
            if "Executable doesn't exist" in str(error):
                pytest.skip("Playwright's Chromium is not installed")
            raise
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    tab = context.new_page()
    tab.route("**/*", lambda route: route.fulfill(body="", content_type="text/plain"))
    tab.add_init_script("window.NovelDBQt = {postMessage: function () {}};")
    tab.set_content((PAGE / "index.html").read_text(encoding="utf-8"))
    tab.add_script_tag(content=(PAGE / "generic.js").read_text(encoding="utf-8"))
    yield tab
    context.close()


def site_card(**extra):
    return dict({"url": MPIA, "title": RAW, "author": "안현", "cover": "", "description": "", "tags": ["현대판타지"],
                 "site": "mpia", "source": "site", "views": "12", "verified": True}, **extra)


def noveldb_card(**extra):
    return dict({"url": MPIA + "/", "title": "A Military-Trained College Girl's Gaming Stream", "originalTitle": RAW,
                 "author": "안현™", "cover": "https://covers.test/1.jpg", "description": "", "tags": ["Modern Fantasy", "Game"],
                 "site": "mpia", "source": "noveldb", "noveldb": {"source": "mpia", "id": "573666"}, "views": "655,161",
                 "chapters": "134", "also": [{"url": NSERIES, "noveldb": {"source": "nseries", "id": "14672427"}}]}, **extra)


def titles(page):
    page.wait_for_timeout(300)
    return page.eval_on_selector_all("#grid .title", "els => els.map(e => e.textContent)")


def test_a_site_card_first_then_noveldbs_gives_one_card_with_the_english_title(page):
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url="https://m.example.test/573666")])
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [noveldb_card()])
    found = titles(page)
    assert len(found) == 1 and found[0].startswith("A Military-Trained")
    # NovelDB's other source and the site's page are its sources: three in all.
    assert page.eval_on_selector("#grid .site", "e => e.textContent").endswith("+2")


def test_noveldbs_card_first_takes_the_sites_card(page):
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [noveldb_card()])
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url=NSERIES), site_card(url="https://m.example.test/1")])
    found = titles(page)
    assert len(found) == 1 and found[0].startswith("A Military-Trained")


def test_the_same_raw_title_by_another_author_stays_apart(page):
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [noveldb_card(also=[])])
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url="https://m.example.test/2", author="다른 작가")])
    assert len(titles(page)) == 2


def test_live_figures_replace_the_index_ones_but_not_the_english_title(page):
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [noveldb_card(also=[], chapters="123")])
    page.evaluate("card => window.NovelDBGeneric.details(card)",
                  noveldb_card(also=[], title="A Military-Trained College Girl's Gaming Stream", chapters="143",
                               views="667,747", fresh=True, complete=True, description="Synopsis."))
    page.wait_for_timeout(300)
    text = page.eval_on_selector("#grid .card", "e => e.textContent")
    assert "A Military-Trained" in text and "143" in text and "123" not in text


def test_an_adults_only_book_gets_the_19_badge_at_the_covers_bottom_right(page):
    # A site's card without a rating, then NovelDB's with one: the card shows NovelDB's 19+ badge.
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url="https://m.example.test/573666")])
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [noveldb_card(ageRating=19)])
    page.wait_for_timeout(300)
    badges = page.eval_on_selector_all("#grid .card .cover .age", "els => els.map(e => [e.className, e.textContent])")
    assert badges == [["age r19", "19+"]]
    cover, badge = page.evaluate("""() => {
        const c = document.querySelector('#grid .card .cover').getBoundingClientRect();
        const b = document.querySelector('#grid .card .cover .age').getBoundingClientRect();
        return [[c.right, c.bottom], [b.right, b.bottom]];
    }""")
    assert 0 <= cover[0] - badge[0] <= 12 and 0 <= cover[1] - badge[1] <= 12  # its bottom-right corner


def test_a_teen_book_gets_the_15_badge_and_others_none(page):
    page.evaluate("cards => window.NovelDBGeneric.add(cards)",
                  [noveldb_card(ageRating=15), noveldb_card(url="https://other.test/2", title="Another", originalTitle="다른",
                                                            noveldb={"source": "npia", "id": "2"}, also=[])])
    page.wait_for_timeout(300)
    assert page.eval_on_selector_all("#grid .card .cover .age", "els => els.map(e => e.textContent)") == ["15"]


def test_the_tag_menu_offers_r15_and_r19():
    text = (PAGE / "generic.js").read_text(encoding="utf-8")
    assert "'R15', 'R19'" in text


def test_noveldbs_card_at_the_same_address_gives_the_site_card_its_translation(page):
    # A live site card first, then NovelDB's card of the same book (same address): one card, NovelDB's English
    # title and its rating, with the site's figures kept where NovelDB has none.
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url=MPIA)])
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [noveldb_card(url=MPIA, also=[], ageRating=19)])
    assert titles(page) == ["A Military-Trained College Girl's Gaming Stream"]
    assert page.eval_on_selector_all("#grid .card .cover .age", "els => els.map(e => e.textContent)") == ["19+"]


def test_the_translate_buttons_sit_in_the_rows_they_had():
    # Google Translate: an icon in the details panel's action row and the reader's top bar, no new row.
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    script = (PAGE / "generic.js").read_text(encoding="utf-8")
    bar = html[html.index('<div class="bar top">'):html.index("</div>", html.index('id="rLook"'))]
    assert 'id="rTranslate"' in bar and "🌐" in bar
    assert "data-act=\"translate\"" in script and "translate.googleapis.com/translate_a/t?" in script


def test_the_reader_translates_its_chapter_and_back(page):
    page.route("https://translate.googleapis.com/**", lambda route: route.fulfill(
        status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
        body=json.dumps([["Chapter one", "ko"], ["Hello there.", "ko"]])))
    page.evaluate("""() => { document.getElementById('flow').innerHTML =
        '<h2 class="chapter">제1화</h2><p>안녕하세요.</p><p><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt=""></p>'; }""")
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(500)
    assert page.eval_on_selector_all("#flow h2, #flow p", "els => els.map(e => e.textContent)")[:2] == ["Chapter one", "Hello there."]
    assert page.eval_on_selector_all("#flow img", "els => els.length") == 1
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(300)
    assert page.eval_on_selector_all("#flow h2, #flow p", "els => els.map(e => e.textContent)")[:2] == ["제1화", "안녕하세요."]


def test_the_sort_menu_orders_the_shown_cards(page):
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [
        noveldb_card(url="https://a.test/1", title="Old", originalTitle="옛", noveldb={"source": "npia", "id": "1"}, also=[], published="2020-01-01", likes="5"),
        noveldb_card(url="https://a.test/2", title="New", originalTitle="새", noveldb={"source": "npia", "id": "2"}, also=[], published="2026-01-01", likes="9")])
    page.wait_for_timeout(300)
    page.select_option("#sort", "new")
    page.wait_for_timeout(300)
    ordered = page.evaluate("""() => [...document.querySelectorAll('#grid .card')].filter(c => c.style.display !== 'none')
        .sort((a, b) => Number(a.style.order) - Number(b.style.order)).map(c => c.querySelector('.title').textContent)""")
    assert ordered == ["New", "Old"]


def shown_order(page):
    return page.evaluate("""() => [...document.querySelectorAll('#grid .card')].filter(c => c.style.display !== 'none')
        .sort((a, b) => Number(a.style.order) - Number(b.style.order)).map(c => c.querySelector('.title').textContent)""")


def dated(number, title, published):
    return noveldb_card(url=f"https://a.test/{number}", title=title, originalTitle=title, also=[],
                        noveldb={"source": "npia", "id": str(number)}, published=published)


def test_cards_keep_their_places_then_sort_once_and_again_when_pulled_past_the_top(page):
    """New stayed in the order the books came in: the cards sort once soon after, and again on a pull."""
    page.select_option("#sort", "new")
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [dated(1, "Old", "2020-01-01")])
    page.wait_for_timeout(300)
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [dated(2, "New", "2026-01-01")])
    page.wait_for_timeout(300)
    assert shown_order(page) == ["Old", "New"]  # placed cards stay put while books come in
    page.wait_for_timeout(3000)
    assert shown_order(page) == ["New", "Old"]  # then once in the full order
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [dated(3, "Newest", "2026-06-01")])
    page.wait_for_timeout(3300)
    assert shown_order(page) == ["New", "Old", "Newest"]  # only once
    page.mouse.move(200, 200)
    for _ in range(4):
        page.mouse.wheel(0, -250)
        page.wait_for_timeout(50)
    page.wait_for_timeout(200)
    assert shown_order(page) == ["Newest", "New", "Old"]


def test_a_card_that_leaves_a_sorted_list_leaves_its_place_not_a_gap_at_the_top(page):
    """Its slot had no place in the sort and went first: the first row of a sorted top list stood empty."""
    page.select_option("#sort", "new")
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [dated(n, f"Book {n}", f"2026-0{n}-01") for n in range(1, 5)])
    page.wait_for_timeout(3500)
    page.evaluate("() => window.NovelDBGeneric.add([{url: 'https://a.test/2', empty: true}])")
    page.wait_for_timeout(200)
    cells = page.evaluate("""() => [...document.querySelectorAll('#grid > .card')].filter(c => c.style.display !== 'none')
        .sort((a, b) => Number(a.style.order) - Number(b.style.order))
        .map(c => c.classList.contains('hole') ? 'hole' : c.querySelector('.title').textContent)""")
    assert cells == ["Book 4", "Book 3", "hole", "Book 1"]


def test_a_top_list_book_found_thin_stays_once_seen_and_leaves_if_not(page):
    """Books left the top list when their pages came in: cards vanished as it was scrolled, the rest moved up."""
    page.evaluate("() => { window.__sent = []; window.NovelDBQt = {postMessage: m => window.__sent.push(JSON.parse(m))}; }")
    page.select_option("#kind", "manga")
    page.evaluate("() => window.NovelDBGeneric.init({sites: ['comics.test'], comic: ['comics.test'], lnreader: []})")
    page.wait_for_timeout(200)
    top = page.evaluate("() => window.__sent.filter(m => m.type === 'genericTop').pop()")
    cards = [{"url": f"https://comics.test/manga/{n}", "title": f"Comic {n}", "site": "comics.test", "source": "site",
              "comic": True, "tags": [], "description": "", "via": "top", "siteRank": 0, "position": n, "listLength": 40}
             for n in range(40)]
    page.evaluate("a => window.NovelDBGeneric.add(a[0], a[1])", [cards, top["id"]])
    page.wait_for_timeout(800)
    for n in (0, 39):  # on screen, and far below it
        page.evaluate("card => window.NovelDBGeneric.details(card)", dict(cards[n], via=None, complete=True))
    page.wait_for_timeout(300)
    shown = titles(page)
    assert "Comic 0" in shown and "Comic 39" not in shown


def test_one_books_page_on_a_site_written_two_ways_is_one_source_and_one_row(page):
    """A site's search and a web search wrote one page two ways: the details listed the site twice."""
    book = "https://mangadex.test/title/a1b2c3d4-1111-2222-3333-444455556666"
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [
        site_card(url=book, site="mangadex.test", title="Move Aside", comic=True),
        site_card(url="http://www." + book[8:] + "/move-aside-i-ll-be/", site="mangadex.test", title="Move Aside", comic=True),
        site_card(url="https://other.test/book/5", site="other.test", title="Move Aside", comic=True)])
    page.wait_for_timeout(300)
    assert page.eval_on_selector_all("#grid .site", "e => e.map(x => x.textContent)") == ["mangadex.test +1"]
    page.click("#grid .card .title")
    page.wait_for_timeout(300)
    rows = page.eval_on_selector_all("#dialog .sources > div > span", "els => els.map(e => e.textContent)")
    assert [r.split(" ")[0] for r in rows] == ["mangadex.test", "other.test"]


def test_a_library_book_opens_at_its_site_and_its_title_is_searched(page):
    sent = page.evaluate("""() => { window.__sent = []; window.NovelDBQt = {postMessage: t => window.__sent.push(JSON.parse(t))}; return 1; }""")
    page.evaluate("book => window.NovelDBGeneric.openBook(book)", {"title": RAW, "url": MPIA, "author": "안현"})
    page.wait_for_timeout(300)
    assert page.eval_on_selector("#query", "e => e.value") == RAW
    assert page.eval_on_selector("#dialog", "d => d.open")
    assert RAW in page.eval_on_selector("#dialog", "d => d.innerText")
    searches = page.evaluate("() => window.__sent.filter(m => m.type === 'genericSearch')")
    assert sent and searches and searches[-1]["queries"][0] == {"query": RAW, "mode": "name", "sort": ""} | {
        k: v for k, v in searches[-1]["queries"][0].items() if k not in ("query", "mode", "sort")}


def test_translations_go_into_the_language_picked_in_the_settings(page):
    asked = []

    def answer(route):
        asked.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps([["Bonjour.", "ko"]]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("() => { var s = document.getElementById('translateTo'); s.value = 'fr'; s.dispatchEvent(new Event('change')); }")
    page.evaluate("() => { document.getElementById('flow').innerHTML = '<p>안녕하세요.</p>'; }")
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(500)
    assert asked and "tl=fr" in asked[0]
    assert page.eval_on_selector("#flow p", "e => e.textContent") == "Bonjour."


def test_more_loading_shows_a_loader_and_keeps_rows_full(page):
    page.set_viewport_size({"width": 400, "height": 800})
    page.evaluate("() => document.getElementById('query').value = 'Book'")
    page.evaluate("() => document.getElementById('go').click()")
    cards = [site_card(url=f"https://m.example.test/{n}", title=f"Book {n}") for n in range(5)]
    page.evaluate("cards => window.NovelDBGeneric.add(cards, undefined)", [dict(c, via="name") for c in cards])
    page.wait_for_timeout(400)
    columns = page.evaluate("() => getComputedStyle(document.getElementById('grid')).gridTemplateColumns.split(' ').length")
    shown = page.evaluate("() => Array.from(document.querySelectorAll('#grid > *')).filter(e => e.offsetParent).length")
    assert page.eval_on_selector("#moreLoading", "e => e.classList.contains('on')")  # the search still runs
    assert shown % columns == 0 and shown < 5


def test_the_texts_go_to_google_in_one_request_each_its_own_answer(page):
    """Every text its own `q` (Google's many-texts address answers one translation per text: no lines to match),
    and a text shown twice (a tag on many cards) asked once."""
    asked = []

    def answer(route):
        texts = __import__("urllib.parse", fromlist=["x"]).parse_qs(route.request.post_data)["q"]
        asked.append(texts)
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps([[t.upper(), "en"] for t in texts]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("() => { document.getElementById('flow').innerHTML = '<p>aa</p><p>bb' + String.fromCharCode(10) + 'b</p><p>aa</p><p>dd</p>'; }")
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(600)
    assert asked == [["aa", "bb" + chr(10) + "b", "dd"]]
    assert page.eval_on_selector_all("#flow p", "els => els.map(e => e.textContent)") == ["AA", "BB" + chr(10) + "B", "AA", "DD"]


def test_a_translate_request_the_browser_refuses_goes_through_the_app(page):
    """The desktop's page is a local file: Qt lets it ask no other site, so Google is asked through the app."""
    page.route("https://translate.googleapis.com/**", lambda route: route.abort())
    page.evaluate("""() => {
        window.__asked = [];
        window.NovelDBQt = {postMessage: function (text) {
            var m = JSON.parse(text);
            if (m.type !== 'genericFetch') return;
            window.__asked.push(m.request.url);
            var body = JSON.stringify([["Hello.", "ko"]]);
            var b64 = btoa(unescape(encodeURIComponent(body)));
            setTimeout(function () { window.NovelDBGeneric.fetched(m.id, {status: 200, responseBase64: b64}); }, 10);
        }};
        document.getElementById('flow').innerHTML = '<p>안녕하세요.</p>';
    }""")
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(600)
    assert page.evaluate("() => window.__asked.length") == 1
    assert page.eval_on_selector("#flow p", "e => e.textContent") == "Hello."


def test_with_the_cards_translated_a_books_details_open_translated(page):
    """The details' 🌐 named a list of parts that was gone: with the cards translated, no details opened."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def answer(route):
        texts = __import__("urllib.parse", fromlist=["x"]).parse_qs(route.request.post_data)["q"]
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps([["EN " + t, "ko"] for t in texts]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("() => document.getElementById('translateCards').click()")
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url="https://m.example.test/1", description="소개")])
    page.wait_for_timeout(800)
    page.click("#grid .card .title")
    page.wait_for_timeout(800)
    assert not errors
    assert page.eval_on_selector("#dialog", "d => d.open")
    assert page.eval_on_selector("#dialog .details h2", "e => e.textContent").startswith("EN ")


PICTURE = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='800' height='1200'><rect width='800' height='1200'/></svg>"


def comic_reader(browser, layout, book=None):
    """A phone's reader on a comic whose first chapter is still coming in: three of its pages found so far;
    `book`: a card opened with Read now, else a Library book."""
    context = browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True)
    tab = context.new_page()
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    tab.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html") if route.request.url == "https://reader.test/"
              else route.fulfill(body="", content_type="text/plain"))
    tab.add_init_script("localStorage.setItem('noveldb.reader.look.v2', JSON.stringify({comicLayout: '%s'}));" % layout)
    tab.goto("https://reader.test/")
    tab.add_script_tag(content=(PAGE / "generic.js").read_text(encoding="utf-8"))
    tab.evaluate("() => { window.__sent = []; window.NovelDBQt = {postMessage: m => window.__sent.push(JSON.parse(m))}; }")
    sent = lambda kind: [m for m in tab.evaluate("() => window.__sent") if m.get("type") == kind]
    if book and book.get("via") == "top":
        # From the top list of manga (its books are "manga" whatever they are).
        tab.select_option("#kind", "manga")
        tab.evaluate("() => window.NovelDBGeneric.init({sites: ['comics.test'], comic: ['comics.test'], lnreader: []})")
        tab.wait_for_timeout(200)
        top = [m for m in tab.evaluate("() => window.__sent") if m.get("type") == "genericTop"][-1]
        tab.evaluate("a => window.NovelDBGeneric.add([a[0]], a[1])", [book, top["id"]])
        tab.wait_for_timeout(800)
        tab.click("#grid .card .title")
        tab.wait_for_timeout(300)
        tab.click("#dialog [data-act=read]")
    elif book:
        tab.evaluate("card => window.NovelDBGeneric.add([card])", book)
        tab.wait_for_timeout(300)
        tab.click("#grid .card .title")
        tab.wait_for_timeout(300)
        tab.click("#dialog [data-act=read]")
    else:
        tab.evaluate("() => window.NovelDBGeneric.readLocal({url: 'https://x.test/b', title: 'T'})")
    tab.wait_for_timeout(200)
    tab.evaluate("id => window.NovelDBGeneric.fetched(id, {chapters: [{name: 'One'}, {name: 'Two'}], comic: true})", sent("genericBook")[0]["id"])
    tab.wait_for_timeout(300)
    tab.evaluate("a => window.NovelDBGeneric.readerImages(a[0], {images: [a[1], a[1], a[1]], total: 3})",
                 [sent("genericChapter")[0]["id"], PICTURE])
    tab.wait_for_timeout(500)
    for message in sent("genericImage"):
        tab.evaluate("a => window.NovelDBGeneric.fetched(a[0], {dataUrl: a[1]})", [message["id"], PICTURE])
    tab.wait_for_timeout(500)
    return context, tab


def test_a_swipe_in_a_chapter_still_coming_in_turns_its_page_not_the_chapter(browser):
    """It was counted one page when it opened: every swipe went to the next chapter until the page was reloaded."""
    context, tab = comic_reader(browser, "paged")
    try:
        def swipe():
            tab.evaluate("""() => { const r = document.getElementById('reader');
              const touch = (type, x) => { const t = new Touch({identifier: 1, target: r, clientX: x, clientY: 400});
                r.dispatchEvent(new TouchEvent(type, {touches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true})); };
              touch('touchstart', 300); touch('touchmove', 250); touch('touchmove', 100); touch('touchend', 100); }""")
            tab.wait_for_timeout(400)
            return tab.evaluate("() => [document.getElementById('rWhere').textContent, document.getElementById('rChapterNo').textContent]")
        assert swipe() == ["Page 2 of 5", "Chapter 1 of 2"]  # three pages and two still to come
        assert swipe() == ["Page 3 of 5", "Chapter 1 of 2"]
    finally:
        context.close()


def test_a_scrolled_chapter_still_coming_in_shows_halgakos_for_the_pages_to_come(browser):
    context, tab = comic_reader(browser, "scroll")
    try:
        slots = tab.evaluate("""() => [...document.querySelectorAll('#flow img.slot')]
            .map(i => i.classList.contains('ready') ? 'page' : i.getBoundingClientRect().height > 100 ? 'halgakos' : 'none')""")
        assert slots == ["page", "page", "page", "halgakos", "halgakos"]
    finally:
        context.close()


def test_a_search_comes_back_when_the_page_is_opened_again(browser):
    """Android ends a page left in the background and the app opens it again: it showed the top list."""
    context = browser.new_context()
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    script = (PAGE / "generic.js").read_text(encoding="utf-8")

    def open_page():
        tab = context.new_page()
        tab.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html") if route.request.url == "https://catalog.test/"
                  else route.fulfill(body="", content_type="text/plain"))
        tab.goto("https://catalog.test/")
        tab.add_script_tag(content=script)
        tab.evaluate("() => { window.__sent = []; window.NovelDBQt = {postMessage: m => window.__sent.push(JSON.parse(m))}; }")
        return tab

    try:
        tab = open_page()
        tab.fill("#query", "magical girl")
        tab.press("#query", "Enter")
        tab.wait_for_timeout(300)
        search = [m for m in tab.evaluate("() => window.__sent") if m.get("type") == "genericSearch"][-1]
        tab.evaluate("a => window.NovelDBGeneric.add(a[0], a[1])",
                     [[dated(n, f"Book {n}", f"2026-0{n}-01") for n in range(1, 4)], search["id"]])
        tab.wait_for_timeout(1500)
        tab.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        tab.close()

        again = open_page()
        again.evaluate("() => window.NovelDBGeneric.init({sites: [], comic: [], lnreader: []})")
        again.wait_for_timeout(700)
        assert again.input_value("#query") == "magical girl"
        assert sorted(titles(again)) == ["Book 1", "Book 2", "Book 3"]
        assert "magical girl" in again.eval_on_selector("#heading", "e => e.textContent")
        # Asking for the top list leaves nothing to come back to.
        again.click("#backTop")
        assert again.evaluate("() => localStorage.getItem('noveldb.generic.search.v1')") in (None, "null")
    finally:
        context.close()


def test_auto_reads_a_manhwa_tagged_manga_in_scroll_and_a_manga_in_pages(browser):
    """Auto went by "manga" (a tag, or the top list of manga it came from): manhwa opened in pages."""
    for tags, via, layout in ((["Manga", "Manhwa", "Action"], "", "scroll"), (["Manga", "Shounen"], "", "paged"),
                              (["Action", "Webtoon/Webcomic"], "top", "scroll"), (["Action", "Shounen"], "top", "paged")):
        book = {"url": "https://comics.test/title/1", "title": "Some Comic", "site": "comics.test", "source": "site",
                "comic": True, "tags": tags, "author": "", "cover": "", "description": "x", "via": via}
        context, tab = comic_reader(browser, "auto", book)
        try:
            shown = "scroll" if "scroll" in tab.evaluate("() => document.getElementById('reader').className") else "paged"
            assert shown == layout, tags
        finally:
            context.close()


def test_after_a_search_the_sites_panel_lists_every_site_to_find_and_scroll(page):
    """It listed only the sites books came from: every other site could not be found or picked."""
    page.evaluate("() => { window.__sent = []; window.NovelDBQt = {postMessage: m => window.__sent.push(JSON.parse(m))}; }")
    sites = [f"site{n}.test" for n in range(400)]
    page.evaluate("s => window.NovelDBGeneric.init({sites: s, comic: [], lnreader: []})", sites)
    page.fill("#query", "a book")
    page.press("#query", "Enter")
    page.wait_for_timeout(300)
    search = [m for m in page.evaluate("() => window.__sent") if m.get("type") == "genericSearch"][-1]
    page.evaluate("a => window.NovelDBGeneric.add(a[0], a[1])", [[site_card(url="https://site7.test/b/1", site="site7.test", title="A Book")], search["id"]])
    page.wait_for_timeout(300)
    page.click("#sitesButton")
    page.wait_for_timeout(300)
    rows = lambda: page.eval_on_selector_all("#sitespanel label.siterow span:first-of-type", "els => els.map(e => e.textContent)")
    first = rows()
    assert first[0] == "site7.test" and "site8.test" in first and len(first) < 400  # the rest come as it is scrolled
    page.eval_on_selector("#sitespanel", "p => p.scrollTop = p.scrollHeight")
    page.wait_for_timeout(300)
    assert len(rows()) > len(first)
    page.fill("#siteFind", "site39")
    page.wait_for_timeout(300)
    assert sorted(rows()) == ["site39.test"] + [f"site39{n}.test" for n in range(10)]
    # Ticking a site no book came from searches it.
    page.click("#sitespanel label:has([data-other-site='site390.test'])")
    page.wait_for_timeout(1200)
    again = [m for m in page.evaluate("() => window.__sent") if m.get("type") == "genericSearch"][-1]
    assert again["id"] != search["id"] and "site390.test" in again["queries"][0]["only"]


def test_tag_results_keep_external_arrival_order_without_a_timed_reshuffle(page):
    """Streaming index metadata must not move the external cards already being read."""
    page.evaluate("() => { window.__sent = []; window.NovelDBQt = {postMessage: m => window.__sent.push(JSON.parse(m))}; }")
    page.click("#tagsButton")
    page.fill("#tagFilter", "Fantasy")
    page.press("#tagFilter", "Enter")
    page.wait_for_timeout(1500)
    search = [m for m in page.evaluate("() => window.__sent") if m.get("type") == "genericSearch"][-1]
    other = lambda n: site_card(url=f"https://bl.test/{n}", site="bl.test", title=f"Other {n}", tags=["Fantasy"], via="tag", verified=True)
    lead = noveldb_card(url="https://lead.test/1", title="Lead", originalTitle="리드", also=[], via="tag", verified=True,
                        noveldb={"source": "sfc", "id": "1"}, tags=["Fantasy"])
    page.evaluate("a => window.NovelDBGeneric.add(a[0], a[1])", [[other(1), other(2)], search["id"]])
    page.wait_for_timeout(300)
    page.evaluate("a => window.NovelDBGeneric.add(a[0], a[1])", [[lead], search["id"]])
    page.wait_for_timeout(3500)  # the one reshuffle
    assert shown_order(page) == ["Other 1", "Other 2", "Lead"]


def test_a_translated_title_is_fitted_again(page):
    """Google's longer English title overflowed the card: the size was fitted to the original only."""
    def answer(route):
        texts = __import__("urllib.parse", fromlist=["x"]).parse_qs(route.request.post_data)["q"]
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps([["The Extraordinarily Long Translated Title Of A Book That Goes On And On And On " + t, "ko"] for t in texts]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [site_card(url="https://m.example.test/1", title="짧은")])
    page.wait_for_timeout(400)
    before = page.eval_on_selector("#grid .card .title", "e => e.style.fontSize")
    page.evaluate("() => document.getElementById('translateCards').click()")
    page.wait_for_timeout(1200)
    assert page.eval_on_selector("#grid .card .title", "e => e.textContent").startswith("The Extraordinarily")
    after = page.eval_on_selector("#grid .card .title", "e => e.style.fontSize")
    assert before == "" and after != ""


def test_a_translated_card_drawn_again_stays_translated(page):
    """A card's synopsis came in and it was drawn again: it showed the original, then the translation again."""
    def answer(route):
        texts = __import__("urllib.parse", fromlist=["x"]).parse_qs(route.request.post_data)["q"]
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps([["EN " + t, "ko"] for t in texts]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("() => document.getElementById('translateCards').click()")
    card = site_card(url="https://m.example.test/1", title="제목")
    page.evaluate("cards => window.NovelDBGeneric.add(cards)", [card])
    page.wait_for_timeout(1200)
    assert page.eval_on_selector("#grid .card .title", "e => e.textContent") == "EN 제목"
    page.evaluate("card => window.NovelDBGeneric.details(card)", dict(card, description="새 소개", complete=True))
    seen = page.evaluate("""() => new Promise(done => { const seen = [];
        const watch = () => { const t = document.querySelector('#grid .card .title'); if (t) seen.push(t.textContent); };
        const timer = setInterval(watch, 10); setTimeout(() => { clearInterval(timer); done(seen); }, 900); })""")
    assert seen and all(text == "EN 제목" for text in seen), sorted(set(seen))


def test_a_novels_chapter_with_illustrations_reads_as_text(browser):
    """A royalroad chapter opening with a few illustrations was read as a comic: centred, paragraphs run together."""
    context, tab = comic_reader(browser, "auto")  # its pictures streamed in while it was read
    try:
        chapter = [m for m in tab.evaluate("() => window.__sent") if m.get("type") == "genericChapter"][0]
        text = "".join(f"<p>Paragraph {n} of the chapter, with enough words in it to be a novel's text.</p>" for n in range(80))
        html = "".join(f'<img src="{PICTURE}">' for _ in range(5)) + text
        tab.evaluate("a => window.NovelDBGeneric.fetched(a[0], {html: a[1], title: 'Chapter One'})", [chapter["id"], html])
        tab.wait_for_timeout(500)
        assert "comic" not in tab.evaluate("() => document.getElementById('reader').className")
        assert tab.eval_on_selector_all("#flow p", "ps => ps.length") >= 80
    finally:
        context.close()


def test_a_page_that_did_not_come_is_asked_again_and_shown_when_it_does(browser):
    """Pages that came late showed as missing (a broken picture) until the reader was opened again."""
    context, tab = comic_reader(browser, "scroll")
    try:
        chapter = [m for m in tab.evaluate("() => window.__sent") if m.get("type") == "genericChapter"][0]
        late = "https://cdn.test/late.jpg"
        tab.evaluate("a => window.NovelDBGeneric.readerImages(a[0], {images: [a[1], a[1], a[1], a[2]], total: 4})",
                     [chapter["id"], PICTURE, late])
        tab.wait_for_timeout(300)
        asks = lambda: [m for m in tab.evaluate("() => window.__sent") if m.get("type") == "genericImage" and m["url"] == late]
        assert len(asks()) == 1
        tab.evaluate("id => window.NovelDBGeneric.fetched(id, null)", asks()[0]["id"])  # it did not come
        tab.wait_for_timeout(300)
        slot = "#flow img.slot:nth-of-type(4)"
        assert tab.eval_on_selector(slot, "i => !i.classList.contains('ready') && !i.getAttribute('src')")  # Halgakos, no broken picture
        tab.wait_for_timeout(1500)
        assert len(asks()) == 2  # asked again
        tab.evaluate("a => window.NovelDBGeneric.fetched(a[0], {dataUrl: a[1]})", [asks()[1]["id"], PICTURE])
        tab.wait_for_timeout(500)
        assert tab.eval_on_selector(slot, "i => i.classList.contains('ready')")
    finally:
        context.close()


def test_reload_with_the_reader_open_scrapes_its_chapter_again(browser):
    """The app's Reload reloaded the whole Catalog (the book and the reader with it)."""
    context, tab = comic_reader(browser, "scroll")
    try:
        chapters = lambda: [m for m in tab.evaluate("() => window.__sent") if m.get("type") == "genericChapter"]
        before = len(chapters())
        assert tab.evaluate("() => window.NovelDBGeneric.reloadReader()") is True
        tab.wait_for_timeout(300)
        assert len(chapters()) == before + 1 and chapters()[-1]["index"] == 0
        assert "open" in tab.evaluate("() => document.getElementById('reader').className")
        tab.evaluate("() => document.getElementById('reader').classList.remove('open')")
        assert tab.evaluate("() => window.NovelDBGeneric.reloadReader()") is False
    finally:
        context.close()
