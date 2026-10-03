import queue
from types import SimpleNamespace

import pytest

import browser_profile
from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper


@pytest.mark.parametrize('argument', [
    '--user-data-dir="{profile}"',
    '"--user-data-dir={profile}"',
    '--user-data-dir "{profile}"',
    '--user-data-dir={profile}',
])
def test_closing_selects_exact_profile_and_its_browser_children(tmp_path, argument):
    profile = str(tmp_path / 'saved')
    records = [
        {'pid': 10, 'parent': 1, 'name': 'chrome.exe',
         'command': 'chrome ' + argument.format(profile=profile)},
        {'pid': 11, 'parent': 10, 'name': 'chrome.exe',
         'command': 'chrome --type=renderer'},
        {'pid': 12, 'parent': 11, 'name': 'chrome.exe',
         'command': 'chrome --type=utility'},
        {'pid': 20, 'parent': 10, 'name': 'chrome.exe',
         'command': f'chrome --user-data-dir="{profile}-other"'},
        {'pid': 21, 'parent': 20, 'name': 'chrome.exe',
         'command': 'chrome --type=renderer'},
        {'pid': 30, 'parent': 10, 'name': 'unrelated.exe',
         'command': f'--user-data-dir="{profile}"'},
        {'pid': 31, 'parent': 1, 'name': 'chrome.exe',
         'command': f'chrome --some-other-flag="{profile}"'},
    ]
    assert [record['pid'] for record in
            browser_profile._select_profile_processes(records, profile)] == [
                10, 11, 12,
            ]


def _profile_shutdown(monkeypatch, states):
    logs, events = [], []
    scraper = ExternalScraper(logger=logs.append)
    sequence = iter(states)
    monkeypatch.setattr(browser_profile, 'profile_processes',
                        lambda path: next(sequence))
    monkeypatch.setattr(browser_profile, 'close_profile_windows',
                        lambda records: events.append(('windows', records)))
    monkeypatch.setattr(browser_profile, 'terminate_profile_processes',
                        lambda records: events.append(('terminate', records)))
    monkeypatch.setattr(scraper, '_cdp_snapshot_session_cookies',
                        lambda port: events.append(('cookies', port)))
    monkeypatch.setattr(scraper, '_request_cdp_browser_close',
                        lambda port: events.append(('close', port)))
    monkeypatch.setattr('external_scraper.time.sleep', lambda seconds: None)
    return scraper, logs, events


def test_download_saves_login_before_graceful_close(monkeypatch):
    records = [{'pid': 10, 'command': '--remote-debugging-port=9222'}]
    scraper, logs, events = _profile_shutdown(monkeypatch, [records, []])
    assert scraper._close_chrome_profile_processes('saved') == []
    assert events == [('cookies', 9222), ('close', 9222), ('windows', records)]
    assert any('continuing Download' in line for line in logs)


def test_download_terminates_only_the_remaining_profile_processes(monkeypatch):
    records = [{'pid': 10, 'command': ''}, {'pid': 11, 'command': ''}]
    leftovers = [records[1]]
    scraper, logs, events = _profile_shutdown(
        monkeypatch, [records, leftovers, []])
    ticks = iter([0, 0, 7, 7, 7])
    monkeypatch.setattr('external_scraper.time.monotonic', lambda: next(ticks))
    assert scraper._close_chrome_profile_processes('saved') == []
    assert ('terminate', leftovers) in events
    assert any('Terminating' in line and '11' in line for line in logs)


def test_download_does_not_relaunch_with_a_still_locked_profile(monkeypatch):
    scraper = ExternalScraper()
    monkeypatch.setattr(scraper, '_close_chrome_profile_processes',
                        lambda path: [10])
    with pytest.raises(RuntimeError, match='could not be closed: 10'):
        scraper.prepare_download_browser()


def test_download_keeps_its_working_browser_context(monkeypatch):
    scraper = ExternalScraper()
    evaluated = []
    scraper._context = object()
    scraper._page = SimpleNamespace(evaluate=evaluated.append)
    monkeypatch.setattr(scraper, '_close_chrome_profile_processes',
                        lambda path: pytest.fail('Closed active worker session'))
    scraper.prepare_download_browser()
    assert evaluated == ['1']


@pytest.mark.parametrize('batch', [False, True])
def test_download_button_releases_profile_before_parsing(monkeypatch, batch):
    events = []
    scraper = SimpleNamespace(
        prepare_download_browser=lambda: events.append('release'),
        parse_book=lambda url: events.append('parse') or None,
    )
    # Select a native site so this GUI regression needs no real browser.
    scraper.__dict__.update({
        name: (lambda url, chosen=name: chosen == 'is_rbooks')
        for name in (
            'is_ntk_novel', 'is_qdn', 'is_yeduji', 'is_1qxs', 'is_69shuba',
            'is_floo', 'is_xiyuwx', 'is_global_npia', 'is_rbooks', 'is_kobo',
            'is_mpia', 'is_jara', 'is_nweb_series', 'is_nweb_novel', 'is_npia',
        )
    })
    dialog = SimpleNamespace(
        _scraper=scraper, _apply_scraper_options=lambda: None,
        _msg_queue=queue.Queue(), _get_output_dir=lambda: '.',
        _downloading=True, _log=lambda line: None,
    )
    if batch:
        ExternalNovelDialog._do_batch(
            dialog, (['https://example.com/book'], 0, 1, False, 1))
    else:
        ExternalNovelDialog._do_fetch_and_download(
            dialog, 'https://example.com/book', 0, 1, False)
    assert events == ['release', 'parse']
