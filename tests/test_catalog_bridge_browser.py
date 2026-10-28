"""The catalog bridge (catalog-bridge.js, shared with the mobile apps) in a real browser.

Every request is fulfilled by a local fixture page. The test plays the app: it reads the bridge's
messages and answers with `NovelDBApp.setMode`, the way `noveldb.qt.catalog.web.CatalogPage` does
(`sites.classify` decides what the page on screen is).
"""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from noveldb.core.catalog import sites  # noqa: E402
from noveldb.core.settings.model import SITE_ENGINES  # noqa: E402

BRIDGE = Path(__file__).resolve().parents[1] / "noveldb" / "qt" / "catalog" / "catalog-bridge.js"
# What the mobile and desktop hosts provide: a channel the bridge posts JSON text to.
STUB = "window.__messages = []; window.NovelDBAndroid = {postMessage: function (t) { window.__messages.push(JSON.parse(t)); }};"
PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<a id="out" href="https://other.test/book/1">Source page</a>
<a id="inside" href="/inner">Inside link</a>
<iframe id="frame" srcdoc="<p>embedded</p>"></iframe>
</body></html>"""
CART_LABEL = "\U0001F517 Download"
IN_CART = "✓ In cart"
NPIA_GLOBAL = "global." + "novel" + "pia.com"  # the real host, kept out of the source text


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

    def serve(route):
        parts = urlsplit(route.request.url)
        title = "Title of " + (parts.path.strip("/") or "home")
        route.fulfill(body=PAGE.format(title=title), content_type="text/html")

    tab.route("**/*", serve)
    tab.add_init_script(STUB)
    tab.add_init_script(BRIDGE.read_text(encoding="utf-8"))
    yield tab
    context.close()


def messages(page, kind=None):
    found = page.evaluate("window.__messages")
    return [m for m in found if kind is None or m["type"] == kind]


def wait_for_messages(page, kind, count):
    page.wait_for_function("([kind, n]) => window.__messages.filter(m => m.type === kind).length >= n", arg=[kind, count])
    return messages(page, kind)


def wait_for_location(page, url, title=None):
    """Waits until the last reported address is `url` (and its title `title`, when given); returns that message."""
    page.wait_for_function(
        "([url, title]) => { const l = window.__messages.filter(m => m.type === 'location').pop();"
        " return !!l && l.url === url && (title === null || l.title === title); }", arg=[url, title])
    return messages(page, "location")[-1]


def download_button(page):
    return page.locator(".noveldb-download")


def external_text(page):
    return page.locator("#out").inner_text()


def answer(page, enabled=SITE_ENGINES, cart_links=True):
    """What the app does for the page on screen: classify it and answer with setMode."""
    reported = messages(page, "location")[-1]
    mode = sites.classify(reported["url"], enabled, cart_links)
    download = None
    if mode.download:
        download = {"url": reported["url"], "title": sites.clean_page_title(reported["title"], reported["url"])}
    page.evaluate("state => NovelDBApp.setMode(state)", {"cartLinks": mode.cart_links, "download": download})
    return mode


def test_the_address_is_reported_on_start_and_on_every_change(page):
    page.goto("https://site.test/start")
    first = wait_for_location(page, "https://site.test/start", "Title of start")
    assert first["type"] == "location"
    assert messages(page)[0] == {"type": "ready"}

    page.evaluate("history.pushState({}, '', '/pushed')")
    wait_for_location(page, "https://site.test/pushed")
    page.evaluate("history.replaceState({}, '', '/replaced?q=1')")
    wait_for_location(page, "https://site.test/replaced?q=1")
    page.evaluate("location.hash = '#part'")
    wait_for_location(page, "https://site.test/replaced?q=1#part")
    page.evaluate("history.back()")  # popstate and hashchange back to the entry without the fragment
    wait_for_location(page, "https://site.test/replaced?q=1")
    found = messages(page, "location")
    assert [m["url"] for m in found if m["title"]][:1] == ["https://site.test/start"]
    assert all(a != b for a, b in zip(found, found[1:]))  # nothing is reported twice in a row


def test_a_change_that_skips_the_wrapped_history_methods_is_still_reported(page):
    """The desktop app runs the bridge in an isolated world, where the page's own pushState is not wrapped."""
    page.goto("https://site.test/start")
    wait_for_location(page, "https://site.test/start")
    page.evaluate("History.prototype.pushState.call(history, {}, '', '/unseen')")
    wait_for_location(page, "https://site.test/unseen")


def test_a_late_title_is_reported_again(page):
    page.goto("https://site.test/start")
    wait_for_location(page, "https://site.test/start", "Title of start")
    page.evaluate("document.title = 'Settled title'")
    last = wait_for_location(page, "https://site.test/start", "Settled title")
    assert last["url"] == "https://site.test/start"


def test_the_download_button_adds_and_removes_the_page(page):
    page.goto("https://site.test/novel/5")
    wait_for_location(page, "https://site.test/novel/5")
    assert download_button(page).count() == 0

    page.evaluate("NovelDBApp.setMode({cartLinks: false, download: {url: location.href, title: 'Five'}})")
    button = download_button(page)
    button.wait_for()
    assert button.inner_text() == CART_LABEL
    assert page.evaluate("getComputedStyle(document.querySelector('.noveldb-download')).position") == "fixed"

    button.click()
    added = wait_for_messages(page, "cartAdd", 1)[0]
    assert added["url"] == "https://site.test/novel/5" and added["title"] == "Five"
    assert button.inner_text() == IN_CART
    assert button.get_attribute("aria-pressed") == "true"

    button.click()  # again: removes it
    removed = wait_for_messages(page, "cartRemove", 1)[0]
    assert removed["url"] == "https://site.test/novel/5"
    assert button.inner_text() == CART_LABEL

    page.evaluate("NovelDBApp.setCart([location.href])")  # the app says it is in the cart
    page.wait_for_function("document.querySelector('.noveldb-download').textContent === '%s'" % IN_CART)
    page.evaluate("NovelDBApp.setMode({cartLinks: false, download: null})")
    page.wait_for_function("!document.querySelector('.noveldb-download')")


def test_the_button_does_not_outlive_its_address(page):
    page.goto("https://site.test/novel/5")
    wait_for_location(page, "https://site.test/novel/5")
    page.evaluate("NovelDBApp.setMode({download: {url: location.href, title: 'Five'}})")
    download_button(page).wait_for()

    page.evaluate("history.pushState({}, '', '/novel/6')")
    page.wait_for_function("!document.querySelector('.noveldb-download')")
    # A late answer for the old page must not bring the button back on the new one.
    page.evaluate("NovelDBApp.setMode({download: {url: 'https://site.test/novel/5', title: 'Five'}})")
    page.wait_for_timeout(300)
    assert download_button(page).count() == 0
    page.evaluate("NovelDBApp.setMode({download: {url: location.href, title: 'Six'}})")
    download_button(page).wait_for()
    download_button(page).click()
    assert wait_for_messages(page, "cartAdd", 1)[0]["url"] == "https://site.test/novel/6"


def test_links_are_renamed_only_when_the_app_says_so(page):
    page.goto("https://site.test/list")
    wait_for_location(page, "https://site.test/list")
    assert external_text(page) == "Source page"

    # NovelDB and supported sites: cartLinks off (with or without a download), nothing is renamed.
    page.evaluate("NovelDBApp.setMode({cartLinks: false})")
    page.wait_for_timeout(200)
    assert external_text(page) == "Source page"
    page.evaluate("NovelDBApp.setMode({cartLinks: false, download: null})")
    page.wait_for_timeout(200)
    assert external_text(page) == "Source page"
    page.evaluate("NovelDBApp.setMode({cartLinks: false, download: {url: location.href, title: 'x'}})")
    download_button(page).wait_for()
    assert external_text(page) == "Source page"

    # Other sites: the old setMode({cartLinks}) keeps working, and the original text comes back.
    page.evaluate("NovelDBApp.setMode({cartLinks: true})")
    page.wait_for_function("document.querySelector('#out').textContent === '%s'" % CART_LABEL)
    assert page.locator("#inside").inner_text() == "Inside link"  # same-origin links are never renamed
    assert download_button(page).count() == 0  # a setMode without download clears the button
    page.evaluate("NovelDBApp.setMode({cartLinks: false})")
    page.wait_for_function("document.querySelector('#out').textContent === 'Source page'")


def test_outside_links_are_handed_to_the_app(page):
    page.goto("https://site.test/list")
    wait_for_location(page, "https://site.test/list")
    page.locator("#out").click()
    opened = wait_for_messages(page, "open", 1)[0]
    assert opened["url"] == "https://other.test/book/1"
    assert page.url == "https://site.test/list"  # the bridge stopped the navigation


def test_a_supported_site_gets_the_button_on_its_novel_pages_only(page):
    """The app's decision for global npia: no button on the home page, one on /novel/<id>."""
    page.goto("https://%s/" % NPIA_GLOBAL)
    wait_for_location(page, "https://%s/" % NPIA_GLOBAL, "Title of home")
    mode = answer(page)
    assert mode.kind == sites.KIND_SUPPORTED and not mode.download and not mode.cart_links
    page.wait_for_timeout(300)
    assert download_button(page).count() == 0
    assert external_text(page) == "Source page"  # links are left alone on a supported site

    page.evaluate("history.pushState({}, '', '/novel/4321')")
    wait_for_location(page, "https://%s/novel/4321" % NPIA_GLOBAL)
    mode = answer(page)
    assert mode.kind == sites.KIND_SUPPORTED and mode.download
    button = download_button(page)
    button.wait_for()
    button.click()
    added = wait_for_messages(page, "cartAdd", 1)[0]
    assert added["url"] == "https://%s/novel/4321" % NPIA_GLOBAL
    assert added["title"] == "Title of home"  # the pushed page has not changed its title
    assert external_text(page) == "Source page"

    page.evaluate("history.pushState({}, '', '/ranking')")  # a page that cannot be downloaded
    wait_for_location(page, "https://%s/ranking" % NPIA_GLOBAL)
    answer(page)
    page.wait_for_function("!document.querySelector('.noveldb-download')")


def test_noveldb_never_gets_the_button_or_renamed_links(page):
    for address in ("https://noveldb.pages.dev/", "https://shirochi-stack.github.io/NovelDB/"):
        page.goto(address)
        wait_for_location(page, address)
        mode = answer(page)
        assert mode.kind == sites.KIND_NOVELDB and not mode.cart_links and not mode.download
        page.wait_for_timeout(300)
        assert download_button(page).count() == 0
        assert external_text(page) == "Source page"


def test_other_sites_keep_renaming_their_outside_links(page):
    page.goto("https://elsewhere.test/")
    wait_for_location(page, "https://elsewhere.test/")
    assert answer(page, cart_links=True).cart_links
    page.wait_for_function("document.querySelector('#out').textContent === '%s'" % CART_LABEL)
    assert not answer(page, cart_links=False).cart_links
    page.wait_for_function("document.querySelector('#out').textContent === 'Source page'")


def test_embedded_frames_are_left_alone(page):
    page.goto("https://site.test/start")
    wait_for_location(page, "https://site.test/start")
    page.frame_locator("#frame").locator("p").wait_for()
    inner = [f for f in page.frames if f != page.main_frame][0]
    assert inner.evaluate("typeof window.NovelDBApp") == "undefined"
    assert inner.evaluate("window.__messages.length") == 0
    assert all(m["url"].startswith("https://") for m in messages(page, "location"))
