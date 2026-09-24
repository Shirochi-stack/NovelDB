import base64
import json

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from external_scraper import ExternalScraper


def make_scraper(tmp_path, monkeypatch):
    messages = []
    scraper = ExternalScraper(logger=messages.append)
    monkeypatch.setattr(
        ExternalScraper, '_get_user_data_dir', classmethod(
            lambda cls: str(tmp_path)
        )
    )
    monkeypatch.setattr(scraper, '_JOARA_MIN_REQUEST_INTERVAL', 0)
    return scraper, messages


def joara_encrypt(text, key, iv):
    padder = padding.PKCS7(128).padder()
    padded = padder.update(text.encode('utf-8')) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(key.encode('utf-8')), modes.CBC(iv.encode('utf-8'))
    ).encryptor()
    return base64.b64encode(
        encryptor.update(padded) + encryptor.finalize()
    ).decode('ascii')


class FakeResponse:
    def __init__(self, text, url):
        self.text = text
        self.url = url


def test_url_detection():
    assert ExternalScraper.is_joara('https://www.joara.com/book/1700629')
    assert ExternalScraper.is_joara(
        'https://www.joara.com/viewer?cid=abc%3D%3D&bookCode=1700629&sortno=1'
    )
    assert ExternalScraper.is_joara(
        'https://m.joara.com/view/book/bookPartList.html?book_code=1700629'
    )
    assert not ExternalScraper.is_joara('https://www.joara.com/')
    assert not ExternalScraper.is_joara('https://example.com/book/1700629')

    assert ExternalScraper.is_naver_novel(
        'https://novel.naver.com/best/list?novelId=1228694'
    )
    assert ExternalScraper.is_naver_novel(
        'https://m.novel.naver.com/webnovel/detail?novelId=934373&volumeNo=5'
    )
    assert not ExternalScraper.is_naver_novel(
        'https://novel.naver.com/best/genre?genre=101'
    )

    assert ExternalScraper.is_naver_series(
        'https://series.naver.com/novel/detail.series?productNo=5693874'
    )
    assert ExternalScraper.is_naver_series(
        'https://series.naver.com/novel/detail.nhn?originalProductId=417509'
    )
    assert not ExternalScraper.is_naver_series(
        'https://series.naver.com/comic/detail.series?productNo=1'
    )
    assert not ExternalScraper.is_naver_novel(
        'https://series.naver.com/novel/detail.series?productNo=5693874'
    )


def test_joara_decrypt_matches_cryptojs_aes_cbc():
    key = '4646aefad3a2c93b2938d68093878e1c'
    iv = '4646aefad3a2c93b'
    text = '군대.\r\n\r\n이시아는 여자다.'
    encrypted = joara_encrypt(text, key, iv)
    assert ExternalScraper._joara_decrypt(encrypted, (key, iv)) == text


def test_joara_token_is_read_from_saved_storage_state(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    signed = {'data': {'token': 'tok-123', 'member_id': 'me'}, 'expire': 0}
    (tmp_path / 'nd_storage_state.json').write_text(json.dumps({
        'cookies': [],
        'origins': [{
            'origin': 'https://www.joara.com',
            'localStorage': [
                {'name': 'signedInfo', 'value': json.dumps(signed)},
            ],
        }],
    }), encoding='utf-8')
    assert scraper._joara_saved_token() == 'tok-123'

    expired = {'data': {'token': 'old'}, 'expire': 1}
    assert ExternalScraper._joara_token_from_signed_info(
        json.dumps(expired)
    ) == ''


def test_joara_book_marks_unowned_paid_chapters(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    calls = []

    def api_get(path, params=None, use_token=True):
        calls.append((path, params))
        return {'status': 1, 'book': {
            'book_code': '42', 'subject': 'Title', 'writer_name': 'Writer',
            'intro': 'Intro\r\nline', 'keyword': ['태그'],
            'is_premium': 'TRUE', 'is_nobless': 'FALSE',
            'chk_finish': 'FALSE', 'category_ko_name': '판타지',
            'chapter': [
                {'cid': 'c2', 'sortno': 2, 'sub_subject': 'Two',
                 'is_free': 'FALSE', 'is_buy': 'FALSE'},
                {'cid': 'c1', 'sortno': 1, 'sub_subject': 'One',
                 'is_free': 'TRUE', 'is_buy': 'FALSE'},
                {'cid': 'c3', 'sortno': 3, 'sub_subject': '',
                 'is_free': 'FALSE', 'is_buy': 'TRUE'},
            ],
        }}

    monkeypatch.setattr(scraper, '_joara_api_get', api_get)
    data = scraper.parse_book('https://www.joara.com/book/42')

    assert calls[0][0] == '/v1/book/detail.joa'
    assert data['_joara'] is True
    assert data['description'] == 'Intro\nline'
    assert [ch['name'] for ch in data['chapters']] == [
        '1화 - One', '2화 - Two', '3화',
    ]
    assert [ch['isAccessible'] for ch in data['chapters']] == [
        True, False, True,
    ]
    assert [ch['isPaid'] for ch in data['chapters']] == [False, True, True]


KEY_A = ('4646aefad3a2c93b2938d68093878e1c', '4646aefad3a2c93b')
KEY_B = ('57a6d5082d2c2b36d2878c5d917166e4', '57a6d5082d2c2b36')


class FakeJoara:
    """Joara API stand-in: chapter_valid keys and chapter.joa responses."""

    def __init__(self, keys, chapters):
        self.keys = list(keys)
        self.chapters = chapters
        self.calls = []

    def __call__(self, path, params=None, use_token=True):
        if path == '/v1/book/chapter_valid.joa':
            self.calls.append('key')
            key = self.keys.pop(0) if len(self.keys) > 1 else self.keys[0]
            return {'status': 1, 'data': list(key)}
        cid = params['cid']
        self.calls.append(cid)
        response = self.chapters[cid]
        return response.pop(0) if isinstance(response, list) else response


def joara_chapter(text, key, **extra):
    return {'status': 1, 'chapter': {
        'content': joara_encrypt(text, *key), **extra,
    }}


def test_joara_chapter_names_follow_the_episode_number():
    name = ExternalScraper._joara_chapter_name
    # Section headings shared by a run of episodes stay distinguishable.
    assert [name(n, t) for n, t in (
        (1, 'prologue'), (2, 'prologue'), (3, '만남'), (4, '만남'),
    )] == ['1화 - prologue', '2화 - prologue', '3화 - 만남', '4화 - 만남']
    assert name(1, '그녀는 군대를 가야했다') == '1화 - 그녀는 군대를 가야했다'
    assert name(183, '1866, 해병대가 세계 열강을 떨게 했다 183화') == (
        '1866, 해병대가 세계 열강을 떨게 했다 183화'
    )
    assert name(7, '') == '7화'
    assert name(0, '체험판') == '체험판'


def test_joara_chapter_decrypts_and_reports_locked(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    scraper._book_data = {'_joara': True}
    api = FakeJoara([KEY_A], {
        'ok': joara_chapter(
            'Heading\n\nFirst line\n<Second>', KEY_A, episode='Author note'
        ),
        'locked': {'status': 0, 'error_code': 9200,
                   'message': '로그인 후 이용하시기 바랍니다.'},
    })
    monkeypatch.setattr(scraper, '_joara_api_get', api)
    results = scraper.parse_chapter_batch([
        {'name': 'Heading', 'url': '', '_cid': 'ok'},
        {'name': 'Paid', 'url': '', '_cid': 'locked'},
    ], interval=0)

    assert results[0]['contentText'] == (
        'First line\n<Second>\n* * *\nAuthor note'
    )
    assert '<p>&lt;Second&gt;</p>' in results[0]['contentHtml']
    assert results[1] == {'_locked': True, 'chapterName': 'Paid'}
    # One key serves both chapters while it is fresh.
    assert api.calls == ['key', 'ok', 'locked']


def test_joara_decrypt_failure_refetches_key_and_chapter(
    tmp_path, monkeypatch,
):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(scraper, '_joara_sleep', lambda seconds: None)
    # The first response was encrypted with a key that expired server-side;
    # only a new request made after the new key decrypts.
    api = FakeJoara([KEY_A, KEY_B], {'c': [
        joara_chapter('stale', ('x' * 32, 'x' * 16)),
        joara_chapter('Fresh text', KEY_B),
    ]})
    monkeypatch.setattr(scraper, '_joara_api_get', api)

    result = scraper._joara_parse_chapter('', 'Ch', cid='c')

    assert result['contentText'] == 'Fresh text'
    assert api.calls == ['key', 'c', 'key', 'c']


def test_joara_gives_up_after_two_decrypt_retries(tmp_path, monkeypatch):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(scraper, '_joara_sleep', lambda seconds: None)
    bad = joara_chapter('never', ('x' * 32, 'x' * 16))
    api = FakeJoara([KEY_A, KEY_B, ('c' * 32, 'c' * 16)], {'c': bad})
    monkeypatch.setattr(scraper, '_joara_api_get', api)

    assert scraper._joara_parse_chapter('', 'Ch', cid='c') is None
    assert api.calls.count('c') == 3
    assert any('Could not decrypt' in message for message in messages)


def test_joara_stale_key_is_waited_out(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    clock = [1000.0]
    slept = []
    monkeypatch.setattr('external_scraper.time.monotonic', lambda: clock[0])

    def sleep(seconds):
        slept.append(round(seconds, 1))
        clock[0] += seconds

    monkeypatch.setattr(scraper, '_joara_sleep', sleep)
    scraper._joara_key = KEY_A
    scraper._joara_key_born = 1000.0 - 25.0
    api = FakeJoara([KEY_A, KEY_B], {})
    monkeypatch.setattr(scraper, '_joara_api_get', api)

    # chapter_valid still returns the 25 s old key, so it is waited out
    # (TTL 31.5 s) and replaced by a new one with a known age.
    assert scraper._joara_chapter_key() == KEY_B
    assert slept == [6.5]
    assert scraper._joara_key_born == 1006.5
    assert api.calls == ['key', 'key']


def test_joara_run_counter_schedules_cooldown(tmp_path, monkeypatch):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    monkeypatch.setattr('external_scraper.time.monotonic', lambda: 500.0)

    scraper._joara_note_chapter_request(
        {'redis_data': {'call_20_30_cnt': 14}}
    )
    assert scraper._joara_next_request_at < 500.0 + 1
    scraper._joara_note_chapter_request(
        {'redis_data': {'call_20_30_cnt': 15}}
    )
    assert scraper._joara_next_request_at == 500.0 + 35.0
    assert any('Pausing 35s' in message for message in messages)


def test_joara_captcha_pauses_for_human_check_then_resumes(
    tmp_path, monkeypatch,
):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    captcha = {'status': 1, 'is_captcha': 'Y', 'chapter': {
        'content': '', 'redis_data': {'is_captcha': 1},
    }}
    api = FakeJoara([KEY_A], {'a': [captcha, joara_chapter('Body', KEY_A)]})
    monkeypatch.setattr(scraper, '_joara_api_get', api)
    checks = []
    monkeypatch.setattr(
        scraper, '_joara_request_human_check',
        lambda: checks.append('shown') or True,
    )

    result = scraper._joara_parse_chapter('', 'A', cid='a')

    assert result['contentText'] == 'Body'
    assert checks == ['shown']
    assert scraper.abort_reason == ''
    assert any('reCAPTCHA' in message for message in messages)


def test_joara_unsolved_captcha_aborts_the_download(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    scraper._book_data = {'_joara': True}
    captcha = {'status': 1, 'chapter': {
        'content': '', 'redis_data': {'is_captcha': 1},
    }}
    api = FakeJoara([KEY_A], {'a': captcha, 'b': captcha})
    monkeypatch.setattr(scraper, '_joara_api_get', api)
    checks = []
    monkeypatch.setattr(
        scraper, '_joara_request_human_check',
        lambda: checks.append('shown') or True,
    )

    results = scraper.parse_chapter_batch([
        {'name': 'A', 'url': '', '_cid': 'a'},
        {'name': 'B', 'url': '', '_cid': 'b'},
    ], interval=0)

    assert results == [None, None]
    # One human check; the captcha is still there afterwards, so the
    # remaining chapter is never requested.
    assert checks == ['shown']
    assert [call for call in api.calls if call != 'key'] == ['a', 'a']
    assert 'still active' in scraper.abort_reason
    assert scraper._joara_parse_chapter('', 'B', cid='b') is None


def test_dialog_stops_instead_of_retrying_after_scraper_abort():
    import queue
    from types import SimpleNamespace
    from unittest.mock import Mock

    from external_dialog import ExternalNovelDialog

    scraper = SimpleNamespace(_context=None, abort_reason='')
    batches = []

    def fetch(batch, interval=0, success_callback=None):
        batches.append([chapter['name'] for chapter in batch])
        scraper.abort_reason = '[Joara] check not completed.'
        return [None] * len(batch)

    scraper.parse_chapter_batch = fetch
    scraper.parse_chapter = Mock(return_value=None)
    logs = []
    dialog = SimpleNamespace(
        _scraper=scraper,
        _book_data={'_joara': True},
        _downloading=True,
        _download_cancelled=False,
        _chapter_results=[],
        _msg_queue=queue.Queue(),
        _apply_scraper_options=lambda: None,
        _sleep_while_downloading=Mock(return_value=True),
        _log=logs.append,
    )
    chapters = [{'name': f'Chapter {number}'} for number in range(1, 9)]

    ExternalNovelDialog._do_download(
        dialog, chapters, 0, 8, 0, num_threads=4, retry_passes=10,
    )

    assert batches == [['Chapter 1', 'Chapter 2', 'Chapter 3', 'Chapter 4']]
    scraper.parse_chapter.assert_not_called()
    # Not a user stop: finished chapters still produce output.
    assert dialog._download_cancelled is False
    assert '❌ [Joara] check not completed.' in logs
    assert not any('Failed to fetch' in line for line in logs)


def test_reader_blocks_keep_line_breaks_images_and_drop_title():
    fragment = (
        '<div class="detail_view_content"><p>Episode 1\n\nFirst line\n'
        'Second&nbsp;line<br>Third</p>'
        '<img src="https://novel-phinf.pstatic.net/a/pic.jpg?type=w500">'
        '<script>alert(1)</script></div>'
    )
    blocks = ExternalScraper._reader_html_blocks(
        fragment, 'https://novel.naver.com/best/detail?novelId=1'
    )
    result = ExternalScraper._reader_chapter_result(
        blocks, 'Episode 1', 'naver-novel-content'
    )

    assert result['contentText'] == 'First line\nSecond line\nThird'
    assert result['images'] == [{
        'url': 'https://novel-phinf.pstatic.net/a/pic.jpg?type=w500',
        'name': 'pic.jpg',
    }]
    assert (
        'src="https://novel-phinf.pstatic.net/a/pic.jpg?type=w500"'
        in result['contentHtml']
    )
    assert 'alert' not in result['contentHtml']


def naver_list_page(volumes, total):
    items = ''.join(
        f'<li class="volumeComment"><a class="list_item" '
        f'href="/best/detail?novelId=7&amp;volumeNo={volume}">'
        f'<p class="subj"><span class="bullet_wrap">UP</span>'
        f'{volume}. Ep {volume}</p><span class="date">2026.09.01</span>'
        f'</a></li>'
        for volume in volumes
    )
    return (
        '<html><head><meta property="og:image" content="https://img/c.jpg">'
        '</head><body><div class="section_area_info"><div class="info_top">'
        '<h2 class="title">Naver Title</h2><div class="info_group">'
        '<span class="item">로판</span><span class="item">'
        '<a href="/search?keyword=w&amp;target=author">Writer</a></span>'
        '</div></div><p class="summary">Line one\r\nLine two'
        '<a>더보기</a></p></div>'
        '<div class="end_tag_area"><div class="tag_collection">'
        '<a class="tag">#회귀</a></div></div>'
        '<div class="cont_sub"><div class="component_head">'
        f'<h3 class="title">작품 회차 ({total})</h3></div>'
        f'<ul class="list_type2">{items}</ul></div></body></html>'
    )


def test_naver_book_reads_every_list_page_in_order(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    pages = {
        'https://novel.naver.com/best/list?novelId=7':
            naver_list_page([5, 4], 5),
        'https://novel.naver.com/best/list?novelId=7&page=2':
            naver_list_page([3, 2], 5),
        'https://novel.naver.com/best/list?novelId=7&page=3':
            naver_list_page([1], 5),
    }
    monkeypatch.setattr(
        scraper, '_load_saved_site_cookies', lambda *args: 0
    )
    monkeypatch.setattr(
        scraper, '_naver_fetch',
        lambda session, url, referer='': FakeResponse(pages[url], url),
    )

    data = scraper.parse_book(
        'https://novel.naver.com/best/detail?novelId=7&volumeNo=3'
    )

    assert data['_naver_novel'] is True
    assert data['bookname'] == 'Naver Title'
    assert data['author'] == 'Writer'
    assert data['description'] == 'Line one\nLine two'
    assert data['tags'] == ['회귀']
    assert data['category'] == ['로판']
    assert [ch['name'] for ch in data['chapters']] == [
        '1. Ep 1', '2. Ep 2', '3. Ep 3', '4. Ep 4', '5. Ep 5',
    ]
    assert data['chapters'][0]['url'] == (
        'https://novel.naver.com/best/detail?novelId=7&volumeNo=1'
    )


def test_naver_series_uses_linked_web_novel(tmp_path, monkeypatch):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    series_url = 'https://series.naver.com/novel/detail.series?productNo=9'
    monkeypatch.setattr(
        scraper, '_load_saved_site_cookies', lambda *args: 0
    )
    monkeypatch.setattr(
        scraper, '_naver_fetch',
        lambda session, url, referer='': FakeResponse(
            "sVolumeListUrl : '/novel/volumeList.series?productNo=9"
            "&sortOrder=DESC&totalCount=30',"
            '<a href="http://novel.naver.com/webnovel/list?novelId=77" '
            'class="link_novel">바로가기</a>',
            series_url,
        ),
    )
    resolved = []
    monkeypatch.setattr(
        scraper, '_naver_parse_book',
        lambda url, series_url='': resolved.append((url, series_url)) or {
            'chapterCount': 12,
        },
    )

    assert scraper.parse_book(series_url) == {'chapterCount': 12}
    assert resolved == [
        ('https://novel.naver.com/webnovel/list?novelId=77', series_url),
    ]
    assert any('30 episode(s); 12 are readable' in m for m in messages)


def test_naver_series_without_web_edition_explains_drm(tmp_path, monkeypatch):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    url = 'https://series.naver.com/novel/detail.series?productNo=9'
    monkeypatch.setattr(
        scraper, '_load_saved_site_cookies', lambda *args: 0
    )
    monkeypatch.setattr(
        scraper, '_naver_fetch',
        lambda session, fetch_url, referer='': FakeResponse('<html/>', url),
    )

    assert scraper.parse_book(url) is None
    assert any('DRM' in message for message in messages)


def test_naver_publisher_logo_is_detected_and_removed_everywhere(
    tmp_path, monkeypatch,
):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    logo = 'https://novel-phinf.pstatic.net/2014/barobook+image.jpg?type=w500'
    cover = 'https://novel-phinf.pstatic.net/2014/cover.jpg?type=w500'
    avatar = 'https://novel-phinf.pstatic.net/2014/someone.jpg?type=w80_2'
    # Modelled on novelId 231619: the cover card sits just before the
    # Barobook logo, and chat avatars repeat in every episode.
    episodes = {
        'e1': [('img', avatar), ('text', 'One'), ('img', cover),
               ('img', logo)],
        'e2': [('text', 'Two'), ('img', cover), ('text', 'More'),
               ('img', logo.replace('w500', 'w80'))],
        'e3': [('img', avatar), ('text', 'Three'), ('img', cover)],
    }
    monkeypatch.setattr(
        scraper, '_naver_episode_blocks',
        lambda url, referer='': (episodes[url], False),
    )
    chapters = [{'url': 'e1'}, {'url': 'e2'}, {'url': 'e3'}]

    found = scraper._naver_detect_end_images(chapters, 'list')

    # Only the image that closes two of three samples; the cover card
    # before it and the avatars are kept.
    assert found == {'novel-phinf.pstatic.net/2014/barobook+image.jpg'}
    assert any('barobook+image.jpg' in message for message in messages)

    scraper._naver_end_images = found
    scraper._book_data = {'bookUrl': 'list'}
    result = scraper._naver_parse_chapter('e1', 'Ep 1')
    assert [image['url'] for image in result['images']] == [avatar, cover]
    assert 'barobook' not in result['contentHtml']

    # The logo is removed wherever it appears, not only at the end.
    episodes['e4'] = [('text', 'Start'), ('img', cover), ('img', logo),
                      ('text', 'Body'), ('img', logo)]
    result = scraper._naver_parse_chapter('e4', 'Ep 4')
    assert [image['url'] for image in result['images']] == [cover]
    assert result['contentText'] == 'Start\nBody'


def test_naver_single_episode_keeps_every_image(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(
        scraper, '_naver_episode_blocks',
        lambda url, referer='': (_ for _ in ()).throw(AssertionError(url)),
    )
    assert scraper._naver_detect_end_images([{'url': 'e1'}], 'list') == set()


def test_console_drops_content_security_policy_noise(tmp_path, monkeypatch):
    from types import SimpleNamespace

    scraper, messages = make_scraper(tmp_path, monkeypatch)
    for text in (
        "Connecting to 'https://ad.doubleclick.net/ccm/s/collect' violates "
        'the following Content Security Policy directive: "connect-src". '
        'The action has been blocked.',
        'Fetch API cannot load https://analytics.google.com/g/collect. '
        "Refused to connect because it violates the document's Content "
        'Security Policy.',
        "Framing 'https://www.facebook.com/' violates the following "
        'report-only Content Security Policy directive: "frame-src".',
    ):
        ridi_page = SimpleNamespace(url='https://ridibooks.com/books/1/view')
        scraper._on_console(
            SimpleNamespace(text=text, type='error', page=ridi_page)
        )
    assert messages == []

    scraper._on_console(SimpleNamespace(text='Real failure', type='error'))
    assert messages == ['[JS] Real failure']

    # On other sites a CSP block can be a real scraping failure.
    kakao_page = SimpleNamespace(url='https://page.kakao.com/content/1')
    blocked = (
        "Connecting to 'https://bff-page.kakao.com/x' violates the "
        'following Content Security Policy directive.'
    )
    scraper._on_console(
        SimpleNamespace(text=blocked, type='error', page=kakao_page)
    )
    assert messages[-1] == f'[JS] {blocked}'



def test_joara_spacing_is_measured_from_the_chapter_request(
    tmp_path, monkeypatch,
):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(scraper, '_JOARA_MIN_REQUEST_INTERVAL', 5.0)
    clock = [100.0]
    monkeypatch.setattr('external_scraper.time.monotonic', lambda: clock[0])

    def slow_key(force=False):
        clock[0] += 11.0  # a stale key being waited out
        return KEY_A

    sent = []

    def api_get(path, params=None, use_token=True):
        sent.append(clock[0])
        return {'status': 1, 'chapter': {
            'content': joara_encrypt('Body', *KEY_A),
        }}

    monkeypatch.setattr(scraper, '_joara_chapter_key', slow_key)
    monkeypatch.setattr(scraper, '_joara_api_get', api_get)

    scraper._joara_parse_chapter('', 'Ch', cid='c')

    assert sent == [111.0]
    assert scraper._joara_next_request_at == 116.0


def test_joara_retry_with_a_live_key_does_not_wait(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    monkeypatch.setattr('external_scraper.time.monotonic', lambda: 1000.0)
    slept = []
    monkeypatch.setattr(scraper, '_joara_sleep', slept.append)
    scraper._joara_key = KEY_A
    scraper._joara_key_born = 1000.0 - 5.0
    monkeypatch.setattr(scraper, '_joara_api_get', FakeJoara([KEY_A], {}))

    assert scraper._joara_chapter_key(force=True) == KEY_A
    assert slept == []
    assert scraper._joara_key_born == 995.0


def test_joara_unchanged_key_after_wait_keeps_its_age(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr('external_scraper.time.monotonic', lambda: clock[0])
    monkeypatch.setattr(
        scraper, '_joara_sleep',
        lambda seconds: clock.__setitem__(0, clock[0] + max(0, seconds)),
    )
    scraper._joara_key = KEY_A
    scraper._joara_key_born = 975.0
    monkeypatch.setattr(scraper, '_joara_api_get', FakeJoara([KEY_A], {}))

    assert scraper._joara_chapter_key() == KEY_A
    assert scraper._joara_key_born == 975.0


def test_joara_captcha_flag_values(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    api = FakeJoara([KEY_A], {'a': joara_chapter(
        'Body', KEY_A, redis_data={'is_captcha': '0', 'call_20_30_cnt': 1},
    )})
    monkeypatch.setattr(scraper, '_joara_api_get', api)
    monkeypatch.setattr(
        scraper, '_joara_request_human_check',
        lambda: (_ for _ in ()).throw(AssertionError('not a captcha')),
    )

    assert scraper._joara_parse_chapter('', 'A', cid='a')['contentText'] == (
        'Body'
    )


def test_naver_reuploaded_logo_copy_is_matched_by_name_and_look(
    tmp_path, monkeypatch,
):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    logo = 'https://novel-phinf.pstatic.net/20130115_84/a/barobook+image.jpg'
    copy = 'https://novel-phinf.pstatic.net/20130116_125/b/barobook+image.jpg'
    other = 'https://novel-phinf.pstatic.net/20130117_1/c/barobook+image.jpg'
    art = 'https://novel-phinf.pstatic.net/2013/d/1.jpg'
    banner = (10.8, [255] * 200 + [30] * 56)
    signatures = {
        f'https://{logo[8:]}': banner,
        copy: banner,
        other: (0.7, [90] * 256),
    }
    fetched = []

    def signature(url, referer=''):
        fetched.append(url)
        return signatures.get(url)

    monkeypatch.setattr(scraper, '_naver_image_signature', signature)
    episodes = {
        'e1': [('text', 'One'), ('img', logo)],
        'e2': [('text', 'Two'), ('img', logo)],
    }
    monkeypatch.setattr(
        scraper, '_naver_episode_blocks',
        lambda url, referer='': (episodes[url], False),
    )
    scraper._naver_end_images = scraper._naver_detect_end_images(
        [{'url': 'e1'}, {'url': 'e2'}], 'list'
    )
    scraper._book_data = {'bookUrl': 'list'}

    kept = scraper._naver_drop_end_images([
        ('img', art), ('text', 'Body'), ('img', copy), ('img', other),
    ])

    # The re-uploaded copy that looks the same goes; a same-named image
    # that looks different, and unrelated art, stay without a download.
    assert kept == [('img', art), ('text', 'Body'), ('img', other)]
    assert art not in fetched


def test_naver_episode_of_only_the_logo_is_kept(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    logo = 'https://novel-phinf.pstatic.net/2014/cp-logo.jpg'
    scraper._naver_end_images = {'novel-phinf.pstatic.net/2014/cp-logo.jpg'}
    scraper._naver_image_verdicts = {
        'novel-phinf.pstatic.net/2014/cp-logo.jpg': True,
    }
    assert scraper._naver_drop_end_images([('img', logo)]) == [('img', logo)]


def test_naver_parse_book_runs_logo_detection(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    pages = {
        'https://novel.naver.com/best/list?novelId=7':
            naver_list_page([3, 2, 1], 3),
    }
    monkeypatch.setattr(
        scraper, '_load_saved_site_cookies', lambda *args: 0
    )
    monkeypatch.setattr(
        scraper, '_naver_fetch',
        lambda session, url, referer='': FakeResponse(pages[url], url),
    )
    logo = 'https://novel-phinf.pstatic.net/2022/cp-logo.jpg?type=w500'
    monkeypatch.setattr(
        scraper, '_naver_episode_blocks',
        lambda url, referer='': ([('text', url), ('img', logo)], False),
    )
    monkeypatch.setattr(
        scraper, '_naver_image_signature', lambda url, referer='': None
    )

    data = scraper.parse_book('https://novel.naver.com/best/list?novelId=7')

    assert data['_naver_end_images'] == [
        'novel-phinf.pstatic.net/2022/cp-logo.jpg',
    ]
    result = scraper._naver_parse_chapter(data['chapters'][1]['url'], 'Ep 2')
    assert result['images'] == []
