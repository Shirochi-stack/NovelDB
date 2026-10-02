"""Read Floo VIP chapter images back into text.

Floo never sends a bought VIP chapter's text to the browser; its reader
shows images drawn on the server (Page4VipImage.aspx) with GDI+ ClearType
text in Microsoft YaHei, saved as web-palette GIFs. The image request
accepts font_size=32 and font_color=000000, which gives crisp black text on
a fixed 32px grid.

This module renders reference glyphs the same way (GDI+ via PowerShell, on
the user's own Windows machine; the font is not redistributable), then reads
each image line by line: full-width cells are matched against the reference
glyphs with a small alignment search and a character-frequency prior, and
half-width runs (digits, Latin, brackets) are matched at their real advance
widths so the grid stays in sync. Floo's anti-OCR strike lines are erased
first and its watermarks are removed from the text.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover - reported by available()
    np = None

ATLAS_VERSION = 2
PITCH = 32
CELL_W, CELL_H, PEN = 48, 56, 8
_COARSE = 2
_CANDIDATES = 20
_SHIFT = 2
_PRIOR_WEIGHT = 0.05
_ACCEPT = 0.72
_STRIKE_MIN = 36
FULL_PUNCT = '，。、；：？！“”‘’（）《》〈〉【】〔〕「」『』…—～·￥'
ASCII = ''.join(chr(c) for c in range(0x21, 0x7f))
_HERE = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
_ATLAS_SCRIPT = os.path.join(_HERE, 'data', 'floo_glyph_atlas.ps1')
_FONT_PATH = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'msyh.ttc')


def available():
    return (np is not None and sys.platform == 'win32'
            and os.path.isfile(_FONT_PATH) and os.path.isfile(_ATLAS_SCRIPT))


def _cache_dir():
    root = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    path = os.path.join(root, 'NpiaDownloader', 'glyph_cache')
    os.makedirs(path, exist_ok=True)
    return path


# Bare strokes and radicals appear in prose only inside other characters,
# but they match fragments of glyphs and punctuation (丨 for ！, 灬 for ……).
_NEVER_ALONE = set('丨丶丿乀乁乚亅冫冖冂凵勹匚匸卩厶宀彳彡攵灬爫犭疒礻纟艹辶钅饣讠阝'
                   '亻刂廴夂夊尢尣屮彐彑罒')


def _charset():
    import qidian_font_decoder  # shares the GB2312 list and the corpus frequencies
    freq = qidian_font_decoder._load_frequency()
    chars = []
    for index, ch in enumerate(qidian_font_decoder._common_hanzi()):
        # GB2312 level 2 (after the first 3755) holds rare characters; those
        # never seen in the novel corpus only cause look-alike mistakes.
        if ch in _NEVER_ALONE or (index >= 3755 and not freq.get(ch)):
            continue
        chars.append(ch)
    known = set(chars)
    chars += [ch for ch in freq if ch not in known and ch not in _NEVER_ALONE][:3000]
    chars += [ch for ch in FULL_PUNCT if ch not in set(chars)]
    chars += list(ASCII)
    return list(dict.fromkeys(chars)), freq


def _to_ink(image):
    """Luma of an RGB/L image as ink strength 0..1 (black text = 1)."""
    grey = np.asarray(image.convert('L'), np.float32)
    return (255.0 - grey) / 255.0


def _centred(block):
    flat = block.reshape(block.shape[0], -1) if block.ndim == 3 else block.reshape(1, -1)
    flat = flat - flat.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(flat, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return flat / norms


def _rows(matrix):
    """Mean-centre and L2-normalise each row of a 2-D batch."""
    matrix = matrix - matrix.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms


def _integral(a):
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), np.float64)
    np.cumsum(np.cumsum(a, axis=0), axis=1, out=out[1:, 1:])
    return out


def _box_sums(integral, h, w):
    return (integral[h:, w:] - integral[:-h, w:]
            - integral[h:, :-w] + integral[:-h, :-w])


class _Area:
    """An image patch with integral images for fast window statistics."""

    def __init__(self, patch):
        self.patch = patch
        self.sum = _integral(patch)
        self.sq = _integral(patch * patch)
        self.lit = _integral((patch > 0.2).astype(np.float32))

    def ncc_map(self, y, x, height, width, ref, h, w):
        """Correlation of the centred, unit-norm template `ref` (h*w) with
        the mean-centred, normalised window at each position of the crop
        (y, x, height, width), plus which windows hold any ink."""
        from numpy.lib.stride_tricks import sliding_window_view
        crop = self.patch[y:y + height, x:x + width]
        dots = np.einsum('ijkl,kl->ij', sliding_window_view(crop, (h, w)),
                         ref.reshape(h, w))
        rows, cols = slice(y, y + height + 1), slice(x, x + width + 1)
        s1 = _box_sums(self.sum[rows, cols], h, w)
        s2 = _box_sums(self.sq[rows, cols], h, w)
        norm = np.sqrt(np.maximum(s2 - s1 * s1 / (h * w), 0.0))
        scores = np.where(norm > 1e-6, dots / np.maximum(norm, 1e-6), 0.0)
        live = _box_sums(self.lit[rows, cols], h, w) > 0.5
        return scores, live


def _coarse(block):
    """Average-pool 2x2 so small misalignments matter less."""
    h, w = block.shape[-2] // _COARSE * _COARSE, block.shape[-1] // _COARSE * _COARSE
    b = block[..., :h, :w]
    return b.reshape(*b.shape[:-2], h // _COARSE, _COARSE, w // _COARSE, _COARSE).mean(axis=(-3, -1))


class _Atlas:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        chars, freq = _charset()
        glyphs, advances = self._load_or_build(chars)
        self.chars = chars
        self.advances = advances
        self.glyphs = glyphs                      # (N, CELL_H, CELL_W) uint8 ink
        self.full = np.nonzero(advances >= PITCH * 0.75)[0]
        self.half = np.nonzero(advances < PITCH * 0.75)[0]
        windows = self.window(self.full)
        self.full_coarse_raw = _coarse(windows).reshape(len(self.full), -1)
        self.full_coarse = _rows(self.full_coarse_raw)
        self.profile = windows.mean(axis=(0, 2))  # average ink per row
        punct = set(FULL_PUNCT)
        self.punct = np.array([i for i in self.full if chars[i] in punct], np.int64)
        self.punct_boxes = []
        self.punct_ink_h = []
        self.punct_refs = []
        for gid in self.punct:
            ys, xs = np.nonzero(glyphs[gid, :, PEN:PEN + PITCH] > 50)
            y0, y1 = max(0, ys.min() - 2), ys.max() + 3
            x0, x1 = max(0, xs.min() - 2), min(PITCH, xs.max() + 3)
            self.punct_boxes.append((y0, y1, x0, x1))
            self.punct_ink_h.append(ys.max() - ys.min() + 1)
            self.punct_refs.append(_centred(
                glyphs[gid, y0:y1, PEN + x0:PEN + x1].astype(np.float32) / 255.0)[0])
        # Normalised full-size references, filled in as glyphs are compared.
        self._full_refs = {}
        top = max(freq.values()) if freq else 1
        self.prior = np.array([np.log10(freq.get(ch, 0) + 1) / np.log10(top + 1)
                               for ch in chars], np.float32)

    def _load_or_build(self, chars):
        key = hashlib.sha1(('\n'.join(chars) + f'|{ATLAS_VERSION}|'
                            + str(os.path.getsize(_FONT_PATH))).encode('utf-8')).hexdigest()[:16]
        path = os.path.join(_cache_dir(), f'floo_glyphs_{key}.npz')
        if os.path.exists(path):
            data = np.load(path)
            return data['glyphs'], data['advances']
        with tempfile.TemporaryDirectory() as tmp:
            chars_file = os.path.join(tmp, 'chars.txt')
            png = os.path.join(tmp, 'atlas.png')
            adv = os.path.join(tmp, 'adv.txt')
            with open(chars_file, 'w', encoding='utf-8') as handle:
                handle.write(''.join(chars))
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            subprocess.run(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 _ATLAS_SCRIPT, '-CharsFile', chars_file, '-OutPng', png,
                 '-OutAdvances', adv],
                check=True, capture_output=True, timeout=600, creationflags=flags,
            )
            sheet = _to_ink(Image.open(png))
            advances = np.array([float(v) for v in open(adv, encoding='utf-8-sig').read().split()],
                                np.float32)
        columns = sheet.shape[1] // CELL_W
        glyphs = np.stack([
            sheet[(i // columns) * CELL_H:(i // columns + 1) * CELL_H,
                  (i % columns) * CELL_W:(i % columns + 1) * CELL_W]
            for i in range(len(chars))
        ])
        glyphs = (glyphs * 255).round().astype(np.uint8)
        np.savez_compressed(path, glyphs=glyphs, advances=advances)
        return glyphs, advances

    def full_refs(self, ids):
        """Mean-centred, unit-norm PITCH-wide windows of glyphs `ids`."""
        cache = self._full_refs
        missing = [int(i) for i in ids if int(i) not in cache]
        if missing:
            for i, ref in zip(missing, _rows(self.window(missing).reshape(len(missing), -1))):
                cache[i] = ref
        return np.stack([cache[int(i)] for i in ids])

    def window(self, ids, width=PITCH, x0=0):
        """Reference glyph windows (ink 0..1) starting at the pen."""
        return self.glyphs[ids, :, PEN + x0:PEN + x0 + width].astype(np.float32) / 255.0

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance


def warm_up():
    if not available():
        return None
    thread = threading.Thread(target=_Atlas.get, name='floo-atlas', daemon=True)
    thread.start()
    return thread


def _erase_strike_lines(ink):
    """Remove Floo's decoy underlines and return (ink, erased mask).

    Some phrases are underlined to disturb OCR. Measured on real chapters,
    every such line is exactly 2px thick and sits in the last two ink rows
    of its text line, 36px or longer. Longer horizontal runs elsewhere are
    real strokes of neighbouring characters (the bottoms of 且 and 直 side by
    side, 一 touching the next glyph) and must stay.
    """
    dark = ink > 0.5
    erased = np.zeros(ink.shape, bool)
    for top, bottom in _line_bands(ink, merge=False):
        for y in range(max(top, bottom - 3), bottom - 1):
            row, below = dark[y], dark[y + 1]
            edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
            for start, end in zip(edges[::2], edges[1::2]):
                if end - start < _STRIKE_MIN or below[start:end].mean() < 0.8:
                    continue
                above = dark[y - 1, start:end].mean() if y > top else 0.0
                if above > 0.5:
                    continue  # the bottom stroke of glyphs, not a rule
                erased[y:y + 2, start:end] = True
                # Faint antialiased fringe just above and below the rule.
                for fy in (y - 1, y + 2):
                    if 0 <= fy < ink.shape[0]:
                        faint = ink[fy, start:end] < 0.5
                        erased[fy, start:end] |= faint
        # Decorated phrases also get thin lines through the glyph tops and
        # bottoms, broken only where they cross strokes. Find rows near the
        # band's edges whose ink runs on (gaps of a few pixels bridged) far
        # longer than any glyph, and denser than the rows beside them.
        edge_rows = list(range(top, min(top + 5, bottom))) + \
            list(range(max(top, bottom - 8), bottom))
        for y in sorted(set(edge_rows)):
            if erased[y].any():
                continue
            lit = ink[y] > 0.15
            bridged = lit.copy()
            for gap in range(1, 5):
                bridged[:-gap] |= lit[gap:]
            edges = np.flatnonzero(np.diff(np.concatenate(([0], bridged.astype(np.int8), [0]))))
            for start, end in zip(edges[::2], edges[1::2]):
                if end - start < 64:
                    continue
                cover = lit[start:end].mean()
                around = [lit_row[start:end].mean() for lit_row in
                          (ink[y - 1] > 0.15 if y > 0 else lit * 0,
                           ink[y + 1] > 0.15 if y + 1 < ink.shape[0] else lit * 0)]
                if cover >= 0.75 and max(around) < 0.7 * cover:
                    erased[y, start:end] |= lit[start:end]
    ink = ink.copy()
    ink[erased] = 0
    return ink, erased


def _line_bands(ink, merge=True):
    rows = (ink > 0.35).sum(axis=1)
    bands, start = [], None
    for y, count in enumerate(rows):
        if count and start is None:
            start = y
        elif not count and start is not None:
            bands.append([start, y])
            start = None
    if start is not None:
        bands.append([start, len(rows)])
    if not merge:
        return bands
    lines = []
    for top, bottom in bands:
        # Punctuation-only fragments of a line sit a few pixels away.
        if lines and top - lines[-1][1] < 12:
            lines[-1][1] = bottom
        else:
            lines.append([top, bottom])
    return [line for line in lines if line[1] - line[0] >= 3]


class _LineReader:
    def __init__(self, atlas, cache, erased=None):
        self.atlas = atlas
        self.cache = cache
        self.erased = erased
        self.punct_rows = 3
        self.punct_only = False
        self._line_scores = {}

    def prefilter_line(self, ink, top, start, right):
        """Coarse scores for every grid cell of a line in one product."""
        xs = list(range(start, right + PITCH, PITCH))
        probes = [_coarse(self._window(ink, top + dy, x, PITCH)).ravel()
                  for x in xs for dy in (-1, 0, 1)]
        if not probes:
            self._line_scores = {}
            return
        scores = (_rows(np.stack(probes)) @ self.atlas.full_coarse.T)
        scores = scores.reshape(len(xs), 3, -1).max(axis=1)
        self._line_scores = {(top, x): scores[i] for i, x in enumerate(xs)}

    @staticmethod
    def _tall_window(ink, top, x, width, height):
        block = np.zeros((height, width), np.float32)
        y0, y1 = max(top, 0), min(top + height, ink.shape[0])
        x0, x1 = max(x, 0), min(x + width, ink.shape[1])
        if y1 > y0 and x1 > x0:
            block[y0 - top:y1 - top, x0 - x:x1 - x] = ink[y0:y1, x0:x1]
        return block

    def _window(self, ink, top, x, width):
        block = np.zeros((CELL_H, width), np.float32)
        y0, y1 = max(top, 0), min(top + CELL_H, ink.shape[0])
        x0, x1 = max(x, 0), min(x + width, ink.shape[1])
        if y1 > y0 and x1 > x0:
            block[y0 - top:y1 - top, x0 - x:x1 - x] = ink[y0:y1, x0:x1]
        return block

    def _full(self, ink, top, x):
        """Best full-width glyph for the cell at (top, x): (char, score, dx)."""
        base = self._window(ink, top, x, PITCH)
        lost = None
        if self.erased is not None:
            lost = self._window(self.erased, top, x, PITCH) > 0
        damaged = lost is not None and bool(lost.any())
        key = base.round(2).tobytes() + (lost.tobytes() if damaged else b'')
        if key in self.cache:
            return self.cache[key]
        atlas = self.atlas
        scores = self._line_scores.get((top, x))
        if scores is None:
            probes = [_coarse(self._window(ink, top + dy, x, PITCH)).ravel()
                      for dy in (-1, 0, 1)]
            scores = (_rows(np.stack(probes)) @ atlas.full_coarse.T).max(axis=0)
        # Damaged cells rely on the masked comparison below; give it more
        # candidates instead of re-normalising every reference per cell.
        take = min(_CANDIDATES * (2 if damaged else 1), len(scores) - 1)
        cand = np.argpartition(-scores, take)[:take]
        shifts = [(dx, dy) for dx in range(-_SHIFT, _SHIFT + 1)
                  for dy in range(-_SHIFT, _SHIFT + 1)]
        if damaged:
            refs_full = atlas.window(atlas.full[cand]).reshape(len(cand), -1)
            corr = np.empty((len(shifts), len(cand)), np.float32)
            for row, (dx, dy) in enumerate(shifts):
                window = self._window(ink, top + dy, x + dx, PITCH).ravel()
                keep_px = 1.0 - (self._window(self.erased, top + dy, x + dx, PITCH)
                                 > 0).ravel()
                corr[row] = _rows(refs_full * keep_px) @ _centred(window * keep_px)[0]
        else:
            from numpy.lib.stride_tricks import sliding_window_view
            patch = self._tall_window(ink, top - _SHIFT, x - _SHIFT, PITCH + 2 * _SHIFT,
                                      CELL_H + 2 * _SHIFT)
            # views[dy, dx]; shifts run dx-major.
            views = sliding_window_view(patch, (CELL_H, PITCH))
            windows = _rows(views.transpose(1, 0, 2, 3).reshape(len(shifts), -1))
            corr = windows @ atlas.full_refs(atlas.full[cand]).T  # (shifts, candidates)
        best_shift = corr.argmax(axis=0)
        best_corr = corr.max(axis=0)
        glyph_ids = atlas.full[cand]
        pick = int(np.argmax(best_corr + _PRIOR_WEIGHT * atlas.prior[glyph_ids]))
        result = (atlas.chars[glyph_ids[pick]], float(best_corr[pick]),
                  shifts[best_shift[pick]][0])
        self.cache[key] = result
        return result

    def line_phase(self, ink, top, start, right):
        """Start position (±4px) at which the line's first cells match best."""
        cells = []
        x = start
        while x < right and len(cells) < 4:
            if self._window(ink, top, x, PITCH).max() > 0.2:
                cells.append(x)
            x += PITCH
        if not cells:
            return start
        shifts = range(-4, 5)
        blocks = _centred(_coarse(np.stack(
            [self._window(ink, top, x + shift, PITCH)
             for shift in shifts for x in cells])))
        scores = (blocks @ self.atlas.full_coarse.T).max(axis=1)
        scores = scores.reshape(len(shifts), len(cells)).mean(axis=1)
        return start + shifts[int(scores.argmax())]

    def _punct(self, ink, top, x):
        """Full-width punctuation, which Floo places up to ~8px away from
        where GDI+ draws it alone, so it gets a wider horizontal search."""
        atlas = self.atlas
        ids = atlas.punct
        if not len(ids):
            return None, -1.0
        from numpy.lib.stride_tricks import sliding_window_view
        best = (None, -1.0)
        area = self._tall_window(ink, top - self.punct_rows, x - 12, PITCH + 24,
                                 CELL_H + 2 * self.punct_rows)
        solid = area > 0.35
        if not solid.any():
            return best
        # Floo draws each mark identically every time; reuse the answer.
        # Key on the cell itself; the mark never leaves it.
        rows = CELL_H + 2 * self.punct_rows
        key = b'p' + self._tall_window(ink, top - self.punct_rows, x - 2, PITCH + 4,
                                       rows).round(2).tobytes()
        if self.erased is not None:
            key += self._tall_window(self.erased, top - self.punct_rows, x - 2,
                                     PITCH + 4, rows).tobytes()
        if key in self.cache:
            return self.cache[key]
        best = self._punct_search(ink, top, x, solid)
        self.cache[key] = best
        return best

    def _punct_search(self, ink, top, x, solid):
        from numpy.lib.stride_tricks import sliding_window_view
        atlas = self.atlas
        ids = atlas.punct
        best = (None, -1.0)
        ink_h = np.ptp(np.flatnonzero(solid.any(axis=1))) + 1
        vr = self.punct_rows
        # Every template's search region is a crop of this one area.
        area = _Area(self._tall_window(ink, top - vr, x - 12, PITCH + 24,
                                       CELL_H + 2 * vr))
        lost_area = None
        if self.erased is not None:
            lost_area = self._tall_window(self.erased, top - vr, x - 12,
                                          PITCH + 24, CELL_H + 2 * vr)
        for k, gid in enumerate(ids):
            y0, y1, x0, x1 = atlas.punct_boxes[k]
            h, w = y1 - y0, x1 - x0
            if atlas.punct_ink_h[k] > ink_h + 6:
                continue  # needs more ink than the cell has
            ref = atlas.punct_refs[k]
            height, width = h + 2 * vr, w + 24
            region = area.patch[y0:y0 + height, x0:x0 + width]
            lost = None
            if lost_area is not None:
                lost = lost_area[y0:y0 + height, x0:x0 + width]
                if not lost.any():
                    lost = None
            if lost is None:
                # Nothing erased here: score every position without copies.
                score_map, live_map = area.ncc_map(y0, x0, height, width, ref, h, w)
                live = live_map.ravel()
                if not live.any():
                    continue
                scores = score_map.ravel()[live]
            else:
                views = sliding_window_view(region, (h, w)).reshape(-1, h * w)
                live = views.max(axis=1) > 0.2
                if not live.any():
                    continue
                keep = 1.0 - sliding_window_view(lost, (h, w)).reshape(-1, h * w)[live]
                refs = ref[None, :] * keep
                refs = refs - refs.mean(axis=1, keepdims=True)
                refs /= np.maximum(np.linalg.norm(refs, axis=1, keepdims=True), 1e-6)
                scores = (_rows(views[live] * keep) * refs).sum(axis=1)
            i = int(scores.argmax())
            if scores[i] <= best[1]:
                continue
            # A real punctuation mark stands alone; strokes elsewhere in the
            # cell mean this is part of a character (了 is not "！").
            pos = np.flatnonzero(live)[i]
            oy, ox = divmod(pos, region.shape[1] - w + 1)
            cell = self._window(ink, top, x, PITCH)
            inside = cell.copy()
            by0, bx0 = oy - vr + y0, ox - 12 + x0
            inside[max(0, by0):max(0, by0 + h), max(0, bx0):max(0, bx0 + w)] = 0
            # Neighbouring glyphs may reach a column or two into the cell.
            # A punctuation-only line's tall search window reaches into the
            # lines around it; there is no character to confuse it with.
            solid = cell > 0.5
            rest = inside[:, 2:-2] > 0.5
            # Decorated phrases are boxed by thin full-height rules; they are
            # not strokes of a character.
            rest[:, rest.sum(axis=0) >= 20] = False
            if not self.punct_only and rest.sum() > 0.15 * max(int(solid.sum()), 1):
                continue
            best = (atlas.chars[gid], float(scores[i]))
        return best

    def _half(self, ink, top, x):
        """Best half-width glyph whose pen position is near x."""
        atlas = self.atlas
        best = (None, -1.0, 0.0, 0)
        for dx in range(-3, 4):
            for gid in atlas.half:
                width = max(4, int(round(atlas.advances[gid])))
                window = self._window(ink, top, x + dx, width + 2)
                ref = atlas.window([gid], width + 2)[0]
                if window.max() <= 0.2:
                    continue
                a = _centred(window)[0]
                b = _centred(ref)[0]
                score = float(a @ b)
                if score > best[1]:
                    best = (atlas.chars[gid], score, float(atlas.advances[gid]), dx)
        return best

    def read(self, ink, top, left, right):
        """Characters of one line from pen position `left` to ink end `right`."""
        out, x = [], float(left)
        while x < right - 2:
            xi = int(round(x))
            cell = self._window(ink, top, xi, PITCH)
            if cell.max() <= 0.2:
                out.append('\u3000')
                x += PITCH
                continue
            bar = self._bar(cell)
            if bar:
                out.append(bar)
                x += PITCH
                continue
            if self.punct_only:
                punct, pscore = self._punct(ink, top, xi)
                out.append(punct if punct and pscore >= 0.6 else '\ufffd')
                x = self._resync(ink, top, xi + PITCH, right)
                continue
            ch, score, dx = self._full(ink, top, xi)
            rows_used = np.flatnonzero(cell.max(axis=1) > 0.2)
            small = len(rows_used) and rows_used[-1] - rows_used[0] < 14
            if score >= _ACCEPT and not small:
                out.append(ch)
                x += PITCH + dx
                continue
            punct, pscore = self._punct(ink, top, xi)
            if pscore >= _ACCEPT and (pscore > score or (small and pscore > score - 0.05)):
                out.append(punct)
                # Marks drift inside their cell but keep the grid; squeezed
                # ones are caught by the re-sync below on the next glyph.
                x = xi + PITCH
                continue
            if score >= _ACCEPT:
                out.append(ch)
                x += PITCH + dx
                continue
            # Squeezed marks ("……", quotes) push the next glyphs off the
            # grid; a full-width glyph a few pixels away beats a guess here.
            xr = self._resync(ink, top, xi, right)
            if xr != xi:
                ch2, score2, dx2 = self._full(ink, top, xr)
                if score2 >= _ACCEPT:
                    out.append(ch2)
                    x = xr + PITCH + dx2
                    continue
            half, hscore, advance, hdx = self._half(ink, top, xi)
            if half is not None and hscore >= 0.6 and hscore > score:
                out.append(half)
                x += advance + hdx
            else:
                out.append(ch if score >= 0.5 else '\ufffd')
                # An unreliable match says nothing about where the next cell
                # starts; find the offset where the following cell fits.
                x = self._resync(ink, top, xi + PITCH, right)
        return ''.join(out)

    @staticmethod
    def _bar(cell):
        """一 is a lone thin bar that shifts too easily to match reliably."""
        solid = cell > 0.5
        # Decorated phrases add thin vertical rules at cell edges; ignore
        # the outer columns when deciding whether the cell is just a bar.
        solid[:, :3] = False
        solid[:, -3:] = False
        # Rows carrying real ink; a stray decoy dot or two does not count.
        weight = solid.sum(axis=1)
        rows = np.flatnonzero(weight >= 6)
        if not len(rows) or rows[-1] - rows[0] > 3:
            return ''
        if solid.sum() - weight[rows[0]:rows[-1] + 1].sum() > 8:
            return ''  # other strokes: a real glyph
        cols = np.flatnonzero(solid[rows[0]:rows[-1] + 1].any(axis=0))
        width = cols[-1] - cols[0] + 1
        if width < 14 or not 20 <= rows[0] <= 34:
            return ''
        # The dash runs edge to edge into the next cell ("——"); 一 has
        # side bearings.
        touches = cell[rows[0]:rows[-1] + 1] > 0.5
        return '—' if touches[:, 0].any() and touches[:, -1].any() else '一'

    def _resync(self, ink, top, x, right):
        """Pen position within ±16px of x where a glyph matches best."""
        probes, offsets = [], []
        for d in range(-16, 17):
            window = self._window(ink, top, x + d, PITCH)
            if window.max() > 0.2:
                probes.append(_coarse(window).ravel())
                offsets.append(d)
        if not probes or x >= right:
            return x
        scores = (_rows(np.stack(probes)) @ self.atlas.full_coarse.T).max(axis=1)
        return x + offsets[int(scores.argmax())]


def _vertical_offset(ink, atlas, band):
    """Row offset placing a line's glyphs where the atlas draws them."""
    top, bottom = band
    best, best_score = top - PEN, -2.0
    ref = atlas.profile - atlas.profile.mean()
    for offset in range(top - CELL_H + 8, bottom - 8):
        rows = np.zeros(CELL_H, np.float32)
        y0, y1 = max(offset, 0), min(offset + CELL_H, ink.shape[0])
        if y1 <= y0:
            continue
        rows[y0 - offset:y1 - offset] = ink[y0:y1].mean(axis=1)
        rows -= rows.mean()
        norm = np.linalg.norm(rows) * np.linalg.norm(ref)
        score = float(rows @ ref / norm) if norm else -1.0
        if score > best_score:
            best, best_score = offset, score
    return best


def _calibrate_phase(ink, atlas, bands, tops, origin, samples=60):
    """Grid origin (±4px) at which sampled cells match glyphs best."""
    cells = []
    for band, top in zip(bands, tops):
        xs = np.flatnonzero((ink[band[0]:band[1]] > 0.35).any(axis=0))
        if not len(xs):
            continue
        start = origin + max(0, (xs[0] - origin + 4) // PITCH) * PITCH
        for x in range(start, xs[-1], PITCH * 3):
            cells.append((top, x))
            if len(cells) >= samples:
                break
        if len(cells) >= samples:
            break
    if not cells:
        return origin
    best, best_score = origin, -1.0
    for shift in range(-4, 5):
        blocks = []
        for top, x in cells:
            block = np.zeros((CELL_H, PITCH), np.float32)
            y0, y1 = max(top, 0), min(top + CELL_H, ink.shape[0])
            x0, x1 = max(x + shift, 0), min(x + shift + PITCH, ink.shape[1])
            if y1 > y0 and x1 > x0:
                block[y0 - top:y1 - top, x0 - x - shift:x1 - x - shift] = ink[y0:y1, x0:x1]
            if block.max() > 0.2:
                blocks.append(block)
        if not blocks:
            continue
        score = float((_centred(_coarse(np.stack(blocks))) @ atlas.full_coarse.T)
                      .max(axis=1).mean())
        if score > best_score:
            best, best_score = origin + shift, score
    return best


def read_image(data, cache=None):
    """Lines of one Floo chapter image as (text, starts_paragraph)."""
    atlas = _Atlas.get()
    if isinstance(data, (bytes, bytearray)):
        image = Image.open(io.BytesIO(data))
    elif isinstance(data, np.ndarray):
        image = Image.fromarray(data)
    else:
        image = data
    ink, erased = _erase_strike_lines(_to_ink(image.convert('RGB')))
    columns = (ink > 0.35).sum(axis=0)
    # Cells never cross the grid, so the grid sits where the least ink
    # falls; several columns tie, so settle the phase by match quality.
    origin = min(range(PITCH), key=lambda o: int(columns[o::PITCH].sum()))
    bands = _line_bands(ink)
    tops = [_vertical_offset(ink, atlas, band) for band in bands]
    # A line of only punctuation is a few pixels tall and its row profile
    # says little; full lines fix how far a line's bottom sits below it.
    full = [bottom - top for (_, bottom), top in zip(bands, tops)
            if bottom - _ >= 24]
    if full:
        drop = int(np.median(full))
        tops = [top if bottom - band_top >= 24 else bottom - drop
                for (band_top, bottom), top in zip(bands, tops)]
    origin = _calibrate_phase(ink, atlas, bands, tops, origin)
    reader = _LineReader(atlas, cache if cache is not None else {}, erased)
    lines = []
    for band, top in zip(bands, tops):
        line_ink = (ink[band[0]:band[1]] > 0.35).any(axis=0)
        xs = np.flatnonzero(line_ink)
        if not len(xs):
            continue
        # A glyph's ink may begin a pixel or two left of its cell, and
        # wrapped lines sit a couple of pixels off the indented lines' grid.
        start = origin + max(0, (xs[0] - origin + 4) // PITCH) * PITCH
        short = band[1] - band[0] < 16
        reader.punct_only = short
        reader.punct_rows = 30 if short else 3
        if not short:
            start = reader.line_phase(ink, top, start, xs[-1] + 1)
            reader.prefilter_line(ink, top, start, xs[-1] + 1)
        text = reader.read(ink, top, start, xs[-1] + 1)
        indent = (start - origin) // PITCH
        lines.append((('\u3000' * indent + text).rstrip(), indent >= 2))
    return lines


# e.g. [072409129floo083493221]; digits may be misread as letters.
_WATERMARK_INLINE = re.compile(
    r'[\[\]［］「」|lI]?[0-9０-９A-Za-z|\ufffd]{4,14}\u98de\u5362[0-9０-９A-Za-z|\ufffd]{4,14}[\[\]［］「」|lI！!]?')
_AD = re.compile(r'[（(][^（）()]{0,12}就上\u98de\u5362小说网[^（）()]{0,3}[）)]')
_LONE_DASH = re.compile(r'(?<=[\u3400-\u9fff])—(?=[\u3400-\u9fff])')
_WATERMARK_LINE = re.compile(r'^\s*(?:\u98de\u5362提醒您|支持\u98de\u5362小说网)')


def lines_to_paragraphs(lines):
    paragraphs = []
    dropping = False
    for text, new_paragraph in lines:
        body = text.strip('\u3000 ')
        if not body:
            continue
        # Floo's footer paragraphs ("floo提醒您…", "支持floo小说网…") wrap onto
        # continuation lines that carry the reader's ID and IP; drop them all.
        if new_paragraph or not paragraphs:
            dropping = bool(_WATERMARK_LINE.match(body))
        if dropping:
            continue
        if new_paragraph or not paragraphs:
            paragraphs.append(body)
        else:
            paragraphs[-1] += body
    cleaned = []
    for paragraph in paragraphs:
        paragraph = _WATERMARK_INLINE.sub('', paragraph).strip()
        # "—" and "一" are the same stroke; a dash comes in pairs ("——").
        paragraph = _LONE_DASH.sub('一', paragraph)
        # Floo inserts its own ads, e.g. （看爽小说，就上floo小说网！）.
        paragraph = _AD.sub('', paragraph).strip()
        if paragraph:
            cleaned.append(paragraph)
    return cleaned


_pool = None
_pool_lock = threading.Lock()


def _worker_pool():
    """Worker processes kept for the session; the reader is CPU-bound Python
    and threads would only take turns on the GIL."""
    global _pool
    with _pool_lock:
        if _pool is None:
            from concurrent.futures import ProcessPoolExecutor
            _pool = ProcessPoolExecutor(max_workers=_WORKERS)
        return _pool


_WORKERS = max(1, min(6, (os.cpu_count() or 2) - 1))
_SLICE_LINES = 40


def _slices(data):
    """Split one chapter image into strips of about _SLICE_LINES lines, cut
    through blank rows, so worker processes share a chapter evenly."""
    rgb = np.asarray(Image.open(io.BytesIO(data)).convert('RGB'))
    blank = (_to_ink(Image.fromarray(rgb)) > 0.2).sum(axis=1) == 0
    step = _SLICE_LINES * 50  # a line is about 50px tall
    cuts, y = [0], step
    while y < len(blank) - step // 2:
        gap = np.flatnonzero(blank[y:y + step // 2])
        if not len(gap):
            break
        cuts.append(y + int(gap[0]))
        y = cuts[-1] + step
    cuts.append(len(blank))
    return [rgb[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]


def _read_slice(strip):
    return read_image(strip)


def read_chapter(images):
    """Text paragraphs from a chapter's image parts, in order."""
    images = [bytes(data) for data in images]
    lines = []
    if _WORKERS > 1:
        try:
            # Build or load the glyph cache here first, so workers only
            # load the saved copy instead of each rendering it.
            _Atlas.get()
            strips = [strip for data in images for strip in _slices(data)]
            if len(strips) > 1:
                for part in _worker_pool().map(_read_slice, strips):
                    lines.extend(part)
                return lines_to_paragraphs(lines)
        except Exception:
            lines = []  # no worker processes here; read in this one
    cache = {}
    for data in images:
        lines.extend(read_image(data, cache))
    return lines_to_paragraphs(lines)
