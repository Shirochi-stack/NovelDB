import threading
import queue
from types import SimpleNamespace

from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper


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
