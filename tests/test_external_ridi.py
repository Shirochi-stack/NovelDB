from external_scraper import ExternalScraper


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


def test_ridi_url_detection_is_scoped_to_book_and_viewer_pages():
    assert ExternalScraper.is_ridibooks(
        'https://ridibooks.com/books/1234567890'
    )
    assert ExternalScraper.is_ridibooks(
        'https://www.ridibooks.com/books/1234567890/view?from=library'
    )
    assert not ExternalScraper.is_ridibooks(
        'https://ridibooks.com/category/books/100'
    )
    assert not ExternalScraper.is_ridibooks(
        'https://example.com/books/1234567890/view'
    )


def test_ridi_book_converts_discovered_chain_to_external_records():
    class Page:
        def goto(self, *_args, **_kwargs):
            return None

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def evaluate(self, _script):
            return {
                'title': 'Example Ridi Novel',
                'author': 'Example Author',
                'synopsis': 'First line\nSecond line',
                'cover': 'https://img.ridicdn.net/cover/example.jpg',
                'publisher': 'Example Publisher',
                'tags': ['fantasy'],
                'links': [
                    '/books/1001/view',
                    'https://ridibooks.com/books/1002/view',
                ],
                'titleById': {'1001': 'Opening'},
                'diagnostics': ['Chained episode links: 2'],
            }

    scraper, messages = make_scraper()
    scraper._page = Page()

    book = scraper._ridi_parse_book('https://ridibooks.com/books/1001')

    assert book['_ridibooks'] is True
    assert book['language'] == 'ko'
    assert book['chapterCount'] == 2
    assert book['chapters'][0]['name'] == 'Opening'
    assert book['chapters'][0]['url'] == (
        'https://ridibooks.com/books/1001/view'
    )
    assert book['chapters'][1]['name'] == 'Episode 2'
    assert book['chapters'][1]['url'].endswith('/books/1002/view')
    assert book['chapters'][0]['isAccessible'] is True
    assert '<br/>' in book['introductionHTML']
    assert any('Chained episode links: 2' in message for message in messages)


def test_ridi_book_rejects_volume_without_webnovel_episode_links():
    class Page:
        def goto(self, *_args, **_kwargs):
            return None

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def evaluate(self, _script):
            return {
                'title': 'Volume Ebook',
                'links': [],
                'titleById': {},
                'diagnostics': [],
            }

    scraper, messages = make_scraper()
    scraper._page = Page()

    assert scraper._ridi_parse_book(
        'https://ridibooks.com/books/9999'
    ) is None
    assert any('not downloadable volume ebooks' in message for message in messages)


def test_ridi_result_preserves_clean_html_and_registers_images():
    scraper, _messages = make_scraper()
    payload = {
        'title': 'Rendered title',
        'content': (
            '<p>Hello</p><img src="images/picture.webp"/>'
            '<img src="images/picture.webp"/>'
        ),
        'contentText': 'Hello',
        'imageUrls': [
            {
                'original': 'images/picture.webp',
                'absolute': (
                    'https://img.ridicdn.net/images/picture.webp'
                ),
            },
            {
                'original': 'images/picture.webp',
                'absolute': (
                    'https://img.ridicdn.net/images/picture.webp'
                ),
            },
        ],
    }

    result = scraper._ridi_build_chapter_result(
        payload,
        'Episode 1',
        'https://ridibooks.com/books/1001/view',
    )

    assert result['chapterName'] == 'Rendered title'
    assert result['contentText'] == 'Hello'
    assert 'https://img.ridicdn.net/images/picture.webp' in result['contentHtml']
    assert len(result['images']) == 1
    assert result['images'][0]['name'] == 'picture.webp'
    assert result['chapterUrl'].endswith('/1001/view')


def test_ridi_redirected_unpurchased_chapter_is_locked_without_retry():
    class Page:
        url = 'https://ridibooks.com/books/1001'

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    scraper, messages = make_scraper()
    scraper._ridi_wait_for_content = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('locked redirects must not wait for content')
    )

    result = scraper._ridi_finish_loaded_chapter(
        Page(),
        'https://ridibooks.com/books/1001/view',
        'Paid episode',
    )

    assert result == {'_locked': True, 'chapterName': 'Paid episode'}
    assert any('LOCKED or unpurchased' in message for message in messages)


def test_ridi_rendered_chapter_uses_contributed_cleanup_payload():
    class Page:
        url = 'https://ridibooks.com/books/1001/view'

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    scraper, messages = make_scraper()
    scraper._ridi_wait_for_content = lambda *_args, **_kwargs: True
    scraper._ridi_extract_loaded_content = lambda *_args, **_kwargs: {
        'title': 'Viewer title',
        'content': '<blockquote>Author note</blockquote><p>Body</p>',
        'contentText': 'Author note\nBody',
        'imageUrls': [],
    }

    result = scraper._ridi_finish_loaded_chapter(
        Page(),
        'https://ridibooks.com/books/1001/view',
        'Episode 1',
    )

    assert result['chapterName'] == 'Viewer title'
    assert '<blockquote>Author note</blockquote>' in result['contentHtml']
    assert result['images'] == []
    assert any('[Ridi] OK: Viewer title' in message for message in messages)


def test_ridi_batch_uses_native_parallel_path():
    scraper, _messages = make_scraper()
    scraper._book_data = {'_ridibooks': True}
    calls = []

    def fake_batch(chapters, interval=0):
        calls.append((chapters, interval))
        return [
            {'chapterName': chapter['name'], 'contentHtml': '<p>ok</p>'}
            for chapter in chapters
        ]

    scraper._ridi_parse_chapter_batch_parallel = fake_batch
    chapters = [
        {'url': 'https://ridibooks.com/books/1001/view', 'name': 'One'},
        {'url': 'https://ridibooks.com/books/1002/view', 'name': 'Two'},
    ]

    results = scraper.parse_chapter_batch(chapters, interval=1.25)

    assert [result['chapterName'] for result in results] == ['One', 'Two']
    assert calls == [(chapters, 1.25)]
