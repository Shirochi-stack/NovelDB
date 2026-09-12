import copy

import pytest

import external_scraper
from external_scraper import ExternalScraper


NOVEL_ID = '564583'
BOOK_URL = f'https://www.munpia.com/novel/detail/{NOVEL_ID}'
DETAIL_PATH = f'/api/v1/pc/novel-detail/{NOVEL_ID}'


def chapter_row(chapter_id, order=None, **overrides):
    row = {
        'id': chapter_id,
        'num': chapter_id if order is None else order,
        'novelId': int(NOVEL_ID),
        'title': f'Episode {chapter_id}',
        'free': True,
        'purchased': False,
        'remainRentSec': 0,
        'createdAt': '2026-09-01T00:00:00',
    }
    row.update(overrides)
    return row


def envelope(result):
    return {'status': 200, 'data': {'code': 'M000_00000', 'result': result}}


class BrowserPage:
    def __init__(self, responses):
        self.responses = responses
        self.navigations = []
        self.evaluations = []

    def goto(self, url, **kwargs):
        self.navigations.append((url, kwargs))

    def evaluate(self, script, path):
        self.evaluations.append((script, path))
        response = self.responses[path]
        if callable(response):
            return response()
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


@pytest.fixture
def book_setup(monkeypatch):
    messages = []
    scraper = ExternalScraper(logger=messages.append)
    detail = {
        'login': False,
        'novelInfo': {
            'id': int(NOVEL_ID),
            'title': 'Fixture novel',
            'authorName': 'Fixture author',
            'introduction': 'Line <one>\nLine two & more',
            'coverUrl': 'https://cdn.munpia.com/fixture.jpg',
            'chapterCount': 1,
            'tags': [{'title': 'Fantasy'}, None, {'title': ''}],
            'finish': False,
        },
    }
    responses = {
        DETAIL_PATH: envelope(detail),
        f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=1&size=30': envelope({
            'total': 1, 'list': [chapter_row(1)],
        }),
    }
    page = BrowserPage(responses)
    scraper._page = page
    monkeypatch.setattr(scraper, '_start_munpia_browser', lambda _: True)
    monkeypatch.setattr(scraper, '_munpia_wait_for_selector', lambda *_: True)
    scraper.munpia_interval = 0
    return scraper, page, responses, detail, messages


@pytest.mark.parametrize('url', [
    BOOK_URL,
    BOOK_URL + '/?from=library#chapters',
    f'https://munpia.com/novel/detail/{NOVEL_ID}',
    f'https://WWW.MUNPIA.COM/novel/viewer/{NOVEL_ID}/12345',
    f'http://novel.munpia.com/{NOVEL_ID}',
    f'https://novel.munpia.com/{NOVEL_ID}/',
    f'https://novel.munpia.com/{NOVEL_ID}/page/2',
    f'https://novel.munpia.com/{NOVEL_ID}/neSrl/12345',
    f'https://novel.munpia.com/{NOVEL_ID}/page/2/neSrl/12345',
])
def test_munpia_current_and_legacy_urls_extract_same_novel_id(url):
    assert ExternalScraper.is_munpia(url)
    assert ExternalScraper._munpia_novel_id(url) == NOVEL_ID


@pytest.mark.parametrize('url', [
    None,
    '',
    564583,
    'https://[invalid',
    'https://munpia.com.attacker.example/novel/detail/564583',
    'https://www.munpia.com@attacker.example/novel/detail/564583',
    'https://attacker.example/path/www.munpia.com/novel/detail/564583',
    'https://novel.munpia.com.attacker.example/564583',
    'https://api.munpia.com/novel/detail/564583',
    'https://www.munpia.com/novel/detail/564583/extra',
    'https://www.munpia.com/novel/viewer/564583',
    'https://www.munpia.com/novel/detail/letters',
    'https://www.munpia.com/',
    'javascript://www.munpia.com/novel/detail/564583',
    '//www.munpia.com/novel/detail/564583',
])
def test_munpia_url_detection_rejects_wrong_hosts_and_non_book_paths(url):
    assert not ExternalScraper.is_munpia(url)
    assert ExternalScraper._munpia_novel_id(url) == ''


@pytest.mark.parametrize(
    'logged_in, free, purchased, rental_seconds, accessible, owned, rented',
    [
        (False, True, False, 0, True, False, False),
        (True, True, False, 0, True, False, False),
        (True, False, True, 0, True, True, False),
        (True, False, False, 3600, True, False, True),
        (True, False, False, 0, False, False, False),
        (True, False, False, -1, False, False, False),
        (False, False, True, 3600, False, False, False),
        (False, False, False, 0, False, False, False),
        (True, 'true', 'true', 0, False, False, False),
    ],
)
def test_munpia_access_flags_keep_paid_status_separate_from_entitlement(
    logged_in, free, purchased, rental_seconds, accessible, owned, rented,
):
    row = chapter_row(
        42, order=3, free=free, purchased=purchased,
        remainRentSec=rental_seconds,
    )
    result = ExternalScraper._munpia_chapter_from_api(row, NOVEL_ID, logged_in)
    assert result['isAccessible'] is accessible
    assert result['isPaid'] is (free is not True)
    assert result['isVIP'] is (free is not True)
    assert result['_munpiaPurchased'] is owned
    assert result['_munpiaRented'] is rented
    assert result['url'] == f'https://www.munpia.com/novel/viewer/{NOVEL_ID}/42'
    assert result['fullName'] == '3. Episode 42'
    assert result['neSrl'] == '42'


@pytest.mark.parametrize('row', [
    None,
    {},
    chapter_row(0),
    chapter_row(-1),
    chapter_row(1, order=0),
    chapter_row(1, novelId=123),
    chapter_row('invalid'),
    chapter_row(1, remainRentSec='invalid'),
])
def test_munpia_invalid_or_foreign_chapter_rows_are_rejected(row):
    assert ExternalScraper._munpia_chapter_from_api(row, NOVEL_ID) is None


def test_munpia_book_uses_saved_browser_api_and_normalizes_legacy_url(book_setup):
    scraper, page, _responses, _detail, messages = book_setup
    book = scraper.parse_book(f'https://novel.munpia.com/{NOVEL_ID}/neSrl/42')
    assert page.navigations[0][0] == BOOK_URL
    assert book['bookUrl'] == BOOK_URL
    assert scraper._book_url == BOOK_URL
    assert scraper._book_data is book
    assert book['bookname'] == 'Fixture novel'
    assert book['author'] == 'Fixture author'
    assert book['language'] == 'ko'
    assert book['introductionHTML'] == 'Line &lt;one&gt;<br/>Line two &amp; more'
    assert book['coverUrl'] == 'https://cdn.munpia.com/fixture.jpg'
    assert book['tags'] == ['Fantasy']
    assert book['status'] == 'Ongoing'
    assert book['_munpia_logged_in'] is False
    assert book['chapterCount'] == 1
    assert any('No saved login' in message for message in messages)
    for script, path in page.evaluations:
        assert "credentials: 'include'" in script
        assert 'fetch(path,' in script
        assert 'AbortController' in script
        assert path.startswith(DETAIL_PATH)


def test_munpia_book_reads_all_pages_deduplicates_and_sorts(book_setup):
    scraper, page, responses, detail, _messages = book_setup
    detail['login'] = True
    detail['novelInfo'].update(chapterCount=4, finish=True)
    responses[DETAIL_PATH] = envelope(detail)
    first_path = f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=1&size=30'
    second_path = f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=2&size=30'
    responses[first_path] = envelope({
        'total': 4,
        'list': [chapter_row(3), chapter_row(1), chapter_row(1)],
    })
    responses[second_path] = envelope({
        'total': 4,
        'list': [
            chapter_row(4, free=False, purchased=True),
            chapter_row(3),
            chapter_row(2, free=False),
        ],
    })
    book = scraper._munpia_parse_book(BOOK_URL)
    assert [chapter['order'] for chapter in book['chapters']] == [1, 2, 3, 4]
    assert [chapter['neSrl'] for chapter in book['chapters']] == ['1', '2', '3', '4']
    assert book['chapterCount'] == 4
    assert book['_munpia_declared_count'] == 4
    assert book['_munpia_logged_in'] is True
    assert book['status'] == 'Completed'
    assert book['chapters'][1]['isAccessible'] is False
    assert book['chapters'][3]['isAccessible'] is True
    assert book['chapters'][3]['isPaid'] is True
    assert [path for _, path in page.evaluations] == [
        DETAIL_PATH, first_path, second_path,
    ]


@pytest.mark.parametrize('second_rows', [[], [chapter_row(1)]])
def test_munpia_pagination_with_no_progress_rejects_partial_book(
    book_setup, second_rows,
):
    scraper, _page, responses, _detail, messages = book_setup
    responses[f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=1&size=30'] = envelope({
        'total': 2, 'list': [chapter_row(1)],
    })
    responses[f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=2&size=30'] = envelope({
        'total': 2, 'list': second_rows,
    })
    assert scraper._munpia_parse_book(BOOK_URL) is None
    assert scraper._book_data is None
    assert any('Incomplete chapter list' in message for message in messages)


@pytest.mark.parametrize('listing', [
    {'total': 0, 'list': []},
    {'total': -1, 'list': [chapter_row(1)]},
    {'total': 'invalid', 'list': [chapter_row(1)]},
    {'total': 1, 'list': None},
    {'total': 1, 'list': [chapter_row(1, novelId=123)]},
    {'list': [chapter_row(1)]},
])
def test_munpia_invalid_listing_is_error_not_partial_success(book_setup, listing):
    scraper, _page, responses, _detail, messages = book_setup
    responses[f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=1&size=30'] = envelope(listing)
    assert scraper._munpia_parse_book(BOOK_URL) is None
    assert scraper._book_data is None
    assert any('ERROR' in message for message in messages)


def test_munpia_optional_null_tags_does_not_crash_book_parse(book_setup):
    scraper, _page, responses, detail, _messages = book_setup
    detail['novelInfo']['tags'] = None
    responses[DETAIL_PATH] = envelope(detail)
    book = scraper._munpia_parse_book(BOOK_URL)
    assert book['tags'] == []


@pytest.mark.parametrize('metadata', [
    {},
    {'id': 123, 'title': 'Wrong book'},
    {'id': int(NOVEL_ID), 'title': ''},
    [],
])
def test_munpia_invalid_metadata_stops_before_chapter_requests(book_setup, metadata):
    scraper, page, responses, detail, _messages = book_setup
    detail['novelInfo'] = metadata
    responses[DETAIL_PATH] = envelope(detail)
    assert scraper._munpia_parse_book(BOOK_URL) is None
    assert [path for _, path in page.evaluations] == [DETAIL_PATH]


def test_munpia_cancellation_during_pagination_discards_partial_book(book_setup):
    scraper, page, responses, _detail, _messages = book_setup

    def first_page_then_cancel():
        scraper._stop_requested = True
        return envelope({'total': 2, 'list': [chapter_row(1)]})

    responses[f'{DETAIL_PATH}/chapters?order=ENTRY_FIRST&page=1&size=30'] = first_page_then_cancel
    assert scraper._munpia_parse_book(BOOK_URL) is None
    assert scraper._book_data is None
    assert len(page.evaluations) == 2


def test_munpia_list_pacing_uses_min_max_and_stop_interrupts_sleep(monkeypatch):
    scraper = ExternalScraper(logger=lambda _: None)
    scraper.munpia_interval = 2.5
    scraper.munpia_interval_max = 1.5
    clock = [0.0]
    draws = []

    def draw(low, high):
        draws.append((low, high))
        return 2

    def sleep(delay):
        clock[0] += delay
        scraper._stop_requested = True

    monkeypatch.setattr(external_scraper.random, 'uniform', draw)
    monkeypatch.setattr(external_scraper.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(external_scraper.time, 'sleep', sleep)
    assert scraper._munpia_wait_list_interval() is False
    assert draws == [(1.5, 2.5)]
    assert clock[0] <= 0.1


@pytest.mark.parametrize('response', [
    None,
    {'status': 403, 'data': {}},
    {'status': 200, 'data': None},
    {'status': 200, 'data': {'code': 'ACCESS_DENIED', 'result': {}}},
    {'status': 200, 'data': {'code': 'M000_00000', 'result': []}},
])
def test_munpia_api_rejects_http_and_application_errors(response):
    scraper = ExternalScraper(logger=lambda _: None)
    scraper._page = BrowserPage({DETAIL_PATH: response})
    with pytest.raises(RuntimeError, match='Munpia API'):
        scraper._munpia_api_get(DETAIL_PATH)


def test_munpia_api_exception_becomes_book_error(book_setup):
    scraper, _page, responses, _detail, messages = book_setup
    responses[DETAIL_PATH] = RuntimeError('Browser JSON parse failed')
    assert scraper._munpia_parse_book(BOOK_URL) is None
    assert scraper._book_data is None
    assert any('Browser JSON parse failed' in message for message in messages)


def test_munpia_browser_start_failure_does_not_fetch_metadata(book_setup, monkeypatch):
    scraper, page, _responses, _detail, _messages = book_setup
    monkeypatch.setattr(scraper, '_start_munpia_browser', lambda _: False)
    assert scraper._munpia_parse_book(BOOK_URL) is None
    assert page.navigations == []
    assert page.evaluations == []


def test_munpia_public_chapter_parser_does_not_open_known_locked_chapter(monkeypatch):
    scraper = ExternalScraper(logger=lambda _: None)
    scraper._book_data = {'_munpia': True}

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError('An inaccessible chapter must not be opened')

    monkeypatch.setattr(scraper, '_munpia_parse_chapter', unexpected_fetch)
    chapter = ExternalScraper._munpia_chapter_from_api(
        chapter_row(42, free=False, purchased=True), NOVEL_ID, logged_in=False,
    )
    assert scraper.parse_chapter(0, chapter) == {
        '_locked': True, 'chapterName': chapter['fullName'],
    }


def test_munpia_public_chapter_parser_fetches_owned_paid_and_preserves_options(
    monkeypatch,
):
    scraper = ExternalScraper(logger=lambda _: None)
    scraper._book_data = {'_munpia': True}
    chapter = ExternalScraper._munpia_chapter_from_api(
        chapter_row(42, free=False, purchased=True), NOVEL_ID, logged_in=True,
    )
    page = object()
    calls = []
    delays = []

    def fetch(url, name, page):
        calls.append((url, name, page))
        return {'chapterName': name, 'contentText': 'Owned chapter content.'}

    monkeypatch.setattr(scraper, '_munpia_parse_chapter', fetch)
    monkeypatch.setattr(
        scraper, '_sleep_interval', lambda low, high: delays.append((low, high)),
    )
    result = scraper.parse_chapter(
        0, chapter, interval=1.25, interval_max=2.5, page=page,
    )
    assert result['contentText'] == 'Owned chapter content.'
    assert calls == [(chapter['url'], chapter['fullName'], page)]
    assert delays == [(1.25, 2.5)]
