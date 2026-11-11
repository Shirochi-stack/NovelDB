from external_scraper import ExternalScraper


CHAPTER = (
    "https://fuck\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/chapter.php?"
    "hash=c233a508413957e9c7942a6b157372e75659d7f7&ch=0001"
)


def test_cloudflare_target_is_not_accepted_until_real_page(monkeypatch):
    scraper = ExternalScraper()
    replies = iter([
        [{"type": "page", "url": CHAPTER, "title": "Just a moment...", "id": "a"}],
        [{"type": "page", "url": CHAPTER, "title": "Loading...", "id": "still-loading"}],
        [{"type": "page", "url": CHAPTER, "title": "Chapter 1", "id": "b"}],
    ])
    monkeypatch.setattr(scraper, "_fnp_cdp_targets", lambda _port: next(replies))
    monkeypatch.setattr("external_scraper.time.sleep", lambda _seconds: None)

    assert scraper._fnp_wait_for_verified_target(9222, CHAPTER, timeout=1) == "b"


def test_lnreader_fnp_chapter_uses_dedicated_reader(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {"_lnreader": True}
    scraper._stop_requested = False
    expected = {
        "chapterName": "Chapter 1",
        "contentHtml": "<p>Chapter text</p>",
        "contentText": "Chapter text",
    }
    calls = []
    monkeypatch.setattr(
        scraper,
        "_fnp_parse_chapter",
        lambda url, name: calls.append((url, name)) or expected,
    )
    monkeypatch.setattr(scraper, "_sleep_interval", lambda *_args: None)

    result = scraper.parse_chapter(0, {"url": CHAPTER, "name": "Chapter 1"})

    assert result == expected
    assert calls == [(CHAPTER, "Chapter 1")]


def test_fnp_installed_chrome_starts_hidden(monkeypatch):
    scraper = ExternalScraper()
    scraper._playwright = object()
    scraper._stop_requested = False
    launched = {}
    monkeypatch.setattr(scraper, "_chrome_remote_debugging_ports_using_profile", lambda _p: [])
    monkeypatch.setattr(scraper, "_wait_for_profile_processes_to_exit", lambda *_a, **_k: [])
    monkeypatch.setattr(
        scraper,
        "_open_system_chrome",
        lambda url, **options: launched.update(url=url, **options) or (object(), 9222),
    )
    monkeypatch.setattr(scraper, "_fnp_wait_for_verified_target", lambda *_a, **_k: "target")
    monkeypatch.setattr(scraper, "_fnp_attach_verified_chrome", lambda *_a: object())
    monkeypatch.setattr(scraper, "_park_chrome_windows_for_profile", lambda _p: None)

    assert scraper._fnp_launch_verified_chapter(CHAPTER) is not None
    assert launched["hidden"] is True
    assert "headless" not in launched


def test_fnp_batch_is_sequential_and_stops_after_abort(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {"_lnreader": True}
    scraper._stop_requested = False
    scraper.abort_reason = ""
    calls = []

    def read(url, name):
        calls.append((url, name))
        if len(calls) == 2:
            scraper.abort_reason = "blocked"
            return None
        return {"chapterName": name, "contentHtml": "<p>ok</p>", "contentText": "ok"}

    monkeypatch.setattr(scraper, "_fnp_parse_chapter", read)
    monkeypatch.setattr(scraper, "_sleep_interval", lambda *_args: None)
    chapters = [
        {"url": CHAPTER.replace("0001", f"{number:04d}"), "name": f"Chapter {number}"}
        for number in (1, 2, 3)
    ]

    results = scraper.parse_chapter_batch(chapters)

    assert len(calls) == 2
    assert results[0] is not None
    assert results[1:] == [None, None]
