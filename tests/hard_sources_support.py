"""Shared helpers for the hard-sources (qdn / rbooks) browser tests.

Builds the pages those tests load: a synthetic reader that has every protection layer of the
real qdn reader, saved-page style fixtures for the book and chapter pages, and the book API of
rbooks. Site names are assembled at run time so no tracked file spells a provider name.
"""
import base64
import io
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / 'android' / 'sources' / 'src' / 'main' / 'resources' / 'com' / 'shirochi' / 'npia' / 'sources' / 'hard'
SCRIPT_PATH = RESOURCES / 'hard-sources.js'
REFERENCE_FONT = RESOURCES / 'qdn' / 'NotoSansCJKsc-Regular-hanzi.otf'
FIXTURES = ROOT / 'tests' / 'fixtures'
PAGES = FIXTURES / 'hard_sources'


def _name(*codes):
    return ''.join(chr(code) for code in codes)


# Host names, built from character codes.
QDN_DOMAIN = _name(113, 105, 100, 105, 97, 110) + '.com'
RBOOKS_DOMAIN = _name(114, 105, 100, 105, 98, 111, 111, 107, 115) + '.com'
QDN_ORIGIN = f'https://www.{QDN_DOMAIN}'
RBOOKS_ORIGIN = f'https://{RBOOKS_DOMAIN}'
RBOOKS_API = f'https://book-api.{RBOOKS_DOMAIN}'
RBOOKS_LIBRARY = f'https://library.{RBOOKS_DOMAIN}'
RBOOKS_LIBRARY_API = f'https://library-api.{RBOOKS_DOMAIN}'
RBOOKS_CDN = _name(114, 105, 100, 105, 99, 100, 110) + '.net'
RBOOKS_BORDER = _name(114, 105, 100, 105) + 'border'
RBOOKS_CENTER = _name(114, 105, 100, 105) + 'center'


def script_source():
    return SCRIPT_PATH.read_text(encoding='utf-8')


def page_text(name):
    """A saved page from tests/fixtures/hard_sources with {QDN} / {RBOOKS} / {CDN} filled in."""
    text = (PAGES / name).read_text(encoding='utf-8')
    return (text.replace('{QDN}', QDN_DOMAIN).replace('{RBOOKS}', RBOOKS_DOMAIN)
            .replace('{CDN}', RBOOKS_CDN).replace('{BORDER}', RBOOKS_BORDER).replace('{CENTER}', RBOOKS_CENTER))


def push_resources(page, verified=False):
    """Hands the decoder its resources the way native code does: base64 chunks."""
    names = [('font', RESOURCES / 'qdn' / 'NotoSansCJKsc-Regular-hanzi.otf'),
             ('frequency', RESOURCES / 'qdn' / 'zh_char_frequency.txt')]
    if verified:
        names.append(('verified', RESOURCES / 'qdn' / 'qdn_verified_maps.json'))
    for name, path in names:
        encoded = base64.b64encode(path.read_bytes()).decode()
        size = 262144
        parts = [encoded[i:i + size] for i in range(0, len(encoded), size)] or ['']
        for index, part in reversed(list(enumerate(parts))):  # any order
            page.evaluate('([n, i, c, d]) => window.__npiaHard.qdn.decoder.putResource(n, i, c, d)',
                          [name, index, len(parts), part])


def prepare_decoder(page, verified=False):
    push_resources(page, verified)
    while True:
        state = page.evaluate('() => window.__npiaHard.qdn.decoder.prepare(10000)')
        if state['done']:
            return state


# --- synthetic cipher fonts ----------------------------------------------------------------------

def cipher_font(mapping, mirrored=(), jitter=0, seed=1):
    """A TrueType font whose code points draw other characters' outlines.

    mapping: cipher code point (int) -> the character its glyph shows. Characters in `mirrored` are
    stored flipped. `jitter` moves every outline point by up to that many font units (the real fonts
    carry small random jitter).
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont

    rng = random.Random(seed)
    source = TTFont(str(REFERENCE_FONT))
    glyphs = source.getGlyphSet()
    cmap = source.getBestCmap()
    order = ['.notdef'] + [f'g{i}' for i in range(len(mapping))]
    outlines = {'.notdef': TTGlyphPen(None).glyph()}
    metrics = {'.notdef': (1000, 0)}
    for index, (cipher, real) in enumerate(mapping.items()):
        recording = RecordingPen()
        glyphs[cmap[ord(real)]].draw(recording)
        pen = TTGlyphPen(None)
        target = Cu2QuPen(pen, 1.0, reverse_direction=True)
        if real in mirrored:
            target = TransformPen(target, (-1, 0, 0, 1, 1000, 0))
        if jitter:
            # Move each point of the recorded outline a little, then replay it.
            moved = RecordingPen()
            for operator, operands in recording.value:
                moved.value.append((operator, tuple(
                    (x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter)) for x, y in operands)))
            moved.replay(target)
        else:
            recording.replay(target)
        outlines[f'g{index}'] = pen.glyph()
        metrics[f'g{index}'] = (1000, 0)
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({cipher: f'g{i}' for i, cipher in enumerate(mapping)})
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=880, descent=-120)
    builder.setupNameTable({'familyName': 'Cipher', 'styleName': 'Regular'})
    builder.setupOS2(sTypoAscender=880, usWinAscent=880, usWinDescent=120)
    builder.setupPost()
    out = io.BytesIO()
    builder.save(out)
    return out.getvalue()


def reference_cmap():
    from fontTools.ttLib import TTFont
    return set(TTFont(str(REFERENCE_FONT)).getBestCmap())


# --- the synthetic reader --------------------------------------------------------------------------

# Original prose, written for these tests, using common characters.
CHAPTER = [
    '清晨的风吹过安静的小镇，远处的山还披着一层薄薄的雾。',
    '他推开木门，走进院子里，看见母亲正在树下整理昨天收来的药草。',
    '“今天要去很远的地方吗？”她头也不抬地问。',
    '他点了点头，把行李放在石桌上，又回屋取了一壶热茶。',
    '路并不好走，河水涨了，桥板也断了几块，只能沿着岸边慢慢向前。',
    '直到太阳升得很高，他才看到那座古老的高塔，塔顶挂着一盏旧灯。',
    '守塔的老人坐在门口，手里握着一把生锈的钥匙，似乎早已等了很久。',
    '“你来晚了，”老人说，“不过没关系，该来的人总会来。”',
    '他想问许多事情，却不知从哪里开始，只好先把带来的东西交给对方。',
    '老人接过信，看了一遍，轻轻叹了口气，转身打开了沉重的大门。',
    '门后是一条长长的楼梯，向下通往看不见的黑暗，空气里有潮湿的味道。',
    '他们一前一后走了很久，脚步声在墙壁之间来回响着，仿佛有人跟在身后。',
    '最后，他们停在一扇铁门前，老人把钥匙递给他，什么也没有多说。',
    '那一刻，他忽然明白，这趟旅程才刚刚开始，而前面等着他的，不会是简单的答案。',
] * 3


def build_reader(seed=7, jitter=5, poison=True, paragraphs=None):
    """The synthetic reader page, the text it hides, and its fonts.

    Layers: cipher fonts (a stylesheet face for U+4E00-9FA5 and a script-built blob face for
    U+3400-4DB5) with jitter, mirrored glyphs, characters in random tags ordered by CSS `order`,
    characters delivered through `::after{content: attr()}`, hidden decoys, and decoy paragraphs that
    hold other text until they are near the viewport. Returns (html, expected_lines, info).
    """
    rng = random.Random(seed)
    lines = list(paragraphs or CHAPTER)
    supported = reference_cmap()
    distinct = sorted({ch for line in lines for ch in line if ord(ch) in supported and ord(ch) >= 0x4E00})
    rare = distinct[:14]            # drawn by the per-chapter blob font (U+3400-4DB5)
    common = distinct[14:]
    mirrored = set(rng.sample(distinct, max(4, len(distinct) // 12)))
    fixed_pool = list(range(0x4E00, 0x9FA5 + 1))
    rng.shuffle(fixed_pool)
    rare_pool = list(range(0x3400, 0x4DB5 + 1))
    rng.shuffle(rare_pool)
    fixed_map = {fixed_pool[i]: ch for i, ch in enumerate(common)}
    rare_map = {rare_pool[i]: ch for i, ch in enumerate(rare)}
    fixed_font = cipher_font(fixed_map, mirrored, jitter, seed=11)
    rare_font = cipher_font(rare_map, mirrored, jitter, seed=12)
    encode = {ch: chr(cp) for cp, ch in {**fixed_map, **rare_map}.items()}
    tags = ['i', 'b', 'em', 'u', 's', 'span', 'q']

    def paragraph(line, decoy):
        items = []
        for position, ch in enumerate(line):
            style = f'style="order:{position}"'
            if ch in encode:
                flip = ' flip' if ch in mirrored else ''
                tag = rng.choice(tags)
                if rng.random() < 0.15:
                    # The character is only in an attribute, painted by ::after.
                    items.append(f'<{tag} {style} class="g{flip} aft" data-c="{encode[ch]}"></{tag}>')
                else:
                    items.append(f'<{tag} {style} class="g{flip}">{encode[ch]}</{tag}>')
            else:
                items.append(f'<span {style} class="lit">{ch}</span>')
        for _ in range(3):
            items.append(f'<y class="sy-0"><samp>{decoy}</samp></y>')
        items.append(f'<span class="clip">{decoy}</span>')
        rng.shuffle(items)  # the DOM order is not the reading order
        return ''.join(items)

    real = [paragraph(line, '假') for line in lines]
    fake = [paragraph(lines[(i + 1) % len(lines)], '毒') for i in range(len(lines))]
    words = sum(len(line) for line in lines)
    page_context = json.dumps({'pageContext': {'pageProps': {'pageData': {
        'isLogin': 1,
        'chapterInfo': {'chapterId': 4242, 'isBuy': 1, 'cES': 2, 'fEnS': 1, 'vipStatus': 1, 'actualWords': words},
    }}}})
    fixed_b64 = base64.b64encode(fixed_font).decode()
    rare_b64 = base64.b64encode(rare_font).decode()
    shown = fake if poison else real
    body = ''.join(f'<p data-i="{i}">{shown[i]}</p>' for i in range(len(lines)))
    html = f'''<!doctype html><html><head><meta charset="utf-8"><title>reader</title>
<style>
@font-face{{font-family:cipherA;src:url(data:font/ttf;base64,{fixed_b64});unicode-range:U+4E00-9FA5}}
body{{margin:0;font:22px/1.6 cipherA,cipherB,sans-serif}}
main{{width:760px;margin:0 auto}}
main p{{display:flex;flex-wrap:wrap;margin:0 0 14px}}
.flip{{transform:scaleX(-1)}}
.aft::after{{content:attr(data-c)}}
.sy-0{{display:none}}
.clip{{width:0;height:0;overflow:hidden}}
</style></head><body>
<script id="vite-plugin-ssr_pageContext" type="application/json">{page_context}</script>
<main class="r-font-encrypt" id="c-4242">{body}</main>
<script>
const REAL = {json.dumps(real)};
const FAKE = {json.dumps(fake)};
// The reader builds one face in script (the per-chapter blob font).
(function () {{
  const bin = atob('{rare_b64}'); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const face = new FontFace('cipherB', bytes.buffer, {{unicodeRange: 'U+3400-4DB5'}});
  document.fonts.add(face);
  face.load();
}})();
// Decoy paragraphs: the real text is present only while a paragraph is within ~200px of the viewport.
if ({str(poison).lower()}) {{
  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {{
    const index = Number(entry.target.dataset.i);
    setTimeout(() => {{ entry.target.innerHTML = entry.isIntersecting ? REAL[index] : FAKE[index]; }}, 25);
  }}), {{rootMargin: '200px'}});
  document.querySelectorAll('main p').forEach((p) => observer.observe(p));
}}
</script></body></html>'''
    return html, lines, {'fixed': fixed_font, 'rare': rare_font, 'mirrored': sorted(mirrored), 'distinct': len(distinct)}
