import json
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from external_scraper import ExternalScraper


@pytest.mark.parametrize('failure', ['timeout', 'stop', 'navigation', 'prepare'])
def test_single_reader_never_extracts_unready_or_stale_content(failure):
    scraper = ExternalScraper()
    page = Mock()
    scraper._munpia_prepare_reader_page = Mock(return_value=failure != 'prepare')
    scraper._munpia_extract_loaded_chapter = Mock()
    def wait(*_args, **_kwargs):
        if failure == 'stop':
            scraper._stop_requested = True
        return failure != 'timeout'
    scraper._munpia_wait_for_selector = wait
    if failure == 'navigation':
        page.goto.side_effect = RuntimeError('net::ERR_ABORTED')
    assert scraper._munpia_parse_chapter(
        'https://www.munpia.com/novel/viewer/1/2', 'Chapter', page=page,
    ) is None
    scraper._munpia_extract_loaded_chapter.assert_not_called()


VIEWER_URL = 'https://www.munpia.com/novel/viewer/1/2'
MODULE_URL = 'https://www.munpia.com/assets/novel_wasm-fixture.js'
INFO_URL = 'https://www.munpia.com/api/v1/pc/novel-detail/1/entries/2/info'
CONTENT_URL = 'https://www.munpia.com/api/v1/pc/novel-detail/1/entries/2/content'

MODULE = '''
export function get_total_pages() { return 2; }
export function render_page(canvas, page) {
    const ctx = canvas.getContext('2d');
    ctx.font = '17px Arial';
    if (page === 0) {
        ctx.fillText('One & <two>', 57, 100);
        ctx.fillText('soft wrapped', 40, 130.6);
    } else {
        ctx.fillText('across pages.', 40, 100);
        ctx.fillText('Final paragraph.', 57, 192);
    }
}
export function get_page_image_rects(canvas, page) {
    return JSON.stringify(page === 1 ? [{id: 'art', y: 160, x: 40}] : []);
}
'''


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def reader(browser):
    context = browser.new_context()
    page = context.new_page()
    scraper = ExternalScraper(logger=lambda _: None)
    yield scraper, page, context
    context.close()


def install_reader(context, *, module=MODULE, content_status=200, content_error=None):
    requests = []
    info = {'result': {'entry': {'id': 2, 'title': 'Source title', 'attachments': [
        {'id': '@PIC:art', 'imageUrl': 'https://cdn1.munpia.com/art.png'},
    ]}}}
    document = f'''<!doctype html><html><body><canvas></canvas>
        <script type="module">
        import * as renderer from {json.dumps(MODULE_URL)};
        window.fixtureRenderer = renderer;
        // The site's Axios metadata path uses XMLHttpRequest.
        await new Promise(resolve => {{
            const request = new XMLHttpRequest();
            request.open('GET', {json.dumps(INFO_URL)});
            request.onload = resolve;
            request.send();
        }});
        const content = await fetch({json.dumps(CONTENT_URL)}, {{method: 'POST',
            body: JSON.stringify({{normalReaderRequest: true}})}});
        await content.arrayBuffer();
        if (content.ok) document.querySelector('canvas').getContext('2d')
            .fillText('Ready', 0, 0);
        </script></body></html>'''

    def route(request_route):
        request = request_route.request
        requests.append((request.method, request.url, request.post_data))
        if request.url == VIEWER_URL:
            request_route.fulfill(content_type='text/html', body=document)
        elif request.url == MODULE_URL:
            request_route.fulfill(content_type='text/javascript', body=module)
        elif request.url == INFO_URL:
            request_route.fulfill(content_type='application/json', body=json.dumps(info))
        elif request.url == CONTENT_URL:
            request_route.fulfill(
                status=content_status,
                content_type='application/json' if content_error else 'application/octet-stream',
                body=json.dumps(content_error) if content_error else b'normal site content',
            )
        else:
            request_route.abort()

    context.route('**/*', route)
    return requests


def test_reader_extracts_all_pages_paragraphs_and_inline_images(reader):
    scraper, page, context = reader
    requests = install_reader(context)
    result = scraper._munpia_parse_chapter(VIEWER_URL, 'List title', page=page)

    assert result['chapterName'] == 'Source title'
    assert result['sourceChapterName'] == 'List title'
    assert result['contentText'] == (
        'One & <two> soft wrapped across pages.\nFinal paragraph.'
    )
    assert '<p>One &amp; &lt;two&gt; soft wrapped across pages.</p>' in result['contentHtml']
    assert result['contentHtml'].index('<img ') < result['contentHtml'].index('Final paragraph.')
    assert result['images'] == [{
        'url': 'https://cdn1.munpia.com/art.png', 'name': 'munpia_2_1.png',
    }]
    assert '\n' in result['contentCss']
    # The extractor reuses the site's loaded content, with no purchase or
    # additional content request. The original reader POST is unchanged.
    assert [request for request in requests if request[0] == 'POST'] == [
        ('POST', CONTENT_URL, '{"normalReaderRequest":true}'),
    ]


@pytest.mark.parametrize('status,error,locked', [
    (403, {'message': 'Login required'}, True),
    (200, {'code': 'NO_PURCHASE', 'message': '구매 후 이용'}, True),
    (500, {'message': 'Server failure'}, False),
])
def test_reader_never_exports_denial_or_server_error_as_chapter(reader, status, error, locked):
    scraper, page, context = reader
    install_reader(context, content_status=status, content_error=error)
    result = scraper._munpia_parse_chapter(VIEWER_URL, 'Locked chapter', page=page)
    if locked:
        assert result == {'_locked': True, 'chapterName': 'Locked chapter'}
    else:
        assert result is None


def test_reader_rejects_uninitialized_or_empty_module(reader):
    scraper, page, context = reader
    install_reader(context, module='''
        export function get_total_pages() { return 0; }
        export function render_page() {}
    ''')
    assert scraper._munpia_parse_chapter(VIEWER_URL, 'Empty chapter', page=page) is None


def test_reader_preparation_is_idempotent_and_persists_across_navigation(reader):
    scraper, page, context = reader
    requests = install_reader(context)
    assert scraper._munpia_prepare_reader_page(page)
    assert scraper._munpia_prepare_reader_page(page)
    for _ in range(2):
        page.goto(VIEWER_URL)
        page.wait_for_selector('html[data-nd-munpia-ready="1"]', state='attached')
        assert scraper._munpia_extract_loaded_chapter(page, 'Chapter')['contentText']
    assert len([request for request in requests if request[0] == 'POST']) == 2


def test_legacy_reader_preserves_text_images_and_rejects_empty_page(reader):
    scraper, page, _context = reader
    page.set_content('''<div id="ENTRY-CONTENT"><div class="subinfo"><h3>Old title</h3></div>
        <div class="tcontent"><p>First paragraph.</p><p>Second paragraph.</p>
        <p class="dummy">Noise</p><img src="https://cdn1.munpia.com/art.png" />
        <script>window.noise = true;</script></div></div>''')
    result = scraper._munpia_extract_loaded_chapter(page, 'List title')
    assert result['chapterName'] == 'Old title'
    assert result['contentText'] == 'First paragraph.\nSecond paragraph.'
    assert 'Noise' not in result['contentHtml']
    assert result['images'][0]['url'] == 'https://cdn1.munpia.com/art.png'

    page.set_content('<html><body><h1>Unrelated page</h1></body></html>')
    assert scraper._munpia_extract_loaded_chapter(page, 'Chapter') is None


def test_stopped_reader_never_navigates(reader):
    scraper, page, context = reader
    requests = install_reader(context)
    scraper._stop_requested = True
    assert scraper._munpia_parse_chapter(VIEWER_URL, 'Chapter', page=page) is None
    assert requests == []
