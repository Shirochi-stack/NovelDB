"""Browser tests for hard-sources.js, the page-side logic shared by the Android and iOS qdn / rbooks sources.

The script runs here in Playwright Chromium the same way the apps run it in a web view: installed as a
document-start script on site pages, and evaluated into a blank page for decoding. Pages are served
from the real site origins through request interception, so relative URLs and same-origin fetches
behave as on the sites. Bought-chapter decoding is tested on captured cipher fonts (tests/fixtures) and
on a synthetic reader that has every protection layer; nothing here needs an account.
"""
import base64
import hashlib
import html
import json
from pathlib import Path

import pytest

import hard_sources_support as hs

sync_api = pytest.importorskip('playwright.sync_api')
pytest.importorskip('fontTools.ttLib')

JS = hs.script_source()
FIXTURES = hs.FIXTURES


# --- fixtures ------------------------------------------------------------------------------------

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


@pytest.fixture(scope='module')
def decoder(browser):
    """A blank page holding the decoder with the reference built, like the apps' decoder page."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    state = hs.prepare_decoder(page)
    assert state['built'] > 9000
    yield page
    ctx.close()


@pytest.fixture(scope='module')
def verified_decoder(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    # No prepare: verified glyphs must decode without ever building the reference.
    hs.push_resources(page, verified=True)
    yield page
    ctx.close()


def serve(page, origin, pages):
    """Serves {path: (body, content-type)} under origin; everything else is an empty 404."""
    def handle(route):
        url = route.request.url
        path = url[len(origin):].split('?')[0] if url.startswith(origin) else ''
        found = pages.get(path) or pages.get(path.rstrip('/') + '/') or pages.get(path.rstrip('/'))
        if found is None:
            route.fulfill(status=404, body='')
        else:
            body, kind = found
            route.fulfill(status=200, content_type=kind, body=body,
                          headers={'access-control-allow-origin': '*'})
    page.route(f'{origin}/**', handle)


def call(page, expression, *args):
    return page.evaluate(f'(args) => window.__npiaHard.{expression}(...args)', list(args))


def decode(decoder_page, payload, **options):
    return decoder_page.evaluate(
        '([payload, options]) => window.__npiaHard.qdn.decoder.decode(payload, options)', [payload, options])


def captured_payload(name='qdn_chapter76_capture.json'):
    sample = json.loads((FIXTURES / name).read_text(encoding='utf-8'))
    paragraphs = [[{'parts': [{'text': atom['text'], 'fonts': atom['fonts']}], 'mirrored': atom['mirrored']}
                   for atom in atoms if atom['src'] != 'attr'] for atoms in sample['atoms']]
    return sample | {'paragraphs': paragraphs}


def chapter76_expected():
    return json.loads((FIXTURES / 'qdn_chapter76_expected.json').read_text(encoding='utf-8'))['paragraphs']


# --- the script itself -----------------------------------------------------------------------------

def test_script_exposes_the_documented_entry_points(context):
    page = context.new_page()
    page.goto('about:blank')
    names = page.evaluate('''() => {
      const h = window.__npiaHard;
      return {version: h.version,
        qdn: Object.keys(h.qdn).sort(), decoder: Object.keys(h.qdn.decoder).sort(),
        rbooks: Object.keys(h.rbooks).sort(), util: Object.keys(h.util).sort()};
    }''')
    assert names['version'] == 1
    assert {'pageState', 'bookInfo', 'chapterState', 'encryptionState', 'readChapter', 'extract', 'userInfo',
            'installFontHook', 'decoder'} <= set(names['qdn'])
    assert {'putResource', 'prepare', 'decode', 'decodeJson', 'knownFonts', 'status', 'resourceStatus'} <= set(names['decoder'])
    assert {'productReady', 'productInfo', 'libraryProbe', 'discover', 'apiState', 'contentState', 'accessWall',
            'extract', 'ownedIds'} <= set(names['rbooks'])


def test_script_is_safe_to_load_twice_and_keeps_its_state(context):
    page = context.new_page()
    page.goto('about:blank')
    page.evaluate('() => { window.__npiaHard.marker = 7; }')
    page.evaluate(JS)  # the document-start copy and a later evaluate must not replace each other
    assert page.evaluate('() => window.__npiaHard.marker') == 7


def test_tasks_run_long_operations_to_completion_and_report_errors(context):
    page = context.new_page()
    page.goto('about:blank')
    page.evaluate("""() => {
      window.__npiaHard.test = {
        slow: async (value) => { await window.__npiaHard.util.sleep(120); return {value}; },
        broken: async () => { throw new Error('boom'); },
      };
    }""")
    ticket = page.evaluate('() => window.__npiaHard.tasks.start("test.slow", [7])')['ticket']
    assert page.evaluate('(t) => window.__npiaHard.tasks.poll(t)', ticket) == {'state': 'running'}
    for _ in range(40):
        polled = page.evaluate('(t) => window.__npiaHard.tasks.poll(t)', ticket)
        if polled['state'] != 'running':
            break
        page.wait_for_timeout(50)
    assert polled == {'state': 'done', 'json': '{"value":7}'}
    assert page.evaluate('(t) => window.__npiaHard.tasks.poll(t)', ticket)['state'] == 'error'  # collected once
    ticket = page.evaluate('() => window.__npiaHard.tasks.start("test.broken", [])')['ticket']
    page.wait_for_timeout(100)
    assert page.evaluate('(t) => window.__npiaHard.tasks.poll(t)', ticket) == {'state': 'error', 'error': 'boom'}
    with pytest.raises(Exception, match='unknown entry point'):
        page.evaluate('() => window.__npiaHard.tasks.start("test.nothing", [])')


def test_script_names_no_provider():
    from scripts.source_names import contains_name
    assert not contains_name(JS)


def test_font_hook_records_script_built_faces_and_keeps_fontface_working(context):
    page = context.new_page()
    page.goto('about:blank')
    result = page.evaluate('''() => {
      const bytes = new Uint8Array([0, 1, 0, 0]);
      const face = new FontFace('probe', bytes.buffer, {unicodeRange: 'U+4E00-9FA5'});
      const records = window.__npiaFontFaces;
      return {hooked: window.FontFace.__npiaHooked === true, isInstance: face instanceof FontFace,
        count: records.length, family: records[0].family, size: records[0].source.byteLength,
        range: records[0].descriptors.unicodeRange, text: String(window.FontFace)};
    }''')
    assert result == {'hooked': True, 'isInstance': True, 'count': 1, 'family': 'probe', 'size': 4,
                      'range': 'U+4E00-9FA5', 'text': 'function FontFace() { [native code] }'}


@pytest.mark.parametrize('size', [0, 1, 55, 56, 57, 64, 1000, 100001])
def test_sha256_and_base64_helpers_agree_with_python(context, size):
    page = context.new_page()
    page.goto('about:blank')
    data = bytes((i * 31 + 7) % 256 for i in range(size))
    encoded = base64.b64encode(data).decode()
    result = page.evaluate('''(encoded) => {
      const u = window.__npiaHard.util;
      const bytes = u.base64ToBytes(encoded);
      return {plain: u.sha256Plain(bytes), subtle: null, size: bytes.length, again: u.bytesToBase64(bytes)};
    }''', encoded)
    assert result['size'] == size
    assert result['plain'] == hashlib.sha256(data).hexdigest()
    assert result['again'] == encoded
    subtle = page.evaluate('(encoded) => window.__npiaHard.util.sha256Hex(window.__npiaHard.util.base64ToBytes(encoded))', encoded)
    assert subtle == hashlib.sha256(data).hexdigest()


def test_sha256_works_without_a_secure_context(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    page.evaluate('() => { Object.defineProperty(window, "crypto", {value: {}}); }')
    digest = page.evaluate('() => window.__npiaHard.util.sha256Hex(new Uint8Array([1, 2, 3]))')
    assert digest == hashlib.sha256(bytes([1, 2, 3])).hexdigest()
    ctx.close()


def test_unicode_range_parsing(decoder):
    parse = lambda value: decoder.evaluate('(v) => window.__npiaHard.qdn.decoder.parseUnicodeRange(v)', value)
    assert parse('U+4E00-9FA5') == [[0x4E00, 0x9FA5]]
    assert parse('U+3400-4DB5, U+20') == [[0x3400, 0x4DB5], [0x20, 0x20]]
    assert parse('U+4??') == [[0x400, 0x4FF]]
    assert parse('') == [[0, 0x10FFFF]]


def test_cmap_reader_matches_fonttools(decoder):
    from fontTools.ttLib import TTFont
    expected = sorted(TTFont(str(hs.REFERENCE_FONT)).getBestCmap())
    data = base64.b64encode(hs.REFERENCE_FONT.read_bytes()).decode()
    found = decoder.evaluate('(d) => window.__npiaHard.qdn.decoder.parseCmap(window.__npiaHard.util.base64ToBytes(d))', data)
    assert sorted(found) == expected


# --- decoding captured fonts ----------------------------------------------------------------------------

def test_complete_captured_chapter_decodes_by_shape_alone(decoder):
    """1,729 glyphs of a real chapter, matched against the reference without the verified table."""
    result = decode(decoder, captured_payload())
    assert result['lines'] == chapter76_expected()
    stats = result['stats']
    assert stats['decoded'] == stats['shapeMatchedGlyphs'] == 1729
    assert stats['verifiedGlyphs'] == 0
    assert stats['missingFonts'] == []
    assert stats['suspiciousPassthrough'] == 90


def test_verified_glyphs_never_need_the_reference(verified_decoder):
    result = decode(verified_decoder, captured_payload())
    assert result['lines'] == chapter76_expected()
    stats = result['stats']
    assert stats['decoded'] == stats['verifiedGlyphs'] == 1729
    assert stats['shapeMatchedGlyphs'] == stats['lowConfidence'] == 0
    assert verified_decoder.evaluate('() => window.__npiaHard.qdn.decoder.status().prepared') is False


def test_screenshot_backed_excerpt_decodes_exactly(decoder):
    sample = json.loads((FIXTURES / 'qdn_screenshot_excerpt.json').read_text(encoding='utf-8'))
    result = decode(decoder, sample['payload'])
    assert result['lines'] == sample['expected']
    assert result['stats']['decoded'] > 0


def test_a_font_with_a_different_digest_cannot_use_the_verified_table(verified_decoder, decoder):
    # Same code points and glyph shapes, different bytes (jitter): the table must not apply.
    mapping = {0x4E01: '借', 0x4E03: '高'}
    data = hs.cipher_font(mapping, jitter=3, seed=5)
    payload = {
        'faces': [{'family': 'X', 'range': 'U+4E00-9FA5', 'url': 'blob:1'}],
        'fontData': {'blob:1': base64.b64encode(data).decode()},
        'paragraphs': [[{'parts': [{'text': chr(0x4E01), 'fonts': 'X'}]}, {'parts': [{'text': chr(0x4E03), 'fonts': 'X'}]}]],
    }
    verified_decoder.evaluate('() => window.__npiaHard.qdn.decoder.prepare(0)')
    while not verified_decoder.evaluate('() => window.__npiaHard.qdn.decoder.prepare(10000)')['done']:
        pass
    result = decode(verified_decoder, payload)
    assert result['lines'] == ['借高']
    assert result['stats']['verifiedGlyphs'] == 0


def test_a_corrupt_verified_table_falls_back_to_matching(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    hs.push_resources(page)
    page.evaluate('([n, d]) => window.__npiaHard.qdn.decoder.putResource(n, 0, 1, d)',
                  ['verified', base64.b64encode(b'{invalid JSON').decode()])
    while not page.evaluate('() => window.__npiaHard.qdn.decoder.prepare(10000)')['done']:
        pass
    result = decode(page, captured_payload())
    assert result['lines'] == chapter76_expected()
    ctx.close()


def test_synthetic_cipher_font_with_mirrored_glyphs_and_literals(decoder):
    data = hs.cipher_font({0x4E01: '高', 0x4E03: '登', 0x4E07: '饭'}, mirrored={'登'}, jitter=4)
    payload = {
        'faces': [{'family': 'CIPHER', 'range': 'U+4E00-9FA5', 'url': 'blob:1'}],
        'fontData': {'blob:1': base64.b64encode(data).decode()},
        'paragraphs': [[
            {'parts': [{'text': chr(0x4E01), 'fonts': 'CIPHER, sans-serif'}], 'mirrored': False},
            {'parts': [{'text': chr(0x4E03), 'fonts': 'CIPHER, sans-serif'}], 'mirrored': True},
            {'parts': [{'text': '和', 'fonts': 'CIPHER, sans-serif'}], 'mirrored': False},
            {'parts': [{'text': '吃', 'fonts': 'sans-serif'}], 'mirrored': False},
            {'parts': [{'text': chr(0x4E07), 'fonts': '"CIPHER", sans-serif'}], 'mirrored': False},
        ]],
    }
    result = decode(decoder, payload)
    # 和 is in no cipher font and 吃 uses none: both stay literal.
    assert result['lines'] == ['高登和吃饭']
    assert result['stats']['lowConfidence'] == 0


def test_a_mirrored_glyph_is_not_matched_unmirrored(decoder):
    data = hs.cipher_font({0x4E01: '登'}, mirrored={'登'}, jitter=0)
    payload = {
        'faces': [{'family': 'C', 'range': 'U+4E00-9FA5', 'url': 'u'}],
        'fontData': {'u': base64.b64encode(data).decode()},
        'paragraphs': [[{'parts': [{'text': chr(0x4E01), 'fonts': 'C'}], 'mirrored': True}]],
    }
    assert decode(decoder, payload)['lines'] == ['登']


def test_the_later_face_of_a_family_takes_precedence(decoder):
    first = hs.cipher_font({0x4E01: '高'})
    second = hs.cipher_font({0x4E01: '登'})
    payload = {
        'faces': [{'family': 'F', 'range': 'U+4E00-9FA5', 'url': 'a'}, {'family': 'F', 'range': 'U+4E00-9FA5', 'url': 'b'}],
        'fontData': {'a': base64.b64encode(first).decode(), 'b': base64.b64encode(second).decode()},
        'paragraphs': [[{'parts': [{'text': chr(0x4E01), 'fonts': 'F'}]}]],
    }
    assert decode(decoder, payload)['lines'] == ['登']


def test_missing_font_and_literal_fallback_are_reported_without_deleting_text(decoder):
    payload = {
        'faces': [{'family': 'MISSING', 'range': 'U+4E00-9FFF', 'url': 'font:0'}],
        'fontData': {'font:0': 'ERR fetch failed'},
        'paragraphs': [[{'parts': [{'text': '名字，', 'fonts': 'MISSING'}]}]],
    }
    result = decode(decoder, payload)
    assert result['lines'] == ['名字，']
    assert len(result['stats']['missingFonts']) == 1
    assert result['stats']['suspiciousPassthrough'] == 2


def test_known_font_digests_let_the_next_extraction_skip_the_font_data(context, decoder):
    html_text, lines, _ = hs.build_reader(poison=False)
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, {'/chapter/1/2/': (html_text, 'text/html')})
    page.goto(hs.QDN_ORIGIN + '/chapter/1/2/')
    first = call(page, 'qdn.extract', {'known': []})
    assert len(first['fontData']) == 2 and len(first['fontDigests']) == 2
    result = decoder.evaluate('(j) => window.__npiaHard.qdn.decoder.decodeJson(j)', json.dumps(first))
    assert result['lines'] == lines
    known = decoder.evaluate('() => window.__npiaHard.qdn.decoder.knownFonts()')
    assert set(first['fontDigests'].values()) <= set(known)
    second = call(page, 'qdn.extract', {'known': known})
    assert second['fontData'] == {}
    assert sorted(second['fontDigests'].values()) == sorted(first['fontDigests'].values())
    again = decoder.evaluate('(j) => window.__npiaHard.qdn.decoder.decodeJson(j)', json.dumps(second))
    assert again['lines'] == lines
    # A digest the decoder never saw cannot be decoded without its data.
    unknown = dict(second, fontDigests={url: '0' * 64 for url in second['fontDigests']})
    result = decoder.evaluate('(j) => window.__npiaHard.qdn.decoder.decodeJson(j)', json.dumps(unknown))
    assert len(result['stats']['missingFonts']) == 2


def test_shape_matching_without_the_font_resource_says_so(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    data = hs.cipher_font({0x4E01: '高'})
    payload = {'faces': [{'family': 'C', 'range': 'U+4E00-9FA5', 'url': 'u'}],
               'fontData': {'u': base64.b64encode(data).decode()},
               'paragraphs': [[{'parts': [{'text': chr(0x4E01), 'fonts': 'C'}]}]]}
    with pytest.raises(Exception, match='reference font'):
        decode(page, payload)
    ctx.close()


def test_resources_may_arrive_in_chunks_in_any_order(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    encoded = base64.b64encode(b'abcdefghij' * 100).decode()
    pieces = [encoded[i:i + 400] for i in range(0, len(encoded), 400)]
    done = []
    for index in reversed(range(len(pieces))):
        done.append(page.evaluate('([i, c, d]) => window.__npiaHard.qdn.decoder.putResource("frequency", i, c, d)',
                                  [index, len(pieces), pieces[index]])['complete'])
    assert done == [False] * (len(pieces) - 1) + [True]
    assert page.evaluate('() => window.__npiaHard.qdn.decoder.resourceStatus()') == {'font': False, 'frequency': True, 'verified': False}
    with pytest.raises(Exception, match='unknown resource'):
        page.evaluate('() => window.__npiaHard.qdn.decoder.putResource("nope", 0, 1, "")')
    with pytest.raises(Exception, match='multiple of 4'):
        page.evaluate('() => window.__npiaHard.qdn.decoder.putResource("font", 0, 2, "AAAAA")')
    ctx.close()


def test_the_reference_builds_in_slices(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('about:blank')
    page.evaluate(JS)
    hs.push_resources(page)
    slices = 0
    while True:
        state = page.evaluate('() => window.__npiaHard.qdn.decoder.prepare(150)')
        slices += 1
        if state['done']:
            break
        assert 0 < state['built'] < state['total']
    assert slices > 1
    assert page.evaluate('() => window.__npiaHard.qdn.decoder.status().referenceChars') == state['built']
    ctx.close()


# --- extraction ---------------------------------------------------------------------------------------

def test_synthetic_reader_with_every_layer_decodes_to_the_hidden_text(context, decoder):
    html_text, lines, info = hs.build_reader()
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, {'/chapter/1/2/': (html_text, 'text/html')})
    page.goto(hs.QDN_ORIGIN + '/chapter/1/2/')
    assert call(page, 'qdn.chapterState') == {'ready': True, 'blocked': False, 'verification': False, 'encrypted': True}
    for _ in range(40):
        state = call(page, 'qdn.encryptionState')
        if state['rendered']:
            break
        page.wait_for_timeout(250)
    assert state['encrypted'] and state['rendered'] and state['words'] == sum(len(line) for line in lines)
    payload = call(page, 'qdn.extract', {'known': []})
    assert payload['unstableParagraphs'] == []
    assert payload['scriptedCount'] == 1  # the blob face, seen through the FontFace hook
    assert [face['family'] for face in payload['faces']] == ['cipherA', 'cipherB']
    assert payload['dropped'] > 0
    result = decoder.evaluate('(j) => window.__npiaHard.qdn.decoder.decodeJson(j)', json.dumps(payload))
    assert result['lines'] == lines
    assert result['stats']['missingFonts'] == []
    assert result['stats']['unstableParagraphs'] == []


def test_a_plain_read_of_the_synthetic_reader_returns_decoys_below_the_first_screens(context):
    """The negative control: without scrolling, paragraphs far from the viewport hold other text."""
    html_text, lines, _ = hs.build_reader()
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, {'/chapter/1/2/': (html_text, 'text/html')})
    page.goto(hs.QDN_ORIGIN + '/chapter/1/2/')
    page.wait_for_timeout(300)
    seen = page.evaluate('() => document.querySelectorAll("main p")[41].textContent')
    real_page = context.new_page()
    plain, _, _ = hs.build_reader(poison=False)
    serve(real_page, hs.QDN_ORIGIN, {'/chapter/1/2/': (plain, 'text/html')})
    real_page.goto(hs.QDN_ORIGIN + '/chapter/1/2/')
    assert seen != real_page.evaluate('() => document.querySelectorAll("main p")[41].textContent')


def test_poisoned_paragraphs_swap_to_the_real_text_only_near_the_viewport(context):
    html_text, lines, _ = hs.build_reader()
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, {'/chapter/1/2/': (html_text, 'text/html')})
    page.goto(hs.QDN_ORIGIN + '/chapter/1/2/')
    page.wait_for_timeout(300)
    before = page.evaluate('() => document.querySelectorAll("main p")[39].innerHTML')
    page.evaluate('() => document.querySelectorAll("main p")[39].scrollIntoView()')
    page.wait_for_timeout(300)
    near = page.evaluate('() => document.querySelectorAll("main p")[39].innerHTML')
    assert before != near


def test_complete_chapter_browser_replay_matches_the_screenshot(context, verified_decoder):
    """Real captured fonts and glyphs in a reconstructed DOM (the page itself is not a live capture)."""
    sample = json.loads((FIXTURES / 'qdn_chapter76_capture.json').read_text(encoding='utf-8'))
    expected = chapter76_expected()
    rules = ['main p {font:20px sans-serif;display:flex;flex-wrap:wrap;margin:0 0 12px}',
             'main span{display:inline-block} .before::before{content:attr(data-real)}',
             '.after::after{content:attr(data-real)} .flip{transform:scaleX(-1)}']
    for face in sample['faces']:
        rules.append('@font-face{font-family:' + face['family'] + ';src:url(data:font/woff2;base64,'
                     + sample['fontData'][face['url']] + ');unicode-range:' + face['range'] + '}')
    paragraphs = []
    for atoms in sample['atoms']:
        spans = []
        decoys = ''.join(a['text'] for a in atoms if a['src'] == 'attr')
        for position, atom in enumerate(atoms):
            if atom['src'] == 'attr':
                continue
            generated = atom['src'] in ('before', 'after')
            css = atom['src'] if generated else ''
            if atom['mirrored']:
                css += ' flip'
            text = html.escape(atom['text'], quote=True)
            attrs = f' data-real="{text}"' if generated else ''
            spans.append(f'<span class="{css}"{attrs} data-unused="{html.escape(decoys, quote=True)}" '
                         f'style="order:{position};font-family:{atom["fonts"]}">'
                         + ('' if generated else text) + '</span>')
        paragraphs.append('<p>' + ''.join(reversed(spans)) + '</p>')  # painted order differs from DOM order
    page = context.new_page()
    page.set_content('<style>' + ''.join(rules) + '</style><main>' + ''.join(paragraphs) + '</main>')
    for zoom in (1, 0.2):
        page.evaluate('(zoom) => document.documentElement.style.zoom = zoom', zoom)
        payload = call(page, 'qdn.extract', {'known': []})
        result = decode(verified_decoder, payload)
        assert len(result['lines']) == len(expected) == 94
        assert result['lines'] == expected
        stats = result['stats']
        assert stats['missingFonts'] == []
        assert stats['suspiciousPassthrough'] == 90
        assert stats['unstableParagraphs'] == []
        assert stats['verifiedGlyphs'] == stats['decoded'] and stats['shapeMatchedGlyphs'] == 0


def extracted_text(payload):
    return [''.join(part['text'] for item in row for part in item['parts']) for row in payload['paragraphs']]


def test_extraction_reads_what_is_painted_in_visual_order(context):
    page = context.new_page()
    page.set_content('''<!doctype html><meta charset="utf-8">
<style>
  main p { display: flex; flex-wrap: wrap; font: 20px sans-serif; margin: 0 0 12px; }
  .a { order: 3 } .b { order: 1 } .c { order: 2 } .d { order: 4 }
  .d::after { content: attr(data-x); }
  .sy-0 { display: none; }
  .ghost { visibility: hidden; } .ghost .back { visibility: visible; }
  .spacer { height: 3000px; }
</style>
<main class="r-font-encrypt" id="c-1">
  <p><i class="a">三</i><i class="b">一</i><i class="c">二</i><i class="d" data-x="四"></i></p>
  <p>甲<y class="sy-0"><samp>假</samp></y>乙<span class="review"><span>9</span></span></p>
  <p class="ghost">隐<b class="back">现</b></p>
  <div class="spacer"></div>
  <p id="late">毒</p>
</main>
<script>
  new FontFace('Scripted', new Uint8Array([1, 2, 3]).buffer, {unicodeRange: 'U+4E00-9FA5'});
  new IntersectionObserver((entries) => entries.forEach((entry) => {
    entry.target.textContent = entry.isIntersecting ? '真' : '毒';
  })).observe(document.getElementById('late'));
</script>''')
    payload = call(page, 'qdn.extract', {})
    assert extracted_text(payload) == ['一二三四', '甲乙', '现', '真']
    assert payload['scriptedCount'] == 0  # never added to document.fonts


def test_extraction_without_a_reader_says_so(context):
    page = context.new_page()
    page.set_content('<p>nothing here</p>')
    assert call(page, 'qdn.extract', {}) == {'error': 'no reader'}


def test_root_wrapped_positioned_pseudos_and_clipped_decoys(context):
    page = context.new_page()
    page.set_content('''<style>
        p{font:20px sans-serif;width:60px;margin:0;line-height:30px}
        .root::before{content:'甲乙丙丁'} .root::after{content:'戊己'}
        .clip{display:inline-block;width:0;height:0;overflow:hidden}
        .clip::before{content:'假'}
        .placed{position:relative;display:inline-block;width:40px;height:30px}
        .placed::before{content:'甲';position:absolute;left:20px}
        .placed::after{content:'乙';position:absolute;left:0}
        </style><main><p class="root"><span class="clip">毒</span></p>
        <p><span class="placed"></span></p></main>''')
    assert extracted_text(call(page, 'qdn.extract', {})) == ['甲乙丙丁戊己', '乙甲']


@pytest.mark.parametrize('zoom', [1, 0.2, 0.08])
def test_pseudo_geometry_preserves_repeats_and_delayed_attribute_swaps(context, zoom):
    page = context.new_page()
    page.set_content('''<!doctype html><meta charset="utf-8"><style>
        main p { font:20px sans-serif; }
        .around::before {content:'甲'} .around::after {content:'丙'}
        .echo {position:relative;display:inline-block} .echo::after {content:'哈';position:absolute;left:0;top:0}
        .late::after {content:attr(data-text)}
        .hidden::after {content:'假';opacity:0}
        </style><main><p><span class="around">乙</span>哈哈<span class="echo">哈</span></p>
        <p><span class="late" data-text="毒"></span><span class="hidden"></span></p></main>
        <script>window.requestAnimationFrame = () => 0;
        setTimeout(() => document.querySelector('.late').dataset.text='真', 650);</script>''')
    page.evaluate('(zoom) => document.documentElement.style.zoom = zoom', zoom)
    payload = call(page, 'qdn.extract', {})
    assert extracted_text(payload) == ['甲乙丙哈哈哈', '真']
    assert payload['duplicates'] == 1
    assert payload['unstableParagraphs'] == []


def test_continuously_changing_capture_is_flagged(context):
    page = context.new_page()
    page.set_content('<main><p>甲</p></main><script>setInterval(() => {const p=document.querySelector("p");'
                     'p.textContent=p.textContent==="甲"?"乙":"甲"},50)</script>')
    assert call(page, 'qdn.extract', {})['unstableParagraphs'] == [0]


def test_nested_mirrors_cancel_but_pseudo_mirror_is_preserved(context):
    page = context.new_page()
    page.set_content('''<style>
        main,.inner {transform:scaleX(-1)}
        .pseudo::after {content:'甲';display:inline-block;transform:scaleX(-1)}
        </style><main><div class="inner"><p>乙<span class="pseudo"></span></p></div></main>''')
    items = {item['parts'][0]['text']: item for item in call(page, 'qdn.extract', {})['paragraphs'][0]}
    assert items['乙']['mirrored'] is False
    assert items['甲']['mirrored'] is True


# --- qdn book and chapter pages -----------------------------------------------------------------------

def qdn_pages(**extra):
    pages = {
        '/book/1049836819/': (hs.page_text('qdn_book.html'), 'text/html'),
        '/chapter/1049836819/1001/': (hs.page_text('qdn_chapter_free.html'), 'text/html'),
        '/chapter/1049836819/1003/': (hs.page_text('qdn_chapter_plain_bought.html'), 'text/html'),
        '/verify/': (hs.page_text('qdn_verification.html'), 'text/html'),
        '/waf/': (hs.page_text('qdn_waf.html'), 'text/html'),
    }
    pages.update(extra)
    return pages


def test_book_page_is_ready_and_describes_the_book(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/book/1049836819/')
    state = call(page, 'qdn.pageState')
    assert state['state'] == 'ready' and state['catalogCount'] == 7 and state['bookName'] is True
    info = call(page, 'qdn.bookInfo')
    assert info['title'] == '示例之书'
    assert info['author'] == '张三'
    assert info['coverUrl'] == 'https://bookcover.yuewen.com/qdbimg/349573/1049836819/180'
    assert info['description'] == '清晨的小镇，古老的高塔。 一把生锈的钥匙。'
    assert info['tags'] == ['玄幻', '热血']
    assert [(c['name'], c['isVip']) for c in info['chapters']] == [
        ('第一章 清晨', False), ('第二章 高塔', False), ('第三章 钥匙', True), ('第四章 铁门', True),
        ('第五章 希望之地', True), ('第六章 钥匙', True), ('第七章 免费', False)]
    assert [c['url'] for c in info['chapters']] == [
        f'https://www.{hs.QDN_DOMAIN}/chapter/1049836819/{n}/' for n in (1001, 1002, 1003, 1004, 2001, 2002, 3001)]


def test_long_books_report_their_total_and_link_the_full_catalog(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/book/1049836819/')
    info = call(page, 'qdn.bookInfo')
    assert info['totalChapters'] == 1418
    assert info['catalogUrl'] == f'https://www.{hs.QDN_DOMAIN}/book/1049836819/catalog/'


def test_the_full_catalog_page_lists_every_chapter_with_its_volume_s_vip_flag(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages(**{'/book/1049836819/catalog/': (hs.page_text('qdn_catalog.html'), 'text/html')}))
    page.goto(hs.QDN_ORIGIN + '/book/1049836819/catalog/')
    state = call(page, 'qdn.catalogState')
    assert state['state'] == 'ready' and state['count'] == 6
    info = call(page, 'qdn.catalogInfo')
    assert [(c['name'], c['isVip']) for c in info['chapters']] == [
        ('第一章 清晨', False), ('第二章 高塔', False),
        ('第一章 希望之地', True), ('第二章 钥匙', True), ('第三章 铁门', True)]
    assert [c['url'].split('/')[-2] for c in info['chapters']] == ['1001', '1002', '2001', '2002', '2003']
    assert [(v['count'], v['isVip']) for v in info['volumes']] == [(2, False), (3, True)]


def test_the_catalog_page_is_told_apart_from_a_captcha_and_a_slow_load(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/verify/')
    assert call(page, 'qdn.catalogState')['state'] == 'verification'
    page.goto(hs.QDN_ORIGIN + '/waf/')
    assert call(page, 'qdn.catalogState')['state'] == 'blocked'
    page.set_content('<p>loading</p>')
    assert call(page, 'qdn.catalogState')['state'] == 'waiting'


def test_captcha_and_firewall_pages_are_told_apart_from_a_slow_book_page(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/verify/')
    assert call(page, 'qdn.pageState')['state'] == 'verification'
    assert call(page, 'qdn.chapterState')['verification'] is True
    page.goto(hs.QDN_ORIGIN + '/waf/')
    assert call(page, 'qdn.pageState')['state'] == 'blocked'
    assert call(page, 'qdn.chapterState')['blocked'] is True
    page.set_content('<h1>loading</h1>')
    assert call(page, 'qdn.pageState')['state'] == 'waiting'


def test_free_chapter_text_drops_reviews_and_stray_numbers(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/chapter/1049836819/1001/')
    state = call(page, 'qdn.chapterState')
    assert state == {'ready': True, 'blocked': False, 'verification': False, 'encrypted': False}
    result = call(page, 'qdn.readChapter')
    assert result['lines'][0].startswith('清晨的风吹过')
    assert len(result['lines']) == 4
    assert all('12' not in line for line in result['lines'])


def test_plain_bought_chapter_reads_without_decoding(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/chapter/1049836819/1003/')
    assert call(page, 'qdn.readChapter')['lines'][0].startswith('老人接过信')


@pytest.mark.parametrize('login,reason', [(0, 'login'), (1, 'purchase')])
def test_unbought_vip_chapter_is_refused_with_the_reason(context, login, reason):
    page = context.new_page()
    body = hs.page_text('qdn_chapter_vip_guest.html').replace('{LOGIN}', str(login))
    serve(page, hs.QDN_ORIGIN, qdn_pages(**{'/chapter/1049836819/1003/': (body, 'text/html')}))
    page.goto(hs.QDN_ORIGIN + '/chapter/1049836819/1003/')
    result = call(page, 'qdn.readChapter')
    assert result['error'] == 'locked' and result['reason'] == reason
    assert result['expected'] == 2600 and result['chars'] > 0


def test_a_captcha_page_is_reported_as_verification_when_reading(context):
    page = context.new_page()
    serve(page, hs.QDN_ORIGIN, qdn_pages())
    page.goto(hs.QDN_ORIGIN + '/verify/')
    assert call(page, 'qdn.readChapter') == {'error': 'verification'}


@pytest.mark.parametrize('reply,live', [
    ({'code': 0, 'data': {'isLogin': 1}}, True),
    ({'code': 0, 'data': {'isLogin': 0}}, False),
    ({'code': 1000, 'msg': 'not signed in'}, False),
    (None, None),
])
def test_user_info_tells_a_live_session_from_an_expired_one(context, reply, live):
    page = context.new_page()
    body = json.dumps(reply) if reply is not None else '<html>WAF</html>'
    pages = qdn_pages(**{'/ajax/UserInfo/GetUserInfo': (body, 'application/json' if reply else 'text/html')})
    serve(page, hs.QDN_ORIGIN, pages)
    page.goto(hs.QDN_ORIGIN + '/book/1049836819/')
    assert call(page, 'qdn.userInfo') == {'live': live}


# --- rbooks ---------------------------------------------------------------------------------------------

def rbooks_pages():
    return {
        '/books/6251000001': (hs.page_text('rbooks_product.html'), 'text/html'),
        '/books/6251000001/view': (hs.page_text('rbooks_viewer.html'), 'text/html'),
        '/books/6251000009/view': (hs.page_text('rbooks_viewer_refused.html'), 'text/html'),
        '/books/6251000008/view': (hs.page_text('rbooks_wall.html'), 'text/html'),
        '/books/6251000008': (hs.page_text('rbooks_wall.html'), 'text/html'),
        '/library/books/8706175/': (hs.page_text('rbooks_library.html'), 'text/html'),
    }


def test_product_page_metadata_and_rendered_episodes(context):
    page = context.new_page()
    serve(page, hs.RBOOKS_ORIGIN, rbooks_pages())
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000001')
    assert call(page, 'rbooks.productReady') is True
    info = call(page, 'rbooks.productInfo')
    assert info['title'] == '예시 소설'
    assert info['author'] == '홍길동'
    assert info['publisher'] == '예시 출판'
    assert info['cover'] == f'https://img.{hs.RBOOKS_CDN}/cover/6251000001/xxlarge'
    assert '용사는 탑으로 향한다' in info['synopsis'] and '가입하면' not in info['synopsis']
    assert info['seriesId'] == '6251000001'
    assert info['links'] == [f'{hs.RBOOKS_ORIGIN}/books/6251000001/view', f'{hs.RBOOKS_ORIGIN}/books/6251000002/view']
    assert info['titleById']['6251000002'] == '예시 소설 2화'
    assert info['tags'] == ['판타지', '모험']
    assert 'Rendered episode links: 3' in info['diagnostics']


def test_a_product_page_without_a_title_is_not_ready(context):
    page = context.new_page()
    page.set_content('<div>loading</div>')
    assert call(page, 'rbooks.productReady') is False


def test_library_page_resolves_the_product_and_reports_unavailable_titles(context):
    page = context.new_page()
    serve(page, hs.RBOOKS_ORIGIN, rbooks_pages())
    page.goto(hs.RBOOKS_ORIGIN + '/library/books/8706175/')
    probe = call(page, 'rbooks.libraryProbe')
    assert probe['product'] == f'{hs.RBOOKS_ORIGIN}/books/6121000538'  # the lowest volume id
    assert probe['unavailable'] is True and probe['adult'] is True and probe['viewer'] is False
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000001/view')
    probe = call(page, 'rbooks.libraryProbe')
    assert probe['viewer'] is True and probe['product'].endswith('/books/6251000001/view')


def test_viewer_content_is_xhtml_with_local_image_names(context):
    page = context.new_page()
    serve(page, hs.RBOOKS_ORIGIN, rbooks_pages())
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000001/view')
    state = call(page, 'rbooks.contentState')
    assert state['found'] and state['ready'] and state['refused'] is False and state['length'] > 500
    assert call(page, 'rbooks.accessWall')['wall'] is False
    result = call(page, 'rbooks.extract', {'fallbackTitle': 'fallback', 'chapterIndex': 7})
    assert result['title'] == '1화 시작'
    assert result['images'] == [
        {'url': f'https://img.{hs.RBOOKS_CDN}/pages/6251000001/001.jpg?cache=1', 'name': 'images/0007_01.jpg'},
        {'url': f'{hs.RBOOKS_ORIGIN}/pages/002.png', 'name': 'images/0007_02.png'}]
    content = result['content']
    assert content.count('src="images/0007_01.jpg"') == 2 and 'src="images/0007_02.png"' in content
    for forbidden in ('class=', 'style=', 'data-', '<script', 'content_footer', '다음 화로', '<hr', '&nbsp;', '​', 'id='):
        assert forbidden not in content
    assert '<blockquote>' in content and 'align="center"' in content and '<br />' in content
    assert '첫 번째 문단입니다. 용사는 이른 아침 마을을 떠났다. 바람은 차가웠다.'.replace(' ', '&#160;') in content or '마을을 떠났다.' in content
    assert result['contentText'].splitlines()[0].startswith('첫 번째 문단입니다.')
    # The EPUB needs well-formed XML.
    parsed = page.evaluate('''(content) => {
      const doc = new DOMParser().parseFromString('<div>' + content + '</div>', 'application/xml');
      return doc.getElementsByTagName('parsererror').length;
    }''', content)
    assert parsed == 0


def test_extract_leaves_the_live_page_alone(context):
    page = context.new_page()
    serve(page, hs.RBOOKS_ORIGIN, rbooks_pages())
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000001/view')
    before = page.evaluate('() => document.body.innerHTML')
    call(page, 'rbooks.extract', {'fallbackTitle': 'x', 'chapterIndex': 1})
    assert page.evaluate('() => document.body.innerHTML') == before


def test_viewer_refusals_and_purchase_walls_are_classified(context):
    page = context.new_page()
    serve(page, hs.RBOOKS_ORIGIN, rbooks_pages())
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000009/view')
    assert call(page, 'rbooks.contentState')['refused'] is True
    wall = call(page, 'rbooks.accessWall')
    assert wall['wall'] is True and wall['refused'] is True
    assert call(page, 'rbooks.extract', {'fallbackTitle': 'x', 'chapterIndex': 1}) is None
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000008/view')
    wall = call(page, 'rbooks.accessWall')
    assert wall['wall'] is True and wall['refused'] is False and wall['hits'] >= 2
    page.goto(hs.RBOOKS_ORIGIN + '/books/6251000008')
    assert call(page, 'rbooks.accessWall')['offViewer'] is True


def book_json(book_id, total, serial, next_id=None, title=None):
    return json.dumps({
        'title': {'main': title or f'Series {book_id}'},
        'series': {'property': {
            'total_book_count': total, 'is_serial': serial,
            'next_books': {next_id: {}} if next_id else {},
        }},
    })


def serve_api(page, books, calls=None, fail=()):
    def handle(route):
        book_id = route.request.url.rstrip('/').split('/')[-1]
        if calls is not None:
            calls.append(book_id)
        if book_id in fail:
            route.fulfill(status=503, body='')
        elif book_id in books:
            route.fulfill(status=200, content_type='application/json', body=books[book_id],
                          headers={'access-control-allow-origin': '*'})
        else:
            route.fulfill(status=404, body='')
    page.route(f'{hs.RBOOKS_API}/books/*', handle)


def run_discover(page, **first):
    out = None
    state = None
    for _ in range(50):
        out = call(page, 'rbooks.discover', dict(first, state=state, budgetMs=20000) if state else dict(first, budgetMs=20000))
        state = out['state']
        if out['done'] or out['stalled']:
            break
    return out


def api_page(context):
    page = context.new_page()
    page.route(f'{hs.RBOOKS_API}/start', lambda route: route.fulfill(status=200, content_type='text/html', body='<body>{}</body>'))
    return page


def test_contiguous_serial_catalog_is_validated_with_one_request(context):
    page = api_page(context)
    calls = []
    books = {
        '6251000001': book_json('6251000001', 5, True, '6251000002', 'Origin 1화'),
        '6251000005': book_json('6251000005', 5, True, None, 'Origin 5화'),
    }
    serve_api(page, books, calls)
    page.goto(f'{hs.RBOOKS_API}/start')
    rendered = [f'{hs.RBOOKS_ORIGIN}/books/6251000001/view', f'{hs.RBOOKS_ORIGIN}/books/6251000002/view']
    out = run_discover(page, seriesId='6251000001', renderedLinks=rendered)
    assert out['done'] and out['isSerial'] is True and out['error'] == ''
    assert [link.split('/')[-2] for link in out['links']] == [f'62510000{n:02d}' for n in range(1, 6)]
    assert out['titleById']['6251000003'] == 'Origin 3화'
    assert out['titleById']['6251000005'] == 'Origin 5화'
    assert calls == ['6251000001', '6251000005']
    assert any('Fast episode catalog: 5' in line for line in out['diagnostics'])


def test_a_catalog_that_is_not_contiguous_is_followed_one_episode_at_a_time(context):
    page = api_page(context)
    ids = ['6251000001', '6251000007', '6251000012', '6251000040']
    books = {}
    for index, book_id in enumerate(ids):
        following = ids[index + 1] if index + 1 < len(ids) else None
        books[book_id] = book_json(book_id, 4, True, following, f'Ep {index + 1}')
    serve_api(page, books)
    page.goto(f'{hs.RBOOKS_API}/start')
    out = run_discover(page, seriesId='6251000001', renderedLinks=[f'{hs.RBOOKS_ORIGIN}/books/6251000001/view'])
    assert out['done'] and [link.split('/')[-2] for link in out['links']] == ids
    assert out['titleById'] == {book_id: f'Ep {i + 1}' for i, book_id in enumerate(ids)}
    assert any('Chained episode links: 4' in line for line in out['diagnostics'])


def test_a_fast_catalog_that_fails_validation_falls_back_to_the_chain(context):
    page = api_page(context)
    books = {
        '100': book_json('100', 3, True, '101', 'A'),
        '101': book_json('101', 3, True, '105', 'B'),  # not contiguous after all
        '105': book_json('105', 3, True, None, 'C'),
        '102': book_json('102', 3, True, '103', 'wrong last'),  # the generated last id still has a next
    }
    serve_api(page, books)
    page.goto(f'{hs.RBOOKS_API}/start')
    out = run_discover(page, seriesId='100', renderedLinks=[f'{hs.RBOOKS_ORIGIN}/books/100/view', f'{hs.RBOOKS_ORIGIN}/books/101/view'])
    assert [link.split('/')[-2] for link in out['links']] == ['100', '101', '105']


def test_discovery_resumes_across_calls_when_its_budget_runs_out(context):
    page = api_page(context)
    ids = [str(2000 + step * 3) for step in range(30)]
    books = {book_id: book_json(book_id, 30, True, ids[i + 1] if i + 1 < len(ids) else None) for i, book_id in enumerate(ids)}
    serve_api(page, books)
    page.goto(f'{hs.RBOOKS_API}/start')
    first = call(page, 'rbooks.discover', {'seriesId': ids[0], 'renderedLinks': [], 'budgetMs': 1})
    assert first['done'] is False and first['stalled'] is False and len(first['state']['ids']) < 30
    out = first
    for _ in range(40):
        if out['done']:
            break
        out = call(page, 'rbooks.discover', {'state': out['state'], 'budgetMs': 1})
    assert out['done'] and [link.split('/')[-2] for link in out['links']] == ids


def test_volume_ebooks_keep_one_entry_each_and_single_volumes_have_no_catalog(context):
    page = api_page(context)
    serve_api(page, {
        '10': book_json('10', 3, False, '11', 'Vol 1'),
        '11': book_json('11', 3, False, '12', 'Vol 2'),
        '12': book_json('12', 3, False, None, 'Vol 3'),
        '20': book_json('20', 1, False, None, 'Only'),
    })
    page.goto(f'{hs.RBOOKS_API}/start')
    out = run_discover(page, seriesId='10', renderedLinks=[])
    assert out['done'] and out['isSerial'] is False
    assert [link.split('/')[-2] for link in out['links']] == ['10', '11', '12']
    out = run_discover(page, seriesId='20', renderedLinks=[])
    assert out['done'] and out['links'] == []


def test_a_silent_api_stalls_and_can_be_given_up_on_with_what_was_found(context):
    page = api_page(context)
    serve_api(page, {
        '1': book_json('1', 9, True, '5', 'one'),
        '5': book_json('5', 9, True, '9', 'five'),
    }, fail=('9',))
    page.goto(f'{hs.RBOOKS_API}/start')
    out = run_discover(page, seriesId='1', renderedLinks=[])
    assert out['stalled'] is True and out['done'] is False and out['state']['nextId'] == '9'
    final = call(page, 'rbooks.discover', {'state': out['state'], 'giveUp': True})
    assert final['done'] and [link.split('/')[-2] for link in final['links']] == ['1', '5']


def test_an_api_that_never_answers_ends_with_an_error(context):
    page = api_page(context)
    serve_api(page, {}, fail=())
    page.goto(f'{hs.RBOOKS_API}/start')
    out = call(page, 'rbooks.discover', {'seriesId': '1', 'renderedLinks': [], 'budgetMs': 1000})
    assert out['done'] and out['links'] == [] and 'did not return JSON' in out['error']


def test_api_page_state_distinguishes_json_from_a_bot_check(context):
    page = context.new_page()
    page.route(f'{hs.RBOOKS_API}/**', lambda route: route.fulfill(
        status=200, content_type='text/html',
        body='<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>'))
    page.goto(f'{hs.RBOOKS_API}/books/1')
    state = call(page, 'rbooks.apiState')
    assert state['json'] is False and state['challenge'] is True
    page.unroute(f'{hs.RBOOKS_API}/**')
    page.route(f'{hs.RBOOKS_API}/**', lambda route: route.fulfill(status=200, content_type='application/json', body='{"title": "x"}'))
    page.goto(f'{hs.RBOOKS_API}/books/2')
    state = call(page, 'rbooks.apiState')
    assert state['json'] is True and state['challenge'] is False


def test_owned_ids_come_from_the_library_api(context):
    page = context.new_page()
    seen = {}

    def handle(route):
        request = route.request
        headers = {'access-control-allow-origin': hs.RBOOKS_LIBRARY, 'access-control-allow-credentials': 'true',
                   'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'POST'}
        if request.method == 'OPTIONS':
            route.fulfill(status=204, headers=headers)
            return
        seen['body'] = json.loads(request.post_data)
        route.fulfill(status=200, content_type='application/json', headers=headers,
                      body=json.dumps({'items': [{'b_id': '101'}, {'b_id': 103}]}))
    page.route(f'{hs.RBOOKS_LIBRARY_API}/items', handle)
    page.route(f'{hs.RBOOKS_LIBRARY}/**', lambda route: route.fulfill(status=200, content_type='text/html', body='<body></body>'))
    page.goto(f'{hs.RBOOKS_LIBRARY}/')
    assert call(page, 'rbooks.ownedIds', ['101', '102', '103']) == {'owned': ['101', '103']}
    assert seen['body'] == {'b_ids': ['101', '102', '103']}
    page.unroute(f'{hs.RBOOKS_LIBRARY_API}/items')
    page.route(f'{hs.RBOOKS_LIBRARY_API}/items', lambda route: route.fulfill(
        status=401, headers={'access-control-allow-origin': hs.RBOOKS_LIBRARY, 'access-control-allow-credentials': 'true',
                             'access-control-allow-headers': 'content-type'}, body=''))
    assert call(page, 'rbooks.ownedIds', ['101'])['owned'] is None
