"""Rbooks rentals and PC viewer failures: one unreadable volume must not hold up the others."""
import datetime

import pytest

from external_scraper import ExternalScraper

AT = '\u0072\u0069\u0064\u0069\u002d\u0061\u0074'
BOOK = {'chapters': [{'id': '1001'}, {'id': '1002'}, {'id': '1003'}]}


class _Library:
    """A library lookup answer from the viewer page."""

    def __init__(self, items):
        self.items = items

    def evaluate(self, script, ids):
        return self.items


class _Context:
    def cookies(self, urls):
        return [{'name': AT}]

    def new_page(self):
        raise RuntimeError('library origin unavailable in tests')


def _scraper(logs, monkeypatch, extract):
    from rbooks_app_proxy import RbooksAppProxy
    monkeypatch.setattr(RbooksAppProxy, 'extract', extract)
    monkeypatch.setattr('sys.platform', 'win32')
    scraper = ExternalScraper(logger=logs.append)
    scraper._context = _Context()
    scraper._book_data = dict(BOOK)
    return scraper


def _item(book_id, service='normal', expires=''):
    return {'id': book_id, 'service': service, 'expires': expires}


def test_library_items_remember_rentals_and_accept_old_answers():
    scraper = ExternalScraper(logger=lambda m: None)
    owned = scraper._rbooks_library_items([_item('1001'), _item('1002', 'rent', '2030-01-01T00:00:00+09:00'), '1003'])
    assert owned == {'1001', '1002', '1003'}
    assert scraper._rbooks_rentals == {'1002': '2030-01-01T00:00:00+09:00'}
    assert scraper._rbooks_library_items(None) is None


@pytest.mark.parametrize('expires,expired', [
    ('2020-01-01T00:00:00+09:00', True),
    ('2999-12-31T23:59:59+09:00', False),
    ('2020-01-01 10:00:00', True),
    ('', False),
    ('not a date', False),
])
def test_rental_expiry(expires, expired):
    now = datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc)
    assert ExternalScraper._rbooks_rental_expired(expires, now) is expired


def test_an_expired_rental_is_reported_without_opening_the_pc_viewer(monkeypatch):
    logs, calls = [], []
    scraper = _scraper(logs, monkeypatch, lambda *a: calls.append(a))
    page = _Library([_item('1001'), _item('1002', 'rent', '2020-01-01T00:00:00+09:00')])
    result = scraper._rbooks_refused_result('Vol 2', page, '1002')
    assert result['_lockReason'] == 'rental_expired'
    assert calls == []
    assert 'rental of Vol 2 expired' in logs[-1] and 'other volumes continue' in logs[-1]


def test_an_active_rental_goes_to_the_pc_viewer(monkeypatch):
    logs, calls = [], []

    def extract(self, context, book_id, title, url):
        calls.append(book_id)
        return {'chapterName': title, 'contentHtml': '<p>rented text</p>'}
    scraper = _scraper(logs, monkeypatch, extract)
    page = _Library([_item('1002', 'rent', '2999-01-01T00:00:00+09:00')])
    assert scraper._rbooks_refused_result('Vol 2', page, '1002')['contentHtml'] == '<p>rented text</p>'
    assert calls == ['1002']


def test_a_volume_the_pc_viewer_cannot_read_is_skipped_after_two_tries(monkeypatch):
    from rbooks_app_proxy import RbooksAppError
    logs = []

    def extract(self, context, book_id, title, url):
        if book_id == '1002':
            raise RbooksAppError('RBOOKS viewer menu was not found.')
        return {'chapterName': title, 'contentHtml': f'<p>{title}</p>'}
    scraper = _scraper(logs, monkeypatch, extract)
    page = _Library([_item('1001'), _item('1002', 'rent', '2999-01-01T00:00:00+09:00'), _item('1003')])
    assert scraper._rbooks_refused_result('Vol 2', page, '1002') is None  # retried once
    assert 'retried once' in logs[-1]
    second = scraper._rbooks_refused_result('Vol 2', page, '1002')
    assert second == {'_locked': True, 'chapterName': 'Vol 2', '_lockReason': 'export_failed'}
    assert 'not marked as paid' in logs[-1] and 'rental' in logs[-1]
    # The other volumes are unaffected.
    assert scraper._rbooks_refused_result('Vol 1', page, '1001')['contentHtml'] == '<p>Vol 1</p>'
    assert scraper._rbooks_refused_result('Vol 3', page, '1003')['contentHtml'] == '<p>Vol 3</p>'


def test_the_pc_viewer_reporting_an_expired_rental_stops_at_once(monkeypatch):
    from rbooks_app_proxy import RbooksAppError
    logs = []

    def extract(self, context, book_id, title, url):
        raise RbooksAppError('RBOOKS says this rental has expired.', reason='rental_expired')
    scraper = _scraper(logs, monkeypatch, extract)
    result = scraper._rbooks_refused_result('Vol 2', _Library([_item('1002', 'rent')]), '1002')
    assert result['_lockReason'] == 'rental_expired'


# -- the PC viewer proxy --------------------------------------------------------------------------------

def test_a_reader_window_that_will_not_close_is_force_closed(monkeypatch):
    import rbooks_app_proxy
    from rbooks_app_proxy import RbooksAppProxy
    logs = []
    proxy = RbooksAppProxy(logs.append)
    proxy.port = 9222
    open_tabs = [{'id': 'reader', 'url': 'app://x/index.html?Viewer'}]
    closed = []
    monkeypatch.setattr(proxy, '_tabs', lambda port: list(open_tabs))
    monkeypatch.setattr(proxy, '_evaluate_target', lambda tab, expression: None)  # window.close() ignored

    def close_target(target_id):
        closed.append(target_id)
        open_tabs.clear()
    monkeypatch.setattr(proxy, '_close_target', close_target)
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep', lambda s: None)
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: next(clock))
    proxy._close_reader_windows()
    assert closed == ['reader']
    assert 'Force-closed' in logs[-1]


def test_a_missing_menu_reports_what_the_reader_shows(monkeypatch):
    from rbooks_app_proxy import RbooksAppProxy
    logs = []
    proxy = RbooksAppProxy(logs.append)
    monkeypatch.setattr(proxy, '_evaluate', lambda suffix, script: {
        'buttons': ['닫기'], 'text': '대여 기간이 만료되었습니다. 다시 대여해 주세요.'})
    error = proxy._viewer_menu_missing()
    assert error.reason == 'rental_expired'
    assert 'buttons [닫기]' in logs[-1]
    monkeypatch.setattr(proxy, '_evaluate', lambda suffix, script: {'buttons': [], 'text': ''})
    error = proxy._viewer_menu_missing()
    assert error.reason == '' and 'menu was not found' in str(error)


@pytest.fixture(scope='module')
def page():
    playwright_api = pytest.importorskip('playwright.sync_api')
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # no bundled Chromium here
            pytest.skip(f'Chromium unavailable: {exc}')
        yield browser.new_page()
        browser.close()


def _dialog(page, text, buttons):
    page.set_content(
        '<div role="dialog"><p>%s</p>%s</div><script>window.clicked=null;'
        'document.querySelectorAll("button").forEach(b=>b.onclick=()=>window.clicked=b.innerText)</script>'
        % (text, ''.join(f'<button>{b}</button>' for b in buttons)))


def test_rental_notices_are_acknowledged_but_never_extended(page):
    from rbooks_app_proxy import RbooksAppProxy
    _dialog(page, '대여 기간이 3일 남았습니다.', ['닫기', '바로 읽기'])
    assert page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) == 'rental-notice'
    assert page.evaluate('window.clicked') == '닫기'
    _dialog(page, '대여 기간이 만료됩니다.', ['대여 연장', '확인'])
    assert page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) is None
    assert page.evaluate('window.clicked') is None
    _dialog(page, '이 책을 구매하시겠습니까?', ['구매', '취소하기'])
    assert page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) is None
