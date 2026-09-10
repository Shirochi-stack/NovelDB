from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import external_scraper
from external_scraper import ExternalScraper


@pytest.fixture
def startup(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    page = Mock()
    page.url = "about:blank"
    page.content.return_value = "<html><body>Book index</body></html>"
    page.goto.return_value = SimpleNamespace(status=200)
    context = Mock(pages=[page])
    browser = Mock(contexts=[context])
    playwright = Mock()
    playwright.chromium.connect_over_cdp.return_value = browser
    monkeypatch.setattr(
        external_scraper, "sync_playwright",
        Mock(return_value=Mock(start=Mock(return_value=playwright))),
    )
    monkeypatch.setattr(scraper, "cleanup", Mock())
    monkeypatch.setattr(
        scraper, "_get_ntk_user_data_dir", Mock(return_value="saved-ntk-profile")
    )
    monkeypatch.setattr(scraper, "_close_ntk_profile_chrome", Mock(return_value=0))
    monkeypatch.setattr(scraper, "_hide_ntk_chrome_windows", Mock())
    monkeypatch.setattr(scraper, "_wait_for_cdp", Mock(return_value=True))
    launch = Mock(return_value=(Mock(), 9222))
    monkeypatch.setattr(scraper, "_open_system_chrome", launch)
    return scraper, page, context, launch


def test_ntk_startup_preserves_manual_login_session(startup):
    scraper, page, context, launch = startup

    assert scraper._start_ntk_browser("https://newtoki1.org/novel/58669/")

    launch.assert_called_once_with(
        "about:blank", remote_debugging=True,
        user_data_dir="saved-ntk-profile", headless=True,
    )
    context.clear_cookies.assert_not_called()
    context.add_cookies.assert_not_called()
    page.evaluate.assert_not_called()
    page.goto.assert_not_called()
    assert scraper._context is context


def test_ntk_refresh_navigates_to_book_once_after_startup(startup):
    scraper, page, context, launch = startup
    book_url = "https://sbxh9.com/novel/58410"

    assert scraper._ntk_refresh_cloudflare_session(book_url)

    assert launch.call_args.args[0] == "about:blank"
    page.goto.assert_called_once_with(
        book_url, wait_until="domcontentloaded", timeout=45000,
    )
    context.clear_cookies.assert_not_called()


def test_ntk_existing_browser_is_reused_without_clearing_session(startup):
    scraper, page, context, launch = startup
    book_url = "https://newtoki1.org/novel/58669/"
    assert scraper._start_ntk_browser(book_url)

    assert scraper._start_ntk_browser(book_url)

    launch.assert_called_once()
    page.evaluate.assert_called_once_with("1")
    context.clear_cookies.assert_not_called()
    page.goto.assert_not_called()


@pytest.mark.parametrize("status", [200, 403, 500])
def test_ntk_refresh_uses_site_router_only_for_forbidden_index(
    startup, monkeypatch, status,
):
    scraper, page, _context, _launch = startup
    book_url = "https://sbxh9.com/novel/58410"
    page.goto.return_value = SimpleNamespace(status=status)
    navigate = Mock(return_value=True)
    monkeypatch.setattr(scraper, "_ntk_navigate_site_route", navigate)

    assert scraper._ntk_refresh_cloudflare_session(book_url)

    if status == 403:
        navigate.assert_called_once_with(page, book_url)
        assert scraper._ntk_spa_origin == "https://sbxh9.com"
    else:
        navigate.assert_not_called()


@pytest.mark.parametrize("status", [403, 503])
def test_ntk_site_router_stops_when_homepage_is_blocked(startup, status):
    scraper, page, _context, _launch = startup
    page.evaluate.return_value = False
    page.goto.return_value = SimpleNamespace(status=status)

    assert not scraper._ntk_navigate_site_route(
        page, "https://sbxh9.com/novel/58410",
    )

    page.goto.assert_called_once_with(
        "https://sbxh9.com/", wait_until="domcontentloaded", timeout=45000,
    )
    page.wait_for_function.assert_not_called()
    assert page.evaluate.call_count == 1


def test_ntk_refresh_stops_and_dumps_when_site_navigation_cannot_start(
    startup, monkeypatch,
):
    scraper, page, _context, _launch = startup
    page.goto.return_value = SimpleNamespace(status=403)
    monkeypatch.setattr(scraper, "_ntk_navigate_site_route", Mock(return_value=False))
    dump = Mock()
    monkeypatch.setattr(scraper, "_ntk_dump_debug_page", dump)

    assert not scraper._ntk_refresh_cloudflare_session(
        "https://sbxh9.com/novel/58410",
    )

    dump.assert_called_once_with(page, "index_58410")
    page.wait_for_timeout.assert_not_called()


def test_ntk_site_router_changes_origin_before_routing(startup):
    scraper, page, _context, _launch = startup
    book_url = "https://sbxh9.com/novel/58410"
    page.url = "https://newtoki1.org/"
    # The origin/router readiness check rejects the old site's router.
    page.evaluate.side_effect = [False, True]
    operations = Mock()
    operations.attach_mock(page.evaluate, "evaluate")
    operations.attach_mock(page.goto, "goto")
    operations.attach_mock(page.wait_for_function, "wait_for_function")

    assert scraper._ntk_navigate_site_route(page, book_url)

    assert [call[0] for call in operations.mock_calls] == [
        "evaluate", "goto", "wait_for_function", "evaluate",
    ]
    assert page.evaluate.call_args_list[0].args[1] == "https://sbxh9.com"
    assert page.goto.call_args.args[0] == "https://sbxh9.com/"
    assert page.evaluate.call_args_list[1].args[1] == book_url


def test_ntk_site_router_reports_missing_router(startup):
    scraper, page, _context, _launch = startup
    page.evaluate.return_value = False
    page.wait_for_function.side_effect = RuntimeError("router did not load")

    assert not scraper._ntk_navigate_site_route(
        page, "https://sbxh9.com/novel/58410",
    )

    assert page.evaluate.call_count == 1
    page.wait_for_function.assert_called_once()


def test_ntk_site_router_reloads_homepage_before_retrying_same_chapter(startup):
    scraper, page, _context, _launch = startup
    chapter_url = "https://sbxh9.com/novel/58410/101"
    page.url = chapter_url + "/"
    page.evaluate.return_value = True

    assert scraper._ntk_navigate_site_route(page, chapter_url)

    page.goto.assert_called_once_with(
        "https://sbxh9.com/", wait_until="domcontentloaded", timeout=45000,
    )
    assert page.evaluate.call_args.args[1] == chapter_url


@pytest.mark.parametrize("result", [None, {"contentText": "Chapter fixture"}])
def test_ntk_single_retry_uses_same_frontend_as_batch(startup, monkeypatch, result):
    scraper, page, _context, _launch = startup
    scraper._page = page
    scraper._ntk_spa_origin = "https://sbxh9.com"
    chapter_url = "https://sbxh9.com/novel/58410/101"
    fetch_batch = Mock(return_value=[result])
    monkeypatch.setattr(scraper, "_ntk_fetch_chapter_batch_browser", fetch_batch)
    prepare_manual = Mock()
    monkeypatch.setattr(scraper, "_ntk_prepare_chapter_page_browser", prepare_manual)

    assert scraper._ntk_fetch_chapter_browser(chapter_url, "Episode 1") is result

    fetch_batch.assert_called_once_with(
        [{"url": chapter_url, "name": "Episode 1"}], interval=0,
    )
    prepare_manual.assert_not_called()
    page.evaluate.assert_not_called()


def test_ntk_spa_workers_route_independently_without_direct_deep_links(
    startup, monkeypatch,
):
    scraper, _page, _context, _launch = startup
    scraper._ntk_spa_origin = "https://sbxh9.com"
    pages = [Mock(), Mock()]
    handlers = {}
    for page in pages:
        page.on.side_effect = (
            lambda _event, handler, worker=page: handlers.__setitem__(worker, handler)
        )
    monkeypatch.setattr(scraper, "_ntk_parallel_pages", lambda _count: pages)
    chapters = [
        {"url": "https://sbxh9.com/novel/58410/101", "name": "Episode 1"},
        {"url": "https://sbxh9.com/novel/58410/202", "name": "Episode 2"},
    ]

    def navigate(page, _url):
        response = Mock(status=200)
        response.url = "https://sbxh9.com/api/novel-content"
        response.json.return_value = {"ok": True, "payload": "encrypted-fixture"}
        handlers[page](response)
        return True

    navigate_site = Mock(side_effect=navigate)
    monkeypatch.setattr(scraper, "_ntk_navigate_site_route", navigate_site)
    monkeypatch.setattr(
        scraper, "_ntk_decrypt_payload_browser",
        lambda _page, url, _payload: {
            "ok": True,
            "plaintext": "This complete chapter contains sufficient content. " + url,
        },
    )

    results = scraper._ntk_fetch_chapter_batch_browser(chapters, interval=0)

    assert [call.args for call in navigate_site.call_args_list] == [
        (pages[0], chapters[0]["url"]), (pages[1], chapters[1]["url"]),
    ]
    assert [result["chapterName"] for result in results] == ["Episode 1", "Episode 2"]
    for page in pages:
        page.goto.assert_not_called()
        page.remove_listener.assert_called_once_with("response", handlers[page])


@pytest.mark.parametrize("status", [401, 403])
def test_ntk_blocked_chapter_document_finishes_without_content_timeout(
    startup, monkeypatch, status,
):
    scraper, page, _context, _launch = startup
    messages = []
    scraper._raw_log = messages.append
    chapter = {"url": "https://sbxh9.com/novel/58410/101", "name": "Episode 1"}
    handlers = []
    page.on.side_effect = lambda _event, handler: handlers.append(handler)
    response = SimpleNamespace(
        status=status, url=chapter["url"],
        request=SimpleNamespace(resource_type="document", frame=page.main_frame),
    )
    page.goto.side_effect = lambda *_args, **_kwargs: handlers[0](response)
    monkeypatch.setattr(scraper, "_ntk_parallel_pages", lambda _count: [page])

    assert scraper._ntk_fetch_chapter_batch_browser([chapter], interval=0) == [None]

    assert any(f"Chapter page access denied (HTTP {status})" in m for m in messages)
    assert not any("Timed out" in message for message in messages)
    page.wait_for_timeout.assert_not_called()


@pytest.mark.parametrize("blocked_kind", ["image", "other_document", "iframe"])
def test_ntk_unrelated_forbidden_response_does_not_fail_chapter(
    startup, monkeypatch, blocked_kind,
):
    scraper, page, _context, _launch = startup
    messages = []
    scraper._raw_log = messages.append
    chapter = {"url": "https://sbxh9.com/novel/58410/101", "name": "Episode 1"}
    handlers = []
    page.on.side_effect = lambda _event, handler: handlers.append(handler)
    unrelated = SimpleNamespace(
        status=403,
        url=("https://sbxh9.com/ad.html"
             if blocked_kind == "other_document" else chapter["url"]),
        request=SimpleNamespace(
            resource_type="image" if blocked_kind == "image" else "document",
            frame=object() if blocked_kind == "iframe" else page.main_frame,
        ),
    )
    content = Mock(status=200)
    content.url = "https://sbxh9.com/api/novel-content"
    content.json.return_value = {"ok": True, "payload": "encrypted-fixture"}

    def navigate(*_args, **_kwargs):
        handlers[0](unrelated)
        handlers[0](content)

    page.goto.side_effect = navigate
    monkeypatch.setattr(scraper, "_ntk_parallel_pages", lambda _count: [page])
    monkeypatch.setattr(
        scraper, "_ntk_decrypt_payload_browser",
        lambda *_args: {
            "ok": True,
            "plaintext": "This chapter succeeds despite an unrelated denied response.",
        },
    )

    results = scraper._ntk_fetch_chapter_batch_browser([chapter], interval=0)

    assert results[0]["chapterName"] == "Episode 1"
    assert not any("access denied" in message for message in messages)
