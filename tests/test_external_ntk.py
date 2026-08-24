import base64
import hashlib
import hmac
import json
import os
import time

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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
        "https://sbxh9.com/novel/58669",
        "https://www.sbxh12.org/webtoon/456/789",
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


def test_ntk_browser_index_reverses_newest_first_chapters():
    scraper, _messages = make_scraper()
    scraper._page = IndexPage()
    original_evaluate = scraper._page.evaluate

    def newest_first_index(script, arguments):
        result = original_evaluate(script, arguments)
        result["chapters"] = [
            {
                "url": "https://newtoki1.org/novel/58669/30",
                "episodeId": "30",
                "name": "Episode 3",
            },
            {
                "url": "https://newtoki1.org/novel/58669/20",
                "episodeId": "20",
                "name": "Episode 2",
            },
            {
                "url": "https://newtoki1.org/novel/58669/10",
                "episodeId": "10",
                "name": "Episode 1",
            },
        ]
        return result

    scraper._page.evaluate = newest_first_index

    book = scraper._ntk_parse_index_browser(
        "https://newtoki1.org/novel/58669"
    )

    assert [chapter["name"] for chapter in book["chapters"]] == [
        "Episode 1",
        "Episode 2",
        "Episode 3",
    ]
    assert [chapter["number"] for chapter in book["chapters"]] == [1, 2, 3]


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
    scraper, messages = make_scraper()
    scraper._page = ChapterPage()

    chapter = scraper._ntk_fetch_chapter_browser(
        "https://newtoki1.org/novel/58669/5862851",
        "Episode One",
    )

    assert chapter["chapterName"] == "Episode One"
    assert "First paragraph" in chapter["contentText"]
    assert "<p>Second paragraph" in chapter["contentHtml"]
    assert chapter["_debugSelector"] == "ntk browser api/novel-content"
    assert not any("Fetching encrypted chapter content" in line for line in messages)


def test_ntk_browser_chapter_fetch_never_scrapes_rendered_login_ui():
    scraper, _messages = make_scraper()
    scraper._page = ChapterPage()
    scraper._ntk_fetch_rendered_chapter_browser = lambda *_args: pytest.fail(
        "text novels must not scrape the rendered page shell"
    )

    chapter = scraper._ntk_fetch_chapter_browser(
        "https://newtoki1.org/novel/58669/5862851",
        "Episode One",
    )

    assert "First paragraph" in chapter["contentText"]
    assert chapter["_debugSelector"] == "ntk browser api/novel-content"


def test_ntk_decrypted_chapter_unshuffles_v3_paragraphs():
    scraper, _messages = make_scraper()

    chapter = scraper._ntk_build_decrypted_chapter(
        "Episode One",
        json.dumps({
            "kind": "text-shuffled",
            "paragraphs": ["Third paragraph", "First paragraph", "Second paragraph"],
            "perm": [2, 0, 1],
        }),
        "v3 fixture",
    )

    assert chapter["contentText"] == (
        "First paragraph\n\nSecond paragraph\n\nThird paragraph"
    )


def test_ntk_browser_fallback_batch_avoids_direct_http_workers(monkeypatch):
    scraper, _messages = make_scraper()
    scraper._ntk_browser_fallback = True
    scraper._book_data = {"_ntk_novel": True, "_ntk_kind": "novel"}
    fetched = []

    def fetch_browser_batch(chapters, interval, success_callback=None):
        fetched.extend((item["url"], item["name"]) for item in chapters)
        results = [
            {"chapterName": item["name"], "contentText": "content"}
            for item in chapters
        ]
        if success_callback is not None:
            for index, result in enumerate(results):
                success_callback(index, result)
        return results

    monkeypatch.setattr(
        scraper,
        "_ntk_fetch_chapter_batch_browser",
        fetch_browser_batch,
    )
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


def test_ntk_browser_batch_uses_independent_pages_and_exact_interval(monkeypatch):
    scraper, _messages = make_scraper()

    class Response:
        url = "https://newtoki1.org/api/novel-content"
        status = 200

        @staticmethod
        def json():
            return {"ok": True, "payload": "encrypted-fixture"}

    class Page:
        def __init__(self):
            self.handlers = []
            self.started_at = None
            self.loaded_url = None

        def on(self, event, handler):
            assert event == "response"
            self.handlers.append(handler)

        def remove_listener(self, event, handler):
            assert event == "response"
            self.handlers.remove(handler)

        def goto(self, url, **_kwargs):
            self.loaded_url = url
            self.started_at = time.perf_counter()
            for handler in list(self.handlers):
                handler(Response())

        @staticmethod
        def wait_for_timeout(milliseconds):
            time.sleep(milliseconds / 1000)

    pages = [Page(), Page(), Page()]
    monkeypatch.setattr(scraper, "_ntk_parallel_pages", lambda count: pages[:count])
    monkeypatch.setattr(
        scraper,
        "_ntk_decrypt_payload_browser",
        lambda _page, url, _payload: {
            "ok": True,
            "plaintext": (
                "Parallel chapter content is long enough for validation. " + url
            ),
        },
    )
    chapters = [
        {
            "url": f"https://newtoki1.org/novel/58669/{index}",
            "name": f"Episode {index}",
        }
        for index in range(1, 4)
    ]

    completed = []
    results = scraper._ntk_fetch_chapter_batch_browser(
        chapters,
        interval=0.02,
        success_callback=lambda index, _result: completed.append(
            (index, time.perf_counter())
        ),
    )

    assert [page.loaded_url for page in pages] == [
        chapter["url"] for chapter in chapters
    ]
    assert pages[1].started_at - pages[0].started_at >= 0.015
    assert pages[2].started_at - pages[1].started_at >= 0.015
    assert [index for index, _finished_at in completed] == [0, 1, 2]
    assert completed[0][1] <= pages[1].started_at
    assert completed[1][1] <= pages[2].started_at
    assert [result["chapterName"] for result in results] == [
        chapter["name"] for chapter in chapters
    ]


def test_ntk_real_headless_browser_extracts_index_and_decrypts_chapter():
    cookie_key = b"headless-browser-key-123"
    stale_novel_token = "eyJuIjoi-stale-novel-token"
    novel_token = "eyJuIjoi-test-novel-token"
    challenge_token = "eyJ2Ijoy-test-ad-challenge"
    challenge_scope = "signed-page-issued-ad-scope"
    plaintext = (
        "The headless browser fetched and decrypted this chapter correctly. "
        "It contains enough text to pass the scraper validation."
    ).encode("utf-8")
    aes_key = hashlib.sha256(
        cookie_key + b":58669:5862851:v3"
    ).digest()
    iv = os.urandom(12)
    encrypted = iv + AESGCM(aes_key).encrypt(iv, plaintext, None)
    encrypted_payload = base64.urlsafe_b64encode(encrypted).decode().rstrip("=")
    nv_cookie = (
        base64.urlsafe_b64encode(cookie_key).decode().rstrip("=") + ".session"
    )
    api_requests = []
    event_sync_requests = []
    challenge_requests = []
    canary_requests = []
    ack_requests = []
    chapter_requests = []
    ack_completed = False

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
            nonlocal ack_completed
            request = route.request
            if "/api/novel-content" in request.url:
                api_requests.append({
                    "body": request.post_data_json,
                    "headers": request.headers,
                })
                if request.post_data_json["token"] != novel_token:
                    route.fulfill(
                        status=403,
                        content_type="application/json",
                        body=json.dumps({
                            "ok": False,
                            "error": "ad_ack_required",
                        }),
                    )
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "payload": encrypted_payload,
                    }),
                )
            elif "/api/ev/sync" in request.url:
                event_sync_requests.append(request.post_data_json)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True}),
                )
            elif "/api/ad/challenge" in request.url:
                challenge_requests.append(request.post_data_json)
                if request.post_data_json.get("scope") != challenge_scope:
                    route.fulfill(
                        status=400,
                        content_type="application/json",
                        body=json.dumps({
                            "ok": False,
                            "error": "bad_scope",
                        }),
                    )
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "challenge": {
                            "token": challenge_token,
                            "slotNonces": ["slot-a", "slot-b"],
                        },
                    }),
                )
            elif "/api/ad/canary" in request.url:
                canary_requests.append(request.post_data_json)
                if request.post_data_json.get("scope") != challenge_scope:
                    route.fulfill(
                        status=400,
                        content_type="application/json",
                        body=json.dumps({
                            "ok": False,
                            "error": "bad_scope",
                        }),
                    )
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True}),
                )
            elif "/api/ad/ack" in request.url:
                ack_requests.append(request.post_data_json)
                if request.post_data_json.get("scope") != challenge_scope:
                    route.fulfill(
                        status=400,
                        content_type="application/json",
                        body=json.dumps({
                            "ok": False,
                            "error": "bad_scope",
                        }),
                    )
                    return
                ack_completed = True
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True}),
                )
            elif "/api/me" in request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True}),
                )
            elif "/novel/58669/5862851" in request.url:
                chapter_requests.append(request.url)
                active_token = (
                    novel_token if ack_completed else stale_novel_token
                )
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=(
                        '<main><form class="login-overlay">'
                        '<p>Login</p><p>User ID</p><p>Password</p>'
                        '<img src="/decorative-login-image.jpg">'
                        '</form></main>'
                        '<div data-br-n="2"></div>'
                        '<script>window.chapter={"token":"'
                        + active_token
                        + '","cookieName":"nv","adScope":"'
                        + challenge_scope
                        + '"}</script>'
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
        user_agent = page.evaluate("navigator.userAgent")

        scraper, _messages = make_scraper()
        scraper._page = page
        scraper._ntk_prepare_chapter_page_browser = lambda _url: True
        book = scraper._ntk_parse_index_browser(
            "https://newtoki1.org/novel/58669"
        )
        api_chapter = scraper._ntk_fetch_chapter_browser(
            book["chapters"][0]["url"],
            book["chapters"][0]["name"],
        )

        assert book["bookname"] == "Browser Routed Novel"
        assert book["chapterCount"] == 1
        assert api_chapter["contentText"] == plaintext.decode("utf-8")
        assert len(chapter_requests) == 2
        assert len(api_requests) == 1
        final_request = api_requests[-1]
        assert final_request["body"]["novelId"] == "58669"
        assert final_request["body"]["episodeId"] == "5862851"
        assert final_request["body"]["token"] == novel_token
        assert final_request["headers"]["x-novel-client"] == "shadow-v3"
        proof_message = (
            f"{novel_token}.{final_request['body']['nonce']}.{user_agent}"
        ).encode()
        expected_proof = base64.urlsafe_b64encode(
            hmac.new(nv_cookie.encode(), proof_message, hashlib.sha256).digest()
        ).decode().rstrip("=")
        assert final_request["body"]["proof"] == expected_proof
        assert event_sync_requests and event_sync_requests[0]["evId"]
        expected_challenge = {
            "scope": challenge_scope,
            "path": "/novel/58669/5862851",
        }
        assert challenge_requests == [
            {
                "path": "/novel/58669/5862851",
                "force": False,
            },
            expected_challenge,
        ]
        expected_canary = {
            "adAckCanary": True,
            "challengeToken": challenge_token,
            "scope": challenge_scope,
            "path": "/novel/58669/5862851",
        }
        assert canary_requests == [expected_canary]
        expected_ack = {
            "challengeToken": challenge_token,
            "scope": challenge_scope,
            "total": 2,
            "visible": 2,
            "path": "/novel/58669/5862851",
            "slotNonces": ["slot-a", "slot-b"],
        }
        assert ack_requests == [expected_ack]

        browser.close()


def test_ntk_real_headless_browser_fetches_wildcard_cors_cover_without_cookies():
    cover_bytes = b"\xff\xd8\xff\xe0headless-cover-fixture"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def handle(route):
            if route.request.url == "https://aws-cdn9.site/cover.jpg":
                route.fulfill(
                    status=200,
                    headers={
                        "access-control-allow-origin": "*",
                        "content-type": "image/jpeg",
                    },
                    body=cover_bytes,
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body>NewToki index</body></html>",
                )

        page.route("**/*", handle)
        page.goto("https://newtoki1.org/novel/58669")

        scraper, messages = make_scraper()
        scraper._page = page
        result = scraper._ntk_fetch_binary_browser(
            "https://aws-cdn9.site/cover.jpg"
        )

        assert result == cover_bytes
        assert not any("asset fetch failed" in line for line in messages)
        browser.close()


def test_sbxh_real_headless_browser_collects_all_paginated_chapters():
    def index_html(first, last, next_page=None):
        rows = "".join(
            (
                f'<li data-ep="{number}"><a '
                f'href="/novel/58410/{100000 + number}">'
                f'<span class="ne-title">Episode {number}</span>'
                '</a></li>'
            )
            for number in range(first, last - 1, -1)
        )
        pagination = (
            f'<a class="pagination" href="?page={next_page}">Older</a>'
            if next_page else ''
        )
        return (
            '<html><head><meta property="og:title" '
            'content="Paginated SBXH Novel"></head><body>'
            '<div class="novel-detail"><h1>Paginated SBXH Novel</h1></div>'
            '<nav><a href="/novel/58410/900001">1화부터 보기 →</a>'
            '<a href="/novel/58410/900002">최신화부터 →</a></nav>'
            f'<ul class="novel-eps">{rows}</ul>{pagination}</body></html>'
        )

    newest_page = index_html(130, 69, next_page=2)
    oldest_page = index_html(68, 1)
    requested_urls = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def handle(route):
            requested_urls.append(route.request.url)
            body = oldest_page if "page=2" in route.request.url else newest_page
            route.fulfill(status=200, content_type="text/html", body=body)

        page.route("https://sbxh9.com/**", handle)
        page.goto("https://sbxh9.com/novel/58410")

        scraper, messages = make_scraper()
        scraper._page = page
        book = scraper._ntk_parse_index_browser(
            "https://sbxh9.com/novel/58410"
        )

        assert book["chapterCount"] == 130
        assert book["chapters"][0]["name"] == "Episode 1"
        assert book["chapters"][-1]["name"] == "Episode 130"
        assert [chapter["number"] for chapter in book["chapters"]] == list(
            range(1, 131)
        )
        assert any("page=2" in url for url in requested_urls)
        assert any(
            "Expanded chapter index from 62 to 130" in line
            for line in messages
        )
        browser.close()
