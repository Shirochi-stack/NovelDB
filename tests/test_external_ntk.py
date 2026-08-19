import base64
import json

import pytest
from playwright.sync_api import sync_playwright

from external_scraper import ExternalScraper


INDEX_HTML = """
<html>
  <head>
    <meta property="og:title" content="Browser Routed Novel" />
    <meta name="description" content="A test novel" />
  </head>
  <body>
    <ul class="novel-eps">
      <li data-ep="1">
        <a href="/novel/58669/5862851">
          <span class="ne-title">Episode One</span>
        </a>
      </li>
    </ul>
  </body>
</html>
"""


@pytest.mark.parametrize(
    "url",
    [
        "https://ntk1.com/novel/123",
        "https://www.ntk42.com/webtoon/456/789",
        "https://ntk1.org/novel/123",
        "https://www.ntk42.org/webtoon/456/789",
        "https://newtoki1.org/novel/58669",
        "https://www.newtoki42.com/webtoon/456/789",
    ],
)
def test_ntk_url_detection_accepts_numbered_ntk_and_newtoki_domains(url):
    assert ExternalScraper.is_ntk_novel(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://ntk.org/novel/123",
        "https://ntk1.net/novel/123",
        "https://newtoki.org/novel/58669",
        "https://newtoki1.net/novel/58669",
        "https://example.org/novel/123",
        "https://ntk1.org/not-a-novel/123",
    ],
)
def test_ntk_url_detection_rejects_unsupported_domains_and_paths(url):
    assert not ExternalScraper.is_ntk_novel(url)


class IndexPage:
    def wait_for_function(self, _script, arguments, timeout):
        assert arguments == {"novelId": "58669", "kind": "novel"}
        assert timeout == 30000

    def evaluate(self, _script, arguments):
        assert arguments == {"novelId": "58669", "kind": "novel"}
        return {
            "currentUrl": "https://newtoki1.org/novel/58669",
            "title": "Browser Routed Novel",
            "author": "Test Author",
            "introduction": "A test novel",
            "introductionHtml": "<p>A test novel</p>",
            "coverUrl": "https://newtoki1.org/cover.jpg",
            "tags": ["Fantasy"],
            "chapters": [
                {
                    "url": "https://newtoki1.org/novel/58669/5862851",
                    "episodeId": "5862851",
                    "name": "Episode One",
                    "number": 1,
                }
            ],
            "bodyText": "Browser Routed Novel Episode One",
            "html": INDEX_HTML,
        }

    def content(self):
        return INDEX_HTML


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


def test_ntk_book_parse_uses_headless_dom_without_direct_http(monkeypatch):
    scraper, _messages = make_scraper()
    scraper._page = IndexPage()
    scraper._context = object()
    scraper._ntk_temp_chrome = True

    monkeypatch.setattr(
        scraper, "_ntk_refresh_cloudflare_session", lambda _url: True
    )
    monkeypatch.setattr(
        scraper,
        "_ntk_prepare_api_state",
        lambda *_args: pytest.fail("direct HTTP state must not be created"),
    )
    monkeypatch.setattr(
        scraper,
        "_ntk_fetch_index_via_api_session",
        lambda *_args: pytest.fail("direct HTTP index must not be fetched"),
    )

    book = scraper._ntk_parse_book(
        "https://newtoki1.org/novel/58669"
    )

    assert book["bookname"] == "Browser Routed Novel"
    assert book["chapters"][0]["url"] == (
        "https://newtoki1.org/novel/58669/5862851"
    )
    assert scraper._ntk_browser_fallback is True
    assert book["_ntk_headless_browser"] is True
    assert book["_ntk_api"] is False


class ChapterPage:
    def evaluate(self, _script, arguments):
        assert arguments["chapterUrl"].endswith("/5862851")
        return {
            "ok": True,
            "plaintext": (
                "First paragraph with enough text for validation.\n\n"
                "Second paragraph also contains useful chapter text."
            ),
        }


def test_ntk_browser_chapter_fetch_builds_download_result():
    scraper, _messages = make_scraper()
    scraper._page = ChapterPage()

    chapter = scraper._ntk_fetch_chapter_browser(
        "https://newtoki1.org/novel/58669/5862851",
        "Episode One",
    )

    assert chapter["chapterName"] == "Episode One"
    assert "First paragraph" in chapter["contentText"]
    assert "<p>Second paragraph" in chapter["contentHtml"]
    assert chapter["_debugSelector"] == "ntk browser api/novel-content"


def test_ntk_browser_fallback_batch_avoids_direct_http_workers(monkeypatch):
    scraper, _messages = make_scraper()
    scraper._ntk_browser_fallback = True
    scraper._book_data = {"_ntk_novel": True, "_ntk_kind": "novel"}
    fetched = []

    def fetch_browser(url, name):
        fetched.append((url, name))
        return {"chapterName": name, "contentText": "content"}

    monkeypatch.setattr(scraper, "_ntk_fetch_chapter_browser", fetch_browser)
    monkeypatch.setattr(
        scraper,
        "_ntk_clone_api_state",
        lambda: pytest.fail("direct HTTP worker should not be used"),
    )

    chapters = [
        {
            "url": "https://newtoki1.org/novel/58669/1",
            "name": "One",
        },
        {
            "url": "https://newtoki1.org/novel/58669/2",
            "name": "Two",
        },
    ]
    results = scraper.parse_chapter_batch(chapters, interval=0)

    assert [result["chapterName"] for result in results] == ["One", "Two"]
    assert fetched == [(item["url"], item["name"]) for item in chapters]


def test_ntk_real_headless_browser_extracts_index_and_decrypts_chapter():
    key = b"headless-browser-key-123"
    plaintext = (
        "The headless browser fetched and decrypted this chapter correctly. "
        "It contains enough text to pass the scraper validation."
    ).encode("utf-8")
    encrypted = bytes(
        value ^ key[index % len(key)]
        for index, value in enumerate(plaintext)
    )
    encrypted_payload = base64.urlsafe_b64encode(encrypted).decode().rstrip("=")
    nv_cookie = (
        base64.urlsafe_b64encode(key).decode().rstrip("=") + ".session"
    )
    api_requests = []
    serve_rendered_chapter = [True]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies([{
            "name": "nv",
            "value": nv_cookie,
            "domain": "newtoki1.org",
            "path": "/",
        }])
        page = context.new_page()

        def handle(route):
            request = route.request
            if "/api/novel-content" in request.url:
                api_requests.append({
                    "body": request.post_data_json,
                    "headers": request.headers,
                })
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "payload": encrypted_payload,
                    }),
                )
            elif "/novel/58669/5862851" in request.url:
                if serve_rendered_chapter[0]:
                    route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=(
                            '<main><div class="viewer-content">'
                            '<p>The browser rendered the first paragraph with '
                            'enough text for extraction.</p>'
                            '<p>The browser rendered the second paragraph '
                            'without using the private API fallback.</p>'
                            '</div></main>'
                        ),
                    )
                    return
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=(
                        '<script>window.chapter={"token":"test-token",'
                        '"cookieName":"nv"}</script>'
                    ),
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=INDEX_HTML,
                )

        page.route("https://newtoki1.org/**", handle)
        page.goto("https://newtoki1.org/novel/58669")

        scraper, _messages = make_scraper()
        scraper._page = page
        book = scraper._ntk_parse_index_browser(
            "https://newtoki1.org/novel/58669"
        )
        rendered_chapter = scraper._ntk_fetch_chapter_browser(
            book["chapters"][0]["url"],
            book["chapters"][0]["name"],
        )
        assert "first paragraph" in rendered_chapter["contentText"]
        assert api_requests == []

        serve_rendered_chapter[0] = False
        api_chapter = scraper._ntk_fetch_chapter_browser(
            book["chapters"][0]["url"],
            book["chapters"][0]["name"],
        )

        assert book["bookname"] == "Browser Routed Novel"
        assert book["chapterCount"] == 1
        assert api_chapter["contentText"] == plaintext.decode("utf-8")
        assert api_requests[0]["body"]["novelId"] == "58669"
        assert api_requests[0]["body"]["episodeId"] == "5862851"
        assert api_requests[0]["headers"]["x-novel-client"] == "shadow-v2"

        browser.close()
