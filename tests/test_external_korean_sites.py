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

    def api_get(path, params=None, use_token=True, paced=False):
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
    assert [ch['name'] for ch in data['chapters']] == ['One', 'Two', '3화']
    assert [ch['isAccessible'] for ch in data['chapters']] == [
        True, False, True,
    ]
    assert [ch['isPaid'] for ch in data['chapters']] == [False, True, True]


def test_joara_chapter_decrypts_and_reports_locked(tmp_path, monkeypatch):
    scraper, _messages = make_scraper(tmp_path, monkeypatch)
    key = ('4646aefad3a2c93b2938d68093878e1c', '4646aefad3a2c93b')
    scraper._joara_key = key
    scraper._book_data = {'_joara': True}
    encrypted = joara_encrypt('Heading\n\nFirst line\n<Second>', *key)

    def api_get(path, params=None, use_token=True, paced=False):
        if params['cid'] == 'locked':
            return {'status': 0, 'error_code': 9200,
                    'message': '로그인 후 이용하시기 바랍니다.'}
        return {'status': 1, 'chapter': {
            'content': encrypted, 'episode': 'Author note',
        }}

    monkeypatch.setattr(scraper, '_joara_api_get', api_get)
    results = scraper.parse_chapter_batch([
        {'name': 'Heading', 'url': '', '_cid': 'ok'},
        {'name': 'Paid', 'url': '', '_cid': 'locked'},
    ], interval=0)

    assert results[0]['contentText'] == (
        'First line\n<Second>\n* * *\nAuthor note'
    )
    assert '<p>&lt;Second&gt;</p>' in results[0]['contentHtml']
    assert results[1] == {'_locked': True, 'chapterName': 'Paid'}


def test_joara_captcha_stops_remaining_chapters(tmp_path, monkeypatch):
    scraper, messages = make_scraper(tmp_path, monkeypatch)
    scraper._joara_key = ('k' * 32, 'i' * 16)
    scraper._book_data = {'_joara': True}
    calls = []

    def api_get(path, params=None, use_token=True, paced=False):
        calls.append(params['cid'])
        return {'status': 1, 'is_captcha': 'Y', 'chapter': {'content': ''}}

    monkeypatch.setattr(scraper, '_joara_api_get', api_get)
    results = scraper.parse_chapter_batch([
        {'name': 'A', 'url': '', '_cid': 'a'},
        {'name': 'B', 'url': '', '_cid': 'b'},
    ], interval=0)

    assert results == [None, None]
    assert calls == ['a']
    assert any('captcha' in message for message in messages)


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
