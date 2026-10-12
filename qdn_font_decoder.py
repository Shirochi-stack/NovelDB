"""Decode Qdn's font-encrypted VIP chapter text.

Bought Qdn chapters are rendered with two per-page cipher fonts: a
"fixed" font for U+4E00-9FA5 and a per-chapter blob font for U+3400-4DB5.
Each code point is drawn with the outline of a different character, some
outlines are stored mirrored, and the outlines carry small random jitter,
so they cannot be matched by hash. The browser extraction script
(``QDN_EXTRACT_JS``) returns what is visibly rendered, in visual order,
plus the font files. Screenshot-verified entries for the exact font bytes,
character code, and mirror state bypass shape matching. Remaining glyphs
are compared with a reference font of the same design (Source Han Sans /
Noto Sans CJK), using character frequency to settle near-identical shapes.
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import threading
import tempfile
import unicodedata

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
_PRIOR_WINDOW = 0.01
_CONFIDENCE_SCORE = 0.90
_CONFIDENCE_MARGIN = 0.03
_HERE = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
_FREQ_FILE = os.path.join(_HERE, 'data', 'zh_char_frequency.txt')
_VERIFIED_FILE = os.path.join(_HERE, 'data', 'qdn_verified_maps.json')
_FONT_EXTENSIONS = ('.otf', '.ttf', '.ttc', '.otc')


def _reference_font_paths():
    """Reference fonts, best design match first.

    The bundled Noto Sans CJK SC subset is the design Qdn's cipher glyphs
    are drawn from; installed CJK fonts only fill characters it lacks.
    """
    candidates = [os.environ.get('NPIA_QDN_REF_FONT', '')]
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


def _is_cjk(cp):
    return (0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF
            or 0x20000 <= cp <= 0x323AF)


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
        with TTFont(source, fontNumber=0, lazy=True) as font:
            return set(font.getBestCmap())
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
    _lock = threading.Lock()

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
        supported = {cp for _, cmap in fonts for cp in (cmap or ()) if _is_cjk(cp)}
        chars += sorted(({chr(cp) for cp in supported} | set(freq)) - known)
        chars = list(dict.fromkeys(chars))
        notdefs = [_vector(font, '') for font, _ in fonts]
        vectors, keep = [], []
        for ch in chars:
            canonical = unicodedata.normalize('NFKC', ch)
            # Compatibility ideographs have the same meaning/shape as their
            # unified form. Do not let duplicate outlines compete as words.
            if (len(canonical) == 1 and canonical != ch
                    and ord(canonical) in supported):
                continue
            for (font, cmap), notdef in zip(fonts, notdefs):
                if cmap is not None and ord(ch) not in cmap:
                    continue
                vec = _vector(font, ch)
                # Without fontTools a missing glyph renders as .notdef.
                if vec is None or (cmap is None and notdef is not None
                                   and float(vec @ notdef) > 0.98):
                    continue
                vectors.append(vec)
                keep.append(canonical if len(canonical) == 1 else ch)
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
        with cls._lock:
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
        self._notdef = _vector(self.font, '\ue000') if self.cmap is None else None

    def covers(self, ch):
        cp = ord(ch)
        if not any(low <= cp <= high for low, high in self.spans):
            return False
        if self.cmap is not None:
            return cp in self.cmap
        vec = _vector(self.font, ch)
        return vec is not None and not (
            self._notdef is not None and float(vec @ self._notdef) > 0.99)


_FONT_CACHE = {}
# (font digest, character, mirrored) -> match details. The
# "fixed" cipher font is often the same file across chapters, so later
# chapters in a download mostly hit this cache.
_GLYPH_CACHE = {}
_CACHE_LOCK = threading.Lock()
_VERIFIED_MAPS = None


def _verified_entries(digest):
    """Only a verified entry for these exact font bytes bypasses matching."""
    global _VERIFIED_MAPS
    with _CACHE_LOCK:
        if _VERIFIED_MAPS is None:
            _VERIFIED_MAPS = {}
            try:
                with open(_VERIFIED_FILE, encoding='utf-8') as stream:
                    saved = json.load(stream)
                if saved.get('schema') == 1:
                    for font_digest, font in saved.get('fonts', {}).items():
                        if not re.fullmatch(r'[0-9a-f]{64}', font_digest):
                            continue
                        entries = font.get('entries', {})
                        _VERIFIED_MAPS[font_digest] = {
                            key: value for key, value in entries.items()
                            if re.fullmatch(r'[0-9A-F]{1,6}:[01]', key)
                            and isinstance(value, str) and len(value) == 1
                        }
            except (OSError, ValueError, TypeError, AttributeError):
                # Missing/corrupt optional maps must not suppress normal decoding.
                _VERIFIED_MAPS = {}
        return _VERIFIED_MAPS.get(digest, {})


def _cipher_font(family, unicode_range, data):
    digest = hashlib.sha1(data).hexdigest()
    key = (digest, family, unicode_range)
    with _CACHE_LOCK:
        font = _FONT_CACHE.get(key)
    if font is None:
        font = _CipherFont(family, unicode_range, data)
        font.digest = digest
        font.verified_digest = hashlib.sha256(data).hexdigest()
        with _CACHE_LOCK:
            if len(_FONT_CACHE) > 64:
                _FONT_CACHE.clear()
            _FONT_CACHE[key] = font
    return font


def warm_up():
    """Build the reference glyphs in the background before chapters arrive."""
    if not available():
        return None
    thread = threading.Thread(target=_Reference.get, name='qdn-ref',
                              daemon=True)
    thread.start()
    return thread


class QdnFontDecoder:
    """Map rendered cipher characters of one chapter page to real text."""

    def __init__(self, payload):
        self.fonts = []
        self.faces = payload.get('faces') or []
        self.missing_fonts = []
        self.diagnostics = []
        self._reported = set()
        self.suspicious_passthrough = 0
        data = payload.get('fontData') or {}
        for face in payload.get('faces') or []:
            encoded = data.get(face.get('url') or '')
            if not encoded or encoded.startswith('ERR'):
                self.missing_fonts.append({'family': face.get('family', ''),
                                           'reason': 'missing_font_data'})
                continue
            try:
                self.fonts.append(_cipher_font(
                    face.get('family', ''), face.get('range', ''),
                    base64.b64decode(encoded),
                ))
            except Exception as exc:
                self.missing_fonts.append({'family': face.get('family', ''),
                                           'reason': 'invalid_font',
                                           'error_type': type(exc).__name__})
                continue
        self.reference = None  # verified captures need no reference rendering
        self.low_confidence = 0
        self.decoded = 0
        self.verified = 0

    def _resolve(self, keys):
        """Match every uncached cipher glyph in one matrix product."""
        pending, vectors = [], []
        with _CACHE_LOCK:
            missing = [(cipher, ch, mirrored)
                       for cipher, ch, mirrored in dict.fromkeys(keys)
                       if (cipher.digest, ch, mirrored) not in _GLYPH_CACHE]
        for cipher, ch, mirrored in missing:
            entries = _verified_entries(getattr(cipher, 'verified_digest', None))
            verified = entries.get(f'{ord(ch):X}:{int(mirrored)}')
            if verified is not None:
                with _CACHE_LOCK:
                    _GLYPH_CACHE[(cipher.digest, ch, mirrored)] = {
                        'character': verified, 'score': None, 'margin': None,
                        'candidates': [], 'reasons': [], 'method': 'verified_font_map',
                    }
                continue
            vec = _vector(cipher.font, ch, mirrored)
            if vec is None:
                with _CACHE_LOCK:
                    _GLYPH_CACHE[(cipher.digest, ch, mirrored)] = {
                        'character': ch, 'score': 0.0, 'margin': 0.0,
                        'candidates': [], 'reasons': ['empty_glyph'],
                    }
                continue
            pending.append((cipher.digest, ch, mirrored))
            vectors.append(vec)
        if not vectors:
            return
        ref = self.reference or _Reference.get()
        self.reference = ref
        scores = np.stack(vectors) @ ref.matrix.T
        count = min(_CANDIDATES, scores.shape[1])
        top = np.argsort(-scores, axis=1, kind='stable')[:, :count]
        top_scores = np.take_along_axis(scores, top, axis=1)
        eligible = top_scores >= top_scores[:, :1] - _PRIOR_WINDOW
        pick = np.argmax(np.where(eligible,
                         top_scores + _PRIOR_WEIGHT * ref.prior[top], -np.inf), axis=1)
        component_scores = {}
        # A large shared right component can drown out the distinguishing
        # left strokes (for example the person/heart radicals). Only revisit
        # weak matches when a candidate has an almost exact left component
        # and the original candidate does not. Keep the original shape score
        # for uncertainty reporting; this extra evidence is not certainty.
        if ref.matrix.shape[1] == _GRID * _GRID:
            for row, vec in enumerate(vectors):
                if top_scores[row, 0] >= _CONFIDENCE_SCORE:
                    continue
                source = vec.reshape(_GRID, _GRID)
                regions = []
                for index in top[row]:
                    target = ref.matrix[index].reshape(_GRID, _GRID)
                    values = []
                    for left, right in ((0, _GRID // 3), (_GRID // 3, 2 * _GRID // 3),
                                        (2 * _GRID // 3, _GRID)):
                        a, b = source[:, left:right], target[:, left:right]
                        norm = float(np.linalg.norm(a) * np.linalg.norm(b))
                        values.append(float(a.ravel() @ b.ravel()) / norm if norm else 0.0)
                    regions.append(values)
                balanced = (top_scores[row] + np.min(regions, axis=1)) / 2
                proposed = int(np.argmax(balanced))
                original = int(pick[row])
                if (regions[proposed][0] >= 0.95 and regions[original][0] < 0.85
                        and balanced[proposed] > balanced[original]):
                    pick[row] = proposed
                    component_scores[row] = regions
        with _CACHE_LOCK:
            if len(_GLYPH_CACHE) > 200000:
                _GLYPH_CACHE.clear()
            for row, key in enumerate(pending):
                best = int(top[row, pick[row]])
                score = float(scores[row, best])
                margin = float(top_scores[row, 0] - top_scores[row, 1]) if count > 1 else 1.0
                reasons = []
                if score < _CONFIDENCE_SCORE:
                    reasons.append('low_shape_score')
                if margin < _CONFIDENCE_MARGIN:
                    reasons.append('ambiguous_shape')
                if row in component_scores:
                    reasons.append('component_refinement')
                _GLYPH_CACHE[key] = {
                    'character': ref.chars[best], 'score': score, 'margin': margin,
                    'method': 'shape_match',
                    'reasons': reasons,
                    'candidates': [{'character': ref.chars[int(index)],
                                    'score': float(scores[row, index]),
                                    **({'component_scores': component_scores[row][column]}
                                       if row in component_scores else {})}
                                   for column, index in enumerate(top[row])],
                }

    def _cipher_for(self, ch, families):
        # Within a family the browser tries the most recently defined face
        # first. The reader adds a script-built face after the stylesheet
        # ones; it redraws the decoy characters the other faces render.
        for family in families:
            for cipher in reversed(self.fonts):
                if cipher.family == family and cipher.covers(ch):
                    return cipher
        return None

    def _lookup(self, cipher, ch, mirrored):
        with _CACHE_LOCK:
            match = _GLYPH_CACHE[(cipher.digest, ch, mirrored)]
        self.decoded += 1
        if match.get('method') == 'verified_font_map':
            self.verified += 1
        if match['reasons']:
            self.low_confidence += 1
        key = (cipher.digest, ch, mirrored)
        if key not in self._reported:
            self._reported.add(key)
            self.diagnostics.append({'font_digest': cipher.digest, 'family': cipher.family,
                                     'source': ch, 'mirrored': mirrored, **match})
        return match['character']

    def _literal(self, ch, families):
        if _is_cjk(ord(ch)) and any(
            face.get('family') in families
            and any(lo <= ord(ch) <= hi for lo, hi in _parse_unicode_range(face.get('range')))
            for face in self.faces
        ):
            self.suspicious_passthrough += 1
            key = ('literal', ch, tuple(families))
            if key not in self._reported:
                self._reported.add(key)
                self.diagnostics.append({'source': ch, 'character': ch,
                                         'reasons': ['unmapped_cipher_range'],
                                         'families': families, 'candidates': []})
        return ch

    def decode_char(self, ch, families, mirrored):
        cipher = self._cipher_for(ch, families)
        if cipher is None:
            return self._literal(ch, families)
        self._resolve([(cipher, ch, mirrored)])
        return self._lookup(cipher, ch, mirrored)

    def decode_paragraphs(self, paragraphs):
        # Pass 1 finds each glyph's cipher font; one batched match follows.
        family_lists = {}
        plan = []
        for items in paragraphs:
            row = []
            for item in items:
                mirrored = bool(item.get('mirrored'))
                for part in item.get('parts') or []:
                    fonts = part.get('fonts') or ''
                    families = family_lists.get(fonts)
                    if families is None:
                        families = [name.strip().strip('"\'')
                                    for name in fonts.split(',')]
                        family_lists[fonts] = families
                    for ch in part.get('text') or '':
                        row.append((self._cipher_for(ch, families), ch, mirrored, families))
            plan.append(row)
        self._resolve([entry[:3] for row in plan for entry in row
                       if entry[0] is not None])
        lines = []
        for row in plan:
            text = ''.join(
                self._literal(ch, families) if cipher is None else self._lookup(cipher, ch, mirrored)
                for cipher, ch, mirrored, families in row
            ).strip()
            if text:
                lines.append(text)
        return lines


def decode_payload(payload):
    """Return (paragraphs, stats) for an extraction payload."""
    decoder = QdnFontDecoder(payload)
    lines = decoder.decode_paragraphs(payload.get('paragraphs') or [])
    return lines, {
        'decoded': decoder.decoded,
        'verified_glyphs': decoder.verified,
        'shape_matched_glyphs': decoder.decoded - decoder.verified,
        'low_confidence': decoder.low_confidence,
        'fonts': [cipher.family for cipher in decoder.fonts],
        'missing_fonts': decoder.missing_fonts,
        'suspicious_passthrough': decoder.suspicious_passthrough,
        'unstable_paragraphs': payload.get('unstableParagraphs') or [],
        'diagnostics': decoder.diagnostics,
        'thresholds': {'score': _CONFIDENCE_SCORE, 'margin': _CONFIDENCE_MARGIN,
                       'prior_window': _PRIOR_WINDOW},
    }


def write_diagnostics(payload, lines, stats, folder):
    """Save a replayable, allowlisted capture without account data or URLs."""
    from scripts.source_names import dump
    faces, data = [], {}
    for index, face in enumerate(payload.get('faces') or []):
        key = f'font:{index}'
        faces.append({'family': face.get('family', ''), 'range': face.get('range', ''), 'url': key})
        value = (payload.get('fontData') or {}).get(face.get('url'))
        if value and not value.startswith('ERR'):
            data[key] = value
    paragraphs = [[{
        key: item[key] for key in ('mirrored', 'x', 'y', 'h', 'w', 'src') if key in item
    } | {'parts': [{key: part[key] for key in ('text', 'fonts', 'src') if key in part}
                   for part in item.get('parts') or []]} for item in row]
                  for row in payload.get('paragraphs') or []]
    capture = {'faces': faces, 'fontData': data, 'paragraphs': paragraphs,
               'unstableParagraphs': payload.get('unstableParagraphs') or []}
    os.makedirs(folder, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json',
                                     prefix='qdn-debug-', dir=folder, delete=False) as stream:
        dump({'schema': 1, 'payload': capture, 'lines': lines, 'stats': stats}, stream, indent=2)
        return stream.name

# Installed before the reader's scripts run (context init script). The
# reader builds one font face in script; it is invisible to stylesheets.
QDN_FONTFACE_HOOK_JS = r'''(() => {
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
QDN_EXTRACT_JS = r'''async () => {
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
  const mirrored = (el, extra = 'none') => {
    let combined = new DOMMatrix(extra === 'none' ? undefined : extra);
    for (let node = el; node; node = node.parentElement) {
      const transform = getComputedStyle(node).transform;
      if (transform !== 'none') combined = new DOMMatrix(transform).multiply(combined);
    }
    return combined.a < 0;
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
  let duplicates = 0;
  // Measure generated text with real Range boxes. Computed advances alone
  // cannot locate wrapped, positioned, flex-ordered, or transformed pseudos.
  let measurementId = 0;
  const painted = (rect, el) => {
    let left = rect.left, right = rect.right, top = rect.top, bottom = rect.bottom;
    for (let node = el; node; node = node.parentElement) {
      const st = getComputedStyle(node);
      const box = node.getBoundingClientRect();
      if (['hidden', 'clip', 'scroll', 'auto'].includes(st.overflowX)) {
        left = Math.max(left, box.left); right = Math.min(right, box.right);
      }
      if (['hidden', 'clip', 'scroll', 'auto'].includes(st.overflowY)) {
        top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom);
      }
      // Legacy clipping is still used by inline decoy containers.
      const clip = st.clip.match(/^rect\((.*)\)$/);
      if (clip && ['absolute', 'fixed'].includes(st.position)) {
        const values = clip[1].split(/[,\s]+/).filter(Boolean);
        if (values.length === 4 && values.every((v) => v !== 'auto')) {
          const [t, r, b, l] = values.map(parseFloat);
          if (r <= l || b <= t) return false;
        }
      }
      if (right - left <= 0.01 || bottom - top <= 0.01) return false;
    }
    return true;
  };
  const capture = (p) => {
    hiddenCache.clear();
    const atoms = [];
    const addPseudo = (el, which) => {
      const st = getComputedStyle(el, which);
      const text = unquote(st.content);
      if (!text || st.display === 'none' || st.visibility === 'hidden'
          || parseFloat(st.opacity) === 0
          || parseFloat(st.fontSize) < 1 || transparent(st.color)) return;
      // Snapshot before suppressing the original pseudo: computed styles
      // are live objects. Everything is restored synchronously before the
      // reader's mutation observers or the next scroll can run.
      const properties = [...st].map((name) => [name, st.getPropertyValue(name)]);
      const fonts = st.fontFamily;
      const flip = mirrored(el, st.transform);
      const attribute = 'data-qdn-capture';
      const previous = el.getAttribute(attribute);
      const id = String(++measurementId);
      const sheet = document.createElement('style');
      sheet.textContent = `[${attribute}="${id}"]${which}{content:none!important;}`;
      const probe = document.createElement('qdn-generated-text');
      for (const [name, value] of properties) {
        if (!['content', 'background-image', 'animation', 'transition'].includes(name)) {
          probe.style.setProperty(name, value, 'important');
        }
      }
      probe.textContent = text;
      try {
        el.setAttribute(attribute, id);
        document.head.appendChild(sheet);
        if (which === '::before') el.prepend(probe); else el.appendChild(probe);
        const range = document.createRange();
        let offset = 0;
        for (const ch of text) {
          range.setStart(probe.firstChild, offset);
          range.setEnd(probe.firstChild, offset + ch.length);
          offset += ch.length;
          const rect = range.getBoundingClientRect();
          if (rect.width <= 0.01 || rect.height <= 0.01 || !painted(rect, probe)) {
            dropped += 1; continue;
          }
          fonts.split(',').forEach((f) => usedFamilies.add(f.replace(/["']/g, '').trim()));
          atoms.push({x: rect.left + rect.width / 2, y: rect.top, h: rect.height,
                      w: rect.width, text: ch, fonts, src: which.slice(2), mirrored: flip});
        }
      } finally {
        probe.remove(); sheet.remove();
        if (previous === null) el.removeAttribute(attribute); else el.setAttribute(attribute, previous);
      }
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
          // Decoys have no box at all; zoomed-out glyphs are only a few px.
          if (r.width <= 0.01 || r.height <= 0.01 || !painted(r, el)) { dropped += 1; continue; }
          atoms.push({x: r.left + r.width / 2, y: r.top, h: r.height, w: r.width, text: ch,
                      fonts: st.fontFamily, src: 'text', mirrored: flip});
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
      addPseudo(el, '::before');
      for (const child of el.childNodes) walk(child);
      addPseudo(el, '::after');
    };
    walk(p);
    // Group into visual lines, then read each line left to right.
    atoms.sort((a, b) => a.y - b.y);
    const lines = [];
    for (const atom of atoms) {
      // Fallback punctuation and cipher faces can have different ascents.
      // Comparing just their tops split one painted line into two, moving
      // names/characters behind its punctuation. Match overlapping boxes.
      const line = lines.find((candidate) => candidate.atoms.some((other) => {
        const overlap = Math.min(atom.y + atom.h, other.y + other.h)
          - Math.max(atom.y, other.y);
        return overlap > Math.min(atom.h, other.h) * 0.5;
      }));
      if (line) {
        line.atoms.push(atom);
      } else {
        lines.push({y: atom.y, atoms: [atom]});
      }
    }
    lines.sort((a, b) => a.y - b.y);
    const ordered = [];
    for (const line of lines) {
      line.atoms.sort((a, b) => a.x - b.x);
      const kept = [];
      for (const atom of line.atoms) {
        if (kept.some((other) => other.text === atom.text && other.fonts === atom.fonts
            && other.mirrored === atom.mirrored && other.src !== atom.src
            && Math.abs(other.x - atom.x) <= Math.max(0.05, Math.min(other.w, atom.w) * 0.05)
            && Math.abs(other.y - atom.y) <= Math.max(0.05, atom.h * 0.1))) {
          duplicates += 1;
          continue;
        }
        kept.push(atom);
        ordered.push({parts: [{text: atom.text, fonts: atom.fonts}],
                      mirrored: atom.mirrored, x: atom.x, y: atom.y,
                      h: atom.h, w: atom.w, src: atom.src});
      }
    }
    return ordered;
  };
  // The reader keeps a decoy copy of every paragraph away from the viewport
  // and swaps in the real text (in about 25 ms) once the paragraph comes
  // within ~200 px of it. Step through the chapter a screen at a time and
  // read each paragraph once it is fully on screen and the swaps settled.
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  await Promise.race([document.fonts.ready, sleep(2000)]);
  const all = [...main.querySelectorAll('p')];
  const paragraphs = new Array(all.length).fill(null);
  const skipped = new Set();
  const view = window.innerHeight;
  const inView = (p) => {
    const r = p.getBoundingClientRect();
    return r.height > 0.01 && r.top >= 0 && r.bottom <= view;
  };
  all.forEach((p, i) => {
    // A paragraph may be visibility:hidden while children set it back
    // to visible, so only display/opacity rule a paragraph out.
    if (concealed(p) || p.getBoundingClientRect().height <= 0.01) skipped.add(i);
  });
  const onScreenText = () => all.filter((p) => {
    const box = p.getBoundingClientRect();
    return box.bottom > 0 && box.top < view;
  }).map((p) => [p.innerHTML,
    ...[p, ...p.querySelectorAll('*')].map((el) => {
      const box = el.getBoundingClientRect();
      return [box.x, box.y, box.width, box.height,
        ...[null, '::before', '::after'].map((which) => {
          const st = getComputedStyle(el, which);
          return [st.content, st.fontFamily, st.fontSize, st.transform,
                  st.display, st.visibility, st.opacity, st.color, st.clip,
                  st.overflowX, st.overflowY].join('|');
        })].join('|');
    })].join('|')).join('\u0000') + document.fonts.status;
  const unstable = new Set();
  const settle = async () => {
    // Initially stable text may still be a decoy. Allow a full second
    // for scroll/attribute swaps, then require a quiet rendered signature.
    const started = Date.now();
    const start = onScreenText();
    let last = start;
    let lastChange = started;
    while (Date.now() - started < 2500) {
      await sleep(25);
      const now = onScreenText();
      if (now !== last) lastChange = Date.now();
      last = now;
      if (Date.now() - lastChange >= 150 && Date.now() - started >= 1000
          && document.fonts.status === 'loaded') return true;
    }
    return false;
  };
  const remaining = () => all.some((p, i) => paragraphs[i] === null && !skipped.has(i));
  // Fast path: zoom out until the whole chapter fits on one screen. The
  // reader picks which paragraphs to reveal from layout positions when the
  // page scrolls, so a 1px scroll after zooming reveals every paragraph at
  // once. (A resized or emulated viewport alone does not trigger it.)
  window.scrollTo(0, 0);
  const fit = (view - 16) / Math.max(1, main.getBoundingClientRect().bottom);
  if (fit < 1 && fit >= 0.08) {
    const root = document.documentElement;
    const previousZoom = root.style.zoom;
    root.style.zoom = String(fit);
    try {
      await sleep(25);
      window.scrollTo(0, 1);
      window.scrollTo(0, 0);
      const stable = await settle();
      all.forEach((p, i) => {
        if (paragraphs[i] === null && !skipped.has(i) && inView(p)) {
          paragraphs[i] = capture(p);
          if (!stable) unstable.add(i);
        }
      });
    } finally {
      root.style.zoom = previousZoom;
      window.scrollTo(0, 0);
    }
  }
  // Otherwise (or for anything the zoom missed) step a screen at a time.
  const tallest = Math.max(0, ...all.map((p) => p.getBoundingClientRect().height));
  const step = Math.max(120, view - tallest - 16);
  const top = main.getBoundingClientRect().top + window.scrollY - 8;
  // Stop at the end of the text: reaching the page end opens the next chapter.
  const bottom = Math.max(top, main.getBoundingClientRect().bottom + window.scrollY - view + 8);
  for (let y = top; remaining(); y = Math.min(bottom, y + step)) {
    window.scrollTo(0, y);
    const stable = await settle();
    all.forEach((p, i) => {
      if (paragraphs[i] === null && !skipped.has(i) && inView(p)) {
        paragraphs[i] = capture(p);
        if (!stable) unstable.add(i);
      }
    });
    if (y >= bottom) break;
  }
  // Only a paragraph taller than the screen is left; read it from its top.
  for (let i = 0; i < all.length; i += 1) {
    if (paragraphs[i] !== null || skipped.has(i)) continue;
    all[i].scrollIntoView({block: 'start'});
    const stable = await settle();
    paragraphs[i] = capture(all[i]);
    if (!stable) unstable.add(i);
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
  return {scriptedCount: scripted.length, skipped: skipped.size,
          faces: usedFaces, fontData,
          paragraphs: paragraphs.filter((p) => p !== null), dropped,
          duplicates, unstableParagraphs: [...unstable],
          url: location.href, title: document.title};
}'''
