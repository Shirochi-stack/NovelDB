import pytest

import external_scraper
from external_scraper import ExternalScraper


class Clock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = None

    def sleep(self, delay):
        self.now += delay
        if self.on_sleep:
            self.on_sleep()


class Page:
    def __init__(self, clock, ready_after=0, navigation_error=None):
        self.clock = clock
        self.ready_after = ready_after
        self.navigation_error = navigation_error
        self.started_at = None
        self.url = 'https://www.munpia.com/novel/detail/564583/view/previous'
        self.goto_options = None

    def goto(self, url, **kwargs):
        assert self.prepared, 'Prepare canvas capture before reader navigation'
        self.started_at = self.clock.now
        self.goto_options = kwargs
        if self.navigation_error:
            raise RuntimeError(self.navigation_error)
        self.url = url


def chapters(count):
    return [
        {
            'url': f'https://www.munpia.com/novel/detail/564583/view/{index}',
            'name': f'Chapter {index}',
        }
        for index in range(count)
    ]


@pytest.fixture
def batch_setup(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(external_scraper.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(external_scraper.time, 'sleep', clock.sleep)
    messages = []
    scraper = ExternalScraper(logger=messages.append)
    scraper._book_data = {'_munpia': True}
    scraper._book_url = 'https://www.munpia.com/novel/detail/564583'
    monkeypatch.setattr(scraper, '_hide_chrome_windows_for_profile', lambda _: None)
    monkeypatch.setattr(scraper, '_get_user_data_dir', lambda: 'fixture-profile')
    monkeypatch.setattr(
        scraper, '_munpia_prepare_reader_page',
        lambda page: setattr(page, 'prepared', True), raising=False,
    )

    def is_ready(page, selector):
        assert selector == scraper._MUNPIA_READER_SELECTOR
        return clock.now >= page.started_at + page.ready_after

    monkeypatch.setattr(scraper, '_munpia_page_has_selector', is_ready)
    extracted = []

    def extract(page, name):
        extracted.append((page.url, name))
        return {'chapterName': name, 'contentText': 'Test chapter content.'}

    monkeypatch.setattr(scraper, '_munpia_extract_loaded_chapter', extract)
    return scraper, clock, messages, extracted


def test_munpia_batch_dispatch_preserves_all_pacing_and_callback_options():
    scraper = ExternalScraper(logger=lambda _: None)
    scraper._book_data = {'_munpia': True}
    seen = {}
    batch = chapters(1)
    callback = lambda *_: None

    def fetch(items, **kwargs):
        seen.update(kwargs)
        assert items is batch
        return ['result']

    scraper._munpia_parse_chapter_batch_parallel = fetch
    assert scraper.parse_chapter_batch(
        batch, interval=1.25, interval_max=2.5, success_callback=callback,
    ) == ['result']
    assert seen == {
        'interval': 1.25,
        'interval_max': 2.5,
        'success_callback': callback,
    }


def test_munpia_launches_use_normalized_random_range_and_live_callbacks(
    batch_setup, monkeypatch,
):
    scraper, clock, _messages, _extracted = batch_setup
    pages = [Page(clock) for _ in range(3)]
    monkeypatch.setattr(scraper, '_munpia_parallel_pages', lambda *_: pages)
    draws = iter([1.5, 2.5])
    ranges = []

    def draw(low, high):
        ranges.append((low, high))
        return next(draws)

    monkeypatch.setattr(external_scraper.random, 'uniform', draw)
    completed = []
    results = scraper.parse_chapter_batch(
        chapters(3), interval=2.75, interval_max=1.25,
        success_callback=lambda index, _: completed.append((index, clock.now)),
    )

    assert ranges == [(1.25, 2.75), (1.25, 2.75)]
    assert [page.started_at for page in pages] == pytest.approx([0, 1.5, 4])
    assert [index for index, _ in completed] == [0, 1, 2]
    assert completed[0][1] < pages[1].started_at
    assert completed[1][1] < pages[2].started_at
    assert [result['chapterName'] for result in results] == [
        'Chapter 0', 'Chapter 1', 'Chapter 2',
    ]
    assert all(page.goto_options['referer'] == scraper._book_url for page in pages)


def test_munpia_batch_skips_locked_chapters_and_fetches_owned_paid_chapters(
    batch_setup, monkeypatch,
):
    scraper, clock, _messages, extracted = batch_setup
    batch = chapters(3)
    batch[0].update(isAccessible=False, isPaid=True, fullName='Locked chapter')
    batch[1].update(isAccessible=True, isPaid=True)
    batch[2].update(isAccessible=True, isPaid=False)
    pages = [Page(clock), Page(clock)]
    allocations = []

    def allocate(count, url):
        allocations.append((count, url))
        return pages

    monkeypatch.setattr(scraper, '_munpia_parallel_pages', allocate)
    completed = []
    results = scraper.parse_chapter_batch(
        batch, interval=0,
        success_callback=lambda index, _: completed.append(index),
    )
    assert allocations == [(2, batch[1]['url'])]
    assert results[0] == {'_locked': True, 'chapterName': 'Locked chapter'}
    assert [url for url, _ in extracted] == [batch[1]['url'], batch[2]['url']]
    assert completed == [1, 2]


def test_munpia_all_locked_batch_needs_no_browser(batch_setup, monkeypatch):
    scraper, _clock, _messages, _extracted = batch_setup

    def unexpected_browser(*_):
        raise AssertionError('Locked chapters must not navigate or start a browser')

    monkeypatch.setattr(scraper, '_munpia_parallel_pages', unexpected_browser)
    batch = chapters(2)
    for chapter in batch:
        chapter['isAccessible'] = False
    assert all(result['_locked'] for result in scraper.parse_chapter_batch(batch))


@pytest.mark.parametrize('page_count', [0, 1])
def test_munpia_missing_workers_fall_back_without_losing_chapters(
    batch_setup, monkeypatch, page_count,
):
    scraper, clock, _messages, _extracted = batch_setup
    pages = [Page(clock) for _ in range(page_count)]
    scraper._page = Page(clock)
    monkeypatch.setattr(scraper, '_munpia_parallel_pages', lambda *_: pages)
    calls = []

    def fetch(url, name, page):
        calls.append((url, name, page, clock.now))
        return {'chapterName': name, 'contentText': 'Fallback chapter.'}

    monkeypatch.setattr(scraper, '_munpia_parse_chapter', fetch)
    completed = []
    batch = chapters(3)
    results = scraper.parse_chapter_batch(
        batch, interval=1.5,
        success_callback=lambda index, _: completed.append(index),
    )
    assert [call[0] for call in calls] == [chapter['url'] for chapter in batch]
    assert [call[3] for call in calls] == pytest.approx([0, 1.5, 3])
    assert all(call[2] is (pages[0] if pages else scraper._page) for call in calls)
    assert completed == [0, 1, 2]
    assert all(result['contentText'] for result in results)


def test_munpia_failed_navigation_never_extracts_previous_chapter(
    batch_setup, monkeypatch,
):
    scraper, clock, _messages, extracted = batch_setup
    pages = [Page(clock, navigation_error='Timeout navigating'), Page(clock)]
    monkeypatch.setattr(scraper, '_munpia_parallel_pages', lambda *_: pages)
    results = scraper.parse_chapter_batch(chapters(2), interval=0)
    assert results[0] is None
    assert results[1]['chapterName'] == 'Chapter 1'
    assert extracted == [(pages[1].url, 'Chapter 1')]


def test_munpia_reader_timeout_is_failure_without_extracting_unready_dom(
    batch_setup, monkeypatch,
):
    scraper, clock, messages, extracted = batch_setup
    monkeypatch.setattr(
        scraper, '_munpia_parallel_pages', lambda *_: [Page(clock, ready_after=60)],
    )
    assert scraper.parse_chapter_batch(chapters(1), interval=0) == [None]
    assert extracted == []
    assert any('Timed out waiting for: Chapter 0' in message for message in messages)
    assert clock.now < 46


def test_munpia_stop_interrupts_long_launch_delay(batch_setup, monkeypatch):
    scraper, clock, _messages, extracted = batch_setup
    pages = [Page(clock), Page(clock)]
    monkeypatch.setattr(scraper, '_munpia_parallel_pages', lambda *_: pages)

    def request_stop():
        scraper._stop_requested = True

    clock.on_sleep = request_stop
    results = scraper.parse_chapter_batch(chapters(2), interval=120)
    assert results[0]['chapterName'] == 'Chapter 0'
    assert results[1] is None
    assert pages[1].started_at is None
    assert len(extracted) == 1
    assert clock.now <= 0.2


def test_munpia_callbacks_follow_completion_order_and_ignore_callback_errors(
    batch_setup, monkeypatch,
):
    scraper, clock, _messages, _extracted = batch_setup
    pages = [Page(clock, ready_after=1), Page(clock)]
    monkeypatch.setattr(scraper, '_munpia_parallel_pages', lambda *_: pages)
    completed = []

    def callback(index, _result):
        completed.append(index)
        raise RuntimeError('The UI callback must not abort scraping')

    results = scraper.parse_chapter_batch(
        chapters(2), interval=0, success_callback=callback,
    )
    assert completed == [1, 0]
    assert [result['chapterName'] for result in results] == ['Chapter 0', 'Chapter 1']


def test_munpia_reader_lock_and_failure_are_preserved_without_success_callbacks(
    batch_setup, monkeypatch,
):
    scraper, clock, _messages, _extracted = batch_setup
    monkeypatch.setattr(
        scraper, '_munpia_parallel_pages', lambda *_: [Page(clock), Page(clock)],
    )
    responses = iter([{'_locked': True, 'chapterName': 'Chapter 0'}, None])
    monkeypatch.setattr(
        scraper, '_munpia_extract_loaded_chapter', lambda *_: next(responses),
    )
    completed = []
    results = scraper.parse_chapter_batch(
        chapters(2), interval=0,
        success_callback=lambda index, _: completed.append(index),
    )
    assert results == [{'_locked': True, 'chapterName': 'Chapter 0'}, None]
    assert completed == []
