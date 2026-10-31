"""The WebToEpub bundle loads in Chromium and parses a book from a fixture site.

WEBTOEPUB_DIR points at another build (the update workflow checks its fresh bundle this way).
"""
import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = Path(os.environ.get("WEBTOEPUB_DIR") or ROOT / "webtoepub")
if not BUNDLE_DIR.is_absolute():
    BUNDLE_DIR = ROOT / BUNDLE_DIR
SITE = "https://novelfull.com"

BOOK = """<!doctype html><html lang="en"><head><title>Test Book</title></head><body>
<div class="book"><img src="/cover.jpg"></div>
<h3 class="title">The Fixture Story</h3>
<ul class="info-meta"><li><h3>Author:</h3><a href="/a">Ada Writer</a></li></ul>
<div class="desc-text">A story used by the tests.</div>
<ul class="list-chapter">
  <li><a href="/fixture/chapter-1.html">Chapter 1 - Start</a></li>
  <li><a href="/fixture/chapter-2.html">Chapter 2 - Next</a></li>
</ul></body></html>"""

CHAPTER = """<!doctype html><html><head><title>{title}</title></head><body>
<h2>{title}</h2>
<div id="chr-content">
  <h3>{heading}</h3>
  <p>First paragraph of {title}.</p>
  <p>Second&nbsp;paragraph<br>with a break.</p>
  <img data-src="/images/{n}.png" src="data:image/gif;base64,R0lGOD">
  <a href="/fixture/chapter-{next}.html">Next chapter</a>
</div></body></html>"""


def _bundle():
    return (BUNDLE_DIR / "webtoepub-lib.js").read_text(encoding="utf-8")


def _route(route):
    path = route.request.url[len(SITE):].split("?")[0]
    if path in ("/fixture.html", "/fixture/"):
        route.fulfill(body=BOOK, content_type="text/html; charset=utf-8")
    elif path.startswith("/fixture/chapter-"):
        n = int(path.rsplit("-", 1)[1].split(".")[0])
        title = ["Chapter 1 - Start", "Chapter 2 - Next"][n - 1]
        route.fulfill(body=CHAPTER.format(title=title, heading=title.replace(" - ", ": "), n=n, next=n + 1),
                      content_type="text/html; charset=utf-8")
    else:
        route.fulfill(status=404, body="")


def _page(p):
    browser = p.chromium.launch()
    page = browser.new_page()
    page.route(f"{SITE}/**", _route)
    return browser, page


def test_manifest_describes_the_bundle():
    manifest = json.loads((BUNDLE_DIR / "manifest.json").read_text(encoding="utf-8"))
    data = (BUNDLE_DIR / "webtoepub-lib.js").read_bytes()
    assert manifest["api"] == 1
    assert manifest["sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["size"] == len(data)
    assert len(manifest["commit"]) == 40
    assert (BUNDLE_DIR / "LICENSE.md").read_text(encoding="utf-8").startswith("GNU GENERAL PUBLIC LICENSE")


def test_bundle_parses_book_and_chapters():
    with sync_playwright() as p:
        browser, page = _page(p)
        try:
            page.goto(f"{SITE}/fixture.html")
            page.evaluate(_bundle())
            assert page.evaluate("window.__W2E_READY === true && !window.__W2E_LOAD_ERROR")
            info = json.loads(page.evaluate("window.__W2E_info()"))
            assert info["sites"] > 300

            book = json.loads(page.evaluate("window.__W2E_parseBook()"))
            assert "error" not in book, book
            assert book["parser"] == "NovelfullParser" and book["match"] == "site"
            assert book["bookname"] == "The Fixture Story"
            assert book["author"] == "Ada Writer"
            assert book["coverUrl"] == f"{SITE}/cover.jpg"
            assert [c["url"] for c in book["chapters"]] == [
                f"{SITE}/fixture/chapter-1.html", f"{SITE}/fixture/chapter-2.html"]
            assert book["chapters"][0]["name"] == "Chapter 1 - Start"

            batch = json.loads(page.evaluate("(items) => window.__W2E_parseChapterBatch(items)",
                                             json.dumps(book["chapters"])))
            first = json.loads(batch[0])
            assert "error" not in first, first
            assert first["chapterName"] == "Chapter 1 - Start"
            html = first["contentHtml"]
            # The heading repeating the title is dropped; the app writes the title itself.
            assert "<h3>" not in html
            # Lazy images become absolute URLs for the app's image downloader.
            assert f'src="{SITE}/images/1.png"' in html and "data-src" not in html
            # The link to the next chapter is removed, as in the extension.
            assert "Next chapter" not in html
            assert first["contentText"].splitlines() == [
                "First paragraph of Chapter 1 - Start.", "Second paragraph", "with a break."]
            assert json.loads(batch[1])["chapterName"] == "Chapter 2 - Next"
        finally:
            browser.close()


def test_site_without_a_parser_is_reported():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("https://example.org/**", lambda r: r.fulfill(
                body="<html><body><a href='/c1'>One</a><a href='/c2'>Two</a></body></html>",
                content_type="text/html"))
            page.goto("https://example.org/book")
            page.evaluate(_bundle())
            result = json.loads(page.evaluate("window.__W2E_parseBook()"))
            assert result.get("noParser") is True
        finally:
            browser.close()


def test_blocked_cross_site_requests_use_the_app_transport():
    with sync_playwright() as p:
        browser, page = _page(p)
        try:
            page.route("https://api.example.net/**", lambda r: r.abort())
            calls = []

            def transport(_source, details):
                calls.append(details)
                return {"status": 200, "statusText": "OK", "finalUrl": details["url"],
                        "responseHeaders": "Content-Type: application/json\r\nX-Test: 1",
                        "responseText": '{"ok": true}'}

            page.expose_binding("__npia_gm_xmlhttp_request", transport)
            page.goto(f"{SITE}/fixture.html")
            page.evaluate(_bundle())
            result = page.evaluate("""async () => {
                const first = await window.__W2E_fetch("https://api.example.net/data", {
                    method: "POST", body: new URLSearchParams({a: "1"}), headers: {"X-Key": "k"}});
                const second = await window.__W2E_fetch("https://api.example.net/more");
                const own = await window.__W2E_fetch("/fixture.html");
                return {json: await first.json(), url: first.url, header: first.headers.get("x-test"),
                        second: second.status, own: (await own.text()).includes("Fixture Story")};
            }""")
            assert result == {"json": {"ok": True}, "url": "https://api.example.net/data", "header": "1",
                              "second": 200, "own": True}
            assert calls[0]["method"] == "POST" and calls[0]["data"] == "a=1"
            assert calls[0]["headers"]["X-Key"] == "k"
            assert calls[0]["headers"]["Content-Type"].startswith("application/x-www-form-urlencoded")
            # The second request to the blocked site goes straight to the transport.
            assert [c["url"] for c in calls] == ["https://api.example.net/data", "https://api.example.net/more"]
        finally:
            browser.close()


def test_cookie_get_reads_through_the_app_transport():
    # A parser that reads an httpOnly login/anti-bot cookie (e.g. the sbxh
    # content path) calls chrome.cookies.get; it must reach the app's transport,
    # tagged as a cookie op, and get the cookie the browser holds.
    with sync_playwright() as p:
        browser, page = _page(p)
        try:
            calls = []

            def transport(_source, details):
                calls.append(details)
                if details.get("op") == "cookie":
                    return {"cookie": {"name": details["name"], "value": "cf-secret",
                                       "domain": "novelfull.com"}}
                return {"status": 404, "statusText": "", "responseText": ""}

            page.expose_binding("__npia_gm_xmlhttp_request", transport)
            page.goto(f"{SITE}/fixture.html")
            page.evaluate(_bundle())
            cookie = page.evaluate(
                "async () => await window.__W2E_cookie('https://novelfull.com/', 'cf_clearance')")
            assert cookie == {"name": "cf_clearance", "value": "cf-secret", "domain": "novelfull.com"}
            assert calls[-1]["op"] == "cookie" and calls[-1]["action"] == "get"
            assert calls[-1]["url"] == "https://novelfull.com/" and calls[-1]["name"] == "cf_clearance"
            # A missing cookie comes back as null, which parsers treat as "no cookie".
            none_result = page.evaluate(
                "async () => await window.__W2E_cookie('', 'x')")
            assert none_result is None
        finally:
            browser.close()
