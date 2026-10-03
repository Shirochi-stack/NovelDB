from external_scraper import ExternalScraper


def make_scraper():
    messages = []
    return ExternalScraper(logger=messages.append), messages


def test_rbooks_harmless_policy_and_amplitude_console_noise_is_hidden():
    scraper, messages = make_scraper()

    class Message:
        type = 'error'

        def __init__(self, text):
            self.text = text

    scraper._on_console(Message(
        'Permissions policy violation: unload is not allowed in this document.'
    ))
    scraper._on_console(Message(
        'Amplitude Logger [Error]: Event rejected due to missing API key'
    ))
    scraper._on_console(Message('Unexpected Rbooks renderer failure'))

    assert messages == ['[JS] Unexpected Rbooks renderer failure']


def test_rbooks_font_cors_noise_is_hidden_without_hiding_api_errors():
    scraper, messages = make_scraper()
    scraper._rbooks_chrome = True

    class Message:
        type = 'error'

        def __init__(self, text):
            self.text = text

    scraper._on_console(Message(
        "Access to font at 'https://static.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/web-font/pretendard/"
        "PretendardJPVariable.subset.84.woff2' has been blocked by CORS policy"
    ))
    scraper._on_console(Message(
        "Access to fetch at 'https://library-api.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/items' "
        "has been blocked by CORS policy"
    ))
    assert len(messages) == 1
    assert 'library-api.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/items' in messages[0]


def test_rbooks_url_detection_is_scoped_to_book_and_viewer_pages():
    assert ExternalScraper.is_rbooks(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1234567890'
    )
    assert ExternalScraper.is_rbooks(
        'https://www.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1234567890/view?from=library'
    )
    assert ExternalScraper.is_rbooks(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/library/books/8706175/'
    )
    assert not ExternalScraper.is_rbooks(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/category/books/100'
    )
    assert not ExternalScraper.is_rbooks(
        'https://example.com/books/1234567890/view'
    )


def test_rbooks_library_reader_uses_authenticated_library_url():
    class Page:
        def __init__(self):
            self.visited = []

        def goto(self, url, **_kwargs):
            self.visited.append(url)

        def evaluate(self, _script):
            return {'viewer': True, 'title': 'Owned Library Book'}

    scraper, _messages = make_scraper()
    page = Page()
    scraper._page = page
    scraper._context = object()
    scraper._rbooks_chrome = True
    url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/library/books/8706175/'

    book = scraper.parse_book(url)

    assert book['chapterCount'] == 1
    assert book['chapters'][0]['url'] == url
    assert page.visited == [url]


def test_rbooks_library_link_resolves_product_before_catalog_lookup():
    class Page:
        def __init__(self):
            self.visited = []

        def goto(self, url, **_kwargs):
            self.visited.append(url)

        def evaluate(self, _script):
            if len(self.visited) == 1:
                return {
                    'viewer': False,
                    'product': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6121000538',
                }
            return {
                'title': 'Owned Series',
                'links': ['https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6121000538/view'],
                'titleById': {},
                'seriesId': '',
            }

    scraper, _messages = make_scraper()
    page = Page()
    scraper._page = page
    scraper._context = object()
    scraper._rbooks_chrome = True

    book = scraper._rbooks_parse_book(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/library/books/8706175/'
    )

    assert book['chapterCount'] == 1
    assert book['_rbooks_book_id'] == '6121000538'
    assert page.visited == [
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/library/books/8706175/',
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6121000538',
    ]


def test_rbooks_book_converts_discovered_chain_to_external_records():
    class Page:
        def goto(self, *_args, **_kwargs):
            return None

        def wait_for_load_state(self, *_args, **_kwargs):
            raise AssertionError(
                'Rbooks metadata must not wait for tracker-driven full load'
            )

        def evaluate(self, _script):
            return {
                'title': 'Example Rbooks Novel',
                'author': 'Example Author',
                'synopsis': 'First line\nSecond line',
                'cover': 'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/cover/example.jpg',
                'publisher': 'Example Publisher',
                'tags': ['fantasy'],
                'links': [
                    '/books/1001/view',
                    'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1002/view',
                ],
                'titleById': {'1001': 'Opening'},
                'diagnostics': ['Chained episode links: 2'],
            }

    scraper, messages = make_scraper()
    scraper._page = Page()
    scraper._context = object()
    scraper._rbooks_chrome = True

    book = scraper._rbooks_parse_book('https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001')

    assert book['_rbooks'] is True
    assert book['language'] == 'ko'
    assert book['chapterCount'] == 2
    assert book['chapters'][0]['name'] == 'Opening'
    assert book['chapters'][0]['url'] == (
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view'
    )
    assert book['chapters'][1]['name'] == 'Episode 2'
    assert book['chapters'][1]['url'].endswith('/books/1002/view')
    assert book['chapters'][0]['isAccessible'] is True
    assert '<br/>' in book['introductionHTML']
    assert any('Chained episode links: 2' in message for message in messages)


def test_rbooks_book_api_discovery_runs_outside_product_page_csp():
    seen = {}

    class Page:
        def goto(self, *_args, **_kwargs):
            return None

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def evaluate(self, script):
            seen['script'] = script
            return {
                'title': 'CSP-safe Rbooks Novel',
                'author': 'Author',
                'links': [],
                'titleById': {},
                'seriesId': '6251000001',
                'diagnostics': ['Rendered episode links: 0'],
            }

    scraper, messages = make_scraper()
    scraper._page = Page()
    scraper._context = object()
    scraper._rbooks_chrome = True
    scraper._rbooks_discover_episode_chain = lambda series_id, **_kwargs: {
        'links': [
            'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6251000001/view',
            'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6251000002/view',
        ],
        'titleById': {
            '6251000001': 'Episode one',
            '6251000002': 'Episode two',
        },
        'diagnostics': ['Chained episode links: 2'],
        'isSerial': True,
        'error': '',
    }

    book = scraper._rbooks_parse_book(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6251000001'
    )

    assert 'book-api.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com' not in seen['script']
    assert 'fetchBook' not in seen['script']
    assert book['chapterCount'] == 2
    assert [chapter['name'] for chapter in book['chapters']] == [
        'Episode one',
        'Episode two',
    ]
    assert any('Chained episode links: 2' in message for message in messages)


def test_rbooks_owned_ebook_volume_chain_is_discovered(monkeypatch):
    scraper, _messages = make_scraper()
    first = {
        'title': 'Example 1권',
        'series': {'property': {
            'is_serial': False, 'total_book_count': 3,
            'next_books': {'6121000539': {}},
        }},
    }
    second = {
        'title': 'Example 2권',
        'series': {'property': {'next_books': {'6121000540': {}}}},
    }
    third = {
        'title': 'Example 3권',
        'series': {'property': {'next_books': {}}},
    }

    class ApiPage:
        def close(self):
            pass

    scraper._context = object()
    monkeypatch.setattr(scraper, '_rbooks_open_api_book',
                        lambda _id: (ApiPage(), first))
    monkeypatch.setattr(scraper, '_rbooks_api_fetch_book',
                        lambda _page, book_id: {
                            '6121000539': second,
                            '6121000540': third,
                        }.get(book_id))

    result = scraper._rbooks_discover_episode_chain('6121000538')

    assert result['isSerial'] is False
    assert [scraper._rbooks_book_id(url) for url in result['links']] == [
        '6121000538', '6121000539', '6121000540',
    ]
    assert result['titleById']['6121000540'] == 'Example 3권'


def test_rbooks_api_chain_follows_next_books_and_collects_titles():
    class ApiPage:
        closed = False

        def close(self):
            self.closed = True

        def wait_for_timeout(self, _milliseconds):
            return None

    page = ApiPage()
    first = {
        'title': {'main': 'Episode 1'},
        'series': {
            'property': {
                'is_serial': True,
                'total_book_count': 3,
                'next_books': {'1002': {'b_id': '1002'}},
            }
        },
    }
    payloads = {
        '1002': {
            'title': {'main': 'Episode 2'},
            'series': {
                'property': {
                    'is_serial': True,
                    'next_books': {'1003': {'b_id': '1003'}},
                }
            },
        },
        '1003': {
            'title': {'main': 'Episode 3'},
            'series': {
                'property': {
                    'is_serial': True,
                    'next_books': {},
                }
            },
        },
    }
    scraper, _messages = make_scraper()
    scraper._rbooks_open_api_book = lambda series_id: (page, first)
    scraper._rbooks_api_fetch_book = (
        lambda _page, book_id, retries=3: payloads.get(book_id)
    )

    result = scraper._rbooks_discover_episode_chain('1001')

    assert [ExternalScraper._rbooks_book_id(url) for url in result['links']] == [
        '1001',
        '1002',
        '1003',
    ]
    assert result['titleById'] == {
        '1001': 'Episode 1',
        '1002': 'Episode 2',
        '1003': 'Episode 3',
    }
    assert result['isSerial'] is True
    assert result['error'] == ''
    assert page.closed is True


def test_rbooks_api_uses_validated_contiguous_fast_catalog():
    class ApiPage:
        closed = False

        def close(self):
            self.closed = True

    page = ApiPage()
    first = {
        'title': {'main': 'Example Novel 1화'},
        'series': {
            'property': {
                'is_serial': True,
                'total_book_count': 182,
                'next_books': {'6251000002': {}},
            }
        },
    }
    last = {
        'title': {'main': 'Example Novel 182화'},
        'series': {
            'property': {
                'is_serial': True,
                'total_book_count': 182,
                'next_books': {},
            }
        },
    }
    fetches = []
    scraper, _messages = make_scraper()
    scraper._rbooks_open_api_book = lambda _series_id: (page, first)

    def fetch(_page, book_id, retries=3):
        fetches.append((book_id, retries))
        return last if book_id == '6251000182' else None

    scraper._rbooks_api_fetch_book = fetch
    result = scraper._rbooks_discover_episode_chain(
        '6251000001',
        rendered_links=[
            f'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/{book_id}/view'
            for book_id in range(6251000001, 6251000026)
        ],
    )

    ids = [ExternalScraper._rbooks_book_id(url) for url in result['links']]
    assert len(ids) == 182
    assert ids[:2] == ['6251000001', '6251000002']
    assert ids[-1] == '6251000182'
    assert fetches == [('6251000182', 2)]
    assert result['titleById']['6251000002'] == 'Example Novel 2화'
    assert result['titleById']['6251000182'] == 'Example Novel 182화'
    assert any(
        'Fast episode catalog: 182 contiguous links validated.' == diagnostic
        for diagnostic in result['diagnostics']
    )
    assert page.closed is True


def test_rbooks_noncontiguous_rendered_ids_fall_back_to_next_book_chain():
    class ApiPage:
        closed = False

        def close(self):
            self.closed = True

        def wait_for_timeout(self, _milliseconds):
            return None

    page = ApiPage()
    first = {
        'title': {'main': 'Episode 1'},
        'series': {
            'property': {
                'is_serial': True,
                'total_book_count': 3,
                'next_books': {'1002': {}},
            }
        },
    }
    payloads = {
        '1002': {
            'title': {'main': 'Episode 2'},
            'series': {'property': {
                'is_serial': True,
                'next_books': {'1003': {}},
            }},
        },
        '1003': {
            'title': {'main': 'Episode 3'},
            'series': {'property': {
                'is_serial': True,
                'next_books': {},
            }},
        },
    }
    fetches = []
    scraper, _messages = make_scraper()
    scraper._rbooks_open_api_book = lambda _series_id: (page, first)

    def fetch(_page, book_id, retries=3):
        fetches.append(book_id)
        return payloads.get(book_id)

    scraper._rbooks_api_fetch_book = fetch
    result = scraper._rbooks_discover_episode_chain(
        '1001',
        rendered_links=[
            'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
            'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1003/view',
        ],
    )

    assert fetches == ['1002', '1003']
    assert [ExternalScraper._rbooks_book_id(url) for url in result['links']] == [
        '1001', '1002', '1003',
    ]
    assert not any(
        diagnostic.startswith('Fast episode catalog:')
        for diagnostic in result['diagnostics']
    )


def test_rbooks_api_chain_reopens_failed_episode_and_continues():
    class ApiPage:
        def __init__(self, name):
            self.name = name
            self.closed = False

        def close(self):
            self.closed = True

        def wait_for_timeout(self, _milliseconds):
            return None

    first_page = ApiPage('first')
    recovered_page = ApiPage('recovered')
    first = {
        'title': {'main': 'Episode 1'},
        'series': {
            'property': {
                'is_serial': True,
                'total_book_count': 3,
                'next_books': {'1002': {}},
            }
        },
    }
    second = {
        'title': {'main': 'Episode 2'},
        'series': {
            'property': {
                'is_serial': True,
                'next_books': {'1003': {}},
            }
        },
    }
    third = {
        'title': {'main': 'Episode 3'},
        'series': {
            'property': {'is_serial': True, 'next_books': {}},
        },
    }
    open_calls = []

    def open_api(book_id):
        open_calls.append(book_id)
        if book_id == '1001':
            return first_page, first
        assert book_id == '1002'
        return recovered_page, second

    scraper, _messages = make_scraper()
    scraper._page = object()
    scraper._rbooks_open_api_book = open_api
    scraper._rbooks_api_fetch_book = lambda page, book_id, retries=3: (
        None if page is first_page else third
    )

    result = scraper._rbooks_discover_episode_chain('1001')

    assert open_calls == ['1001', '1002']
    assert [ExternalScraper._rbooks_book_id(url) for url in result['links']] == [
        '1001',
        '1002',
        '1003',
    ]
    assert result['error'] == ''
    assert any(
        'Episode API session recovered at 1002' in diagnostic
        for diagnostic in result['diagnostics']
    )
    assert first_page.closed is True
    assert recovered_page.closed is True


def test_rbooks_book_rejects_volume_without_webnovel_episode_links():
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
    scraper._context = object()
    scraper._rbooks_chrome = True

    assert scraper._rbooks_parse_book(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/9999'
    ) is None
    assert any('No webnovel episodes or linked ebook volumes' in message
               for message in messages)


def test_rbooks_result_preserves_clean_html_and_registers_images():
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
                    'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/images/picture.webp'
                ),
            },
            {
                'original': 'images/picture.webp',
                'absolute': (
                    'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/images/picture.webp'
                ),
            },
        ],
    }

    result = scraper._rbooks_build_chapter_result(
        payload,
        'Episode 1',
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
    )

    assert result['chapterName'] == 'Rendered title'
    assert result['contentText'] == 'Hello'
    assert 'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/images/picture.webp' in result['contentHtml']
    assert len(result['images']) == 1
    assert result['images'][0]['name'] == 'picture.webp'
    assert result['chapterUrl'].endswith('/1001/view')


def test_rbooks_series_title_is_not_used_as_every_chapter_name():
    scraper, _messages = make_scraper()
    scraper._book_data = {'bookname': '오리진 1st'}
    payload = {'title': '오리진 1st', 'content': '<p>1화</p>'}

    result = scraper._rbooks_build_chapter_result(
        payload,
        '오리진 1st 2화',
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/102001743/view',
    )

    assert result['chapterName'] == '오리진 1st 2화'
    assert result['sourceChapterName'] == '오리진 1st 2화'


def test_rbooks_redirected_unpurchased_chapter_is_locked_without_retry():
    class Page:
        url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001'

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    scraper, messages = make_scraper()
    scraper._rbooks_wait_for_content = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError('locked redirects must not wait for content')
    )

    result = scraper._rbooks_finish_loaded_chapter(
        Page(),
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
        'Paid episode',
    )

    assert result == {'_locked': True, 'chapterName': 'Paid episode'}
    assert any('LOCKED or unpurchased' in message for message in messages)


def test_rbooks_closed_page_is_not_misclassified_as_locked_redirect():
    class ClosedPage:
        url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001'

        @staticmethod
        def is_closed():
            return True

        def wait_for_load_state(self, *_args, **_kwargs):
            raise AssertionError('a closed page must not be inspected')

    scraper, messages = make_scraper()

    result = scraper._rbooks_finish_loaded_chapter(
        ClosedPage(),
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
        'Free episode',
    )

    assert result is None
    assert not any('LOCKED' in message for message in messages)


def test_rbooks_chapter_restarts_session_after_browser_closes_during_goto():
    class ClosingPage:
        url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001'

        def __init__(self):
            self.closed = False

        def is_closed(self):
            return self.closed

        def goto(self, *_args, **_kwargs):
            self.closed = True
            raise RuntimeError(
                'Target page, context or browser has been closed'
            )

    class RecoveredPage:
        def __init__(self):
            self.url = ''
            self.goto_calls = []

        @staticmethod
        def is_closed():
            return False

        def goto(self, url, **kwargs):
            self.url = url
            self.goto_calls.append((url, kwargs))

    closing_page = ClosingPage()
    recovered_page = RecoveredPage()
    starts = []
    scraper, messages = make_scraper()
    scraper._page = closing_page
    scraper._context = object()
    scraper._rbooks_chrome = True
    scraper._book_url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001'

    def restart(_url):
        starts.append(_url)
        scraper._page = recovered_page
        scraper._context = object()
        scraper._rbooks_chrome = True
        return True

    scraper._start_rbooks_browser = restart
    scraper._rbooks_finish_loaded_chapter = (
        lambda _page, _url, name: {'chapterName': name, 'contentHtml': '<p>x</p>'}
    )

    result = scraper._rbooks_parse_chapter(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
        'Free episode',
    )

    assert result['chapterName'] == 'Free episode'
    assert starts == ['https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001']
    assert recovered_page.goto_calls[0][0].endswith('/1001/view')
    assert not any('LOCKED' in message for message in messages)


def test_rbooks_chrome_is_created_offscreen_without_minimizing_or_headless():
    class Process:
        pid = 4321

    scraper, _messages = make_scraper()
    launches = []
    parked = []
    scraper._get_user_data_dir = lambda: 'rbooks-profile'
    scraper._chrome_processes_using_profile = lambda _path: []
    scraper._open_system_chrome = lambda url, **kwargs: (
        launches.append((url, kwargs)) or (Process(), 9222)
    )
    scraper._wait_for_cdp = lambda _port, timeout=0: True
    scraper._park_chrome_windows_for_profile = lambda path: (
        parked.append(path) or True
    )
    scraper._rbooks_connect_cdp = lambda port: port == 9222

    assert scraper._start_rbooks_browser(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6251000001'
    )

    assert launches == [(
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6251000001',
        {
            'remote_debugging': True,
            'user_data_dir': 'rbooks-profile',
            'hidden': False,
            'window_size': (1280, 900),
            'window_position': (-32000, -32000),
        },
    )]
    assert parked == ['rbooks-profile', 'rbooks-profile']


def test_rbooks_rendered_chapter_uses_contributed_cleanup_payload():
    class Page:
        url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view'

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    scraper, messages = make_scraper()
    scraper._rbooks_wait_for_content = lambda *_args, **_kwargs: True
    scraper._rbooks_extract_loaded_content = lambda *_args, **_kwargs: {
        'title': 'Viewer title',
        'content': '<blockquote>Author note</blockquote><p>Body</p>',
        'contentText': 'Author note\nBody',
        'imageUrls': [],
    }

    result = scraper._rbooks_finish_loaded_chapter(
        Page(),
        'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
        'Episode 1',
    )

    assert result['chapterName'] == 'Viewer title'
    assert '<blockquote>Author note</blockquote>' in result['contentHtml']
    assert result['images'] == []
    assert not any('[Rbooks] OK:' in message for message in messages)


def test_rbooks_batch_uses_native_parallel_path():
    scraper, _messages = make_scraper()
    scraper._book_data = {'_rbooks': True}
    calls = []

    def fake_batch(
        chapters, interval=0, interval_max=None, success_callback=None
    ):
        calls.append((chapters, interval, interval_max))
        results = [
            {'chapterName': chapter['name'], 'contentHtml': '<p>ok</p>'}
            for chapter in chapters
        ]
        for index, result in enumerate(results):
            success_callback(index, result)
        return results

    scraper._rbooks_parse_chapter_batch_parallel = fake_batch
    chapters = [
        {'url': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view', 'name': 'One'},
        {'url': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1002/view', 'name': 'Two'},
    ]

    completed = []
    results = scraper.parse_chapter_batch(
        chapters,
        interval=1.25,
        success_callback=lambda index, result: completed.append(
            (index, result['chapterName'])
        ),
    )

    assert [result['chapterName'] for result in results] == ['One', 'Two']
    assert calls == [(chapters, 1.25, None)]
    assert completed == [(0, 'One'), (1, 'Two')]


def test_rbooks_batch_restarts_when_browser_closes_during_navigation():
    class ClosingPage:
        url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001'

        def __init__(self):
            self.closed = False

        def is_closed(self):
            return self.closed

        def goto(self, *_args, **_kwargs):
            self.closed = True
            raise RuntimeError(
                'Target page, context or browser has been closed'
            )

    class HealthyPage:
        def __init__(self):
            self.url = ''

        @staticmethod
        def is_closed():
            return False

        def goto(self, url, **_kwargs):
            self.url = url

    page_sets = [[ClosingPage()], [HealthyPage()]]
    starts = []
    scraper, messages = make_scraper()
    scraper._book_url = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001'
    scraper._rbooks_parallel_pages = lambda _count: page_sets.pop(0)
    scraper.cleanup = lambda: None
    scraper._start_rbooks_browser = lambda url: starts.append(url) or True
    scraper._rbooks_finish_loaded_chapter = (
        lambda _page, _url, name: {'chapterName': name, 'contentHtml': '<p>x</p>'}
    )

    completed = []
    result = scraper._rbooks_parse_chapter_batch_parallel([
        {
            'url': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001/view',
            'name': 'Free episode',
        }
    ], success_callback=lambda index, data: completed.append(
        (index, data['chapterName'])
    ))

    assert result[0]['chapterName'] == 'Free episode'
    assert completed == [(0, 'Free episode')]
    assert starts == ['https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/1001']
    assert any('restarting this batch' in message for message in messages)
    assert not any('LOCKED' in message for message in messages)


def _rbooks_with_cookies(names, logs):
    scraper = ExternalScraper(logger=logs.append)
    scraper._context = type('Context', (), {
        'cookies': lambda self, urls: [{'name': name} for name in names],
    })()
    return scraper


def test_rbooks_refused_viewer_names_missing_login():
    logs = []
    scraper = _rbooks_with_cookies(['rbooks-ffid', '_ga'], logs)
    result = scraper._rbooks_refused_result('Vol 1')
    assert result['_locked'] and result['_lockReason'] == 'login'
    assert 'could not confirm this browser session' in logs[-1]


def test_rbooks_refused_viewer_when_signed_in_is_not_called_login():
    logs = []
    scraper = _rbooks_with_cookies(['\u0072\u0069\u0064\u0069\u002d\u0061\u0074', '\u0072\u0069\u0064\u0069\u002d\u0072\u0074'], logs)
    result = scraper._rbooks_refused_result('Vol 1')
    assert result['_lockReason'] == 'verification'
    assert 'could not confirm this browser session' not in logs[-1]


class _OwnedPage:
    def __init__(self, owned):
        self.owned = owned
        self.calls = []

    def evaluate(self, script, ids):
        self.calls.append(list(ids))
        return self.owned


def _rbooks_book(scraper):
    scraper._book_data = {'chapters': [
        {'id': '6121000538'}, {'id': '6121000539'}, {'id': '6121000540'},
    ]}


def test_rbooks_refusal_of_owned_volume_is_reported_as_app_only():
    logs = []
    scraper = _rbooks_with_cookies(['\u0072\u0069\u0064\u0069\u002d\u0061\u0074'], logs)
    _rbooks_book(scraper)
    page = _OwnedPage(['6121000538'])
    result = scraper._rbooks_refused_result('Vol 1', page, '6121000538')
    assert result['_lockReason'] == 'app_only'
    assert 'You own' in logs[-1]
    # A second refused volume reuses the book's ownership lookup.
    result = scraper._rbooks_refused_result('Vol 2', page, '6121000539')
    assert result['_lockReason'] == 'purchase'
    assert 'not in this account' in logs[-1]
    assert page.calls == [['6121000538', '6121000539', '6121000540']]


def test_rbooks_owned_volume_uses_pc_viewer_after_library_confirms_purchase(
    monkeypatch,
):
    from rbooks_app_proxy import RbooksAppProxy
    calls = []
    class Context:
        def cookies(self, urls):
            return [{'name': '\u0072\u0069\u0064\u0069\u002d\u0061\u0074'}]
        def new_page(self):
            raise AssertionError('The mocked proxy should own this operation')
    def extract(self, context, book_id, title, url):
        calls.append((book_id, title, url))
        return {'chapterName': title, 'contentHtml': '<p>owned text</p>'}
    monkeypatch.setattr(RbooksAppProxy, 'extract', extract)
    scraper = ExternalScraper(logger=lambda message: None)
    scraper._context = Context()
    _rbooks_book(scraper)
    page = _OwnedPage(['6121000538'])
    owned = scraper._rbooks_refused_result('Vol 1', page, '6121000538')
    unowned = scraper._rbooks_refused_result('Vol 2', page, '6121000539')
    assert owned['contentHtml'] == '<p>owned text</p>'
    assert unowned['_lockReason'] == 'purchase'
    assert calls == [('6121000538', 'Vol 1',
                      'https://view.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/6121000538')]


def test_rbooks_ownership_falls_back_to_library_origin(monkeypatch):
    from rbooks_app_proxy import RbooksAppProxy
    calls = []

    class LibraryPage:
        def goto(self, url, **kwargs):
            calls.append(('goto', url))

        def evaluate(self, script, ids):
            calls.append(('lookup', ids))
            return ['6121000538']

        def close(self):
            calls.append(('close',))

    class Context:
        def cookies(self, urls):
            return []

        def new_page(self):
            return LibraryPage()

    def extract(self, context, book_id, title, url):
        calls.append(('extract', book_id))
        return {'chapterName': title, 'contentHtml': '<p>owned text</p>'}

    monkeypatch.setattr(RbooksAppProxy, 'extract', extract)
    scraper = ExternalScraper(logger=lambda message: None)
    scraper._context = Context()
    _rbooks_book(scraper)
    result = scraper._rbooks_refused_result(
        'Vol 1', _OwnedPage(None), '6121000538')

    assert result['contentHtml'] == '<p>owned text</p>'
    assert ('goto', 'https://library.\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/') in calls
    assert ('extract', '6121000538') in calls
    assert ('close',) in calls


def test_rbooks_reader_selects_new_spine_on_two_page_chapter_boundary():
    from rbooks_app_proxy import RbooksAppProxy
    frames = [
        {'spine': 10, 'html': '<p>Previous chapter</p>'},
        {'spine': 11, 'html': '<p>New chapter</p>'},
    ]
    assert RbooksAppProxy._select_front_section(frames) == frames[1]


def test_rbooks_recognizes_per_user_install_and_rejects_remote_pages():
    from rbooks_app_proxy import RbooksAppProxy

    assert RbooksAppProxy._is_rbooks_tab({
        'type': 'page', 'url': 'file:///C:/Users/Reader/AppData/Local/'
        'Programs\u002f\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073\u002f\u0072\u0065\u0073\u006f\u0075\u0072\u0063\u0065\u0073\u002f\u0061\u0070\u0070\u002e\u0061\u0073\u0061\u0072\u002findex.html?Viewer',
    })
    assert not RbooksAppProxy._is_rbooks_tab({
        'type': 'page', 'url': 'https://example.com/Rbooks/resources/'
        'app.asar/index.html?Viewer',
    })


def test_rbooks_connect_keeps_accessible_process_after_access_denied(monkeypatch):
    import sys
    from types import SimpleNamespace
    import rbooks_app_proxy

    class AccessDenied(Exception):
        pass

    class Process:
        info = {'name': '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe'}

        def __init__(self, protected=False):
            self.protected = protected

        def cmdline(self):
            if self.protected:
                raise AccessDenied()
            return ['reader.exe', '--remote-debugging-port=51234']

        def exe(self):
            return 'per-user-reader.exe'

    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(
        process_iter=lambda attrs: [Process(), Process(True)],
        AccessDenied=AccessDenied, NoSuchProcess=ProcessLookupError,
    ))
    proxy = rbooks_app_proxy.RbooksAppProxy(lambda message: None)
    monkeypatch.setattr(proxy, '_tabs',
                        lambda port: [{'type': 'page'}] if port == 51234 else [])
    proxy.connect()
    assert proxy.port == 51234
    assert proxy.executable == 'per-user-reader.exe'


def test_rbooks_navigation_waits_for_render_not_just_slider(monkeypatch):
    import rbooks_app_proxy

    proxy = rbooks_app_proxy.RbooksAppProxy(lambda message: None)
    old = [{'spine': 8, 'html': '<p>Story</p>', 'ready': True}]
    loading = [{'spine': 0, 'html': '<img src="cover">', 'ready': False}]
    new = [{'spine': 0, 'html': '<img src="cover">', 'ready': True}]
    frames = iter([old, old, loading, new, new])
    page = [8]
    monkeypatch.setattr(proxy, '_front_sections', lambda: next(frames))
    monkeypatch.setattr(proxy, '_reader_page', lambda: page[0])

    def navigate(*args):
        page[0] = 0
        return 0

    monkeypatch.setattr(proxy, '_evaluate', navigate)
    ticks = iter(range(100))
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: next(ticks))

    def wait(check, *args):
        assert check() is None  # Old chapter remains after slider changes.
        assert check() is None  # New frame is still loading.
        assert check() is None  # Wait for the new frame to settle.
        return check()

    monkeypatch.setattr(proxy, '_wait', wait)
    assert proxy._navigate_reader_page(0) == new


def test_rbooks_front_matter_failure_is_retried_and_never_silently_omitted(
    monkeypatch,
):
    import pytest
    from rbooks_app_proxy import RbooksAppProxy, RbooksAppError

    proxy = RbooksAppProxy(lambda message: None)
    attempts = []

    def read(page):
        attempts.append(page)
        raise RbooksAppError('Opening pages did not render')

    monkeypatch.setattr(proxy, '_front_matter', read)
    with pytest.raises(RbooksAppError, match='Opening pages'):
        proxy._read_front_matter(9)
    assert attempts == [9, 9]


def test_rbooks_front_matter_does_not_accept_unreadable_source_cover(monkeypatch):
    import pytest
    from rbooks_app_proxy import RbooksAppProxy, RbooksAppError

    proxy = RbooksAppProxy(lambda message: None)
    monkeypatch.setattr(proxy, '_navigate_reader_page', lambda page: [{
        'spine': 2 if page else 0,
        'html': '<p>Story</p>' if page else
                '<div class="cover-image"><img src="local://cover"></div>',
    }])
    monkeypatch.setattr(proxy, '_images', lambda content: [])
    with pytest.raises(RbooksAppError, match='source cover'):
        proxy._front_matter(3)


def test_rbooks_wait_for_viewer_reopens_book_during_slow_download(monkeypatch):
    import rbooks_app_proxy

    proxy = rbooks_app_proxy.RbooksAppProxy(lambda message: None)
    tabs = iter([None, None, None, {'type': 'page'}])
    monkeypatch.setattr(proxy, '_tab', lambda suffix: next(tabs))
    ticks = iter(range(0, 1000, 5))
    monkeypatch.setattr(rbooks_app_proxy.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(rbooks_app_proxy.time, 'sleep', lambda delay: None)
    reopened = []
    proxy._wait_for_viewer(None, reopen=lambda: reopened.append(True))
    assert reopened


def test_rbooks_cache_rejects_missing_front_matter_and_stripped_cover():
    from external_dialog import ExternalNovelDialog
    from rbooks_app_proxy import RbooksAppProxy

    old = {'contentHtml': '<div class="rbooks-content">'
                         '<div class="rbooks-volume-section"><p>Story</p></div>'
                         '</div>'}
    assert not ExternalNovelDialog._external_cacheable(old)
    complete = dict(old, _rbooksAppExportVersion=RbooksAppProxy.EXPORT_VERSION,
                    _rbooksAppHasSourceCover=True, _coverData='data:image/jpeg;base64,YQ==',
                    _rbooksVerification={'bookId': '123', 'expectedSections': 1,
                                         'verifiedSections': 1, 'complete': True})
    assert ExternalNovelDialog._external_cacheable(complete)
    stripped = ExternalNovelDialog._external_cache_result(complete, False)
    assert not ExternalNovelDialog._external_cacheable(stripped)


def test_rbooks_pc_handoff_opens_executable_without_windows_uri_handler(
    monkeypatch,
):
    import os
    import rbooks_app_proxy
    calls = []
    proxy = rbooks_app_proxy.RbooksAppProxy(lambda message: None)
    proxy.executable = r'C:\Program Files\RBOOKS\Rbooks\\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe'
    monkeypatch.setattr(proxy, '_sso', lambda context: 'short-lived-ticket')
    monkeypatch.setattr(proxy, '_close_reader_windows', lambda: None)
    monkeypatch.setattr(proxy, '_wait', lambda *args, **kwargs: True)
    monkeypatch.setattr(proxy, '_wait_for_viewer',
                        lambda snapshot, reopen=None, title=None: True)
    monkeypatch.setattr(rbooks_app_proxy.subprocess, 'Popen',
                        lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(os, 'startfile',
                        lambda link: (_ for _ in ()).throw(
                            AssertionError('Windows URI handler was used')))
    proxy._open_owned_book(object(), '6121000538', 'Owned volume')
    assert len(calls) == 1
    assert calls[0][0] == proxy.executable
    assert calls[0][1].startswith('\u0072\u0069\u0064\u0069://download?sso_otp=')
    assert '6121000538' in calls[0][1]


def test_rbooks_missing_viewer_installs_from_signed_official_download(monkeypatch):
    import io
    import rbooks_app_proxy

    logs = []
    calls = []
    proxy = rbooks_app_proxy.RbooksAppProxy(logs.append)
    expected = r'C:\Users\Reader\AppData\Local\Programs\Rbooks\\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe'

    class Download(io.BytesIO):
        def geturl(self):
            return 'https://viewer-ota.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/pc_electron/Setup.exe'

    monkeypatch.setattr(
        rbooks_app_proxy.urllib.request, 'urlopen',
        lambda request, timeout: Download(b'MZ' + b'\0' * 2048),
    )
    monkeypatch.setattr(proxy, '_find_executable', lambda: expected)

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == 'powershell.exe':
            return type('Result', (), {
                'returncode': 0,
                'stdout': 'Valid\nCN=\u0052\u0069\u0064\u0069\u0020\u0043\u006f\u0072\u0070\u006f\u0072\u0061\u0074\u0069\u006f\u006e, O=\u0052\u0069\u0064\u0069\u0020\u0043\u006f\u0072\u0070\u006f\u0072\u0061\u0074\u0069\u006f\u006e\n',
            })()
        return type('Result', (), {'returncode': 0})()

    monkeypatch.setattr(rbooks_app_proxy.subprocess, 'run', run)
    assert proxy._install_viewer() == expected
    assert calls[1][1:] == ['/S', '/currentuser']
    assert any('installed' in message for message in logs)


def test_rbooks_connect_installs_missing_viewer_before_launch(monkeypatch):
    import sys
    from types import SimpleNamespace
    import rbooks_app_proxy

    proxy = rbooks_app_proxy.RbooksAppProxy(lambda message: None)
    proxy.executable = 'missing-viewer.exe'
    launched = []
    installed = 'installed-viewer.exe'
    monkeypatch.setitem(
        sys.modules, 'psutil',
        SimpleNamespace(process_iter=lambda attrs: []),
    )
    monkeypatch.setattr(rbooks_app_proxy.os.path, 'isfile', lambda path: False)
    monkeypatch.setattr(proxy, '_find_executable', lambda: None)
    monkeypatch.setattr(proxy, '_install_viewer', lambda: installed)
    monkeypatch.setattr(proxy, '_tabs', lambda port: [])
    monkeypatch.setattr(proxy, '_wait', lambda *args, **kwargs: True)
    monkeypatch.setattr(
        rbooks_app_proxy.subprocess, 'Popen',
        lambda args, **kwargs: launched.append(args),
    )
    proxy.connect()
    assert proxy.executable == installed
    assert launched[0][0] == installed


def test_rbooks_installer_rejects_untrusted_redirect(monkeypatch):
    import io
    import pytest
    import rbooks_app_proxy

    class Download(io.BytesIO):
        def geturl(self):
            return 'https://other.example/Setup.exe'

    monkeypatch.setattr(
        rbooks_app_proxy.urllib.request, 'urlopen',
        lambda request, timeout: Download(b'MZ' + b'\0' * 2048),
    )
    monkeypatch.setattr(
        rbooks_app_proxy.subprocess, 'run',
        lambda *args, **kwargs: pytest.fail('Untrusted installer was run'),
    )
    proxy = rbooks_app_proxy.RbooksAppProxy(lambda message: None)
    with pytest.raises(rbooks_app_proxy.RbooksAppError, match='official'):
        proxy._install_viewer()


def test_rbooks_popup_watcher_presses_enter_only_for_detected_dialog(
    monkeypatch,
):
    from rbooks_app_proxy import RbooksAppProxy
    logs = []
    pressed = []
    proxy = RbooksAppProxy(logs.append)
    monkeypatch.setattr(proxy, '_native_rbooks_dialog', lambda: 12345)
    monkeypatch.setattr(proxy, '_press_enter_on_dialog',
                        lambda hwnd: pressed.append(hwnd) or True)
    monkeypatch.setattr(proxy, '_accept_js_dialog', lambda: False)

    class Stop:
        ended = False

        def is_set(self):
            return self.ended

        def wait(self, duration):
            self.ended = True

    proxy._dismiss_viewer_popups(Stop())
    assert pressed == [12345]
    assert 'Pressed Enter' in logs[0]


def test_rbooks_popup_detection_includes_background_dialog(monkeypatch):
    import ctypes
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock
    from rbooks_app_proxy import RbooksAppProxy

    user32 = SimpleNamespace(
        GetForegroundWindow=Mock(return_value=100),
        IsWindowVisible=Mock(return_value=True),
        EnumWindows=Mock(side_effect=lambda callback, value: callback(200, value)),
    )

    def window_class(hwnd, buffer, size):
        buffer.value = '#32770' if hwnd == 200 else 'Chrome_WidgetWin_1'

    def process_id(hwnd, pointer):
        pointer._obj.value = 1234

    user32.GetClassNameW = Mock(side_effect=window_class)
    user32.GetWindowThreadProcessId = Mock(side_effect=process_id)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(
        Process=lambda pid: SimpleNamespace(name=lambda: '\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.exe'),
        AccessDenied=PermissionError, NoSuchProcess=ProcessLookupError,
    ))
    assert RbooksAppProxy._native_rbooks_dialog() == 200


def test_rbooks_popup_watcher_accepts_reader_js_dialog_only(monkeypatch):
    import json
    import sys
    import types
    from rbooks_app_proxy import RbooksAppProxy
    calls = []
    proxy = RbooksAppProxy(lambda message: None)
    proxy.port = 49318
    monkeypatch.setattr(proxy, '_tabs', lambda port: [
        {'type': 'page', 'url': 'https://example.com/?Viewer',
         'webSocketDebuggerUrl': 'ws://unrelated'},
        {'type': 'page',
         'url': 'file:///C:/Program%20Files/\u0052\u0049\u0044\u0049/\u0052\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073/resources/'
                'app.asar/index.html?Viewer',
         'webSocketDebuggerUrl': 'ws://rbooks'},
    ])

    class Socket:
        def send(self, command):
            calls.append(json.loads(command))

        def recv(self):
            return json.dumps({'id': 1, 'result': {}})

        def close(self):
            pass

    def connect(url, **kwargs):
        calls.append(url)
        return Socket()

    monkeypatch.setitem(sys.modules, 'websocket', types.SimpleNamespace(
        create_connection=connect))
    assert proxy._accept_js_dialog()
    assert calls[0] == 'ws://rbooks'
    assert calls[1]['method'] == 'Page.handleJavaScriptDialog'
    assert calls[1]['params'] == {'accept': True}


def test_rbooks_reader_reports_invalid_local_cache_from_new_log_lines(
    monkeypatch,
):
    import io
    import rbooks_app_proxy
    reader_log = b'previous error\nInvalid or unsupported zip format. No END header found\n'
    monkeypatch.setattr(rbooks_app_proxy, 'open',
                        lambda *args, **kwargs: io.BytesIO(reader_log),
                        raising=False)
    assert not rbooks_app_proxy.RbooksAppProxy._reader_cache_error(
        ('reader.log', len(reader_log)))
    assert rbooks_app_proxy.RbooksAppProxy._reader_cache_error(
        ('reader.log', len(b'previous error\n')))


def test_rbooks_volume_sections_populate_epub_navigation():
    import xml.etree.ElementTree as ET
    from epub_generator import EpubGenerator

    epub = EpubGenerator(
        {'title': 'Owned volume', 'author': 'Author'}, 'unused.epub', ''
    )
    epub.add_chapter(
        'Owned volume',
        '<div id="rbooks-section-1">Prologue</div>'
        '<div id="rbooks-section-2">Chapter one</div>',
        show_title=False,
        toc_sections=[
            {'title': 'Prologue', 'id': 'rbooks-section-1'},
            {'title': 'Chapter one', 'id': 'rbooks-section-2'},
        ],
    )
    ncx = ET.fromstring(epub._create_toc_ncx())
    ns = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    points = ncx.findall('.//n:navPoint', ns)
    assert [point.find('n:navLabel/n:text', ns).text for point in points] == [
        'Prologue', 'Chapter one'
    ]
    assert [point.find('n:content', ns).get('src') for point in points] == [
        'Text/chapter0001.xhtml#rbooks-section-1',
        'Text/chapter0001.xhtml#rbooks-section-2',
    ]


def test_rbooks_front_matter_links_printed_contents_and_groups_navigation():
    import xml.etree.ElementTree as ET
    from epub_generator import EpubGenerator
    from rbooks_app_proxy import RbooksAppProxy

    front_pages = [{
        'html': '<h1 class="mtitle-h1-subtitle">1부 | 겨울</h1>',
    }]
    title, anchor = RbooksAppProxy._part_heading(front_pages)
    assert (title, anchor) == ('1부 | 겨울', 'rbooks-front-1')

    printed = RbooksAppProxy._link_printed_contents(
        '<div class="contents-body"><p>서장<span>序章</span></p>'
        '<p>1장 | 결빙<span>結氷</span></p></div>',
        ['서장序章', '1장 | 결빙結氷'],
    )
    assert 'href="#rbooks-section-1"' in printed
    assert 'href="#rbooks-section-2"' in printed
    assert '서장<span>序章</span>' in printed

    epub = EpubGenerator({'title': 'Volume', 'author': 'Author'},
                         'unused.epub', '')
    epub.add_chapter('Volume', '<div id="rbooks-front-1"></div>',
                     show_title=False, toc_sections=[{
                         'title': title, 'id': anchor,
                         'children': [
                             {'title': '서장序章', 'id': 'rbooks-section-1'},
                             {'title': '1장 | 결빙結氷',
                              'id': 'rbooks-section-2'},
                         ],
                     }])
    ncx = ET.fromstring(epub._create_toc_ncx())
    ns = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    parent = ncx.find('n:navMap/n:navPoint', ns)
    assert parent.find('n:navLabel/n:text', ns).text == '1부 | 겨울'
    assert parent.find('n:content', ns).get('src').endswith(
        '#rbooks-front-1'
    )
    assert [child.find('n:navLabel/n:text', ns).text
            for child in parent.findall('n:navPoint', ns)] == [
                '서장序章', '1장 | 결빙結氷',
            ]
    assert ncx.find('n:head/n:meta[@name="dtb:depth"]', ns).get(
        'content'
    ) == '2'


def test_rbooks_owned_cover_replaces_public_adult_warning_image():
    from external_dialog import ExternalNovelDialog

    url, data = ExternalNovelDialog._preferred_external_cover(
        {'_rbooks': True, 'coverUrl': 'cover_adult.png'},
        [
            {'_locked': True},
            {'coverUrl': 'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/cover/123/large',
             '_coverData': 'data:application/octet-stream;base64,Y292ZXI='},
        ],
    )
    assert url == 'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/cover/123/large'
    assert ExternalNovelDialog._decode_image_data_url(data) == b'cover'


def test_rbooks_refusal_without_ownership_answer_stays_generic():
    logs = []
    scraper = _rbooks_with_cookies(['\u0072\u0069\u0064\u0069\u002d\u0061\u0074'], logs)
    _rbooks_book(scraper)
    result = scraper._rbooks_refused_result('Vol 1', _OwnedPage(None), '6121000538')
    assert result['_lockReason'] == 'verification'
