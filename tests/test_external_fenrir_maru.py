import requests
import pytest

from external_scraper import ExternalScraper


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


def test_fenrir_url_detection():
    assert ExternalScraper.is_fenrir(
        "https://fenrirealm.com/series/absolute-regression"
    )
    assert ExternalScraper.is_fenrir(
        "https://www.fenrirealm.com/series/absolute-regression/12"
    )
    assert ExternalScraper._fenrir_url_parts(
        "https://fenrirealm.com/series/absolute-regression/12.1"
    ) == ("absolute-regression", "12.1")
    assert not ExternalScraper.is_fenrir("https://fenrirealm.com/")
    assert not ExternalScraper.is_fenrir(
        "https://example.com/series/absolute-regression"
    )


def test_fenrir_clean_html_drops_hidden_junk_and_invisible_chars():
    content = (
        "<style>.c1a2{position:absolute;width:1px;height:1px;"
        "overflow:hidden;clip:rect(0,0,0,0);clip-path:inset(50%)}</style>"
        '<div class="c1a2" aria-hidden="true">e3y1J9p2d7idI967</div>'
        "<p>Translator: Fenrir</p>"
        '<div class="c1a2" aria-hidden="true">junk</div>'
        "<p>Editor ​‌‍name</p>"
        '<p><span style="display:none">x</span>Real line</p>'
    )
    scraper, _ = make_scraper()
    result = scraper._fenrir_build_chapter(
        {"content": content, "content_format": "html"}, "Chapter 1"
    )
    assert result["contentText"] == "Translator: Fenrir\nEditor name\nReal line"


def test_fenrir_paid_lock_detection():
    paid = {"locked": {"price": 15, "unlocked_at": None},
            "bought": {"bought_at": None}}
    bought = {"locked": {"price": 15, "unlocked_at": None},
              "bought": {"bought_at": "2026-01-01T00:00:00Z"}}
    free = {"locked": {"price": 0, "unlocked_at": "2025-01-01"},
            "bought": {}}
    assert ExternalScraper._fenrir_paid_locked(paid)
    assert not ExternalScraper._fenrir_paid_locked(bought)
    assert not ExternalScraper._fenrir_paid_locked(free)


def test_fenrir_parse_book_builds_reader_urls(monkeypatch):
    scraper, _ = make_scraper()
    monkeypatch.setattr(
        scraper, "_load_saved_site_cookies", lambda *args: 0
    )
    replies = {
        "/api/new/v2/series/demo": {
            "title": "Demo", "description": "<p>Intro</p>",
            "cover": "storage/1/c.png", "status": "Ongoing",
            "user": {"name": "tl"}, "genres": [{"name": "Action"}],
            "tags": [{"name": "Regression"}],
        },
        "/api/new/v2/series/demo/chapters": [
            {"id": 2, "slug": "2", "name": "Chapter 2", "title": "B",
             "number": 2, "index": 1,
             "locked": {"price": 10, "unlocked_at": None}, "bought": {}},
            {"id": 1, "slug": "1", "name": "Chapter 1 - A", "title": "A",
             "number": 1, "index": 0,
             "locked": {"price": 0, "unlocked_at": "x"}, "bought": {}},
        ],
    }

    def fake_get(self, url, timeout=None):
        return Response(replies[url.replace(ExternalScraper._FENRIR_ORIGIN, "")])

    monkeypatch.setattr(requests.Session, "get", fake_get)
    data = scraper.parse_book("https://fenrirealm.com/series/demo/1")
    assert data["bookname"] == "Demo"
    assert data["coverUrl"] == "https://fenrirealm.com/storage/1/c.png"
    assert [c["name"] for c in data["chapters"]] == [
        "Chapter 1 - A", "Chapter 2 - B"
    ]
    assert data["chapters"][0]["url"] == "https://fenrirealm.com/series/demo/1"
    assert not data["chapters"][0]["isPaid"]
    assert data["chapters"][1]["isPaid"]
    assert data["chapters"][1]["isAccessible"] is False


def test_maru_url_detection_accepts_any_numbered_domain():
    assert ExternalScraper.is_maru("https://marumaru104.com/novel/24854")
    assert ExternalScraper.is_maru(
        "https://marumaru105.com/novel/24854/25361"
    )
    assert ExternalScraper._maru_url_parts(
        "https://marumaru105.com/novel/24854/25361"
    ) == ("24854", "25361")
    assert not ExternalScraper.is_maru("https://marumaru105.com/novel/")
    assert not ExternalScraper.is_maru("https://example.com/novel/24854")


def test_maru_book_and_episodes(monkeypatch):
    scraper, _ = make_scraper()
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        if url.endswith("/works/7"):
            return Response({"ok": True, "item": {
                "title": "Work", "author": "A", "genres": "판타지",
                "cover_url": "https://img/c.dat"}, "synopsis": "S",
                "episodes": [
                    {"id": 30, "number": 2, "title": "Work 2화", "ready": False},
                    {"id": 10, "number": 1, "title": "Work 1화", "ready": True},
                ]})
        if url.endswith("/episodes/10"):
            return Response({"ok": True, "item": {"ready": True, "body": [
                {"kind": "narration", "text": "1화\n\nLine one\nLine two"},
                {"kind": "image", "url": "https://img/a.webp"},
            ]}})
        if url.endswith("/episodes/40"):
            return Response({"ok": True, "item": {"ready": True, "body": [
                {"kind": "paid", "text": "포인트 구매 회차"}]}})
        return Response({"ok": True, "item": {"ready": False, "body": []}})

    monkeypatch.setattr(requests, "get", fake_get)
    data = scraper.parse_book("https://marumaru104.com/novel/7")
    assert [c["name"] for c in data["chapters"]] == ["1화", "2화"]
    assert data["chapters"][1]["isAccessible"] is False
    # Retired domains are rejected by Referer, so none is sent.
    assert all("Referer" not in headers for _, headers in calls)

    first = scraper.parse_chapter(0, data["chapters"][0], interval=0)
    assert first["contentText"] == "Line one\nLine two"
    assert first["images"][0]["url"] == "https://img/a.webp"

    waiting = scraper.parse_chapter(1, data["chapters"][1], interval=0)
    assert waiting["_locked"]

    paid = scraper._maru_parse_chapter(
        "https://marumaru104.com/novel/7/40", "3화"
    )
    assert paid == {"_locked": True, "chapterName": "3화",
                    "_lockReason": "paid"}


def test_fenrir_page_console_is_not_logged():
    scraper, messages = make_scraper()

    class Msg:
        type = "error"
        text = ("Connecting to 'data:,' violates the following Content "
                "Security Policy directive")

    scraper._rbooks_chrome = True
    scraper._rbooks_site = "Fenrir"
    scraper._on_console(Msg())
    assert messages == []

    scraper._rbooks_site = "Floo"
    scraper._on_console(Msg())
    assert messages


def test_fenrir_serialized_document_renders_paragraphs_and_images():
    import json
    scraper, _ = make_scraper()
    doc = {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Chapter 91'}]},
        {'type': 'paragraph', 'content': [
            {'type': 'text', 'text': 'First <line>'},
            {'type': 'hardBreak'}, {'type': 'text', 'text': 'Second line'}]},
        {'type': 'image', 'attrs': {'src': '/image.png'}},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Last line'}]},
    ]}
    for content in (json.dumps(doc), doc):
        result = scraper._fenrir_build_chapter(
            {'content': content, 'content_format': 'text'}, 'Chapter 91')
        assert result['contentText'] == 'First <line>\nSecond line\nLast line'
        assert result['images'][0]['url'].endswith('/image.png')
        assert '"type"' not in result['contentText']


def test_fenrir_all_locked_batch_never_starts_browser(monkeypatch):
    scraper, _ = make_scraper()
    scraper._book_data = {'_fenrir': True, '_fenrir_slug': 'demo'}
    monkeypatch.setattr(scraper, '_fenrir_page_ready',
                        lambda url: (_ for _ in ()).throw(AssertionError('browser started')))
    results = scraper._fenrir_parse_chapter_batch([
        {'name': 'Chapter 100', 'isAccessible': False, 'isPaid': True}])
    assert results == [{'_locked': True, '_lockReason': 'paid',
                        'chapterName': 'Chapter 100'}]


def test_fenrir_check_stays_hidden(monkeypatch):
    scraper, messages = make_scraper()
    calls = []
    class Page:
        def goto(self, *args, **kwargs):
            pass
        def bring_to_front(self):
            raise AssertionError('reader was shown')
    scraper._page = Page()
    waits = iter([False, True])
    monkeypatch.setattr(scraper, '_fenrir_wait_for_token', lambda seconds: next(waits))
    monkeypatch.setattr(scraper, '_get_user_data_dir', lambda: 'profile')
    monkeypatch.setattr(scraper, '_hide_chrome_windows_for_profile', calls.append)
    assert scraper._fenrir_unlock_reader('https://example.com/chapter')
    assert calls == ['profile']
    assert not any('tick' in m or 'window that just opened' in m for m in messages)


@pytest.mark.parametrize('number', ['1', '104', '105', '106', '9999'])
def test_maru_requested_episode_accepts_changing_domain_numbers(number):
    url = f'https://marumaru{number}.com/novel/48182/2187866'
    assert ExternalScraper.is_maru(url)
    assert ExternalScraper._maru_url_parts(url) == ('48182', '2187866')


@pytest.mark.parametrize('number', ['105', '106', '9999'])
def test_maru_embedded_text_uses_api_url_without_numbered_referer(monkeypatch, number):
    scraper, _ = make_scraper()
    monkeypatch.setattr(scraper, '_maru_get', lambda path: {
        'ok': True, 'item': {'ready': True,
                           'embed_url': 'https://peer.example/episode.xyz'}})
    calls = []
    def get(url, headers=None, timeout=None):
        calls.append((url, headers))
        class TextResponse:
            content = '\ufeffFirst paragraph\nSecond paragraph'.encode('utf-8')
            def raise_for_status(self):
                pass
        return TextResponse()
    monkeypatch.setattr(requests, 'get', get)
    result = scraper._maru_parse_chapter(
        f'https://marumaru{number}.com/novel/48182/2187866', 'Chapter 300')
    assert result['contentText'] == 'First paragraph\nSecond paragraph'
    assert calls[0][0] == 'https://peer.example/episode.xyz'
    assert 'Referer' not in calls[0][1]


@pytest.mark.parametrize('content', [b'<html>Unavailable</html>', b'\xff'])
def test_maru_embedded_error_is_not_saved_as_chapter(monkeypatch, content):
    scraper, _ = make_scraper()
    monkeypatch.setattr(scraper, '_maru_get', lambda path: {
        'item': {'ready': True, 'embed_url': 'https://peer.example/episode.xyz'}})
    class TextResponse:
        def raise_for_status(self):
            pass
    response = TextResponse()
    response.content = content
    monkeypatch.setattr(requests, 'get', lambda *args, **kwargs: response)
    assert scraper._maru_parse_chapter(
        'https://marumaru105.com/novel/48182/2187866', 'Chapter 300') is None


@pytest.mark.parametrize('value, expected', [
    ('7', 7), ('0', 0), (' 120 ', 120), (None, 30), ('', 30),
    ('invalid', 30), ('-1', 30), ('NaN', 30), ('Infinity', 30), ('1.5', 30),
    ('9' * 400, 30),
    ('Thu, 01 Jan 1970 00:17:00 GMT', 20),
    ('Thu, 01 Jan 1970 00:16:00 GMT', 0),
])
def test_fenrir_retry_after_delay(monkeypatch, value, expected):
    monkeypatch.setattr('external_scraper.time.time', lambda: 1000)
    assert ExternalScraper._fenrir_retry_delay(value) == expected


@pytest.mark.parametrize('headers, expected', [
    (['7', '12'], 12), ([None, '7'], 30), (['0', '0'], 0),
])
def test_fenrir_batch_respects_longest_retry_after(monkeypatch, headers, expected):
    scraper, messages = make_scraper()
    scraper._book_data = {'_fenrir': True, '_fenrir_slug': 'demo'}
    replies = iter([
        [{'status': 429, 'retryAfter': value} for value in headers],
        [{'status': 200, 'payload': {'content': 'Body', 'content_format': 'text'}}
         for _ in headers],
    ])
    class Page:
        def evaluate(self, *args):
            return next(replies)
    scraper._page = Page()
    monkeypatch.setattr(scraper, '_fenrir_page_ready', lambda url: True)
    monkeypatch.setattr(scraper, '_fenrir_token_active', lambda: True)
    monkeypatch.setattr(scraper, '_get_user_data_dir', lambda: 'profile')
    monkeypatch.setattr(scraper, '_hide_chrome_windows_for_profile', lambda path: None)
    sleeps = []
    monkeypatch.setattr(scraper, '_sleep_interval', lambda delay, *args: sleeps.append(delay))
    result = scraper._fenrir_parse_chapter_batch([
        {'name': f'Chapter {i}', 'url': f'https://fenrirealm.com/series/demo/{i}'}
        for i in range(len(headers))])
    assert all(ch['contentText'] == 'Body' for ch in result)
    assert sleeps == [expected, 0]
    assert any(f'waiting {expected:g} s' in message for message in messages)
