"""The generic catalogue page (noveldb/qt/catalog/generic/generic.js, the desktop's and the phones') in a
real browser: a site's card of a book NovelDB has joins NovelDB's card under its English title."""

import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PAGE = Path(__file__).resolve().parents[1] / "noveldb" / "qt" / "catalog" / "generic"
MPIA = "https://www.munpia.com/novel/detail/573666"
NSERIES = "https://series.naver.com/novel/detail.series?productNo=14672427"
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
    assert "data-act=\"translate\"" in script and "translate.googleapis.com/translate_a/single" in script


def test_the_reader_translates_its_chapter_and_back(page):
    page.route("https://translate.googleapis.com/**", lambda route: route.fulfill(
        status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
        body=json.dumps([[["Chapter one" + chr(10), "x"], ["Hello there.", "y"]]])))
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
                      body=json.dumps([[["Bonjour.", "x"]]]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("() => { var s = document.getElementById('translateTo'); s.value = 'fr'; s.dispatchEvent(new Event('change')); }")
    page.evaluate("() => { document.getElementById('flow').innerHTML = '<p>안녕하세요.</p>'; }")
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(500)
    assert asked and "tl=fr" in asked[0]
    assert page.eval_on_selector("#flow p", "e => e.textContent") == "Bonjour."


def test_more_loading_shows_a_loader_and_keeps_rows_full(page):
    page.set_viewport_size({"width": 400, "height": 800})
    page.evaluate("() => document.getElementById('query').value = 'x'")
    page.evaluate("() => document.getElementById('go').click()")
    cards = [site_card(url=f"https://m.example.test/{n}", title=f"Book {n}") for n in range(5)]
    page.evaluate("cards => window.NovelDBGeneric.add(cards, undefined)", [dict(c, via="name") for c in cards])
    page.wait_for_timeout(400)
    columns = page.evaluate("() => getComputedStyle(document.getElementById('grid')).gridTemplateColumns.split(' ').length")
    shown = page.evaluate("() => Array.from(document.querySelectorAll('#grid > *')).filter(e => e.offsetParent).length")
    assert page.eval_on_selector("#moreLoading", "e => e.classList.contains('on')")  # the search still runs
    assert shown % columns == 0 and shown < 5


def test_a_batch_google_answers_in_other_lines_is_asked_again_in_halves(page):
    asked = []

    def answer(route):
        texts = __import__("urllib.parse", fromlist=["x"]).parse_qs(route.request.post_data)["q"][0].split(chr(10))
        asked.append(len(texts))
        if len(texts) > 2:
            texts = [texts[0] + " " + texts[1]] + texts[2:]  # Google merged two lines: no longer one per text
        out = [[t.upper() + ("" if i == len(texts) - 1 else chr(10)), t] for i, t in enumerate(texts)]
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps([out]))

    page.route("https://translate.googleapis.com/**", answer)
    page.evaluate("() => { document.getElementById('flow').innerHTML = '<p>aa</p><p>bb</p><p>cc</p><p>dd</p>'; }")
    page.evaluate("() => document.getElementById('rTranslate').click()")
    page.wait_for_timeout(600)
    assert asked[0] == 4 and sorted(asked[1:]) == [2, 2]
    assert page.eval_on_selector_all("#flow p", "els => els.map(e => e.textContent)") == ["AA", "BB", "CC", "DD"]
