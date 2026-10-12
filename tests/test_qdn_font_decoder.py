import base64
import io
import os
import json
import html
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip('numpy')
ttLib = pytest.importorskip('fontTools.ttLib')

import qdn_font_decoder as q  # noqa: E402

BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'fonts', 'NotoSansCJKsc-Regular-hanzi.otf')


def _cipher_font(mapping, mirrored=()):
    """A TrueType font whose code points draw other characters' outlines.

    ``mapping`` maps cipher code point -> the character its glyph shows.
    Characters in ``mirrored`` are stored flipped, as Qdn stores some.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    source = ttLib.TTFont(BUNDLED)
    glyphs = source.getGlyphSet()
    cmap = source.getBestCmap()
    order = ['.notdef'] + [f'g{i}' for i in range(len(mapping))]
    outlines = {'.notdef': TTGlyphPen(None).glyph()}
    metrics = {'.notdef': (1000, 0)}
    for index, (cipher, real) in enumerate(mapping.items()):
        pen = TTGlyphPen(None)
        target = Cu2QuPen(pen, 1.0, reverse_direction=True)
        if real in mirrored:
            target = TransformPen(target, (-1, 0, 0, 1, 1000, 0))
        glyphs[cmap[ord(real)]].draw(target)
        outlines[f'g{index}'] = pen.glyph()
        metrics[f'g{index}'] = (1000, 0)
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(c): f'g{i}' for i, c in enumerate(mapping)})
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=880, descent=-120)
    builder.setupNameTable({'familyName': 'Cipher', 'styleName': 'Regular'})
    builder.setupOS2(sTypoAscender=880, usWinAscent=880, usWinDescent=120)
    builder.setupPost()
    out = io.BytesIO()
    builder.save(out)
    return out.getvalue()


@pytest.mark.skipif(not os.path.exists(BUNDLED), reason='bundled font missing')
def test_cipher_glyphs_decode_to_the_characters_they_draw():
    data = _cipher_font({'丁': '高', '七': '登', '万': '饭'}, mirrored={'登'})
    payload = {
        'faces': [{'family': 'CIPHER', 'range': 'U+4E00-9FA5', 'url': 'blob:1'}],
        'fontData': {'blob:1': base64.b64encode(data).decode()},
        'paragraphs': [[
            {'parts': [{'text': '丁', 'fonts': 'CIPHER, sans-serif'}], 'mirrored': False},
            {'parts': [{'text': '七', 'fonts': 'CIPHER, sans-serif'}], 'mirrored': True},
            {'parts': [{'text': '和', 'fonts': 'CIPHER, sans-serif'}], 'mirrored': False},
            {'parts': [{'text': '吃', 'fonts': 'sans-serif'}], 'mirrored': False},
            {'parts': [{'text': '万', 'fonts': 'CIPHER, sans-serif'}], 'mirrored': False},
        ]],
    }
    lines, stats = q.decode_payload(payload)
    # 和 is not in the cipher font and 吃 uses no cipher font: both literal.
    assert lines == ['高登和吃饭']
    assert stats['low_confidence'] == 0


def test_later_face_of_a_family_takes_precedence():
    decoder = q.QdnFontDecoder.__new__(q.QdnFontDecoder)
    first = type('Face', (), {'family': 'F', 'covers': lambda self, ch: True})()
    second = type('Face', (), {'family': 'F', 'covers': lambda self, ch: True})()
    decoder.fonts = [first, second]
    assert decoder._cipher_for('字', ['F']) is second


def test_unicode_range_parsing():
    assert q._parse_unicode_range('U+4E00-9FA5') == [(0x4E00, 0x9FA5)]
    assert q._parse_unicode_range('U+3400-4DB5, U+20') == [(0x3400, 0x4DB5), (0x20, 0x20)]
    assert q._parse_unicode_range('U+4??') == [(0x400, 0x4FF)]
    assert q._parse_unicode_range('') == [(0, 0x10FFFF)]


def test_screenshot_backed_excerpt_decodes_exactly():
    sample = json.loads((Path(__file__).parent / 'fixtures' / 'qdn_screenshot_excerpt.json').read_text(encoding='utf-8'))
    lines, stats = q.decode_payload(sample['payload'])
    assert lines == sample['expected']
    assert stats['decoded'] > 0


@pytest.mark.parametrize('zoom', [1, 0.2])
def test_complete_chapter_browser_replay_matches_screenshot(zoom):
    """Real captured fonts/glyphs in reconstructed DOM, not a live reader."""
    sync_api = pytest.importorskip('playwright.sync_api')
    folder = Path(__file__).parent / 'fixtures'
    sample = json.loads((folder / 'qdn_chapter76_capture.json').read_text(encoding='utf-8'))
    expected = json.loads((folder / 'qdn_chapter76_expected.json').read_text(encoding='utf-8'))['paragraphs']
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
                continue  # stored attribute values have no painted box
            generated = atom['src'] in ('before', 'after')
            css = atom['src'] if generated else ''
            if atom['mirrored']:
                css += ' flip'
            text = html.escape(atom['text'], quote=True)
            attrs = f' data-real="{text}"' if generated else ''
            spans.append(f'<span class="{css}"{attrs} data-unused="{html.escape(decoys, quote=True)}" '
                         f'style="order:{position};font-family:{atom["fonts"]}">'
                         + ('' if generated else text) + '</span>')
        # Reverse DOM order to ensure extraction follows the painted order.
        paragraphs.append('<p>' + ''.join(reversed(spans)) + '</p>')
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1000, 'height': 900})
        page.set_content('<style>' + ''.join(rules) + '</style><main>' + ''.join(paragraphs) + '</main>')
        page.evaluate('(zoom) => document.documentElement.style.zoom = zoom', zoom)
        payload = page.evaluate(q.QDN_EXTRACT_JS)
        browser.close()
    lines, stats = q.decode_payload(payload)
    assert len(lines) == len(expected) == 94
    assert lines == expected
    assert stats['missing_fonts'] == []
    # The captured fonts intentionally omit some legitimate fallback CJK.
    # Preserve these and report them, rather than stripping them as decoys.
    assert stats['suspicious_passthrough'] == 90
    assert stats['unstable_paragraphs'] == []
    refinement = [item for item in stats['diagnostics'] if 'component_refinement' in item['reasons']]
    assert any(item['character'] == '借' for item in refinement)


def test_root_wrapped_positioned_pseudos_and_clipped_decoys():
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
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
        payload = page.evaluate(q.QDN_EXTRACT_JS)
        browser.close()
    text = [''.join(part['text'] for item in row for part in item['parts']) for row in payload['paragraphs']]
    assert text == ['甲乙丙丁戊己', '乙甲']


def test_reference_includes_supported_rare_hanzi(tmp_path, monkeypatch):
    rare = '㐀'
    reference_path = tmp_path / 'reference.ttf'
    reference_path.write_bytes(_cipher_font({rare: '高'}))
    monkeypatch.setattr(q, '_reference_font_paths', lambda: [str(reference_path)])
    monkeypatch.setattr(q, '_common_hanzi', lambda: [])
    monkeypatch.setattr(q, '_load_frequency', lambda: {})
    reference = q._Reference()
    assert rare in reference.chars
    monkeypatch.setattr(q._Reference, '_instance', reference)
    monkeypatch.setattr(q, '_GLYPH_CACHE', {})
    data = _cipher_font({'丁': '高'})
    lines, _ = q.decode_payload({
        'faces': [{'family': 'RARE', 'range': 'U+4E00-9FFF', 'url': 'rare'}],
        'fontData': {'rare': base64.b64encode(data).decode()},
        'paragraphs': [[{'parts': [{'text': '丁', 'fonts': 'RARE'}]}]],
    })
    assert lines == [rare]


@pytest.mark.parametrize('second_score,expected', [(0.98, '罕'), (0.995, '常')])
def test_frequency_cannot_override_a_stronger_shape(monkeypatch, second_score, expected):
    monkeypatch.setattr(q, '_GLYPH_CACHE', {})
    monkeypatch.setattr(q, '_vector', lambda *_args: np.array([1., 0.]))
    decoder = q.QdnFontDecoder.__new__(q.QdnFontDecoder)
    decoder.reference = SimpleNamespace(chars=['罕', '常'], matrix=np.array([[1., 0.], [second_score, 0.]]),
                                        prior=np.array([0., 1.]))
    cipher = type('Cipher', (), {'digest': 'test', 'font': None})()
    decoder._resolve([(cipher, '丁', False)])
    match = q._GLYPH_CACHE[('test', '丁', False)]
    assert match['character'] == expected
    assert 'ambiguous_shape' in match['reasons']
    assert len(match['candidates']) == 2


def test_missing_font_and_literal_fallback_are_reported_without_deleting_text():
    lines, stats = q.decode_payload({
        'faces': [{'family': 'MISSING', 'range': 'U+4E00-9FFF', 'url': 'font:0'}],
        'fontData': {'font:0': 'ERR fetch failed'},
        'paragraphs': [[{'parts': [{'text': '名字，', 'fonts': 'MISSING'}]}]],
    })
    assert lines == ['名字，']
    assert len(stats['missing_fonts']) == 1
    assert stats['suspicious_passthrough'] == 2


def test_diagnostics_replay_and_exclude_account_data(tmp_path):
    sample = json.loads((Path(__file__).parent / 'fixtures' / 'qdn_screenshot_excerpt.json').read_text(encoding='utf-8'))
    payload = sample['payload'] | {'cookies': 'secret-cookie', 'url': 'https://example.test/?token=secret-token'}
    payload['paragraphs'][0][0]['password'] = 'secret-password'
    payload['paragraphs'][0][0]['x'] = 12.5
    payload['paragraphs'][0][0]['parts'][0]['headers'] = {'Authorization': 'secret-header'}
    lines, stats = q.decode_payload(payload)
    path = q.write_diagnostics(payload, lines, stats, str(tmp_path))
    text = Path(path).read_text(encoding='utf-8')
    assert 'secret-' not in text
    saved = json.loads(text)
    replay, _ = q.decode_payload(saved['payload'])
    assert replay == lines
    assert saved['payload']['paragraphs'][0][0]['parts'][0]['src'] == payload['paragraphs'][0][0]['parts'][0]['src']
    assert saved['payload']['paragraphs'][0][0]['x'] == 12.5


FIXTURE = r"""<!doctype html><meta charset="utf-8">
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
  // Off-screen paragraphs carry decoy text until they are scrolled into view.
  new IntersectionObserver((entries) => entries.forEach((entry) => {
    entry.target.textContent = entry.isIntersecting ? '真' : '毒';
  })).observe(document.getElementById('late'));
</script>
"""


def test_extraction_reads_what_is_painted_in_visual_order(tmp_path):
    sync_api = pytest.importorskip('playwright.sync_api')
    page_file = tmp_path / 'reader.html'
    page_file.write_text(FIXTURE, encoding='utf-8')
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 800, 'height': 600})
            page.add_init_script(q.QDN_FONTFACE_HOOK_JS)
            page.goto(page_file.as_uri())
            payload = page.evaluate(q.QDN_EXTRACT_JS)
            browser.close()
    except Exception as exc:  # pragma: no cover - no browser installed
        pytest.skip(f'Chromium unavailable: {exc}')
    text = [''.join(part['text'] for item in para for part in item['parts'])
            for para in payload['paragraphs']]
    assert text == ['一二三四', '甲乙', '现', '真']
    assert payload['scriptedCount'] == 0  # never added to document.fonts


@pytest.mark.parametrize('zoom', [1, 0.2, 0.08])
def test_pseudo_geometry_preserves_repeats_and_delayed_attribute_swaps(zoom):
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
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
        payload = page.evaluate(q.QDN_EXTRACT_JS)
        browser.close()
    text = [''.join(part['text'] for item in row for part in item['parts']) for row in payload['paragraphs']]
    assert text == ['甲乙丙哈哈哈', '真']
    assert payload['duplicates'] == 1
    assert payload['unstableParagraphs'] == []


def test_continuously_changing_capture_is_flagged():
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<main><p>甲</p></main><script>setInterval(() => {const p=document.querySelector("p");p.textContent=p.textContent==="甲"?"乙":"甲"},50)</script>')
        payload = page.evaluate(q.QDN_EXTRACT_JS)
        browser.close()
    assert payload['unstableParagraphs'] == [0]


def test_nested_mirrors_cancel_but_pseudo_mirror_is_preserved():
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<style>
        main,.inner {transform:scaleX(-1)}
        .pseudo::after {content:'甲';display:inline-block;transform:scaleX(-1)}
        </style><main><div class="inner"><p>乙<span class="pseudo"></span></p></div></main>''')
        payload = page.evaluate(q.QDN_EXTRACT_JS)
        browser.close()
    items = {item['parts'][0]['text']: item for item in payload['paragraphs'][0]}
    assert items['乙']['mirrored'] is False
    assert items['甲']['mirrored'] is True


def test_low_shape_scores_remain_uncertain_on_cache_hits(monkeypatch):
    monkeypatch.setattr(q, '_GLYPH_CACHE', {})
    monkeypatch.setattr(q, '_vector', lambda *_args: np.array([1., 0.]))
    reference = SimpleNamespace(chars=['甲', '乙'], matrix=np.array([[0.85, 0.], [0.7, 0.]]),
                                prior=np.array([0., 0.]))
    cipher = type('Cipher', (), {'digest': 'weak', 'font': None, 'family': 'F'})()
    decoder = q.QdnFontDecoder({'faces': []})
    decoder.reference = reference
    decoder._resolve([(cipher, '丁', False)])
    assert decoder._lookup(cipher, '丁', False) == '甲'
    assert decoder._lookup(cipher, '丁', False) == '甲'
    assert decoder.low_confidence == 2
    assert decoder.diagnostics[0]['reasons'] == ['low_shape_score']
    assert len(decoder.diagnostics) == 1
