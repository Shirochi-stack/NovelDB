from external_dialog import ExternalNovelDialog
from external_scraper import ExternalScraper
from kobo_web_proxy import KoboWebReader


BOOK_URL = ('https://www.kobo.com/ww/en/ebook/'
            'nia-liston-the-merciless-maiden-volume-1')
READER_URL = 'https://readnow.kobo.com/2f17c5b3-10e2-4cdc-bc77-9e57cabe71cc'


def test_kobo_url_recognizes_regional_ebook_pages_only():
    assert ExternalScraper.is_kobo(BOOK_URL)
    assert ExternalScraper.is_kobo('https://www.kobo.com/us/en/ebook/example')
    assert not ExternalScraper.is_kobo('https://www.kobo.com/ww/en/audiobook/example')
    assert not ExternalScraper.is_kobo('https://readnow.kobo.com/abc')
    assert not ExternalScraper.is_kobo('https://evil-kobo.com/ww/en/ebook/example')


def test_kobo_browser_errors_are_not_filtered_as_ridi_noise():
    logs = []
    scraper = ExternalScraper(logger=logs.append)
    scraper._ridi_chrome = True
    scraper._ridi_site = 'Kobo'

    class Page:
        url = BOOK_URL

    class Message:
        type = 'error'
        page = Page()
        text = 'Access blocked by content security policy'

    scraper._on_console(Message())
    assert logs == ['[JS] Access blocked by content security policy']


def test_kobo_merges_overlapping_reader_windows_in_source_order():
    windows = [
        ['cover', 'toc', 'prologue', 'chapter1', 'chapter8'],
        ['chapter1', 'insert1', 'chapter2', 'chapter3'],
        ['chapter3', 'chapter4', 'chapter5', 'chapter8', 'epilogue'],
    ]
    assert KoboWebReader._ordered_paths(windows) == [
        'cover', 'toc', 'prologue', 'chapter1', 'insert1', 'chapter2',
        'chapter3', 'chapter4', 'chapter5', 'chapter8', 'epilogue',
    ]


def test_kobo_source_links_are_rewritten_to_epub_anchors():
    toc_path = 'OEBPS/Text/toc.xhtml'
    chapter_path = 'OEBPS/Text/chapter1.xhtml'
    anchors = {chapter_path: KoboWebReader._anchor(chapter_path)}
    clean = KoboWebReader._clean_html(
        '<a href="OEBPS/Text/chapter1.xhtml">Chapter 1</a>'
        '<script>bad()</script><p onclick="bad()">Text</p>',
        toc_path, anchors,
    )
    assert 'href="#' + anchors[chapter_path] + '"' in clean
    assert '<script' not in clean and 'onclick=' not in clean
    assert KoboWebReader._path('chapter1.xhtml', toc_path) == chapter_path
    assert KoboWebReader._path('https://example.com/chapter1.xhtml') == ''


def test_kobo_product_uses_owned_read_now_link(monkeypatch):
    scraper = ExternalScraper(logger=lambda message: None)
    monkeypatch.setattr(scraper, '_start_ridi_browser',
                        lambda url, site='Kobo': True)

    class Locator:
        first = None

        def __init__(self):
            self.first = self

        def wait_for(self, **kwargs):
            pass

    class Page:
        def goto(self, url, **kwargs):
            assert url == BOOK_URL

        def locator(self, selector):
            return Locator()

        def evaluate(self, script):
            return {'title': 'Nia Liston Volume 1',
                    'author': 'Umikaze Minamino',
                    'cover': 'https://cdn.kobo.com/cover.jpg',
                    'readUrl': READER_URL,
                    'language': 'en'}

    scraper._page = Page()
    book = scraper._kobo_parse_book(BOOK_URL)
    assert book['_kobo'] and book['chapterCount'] == 1
    assert book['chapters'][0]['url'] == READER_URL
    assert not book['chapters'][0]['isPaid']


def test_kobo_unowned_volume_is_marked_locked(monkeypatch):
    scraper = ExternalScraper(logger=lambda message: None)
    scraper._book_data = {'_kobo': True, '_kobo_read_url': ''}
    result = scraper._kobo_parse_chapter(BOOK_URL, 'Unowned book')
    assert result == {'_locked': True, 'chapterName': 'Unowned book',
                      '_lockReason': 'purchase'}


def test_kobo_owned_volume_uses_saved_browser_reader(monkeypatch):
    calls = []
    scraper = ExternalScraper(logger=lambda message: None)
    scraper._book_data = {'_kobo': True, '_kobo_read_url': READER_URL,
                          'coverUrl': 'https://cdn.kobo.com/cover.jpg'}
    scraper._context = object()
    monkeypatch.setattr(scraper, '_start_ridi_browser',
                        lambda url, site='Kobo': calls.append((url, site)) or True)
    monkeypatch.setattr(KoboWebReader, 'extract',
                        lambda self, context, url, title, cover:
                        {'contentHtml': '<p>owned text</p>',
                         'coverUrl': cover})
    result = scraper._kobo_parse_chapter(READER_URL, 'Owned book')
    assert result['contentHtml'] == '<p>owned text</p>'
    assert calls == [(READER_URL, 'Kobo')]


def test_kobo_reader_cover_data_is_used_by_epub_builder():
    url, data = ExternalNovelDialog._preferred_external_cover(
        {'_kobo': True, 'coverUrl': 'https://cdn.kobo.com/cover.jpg'},
        [{'coverUrl': 'https://cdn.kobo.com/cover.jpg',
          '_coverData': 'data:image/png;base64,Y292ZXI='}],
    )
    assert url == 'https://cdn.kobo.com/cover.jpg'
    assert ExternalNovelDialog._decode_image_data_url(data) == b'cover'


def test_kobo_blob_images_require_cached_bytes():
    result = {'contentHtml': '<img src="blob:https://readnow.kobo.com/one"/>',
              'images': [{'url': 'blob:https://readnow.kobo.com/one',
                          'data': 'data:image/png;base64,Y292ZXI='}]}
    assert ExternalNovelDialog._external_cacheable(result)
    stripped = ExternalNovelDialog._external_cache_result(
        result, cache_images=False)
    assert not ExternalNovelDialog._external_cacheable(stripped)
