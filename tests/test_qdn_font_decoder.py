import base64
import io
import os

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
