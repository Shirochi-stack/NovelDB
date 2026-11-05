"""The generic catalogue page (noveldb/qt/catalog/generic/generic.js, the desktop's and the phones') in a
real browser: a site's card of a book NovelDB has joins NovelDB's card under its English title."""

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
