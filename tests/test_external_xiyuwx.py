from external_scraper import ExternalScraper


BOOK_URL = 'http://www.xiyuwx.com/book/333559/'
BOOK_HTML = '''
<html><head><meta charset="gbk"></head><body>
<div id="info"><h1>测试西域小说</h1><p class="author">作者：张三</p></div>
<div id="intro">这是一本测试小说。</div>
<div id="fmimg"><img src="/covers/333559.jpg"></div>
<div id="list"><dl>
  <dd><a href="1001.html">第一章 开始</a></dd>
  <dd><a href="1002.html">第二章 继续</a></dd>
  <dd><a href="1002.html">第二章 继续</a></dd>
  <dd><a href="/book/333559/">返回目录</a></dd>
  <dd><a href="/book/888/1.html">别的书</a></dd>
</dl></div>
</body></html>
'''
CHAPTER_HTML = '''
<h1>第一章 开始</h1>
<div id="content"><p>这是第一段正文，包含正常的中文字符。</p>
<p>这是第二段正文。</p><script>doBadThings()</script></div>
'''


def test_xiyuwx_url_detection():
    assert ExternalScraper.is_xiyuwx(BOOK_URL)
    assert ExternalScraper.is_xiyuwx('https://xiyuwx.com/book/333559/1001.html')
    assert not ExternalScraper.is_xiyuwx('https://example.com/book/333559/')
    assert not ExternalScraper.is_xiyuwx('https://www.xiyuwx.com/')


def test_xiyuwx_book_and_unicode_chapter(monkeypatch):
    messages = []
    scraper = ExternalScraper(logger=messages.append)
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url == BOOK_URL:
            return BOOK_HTML.encode('gb18030'), url
        return CHAPTER_HTML.encode('gb18030'), url

    monkeypatch.setattr(scraper, '_xiyuwx_fetch', fetch)
    book = scraper.parse_book(BOOK_URL)
    assert book['bookname'] == '测试西域小说'
    assert book['author'] == '张三'
    assert book['coverUrl'] == 'http://www.xiyuwx.com/covers/333559.jpg'
    assert book['chapterCount'] == 2
    assert [chapter['url'] for chapter in book['chapters']] == [
        BOOK_URL + '1001.html', BOOK_URL + '1002.html',
    ]
    assert any('测试西域小说' in message for message in messages)
    result = scraper.parse_chapter(0, book['chapters'][0], interval=0)
    assert '正常的中文字符' in result['contentText']
    assert 'doBadThings' not in result['contentText']
    assert result['contentHtml'].startswith('<p>')
    assert fetched == [BOOK_URL, BOOK_URL + '1001.html']


def test_xiyuwx_batch_uses_parallel_workers_and_reports_success(monkeypatch):
    scraper = ExternalScraper()
    scraper._book_data = {'_xiyuwx': True}
    chapters = [
        {'url': BOOK_URL + f'{n}.html', 'name': f'第{n}章'}
        for n in range(1, 5)
    ]
    seen = []
    monkeypatch.setattr(scraper, '_random_interval_delay', lambda *args: 0)
    monkeypatch.setattr(scraper, '_xiyuwx_parse_chapter', lambda url, name: {
        'chapterName': name, 'contentText': url, 'contentHtml': '<p>x</p>',
        'images': [],
    })
    results = scraper.parse_chapter_batch(
        chapters, interval=0, success_callback=lambda i, result: seen.append(i)
    )
    assert [result['chapterName'] for result in results] == [
        chapter['name'] for chapter in chapters
    ]
    assert sorted(seen) == [0, 1, 2, 3]
