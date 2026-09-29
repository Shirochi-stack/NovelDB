"""Decode Qidian's font-encrypted VIP chapter text.

Bought Qidian chapters are rendered with two per-page cipher fonts: a
"fixed" font for U+4E00-9FA5 and a per-chapter blob font for U+3400-4DB5.
Each code point is drawn with the outline of a different character, some
outlines are stored mirrored, and the outlines carry small random jitter,
so they cannot be matched by hash. The browser extraction script
(``QIDIAN_EXTRACT_JS``) returns what is visibly rendered, in visual order,
plus the font files. This module identifies each cipher glyph by comparing
its rendering with a reference font of the same design (Source Han Sans /
Noto Sans CJK), using character frequency to settle near-identical shapes.
"""
import base64
import io
import os
import re
import sys

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover - reported by ``available()``
    np = None

_RENDER_SIZE = 96
_GRID = 56
_BLUR = 0.5
_CANDIDATES = 5
# Weight of the frequency prior relative to shape similarity (cosine, 0-1).
_PRIOR_WEIGHT = 0.1
_HERE = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
_FREQ_FILE = os.path.join(_HERE, 'data', 'zh_char_frequency.txt')
_FONT_EXTENSIONS = ('.otf', '.ttf', '.ttc', '.otc')


def _reference_font_paths():
    """Reference fonts, best design match first.

    The bundled Noto Sans CJK SC subset is the design Qidian's cipher glyphs
    are drawn from; installed CJK fonts only fill characters it lacks.
    """
    candidates = [os.environ.get('NPIA_QIDIAN_REF_FONT', '')]
    bundled = os.path.join(_HERE, 'data', 'fonts')
    if os.path.isdir(bundled):
        candidates += [os.path.join(bundled, name)
                       for name in sorted(os.listdir(bundled))
                       if name.lower().endswith(_FONT_EXTENSIONS)]
    if sys.platform == 'win32':
        fonts = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
        local = os.path.join(os.environ.get('LOCALAPPDATA', ''),
                             'Microsoft', 'Windows', 'Fonts')
        for folder in (local, fonts):
            candidates += [os.path.join(folder, name) for name in (
                'NotoSansSC-VF.ttf', 'NotoSansSC-Regular.ttf',
                'SourceHanSansSC-Regular.otf', 'NotoSansCJK-Regular.ttc',
                'NotoSansJP-VF.ttf', 'msyh.ttc',
            )]
    elif sys.platform == 'darwin':
        candidates += ['/System/Library/Fonts/PingFang.ttc',
                       '/Library/Fonts/NotoSansSC-Regular.otf']
    else:
        candidates += ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                       '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc']
    seen, paths = set(), []
    for path in candidates:
        if path and os.path.isfile(path) and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def available():
    return np is not None and bool(_reference_font_paths())


def _common_hanzi():
    chars = []
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.append(bytes([hi, lo]).decode('gb2312'))
            except UnicodeDecodeError:
                pass
    return chars


def _load_frequency():
    try:
        with open(_FREQ_FILE, encoding='utf-8') as handle:
            ranked = [line.split('\t', 1) for line in handle.read().splitlines()]
        return {ch: int(count) for ch, count in ranked if ch}
    except (OSError, ValueError):
        return {}


def _regular_weight(font):
    try:
        axes = font.get_variation_axes()
    except Exception:
        return font
    values = []
    for axis in axes:
        name = axis.get('name')
        name = name.decode('latin-1') if isinstance(name, bytes) else str(name)
        values.append(400 if name.lower().startswith('weight') else axis['default'])
    try:
        font.set_variation_by_axes(values)
    except Exception:
        pass
    return font


def _font_cmap(path_or_bytes):
    """Code points a font maps, read from its cmap table."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    source = (io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes)
              else path_or_bytes)
    try:
        return set(TTFont(source, fontNumber=0, lazy=True).getBestCmap())
    except Exception:
        return None


def _vector(font, ch, mirror=False):
    left, top, right, bottom = font.getbbox(ch)
    if right <= left or bottom <= top:
        return None
    img = Image.new('L', (right - left + 8, bottom - top + 8), 0)
    ImageDraw.Draw(img).text((4 - left, 4 - top), ch, font=font, fill=255)
    box = img.getbbox()
    if not box:
        return None
    img = img.crop(box)
    width, height = img.size
    side = max(width, height)
    square = Image.new('L', (side, side), 0)
    square.paste(img, ((side - width) // 2, (side - height) // 2))
    square = square.resize((_GRID, _GRID), Image.LANCZOS)
    if _BLUR:
        square = square.filter(ImageFilter.GaussianBlur(_BLUR))
    arr = np.asarray(square, dtype=np.float32)
    if mirror:
        arr = arr[:, ::-1]
    arr = arr - arr.mean()
    norm = float(np.linalg.norm(arr))
    return (arr / norm).ravel() if norm else None


class _Reference:
    """Rendered reference glyphs for common hanzi (built once per process)."""

    _instance = None

    def __init__(self):
        freq = _load_frequency()
        fonts = []
        for path in _reference_font_paths():
            cmap = _font_cmap(path)
            font = _regular_weight(ImageFont.truetype(path, _RENDER_SIZE))
            fonts.append((font, cmap))
        chars = _common_hanzi()
        # Rare characters outside GB2312 still appear in novels.
        known = set(chars)
        chars += [ch for ch in freq if ch not in known][:3000]
        notdefs = [_vector(font, '') for font, _ in fonts]
        vectors, keep = [], []
        for ch in chars:
            for (font, cmap), notdef in zip(fonts, notdefs):
                if cmap is not None and ord(ch) not in cmap:
                    continue
                vec = _vector(font, ch)
                # Without fontTools a missing glyph renders as .notdef.
                if vec is None or (cmap is None and notdef is not None
                                   and float(vec @ notdef) > 0.98):
                    continue
                vectors.append(vec)
                keep.append(ch)
                break
        self.chars = keep
        self.matrix = np.stack(vectors)
        top = max(freq.values()) if freq else 1
        self.prior = np.array(
            [np.log10(freq.get(ch, 0) + 1) / np.log10(top + 1) for ch in keep],
            dtype=np.float32,
        )

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def _parse_unicode_range(text):
    spans = []
    for part in (text or '').split(','):
        match = re.match(r'\s*U\+([0-9A-Fa-f?]+)(?:-([0-9A-Fa-f]+))?', part)
        if not match:
            continue
        low_text = match.group(1)
        if '?' in low_text:
            spans.append((int(low_text.replace('?', '0'), 16),
                          int(low_text.replace('?', 'F'), 16)))
        else:
            low = int(low_text, 16)
            spans.append((low, int(match.group(2) or low_text, 16)))
    return spans or [(0, 0x10FFFF)]


class _CipherFont:
    def __init__(self, family, unicode_range, data):
        self.family = family
        self.spans = _parse_unicode_range(unicode_range)
        self.font = ImageFont.truetype(io.BytesIO(data), _RENDER_SIZE)
        self.cmap = _font_cmap(data)
        self._notdef = _vector(self.font, '\ue000')

    def covers(self, ch):
        cp = ord(ch)
        if not any(low <= cp <= high for low, high in self.spans):
            return False
        if self.cmap is not None:
            return cp in self.cmap
        vec = _vector(self.font, ch)
        return vec is not None and not (
            self._notdef is not None and float(vec @ self._notdef) > 0.99)


class QidianFontDecoder:
    """Map rendered cipher characters of one chapter page to real text."""

    def __init__(self, payload):
        self.fonts = []
        data = payload.get('fontData') or {}
        for face in payload.get('faces') or []:
            encoded = data.get(face.get('url') or '')
            if not encoded or encoded.startswith('ERR'):
                continue
            try:
                self.fonts.append(_CipherFont(
                    face.get('family', ''), face.get('range', ''),
                    base64.b64decode(encoded),
                ))
            except Exception:
                continue
        self.reference = _Reference.get()
        self._cache = {}
        self.low_confidence = 0
        self.decoded = 0

    def _cipher_for(self, ch, families):
        # Within a family the browser tries the most recently defined face
        # first. The reader adds a script-built face after the stylesheet
        # ones; it redraws the decoy characters the other faces render.
        for family in families:
            for cipher in reversed(self.fonts):
                if cipher.family == family and cipher.covers(ch):
                    return cipher
        return None

    def decode_char(self, ch, families, mirrored):
        cipher = self._cipher_for(ch, families)
        if cipher is None:
            return ch
        key = (cipher.family, ch, mirrored)
        if key not in self._cache:
            vec = _vector(cipher.font, ch, mirrored)
            if vec is None:
                self._cache[key] = ('', 0.0)
            else:
                ref = self.reference
                scores = ref.matrix @ vec
                top = np.argpartition(-scores, _CANDIDATES)[:_CANDIDATES]
                best = max(top, key=lambda i: scores[i] + _PRIOR_WEIGHT * ref.prior[i])
                self._cache[key] = (ref.chars[best], float(scores[best]))
        char, score = self._cache[key]
        self.decoded += 1
        if score < 0.6:
            self.low_confidence += 1
        return char

    def decode_paragraphs(self, paragraphs):
        lines = []
        for items in paragraphs:
            line = []
            for item in items:
                for part in item.get('parts') or []:
                    families = [name.strip().strip('"\'') for name in
                                (part.get('fonts') or '').split(',')]
                    for ch in part.get('text') or '':
                        line.append(self.decode_char(ch, families,
                                                     bool(item.get('mirrored'))))
            text = ''.join(line).strip()
            if text:
                lines.append(text)
        return lines


def decode_payload(payload):
    """Return (paragraphs, stats) for an extraction payload."""
    decoder = QidianFontDecoder(payload)
    lines = decoder.decode_paragraphs(payload.get('paragraphs') or [])
    return lines, {
        'decoded': decoder.decoded,
        'low_confidence': decoder.low_confidence,
        'fonts': [cipher.family for cipher in decoder.fonts],
    }

# Installed before the reader's scripts run (context init script). The
# reader builds one font face in script; it is invisible to stylesheets.
QIDIAN_FONTFACE_HOOK_JS = r'''(() => {
  // Record fonts the reader builds in script. They override the CSS
  // @font-face fonts and are not visible in any stylesheet.
  const Native = window.FontFace;
  if (!Native || Native.__npiaHooked) return;
  const records = [];
  Object.defineProperty(window, '__npiaFontFaces', {value: records});
  const Hooked = function FontFace(family, source, descriptors) {
    const face = new Native(family, source, descriptors);
    let copy = source;
    try {
      if (source instanceof ArrayBuffer) copy = source.slice(0);
      else if (ArrayBuffer.isView(source)) {
        copy = source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength);
      }
    } catch (e) {}
    records.push({family, source: copy, descriptors: descriptors || {}, face});
    return face;
  };
  Hooked.prototype = Native.prototype;
  Object.defineProperty(Hooked, 'name', {value: 'FontFace'});
  Hooked.toString = () => 'function FontFace() { [native code] }';
  Hooked.__npiaHooked = true;
  window.FontFace = Hooked;
})();'''

# Collects the glyphs that actually paint, in visual order, plus the cipher
# fonts. Scrolls the chapter, because off-screen paragraphs are decoys.
QIDIAN_EXTRACT_JS = r'''async () => {
  const main = document.querySelector('main.r-font-encrypt, main[id^="c-"]')
    || document.querySelector('main');
  if (!main) return {error: 'no reader'};
  const unquote = (value) => {
    if (!value || value === 'none' || value === 'normal') return '';
    const m = value.match(/^"(.*)"$/s) || value.match(/^'(.*)'$/s);
    if (!m) return '';
    return m[1].replace(/\\([0-9a-fA-F]{1,6})\s?/g,
      (_, hex) => String.fromCodePoint(parseInt(hex, 16))).replace(/\\(.)/g, '$1');
  };
  const transparent = (color) => /rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(color)
    || color === 'transparent';
  const hiddenCache = new Map();
  // Zero opacity or display:none anywhere above hides a node. Visibility is
  // inherited but a child may set it back to visible, so only the node's
  // own computed value counts.
  const concealed = (el) => {
    if (!el || el === main) return false;
    if (hiddenCache.has(el)) return hiddenCache.get(el);
    const st = getComputedStyle(el);
    const value = st.display === 'none' || parseFloat(st.opacity) === 0
      || concealed(el.parentElement);
    hiddenCache.set(el, value);
    return value;
  };
  const hidden = (el) => concealed(el) || getComputedStyle(el).visibility === 'hidden';
  const mirrored = (el) => {
    for (let node = el; node && node !== main; node = node.parentElement) {
      if (/matrix\(-1/.test(getComputedStyle(node).transform)) return true;
    }
    return false;
  };
  const faces = [];
  const readSheet = (sheet) => {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { return; }
    for (const rule of rules) {
      if (rule.type === CSSRule.FONT_FACE_RULE) {
        const src = rule.style.getPropertyValue('src');
        faces.push({
          family: rule.style.getPropertyValue('font-family').replace(/["']/g, '').trim(),
          range: rule.style.getPropertyValue('unicode-range'),
          urls: [...src.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]),
        });
      }
    }
  };
  [...document.styleSheets].forEach(readSheet);
  (document.adoptedStyleSheets || []).forEach(readSheet);
  const usedFamilies = new Set();
  let dropped = 0;
  const capture = (p) => {
    hiddenCache.clear();
    const atoms = [];
    const addPseudo = (el, which, rect) => {
      const st = getComputedStyle(el, which);
      const text = unquote(st.content);
      if (!text || st.display === 'none' || st.visibility === 'hidden'
          || parseFloat(st.fontSize) < 1 || transparent(st.color)) return;
      st.fontFamily.split(',').forEach((f) => usedFamilies.add(f.replace(/["']/g, '').trim()));
      // A pseudo-element in an otherwise empty box is that box's glyph.
      const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      const x = !hasText ? rect.left + rect.width / 2
        : (which === '::before' ? rect.left - 0.5 : rect.right + 0.5);
      atoms.push({x, y: rect.top, h: rect.height, text, fonts: st.fontFamily,
                  mirrored: mirrored(el)});
    };
    const walk = (node) => {
      if (node.nodeType === 3) {
        const el = node.parentElement;
        const st = getComputedStyle(el);
        if (hidden(el) || transparent(st.color) || parseFloat(st.fontSize) < 1) {
          dropped += node.textContent.length;
          return;
        }
        st.fontFamily.split(',').forEach((f) => usedFamilies.add(f.replace(/["']/g, '').trim()));
        const flip = mirrored(el);
        const text = node.textContent;
        const range = document.createRange();
        let offset = 0;
        for (const ch of text) {
          range.setStart(node, offset);
          range.setEnd(node, offset + ch.length);
          offset += ch.length;
          const r = range.getBoundingClientRect();
          if (r.width < 1 || r.height < 1) { dropped += 1; continue; }
          atoms.push({x: r.left + r.width / 2, y: r.top, h: r.height, text: ch,
                      fonts: st.fontFamily, mirrored: flip});
        }
        return;
      }
      if (node.nodeType !== 1) return;
      const el = node;
      if (el.matches('.review, .review *, script, style')) return;
      if (concealed(el)) {
        dropped += (el.textContent || '').length;
        return;
      }
      const rect = el.getBoundingClientRect();
      addPseudo(el, '::before', rect);
      for (const child of el.childNodes) walk(child);
      addPseudo(el, '::after', rect);
    };
    for (const child of p.childNodes) walk(child);
    // Group into visual lines, then read each line left to right.
    atoms.sort((a, b) => a.y - b.y);
    const lines = [];
    for (const atom of atoms) {
      const line = lines[lines.length - 1];
      if (line && Math.abs(atom.y - line.y) < Math.max(4, atom.h / 2)) {
        line.atoms.push(atom);
      } else {
        lines.push({y: atom.y, atoms: [atom]});
      }
    }
    const ordered = [];
    for (const line of lines) {
      line.atoms.sort((a, b) => a.x - b.x);
      for (const atom of line.atoms) {
        ordered.push({parts: [{text: atom.text, fonts: atom.fonts}],
                      mirrored: atom.mirrored});
      }
    }
    return ordered;
  };
  // The reader keeps a poisoned copy of every paragraph outside the viewport
  // and swaps in the real text once it is on screen. Paragraphs visible at
  // load are already real. Every other one is read only after its text has
  // changed from the load-time copy, which proves the swap happened.
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const all = [...main.querySelectorAll('p')];
  const paragraphs = new Array(all.length).fill(null);
  const skipped = new Set();
  const unswapped = [];
  const view = window.innerHeight;
  const inView = (p) => {
    const r = p.getBoundingClientRect();
    return r.height >= 1 && r.top >= 0 && r.bottom <= view;
  };
  all.forEach((p, i) => {
    // A paragraph may be visibility:hidden while children set it back
    // to visible, so only display/opacity rule a paragraph out.
    if (concealed(p) || p.getBoundingClientRect().height < 1) skipped.add(i);
  });
  const initial = all.map((p) => p.textContent);
  all.forEach((p, i) => {
    if (!skipped.has(i) && inView(p)) paragraphs[i] = capture(p);
  });
  const swapped = (i) => all[i].textContent !== initial[i];
  for (let i = 0; i < all.length; i += 1) {
    if (paragraphs[i] !== null || skipped.has(i)) continue;
    all[i].scrollIntoView({block: 'center'});
    let waited = 0;
    while (!swapped(i) && waited < 4000) {
      await sleep(150);
      waited += 150;
    }
    await sleep(150);
    if (!swapped(i)) unswapped.push(i);
    all.forEach((p, j) => {
      if (paragraphs[j] === null && !skipped.has(j)
          && (swapped(j) || j === i) && inView(p)) {
        paragraphs[j] = capture(p);
      }
    });
    if (paragraphs[i] === null) paragraphs[i] = capture(all[i]);
  }
  window.scrollTo(0, 0);
  // Faces built in script come after the stylesheet ones, so the browser
  // tries them first. Keep definition order; the decoder walks it backwards.
  const toBase64 = (buffer) => {
    const buf = new Uint8Array(buffer);
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  };
  const fontData = {};
  const scripted = (window.__npiaFontFaces || [])
    .filter((rec) => document.fonts.has(rec.face));
  scripted.forEach((rec, i) => {
    const face = {
      family: String(rec.family).replace(/["']/g, '').trim(),
      range: rec.descriptors.unicodeRange || 'U+0-10FFFF',
      urls: [], scripted: true,
    };
    if (typeof rec.source === 'string') {
      face.urls = [...rec.source.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]);
    } else {
      face.url = 'scripted:' + i;
      try { fontData[face.url] = toBase64(rec.source); } catch (e) { fontData[face.url] = 'ERR ' + e.message; }
    }
    faces.push(face);
  });
  const usedFaces = faces.filter((face) => usedFamilies.has(face.family));
  for (const face of usedFaces) {
    const url = face.url || face.urls.find((u) => /woff2|ttf|otf|blob:/.test(u)) || face.urls[0];
    face.url = url;
    if (!url || fontData[url]) continue;
    try {
      fontData[url] = toBase64(await (await fetch(url)).arrayBuffer());
    } catch (e) {
      fontData[url] = 'ERR ' + e.message;
    }
  }
  return {scriptedCount: scripted.length, unswapped, skipped: skipped.size,
          faces: usedFaces, fontData,
          paragraphs: paragraphs.filter((p) => p !== null), dropped,
          url: location.href, title: document.title};
}'''
