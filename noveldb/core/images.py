"""Image download, format detection and re-encoding shared by the download pipelines (moved from gui.py)."""

import io
import json
import re
import threading
import time

from noveldb.core.net_timeouts import request_timeout

try:
    from PIL import Image
except Exception:
    Image = None

# Importing the plugin registers AVIF support in Pillow.
try:
    import pillow_avif  # noqa: F401
    _AVIF_AVAILABLE = True
except Exception:
    _AVIF_AVAILABLE = False


def _format_size(nbytes):
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes / (1024 * 1024):.2f} MB"


def _count_images_in_json(content_json):
    """Lightweight count of <img> tags in chapter JSON without downloading.
    Parses the JSON segments and counts src-bearing <img> tags.
    Returns the count (0 if parsing fails or no images found)."""
    try:
        data = json.loads(content_json)
        segments = data.get("s")
        if not isinstance(segments, list):
            return 0
        count = 0
        img_pat = re.compile(r"<img[^>]+src=[\"'][^\"']+[\"']")
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "")
            if not text or "cover-wrapper" in text:
                continue
            count += len(img_pat.findall(text))
        return count
    except Exception:
        return 0


class _DownloadStats:
    """Thread-safe accumulator for download statistics."""
    def __init__(self):
        self._lock = threading.Lock()
        self.total_images = 0
        self.total_bytes = 0
        self.image_failures = 0
        self.image_failure_chapters = []  # list of (chapter_id, title, count)
        self.blocked_chapters = []   # list of (chapter_id, title)
        self.failed_chapters = []    # list of (chapter_id, title)
        self.t0 = time.time()

    def add(self, image_count, byte_count):
        with self._lock:
            self.total_images += image_count
            self.total_bytes += byte_count

    def add_blocked(self, chapter_id, title):
        with self._lock:
            self.blocked_chapters.append((chapter_id, title))

    def add_failed(self, chapter_id, title):
        with self._lock:
            self.failed_chapters.append((chapter_id, title))

    def add_image_failures(self, chapter_id, title, count):
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return
        with self._lock:
            self.image_failures += count
            self.image_failure_chapters.append((chapter_id, title, count))

    def elapsed(self):
        return time.time() - self.t0

    def summary(self):
        elapsed = self.elapsed()
        parts = [f"Total images: {self.total_images}"]
        if self.total_bytes > 0:
            parts.append(f"Total image data: {_format_size(self.total_bytes)}")
        if elapsed > 0 and self.total_bytes > 0:
            parts.append(f"Avg speed: {_format_size(int(self.total_bytes / elapsed))}/s")
        mins, secs = divmod(int(elapsed), 60)
        if mins > 0:
            parts.append(f"Elapsed: {mins}m {secs}s")
        else:
            parts.append(f"Elapsed: {secs}s")
        return " | ".join(parts)

    def warnings_summary(self):
        """Return a multi-line string of blocked/failed chapter warnings, or empty string."""
        lines = []
        if self.blocked_chapters:
            lines.append(f"\u26d4 {len(self.blocked_chapters)} chapter(s) blocked (login/age verification):")
            for cid, title in self.blocked_chapters:
                lines.append(f"   \u2022 [{cid}] {title}")
        if self.failed_chapters:
            lines.append(f"\u274c {len(self.failed_chapters)} chapter(s) failed after retries:")
            for cid, title in self.failed_chapters:
                lines.append(f"   \u2022 [{cid}] {title}")
        if self.image_failures:
            lines.append(
                f"\u26a0 {self.image_failures} image(s) failed across "
                f"{len(self.image_failure_chapters)} item(s):"
            )
            for cid, title, count in self.image_failure_chapters:
                lines.append(f"   \u2022 [{cid}] {title}: {count} image(s)")
        return "\n".join(lines)


def _download_image_with_progress(
    session,
    url,
    logger,
    label="Image",
    max_retries=3,
    retry_delay=2.0,
):
    """Stream-download an image, logging progress/speed. Returns bytes or None."""
    try:
        max_retries = max(1, int(max_retries))
    except (TypeError, ValueError):
        max_retries = 3
    try:
        retry_delay = max(0.0, float(retry_delay))
    except (TypeError, ValueError):
        retry_delay = 2.0

    def retry_wait(attempt):
        return min(60.0, retry_delay * (2 ** attempt))

    for attempt in range(max_retries):
        try:
            r = session.get(url, timeout=request_timeout(30), stream=True)
            if r.status_code != 200:
                logger(f"  ✗ {label}: HTTP {r.status_code}")
                if r.status_code >= 500 and attempt < max_retries - 1:
                    wait = retry_wait(attempt)
                    logger(f"  ↻ Retrying {label} in {wait:g}s (attempt {attempt + 2}/{max_retries})...")
                    if wait > 0:
                        time.sleep(wait)
                    continue
                return None
            total = int(r.headers.get('content-length', 0))
            chunks, downloaded, t0, last_pct = [], 0, time.time(), 0
            last_log_bytes, last_log_time = 0, t0
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if total > 0:
                        pct = int(downloaded / total * 100)
                        if pct >= last_pct + 10:
                            speed = _format_size(int(downloaded / max(time.time() - t0, 0.001))) + "/s"
                            logger(f"  ↓ {label}: {pct}% ({_format_size(downloaded)}/{_format_size(total)}) [{speed}]")
                            last_pct = pct
                    elif downloaded - last_log_bytes >= 512 * 1024 or now - last_log_time >= 5:
                        speed = _format_size(int(downloaded / max(now - t0, 0.001))) + "/s"
                        logger(f"  ↓ {label}: {_format_size(downloaded)} [{speed}]")
                        last_log_bytes, last_log_time = downloaded, now
            data = b''.join(chunks)
            speed = _format_size(int(len(data) / max(time.time() - t0, 0.001))) + "/s"
            logger(f"  ✓ {label}: {_format_size(len(data))} [{speed}]")
            return data
        except Exception as e:
            if attempt < max_retries - 1:
                wait = retry_wait(attempt)
                logger(f"  ✗ {label}: {e}")
                logger(f"  ↻ Retrying {label} in {wait:g}s (attempt {attempt + 2}/{max_retries})...")
                if wait > 0:
                    time.sleep(wait)
                continue
            else:
                logger(f"  ✗ {label}: {e} (failed after {max_retries} attempts)")
                return None
    return None


def _detect_source_ext(img_bytes, url_hint=""):
    """Return the canonical file extension for raw image bytes.

    Prefers PIL's header detection (reliable regardless of URL) and only
    falls back to URL extension heuristics if PIL is unavailable or can't
    identify the bytes. This is critical for Npia's imagebox URLs,
    which often don't expose the extension — so URL-only detection would
    mislabel GIFs as JPEGs and bypass the 'preserve GIF' code path.
    """
    fmt_to_ext = {
        "JPEG": "jpg", "PNG": "png", "WEBP": "webp",
        "AVIF": "avif", "GIF": "gif",
    }
    if Image is not None:
        try:
            fmt = (Image.open(io.BytesIO(img_bytes)).format or "").upper()
            if fmt in fmt_to_ext:
                return fmt_to_ext[fmt]
        except Exception:
            pass
    lo = (url_hint or "").lower()
    if ".gif" in lo:
        return "gif"
    if ".png" in lo:
        return "png"
    if ".webp" in lo:
        return "webp"
    if ".avif" in lo:
        return "avif"
    return "jpg"


def _encode_image_bytes(im, image_format, quality, logger=None, static_only=False):
    """Encode a PIL image to `image_format` and return (bytes, ext).

    Preserves animation when the source has multiple frames AND the target
    format supports it (WEBP, AVIF, PNG/APNG). JPEG drops animation
    (first frame only) because the codec itself has no sequence support.

    When `static_only` is True, ANY animated source is flattened to its
    first frame regardless of the target format — this yields the smallest
    possible files at the cost of motion.

    If `image_format` is "AVIF" but pillow-avif-plugin is missing, this
    falls back to WEBP and logs a warning.
    """
    from PIL import ImageSequence

    is_animated = (
        getattr(im, "is_animated", False)
        and getattr(im, "n_frames", 1) > 1
        and not static_only
    )

    # Pick the working colour mode for the target format.
    wants_alpha = image_format in ("WEBP", "PNG", "AVIF")
    target_mode = "RGBA" if wants_alpha else "RGB"

    # Collect (copies of) frames + per-frame durations. A static image produces a single-element list.
    frames = []
    durations = []
    if is_animated:
        for f in ImageSequence.Iterator(im):
            fr = f.convert(target_mode) if f.mode != target_mode else f.copy()
            frames.append(fr)
            durations.append(f.info.get("duration", 100))
    else:
        still = im.convert(target_mode) if im.mode != target_mode else im
        if image_format == "JPEG" and still.mode != "RGB":
            still = still.convert("RGB")
        frames.append(still)

    loop = im.info.get("loop", 0)
    save_animated = is_animated and image_format in ("WEBP", "PNG", "AVIF")

    # Resolve AVIF availability once; fall back to WEBP uniformly.
    target = image_format
    if target == "AVIF" and not _AVIF_AVAILABLE:
        if logger:
            logger(
                "  \u26a0 AVIF plugin not installed, falling back to WEBP. "
                "Install 'pillow-avif-plugin'."
            )
        target = "WEBP"

    out = io.BytesIO()

    if target == "WEBP":
        if save_animated:
            frames[0].save(
                out, format="WEBP", save_all=True,
                append_images=frames[1:], duration=durations, loop=loop,
                quality=int(quality),
            )
        else:
            frames[0].save(out, format="WEBP", quality=int(quality))
        return out.getvalue(), "webp"

    if target == "PNG":
        # save_all=True with PNG produces APNG when there are multiple frames.
        if save_animated:
            frames[0].save(
                out, format="PNG", save_all=True,
                append_images=frames[1:], duration=durations, loop=loop,
                optimize=True,
            )
        else:
            frames[0].save(out, format="PNG", optimize=True)
        return out.getvalue(), "png"

    if target == "AVIF":
        # AVIF-specific encoder knobs:
        #   speed: 0 (slowest, smallest) .. 10 (fastest, largest). Pillow default
        #     is 6 which leaves a LOT of bytes on the table. speed=3 roughly
        #     halves the output for a few x slower encode — a good tradeoff
        #     when we're already re-encoding for size.
        #   subsampling "4:2:0" is the default; keep it explicit so Pillow
        #     versions with different defaults behave consistently.
        avif_kwargs = dict(
            quality=int(quality),
            speed=3,
            subsampling="4:2:0",
        )
        if save_animated:
            frames[0].save(
                out, format="AVIF", save_all=True,
                append_images=frames[1:], duration=durations, loop=loop,
                **avif_kwargs,
            )
        else:
            frames[0].save(out, format="AVIF", **avif_kwargs)
        return out.getvalue(), "avif"

    # JPEG: static only (animation flattens to first frame).
    first = frames[0]
    if first.mode != "RGB":
        first = first.convert("RGB")
    first.save(out, format="JPEG", quality=int(quality), optimize=True)
    return out.getvalue(), "jpg"
