import pytest

from tests.test_reader_toc_browser import browser, DESKTOP, MOBILE


@pytest.fixture(params=[DESKTOP, *MOBILE], ids=["desktop", "android", "ios"])
def page(browser, request):
    with browser.new_context(viewport={"width": 600, "height": 850}) as context:
        tab = context.new_page()
        tab.set_content((request.param / "index.html").read_text(encoding="utf-8"))
        tab.evaluate("window.calls=[];window.NovelDBQt={postMessage:m=>calls.push(JSON.parse(m))}")
        tab.add_script_tag(path=request.param / "generic.js")
        tab.evaluate("NovelDBGeneric.init({sites:[],comic:[],lnreader:[]})")
        yield tab


def order(page):
    return page.locator("#grid .card").evaluate_all("cards=>cards.sort((a,b)=>Number(a.style.order)-Number(b.style.order)).map(card=>card.querySelector('.title').textContent)")


def test_sort_only_moves_index_cards_and_metadata_never_reshuffles(page):
    page.fill("#query", "Target")
    page.locator("#form").dispatch_event("submit")
    search_id = page.evaluate("calls.filter(m=>m.type==='genericSearch').pop().id")
    cards = []
    for i, indexed in enumerate([False, True, False, True]):
        card = dict(url=f"https://books.test/book/{i}", title=f"Target {'Index' if indexed else 'External'} {i}",
                    site="books.test", source="noveldb" if indexed else "site", likes=i, complete=True, verified=True, via="name")
        if indexed:
            card["noveldb"] = {"source": "npia", "id": str(i)}
        cards.append(card)
    page.evaluate("args=>NovelDBGeneric.add(args.cards,args.id)", {"cards": cards, "id": search_id})
    page.wait_for_function("document.querySelectorAll('#grid .card').length===4")
    initial = order(page)
    cards[3]["likes"] = 9999
    page.evaluate("card=>NovelDBGeneric.add([card])", cards[3])
    page.wait_for_timeout(3300)
    assert order(page) == initial
    before = page.evaluate("calls.filter(m=>m.type==='genericSearch').length")
    page.select_option("#sort", "likes")
    assert order(page) == [initial[0], initial[3], initial[2], initial[1]]
    assert page.evaluate("calls.filter(m=>m.type==='genericSearch').length") == before
    sort_request = page.evaluate("calls.filter(m=>m.type==='genericIndexSort').pop()")
    assert sort_request["queries"][0]["sort"] == "likes"
    page.evaluate("args=>NovelDBGeneric.fetched(args.id,{cards:args.cards,more:true})", {"id": sort_request["id"], "cards": [cards[3], cards[1]]})
    page.wait_for_timeout(100)
    assert order(page) == [initial[0], initial[3], initial[2], initial[1]]
    cards[1]["likes"] = 1000000
    page.evaluate("card=>NovelDBGeneric.add([card])", cards[1])
    page.wait_for_timeout(200)
    assert order(page) == [initial[0], initial[3], initial[2], initial[1]]


def test_back_closes_current_panel_then_reader_then_returns_to_root(page):
    page.click("#settingsButton")
    assert page.evaluate("NovelDBGeneric.back()")
    assert "open" not in (page.locator("#settingspanel").get_attribute("class") or "")
    page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/test.epub',title:'Book'})")
    page.evaluate("NovelDBGeneric.fetched(calls.filter(m=>m.type==='genericBook').pop().id,{chapters:[{name:'One'}]})")
    page.evaluate("NovelDBGeneric.fetched(calls.filter(m=>m.type==='genericChapter').pop().id,{html:'<p>Saved text</p>'})")
    page.wait_for_function("document.getElementById('flow').textContent.includes('Saved text')")
    page.click("#rToc")
    assert page.evaluate("NovelDBGeneric.back()")
    assert "open" in page.locator("#reader").get_attribute("class")
    assert "open" not in page.locator("#toc").get_attribute("class")
    assert page.evaluate("NovelDBGeneric.back()")
    assert "open" not in page.locator("#reader").get_attribute("class")
    assert not page.evaluate("NovelDBGeneric.back()")


def test_back_restores_previous_search_without_searching_again(page):
    for query in ("First target", "Second target"):
        page.fill("#query", query)
        page.locator("#form").dispatch_event("submit")
    searches = page.evaluate("calls.filter(m=>m.type==='genericSearch').length")
    assert page.evaluate("NovelDBGeneric.back()")
    assert page.input_value("#query") == "First target"
    assert page.evaluate("calls.filter(m=>m.type==='genericSearch').length") == searches
    assert page.evaluate("NovelDBGeneric.back()")
    assert page.input_value("#query") == ""
