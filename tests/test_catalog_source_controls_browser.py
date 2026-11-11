"""Catalogue source controls through the shared desktop and mobile pages."""
from pathlib import Path

import pytest

from tests.test_generic_page_merge_browser import browser

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "noveldb/qt/catalog/generic", ROOT / "android/app/src/main/assets/catalog",
           ROOT / "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog"]


@pytest.fixture(params=TARGETS, ids=["desktop", "android", "ios"])
def catalogue(browser, request):
    with browser.new_context(viewport={"width": 390, "height": 844}) as context:
        page = context.new_page()
        html = (request.param / "index.html").read_text(encoding="utf-8")
        page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html")
                   if route.request.url == "http://catalog-controls.test/" else route.fulfill(body=""))
        page.goto("http://catalog-controls.test/")
        page.evaluate("""() => {
            window.__sent = [];
            window.NovelDBQt = {postMessage: value => window.__sent.push(JSON.parse(value))};
        }""")
        page.add_script_tag(path=request.param / "generic.js")
        page.evaluate("""() => {
            window.NovelDBGeneric.init({sites:['novels.test','novelmirror.test'],comic:[],lnreader:[]});
            const card = {title:'Source Book', tags:['Action'], description:'A novel about adventures in another world.',
                source:'site', comic:false, via:'top', verified:true};
            NovelDBGeneric.add([
                {...card,url:'https://novels.test/book/1',site:'novels.test'},
                {...card,url:'https://novelmirror.test/book/1',site:'novelmirror.test'}
            ]);
        }""")
        page.wait_for_selector("#grid .card .title")
        yield page


def test_cart_controls_use_the_selected_source_url(catalogue):
    page = catalogue
    page.locator("#grid .card").click()
    rows = page.locator("#dialog .sources > div")
    assert rows.count() == 2
    second = rows.filter(has_text="novelmirror.test").get_by_role("button", name="Add to cart")
    second.click()
    request = page.evaluate("__sent.filter(m=>m.type==='cartAdd').at(-1)")
    assert request["url"] == "https://novelmirror.test/book/1"
    page.evaluate("url=>NovelDBGeneric.setCart([url])", request["url"])
    assert rows.filter(has_text="novelmirror.test").get_by_role("button", name="In cart").is_visible()
    assert rows.filter(has_text="novels.test").get_by_role("button", name="Add to cart").is_visible()
    rows.filter(has_text="novelmirror.test").get_by_role("button", name="In cart").click()
    assert page.evaluate("__sent.filter(m=>m.type==='cartRemove').at(-1).url") == request["url"]


def test_full_search_is_optional_persisted_and_settings_fit_phone(catalogue):
    page = catalogue
    page.click("#settingsButton")
    assert not page.is_checked("#useFull")
    page.check("#useFull")
    assert page.evaluate("JSON.parse(localStorage.getItem('noveldb.generic.searches')).full")
    labels = page.locator("#settingspanel .checks").inner_text()
    assert "Index" in labels and "Native" in labels and "Full search" in labels
    bounds = page.locator("#settingspanel").bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
