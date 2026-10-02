import html
import threading
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper


@pytest.mark.parametrize('url', [
    'https://b.faloo.com/724903.html',
    'https://www.qidian.com/book/123456/',
    'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6121000538',
    'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/library/books/8706175/',
])
def test_login_sites_open_the_saved_installed_chrome_profile(url):
    class Setting:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    dialog = SimpleNamespace(
        _normalised_url=lambda: url,
        _url_var=Setting(''),
        _save_ext_config=lambda: None,
        _var_regular_browser=Setting(False),
        _append_log=Mock(),
        _work_queue=queue.Queue(),
    )
    for name in (
        '_btn_download', '_btn_browser', '_btn_sfc_app',
        '_chk_regular_browser', '_btn_paste_batch', '_btn_batch_file',
    ):
        setattr(dialog, name, Mock())

    ExternalNovelDialog._on_enter_browser(dialog)

    assert dialog._var_regular_browser.get() is True
    assert dialog._work_queue.get_nowait() == (
        'browser', {'url': url, 'regular': True},
    )


BOOK_HTML = '''
<h1 id="novelName">测试小说</h1>
<a class="rentouOne">作者甲</a>
<div class="T-L-T-C-Box1">作品简介。</div>
<img class="imgcss" src="//img.faloo.com/cover.jpg">
<div class="C-Fo-Zuo"><div class="DivTable">
  <div><a href="/1543561_1.html">第一章 开始</a></div>
  <div><a href="/1543561_2.html">第二章 继续</a></div>
  <div class="vip"><a href="/1543561_3.html">第三章</a></div>
</div></div>
'''


def test_faloo_book_accepts_desktop_and_mobile_urls(monkeypatch):
    scraper = ExternalScraper()
    calls = []

    def fetch(url):
        calls.append(url)
        return BOOK_HTML.encode(), url

    monkeypatch.setattr(scraper, '_faloo_fetch', fetch)
    for url in ('https://b.faloo.com/1543561.html',
                'https://wap.faloo.com/1543561.html'):
        book = scraper.parse_book(url)
        assert book['bookname'] == '测试小说'
        assert book['author'] == '作者甲'
        assert book['chapterCount'] == 3
        assert [ch['name'] for ch in book['chapters']] == [
            '第一章 开始', '第二章 继续', '第三章'
        ]
        assert book['chapters'][2]['isVIP'] is True
        assert book['chapters'][2]['isAccessible'] is False
        assert book['coverUrl'] == 'https://img.faloo.com/cover.jpg'
    assert calls == ['https://b.faloo.com/1543561.html'] * 2


def test_faloo_mobile_catalog_fallback(monkeypatch):
    scraper = ExternalScraper()
    mobile = b'''<h1>Mobile Novel</h1><div class="chapter-list">
        <a href="/book/1543561/101.html">Chapter 1</a>
        <a href="/book/1543561/102.html">Chapter 2</a>
    </div>'''

    def fetch(url):
        if url.startswith('https://b.faloo.com'):
            raise OSError('desktop host unavailable')
        return mobile, url

    monkeypatch.setattr(scraper, '_faloo_fetch', fetch)
    book = scraper.parse_book('https://wap.faloo.com/book/1543561.html')
    assert book['chapterCount'] == 2
    assert book['chapters'][0]['url'] == 'https://wap.faloo.com/book/1543561/101.html'


def test_faloo_gbk_book_and_chapters_log_unicode(monkeypatch):
    messages = []
    scraper = ExternalScraper(logger=messages.append)
    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        BOOK_HTML.encode('gb18030'), url,
    ))
    book = scraper.parse_book('https://b.faloo.com/1543561.html')
    assert book['bookname'] == '测试小说'
    assert book['chapters'][0]['name'] == '第一章 开始'
    assert any('测试小说' in message and '作者甲' in message for message in messages)

    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        '<div class="noveContent"><p>盘点万界，降临星河</p></div>'.encode('gb18030'),
        url,
    ))
    result = scraper.parse_chapter(0, book['chapters'][0], interval=0)
    assert result['contentText'] == '盘点万界，降临星河'


def test_faloo_recovers_when_explicit_decode_loses_catalog(monkeypatch):
    scraper = ExternalScraper()
    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        BOOK_HTML.encode('gb18030'), url,
    ))
    monkeypatch.setattr(scraper, '_faloo_decode_html', lambda page: '<html></html>')
    book = scraper.parse_book('https://b.faloo.com/1543561.html')
    assert book['chapterCount'] == 3
    assert book['bookname'] == '测试小说'


def test_faloo_uses_browser_when_http_catalog_is_missing(monkeypatch):
    scraper = ExternalScraper()
    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        b'<html><title>Incomplete response</title></html>', url,
    ))

    class BrowserPage:
        url = 'https://b.faloo.com/1543561.html'

        def goto(self, url, **kwargs):
            self.url = url

        def wait_for_selector(self, selector, **kwargs):
            return None

        def content(self):
            return BOOK_HTML

    scraper._page = BrowserPage()
    book = scraper.parse_book('https://b.faloo.com/1543561.html')
    assert book['chapterCount'] == 3


def test_faloo_repairs_existing_mojibake():
    broken = '盘点万界，降临星河'.encode('gb18030').decode('latin-1')
    assert ExternalScraper._faloo_fix_text(broken) == '盘点万界，降临星河'


def test_faloo_chapter_text_and_locked_preview(monkeypatch):
    scraper = ExternalScraper()
    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        '<div class="noveContent"><p>第一段 &amp; 文字</p><p>第二段</p></div>'.encode(),
        url,
    ))
    result = scraper._faloo_parse_chapter('https://b.faloo.com/1543561_1.html', '第一章')
    assert result['contentText'] == '第一段 & 文字\n第二段'
    assert '<p>第一段 &amp; 文字</p>' in result['contentHtml']

    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        '<div class="noveContent"><div class="con_img">VIP</div></div>'.encode(),
        url,
    ))
    assert scraper._faloo_parse_chapter('https://b.faloo.com/1543561_3.html', '第三章')['_locked']


# Markup served by b.faloo.com/724903_1658.html to a guest on 2026-09-29.
FALOO_LOGIN_WALL = (
    '<script>is_vip=1;</script><div class="noveContent">'
    '<div class="c_c1">您还没有登录，请登录后在继续阅读本部小说!</div>'
    '<div class="c_c3"><a href="//u.faloo.com/regist/login.aspx?backUrl=x">'
    '立即登录</a></div><div class="c_c4"><div class="c_c4_i_l">'
    '<span>设置自动订阅</span>(免费)系统将第一时间为您订阅最新发布的章节。'
    '</div></div></div>'
)


def test_faloo_login_wall_is_locked_not_saved_as_text(monkeypatch):
    scraper = ExternalScraper()
    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        FALOO_LOGIN_WALL.encode('gb18030'), url,
    ))
    result = scraper._faloo_parse_chapter(
        'https://b.faloo.com/724903_1658.html', '1644'
    )
    assert result['_locked'] and result['_lockReason'] == 'login'
    assert 'contentText' not in result


def test_faloo_purchase_prompt_for_signed_in_reader_is_locked():
    page = ('<div class="noveContent"><div class="c_c4">'
            '<span>设置自动订阅</span></div></div>')
    result = ExternalScraper()._faloo_chapter_from_page(page, 'VIP')
    assert result['_locked'] and result['_lockReason'] == 'purchase'


def test_faloo_readable_vip_images_keep_reader_cookies(monkeypatch):
    import faloo_image_reader
    # Without the text reader the chapter keeps its images.
    monkeypatch.setattr(faloo_image_reader, 'available', lambda: False)
    # Signed-in markup of b.faloo.com/724903_1658.html on 2026-09-29.
    image = ('//read.faloo.com/Page4VipImage.aspx?num=1&amp;o=3&amp;'
             'id=724903&amp;n=1658&amp;k=BEF5')
    page = ('<div class="noveContent"><div class="con_img">'
            '<div id="img_src_cok_1658_1" style=\'background-image: '
            f'url("{image}"); width: 945px;\'>'
            '<img src="http://s.faloo.com/adimages/beijing_page.gif"/>'
            '</div></div></div>')
    result = ExternalScraper()._faloo_chapter_from_page(
        page, 'VIP', 'https://b.faloo.com/724903_1658.html',
        lambda: {'KeenFire': 'abc'},
    )
    url = ('https://read.faloo.com/Page4VipImage.aspx?num=1&o=3&'
           'id=724903&n=1658&k=BEF5')
    assert not result.get('_locked')
    assert [image['url'] for image in result['images']] == [url]
    assert result['_imageCookies'] == {'KeenFire': 'abc'}
    assert html.escape(url, quote=True) in result['contentHtml']


def test_faloo_unbought_chapter_for_signed_in_reader_is_purchase_lock():
    page = ('<div class="noveContent"><div class="c_c1">'
            '您还没有订阅本章节(VIP章节)</div><div class="c_c3">'
            '<a href="//b.faloo.com/buy_724903.html">批量订阅VIP章节</a>'
            '</div></div>')
    result = ExternalScraper()._faloo_chapter_from_page(page, '1655')
    assert result['_locked'] and result['_lockReason'] == 'purchase'


def test_faloo_fetch_answers_c3vk_challenge(monkeypatch):
    import requests
    challenge = (b'<script>window.open("/724903_1.html", "_self");'
                 b'window[x].cookie="C3VK=3ba581; path=/; max-age=300;"'
                 b'</script>')
    seen = []

    def get(self, url, timeout):
        seen.append(self.cookies.get('C3VK'))
        body = challenge if len(seen) == 1 else b'<p>ok</p>'
        return SimpleNamespace(content=body, url=url,
                               raise_for_status=lambda: None)

    monkeypatch.setattr(requests.Session, 'get', get)
    scraper = ExternalScraper()
    monkeypatch.setattr(scraper, '_load_saved_site_cookies',
                        lambda *args: 0)
    assert scraper._faloo_fetch('https://b.faloo.com/724903_1.html')[0] == b'<p>ok</p>'
    assert seen == [None, '3ba581']


def test_faloo_free_chapter_drops_recharge_promotion():
    page = ('<div class="noveContent"><p>顾长歌！</p><p><b><font>中秋读书！'
            '</font><a href="http://pay.faloo.com/">立即抢充</a></b>'
            '(活动时间：9月25日到9月27日)</p></div>')
    result = ExternalScraper()._faloo_chapter_from_page(page, '1')
    assert result['contentText'] == '顾长歌！'



def test_faloo_batch_recovers_missing_http_chapters_in_browser(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {'_faloo': True}
    monkeypatch.setattr(scraper, '_faloo_fetch', lambda url: (
        b'<html><title>Reader loading</title></html>', url,
    ))

    class BrowserPage:
        def __init__(self):
            self.url = ''
            self.closed = False

        def goto(self, url, **kwargs):
            self.url = url
            assert kwargs['wait_until'] == 'commit'

        def wait_for_selector(self, selector, **kwargs):
            return None

        def content(self):
            return f'<div class="noveContent"><p>正文 {self.url[-1]}</p></div>'

        def close(self):
            self.closed = True

    class BrowserContext:
        def __init__(self):
            self.pages = []

        def new_page(self):
            page = BrowserPage()
            self.pages.append(page)
            return page

    scraper._context = BrowserContext()
    chapters = [{'url': f'https://b.faloo.com/{i}', 'name': f'第{i}章'}
                for i in range(3)]
    completed = []
    results = scraper.parse_chapter_batch(
        chapters, interval=0,
        success_callback=lambda index, result: completed.append(index),
    )
    assert [result['contentText'] for result in results] == [
        '正文 0', '正文 1', '正文 2',
    ]
    assert completed == [0, 1, 2]
    assert len(scraper._context.pages) == 3
    scraper.parse_chapter_batch(chapters, interval=0)
    assert len(scraper._context.pages) == 3
    scraper.close_faloo_pages()
    assert all(page.closed for page in scraper._context.pages)


def test_faloo_retries_http_login_wall_in_saved_browser(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {'_faloo': True}
    chapter = {'url': 'https://b.faloo.com/724903_1658.html',
               'name': '第1658章'}
    monkeypatch.setattr(scraper, '_faloo_parse_chapter',
                        lambda *_args: {'_locked': True,
                                        'chapterName': '第1658章'})
    seen = []

    def browser(chapters, *_args):
        seen.extend(chapters)
        return [{'chapterName': '第1658章', 'contentText': '已登录正文'}]

    monkeypatch.setattr(scraper, '_faloo_parse_chapters_browser', browser)
    result = scraper.parse_chapter_batch([chapter], interval=0)[0]
    assert result['contentText'] == '已登录正文'
    assert seen == [chapter]


def test_faloo_paid_chapter_goes_directly_to_login_browser(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {'_faloo': True}
    chapter = {
        'url': 'https://b.faloo.com/724903_1658.html',
        'name': '第1658章', 'isPaid': True, 'isVIP': True,
    }
    monkeypatch.setattr(scraper, '_faloo_parse_chapter',
                        lambda *_args: (_ for _ in ()).throw(AssertionError(
                            'Paid chapter must use the authenticated browser'
                        )))
    monkeypatch.setattr(scraper, '_faloo_parse_chapters_browser',
                        lambda _chapters, *_args: [
                            {'chapterName': '第1658章', 'contentText': '已登录正文'}
                        ])
    result = scraper.parse_chapter_batch([chapter], interval=0)[0]
    assert result['contentText'] == '已登录正文'


def test_faloo_batch_uses_parallel_workers_and_preserves_order(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {'_faloo': True}
    seen = []
    barrier = threading.Barrier(3)

    def fetch(url, name):
        barrier.wait(timeout=1)
        seen.append(name)
        return {'chapterName': name, 'contentText': name}

    monkeypatch.setattr(scraper, '_faloo_parse_chapter', fetch)
    chapters = [{'url': str(i), 'name': str(i)} for i in range(3)]
    completed = []
    result = scraper.parse_chapter_batch(
        chapters, interval=0, success_callback=lambda i, item: completed.append(i)
    )
    assert [item['chapterName'] for item in result] == ['0', '1', '2']
    assert sorted(completed) == [0, 1, 2]


def test_faloo_download_does_not_start_browser_for_metadata(monkeypatch):
    started = []

    class Scraper:
        _normalize_interval_range = staticmethod(
            ExternalScraper._normalize_interval_range
        )

        def __init__(self, logger):
            self._context = None

        def __getattr__(self, name):
            if name.startswith('is_'):
                return lambda url: False
            raise AttributeError(name)

        def is_faloo(self, url):
            return True

        def start(self):
            started.append(True)

        def parse_book(self, url):
            return {'_faloo': True, 'chapterCount': 0, 'chapters': []}

    class Setting:
        def get(self):
            return False

    monkeypatch.setattr('external_dialog.ExternalScraper', Scraper)
    dialog = SimpleNamespace(
        _scraper=None, _book_data=None, _downloading=True,
        _download_cancelled=False, _active_generate_on_stop=False,
        _var_from_enabled=Setting(), _var_to_enabled=Setting(),
        _msg_queue=queue.Queue(), _chapter_results=[],
        _apply_scraper_options=lambda: None,
        _format_interval_range=lambda low, high: f'{low}-{high}',
        _do_download=lambda *args, **kwargs: None,
        _log=lambda message: None,
    )
    ExternalNovelDialog._do_fetch_and_download(
        dialog, 'https://b.faloo.com/1543561.html',
        1.0, 4, False, interval_max=2.0,
    )
    assert started == []
    assert dialog._book_data['_faloo'] is True
    assert [kind for kind, _ in list(dialog._msg_queue.queue)] == [
        'book_parsed', 'finished',
    ]


@pytest.mark.parametrize('url', [
    'https://b.faloo.com/vip/724903/1658.html',   # VIP reader chapter
    'https://b.faloo.com/html_724_724903/',       # chapter list page
    'https://b.faloo.com/html_724_724903',
])
def test_faloo_recognises_vip_reader_and_chapter_list_urls(url):
    assert ExternalScraper.is_faloo(url)
    assert ExternalScraper._faloo_book_id(url) == '724903'


def test_faloo_rejects_malformed_vip_and_list_urls():
    assert not ExternalScraper.is_faloo('https://b.faloo.com/vip/abc/1.html')
    assert not ExternalScraper.is_faloo('https://b.faloo.com/html_724/')


def test_faloo_author_from_desktop_book_header():
    # Markup of b.faloo.com/724903.html on 2026-09-29.
    page = (
        '<div class="T-L-O-Z-Box1"><h1 id="novelName">玄幻：我！天命大反派</h1>'
        '<a href="//u.faloo.com/guru/x.html" title="天命反派_飞卢大神作家">'
        '<img class="rentouOne rentouOne2" src="//s.faloo.com/a.png"/></a>'
        '<a href="//b.faloo.com/l_0_1.html?t=2&amp;k=x" title="天命反派">天命反派</a>'
        '</div><div class="C-Fo-Zuo"><div class="DivTable">'
        '<a href="//b.faloo.com/724903_1.html">第一章</a></div></div>'
    )
    data = ExternalScraper()._faloo_book_from_page(
        page, 'https://b.faloo.com/724903.html', '724903',
        'https://b.faloo.com/724903.html',
    )
    assert data['author'] == '天命反派'
