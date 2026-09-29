from types import SimpleNamespace

import external_scraper
from external_scraper import ExternalScraper


def test_qidian_download_opens_the_original_login_profile(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    original_profile = 'saved-login-profile'
    launched = []

    class Page:
        def on(self, *_args):
            pass

    class Context:
        pages = []

        def add_init_script(self, *_args, **_kwargs):
            pass

        def new_page(self):
            return Page()

    class Chromium:
        def launch_persistent_context(self, path, **_kwargs):
            launched.append(path)
            return Context()

    monkeypatch.setattr(scraper, '_get_user_data_dir',
                        lambda: original_profile)
    monkeypatch.setattr(scraper, '_chrome_processes_using_profile',
                        lambda _path: [])
    # Installed Chrome is unavailable, so the headless fallback runs.
    monkeypatch.setattr(scraper, '_start_ridi_browser',
                        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(scraper, '_restore_storage_state',
                        lambda: (_ for _ in ()).throw(AssertionError(
                            'Stale storage backup must not replace the login'
                        )))
    monkeypatch.setattr(scraper, '_create_qidian_profile_snapshot',
                        lambda _path: (_ for _ in ()).throw(AssertionError(
                            'Qidian must not clone the encrypted login profile'
                        )))
    monkeypatch.setattr(external_scraper, 'sync_playwright',
                        lambda: SimpleNamespace(start=lambda: SimpleNamespace(
                            chromium=Chromium()
                        )))

    assert scraper._start_qidian_browser('https://www.qidian.com/book/1/')
    assert launched == [original_profile]


def test_qidian_guest_preview_is_reported_as_login_lock():
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    result = scraper._qidian_locked_result(
        {'error': 'locked', 'reason': 'login', 'chars': 142,
         'expected': 3018},
        'ch265',
    )
    assert result == {'_locked': True, 'chapterName': 'ch265',
                      '_lockReason': 'login'}
    assert '142/3018' in logs[-1] and 'Enter Browser' in logs[-1]


def test_enter_browser_session_login_survives_window_close(monkeypatch, tmp_path):
    import json
    import sys
    import types

    cdp_cookies = [
        {'name': 'ywkey', 'value': 'k', 'domain': '.qidian.com', 'path': '/',
         'session': True, 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'},
        {'name': 'alk', 'value': 'a', 'domain': '.yuewen.com', 'path': '/',
         'session': False},
        {'name': 'ridi-at', 'value': 'r', 'domain': '.ridibooks.com',
         'path': '/', 'session': True},
    ]

    class Socket:
        def send(self, _message):
            pass

        def recv(self):
            return json.dumps({'id': 1, 'result': {'cookies': cdp_cookies}})

        def close(self):
            pass

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"webSocketDebuggerUrl": "ws://127.0.0.1/devtools"}'

    monkeypatch.setitem(sys.modules, 'websocket', types.SimpleNamespace(
        create_connection=lambda *_args, **_kwargs: Socket()))
    monkeypatch.setattr(external_scraper.urllib.request, 'urlopen',
                        lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(ExternalScraper, '_get_user_data_dir',
                        staticmethod(lambda: str(tmp_path)))

    scraper = ExternalScraper()
    # Only the Qidian session cookie is kept; persistent ones are on disk.
    assert scraper._cdp_snapshot_session_cookies(9222) == 1

    added = []

    class Context:
        def cookies(self, *_args):
            return [{'name': '_csrfToken', 'domain': '.qidian.com'}]

        def add_cookies(self, cookies):
            added.extend(cookies)

    scraper._context = Context()
    assert scraper._restore_session_cookies('qidian.com') == 1
    assert added == [{'name': 'ywkey', 'value': 'k', 'domain': '.qidian.com',
                      'path': '/', 'httpOnly': True, 'secure': False,
                      'sameSite': 'Lax'}]
    # A cookie the profile already holds is newer and is never replaced.
    Context.cookies = lambda self, *_args: [
        {'name': 'ywkey', 'domain': '.qidian.com'}]
    assert scraper._restore_session_cookies('qidian.com') == 0


def test_qidian_prefers_headed_installed_chrome_and_restores_login(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    calls = []
    scripts = []

    def headed(url, site='Ridi'):
        calls.append((url, site))
        scraper._context = SimpleNamespace(add_init_script=scripts.append)
        scraper._page = SimpleNamespace(evaluate=lambda _script: 1)
        return True

    monkeypatch.setattr(scraper, '_start_ridi_browser', headed)
    monkeypatch.setattr(scraper, '_start_qidian_headless', lambda _url: (
        _ for _ in ()).throw(AssertionError('headless must not run')))
    restored = []
    monkeypatch.setattr(scraper, '_restore_session_cookies',
                        lambda domain: restored.append(domain) or 1)

    assert scraper._start_qidian_browser('https://www.qidian.com/book/1/')
    assert calls == [('https://www.qidian.com/book/1/', 'Qidian')]
    assert restored == ['qidian.com', 'yuewen.com']
    assert scripts and '__npiaFontFaces' in scripts[0]


def test_qidian_does_not_fall_back_while_enter_browser_is_open(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    monkeypatch.setattr(scraper, '_start_ridi_browser', lambda *a, **k: False)
    monkeypatch.setattr(scraper, '_chrome_processes_using_profile',
                        lambda _path: [1234])
    monkeypatch.setattr(scraper, '_start_qidian_headless', lambda _url: (
        _ for _ in ()).throw(AssertionError('headless must not run')))
    assert not scraper._start_qidian_browser('https://www.qidian.com/book/1/')
