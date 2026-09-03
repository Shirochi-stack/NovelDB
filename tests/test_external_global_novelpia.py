import threading
import time
from types import SimpleNamespace

from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


def test_global_novelpia_url_detection_does_not_use_korean_scraper():
    novel_url = 'https://global.novelpia.com/novel/123?ref=home'
    viewer_url = 'https://global.novelpia.com/viewer/456'

    assert ExternalScraper.is_global_novelpia(novel_url)
    assert not ExternalScraper.is_global_novelpia(viewer_url)
    assert not ExternalScraper.is_novelpia(novel_url)
    assert ExternalScraper.is_novelpia('https://novelpia.com/novel/123')
    assert not ExternalScraper.is_global_novelpia(
        'https://global.novelpia.com/search'
    )


def test_global_novelpia_book_uses_api_metadata_and_ascending_episode_order():
    class Page:
        def goto(self, *_args, **_kwargs):
            raise AssertionError('anonymous book discovery must not load the site')

    scraper, messages = make_scraper()
    scraper._page = Page()
    session = object()
    session_modes = []

    def ensure_session(refresh_login=False):
        session_modes.append(refresh_login)
        return session

    scraper._global_novelpia_ensure_session = ensure_session

    def request_json(_session, url, params=None, **_kwargs):
        assert _session is session
        assert _kwargs.get('login_at') is None
        if url.endswith('/v1/novel'):
            assert params == {'novel_no': '123'}
            return 200, {
                'result': {
                    'novel': {
                        'novel_name': 'Global Example',
                        'novel_story': 'Line one\nLine two',
                        'novel_full_img': '//images.example/cover.jpg',
                        'flag_complete': 1,
                        'cp_name': 'Publisher',
                        'tag_list': [{'tag_name': 'Magic'}],
                    },
                    'writer_list': [{'writer_name': 'Author'}],
                    'info': {'epi_cnt': 2},
                    'cate_list': [{'cate_name': 'Fantasy'}],
                }
            }
        assert url.endswith('/v1/novel/episode/list')
        assert params == {'novel_no': '123', 'rows': 2, 'sort': 'ASC'}
        return 200, {
            'result': {
                'list': [
                    {
                        'episode_no': 110,
                        'epi_num': '10',
                        'epi_title': 'Tenth',
                    },
                    {
                        'episode_no': 102,
                        'epi_num': '2',
                        'epi_title': 'Second',
                    },
                ]
            }
        }

    scraper._global_novelpia_request_json = request_json

    book = scraper.parse_book('https://global.novelpia.com/novel/123')

    assert book['_global_novelpia'] is True
    assert book['bookname'] == 'Global Example'
    assert book['author'] == 'Author'
    assert book['publisher'] == 'Publisher'
    assert book['status'] == 'Completed'
    assert book['coverUrl'] == 'https://images.example/cover.jpg'
    assert book['tags'] == ['Fantasy', 'Magic']
    assert '<br/>' in book['introductionHTML']
    assert [chapter['id'] for chapter in book['chapters']] == ['102', '110']
    assert [
        chapter['_globalNovelpiaChapterNumber']
        for chapter in book['chapters']
    ] == [2, 10]
    assert session_modes == [False]
    assert any('site can respond slowly' in message for message in messages)
    assert any(
        'using anonymous access without an account check' in message
        for message in messages
    )


def test_global_novelpia_saved_login_is_ready_before_book_download():
    import requests

    scraper, messages = make_scraper()
    session = requests.Session()
    session.cookies.set('USERKEY', 'saved-user', domain='.novelpia.com')
    session.cookies.set('TKEY', 'saved-token', domain='.novelpia.com')
    scraper._global_novelpia_ensure_session = lambda **_kwargs: session
    refreshes = []

    def refresh_login(*_args):
        refreshes.append(True)
        scraper._global_novelpia_login_at = 'api-login-token'
        return True

    scraper._global_novelpia_refresh_login = refresh_login
    request_tokens = []

    def request_json(_session, url, login_at=None, **_kwargs):
        request_tokens.append(login_at)
        if url.endswith('/v1/novel'):
            return 200, {
                'result': {
                    'novel': {'novel_name': 'Authenticated Book'},
                    'info': {'epi_cnt': 1},
                }
            }
        return 200, {
            'result': {
                'list': [
                    {'episode_no': 1, 'epi_num': 1, 'epi_title': 'Paid'}
                ]
            }
        }

    scraper._global_novelpia_request_json = request_json

    book = scraper._global_novelpia_parse_book(
        'https://global.novelpia.com/novel/123'
    )

    assert book['chapterCount'] == 1
    assert refreshes == [True]
    assert request_tokens == ['api-login-token', 'api-login-token']
    assert any(
        'Using the saved authenticated browser session' in message
        for message in messages
    )


def test_global_novelpia_current_profile_cookies_replace_stale_snapshot():
    import requests

    scraper, _messages = make_scraper()
    scraper._storage_cookies_for_url = lambda _url: [{
        'name': 'TKEY',
        'value': 'stale-token',
        'domain': '.novelpia.com',
        'path': '/',
    }]
    scraper._global_novelpia_profile_cookies = lambda: [
        {
            'name': 'USERKEY',
            'value': 'current-user',
            'domain': '.novelpia.com',
            'path': '/',
        },
        {
            'name': 'TKEY',
            'value': 'current-token',
            'domain': '.novelpia.com',
            'path': '/',
        },
    ]
    session = requests.Session()

    copied = scraper._global_novelpia_sync_browser_cookies(session)
    cookies = session.cookies.get_dict(domain='.novelpia.com')

    assert copied == 2
    assert cookies['USERKEY'] == 'current-user'
    assert cookies['TKEY'] == 'current-token'


def test_global_novelpia_api_uses_extended_slow_site_timeout():
    calls = []

    class Response:
        status_code = 200
        text = ''

        @staticmethod
        def json():
            return {'result': {}}

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    scraper, _messages = make_scraper()
    status, payload = scraper._global_novelpia_request_json(
        Session(),
        'https://api-global.novelpia.com/v1/novel',
        max_retries=1,
    )

    assert status == 200
    assert payload == {'result': {}}
    assert calls[0][1]['timeout'] == 90


def test_global_novelpia_spoiler_shield_persists_across_continue_reload():
    scripts = []

    class Context:
        def add_init_script(self, script):
            scripts.append(script)

    assert ExternalScraper._global_novelpia_install_spoiler_shield(Context())
    assert len(scripts) == 1
    assert '__npia_global_spoiler_shield' in scripts[0]
    assert '__npia-global-spoiler-shield' in scripts[0]
    assert 'Advertisement complete. Preparing the chapter' in scripts[0]
    assert "sessionStorage.getItem(marker) === '1'" in scripts[0]


def test_global_novelpia_continue_is_exact_and_shields_before_click():
    scripts = []

    class Page:
        def evaluate(self, script):
            scripts.append(script)
            return True

    assert ExternalScraper._global_novelpia_click_ad_continue(Page())
    script = scripts[0]
    assert "!== 'continue'" in script
    assert "window.__npiaInstallSpoilerShield();" in script
    assert script.index('__npiaInstallSpoilerShield();') < script.index(
        'button.click();'
    )


def test_global_novelpia_continue_locator_pierces_shadow_dom_and_shields_first():
    events = []

    class Candidate:
        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def click(timeout):
            events.append(('click', timeout))

        @staticmethod
        def evaluate(_script):
            raise AssertionError('normal locator click should succeed')

    class Matches:
        @staticmethod
        def count():
            return 1

        @staticmethod
        def nth(index):
            assert index == 0
            return Candidate()

    class Page:
        @staticmethod
        def get_by_role(role, name):
            events.append(('locate', role, name.pattern))
            return Matches()

        @staticmethod
        def evaluate(_script):
            events.append(('shield',))
            return True

    assert ExternalScraper._global_novelpia_click_ad_continue(Page())
    assert events[0] == ('locate', 'button', r'^\s*continue\s*$')
    assert events[1] == ('shield',)
    assert events[2] == ('click', 1500)


def test_global_novelpia_next_ad_clears_marker_but_keeps_current_shield():
    scripts = []

    class Page:
        def evaluate(self, script):
            scripts.append(script)
            return True

    assert ExternalScraper._global_novelpia_prepare_ad_navigation(Page())
    assert "sessionStorage.removeItem('__npia_global_spoiler_shield')" in scripts[0]
    assert '.remove()' not in scripts[0]


def test_global_novelpia_ad_gate_is_not_retried_as_a_transient_500():
    calls = []

    class Response:
        status_code = 500
        text = ''

        @staticmethod
        def json():
            return {
                'code': '0010',
                'result': {
                    'name': 'NOVEL_ERROR',
                    'message': 'This episode has a basic advertisement.',
                },
            }

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    scraper, _messages = make_scraper()
    status, payload = scraper._global_novelpia_request_json(
        Session(),
        'https://api-global.novelpia.com/v1/novel/episode',
        max_retries=4,
    )

    assert status == 500
    assert ExternalScraper._global_novelpia_ad_required(status, payload)
    assert len(calls) == 1


def test_global_novelpia_session_does_not_probe_account_by_default():
    scraper, _messages = make_scraper()
    scraper._global_novelpia_sync_browser_cookies = lambda _session: 0
    scraper._global_novelpia_refresh_login = lambda *_args: (_ for _ in ()).throw(
        AssertionError('account refresh must be lazy')
    )

    session = scraper._global_novelpia_ensure_session()

    assert session is not None
    assert scraper._global_novelpia_login_at == ''
    assert scraper._global_novelpia_refresh_attempted is False


def test_global_novelpia_ticket_and_segment_helpers_follow_api_shapes():
    token, direct_url = ExternalScraper._global_novelpia_extract_ticket_token({
        'result': {'nested': {'_t': 'header.payload.signature'}}
    })
    assert token == 'header.payload.signature'
    assert direct_url == ''

    raw = ExternalScraper._global_novelpia_raw_content({
        'result': {
            'data': {
                'epi_content10': '<p>ten</p>',
                'epi_content2': '<p>two</p>',
                'epi_content': '<p>first</p>',
            }
        }
    })
    assert raw == '<p>first</p><p>two</p><p>ten</p>'


def test_global_novelpia_normalizes_lazy_and_background_images():
    scraper, _messages = make_scraper()
    signed = {
        'CloudFront-Policy': 'policy',
        'CloudFront-Key-Pair-Id': 'key-id',
        'CloudFront-Signature': 'signature',
    }
    raw_html = '''
        <p>Hello</p>
        <picture>
          <source srcset="//pv-gn.novelpia.com/small.webp 1x,
                          //pv-gn.novelpia.com/large.webp 2x">
          <img data-src="/fallback.jpg" src="blank.gif">
        </picture>
        <div style="background-image: url('/background.jpg')">Panel</div>
        <style>.hero { background: url('//pv-gn.novelpia.com/hero.png'); }</style>
        <script>throw new Error('do not keep me')</script>
    '''

    result = scraper._global_novelpia_build_chapter_result(
        raw_html,
        'Episode 2',
        'https://global.novelpia.com/viewer/102',
        signed,
    )

    urls = [image['url'] for image in result['images']]
    assert 'https://pv-gn.novelpia.com/large.webp' in urls
    assert 'https://global.novelpia.com/background.jpg' in urls
    assert 'https://pv-gn.novelpia.com/hero.png' in urls
    assert 'small.webp' not in result['contentHtml']
    assert '<script' not in result['contentHtml']
    protected = [
        image for image in result['images']
        if image['url'].startswith('https://pv-gn.novelpia.com/')
    ]
    public = [
        image for image in result['images']
        if image['url'] == 'https://global.novelpia.com/background.jpg'
    ]
    assert protected and all(image['_cookies'] == signed for image in protected)
    assert public and '_cookies' not in public[0]


def test_global_novelpia_chapter_fetches_ticket_content_and_signed_images():
    scraper, messages = make_scraper()

    class Session:
        def close(self):
            return None

    scraper._global_novelpia_ensure_session = lambda *args, **kwargs: Session()
    scraper._global_novelpia_clone_session = lambda: Session()
    scraper._global_novelpia_login_at = 'login-token'
    calls = []

    def request_json(_session, url, params=None, login_at=None, **_kwargs):
        calls.append((url, params, login_at))
        if url.endswith('/v1/novel/episode'):
            return 200, {
                'result': {
                    '_t': 'header.payload.signature',
                    'signed_key': {
                        'CloudFront-Policy': 'policy',
                        'CloudFront-Key-Pair-Id': 'key-id',
                        'CloudFront-Signature': 'signature',
                    },
                }
            }
        return 200, {
            'result': {
                'data': {
                    'epi_content': (
                        '<p>Body</p><img '
                        'src="https://pv-gn.novelpia.com/chapter.jpg">'
                    )
                }
            }
        }

    scraper._global_novelpia_request_json = request_json

    result = scraper._global_novelpia_parse_chapter(
        'https://global.novelpia.com/viewer/456',
        'Episode 4',
    )

    assert result['chapterName'] == 'Episode 4'
    assert result['contentText'] == 'Body'
    assert result['images'][0]['_cookies']['CloudFront-Policy'] == 'policy'
    assert calls[0][1] == {'episode_no': '456'}
    assert calls[0][2] == 'login-token'
    assert calls[1][1] == {'_t': 'header.payload.signature'}
    assert not any('[Global Novelpia] OK:' in message for message in messages)


def test_global_novelpia_ad_completion_retries_ticket_then_downloads():
    scraper, _messages = make_scraper()

    class Session:
        def close(self):
            return None

    scraper._global_novelpia_ensure_session = lambda *args, **kwargs: Session()
    scraper._global_novelpia_clone_session = lambda: Session()
    scraper._book_data = {'_global_novelpia_novel_id': '4770'}
    ad_calls = []
    ticket_calls = []

    def complete_ad(novel_no, episode_no, chapter_name):
        ad_calls.append((novel_no, episode_no, chapter_name))
        return True

    def request_json(_session, url, params=None, **_kwargs):
        if url.endswith('/v1/novel/episode'):
            ticket_calls.append(params)
            if len(ticket_calls) == 1:
                return 500, {
                    'code': '0010',
                    'result': {
                        'name': 'NOVEL_ERROR',
                        'message': 'This episode has a basic advertisement.',
                        'data': {'novel_no': 4770},
                    },
                }
            return 200, {'result': {'_t': 'ticket'}}
        return 200, {'result': {'data': {'epi_content': '<p>Unlocked</p>'}}}

    scraper._global_novelpia_complete_ad = complete_ad
    scraper._global_novelpia_request_json = request_json

    result = scraper._global_novelpia_parse_chapter(
        'https://global.novelpia.com/viewer/670412',
        '36 - Example',
    )

    assert result['contentText'] == 'Unlocked'
    assert ad_calls == [('4770', '670412', '36 - Example')]
    assert ticket_calls == [
        {'episode_no': '670412'},
        {'episode_no': '670412'},
    ]


def test_global_novelpia_unconfirmed_ad_returns_non_retrying_skip_marker():
    scraper, _messages = make_scraper()

    class Session:
        def close(self):
            return None

    scraper._global_novelpia_ensure_session = lambda *args, **kwargs: Session()
    scraper._global_novelpia_clone_session = lambda: Session()
    scraper._global_novelpia_complete_ad = lambda *_args: False
    scraper._global_novelpia_request_json = lambda *_args, **_kwargs: (
        500,
        {
            'code': '0010',
            'result': {
                'name': 'NOVEL_ERROR',
                'message': 'This episode has a basic advertisement.',
            },
        },
    )

    result = scraper._global_novelpia_parse_chapter(
        'https://global.novelpia.com/viewer/670412',
        '36 - Example',
    )

    assert result == {
        '_locked': True,
        '_ad_required': True,
        'chapterName': '36 - Example',
    }


def test_global_novelpia_parallel_ad_requests_share_one_owner_thread():
    scraper, _messages = make_scraper()
    owner_threads = []
    active = 0
    peak_active = 0
    guard = threading.Lock()

    def run_ad(request):
        nonlocal active, peak_active
        with guard:
            active += 1
            peak_active = max(peak_active, active)
            owner_threads.append(threading.get_ident())
        time.sleep(0.02)
        with guard:
            active -= 1
        return bool(request['episode_no'])

    scraper._global_novelpia_run_ad_request = run_ad
    results = []

    def request_ad(episode):
        results.append(scraper._global_novelpia_complete_ad(
            '4770', episode, f'Episode {episode}'
        ))

    callers = [
        threading.Thread(target=request_ad, args=(str(index),))
        for index in range(1, 5)
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()
    scraper._global_novelpia_shutdown_ad_worker()

    assert results == [True, True, True, True]
    assert len(set(owner_threads)) == 1
    assert peak_active == 1


def test_global_novelpia_denied_ticket_is_reported_as_locked():
    scraper, messages = make_scraper()

    class Session:
        def close(self):
            return None

    scraper._global_novelpia_ensure_session = lambda *args, **kwargs: Session()
    scraper._global_novelpia_clone_session = lambda: Session()
    scraper._global_novelpia_refresh_login = lambda *_args: False
    scraper._global_novelpia_request_json = lambda *_args, **_kwargs: (
        403,
        {'message': 'Purchase required'},
    )

    result = scraper._global_novelpia_parse_chapter(
        'https://global.novelpia.com/viewer/999',
        'Premium episode',
    )

    assert result == {'_locked': True, 'chapterName': 'Premium episode'}
    assert any('LOCKED or login required' in message for message in messages)


def test_global_novelpia_batch_uses_native_api_path():
    scraper, _messages = make_scraper()
    scraper._book_data = {'_global_novelpia': True}
    chapters = [{'url': 'https://global.novelpia.com/viewer/1', 'name': 'One'}]
    calls = []

    def fake_batch(value, success_callback=None):
        calls.append(value)
        result = {'chapterName': 'One', 'contentHtml': '<p>ok</p>'}
        success_callback(0, result)
        return [result]

    scraper._global_novelpia_parse_chapter_batch = fake_batch

    completed = []
    result = scraper.parse_chapter_batch(
        chapters,
        interval=0,
        success_callback=lambda index, data: completed.append(
            (index, data['chapterName'])
        ),
    )

    assert result[0]['chapterName'] == 'One'
    assert calls == [chapters]
    assert completed == [(0, 'One')]


def test_global_novelpia_batch_reports_workers_in_completion_order():
    scraper, _messages = make_scraper()
    scraper._global_novelpia_ensure_session = lambda: object()

    def fake_parse(_url, name):
        if name == 'One':
            time.sleep(0.05)
        return {'chapterName': f'Viewer {name}', 'contentHtml': '<p>ok</p>'}

    scraper._global_novelpia_parse_chapter = fake_parse
    completed = []
    results = scraper._global_novelpia_parse_chapter_batch(
        [
            {'url': 'https://global.novelpia.com/viewer/1', 'name': 'One'},
            {'url': 'https://global.novelpia.com/viewer/2', 'name': 'Two'},
        ],
        success_callback=lambda index, data: completed.append(
            (index, data['chapterName'])
        ),
    )

    assert [result['chapterName'] for result in results] == [
        'Viewer One',
        'Viewer Two',
    ]
    assert completed == [(1, 'Viewer Two'), (0, 'Viewer One')]


def test_native_chapter_retry_uses_random_interval_range(monkeypatch):
    scraper, _messages = make_scraper()
    scraper._book_data = {'_global_novelpia': True}
    scraper._global_novelpia_parse_chapter = lambda _url, name: {
        'chapterName': name,
        'contentHtml': '<p>ok</p>',
    }
    sleeps = []
    monkeypatch.setattr(
        'external_scraper.random.uniform',
        lambda minimum, maximum: maximum,
    )
    monkeypatch.setattr(
        'external_scraper.time.sleep', sleeps.append
    )

    result = scraper.parse_chapter(
        0,
        {'url': 'https://global.novelpia.com/viewer/1', 'name': 'One'},
        interval=0.1,
        interval_max=0.4,
    )

    assert result['chapterName'] == 'One'
    assert sleeps == [0.4]


def test_signed_image_cookies_are_only_forwarded_to_novelpia_cdn():
    requests = []

    class Response:
        status_code = 200
        content = b'x' * 128
        headers = {'content-type': 'image/jpeg'}

    class Session:
        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()

    dialog = SimpleNamespace(_log=lambda _message: None)
    signed = {
        'CloudFront-Policy': 'policy',
        'CloudFront-Key-Pair-Id': 'key-id',
        'CloudFront-Signature': 'signature',
    }

    ExternalNovelDialog._download_image_python(
        dialog,
        'https://pv-gn.novelpia.com/chapter.jpg',
        session=Session(),
        request_cookies=signed,
        log_success=False,
    )
    ExternalNovelDialog._download_image_python(
        dialog,
        'https://unrelated.example/chapter.jpg',
        session=Session(),
        request_cookies=signed,
        log_success=False,
    )

    assert requests[0][1]['cookies'] == signed
    assert 'cookies' not in requests[1][1]
