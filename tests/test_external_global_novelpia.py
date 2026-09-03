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
            return None

    scraper, _messages = make_scraper()
    scraper._page = Page()
    session = object()
    scraper._global_novelpia_ensure_session = lambda refresh_login=False: session

    def request_json(_session, url, params=None, **_kwargs):
        assert _session is session
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
    scraper, _messages = make_scraper()

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

    def fake_batch(value):
        calls.append(value)
        return [{'chapterName': 'One', 'contentHtml': '<p>ok</p>'}]

    scraper._global_novelpia_parse_chapter_batch = fake_batch

    result = scraper.parse_chapter_batch(chapters, interval=0)

    assert result[0]['chapterName'] == 'One'
    assert calls == [chapters]


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
