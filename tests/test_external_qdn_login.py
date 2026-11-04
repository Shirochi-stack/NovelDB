from types import SimpleNamespace

import external_scraper
from external_scraper import ExternalScraper
import pytest


def test_qdn_global_url_recognition_and_native_routing():
    domain = '\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com'
    book = f'https://www.{domain}/book/title_11022733006234505'
    catalog = f'https://www.{domain}/book/11022733006234505/catalog'
    chapter = f'https://www.{domain}/book/title_11022733006234505/one_31208015751346911'

    assert ExternalScraper.is_qdn_global(book)
    assert ExternalScraper.is_qdn_global(catalog)
    assert ExternalScraper.is_qdn_global(chapter)
    assert not ExternalScraper.is_qdn_global(f'https://www.{domain}/search')
    assert not ExternalScraper.is_qdn(book)

    scraper = ExternalScraper()
    message, parser = scraper._native_book_parser(book)
    assert message == '[Qdn] Detected Global URL, using native scraper.'
    assert parser == scraper._qdn_global_parse_book
    assert scraper._qdn_global_catalog_url(book) == catalog


def test_qdn_global_book_parser_uses_rendered_catalog(monkeypatch):
    domain = '\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com'
    book_url = f'https://www.{domain}/book/title_11022733006234505'
    catalog_url = f'https://www.{domain}/book/11022733006234505/catalog'
    chapter_url = (
        f'https://www.{domain}/book/title_11022733006234505/'
        'one_31208015751346911'
    )
    visits = []
    parsed = {
        'bookname': 'Lord of Mysteries',
        'author': 'Cuttlefish That Loves Diving',
        'coverUrl': 'https://book-pic.invalid/cover.jpg',
        'description': 'Synopsis',
        'language': 'en',
        'chapterCount': 1,
        'chapters': [{
            'id': '31208015751346911',
            'url': chapter_url,
            'name': '1: Crimson',
            'fullName': '1: Crimson',
            'isVIP': False,
            'isPaid': False,
            'isAccessible': True,
        }],
        'bookUrl': f'https://www.{domain}/book/11022733006234505',
    }
    page = SimpleNamespace(
        goto=lambda url, **_kwargs: visits.append(url),
        wait_for_function=lambda *_args, **_kwargs: None,
        evaluate=lambda _script: dict(parsed),
    )
    scraper = ExternalScraper(logger=lambda _message: None)
    scraper._page = page
    scraper._context = object()
    monkeypatch.setattr(scraper, '_qdn_global_parse_book_http', lambda _url: None)
    monkeypatch.setattr(scraper, '_start_qdn_global_browser', lambda _url: True)

    result = scraper._qdn_global_parse_book(book_url)

    assert visits == [catalog_url]
    assert result['_qdn_global'] is True
    assert result['chapterCount'] == 1
    assert result['chapters'][0]['url'] == chapter_url
    assert scraper._book_url == book_url


def test_qdn_global_chapter_parser_returns_text_and_reports_locks():
    scraper = ExternalScraper(logger=lambda _message: None)
    scraper._context = object()
    chapter_url = (
        'https://www.\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com/'
        'book/title_11022733006234505/one_31208015751346911'
    )
    scraper._book_url = chapter_url.rsplit('/', 1)[0]
    responses = iter([
        {
            'chapterName': '1: Crimson',
            'sourceChapterName': 'Chapter 1: Crimson',
            'chapterUrl': chapter_url,
            'contentText': 'Ouch!\nIt hurts so much!',
            'contentHtml': '<p>Ouch!</p>\n<p>It hurts so much!</p>',
            'images': [],
        },
        {'error': 'locked', 'reason': 'purchase', 'chars': 0},
    ])
    page = SimpleNamespace(
        goto=lambda *_args, **_kwargs: None,
        wait_for_function=lambda *_args, **_kwargs: None,
        evaluate=lambda _script, _name: next(responses),
    )
    scraper._page = page
    scraper._qdn_global_parse_chapter_http = lambda *_args: None

    result = scraper._qdn_global_parse_chapter(
        chapter_url, '1: Crimson', page=page
    )
    assert result['contentText'] == 'Ouch!\nIt hurts so much!'
    assert result['sourceChapterName'] == 'Chapter 1: Crimson'

    locked = scraper._qdn_global_parse_chapter(
        chapter_url, 'Paid chapter', page=page
    )
    assert locked == {
        '_locked': True,
        'chapterName': 'Paid chapter',
        '_lockReason': 'purchase',
    }


def test_qdn_global_http_html_parsers_match_public_page_shape():
    domain = '\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com'
    catalog_url = f'https://www.{domain}/book/11022733006234505/catalog'
    chapter_url = (
        f'https://www.{domain}/book/title_11022733006234505/'
        'crimson_31208015751346911'
    )
    catalog_html = f'''
      <html><head>
        <meta property="og:image" content="//book-pic.invalid/cover.jpg">
        <meta property="og:description" content="A mystery synopsis.">
      </head><body>
        <h1>Lord of Mysteries</h1>
        <div>Author: Cuttlefish That Loves Diving Translator: Atlas</div>
        <div class="volume-item"><ol><li>
          <a href="{chapter_url}"><i>1</i><strong>Crimson</strong>
            <time>7 years ago</time></a>
        </li><li>
          <a href="/book/title_11022733006234505/paid_31208036152441571">
            <i>2</i><strong>Situation</strong><svg><use href="#i-lock"></use></svg>
          </a>
        </li></ol></div>
      </body></html>
    '''
    book = ExternalScraper._qdn_global_parse_catalog_html(
        catalog_html, catalog_url
    )
    assert book['bookname'] == 'Lord of Mysteries'
    assert book['author'] == 'Cuttlefish That Loves Diving'
    assert book['chapterCount'] == 2
    assert book['chapters'][0]['name'] == '1: Crimson'
    assert book['chapters'][0]['isVIP'] is False
    assert book['chapters'][1]['isVIP'] is True

    chapter_html = '''
      <html><body><div class="chapter_content">
        <h1>Chapter 1: Crimson</h1>
        <p>Ouch!</p><p>It hurts so much!</p>
        <p>The bizarre dream shattered instantly around Zhou Mingrui.</p>
      </div></body></html>
    '''
    chapter = ExternalScraper._qdn_global_parse_chapter_html(
        chapter_html, chapter_url, '1: Crimson'
    )
    assert chapter['sourceChapterName'] == 'Chapter 1: Crimson'
    assert chapter['contentText'].startswith('Ouch!\nIt hurts so much!')
    assert '<p>Ouch!</p>' in chapter['contentHtml']


def test_qdn_global_public_book_and_batch_do_not_start_browser(monkeypatch):
    domain = '\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com'
    book_url = f'https://www.{domain}/book/title_11022733006234505'
    chapters = [
        {
            'url': f'{book_url}/chapter_{index}',
            'name': f'Chapter {index}',
            'fullName': f'Chapter {index}',
        }
        for index in (1, 2)
    ]
    book = {
        'bookname': 'Lord of Mysteries',
        'author': 'Cuttlefish That Loves Diving',
        'chapterCount': 2,
        'chapters': chapters,
    }
    scraper = ExternalScraper(logger=lambda _message: None)
    monkeypatch.setattr(
        scraper, '_qdn_global_parse_book_http', lambda _url: dict(book)
    )
    monkeypatch.setattr(
        scraper,
        '_start_qdn_global_browser',
        lambda _url: (_ for _ in ()).throw(
            AssertionError('public metadata must not start Chrome')
        ),
    )
    result = scraper._qdn_global_parse_book(book_url)
    assert result['_qdn_global'] is True

    def fetch(url, name):
        return {
            'chapterName': name,
            'sourceChapterName': name,
            'chapterUrl': url,
            'contentText': f'{name} public content',
            'contentHtml': f'<p>{name} public content</p>',
            'images': [],
        }

    monkeypatch.setattr(scraper, '_qdn_global_parse_chapter_http', fetch)
    results = scraper.parse_chapter_batch(chapters, interval=0)
    assert [item['chapterName'] for item in results] == [
        'Chapter 1', 'Chapter 2'
    ]


def test_qdn_download_opens_the_original_login_profile(monkeypatch):
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
    monkeypatch.setattr(scraper, '_start_rbooks_browser',
                        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(scraper, '_restore_storage_state',
                        lambda: (_ for _ in ()).throw(AssertionError(
                            'Stale storage backup must not replace the login'
                        )))
    monkeypatch.setattr(scraper, '_create_qdn_profile_snapshot',
                        lambda _path: (_ for _ in ()).throw(AssertionError(
                            'Qdn must not clone the encrypted login profile'
                        )))
    monkeypatch.setattr(external_scraper, 'sync_playwright',
                        lambda: SimpleNamespace(start=lambda: SimpleNamespace(
                            chromium=Chromium()
                        )))

    assert scraper._start_qdn_browser('https://www.\u0071\u0069\u0064\u0069\u0061\u006e.com/book/1/')
    assert launched == [original_profile]


def test_qdn_guest_preview_is_reported_as_login_lock():
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    result = scraper._qdn_locked_result(
        {'error': 'locked', 'reason': 'login', 'chars': 142,
         'expected': 3018},
        'ch265',
    )
    assert result == {'_locked': True, 'chapterName': 'ch265',
                      '_lockReason': 'login'}
    assert '142/3018' in logs[-1] and 'Enter Browser' in logs[-1]


@pytest.mark.parametrize('retry', ['better', 'worse', 'error'])
def test_qdn_capture_retries_once_and_saves_best_output(monkeypatch, tmp_path, retry):
    import qdn_font_decoder as decoder
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    monkeypatch.setattr(decoder, 'available', lambda: True)
    monkeypatch.setattr(scraper, '_qdn_encryption_state', lambda page: {'rendered': True, 'words': 1000})
    monkeypatch.setattr(external_scraper, '_get_base_dir', lambda: str(tmp_path))
    monkeypatch.setattr(external_scraper, '_get_app_data_dir', lambda: str(tmp_path))
    first = {'paragraphs': [], 'result': 'First output', 'missing': True}
    second = {'paragraphs': [], 'result': 'Better output' if retry == 'better' else '', 'missing': False}
    calls = []

    class Page:
        def bring_to_front(self):
            pass

        def evaluate(self, script):
            calls.append(script)
            if len(calls) == 1:
                return first
            if retry == 'error':
                raise RuntimeError('retry failed')
            return second

    def decode(payload):
        return ([payload['result']] if payload['result'] else []), {
            'decoded': 10, 'low_confidence': 1,
            'missing_fonts': [{'family': 'F'}] if payload['missing'] else [],
            'suspicious_passthrough': 2, 'unstable_paragraphs': [],
        }

    monkeypatch.setattr(decoder, 'decode_payload', decode)
    result = scraper._qdn_decode_encrypted(Page(), 'Chapter')
    assert result['contentText'] == ('Better output' if retry == 'better' else 'First output')
    assert len(calls) == 2
    assert any('WARNING: Saving best available text' in line for line in logs)
    assert any('incomplete=1' in line for line in logs)
    assert any('Replay diagnostics saved:' in line for line in logs)
    assert len(list((tmp_path / 'logs').glob('qdn-debug-*.json'))) == 1


def test_qdn_diagnostic_failure_does_not_discard_text(monkeypatch):
    import qdn_font_decoder as decoder
    scraper = ExternalScraper(logger=lambda message: None)
    monkeypatch.setattr(decoder, 'available', lambda: True)
    monkeypatch.setattr(scraper, '_qdn_encryption_state', lambda page: {'rendered': True})
    monkeypatch.setattr(decoder, 'decode_payload', lambda payload: (['Keep'], {'decoded': 1, 'low_confidence': 1}))
    monkeypatch.setattr(external_scraper, '_is_writable_dir', lambda path: False)
    page = SimpleNamespace(bring_to_front=lambda: None, evaluate=lambda script: {})
    assert scraper._qdn_decode_encrypted(page, 'Chapter')['contentText'] == 'Keep'


def test_enter_browser_session_login_survives_window_close(monkeypatch, tmp_path):
    import json
    import sys
    import types

    cdp_cookies = [
        {'name': 'ywkey', 'value': 'k', 'domain': '.\u0071\u0069\u0064\u0069\u0061\u006e.com', 'path': '/',
         'session': True, 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'},
        {'name': 'alk', 'value': 'a', 'domain': '.yuewen.com', 'path': '/',
         'session': False},
        {'name': '\u0072\u0069\u0064\u0069\u002d\u0061\u0074', 'value': 'r', 'domain': '.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com',
         'path': '/', 'session': True},
        {'name': 'KoboSession', 'value': 's', 'domain': '.kobo.com',
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
    # Session cookies for each supported site survive Enter Browser closing.
    assert scraper._cdp_snapshot_session_cookies(9222) == 3
    cdp_cookies[:] = [cdp_cookies[-1]]
    assert scraper._cdp_snapshot_session_cookies(9222) == 1
    with open(scraper._get_session_cookie_path(), encoding='utf-8') as saved:
        assert {cookie['name'] for cookie in json.load(saved)['cookies']} == {
            'ywkey', '\u0072\u0069\u0064\u0069\u002d\u0061\u0074', 'KoboSession'
        }

    added = []

    class Context:
        def cookies(self, *_args):
            return [{'name': '_csrfToken', 'domain': '.\u0071\u0069\u0064\u0069\u0061\u006e.com'}]

        def add_cookies(self, cookies):
            added.extend(cookies)

    scraper._context = Context()
    assert scraper._restore_session_cookies('\u0071\u0069\u0064\u0069\u0061\u006e.com') == 1
    assert added == [{'name': 'ywkey', 'value': 'k', 'domain': '.\u0071\u0069\u0064\u0069\u0061\u006e.com',
                      'path': '/', 'httpOnly': True, 'secure': False,
                      'sameSite': 'Lax'}]
    assert scraper._restore_session_cookies('\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com') == 1
    assert added[-1] == {'name': '\u0072\u0069\u0064\u0069\u002d\u0061\u0074', 'value': 'r',
                         'domain': '.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com', 'path': '/',
                         'httpOnly': False, 'secure': False}
    assert scraper._restore_session_cookies('kobo.com') == 1
    assert added[-1] == {'name': 'KoboSession', 'value': 's',
                         'domain': '.kobo.com', 'path': '/',
                         'httpOnly': False, 'secure': False}
    # A cookie the profile already holds is newer and is never replaced.
    Context.cookies = lambda self, *_args: [
        {'name': 'ywkey', 'domain': '.\u0071\u0069\u0064\u0069\u0061\u006e.com'}]
    assert scraper._restore_session_cookies('\u0071\u0069\u0064\u0069\u0061\u006e.com') == 0


def test_qdn_prefers_headed_installed_chrome_and_restores_login(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    calls = []
    scripts = []

    def headed(url, site='Rbooks'):
        calls.append((url, site))
        scraper._context = SimpleNamespace(add_init_script=scripts.append)
        scraper._page = SimpleNamespace(evaluate=lambda _script: 1)
        return True

    monkeypatch.setattr(scraper, '_start_rbooks_browser', headed)
    monkeypatch.setattr(scraper, '_start_qdn_headless', lambda _url: (
        _ for _ in ()).throw(AssertionError('headless must not run')))
    restored = []
    monkeypatch.setattr(scraper, '_restore_session_cookies',
                        lambda domain: restored.append(domain) or 1)

    assert scraper._start_qdn_browser('https://www.\u0071\u0069\u0064\u0069\u0061\u006e.com/book/1/')
    assert calls == [('https://www.\u0071\u0069\u0064\u0069\u0061\u006e.com/book/1/', 'Qdn')]
    assert restored == ['\u0071\u0069\u0064\u0069\u0061\u006e.com', 'yuewen.com']
    assert scripts and '__npiaFontFaces' in scripts[0]


def test_qdn_does_not_fall_back_while_enter_browser_is_open(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    monkeypatch.setattr(scraper, '_start_rbooks_browser', lambda *a, **k: False)
    monkeypatch.setattr(scraper, '_chrome_processes_using_profile',
                        lambda _path: [1234])
    monkeypatch.setattr(scraper, '_start_qdn_headless', lambda _url: (
        _ for _ in ()).throw(AssertionError('headless must not run')))
    assert not scraper._start_qdn_browser('https://www.\u0071\u0069\u0064\u0069\u0061\u006e.com/book/1/')


def test_expired_saved_login_is_cleared_so_qdn_can_renew_it(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    cleared, scripts = [], []
    scraper._context = SimpleNamespace(
        clear_cookies=lambda name=None: cleared.append(name),
        add_init_script=scripts.append,
    )
    monkeypatch.setattr(scraper, '_restore_session_cookies', lambda domain: 1)
    monkeypatch.setattr(scraper, '_qdn_session_live', lambda: False)
    scraper._prepare_qdn_context()
    assert cleared == ['ywkey', 'ywguid', 'ywopenid']

    cleared.clear()
    monkeypatch.setattr(scraper, '_qdn_session_live', lambda: True)
    scraper._prepare_qdn_context()
    assert cleared == []


def test_chapter_waits_out_qdn_firewall_then_retries(monkeypatch):
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    visits = []
    page = SimpleNamespace(goto=lambda url, **_kwargs: visits.append(url))
    scraper._context, scraper._page = object(), page
    blocked = iter([True, True, False])
    monkeypatch.setattr(scraper, '_qdn_blocked', lambda _page: next(blocked))
    monkeypatch.setattr(scraper, '_QDN_MIN_GAP', 0)
    monkeypatch.setattr(scraper, '_QDN_WAF_COOLDOWNS', (0, 0, 0))
    monkeypatch.setattr(scraper, '_qdn_wait_for_chapter', lambda _page: True)
    monkeypatch.setattr(scraper, '_qdn_is_encrypted', lambda _page: True)
    monkeypatch.setattr(scraper, '_qdn_decode_encrypted',
                        lambda _page, name: {'chapterName': name, 'contentText': 'ok'})
    result = scraper._qdn_parse_chapter('https://www.\u0071\u0069\u0064\u0069\u0061\u006e.com/chapter/1/2/', 'ch')
    assert result['contentText'] == 'ok'
    assert len(visits) == 3
    assert sum('firewall' in line for line in logs) == 2


def test_chapter_gives_up_after_every_cooldown(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    scraper._context = object()
    scraper._page = SimpleNamespace(goto=lambda url, **_kwargs: None)
    monkeypatch.setattr(scraper, '_qdn_blocked', lambda _page: True)
    monkeypatch.setattr(scraper, '_QDN_MIN_GAP', 0)
    monkeypatch.setattr(scraper, '_QDN_WAF_COOLDOWNS', (0, 0))
    assert scraper._qdn_parse_chapter('https://www.\u0071\u0069\u0064\u0069\u0061\u006e.com/chapter/1/2/', 'ch') is None


def test_batch_rereads_blocked_or_unfinished_tabs(monkeypatch):
    scraper = ExternalScraper(logger=lambda _message: None)
    pages = [SimpleNamespace(goto=lambda url, **_kwargs: None, name=str(i))
             for i in range(3)]
    monkeypatch.setattr(scraper, '_qdn_parallel_pages', lambda count, url: pages)
    monkeypatch.setattr(scraper, '_QDN_MIN_GAP', 0)
    monkeypatch.setattr(scraper, '_qdn_chapter_ready', lambda _page: True)
    monkeypatch.setattr(scraper, '_qdn_blocked', lambda page: page.name == '1')
    extracted = {'0': {'contentText': 'a'},
                 '2': {'_locked': True, '_verification_required': True}}
    monkeypatch.setattr(scraper, '_qdn_extract_loaded_chapter',
                        lambda page, name: extracted[page.name])
    reread = []
    monkeypatch.setattr(scraper, '_qdn_parse_chapter',
                        lambda url, name: reread.append(url) or {'contentText': url})
    batch = [{'url': f'u{i}', 'name': f'c{i}'} for i in range(3)]
    results = scraper._qdn_parse_chapter_batch_parallel(batch)
    assert reread == ['u1', 'u2']
    assert [r['contentText'] for r in results] == ['a', 'u1', 'u2']


def test_qdn_global_chapter_images_are_what_the_epub_writer_takes():
    from external_scraper import ExternalScraper

    cover = 'https://book-pic.example/bookcover/11022733006234505?imageMogr2/thumbnail/150x'
    picture = 'https://img.example/chapter/1.png'
    data = ExternalScraper._qdn_global_chapter_images(
        {'contentHtml': '<p>Text</p>', 'images': [cover, picture, picture]})
    assert data['images'] == [{'url': picture, 'name': 'qdn_global_001.jpg'}]
    assert data['contentHtml'] == f'<p>Text</p>\n<p><img src="{picture}" alt=""/></p>'
    plain = ExternalScraper._qdn_global_chapter_images({'contentHtml': '<p>Text</p>', 'images': [cover]})
    assert plain['images'] == [] and plain['contentHtml'] == '<p>Text</p>'
