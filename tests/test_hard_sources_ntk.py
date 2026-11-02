"""The ntk side of hard-sources.js in a real browser: webtoon episode lists, the reader's page list, covers."""
import json

import pytest

import hard_sources_support as hs

sync_api = pytest.importorskip('playwright.sync_api')

ORIGIN = 'https://sbxh9.com'
JS = hs.script_source()

ROW = ('<li class="ep-row-v2" data-episode-number="{number}"><a href="/webtoon/5/{id}" class="ep-row-v2-link">'
       '<div class="ep-row-v2-thumb"><span class="ep-row-v2-no">{number}</span></div>'
       '<div class="ep-row-v2-title"><span class="ep-badge-up">UP</span><strong>Episode {number}</strong></div></a></li>')


def rows(*episodes):
    return ''.join(ROW.format(number=number, id=episode) for number, episode in episodes)


def page_html(body, head=''):
    return f'<!doctype html><html><head>{head}</head><body><main>{body}</main></body></html>'


READER = """<script>
fetch('/api/webtoon-images', {method: 'POST', body: JSON.stringify({workId: '5', episodeId: 'kp-5-6', token: 't'})});
</script>"""


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - no browser installed
            pytest.skip(f'Chromium unavailable: {exc}')
        yield instance
        instance.close()


@pytest.fixture
def context(browser):
    ctx = browser.new_context(viewport={'width': 1280, 'height': 800})
    ctx.add_init_script(JS)  # what the apps do: a document-start user script
    yield ctx
    ctx.close()


def serve(page, routes):
    """{path or path?query: (status, body, content-type)}; everything else is an empty 404."""
    def handle(route):
        url = route.request.url[len(ORIGIN):]
        found = routes.get(url) or routes.get(url.split('?cb=')[0])  # the script's cache buster
        if found is None:
            route.fulfill(status=404, body='')
        else:
            status, body, kind = found
            route.fulfill(status=status, content_type=kind, body=body)
    page.route(f'{ORIGIN}/**', handle)


def html_route(body):
    return (200, body, 'text/html')


def json_route(data, status=200):
    return (status, json.dumps(data), 'application/json')


def webtoon(page, **options):
    args = {'chapterUrl': f'{ORIGIN}/webtoon/5/kp-5-6', 'name': 'One', 'index': 3, **options}
    return page.evaluate('(args) => window.__npiaHard.ntk.webtoon(args)', args)


def test_chapter_list_keeps_mirror_source_ids_in_order_and_reads_the_title_not_the_badge(context):
    page = context.new_page()
    serve(page, {'/webtoon/5': html_route(page_html(
        '<ul>' + rows((3, 'goodtoon-745176'), (2, 'kp-5656-6755'), (1, 'tkor5-118')) + '</ul>'))})
    page.goto(f'{ORIGIN}/webtoon/5')
    state = page.evaluate("() => window.__npiaHard.ntk.pageState({novelId: '5', kind: 'webtoon'})")
    assert state['state'] == 'ready'
    listing = page.evaluate("() => window.__npiaHard.ntk.chapterList({novelId: '5', kind: 'webtoon'})")
    assert listing['ok']
    assert [c['episodeId'] for c in listing['chapters']] == ['tkor5-118', 'kp-5656-6755', 'goodtoon-745176']
    assert [c['number'] for c in listing['chapters']] == [1, 2, 3]
    assert [c['name'] for c in listing['chapters']] == ['Episode 1', 'Episode 2', 'Episode 3']


def test_chapter_list_follows_the_epage_pager(context):
    page = context.new_page()
    pager = '<a href="/webtoon/5?epage=2">2</a>'
    serve(page, {
        '/webtoon/5': html_route(page_html('<ul>' + rows((3, 'kp-5-3')) + '</ul>' + pager)),
        '/webtoon/5?epage=2': html_route(page_html('<ul>' + rows((2, 'kp-5-2'), (1, 'kp-5-1')) + '</ul>')),
    })
    page.goto(f'{ORIGIN}/webtoon/5')
    listing = page.evaluate("() => window.__npiaHard.ntk.chapterList({novelId: '5', kind: 'webtoon'})")
    assert [c['episodeId'] for c in listing['chapters']] == ['kp-5-1', 'kp-5-2', 'kp-5-3']


def test_pages_beside_the_episodes_are_not_episodes(context):
    page = context.new_page()
    serve(page, {'/webtoon/5': html_route(page_html(
        '<a href="/webtoon/5/reviews">Reviews</a><a href="/webtoon/5/8812">One</a>'))})
    page.goto(f'{ORIGIN}/webtoon/5')
    listing = page.evaluate("() => window.__npiaHard.ntk.chapterList({novelId: '5', kind: 'webtoon'})")
    assert [c['episodeId'] for c in listing['chapters']] == ['8812']


def test_novel_ids_still_work_and_keep_their_big_number_tiebreak(context):
    page = context.new_page()
    serve(page, {'/novel/5': html_route(page_html(
        '<ul><li data-ep="2"><a href="/novel/5/902">Two</a></li><li data-ep="1"><a href="/novel/5/901">One</a></li></ul>'))})
    page.goto(f'{ORIGIN}/novel/5')
    listing = page.evaluate("() => window.__npiaHard.ntk.chapterList({novelId: '5', kind: 'novel'})")
    assert [c['episodeId'] for c in listing['chapters']] == ['901', '902']


def test_webtoon_reads_the_readers_page_list_with_every_mirror_in_page_order(context):
    page = context.new_page()
    reply = {'ok': True, 'images': [
        {'page': 2, 'src': 'https://a.example/up/two.css', 'srcCandidates': ['https://b.example/up/two.css']},
        {'page': 1, 'src': 'https://a.example/up/one.js', 'srcCandidates': ['https://b.example/up/one.js', 'https://a.example/up/one.js']},
        {'page': 3, 'src': 'https://a.example/up/three.webp', 'srcCandidates': []},
        {'page': 4},
    ]}
    serve(page, {
        '/webtoon/5/kp-5-6': html_route(page_html('<img src="/board_uploads/ad.gif">' + READER)),
        '/api/webtoon-images': json_route(reply),
    })
    page.goto(f'{ORIGIN}/webtoon/5/kp-5-6')
    result = webtoon(page)
    assert result['ok']
    assert [image['name'] for image in result['images']] == [
        'images/0003_0001.webp', 'images/0003_0002.webp', 'images/0003_0003.webp']
    assert result['images'][0]['url'] == 'https://a.example/up/one.js'
    assert result['images'][0]['candidates'] == ['https://a.example/up/one.js', 'https://b.example/up/one.js']
    assert result['images'][2]['candidates'] == ['https://a.example/up/three.webp']
    assert 'src="images/0003_0002.webp"' in result['contentHtml']
    assert 'board_uploads' not in result['contentHtml']


def test_two_chapters_never_share_an_image_name(context):
    page = context.new_page()
    serve(page, {
        '/webtoon/5/kp-5-6': html_route(page_html(READER)),
        '/api/webtoon-images': json_route({'ok': True, 'images': [{'page': 1, 'src': 'https://a.example/1.js'}]}),
    })
    page.goto(f'{ORIGIN}/webtoon/5/kp-5-6')
    names = {webtoon(page, index=index)['images'][0]['name'] for index in (1, 2)}
    assert len(names) == 2


def test_webtoon_hook_leaves_the_sites_own_reply_readable(context):
    page = context.new_page()
    serve(page, {
        '/webtoon/5/kp-5-6': html_route(page_html('<p>x</p>')),
        '/api/webtoon-images': json_route({'ok': True, 'images': []}),
    })
    page.goto(f'{ORIGIN}/webtoon/5/kp-5-6')
    assert page.evaluate("""async () => (await (await fetch('/api/webtoon-images', {method: 'POST', body: '{}'})).json()).ok""")


def test_a_rate_limited_page_list_asks_the_app_to_wait(context):
    page = context.new_page()
    serve(page, {
        '/webtoon/5/kp-5-6': html_route(page_html(READER)),
        '/api/webtoon-images': json_route({'ok': False, 'error': 'too many requests'}, status=429),
    })
    page.goto(f'{ORIGIN}/webtoon/5/kp-5-6')
    result = webtoon(page, pageWaitMs=800)
    assert result['ok'] is False and result['stage'] == 'rate-limit' and result['status'] == 429


def test_without_a_page_list_the_chapter_html_is_used_but_never_its_banners(context):
    page = context.new_page()
    body = ('<img src="https://a.example/board_uploads/ad.png">'
            '<img src="https://a.example/data/page1.jpg">')
    serve(page, {'/webtoon/5/kp-5-6': html_route(page_html(body))})
    page.goto(f'{ORIGIN}/webtoon/5/kp-5-6')
    result = webtoon(page, pageWaitMs=0)
    assert [image['url'] for image in result['images']] == ['https://a.example/data/page1.jpg']
    assert result['images'][0]['name'] == 'images/0003_0001.jpg'
    assert result['images'][0]['candidates'] == []


def test_only_banners_in_the_chapter_html_is_no_chapter(context):
    page = context.new_page()
    serve(page, {'/webtoon/5/kp-5-6': html_route(page_html('<img src="https://a.example/board_uploads/ad.png">'))})
    page.goto(f'{ORIGIN}/webtoon/5/kp-5-6')
    result = webtoon(page, pageWaitMs=0)
    assert result['ok'] is False and result['error'] == 'no webtoon images were found'


@pytest.mark.parametrize('head, body, expected', [
    ('<meta property="og:image" content="https://cdn.example/9289.png">',
     '<main><img src="https://a.example/board_uploads/ad.png"></main>', 'https://cdn.example/9289.png'),
    ('<meta property="og:image" content="https://a.example/board_uploads/ad.png">',
     '<img src="https://a.example/board_uploads/ad.png">', ''),
    ('<meta property="og:image" content="https://cdn.example/9289.png">',
     '<div class="nd-thumb"><img src="https://cdn.example/thumb.jpg"></div>', 'https://cdn.example/thumb.jpg'),
])
def test_the_cover_is_never_a_banner(context, head, body, expected):
    page = context.new_page()
    serve(page, {'/webtoon/5': html_route(page_html(body + '<a href="/webtoon/5/kp-5-1">One</a>', head=head))})
    page.goto(f'{ORIGIN}/webtoon/5')
    info = page.evaluate("() => window.__npiaHard.ntk.bookInfo({novelId: '5', kind: 'webtoon'})")
    assert info['cover'] == expected
