from types import SimpleNamespace

import pytest

from rbooks_app_proxy import RbooksAppProxy, RbooksAppError


@pytest.fixture(scope='module')
def popup_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def test_sync_popup_cancels_without_moving_the_selected_page(popup_page):
    popup_page.set_content('''<p>Requested section</p><div id="prompt">
      다른 기기에서 7분 전에 읽던 페이지로 가시겠습니까?
      현재 페이지 364 → 읽던 페이지 529
      <button onclick="window.action='cancel';this.parentElement.remove()">취소</button>
      <button onclick="window.action='move'">이동</button></div>''')
    assert popup_page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) == 'reading-position'
    assert popup_page.evaluate('window.action') == 'cancel'
    assert popup_page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) is None


def test_notice_appearing_after_page_change_is_dismissed(popup_page):
    popup_page.set_content('<h2>Chapter one</h2><p>Text</p>')
    assert popup_page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) is None
    popup_page.evaluate('''() => {
      const box = document.createElement('div'); box.setAttribute('role', 'dialog');
      box.innerHTML = 'Notice <button onclick="this.parentElement.remove()">확인</button>';
      document.body.appendChild(box);
    }''')
    assert popup_page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) == 'notice'
    assert popup_page.locator('h2').inner_text() == 'Chapter one'


@pytest.mark.parametrize('markup', [
    '<div role="dialog" hidden>Notice <button>확인</button></div>',
    '<p>Book text <button>확인</button></p>',
    '<div role="dialog">Delete? <button>취소</button><button>확인</button></div>',
    '<div role="dialog">Purchase? <button>구매</button></div>',
])
def test_popup_handler_leaves_hidden_and_unrelated_actions_alone(popup_page, markup):
    popup_page.set_content(markup)
    assert popup_page.evaluate(RbooksAppProxy.PAGE_POPUP_SCRIPT) is None


def test_popup_watcher_continues_after_reader_pages_change(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    monkeypatch.setattr(proxy, '_native_rbooks_dialog', lambda: None)
    monkeypatch.setattr(proxy, '_accept_js_dialog', lambda: False)
    polls = []
    monkeypatch.setattr(proxy, '_dismiss_page_popup', lambda: polls.append(True))

    class Stop:
        def is_set(self):
            return len(polls) >= 3

        def wait(self, delay):
            pass

    proxy._dismiss_viewer_popups(Stop())
    assert len(polls) == 3


def test_in_page_handler_only_uses_bound_reader(monkeypatch):
    logs = []
    proxy = RbooksAppProxy(logs.append)
    calls = []
    monkeypatch.setattr(proxy, '_evaluate',
                        lambda tab, code: calls.append(tab) or 'reading-position')
    assert not proxy._dismiss_page_popup()
    assert not calls
    proxy._reader_targets['Viewer'] = 'requested-volume'
    assert proxy._dismiss_page_popup()
    assert calls == ['Viewer']
    assert proxy._popup_revision == 1
    assert 'Cancelled' in logs[0]


def test_popup_during_section_verification_resets_render_stability(monkeypatch):
    import rbooks_app_proxy
    proxy = RbooksAppProxy(lambda line: None)
    section = {'spine': 9, 'html': '<h2>Chapter one</h2><p>Text</p>'}
    monkeypatch.setattr(proxy, '_front_sections', lambda: [section])
    monkeypatch.setattr(proxy, '_open_toc_row', lambda index: True)
    polls = []

    def popup():
        polls.append(True)
        if len(polls) == 2:
            proxy._popup_revision += 1
        return len(polls) == 2

    monkeypatch.setattr(proxy, '_dismiss_page_popup', popup)
    ticks = iter(range(100))
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: next(ticks))

    def wait(check, *args):
        assert check() is None
        assert check() is None  # Popup interrupts the first stable render.
        return check()

    monkeypatch.setattr(proxy, '_wait', wait)
    assert proxy._read_toc_section(
        {'index': 0, 'page': 20, 'title': 'Chapter one'}, set()) == section


def test_bound_reader_ignores_other_volumes(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    proxy._reader_targets['Viewer'] = 'volume-2'
    monkeypatch.setattr(proxy, '_tabs', lambda port: [
        {'id': 'volume-1', 'type': 'page', 'url': 'file:///index.html?Viewer'},
        {'id': 'volume-2', 'type': 'page', 'url': 'file:///index.html?Viewer'},
    ])
    assert proxy._tab('Viewer')['id'] == 'volume-2'


def test_wait_for_viewer_rejects_wrong_volume_title(monkeypatch):
    import rbooks_app_proxy
    proxy = RbooksAppProxy(lambda line: None)
    tabs = iter([{'id': 'wrong'}, {'id': 'requested'}])
    monkeypatch.setattr(proxy, '_tab', lambda suffix: next(tabs))
    inspected = []

    def evaluate(tab, expression):
        inspected.append(tab['id'])
        return tab['id'] == 'requested'

    monkeypatch.setattr(proxy, '_evaluate_target', evaluate)
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep', lambda delay: None)
    proxy._wait_for_viewer(None, title='Requested volume')
    assert inspected == ['wrong', 'requested']
    assert proxy._reader_targets['Viewer'] == 'requested'


def test_old_reader_and_contents_close_without_closing_library(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    library = {'id': 'library', 'url': 'file:///index.html?Books'}
    old = [{'id': 'old-reader', 'url': 'file:///index.html?Viewer'},
           {'id': 'old-toc', 'url': 'file:///index.html?TocModal'}]
    tabs = iter([[library, *old], [library]])
    monkeypatch.setattr(proxy, '_tabs', lambda port: next(tabs))
    closed = []
    monkeypatch.setattr(proxy, '_evaluate_target',
                        lambda tab, code: closed.append((tab['id'], code)))
    monkeypatch.setattr(proxy, '_wait', lambda check, *args: check())
    proxy._reader_targets['Viewer'] = 'old-reader'
    proxy._close_reader_windows()
    assert closed == [('old-reader', 'window.close()'),
                      ('old-toc', 'window.close()')]
    assert proxy._reader_targets == {}


def test_section_read_uses_matching_heading_in_two_page_spread(monkeypatch):
    import rbooks_app_proxy
    proxy = RbooksAppProxy(lambda line: None)
    wrong = {'spine': 8, 'html': '<h2>Previous section</h2><p>Wrong text</p>'}
    requested = {'spine': 9, 'html': '<h2>Chapter <span>one</span></h2><p>Text</p>'}
    next_section = {'spine': 10, 'html': '<h2>Chapter two</h2><p>Text</p>'}
    frames = iter([[wrong], [requested, next_section], [requested, next_section]])
    monkeypatch.setattr(proxy, '_front_sections', lambda: next(frames))
    monkeypatch.setattr(proxy, '_open_toc_row', lambda index: True)
    ticks = iter(range(100))
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: next(ticks))

    def wait(check, *args):
        assert check() is None
        assert check() is None
        return check()

    monkeypatch.setattr(proxy, '_wait', wait)
    assert proxy._read_toc_section(
        {'index': 0, 'page': 20, 'title': 'Chapter one'}, {8}) == requested


def test_section_timeout_retries_same_toc_entry(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    selected = []
    monkeypatch.setattr(proxy, '_open_toc_row',
                        lambda index: selected.append(index) or True)
    result = {'spine': 9, 'html': '<h2>Chapter one</h2><p>Text</p>'}
    attempts = []

    def wait(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise RbooksAppError('Page did not render')
        return result

    monkeypatch.setattr(proxy, '_wait', wait)
    assert proxy._read_toc_section(
        {'index': 2, 'page': 330, 'title': 'Chapter one'}, set()) == result
    assert selected == [2, 2]


def test_completeness_rejects_missing_section_or_changed_toc(monkeypatch):
    proxy = RbooksAppProxy(lambda line: None)
    rows = [{'index': 0, 'page': 9, 'title': 'Prologue'},
            {'index': 1, 'page': 19, 'title': 'Chapter one'}]
    sections = [('Prologue', '<h2>Prologue</h2><p>Text</p>'),
                ('Chapter one', '<h2>Chapter one</h2><p>Text</p>')]
    monkeypatch.setattr(proxy, '_toc_rows', lambda: rows)
    with pytest.raises(RbooksAppError, match='incomplete'):
        proxy._verify_sections('123', rows, sections[:1], {8})
    with pytest.raises(RbooksAppError, match='incomplete'):
        proxy._verify_sections('123', rows, sections, {8})
    monkeypatch.setattr(proxy, '_toc_rows', lambda: rows[::-1])
    with pytest.raises(RbooksAppError, match='changed'):
        proxy._verify_sections('123', rows, sections, {8, 9})
    monkeypatch.setattr(proxy, '_toc_rows', lambda: rows)
    assert proxy._verify_sections('123', rows, sections, {8, 9}) == {
        'bookId': '123', 'expectedSections': 2, 'verifiedSections': 2,
        'complete': True,
    }


def test_verified_cache_rejects_lost_body_section():
    result = {'_rbooksAppExportVersion': RbooksAppProxy.EXPORT_VERSION,
              '_rbooksVerification': {'bookId': '123', 'complete': True,
                                      'expectedSections': 2, 'verifiedSections': 2},
              'contentHtml': '<div class="rbooks-volume-section">One</div>'}
    assert not RbooksAppProxy.is_verified_export(result)
    result['contentHtml'] += '<div class="rbooks-volume-section">Two</div>'
    assert RbooksAppProxy.is_verified_export(result)
    result['_rbooksVerification']['complete'] = False
    assert not RbooksAppProxy.is_verified_export(result)


def test_heading_only_or_watermark_only_section_is_incomplete():
    assert not RbooksAppProxy._section_has_content(
        {'html': '<h2>Chapter one</h2><p>\u2060\u2063</p>'}, 'Chapter one')
    assert RbooksAppProxy._section_has_content(
        {'html': '<h2>Chapter one</h2><p>Actual story text.</p>'}, 'Chapter one')
    assert RbooksAppProxy._section_has_content(
        {'html': '<h2>Illustrations</h2><img src="local://image">'}, 'Illustrations')


def test_cache_from_before_codename_migration_is_rechecked():
    from external_dialog import ExternalNovelDialog
    old = {'contentHtml': '<div class="\u0072\u0069\u0064\u0069-content">'
                         '<p>Old export without verification</p></div>'}
    assert not ExternalNovelDialog._external_cacheable(old)


def test_reader_failure_is_retryable_instead_of_app_only(monkeypatch):
    import external_scraper
    from external_scraper import ExternalScraper
    monkeypatch.setattr(external_scraper.sys, 'platform', 'win32')
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    scraper._context = SimpleNamespace(new_page=lambda: None)
    monkeypatch.setattr(scraper, '_rbooks_owned_ids', lambda *args: {'123'})

    def extract(*args):
        raise RbooksAppError('Repeated section')

    monkeypatch.setattr(RbooksAppProxy, 'extract', extract)
    assert scraper._rbooks_refused_result('Volume one', object(), '123') is None
    assert any('will be retried' in line for line in logs)
    assert not any('not open it in its web viewer' in line for line in logs)


@pytest.mark.parametrize('second,cancelled,generate_on_stop,expected_output', [
    (None, False, False, False),
    ({'_locked': True, '_lockReason': 'purchase'}, False, False, True),
    (None, True, True, True),
])
def test_failed_volume_blocks_automatic_output_but_preserves_explicit_stop(
    second, cancelled, generate_on_stop, expected_output,
):
    import queue
    from external_dialog import ExternalNovelDialog
    setting = lambda value: SimpleNamespace(get=lambda: value)
    data = {'_rbooks': True, 'bookname': 'Series', 'author': 'Author',
            'chapterCount': 2, 'chapters': [{'name': 'One'}, {'name': 'Two'}]}
    generated, logs = [], []
    dialog = SimpleNamespace(
        _scraper=SimpleNamespace(parse_book=lambda url: data),
        _apply_scraper_options=lambda: None, _book_data=None,
        _msg_queue=queue.Queue(), _download_cancelled=False,
        _downloading=True, _active_generate_on_stop=generate_on_stop,
        _chapter_results=[], _var_from_enabled=setting(False),
        _var_to_enabled=setting(False),
        _format_interval_range=lambda low, high: f'{low}-{high}',
        _log=logs.append, _generate_output=lambda: generated.append(True),
    )

    def download(*args, **kwargs):
        dialog._chapter_results = [{'contentText': 'One'}, second]
        dialog._download_cancelled = cancelled
        dialog._downloading = not cancelled

    dialog._do_download = download
    ExternalNovelDialog._do_fetch_and_download(
        dialog, 'https://example.com/series', 0, 2, False)
    assert bool(generated) is expected_output
    if not expected_output:
        assert any('No partial EPUB was written' in line for line in logs)
