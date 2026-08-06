import base64
import concurrent.futures

from external_scraper import ExternalScraper


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


class DummyCookies:
    def __init__(self, values=None):
        self.values = values or {}

    def get_dict(self):
        return dict(self.values)


class DummySession:
    def __init__(self):
        self.cookies = DummyCookies({'uid': 'test'})

    def close(self):
        return None


def test_1qxs_url_detection_accepts_catalog_book_and_chapter_urls():
    assert ExternalScraper.is_1qxs(
        'https://www.1qxs.com/catalog_1/92476.html'
    )
    assert ExternalScraper.is_1qxs(
        'https://www.1qxs.com/xs_1/92476.html'
    )
    assert ExternalScraper.is_1qxs(
        'https://m.1qxs.com/xs_1/92476/1/2'
    )
    assert ExternalScraper.is_1qxs(
        'https://www.1qxs.com/catalog/64291.html'
    )
    assert not ExternalScraper.is_1qxs('https://www.1qxs.com/')
    assert not ExternalScraper.is_1qxs(
        'https://example.com/catalog_1/92476.html'
    )


def test_1qxs_fetch_retries_cookie_handshake_without_running_scripts(
    monkeypatch,
):
    scraper, _messages = make_scraper()
    monkeypatch.setattr(scraper, '_1QXS_MIN_REQUEST_INTERVAL', 0)

    class Response:
        def __init__(self, text):
            self.text = text
            self.status_code = 200

        def raise_for_status(self):
            return None

    class Session(DummySession):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get(self, url, headers, timeout):
            self.calls.append((url, headers, timeout))
            if len(self.calls) == 1:
                return Response('<div class="error">refresh</div>')
            return Response('<html><h1>Book</h1></html>')

    session = Session()
    result = scraper._1qxs_fetch_html(
        session,
        'https://www.1qxs.com/catalog_1/92476.html',
        referer='https://www.1qxs.com/xs_1/92476.html',
    )

    assert result == '<html><h1>Book</h1></html>'
    assert len(session.calls) == 2
    assert session.calls[0][1]['Referer'].endswith('/xs_1/92476.html')
    assert scraper._1qxs_cookies == {'uid': 'test'}


def test_1qxs_catalog_parses_metadata_and_complete_chapter_links(monkeypatch):
    scraper, messages = make_scraper()
    catalog_html = '''
        <div class="book">
          <h1><a href="/xs_1/92476.html">Fallback title</a></h1>
        </div>
        <a href="/xs_1/92476/1.html"><p>Chapter One</p></a>
        <a href="/xs_1/92476/2.html"><p>Chapter &amp; Two</p></a>
        <a href="/xs_1/99999/1.html"><p>Other book</p></a>
    '''
    book_html = '''
        <meta property="og:title" content="Test Novel">
        <meta property="og:description"
              content="Test Novel由一七小说提供精彩免费全文阅读:Synopsis">
        <meta property="og:image" content="//img.1qxs.com/cover/92476.jpg">
        <meta property="og:novel:author" content="Test Author">
        <meta property="og:novel:category" content="Fantasy">
        <meta property="og:novel:status" content="Ongoing">
    '''
    monkeypatch.setattr(scraper, '_1qxs_new_session', DummySession)
    monkeypatch.setattr(
        scraper,
        '_1qxs_fetch_html',
        lambda session, url, referer='': (
            catalog_html if '/catalog_1/' in url else book_html
        ),
    )

    book = scraper.parse_book(
        'https://www.1qxs.com/catalog_1/92476.html'
    )

    assert book['bookname'] == 'Test Novel'
    assert book['author'] == 'Test Author'
    assert book['description'] == 'Synopsis'
    assert book['coverUrl'] == 'https://img.1qxs.com/cover/92476.jpg'
    assert book['category'] == ['Fantasy']
    assert book['status'] == 'Ongoing'
    assert book['chapterCount'] == 2
    assert book['chapters'][1]['name'] == 'Chapter & Two'
    assert book['chapters'][1]['url'].endswith('/xs_1/92476/2.html')
    assert book['_1qxs'] is True
    assert any('2 chapters' in message for message in messages)


def _chapter_page(number, total, visible, hidden):
    encoded = base64.b64encode(hidden.encode('utf-8')).decode('ascii')
    return f'''
        <h1>Chapter &amp; One({number}/{total})</h1>
        <div class="content">
          <p>【Novel】小说免费阅读，请收藏 一七小说【1qxs.com】</p>
          {visible}
          <p id="prompt">如遇到内容无法显示或者显示不全，请更换浏览器。</p>
        </div>
        <script>const p_key='{encoded}';</script>
    '''


def test_1qxs_chapter_recombines_pages_and_decodes_hidden_tail(monkeypatch):
    scraper, _messages = make_scraper()
    scraper._book_data = {
        '_1qxs': True,
        '_1qxs_book_id': '92476',
        '_1qxs_variant': '_1',
    }
    pages = {
        'first': _chapter_page(
            1,
            2,
            '<p>一秒记住【笔趣阁】，更新快，无弹窗！</p>'
            '<p>Chapter &amp; One</p>'
            '<p>Visible &lt;one&gt;</p>',
            '<p>Hidden one</p><p>本章未完，点击[下一页]继续阅读-->></p>',
        ),
        'second': _chapter_page(
            2,
            2,
            '<p>Visible two</p>',
            '<p>Final &amp; line</p>',
        ),
    }
    monkeypatch.setattr(scraper, '_1qxs_new_session', DummySession)
    monkeypatch.setattr(
        scraper,
        '_1qxs_fetch_html',
        lambda session, url, referer='': (
            pages['second'] if '/1/2.html' in url else pages['first']
        ),
    )

    result = scraper._1qxs_parse_chapter(
        'https://www.1qxs.com/xs_1/92476/1.html',
        'Catalog name',
    )

    assert result['chapterName'] == 'Chapter & One'
    assert result['sourceChapterName'] == 'Catalog name'
    assert result['contentText'].splitlines() == [
        'Visible <one>',
        'Hidden one',
        'Visible two',
        'Final & line',
    ]
    assert '小说免费阅读' not in result['contentText']
    assert '本章未完' not in result['contentText']
    assert 'Visible &lt;one&gt;' in result['contentHtml']
    assert 'Final &amp; line' in result['contentHtml']


def test_1qxs_batch_uses_the_full_user_sized_batch(monkeypatch):
    scraper, _messages = make_scraper()
    scraper._book_data = {'_1qxs': True}
    observed = {}

    class Executor:
        def __init__(self, max_workers):
            observed['max_workers'] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def map(self, function, items):
            return [function(item) for item in items]

    monkeypatch.setattr(concurrent.futures, 'ThreadPoolExecutor', Executor)
    monkeypatch.setattr(
        scraper,
        '_1qxs_parse_chapter',
        lambda url, name: {'chapterName': name, 'contentHtml': '<p>ok</p>'},
    )
    batch = [
        {'url': f'https://www.1qxs.com/xs_1/92476/{index}.html',
         'name': f'Chapter {index}'}
        for index in range(1, 13)
    ]

    results = scraper.parse_chapter_batch(batch)

    assert observed['max_workers'] == 12
    assert len(results) == 12
