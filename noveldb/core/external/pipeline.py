"""The external downloader pipeline (moved verbatim from external_dialog.py's ExternalNovelDialog).

`ExternalPipelineMixin` fetches books through `ExternalScraper`, downloads chapters with caching,
retries and rate limiting, and writes TXT, CBZ, EPUB and PDF. It reads options through `_var_*`
objects with `.get()`/`.set()` and reports through `_msg_queue.put((kind, data))` (the Tk dialog
polls it; `noveldb.core.events.MsgQueueAdapter` turns it into events), so the same code runs under
Tk, Qt, or headless. `_ui_fetch_done` is the one interface hook.
"""

import base64
import hashlib
import html
import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path

from external_scraper import ExternalScraper, write_text_output
from noveldb.core.chapter_cache import ChapterCache
from noveldb.core.chapter_timing import timing_suffix
from noveldb.core.net_timeouts import page_timeout_ms, request_timeout


def _get_base_dir():
    """The app folder (next to the exe, or the source checkout)."""
    from noveldb.core import paths
    return str(paths.install_dir())


def _value_errors():
    """Errors a bad option value raises; includes Tk's TclError when tkinter is loaded."""
    tk = sys.modules.get("tkinter")
    extra = (tk.TclError,) if tk is not None and hasattr(tk, "TclError") else ()
    return (AttributeError, TypeError, ValueError) + extra


def _external_cache_dir():
    try:
        from noveldb.core import paths as _paths
        return os.path.join(str(_paths.cache_dir(_get_base_dir())), 'external')
    except Exception:
        return os.path.join(_get_base_dir(), '.cache', 'external')


def _sanitize_filename(name):
    """Remove characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip('. ')
    return name or "novel"


def _scrambled_text(data):
    """True when a chapter's Korean text is random syllables (a site's anti-copy scrambling)."""
    from noveldb.core.engines.generic import scrambled_hangul

    return scrambled_hangul((data or {}).get('contentText') or re.sub(
        r'<[^>]+>', ' ', (data or {}).get('contentHtml') or ''))


def _format_size(num_bytes):
    """Human-readable byte count for progress logs."""
    try:
        value = float(num_bytes or 0)
    except Exception:
        value = 0.0
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


class _OutputStopped(BaseException):
    """Stop was pressed while a file was being built. A BaseException, so the generators'
    `except Exception` fallbacks (EPUB failed: write TXT instead) do not catch it."""


class ExternalPipelineMixin:
    """External-source downloads: fetch, download, batch, cache and output."""

    def _publish_live_chapter(self, index, data):
        events = getattr(self, "events", None)
        if events is None:
            return
        body = ""
        if data and not data.get("_locked"):
            body = data.get("contentHtml") or '<div style="white-space:pre-wrap">' + html.escape(data.get("contentText") or "") + '</div>'
            for image in data.get("images") or []:
                if isinstance(image, dict) and image.get("url") and str(image.get("data", "")).startswith("data:"):
                    body = body.replace('"' + image["url"] + '"', '"' + image["data"] + '"')
        events.live_chapter(index, body)

    def _ui_fetch_done(self):
        """A fetch finished; the interface re-enables its Download button."""

    # -- moved from external_dialog.py ------------------------------------------------

    # ------------------------------------------------------------------
    # Thread-safe logging via queue
    # ------------------------------------------------------------------
    def _log(self, text):
        """Thread-safe: enqueue a log message."""
        self._msg_queue.put(("log", text))

    def _apply_scraper_options(self):
        """Copy current dialog options into the scraper instance."""
        if not self._scraper:
            return
        self._scraper.mpia_interval, self._scraper.mpia_interval_max = (
            self._get_interval_range()
        )
        self._scraper.kpage_skip_last_page = (
            self._var_kpage_skip_last_page.get()
        )
        self._scraper.kpage_keep_filler = self._var_kpage_keep_filler.get()
        self._scraper.ntk_prefer_npia_cover = (
            self._var_ntk_npia_cover.get()
        )
        include_notices = getattr(
            self._parent_gui, 'var_include_notices', None
        )
        self._scraper.npia_include_notices = (
            include_notices.get() if include_notices else True
        )
        self._scraper.syosetu_amazon_cover_fallback = (
            self._var_syosetu_amazon_cover.get()
        )
        self._scraper.kobo_horizontal_layout = (
            self._var_kobo_horizontal_layout.get()
        )
        engine_order = getattr(self, '_var_engine_order', None)
        if engine_order is not None:
            self._scraper.engine_order = list(engine_order.get())
        dynamic = getattr(self, '_var_engine_dynamic', None)
        if dynamic is not None:
            self._scraper.dynamic_priority = bool(dynamic.get())

    # ------------------------------------------------------------------
    # Persistent worker thread (all Playwright calls run here)
    # ------------------------------------------------------------------
    def _worker_loop(self):
        """Single long-lived thread that owns the Playwright browser.

        Commands arrive via self._work_queue as (kind, payload) tuples.
        """
        while True:
            try:
                kind, payload = self._work_queue.get()
            except Exception:
                break

            if kind == "quit":
                break
            elif kind == "fetch":
                self._do_fetch(payload)
            elif kind == "download":
                self._do_download(*payload)
            elif kind == "fetch_and_download":
                self._do_fetch_and_download(*payload)
            elif kind == "batch":
                self._do_batch(payload)
            elif kind == "browser":
                if isinstance(payload, dict):
                    self._do_open_browser(
                        payload.get("url"),
                        payload.get("regular", False),
                    )
                else:
                    self._do_open_browser(payload)
            elif kind == "sfc_app_login":
                self._do_sfc_app_login(payload)
            elif kind == "sfc_android_open":
                self._do_sfc_android_open()
            elif kind == "sfc_android_import":
                self._do_sfc_android_import()

    def _do_fetch(self, url):
        """Run parse_book on the worker thread."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
                if (not self._scraper.is_ntk_novel(url)
                        and not self._scraper.is_qdn(url)
                        and not self._scraper.is_yeduji(url)
                        and not self._scraper.is_1qxs(url)
                        and not self._scraper.is_69shuba(url)
                        and not self._scraper.is_floo(url)
                        and not self._scraper.is_fenrir(url)
                        and not self._scraper.is_maru(url)
                        and not self._scraper.is_xiyuwx(url)
                        and not self._scraper.is_global_npia(url)
                        and not self._scraper.is_rbooks(url)
                        and not self._scraper.is_kobo(url)
                        and not self._scraper.is_mpia(url)
                        and not self._scraper.is_jara(url)
                        and not self._scraper.is_nweb_series(url)
                        and not self._scraper.is_nweb_novel(url)
                        and not self._scraper.is_npia(url)):
                    self._scraper.start()
            self._apply_scraper_options()

            data = self._scraper.parse_book(url)
            if data:
                self._msg_queue.put(("book_parsed", data))
            else:
                self._msg_queue.put(("error", "Failed to parse book info."))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))
        finally:
            self._ui_fetch_done()

    def _sleep_while_downloading(self, seconds):
        """Sleep in short slices so Stop can interrupt rate limiting."""
        deadline = time.time() + max(0, seconds)
        while time.time() < deadline:
            if not self._downloading:
                return False
            time.sleep(min(0.1, deadline - time.time()))
        return self._downloading

    @staticmethod
    def _external_cache_chapter_key(chapter, fallback_index=0):
        """Return a stable cache key for a chapter across index refreshes."""
        for field in ('episodeId', 'id', 'itemId', 'productId', 'url'):
            value = chapter.get(field) if isinstance(chapter, dict) else None
            if value not in (None, ''):
                return f'{field}:{value}'
        return f'index:{fallback_index}'

    def _external_cache_path(self):
        """Return the per-book external cache file without exposing the URL."""
        book_url = (self._book_data or {}).get('bookUrl') or self._url_var.get()
        digest = hashlib.sha256(str(book_url).encode('utf-8')).hexdigest()[:24]
        cache_dir = _external_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f'{digest}.json')

    def _load_external_cache(self):
        if not self._var_use_cache.get():
            return {}, None
        try:
            path = self._external_cache_path()
        except Exception as exc:
            self._log(f"⚠ External cache is unavailable: {exc}")
            return {}, None
        entries = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as cache_file:
                    payload = json.load(cache_file)
                if ((self._book_data or {}).get('_lncrawl')
                        and payload.get('textEncodingVersion', 1) < 2):
                    self._log("Refreshing the chapter cache after the text decoding fix.")
                elif isinstance(payload.get('chapters'), dict):
                    entries = payload['chapters']
            except Exception as exc:
                self._log(f"⚠ Legacy external cache could not be loaded: {exc}")
        try:
            for key, value in ChapterCache(path).entries().items():
                if value is None:
                    entries.pop(key, None)
                else:
                    entries[key] = value
        except Exception as exc:
            self._log(f"⚠ External chapter checkpoints could not be loaded: {exc}")
        if entries:
            self._log(f"Cache loaded ({len(entries)} external chapters cached).")
        return entries, path

    @staticmethod
    def _external_cache_result(result, cache_images):
        """Make a JSON-safe result, retaining downloaded image bytes on request."""
        cached = json.loads(json.dumps(result))
        if not cache_images:
            for image in cached.get('images') or []:
                if isinstance(image, dict):
                    image.pop('data', None)
            cached.pop('_coverData', None)
        return cached

    @staticmethod
    def _external_cacheable(result):
        # A login, purchase, or app-viewer refusal can change between runs.
        # Reusing it after the user signs in would prevent any network retry.
        if not isinstance(result, dict) or result.get('_locked'):
            return False
        # Old RBOOKS desktop exports could silently omit their opening pages.
        # Source covers are local reader bytes, unavailable after stripping
        # image data from a cache entry.
        cached_html = str(result.get('contentHtml') or '')
        if ('rbooks-content' in cached_html or
                '\u0072\u0069\u0064\u0069-content' in cached_html):
            from rbooks_app_proxy import RbooksAppProxy
            if (not RbooksAppProxy.is_verified_export(result) or
                    (result.get('_rbooksAppHasSourceCover') and
                     not result.get('_coverData'))):
                return False
        # Kobo's blob image URLs exist only in the reader process. A cache
        # without their captured bytes cannot recreate the EPUB later.
        return not any(
            isinstance(image, dict)
            and str(image.get('url') or '').startswith('blob:')
            and not image.get('data')
            for image in result.get('images') or []
        )

    def _cache_external_result_images(self, result):
        """Attach downloaded image bytes so cached chapters are self-contained."""
        if not isinstance(result, dict) or not self._var_cache_images.get():
            return result
        images = result.get('images') or []
        for image_index, image in enumerate(images, 1):
            if not isinstance(image, dict):
                continue
            data_url = image.get('data') or ''
            if data_url and not data_url.startswith('file:'):
                continue
            image_url = html.unescape(image.get('url') or '').strip()
            if not image_url:
                continue
            # A page the scraper saved to a temporary file goes into the cache as bytes.
            raw = ExternalPipelineMixin._decode_image_data_url(data_url)
            if (not raw and (self._book_data or {}).get('_ntk_novel')
                    and self._scraper):
                fetch_ntk_binary = getattr(
                    self._scraper, 'fetch_ntk_binary', None
                )
                if fetch_ntk_binary:
                    raw = fetch_ntk_binary(
                        image_url,
                        result.get('chapterUrl')
                        or (self._book_data or {}).get('bookUrl', ''),
                    )
            if not raw:
                raw = self._download_image_python(
                    image_url,
                    f"Cached image {image_index}/{len(images)}",
                    request_cookies=(
                        image.get('_cookies') or result.get('_imageCookies')
                    ),
                    log_success=False,
                    log_failure=True,
                )
            if raw:
                extension = self._image_ext_from_bytes(raw)
                mime = 'image/jpeg' if extension == 'jpg' else f'image/{extension}'
                image['data'] = (
                    f'data:{mime};base64,'
                    + base64.b64encode(raw).decode('ascii')
                )
        return result

    def _do_download(self, chapters, start, end, interval, num_threads=1,
                      skip_paid=False, retry_passes=5, interval_max=None):
        """Run chapter downloads on the worker thread.

        Chapters are split into batches of `num_threads` size.
        Each batch uses the scraper's fastest available concurrent path.
        Qdn renders one chapter per browser page in the batch.
        Failed chapters are retried immediately before the next batch starts.
        """
        # Keep the source-book position after slicing.  Output writers use
        # this instead of restarting their filenames at chapter 1.
        self._chapter_number_start = max(1, start + 1)
        try:
            interval_max_supplied = interval_max is not None
            interval, interval_max = ExternalScraper._normalize_interval_range(
                interval, interval_max
            )
            retry_passes = max(1, int(retry_passes))
            # Ensure the headless browser is alive.  After "Enter Browser"
            # the context is destroyed; start() re-launches it with fresh
            # cookies from browser_data (including any new login sessions).
            is_ntk = bool(
                self._book_data and self._book_data.get("_ntk_novel")
            )
            is_yeduji = bool(
                self._book_data and self._book_data.get("_yeduji")
            )
            is_npia = bool(
                self._book_data and self._book_data.get("_npia")
            )
            is_mpia = bool(
                self._book_data and self._book_data.get("_mpia")
            )
            is_rbooks = bool(
                self._book_data and self._book_data.get("_rbooks")
            )
            is_kobo = bool(
                self._book_data and self._book_data.get("_kobo")
            )
            is_kobo_desktop = bool(
                self._book_data and self._book_data.get('_kobo_desktop')
            )
            is_global_npia = bool(
                self._book_data
                and self._book_data.get("_global_npia")
            )
            is_69shuba = bool(
                self._book_data and self._book_data.get("_69shuba")
            )
            is_floo = bool(
                self._book_data and self._book_data.get("_floo")
            )
            is_fenrir = bool(
                self._book_data and self._book_data.get("_fenrir")
            )
            is_maru = bool(
                self._book_data and self._book_data.get("_maru")
            )
            is_xiyuwx = bool(
                self._book_data and self._book_data.get("_xiyuwx")
            )
            is_1qxs = bool(
                self._book_data and self._book_data.get("_1qxs")
            )
            is_jara = bool(
                self._book_data and self._book_data.get("_jara")
            )
            is_nweb_novel = bool(
                self._book_data and self._book_data.get("_nweb_novel")
            )
            if (self._scraper and not self._scraper._context
                    and not is_ntk and not is_yeduji and not is_npia
                    and not is_mpia
                    and not is_rbooks
                    and not is_kobo
                    and not is_global_npia
                    and not is_69shuba and not is_1qxs and not is_floo
                    and not is_fenrir and not is_maru
                    and not is_xiyuwx
                    and not is_jara and not is_nweb_novel
                    and not (self._book_data and self._book_data.get('_qdn'))):
                self._scraper.start()
                # Navigate to the book page so that JS fetch() calls
                # originate from the \u006b\u0061\u006b\u0061\u006f.com domain.  The BFF API
                # returns 403 Forbidden for requests from about:blank.
                if (self._scraper._page and self._book_data
                        and self._book_data.get('_kpage')
                        and self._scraper._book_url):
                    try:
                        self._scraper._page.goto(
                            self._scraper._book_url,
                            wait_until="domcontentloaded",
                            timeout=page_timeout_ms(30000),
                        )
                        self._scraper._page.wait_for_timeout(1000)
                    except Exception:
                        pass
            self._apply_scraper_options()

            if is_npia:
                notices = [ch for ch in chapters if ch.get('isNotice')]
                regular_chapters = [
                    ch for ch in chapters if not ch.get('isNotice')
                ]
                # Match the main downloader: author notices are prepended,
                # while From/To continues to select regular chapters only.
                selected_regular = regular_chapters[start:end]
                selected = (
                    notices + selected_regular if selected_regular else []
                )
            else:
                selected = chapters[start:end]
            live = getattr(self, "events", None)
            if live is not None:
                live.live_book(selected)
            total = len(selected)
            results = [None] * total
            use_cache_variable = getattr(self, '_var_use_cache', None)
            image_cache_variable = getattr(self, '_var_cache_images', None)
            use_cache = bool(
                use_cache_variable and use_cache_variable.get()
                and not is_kobo_desktop
            )
            cache_images = bool(
                use_cache and image_cache_variable
                and image_cache_variable.get()
            )
            if use_cache:
                cache_entries, cache_path = (
                    ExternalPipelineMixin._load_external_cache(self)
                )
            else:
                cache_entries, cache_path = {}, None
            checkpoint = ChapterCache(cache_path) if cache_path else None
            cache_write_failed = False

            def save_checkpoint(key, value):
                nonlocal cache_write_failed
                if checkpoint is None:
                    return False
                try:
                    checkpoint.put(key, value)
                    return True
                except Exception as exc:
                    if not cache_write_failed:
                        self._log(f"⚠ External chapter checkpoint could not be saved: {exc}")
                        cache_write_failed = True
                    return False

            def numbered(idx, data):
                chapter = selected[idx]
                is_notice = bool(chapter.get('isNotice'))
                data.setdefault('_is_notice', is_notice)
                source_number = chapter.get('_npiaNoticeNumber', idx + 1) if is_notice else chapter.get(
                    '_npiaChapterNumber', chapter.get('_globalNpiaChapterNumber', start + idx + 1)
                )
                data.setdefault('_chapter_number', source_number)
                return data

            def checkpoint_result(idx, data, include_images):
                if checkpoint is None or not ExternalPipelineMixin._external_cacheable(data):
                    return
                if is_kobo and not cache_images:
                    return
                snapshot = numbered(idx, dict(data))
                snapshot.pop("_scrape_seconds", None)
                cached = ExternalPipelineMixin._external_cache_result(snapshot, include_images)
                # A path into a temporary reader is invalid after a restart.
                if any(isinstance(image, dict) and str(image.get('data') or '').startswith('file:')
                       for image in cached.get('images') or []):
                    return
                if ExternalPipelineMixin._external_cacheable(cached):
                    key = ExternalPipelineMixin._external_cache_chapter_key(selected[idx], start + idx)
                    return save_checkpoint(key, cached)
            cache_hits = 0
            stale_cache_entries = 0
            if use_cache:
                for selected_index, chapter in enumerate(selected):
                    cache_key = ExternalPipelineMixin._external_cache_chapter_key(
                        chapter, start + selected_index
                    )
                    cached = cache_entries.get(cache_key)
                    if cached is not None and (
                        not ExternalPipelineMixin._external_cacheable(cached)
                        or (is_kobo and not cache_images)
                    ):
                        cache_entries.pop(cache_key, None)
                        save_checkpoint(cache_key, None)
                        stale_cache_entries += 1
                        continue
                    if isinstance(cached, dict):
                        if cache_images:
                            ExternalPipelineMixin._cache_external_result_images(
                                self, cached
                            )
                            if cached.get('images'):
                                checkpoint_result(selected_index, cached, True)
                        results[selected_index] = cached
                        ExternalPipelineMixin._publish_live_chapter(self, selected_index, cached)
                        cache_hits += 1
                if stale_cache_entries:
                    self._log(
                        f"Ignored {stale_cache_entries} stale cached "
                        "chapter(s); checking access again."
                    )
                if cache_hits:
                    self._log(
                        f"Cache hits: {cache_hits}; "
                        f"{total - cache_hits} chapter(s) need network download."
                    )
            is_qdn = bool(
                self._book_data and self._book_data.get('_qdn')
            )
            batch_size = 1 if is_npia else max(1, num_threads)
            rate_interval = interval
            rate_interval_max = interval_max
            if is_npia:
                self._log(
                    "[Npia] Safe mode: one chapter request at a time; "
                    "automatic chapter retries are disabled."
                )
            if is_qdn and self._scraper:
                qdn_floor = getattr(
                    self._scraper, '_QDN_MIN_INTERVAL', 0.0
                )
                rate_interval = max(
                    interval,
                    qdn_floor,
                )
                rate_interval_max = max(
                    interval_max, rate_interval
                )
            completed = 0

            # Pre-filter paid chapters if the user opted to skip them.
            # For Kpage, skip only rows that the product list marks as not
            # accessible to this account. Purchased/rented rows are kept.
            # Mpia's free-only option excludes purchased paid chapters too.
            # Explicitly inaccessible Mpia rows never need a viewer request.
            if skip_paid or is_mpia or is_fenrir:
                skipped = 0
                unavailable = 0
                for i, ch in enumerate(selected):
                    if is_mpia:
                        inaccessible = ch.get('isAccessible') is False
                        should_skip = inaccessible or (
                            skip_paid and bool(
                                ch.get('isVIP', False)
                                or ch.get('isPaid', False)
                            )
                        )
                        unavailable += int(inaccessible)
                    else:
                        should_skip = (
                            ch.get('isVIP', False)
                            and not ch.get('isAccessible', False)
                        )
                    if should_skip:
                        results[i] = {'_locked': True,
                                      'chapterName': ch.get('name', ''),
                                      '_chapter_number': start + i + 1}
                        ExternalPipelineMixin._publish_live_chapter(self, i, results[i])
                        skipped += 1
                if skipped:
                    if is_mpia:
                        if unavailable:
                            self._log(
                                f"  [Mpia] Skipped {unavailable} chapter(s) "
                                "unavailable to this account."
                            )
                        if skipped > unavailable:
                            self._log(
                                f"  [Mpia] Skipped {skipped - unavailable} "
                                "paid chapter(s) (free-only)."
                            )
                    else:
                        self._log(f"  Skipped {skipped} paid chapter(s).")

            last_download_index = (
                max((i for i, result in enumerate(results) if result is None),
                    default=-1)
                if is_mpia else total - 1
            )

            for batch_start in range(0, total, batch_size):
                if not self._downloading:
                    self._download_cancelled = True
                    self._log("Download stopped by user.")
                    break

                batch_end = min(batch_start + batch_size, total)

                # Collect only chapters that haven't been pre-filtered
                batch_indices = [i for i in range(batch_start, batch_end)
                                 if results[i] is None]
                if not batch_indices:
                    completed += (batch_end - batch_start)
                    self._msg_queue.put(("progress", (completed, total)))
                    continue

                batch = [selected[i] for i in batch_indices]

                # For the native browser/API scrapers, a numbered chapter line
                # means that chapter has actually completed. Other legacy
                # scrapers keep announcing work before the fetch starts.
                timing_option = getattr(self, "_var_chapter_timing", None)
                chapter_timing = bool(timing_option and timing_option.get())
                log_on_success = (
                    chapter_timing
                    or
                    is_ntk
                    or is_rbooks
                    or is_kobo
                    or is_global_npia
                    or is_npia
                    or is_mpia
                    or is_jara
                    or is_nweb_novel
                    or is_fenrir
                )
                if not log_on_success:
                    for i in batch_indices:
                        name = selected[i].get(
                            'name', f'Chapter {start + i + 1}'
                        )
                        self._log(f"  [{i + 1}/{total}] {name}")

                # Workers report as soon as content is ready. Hold later
                # entries briefly so near-simultaneous completions are printed
                # in chapter order instead of thread scheduling order.
                batch_number = (batch_start // batch_size) + 1
                batch_t0 = time.perf_counter()
                batch_options = {'interval': interval}
                saved_in_batch = set()
                if log_on_success:
                    pending_chapter_logs = {}
                    logged_batch_indices = set()
                    next_log_batch_index = 0
                    chapter_log_lock = threading.Lock()

                    def log_chapter_success(batch_index, data):
                        nonlocal next_log_batch_index
                        idx = batch_indices[batch_index]
                        name = (
                            (data or {}).get('chapterName')
                            or selected[idx].get(
                                'name', f'Chapter {start + idx + 1}'
                            )
                        )
                        line = f"  [{idx + 1}/{total}] {name}"
                        if chapter_timing:
                            line += timing_suffix(data)
                        with chapter_log_lock:
                            if batch_index in logged_batch_indices:
                                return
                            pending_chapter_logs[batch_index] = line
                            while next_log_batch_index in pending_chapter_logs:
                                self._log(pending_chapter_logs.pop(
                                    next_log_batch_index
                                ))
                                logged_batch_indices.add(
                                    next_log_batch_index
                                )
                                next_log_batch_index += 1

                    def flush_chapter_success_logs():
                        with chapter_log_lock:
                            for batch_index in sorted(pending_chapter_logs):
                                if batch_index not in logged_batch_indices:
                                    self._log(
                                        pending_chapter_logs[batch_index]
                                    )
                                    logged_batch_indices.add(batch_index)
                            pending_chapter_logs.clear()

                def chapter_completed(batch_index, data):
                    if checkpoint_result(batch_indices[batch_index], data, cache_images):
                        saved_in_batch.add(batch_index)
                    if log_on_success:
                        log_chapter_success(batch_index, data)

                if log_on_success or checkpoint is not None:
                    batch_options['success_callback'] = chapter_completed
                if interval_max_supplied:
                    batch_options['interval_max'] = interval_max
                batch_results = self._scraper.parse_chapter_batch(
                    batch, **batch_options
                )
                batch_elapsed = time.perf_counter() - batch_t0

                for j, data in enumerate(batch_results):
                    idx = batch_indices[j]
                    abort_reason = getattr(self._scraper, 'abort_reason', '')
                    if (data is None and self._downloading and not is_npia
                            and not abort_reason):
                        chapter = selected[idx]
                        for retry_attempt in range(1, retry_passes + 1):
                            if (not self._downloading or getattr(
                                    self._scraper, 'abort_reason', '')):
                                break
                            self._log(
                                f"  [{idx + 1}/{total}] Failed to fetch, "
                                f"retry {retry_attempt}/{retry_passes}"
                            )
                            retry_options = {
                                'interval': max(interval, 1.0),
                                'log_errors': False,
                            }
                            if interval_max_supplied:
                                retry_options['interval_max'] = max(
                                    interval_max, 1.0
                                )
                            data = self._scraper.parse_chapter(
                                start + idx, chapter, **retry_options
                            )
                            if data:
                                if data.get('_locked'):
                                    break
                                if checkpoint_result(idx, data, cache_images):
                                    saved_in_batch.add(j)
                                if log_on_success:
                                    log_chapter_success(j, data)
                                else:
                                    self._log(
                                        f"  [{idx + 1}/{total}] "
                                        "Retry succeeded."
                                    )
                                break
                        if (data is None and self._downloading
                                and not getattr(
                                    self._scraper, 'abort_reason', '')):
                            self._log(
                                f"  [{idx + 1}/{total}] Failed after "
                                f"{retry_passes} retries."
                            )
                    if isinstance(data, dict):
                        chapter_info = selected[idx]
                        numbered(idx, data)
                        if _scrambled_text(data):
                            self._log(
                                f"  ⚠️ [{idx + 1}/{total}] "
                                f"{data.get('chapterName') or chapter_info.get('name', '')}: "
                                "the text looks scrambled (random Korean syllables, "
                                "the site's anti-copy protection). Saved as it came."
                            )
                    if log_on_success and data and not data.get("_locked"):
                        log_chapter_success(j, data)
                    results[idx] = data
                    ExternalPipelineMixin._publish_live_chapter(self, idx, data)
                    lookahead = getattr(self, "_kpage_images", None)
                    if lookahead and data and not data.get("_locked") and self._downloading:
                        lookahead.offer(data)
                    if checkpoint is not None and ExternalPipelineMixin._external_cacheable(data):
                        needs_images = cache_images and any(
                            isinstance(image, dict) and (not image.get('data') or str(image['data']).startswith('file:'))
                            for image in data.get('images') or []
                        )
                        if needs_images:
                            ExternalPipelineMixin._cache_external_result_images(self, data)
                        if needs_images or j not in saved_in_batch:
                            checkpoint_result(idx, data, cache_images)

                if log_on_success:
                    # A failed or locked earlier chapter has no success
                    # callback. Flush later successes now rather than holding
                    # them past the next-batch message.
                    flush_chapter_success_logs()

                completed += (batch_end - batch_start)
                self._msg_queue.put(("progress", (completed, total)))

                # The scraper asked to stop because retrying cannot help.
                # Unlike a user stop, the chapters already downloaded still
                # produce output and a batch continues with its next URL; the
                # remaining chapters are reported as failed.
                abort_reason = getattr(self._scraper, 'abort_reason', '')
                if abort_reason and self._downloading:
                    self._log(f"❌ {abort_reason}")
                    break

                if not self._downloading:
                    self._download_cancelled = True
                    self._log("Download stopped by user.")
                    break

                # Rate limiting between batches
                sleep_time = 0.0
                if rate_interval_max > 0 and batch_end <= last_download_index:
                    sleep_time = ExternalScraper._random_interval_delay(
                        rate_interval, rate_interval_max
                    )
                if is_qdn:
                    sleep_display = (
                        f"{sleep_time:.1f}s"
                        if sleep_time
                        else "0s"
                    )
                    self._log(
                        f"[Qdn] Batch {batch_number} took "
                        f"{batch_elapsed:.1f}s; rate sleep {sleep_display}"
                    )
                elif sleep_time > 0:
                    self._log(
                        f"Next chapter batch in {sleep_time:.1f}s..."
                    )
                if sleep_time > 0:
                    if not self._sleep_while_downloading(sleep_time):
                        self._download_cancelled = True
                        self._log("Download stopped by user.")
                        break

            # Npia intentionally does not retry failed chapters.
            failed_indices = [
                i for i, r in enumerate(results)
                if r is None and self._downloading
            ]
            if failed_indices and is_npia:
                self._log(
                    f"❌ [Npia] {len(failed_indices)} chapter(s) failed. "
                    "They were not retried."
                )

            # --- Summary ---
            ad_required = sum(1 for r in results
                              if r and r.get('_ad_required'))
            locked = sum(1 for r in results
                         if r and r.get('_locked')
                         and not r.get('_ad_required'))
            succeeded = sum(1 for r in results
                            if r and not r.get('_locked'))
            if self._download_cancelled:
                generate_on_stop = bool(
                    getattr(self, '_active_generate_on_stop', False)
                )
                if generate_on_stop and succeeded:
                    output_status = "Partial output will be generated."
                elif generate_on_stop:
                    output_status = (
                        "No downloaded chapters are available for output."
                    )
                else:
                    output_status = "Output generation skipped."
                if locked or ad_required:
                    incomplete = []
                    if locked:
                        incomplete.append(f"{locked} locked/paid")
                    if ad_required:
                        incomplete.append(
                            f"{ad_required} advertisement(s) not completed"
                        )
                    self._log(
                        f"Download cancelled: {succeeded} succeeded, "
                        f"{', '.join(incomplete)}. "
                        f"{output_status}"
                    )
                else:
                    self._log(
                        f"Download cancelled: {succeeded} succeeded. "
                        f"{output_status}"
                    )
                self._chapter_results = results
                return

            failed = sum(1 for r in results if r is None)

            if is_npia:
                status = (
                    "FAILED" if failed
                    else "PARTIAL" if locked or ad_required
                    else "SUCCESS"
                )
                self._log(
                    f"FINAL SUMMARY: {status} | "
                    f"downloaded chapters: {succeeded} | "
                    f"failed chapters: {failed} | "
                    f"locked chapters: {locked} | "
                    f"ads not completed: {ad_required}"
                )

            # Rbooks refuses some owned ebooks in its web viewer (app-only);
            # these are not paywalled, so report them apart.
            web_only = sum(1 for r in results
                           if r and r.get('_lockReason') in ('app_only', 'unsupported'))
            login_required = (sum(1 for r in results
                                  if r and r.get('_lockReason') in
                                  ('login', 'verification'))
                              if is_rbooks or is_kobo else 0)
            # Owned or rented Rbooks volumes the PC viewer could not export, and
            # expired rentals: neither is a paywall.
            export_failed = sum(1 for r in results
                                if r and r.get('_lockReason') == 'export_failed')
            rental_expired = sum(1 for r in results
                                 if r and r.get('_lockReason') == 'rental_expired')
            locked -= web_only + login_required + export_failed + rental_expired
            if (locked or ad_required or web_only or login_required
                    or export_failed or rental_expired):
                incomplete = []
                if locked:
                    incomplete.append(f"{locked} locked (paid)")
                if web_only:
                    incomplete.append(f"{web_only} not available on the web")
                if login_required:
                    incomplete.append(f"{login_required} access not confirmed")
                if export_failed:
                    incomplete.append(f"{export_failed} not exported by the PC viewer")
                if rental_expired:
                    incomplete.append(f"{rental_expired} rental(s) expired")
                if ad_required:
                    incomplete.append(
                        f"{ad_required} advertisement(s) not completed"
                    )
                self._log(
                    f"Download complete: {succeeded} succeeded, "
                    f"{', '.join(incomplete)}, {failed} failed."
                )
                if locked:
                    self._log(
                        "⚠ Locked chapters require a subscription "
                        "or ticket purchase to access."
                    )
                if web_only:
                    self._log(
                        "⚠ Rbooks would not open these volumes in its web "
                        "viewer even though you are signed in. Rbooks serves "
                        "some ebooks only in the RBOOKS app or PC viewer; "
                        "otherwise the volume is not in your library."
                    )
                if login_required:
                    store = 'RBOOKS' if is_rbooks else 'Kobo'
                    self._log(
                        f"⚠ {store} library access could not be confirmed. "
                        "Retry; if needed, sign in using Enter Browser and "
                        "close that window before downloading."
                    )
                if export_failed:
                    self._log(
                        "⚠ The RBOOKS PC viewer could not export some volumes you "
                        "own or rent. They are not paid chapters; download them "
                        "again later, and include the log above if it keeps failing."
                    )
                if rental_expired:
                    self._log("⚠ Some rentals have expired. Rent them again on "
                              "Rbooks to download them.")
                if ad_required:
                    self._log(
                        "⚠ Ad-gated chapters were skipped because Npia "
                        "did not confirm the displayed advertisement."
                    )
            elif failed:
                self._log(
                    f"Download complete: {succeeded} succeeded, "
                    f"{failed} failed."
                )

            if is_rbooks and failed:
                missing = [chapter.get('name') or f'Volume {start + index + 1}'
                           for index, chapter in enumerate(selected)
                           if results[index] is None]
                self._log(f'❌ [Rbooks] INCOMPLETE: {failed} selected volume(s) '
                          'failed verification. Missing: ' + '; '.join(missing))

            self._chapter_results = results
        except Exception as e:
            self._log(f"\u274c Download error: {e}")
        finally:
            if (self._scraper and self._book_data
                    and self._book_data.get('_floo')):
                self._scraper.close_floo_pages()

    def _do_open_browser(self, start_url=None, regular_browser=False):
        """Open a visible browser on the worker thread for manual login."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            # Prefer the current text field URL.  The scraper's _book_url may
            # still point at a previously fetched book.
            start_url = (start_url or self._scraper._book_url
                         or 'https://www.google.com')
            self._scraper.open_visible_browser(
                start_url=start_url,
                regular_browser=regular_browser,
            )
        except Exception as e:
            self._msg_queue.put(("error", f"Browser error: {e}"))
        finally:
            self._msg_queue.put(("browser_closed", None))

    def _do_sfc_app_login(self, payload):
        """Log in to SFC's app API on the worker thread."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            cookie = payload.get("cookie", "")
            if cookie:
                ok = self._scraper.save_sfc_app_cookie(cookie)
            else:
                ok = self._scraper.login_sfc_app(
                    payload.get("username", ""),
                    payload.get("password", ""),
                )
            self._msg_queue.put(("sfc_app_done", ok))
        except Exception as e:
            self._msg_queue.put(("error", f"SFC app login error: {e}"))
            self._msg_queue.put(("sfc_app_done", False))

    def _do_sfc_android_open(self):
        """Open the configured Android emulator."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            ok = self._scraper.open_android_emulator()
            self._msg_queue.put(("sfc_android_done", ok))
        except Exception as e:
            self._msg_queue.put(("error", f"Android emulator error: {e}"))
            self._msg_queue.put(("sfc_android_done", False))

    def _do_sfc_android_import(self):
        """Import SFC app cookie from Android emulator app data."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            ok = self._scraper.import_sfc_app_cookie_from_android()
            self._msg_queue.put(("sfc_app_done", ok))
        except Exception as e:
            self._msg_queue.put(("error", f"Android import error: {e}"))
            self._msg_queue.put(("sfc_app_done", False))

    # ------------------------------------------------------------------
    # Download (combined fetch → download)
    # ------------------------------------------------------------------
    def _normalised_url(self):
        """Return the current URL field value with an http scheme if needed."""
        url = self._url_var.get().strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _get_retry_passes(self):
        """Snapshot the main GUI's chapter retry setting on the UI thread."""
        try:
            return max(1, int(self._retry_variable.get()))
        except _value_errors():
            return 5

    def _get_interval_range(self):
        """Snapshot and normalize the external chapter-delay controls."""
        try:
            interval = self._var_interval.get()
        except _value_errors():
            interval = 0.5
        try:
            interval_max = self._var_interval_max.get()
        except _value_errors():
            # Old dialogs/configs exposed only the fixed ext_interval value.
            interval_max = interval
        return ExternalScraper._normalize_interval_range(
            interval, interval_max
        )

    @staticmethod
    def _format_interval_range(interval, interval_max):
        if interval == interval_max:
            return f"{interval:g}s"
        return f"{interval:g}–{interval_max:g}s"

    # ------------------------------------------------------------------
    # Combined fetch + download worker
    # ------------------------------------------------------------------
    def _do_fetch_and_download(self, url, interval, num_threads, skip_paid,
                               retry_passes=5, interval_max=None):
        """Fetch metadata then download chapters — runs on worker thread."""
        # Step 1: Fetch metadata
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
                self._scraper.prepare_download_browser()
                if (not self._scraper.is_ntk_novel(url)
                        and not self._scraper.is_qdn(url)
                        and not self._scraper.is_yeduji(url)
                        and not self._scraper.is_1qxs(url)
                        and not self._scraper.is_69shuba(url)
                        and not self._scraper.is_floo(url)
                        and not self._scraper.is_fenrir(url)
                        and not self._scraper.is_maru(url)
                        and not self._scraper.is_xiyuwx(url)
                        and not self._scraper.is_global_npia(url)
                        and not self._scraper.is_rbooks(url)
                        and not self._scraper.is_kobo(url)
                        and not self._scraper.is_mpia(url)
                        and not self._scraper.is_jara(url)
                        and not self._scraper.is_nweb_series(url)
                        and not self._scraper.is_nweb_novel(url)
                        and not self._scraper.is_npia(url)):
                    self._scraper.start()
            else:
                self._scraper.prepare_download_browser()
            self._apply_scraper_options()

            data = self._scraper.parse_book(url)
            if not data:
                self._msg_queue.put(("error", "Failed to parse book info."))
                self._msg_queue.put(("finished", None))
                return

            self._book_data = data
            self._msg_queue.put(("book_parsed", data))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))
            self._msg_queue.put(("finished", None))
            return
        if self._download_cancelled or not self._downloading:
            self._msg_queue.put(("finished", None))
            return

        # Step 2: Download chapters
        chapters = data.get('chapters', [])
        start = 0
        end = (
            data.get('chapterCount', len(chapters))
            if data.get('_npia')
            else len(chapters)
        )
        if self._var_from_enabled.get():
            start = max(0, self._var_from.get() - 1)
        if self._var_to_enabled.get():
            end = min(end, self._var_to.get())

        interval_max_supplied = interval_max is not None
        interval, interval_max = ExternalScraper._normalize_interval_range(
            interval, interval_max
        )
        interval_display = self._format_interval_range(
            interval, interval_max
        )
        self._log(f"Starting download: chapters {start + 1}\u2013{end}, "
                  f"threads={num_threads}, interval={interval_display}"
                  + (', skipping paid' if skip_paid else ''))

        try:
            formats = (getattr(self, "_active_output_formats", None) or self._selected_output_formats()) if data.get("_kpage") else ()
            if (data.get("_kpage") and any(fmt != "txt" for fmt in formats)
                    and not (getattr(self, "_var_save_image_urls_only", None) and self._var_save_image_urls_only.get())):
                from noveldb.core.external.kpage_images import KpageImages

                workers = min(32, max(1, self._var_ext_image_workers.get()))
                try:
                    self._kpage_images = KpageImages(self, data.get("bookUrl") or url, workers)
                    self._log(f"[Kpage] Downloading images as episode lists arrive ({workers} workers).")
                except OSError:
                    self._log("[Kpage] Temporary image storage unavailable. Images will download during export.")
            self._do_download(chapters, start, end, interval, num_threads, skip_paid, retry_passes,
                              interval_max=interval_max if interval_max_supplied else None)

            generate_after_stop = (
                self._download_cancelled
                and bool(getattr(
                    self, '_active_generate_on_stop', False
                ))
            )
            if self._download_cancelled and not generate_after_stop:
                return
            successes = sum(1 for r in self._chapter_results
                            if r is not None and not r.get('_locked'))
            failures = sum(
                1 for result in self._chapter_results if result is None
            )
            if (
                (data.get('_npia') or data.get('_rbooks'))
                and failures
                and not generate_after_stop
            ):
                source = 'Rbooks' if data.get('_rbooks') else 'Npia'
                self._log(
                    f"❌ [{source}] Output not generated because {failures} "
                    "chapter(s) failed. No partial EPUB was written."
                )
                return
            if successes > 0:
                self._book_data = data
                self._log(f"Chapter details ready for {successes} chapter(s).")
                self._generate_output()
        except Exception as e:
            self._log(f"❌ Output generation error: {e}")
        finally:
            lookahead = getattr(self, "_kpage_images", None)
            if lookahead:
                self._kpage_images = None
                lookahead.close()
            self._log("Finished.")
            self._msg_queue.put(("finished", None))

    # ------------------------------------------------------------------
    # Batch worker (runs on worker thread)
    # ------------------------------------------------------------------
    def _do_batch(self, payload):
        """Process multiple URLs sequentially on the worker thread."""
        if len(payload) >= 6:
            (urls, interval, num_threads, skip_paid, retry_passes,
             interval_max) = payload[:6]
            interval_max_supplied = True
        else:
            urls, interval, num_threads, skip_paid, retry_passes = payload
            interval_max = interval
            interval_max_supplied = False
        output_dir = self._get_output_dir()

        if self._scraper is None:
            self._scraper = ExternalScraper(logger=self._log)
        try:
            self._scraper.prepare_download_browser()
        except Exception as exc:
            self._msg_queue.put(("error", str(exc)))
            self._msg_queue.put(("finished", None))
            return
        self._apply_scraper_options()

        total_urls = len(urls)
        for url_idx, url in enumerate(urls):
            if not self._downloading:
                self._log("Batch stopped by user.")
                break

            url = url.strip()
            if not url.startswith("http"):
                url = "https://" + url

            self._log(f"\n{'='*50}")
            self._log(f"[{url_idx + 1}/{total_urls}] {url}")
            self._log(f"{'='*50}")

            # Fetch metadata
            try:
                if (not self._scraper.is_ntk_novel(url)
                        and not self._scraper.is_qdn(url)
                        and not self._scraper.is_1qxs(url)
                        and not self._scraper.is_69shuba(url)
                        and not self._scraper.is_floo(url)
                        and not self._scraper.is_fenrir(url)
                        and not self._scraper.is_maru(url)
                        and not self._scraper.is_xiyuwx(url)
                        and not self._scraper.is_global_npia(url)
                        and not self._scraper.is_rbooks(url)
                        and not self._scraper.is_kobo(url)
                        and not self._scraper.is_mpia(url)
                        and not self._scraper.is_jara(url)
                        and not self._scraper.is_nweb_series(url)
                        and not self._scraper.is_nweb_novel(url)
                        and not self._scraper.is_npia(url)
                        and not self._scraper._context):
                    self._scraper.start()
                    self._apply_scraper_options()
                data = self._scraper.parse_book(url)
            except Exception as e:
                self._log(f"\u274c Fetch failed: {e}")
                continue
            if not data:
                self._log("\u274c Failed to parse book info, skipping.")
                continue
            if self._download_cancelled or not self._downloading:
                break

            self._book_data = data
            self._msg_queue.put(("book_parsed", data))
            title = _sanitize_filename(data.get('bookname', 'novel'))
            self._log(f"Title: {title}, Chapters: {data.get('chapterCount', 0)}")

            # Download chapters (respects range settings)
            chapters = data.get('chapters', [])
            if not chapters:
                self._log("No chapters found, skipping.")
                continue

            start = 0
            end = (
                data.get('chapterCount', len(chapters))
                if data.get('_npia')
                else len(chapters)
            )
            if self._var_from_enabled.get():
                start = max(0, self._var_from.get() - 1)
            if self._var_to_enabled.get():
                end = min(end, self._var_to.get())

            self._chapter_results = []
            self._do_download(chapters, start, end, interval,
                              num_threads, skip_paid, retry_passes,
                              interval_max=(
                                  interval_max
                                  if interval_max_supplied else None
                              ))

            generate_after_stop = (
                self._download_cancelled
                and bool(getattr(
                    self, '_active_generate_on_stop', False
                ))
            )
            if self._download_cancelled and not generate_after_stop:
                break

            # Generate output if any succeeded
            successes = sum(1 for r in self._chapter_results
                            if r is not None and not r.get('_locked'))
            failures = sum(
                1 for result in self._chapter_results if result is None
            )
            if (
                (data.get('_npia') or data.get('_rbooks'))
                and failures
                and not generate_after_stop
            ):
                source = 'Rbooks' if data.get('_rbooks') else 'Npia'
                self._log(
                    f"❌ [{source}] Output not generated because {failures} "
                    "chapter(s) failed. No partial file was written."
                )
                continue
            if successes > 0:
                # Temporarily set _book_data for output generation
                old_book = self._book_data
                self._book_data = data
                try:
                    self._log(f"Chapter details ready for {successes} chapter(s).")
                    self._generate_output()
                except Exception as e:
                    self._log(f"❌ Output generation error: {e}")
                finally:
                    self._book_data = old_book

            if self._download_cancelled:
                break

        self._log(f"\nBatch complete: processed {total_urls} URL(s).")
        self._log("Finished.")
        self._msg_queue.put(("finished", None))

    # ------------------------------------------------------------------
    # Output Generation
    # ------------------------------------------------------------------
    def _selected_output_formats(self):
        """Return checked output formats in their stable UI order."""
        return [
            fmt
            for fmt, variable in (
                ('epub', self._var_format_epub),
                ('txt', self._var_format_txt),
                ('pdf', self._var_format_pdf),
                ('cbz', self._var_format_cbz),
                ('folder', getattr(self, '_var_format_folder', None)),
                ('zip', getattr(self, '_var_format_zip', None)),
            )
            if variable is not None and variable.get()
        ]

    def _numbered_title(self, chapter_number, chapter_name):
        """Heading/TOC title, numbered by the chapter's position in the book
        when "Number chapters" is on."""
        if not chapter_name:
            return f"Chapter {chapter_number}"
        setting = getattr(self, '_var_number_chapters', None)
        if setting is None or not setting.get():
            return chapter_name
        return f"{chapter_number}. {chapter_name}"

    def _result_chapter_number(self, result_index, chapter_data=None):
        """Return the chapter's 1-based position in the unsliced book."""
        if isinstance(chapter_data, dict):
            stored_number = chapter_data.get('_chapter_number')
            try:
                if stored_number is not None:
                    return max(1, int(stored_number))
            except (TypeError, ValueError):
                pass
        try:
            start = max(1, int(getattr(self, '_chapter_number_start', 1)))
        except (TypeError, ValueError):
            start = 1
        return start + result_index

    def _generate_output(self):
        """Generate every selected format from downloaded chapter data."""
        data = self._book_data
        if not data:
            return

        title = _sanitize_filename(data.get('bookname', 'novel'))
        author = data.get('author', 'Unknown')

        formats = (
            getattr(self, '_active_output_formats', None)
            or self._selected_output_formats()
        )
        if not formats:
            self._log("❌ No output format selected.")
            return

        self._var_format.set(formats[0])
        self._log(
            "Selected outputs: "
            + ", ".join(fmt.upper() for fmt in formats)
        )
        # A Stop pressed from here on ends the build; one pressed earlier with "generate on
        # stop" is why this build runs, so it does not count.
        self._generation_stop_baseline = bool(
            getattr(self, '_download_cancelled', False))
        try:
            ExternalPipelineMixin._generate_formats(
                self, data, formats, title, author)
        except _OutputStopped:
            self._log("Stopped while building the file; it was not written.")
        finally:
            self._generation_stop_baseline = True

    def _generation_stopped(self):
        """True once Stop is pressed during `_generate_output` (never outside it)."""
        return (bool(getattr(self, '_download_cancelled', False))
                and not getattr(self, '_generation_stop_baseline', True))

    def _generate_formats(self, data, formats, title, author):
        chapter_exports = {"folder", "zip"}.intersection(formats)
        if not chapter_exports:
            return ExternalPipelineMixin._write_selected_formats(self, data, formats, title, author,
                                                                chapter_exports, None, None)

        from noveldb.core.chapter_folder import ChapterWrites

        links_only, height, chapter_folders = self._folder_options()
        compression = self._stitch_compression() if height is not None else None
        with ExitStack() as stack:
            early = None
            if chapter_exports and not links_only and {"epub", "cbz"}.intersection(formats):
                stage = Path(stack.enter_context(tempfile.TemporaryDirectory(
                    dir=self._get_output_dir(), prefix=".noveldb-chapters-")))
                pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
                early = ChapterWrites(stage, pool)

            early_failed = False

            def chapter_ready(index, chapter, pages):
                nonlocal early_failed
                if early_failed or index in early.submitted or ExternalPipelineMixin._generation_stopped(self):
                    return
                try:
                    early.submit(index, self._write_export_chapter, chapter, pages, (), height, chapter_folders, compression)
                except Exception as error:
                    early_failed = True
                    self._log(f"  Could not prepare the chapter export: {error}")

            self._write_selected_formats(data, formats, title, author, chapter_exports, early,
                                         chapter_ready if early is not None else None, preserve_panels=bool(compression))

    def _write_selected_formats(self, data, formats, title, author, chapter_exports, early, chapter_ready,
                                preserve_panels=False):
        image_archive = None
        for fmt in formats:
            if (data.get('_kobo_desktop') and fmt != 'epub'
                    and any(r and r.get('_nativeEpubBytes')
                            for r in self._chapter_results)):
                self._log('  [Kobo] Rakuten Kobo Desktop currently exports '
                          'the source EPUB only; select EPUB for this volume.')
                continue
            if fmt == "txt":
                self._generate_txt(title, author)
            elif fmt == "pdf":
                self._generate_pdf(title, author)
            elif fmt == "cbz":
                if chapter_exports and image_archive is None:
                    image_entries = {}
                    cbz_path = self._generate_image_archive(title, author, image_entries=image_entries,
                                                            on_chapter_ready=chapter_ready, preserve_panels=preserve_panels)
                    if cbz_path and image_entries:
                        image_archive = cbz_path, image_entries
                else:
                    self._generate_image_archive(title, author)
            elif fmt in chapter_exports:
                if fmt == "zip" and "folder" in chapter_exports:
                    continue
                self._generate_folder(title, fmt, image_archive=image_archive, also_zip=len(chapter_exports) == 2,
                                      early=early)
            else:
                if chapter_exports:
                    image_entries = {}
                    epub_path = self._generate_epub(title, author, image_entries=image_entries,
                                                   on_chapter_ready=chapter_ready, preserve_panels=preserve_panels)
                    if epub_path and image_entries:
                        image_archive = epub_path, image_entries
                else:
                    self._generate_epub(title, author)

    @staticmethod
    def _chapter_images(chapter):
        """A chapter's images: its list, else a comic's pages as its text has them. The generic engine's comics
        come as <img> tags only, which the Folder and CBZ outputs (they write the list) found no pages in."""
        images = chapter.get("images") or []
        if images or not (chapter.get("comic") or not (chapter.get("contentText") or "").strip()):
            return images
        pages = []
        for source in re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', chapter.get("contentHtml") or ""):
            source = html.unescape(source).strip()
            if source.startswith("data:"):
                pages.append({"data": source})
            elif source.startswith(("http://", "https://")):
                pages.append({"url": source})
        return pages

    def _folder_options(self):
        links = getattr(self, "_var_save_image_urls_only", None)
        links_only = bool(links and links.get())
        webtoon = getattr(self, "_var_webtoon_export", None)
        stitch = not links_only and webtoon is not None and webtoon.get() and self._var_stitch_images.get()
        height = min(32_000, max(0, self._var_stitch_height.get())) if stitch else None
        return links_only, height, bool(stitch and self._var_stitch_chapter_folders.get())

    def _stitch_compression(self):
        pg = self._parent_gui
        enabled = getattr(pg, "var_compress_images", None)
        if enabled and enabled.get():
            return pg.var_image_format.get(), pg.var_jpeg_quality.get()
        return None

    def _write_export_chapter(self, stage, index, chapter, pages, links, height, chapter_folders, compression):
        from noveldb.core.chapter_folder import chapter_text, write_chapter

        if ExternalPipelineMixin._generation_stopped(self):
            return
        number = ExternalPipelineMixin._result_chapter_number(self, index, chapter)
        name = chapter.get("chapterName") or f"Chapter {number}"
        text = chapter_text(chapter.get("contentHtml") or "")
        if not text and not ExternalPipelineMixin._chapter_images(chapter):
            text = (chapter.get("contentText") or "").strip()
        action = "Stitching" if height is not None and len(pages) > 1 else "Writing"
        self._log(f"  {action} chapter {number}: {name} ({len(pages)} image(s) ready)")
        write_chapter(stage, number, name, text, pages, links, max_height=height,
                      chapter_folders=chapter_folders, compression=compression)

    def _generate_folder(self, title, output_format, *, image_archive=None, also_zip=False, early=None):
        import requests

        from noveldb.core.chapter_folder import ChapterWrites, folder_export

        path = Path(self._get_output_dir()) / f"{title}.{output_format}"
        data = self._book_data or {}
        workers = min(32, max(1, self._var_ext_image_workers.get()))
        links_only, height, subfolders = self._folder_options()
        stitch_compression = self._stitch_compression() if height is not None else None
        pg = self._parent_gui
        compression = getattr(pg, "var_compress_images", None)
        compress = bool(compression and compression.get())
        quality = pg.var_jpeg_quality.get() if compress else None
        image_format = pg.var_image_format.get() if compress else None
        session_pool = queue.Queue()
        with ExitStack() as stack:
            network = stack.enter_context(ExitStack())
            saved_images = {}
            archive = None
            if image_archive and not links_only and not stitch_compression:
                candidates = {key: name for key, name in image_archive[1].items()
                              if not early or key[0] not in early.submitted}
            else:
                candidates = {}
            if candidates:
                try:
                    archive = stack.enter_context(zipfile.ZipFile(image_archive[0]))
                    names = set(archive.namelist())
                    saved_images = {key: name for key, name in candidates.items() if name in names}
                    if saved_images:
                        self._log(f"  Reusing {len(saved_images)} image(s) from {Path(image_archive[0]).suffix.upper()[1:]}.")
                except (OSError, zipfile.BadZipFile):
                    self._log("  Could not reopen the previous export. Fetching chapter images as needed.")

            def open_sessions():
                if not session_pool.empty():
                    return
                template = network.enter_context(requests.Session())
                self._copy_browser_cookies_to_session(template)
                for _ in range(workers):
                    session = network.enter_context(requests.Session())
                    session.headers["Referer"] = data.get("bookUrl") or ""
                    session.cookies.update(template.cookies)
                    session_pool.put(session)

            pool = stack.enter_context(ThreadPoolExecutor(max_workers=workers))

            def fetch(item, chapter_index, chapter_cookies):
                if ExternalPipelineMixin._generation_stopped(self):
                    return None
                position, info = item
                archived = saved_images.get((chapter_index, position))
                if archived:
                    raw = archive.read(archived)
                    return self._image_ext_from_bytes(raw), raw
                cookies = info.get("_cookies") or chapter_cookies
                raw = ExternalPipelineMixin._decode_image_data_url(info.get("data"))
                url = html.unescape(info.get("url", "")).strip()
                if not raw and url:
                    session = session_pool.get()
                    try:
                        if info.get("candidates"):
                            raw = ExternalPipelineMixin._download_image_mirrors(
                                self, [url, *info["candidates"]], "Image", session=session, request_cookies=cookies)
                        else:
                            raw = self._download_image_python(url, "Image", session=session,
                                                              request_cookies=cookies, log_success=False)
                    finally:
                        session_pool.put(session)
                if raw and compress and not stitch_compression:
                    raw = self._compress_image(raw, quality, image_format)
                return (self._image_ext_from_bytes(raw), raw) if raw else None

            with folder_export(path, also_zip=also_zip,
                               prepared=early.root if early else None) as stage, ExitStack() as output_workers:
                if early:
                    writer = early
                else:
                    writer = ChapterWrites(stage, output_workers.enter_context(ThreadPoolExecutor(max_workers=1)))
                for index, chapter in enumerate(self._chapter_results):
                    if ExternalPipelineMixin._generation_stopped(self):
                        raise _OutputStopped()
                    if not chapter or chapter.get("_locked") or index in writer.submitted:
                        continue
                    number = ExternalPipelineMixin._result_chapter_number(self, index, chapter)
                    name = chapter.get("chapterName") or f"Chapter {number}"
                    images = ExternalPipelineMixin._chapter_images(chapter)
                    links = [html.unescape(img["url"]) for img in images if img.get("url")] if links_only else []
                    image_items = list(enumerate(images, 1))
                    missing = sum(1 for position, info in image_items
                                  if (index, position) not in saved_images and info.get("url") and not info.get("data"))
                    if not links_only and missing:
                        self._log(f"  Downloading {missing} image(s) for chapter {number}: {name}")
                        open_sessions()
                    elif not links_only and any(info.get("url") and (index, position) not in saved_images
                                                for position, info in image_items):
                        open_sessions()  # a bad inline image may still need its URL
                    pages = [] if links_only else list(pool.map(
                        lambda item: fetch(item, index, chapter.get("_imageCookies")), image_items))
                    if ExternalPipelineMixin._generation_stopped(self):
                        raise _OutputStopped()
                    available = [(idx, page) for idx, page in enumerate(pages, 1) if page is not None]
                    if len(available) != len(pages):
                        self._log(f"    {len(pages) - len(available)} image(s) could not be downloaded.")
                    writer.submit(index, self._write_export_chapter, chapter, available, links,
                                  height if len(available) == len(images) else None, subfolders, stitch_compression)
                writer.finish()
                if ExternalPipelineMixin._generation_stopped(self):
                    raise _OutputStopped()
                if output_format == "zip" or also_zip:
                    self._log("  Packaging the prepared chapter files into ZIP.")
        self._log(f"✅ Saved: {path}")
        if also_zip:
            self._log(f"✅ Saved: {path.with_suffix('.zip')}")

    def _get_output_dir(self):
        """Get output directory — respects parent GUI's Quick Download path."""
        pg = self._parent_gui
        quick_enable = getattr(pg, 'var_quick_enable', None)
        quick_path = getattr(pg, 'var_quick_path', None)
        if quick_enable and quick_path and quick_enable.get() and quick_path.get().strip():
            path = quick_path.get().strip()
            os.makedirs(path, exist_ok=True)
            return path
        return _get_base_dir()

    def _generate_txt(self, title, author):
        """Generate a plain text file."""
        output_dir = self._get_output_dir()
        filename = f"{title}.txt"
        filepath = os.path.join(output_dir, filename)

        header = f"{title}\nAuthor: {author}\n" + "=" * 60 + "\n\n"
        setting = getattr(self, '_var_separate_txt', None)
        separate = getattr(self, '_active_separate_txt', None)
        if separate is None:
            separate = bool(setting and setting.get())

        def entries():
            if not separate:
                yield 0, title, header
            for i, ch_data in enumerate(self._chapter_results):
                if ExternalPipelineMixin._generation_stopped(self):
                    raise _OutputStopped()
                if ch_data is None or ch_data.get('_locked'):
                    continue
                chapter_number = ExternalPipelineMixin._result_chapter_number(
                    self, i, ch_data
                )
                heading = ExternalPipelineMixin._numbered_title(
                    self, chapter_number, ch_data.get('chapterName'))
                content = ch_data.get('contentText', '')
                if ExternalPipelineMixin._blank_paragraph_lines_enabled(self):
                    content = ExternalPipelineMixin._space_text_paragraphs(
                        content, ch_data.get('contentHtml', '')
                    )
                content = ((header if separate else "") + f"\n{'─' * 40}\n{heading}\n"
                           + f"{'─' * 40}\n\n{content}\n")
                yield chapter_number, heading, content

        try:
            location = write_text_output(filepath, entries(), separate)
            self._log(f"\u2705 Saved: {location}")
        except Exception as e:
            self._log(f"\u274c TXT generation failed: {e}")

    def _blank_paragraph_lines_enabled(self):
        setting = getattr(self, '_var_blank_paragraph_lines', None)
        return bool(setting and setting.get())

    @staticmethod
    def _space_html_paragraphs(content):
        """Add an explicit blank rendered line after each HTML paragraph."""
        return re.sub(r'</p\s*>', '</p><br/>', content or '',
                      flags=re.IGNORECASE)

    @staticmethod
    def _space_text_paragraphs(content, content_html=''):
        """Keep one empty text line between source paragraph lines."""
        if content_html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content_html, 'html.parser')
            paragraphs = soup.find_all('p')
            if paragraphs and (not content or
                               (content or '').count('\n') < len(paragraphs) - 1):
                for br in soup.find_all('br'):
                    br.replace_with('\n')
                for paragraph in paragraphs:
                    paragraph.insert_after('\n\n')
                content = soup.get_text().strip()
        return re.sub(r'(?<!\n)\n(?!\n)', '\n\n',
                      (content or '').replace('\r\n', '\n'))

    def _image_ext_from_bytes(self, raw_data, default='jpg'):
        """Infer a usable image extension from raw bytes."""
        if not raw_data:
            return default
        if raw_data[:4] == b'\x89PNG':
            return 'png'
        if raw_data[:4] == b'RIFF':
            return 'webp'
        if raw_data[:4] == b'GIF8':
            return 'gif'
        if raw_data[:12] == b'\x00\x00\x00\x18ftypavif':
            return 'avif'
        if raw_data[:3] == b'\xff\xd8\xff':
            return 'jpg'
        return default

    def _format_ext(self, fmt):
        ext = (fmt or '').lower()
        return 'jpg' if ext == 'jpeg' else (ext or 'jpg')

    def _generate_image_archive(self, title, author, *, image_entries=None, on_chapter_ready=None, preserve_panels=False):
        """Generate a CBZ image archive for webtoon-style image chapters."""
        output_dir = self._get_output_dir()
        filename = f"{title}.cbz"
        filepath = os.path.join(output_dir, filename)

        pg = self._parent_gui
        compress_images = getattr(pg, 'var_compress_images', None)
        compress_images = compress_images.get() if compress_images else False
        jpeg_quality = getattr(pg, 'var_jpeg_quality', None)
        jpeg_quality = jpeg_quality.get() if jpeg_quality else 80
        image_format = getattr(pg, 'var_image_format', None)
        image_format = image_format.get() if image_format else 'WEBP'

        compress_cover = getattr(pg, 'var_compress_cover', None)
        compress_cover = compress_cover.get() if compress_cover else False
        cover_quality = getattr(pg, 'var_cover_quality', None)
        cover_quality = cover_quality.get() if cover_quality else 90
        cover_format = getattr(pg, 'var_cover_format', None)
        cover_format = cover_format.get() if cover_format else 'JPEG'

        data = self._book_data or {}

        import base64
        import requests as _req
        import zipfile

        img_session = _req.Session()
        img_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36',
            'Referer': data.get('bookUrl', ''),
        })
        if data.get('_rbooks'):
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/',
                'Origin': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_kobo'):
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://www.kobo.com/',
                'Origin': 'https://www.kobo.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_mpia'):
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com/',
                'Origin': 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_global_npia'):
            img_session.headers.update({
                'Referer': (
                    data.get('bookUrl') or 'https://global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/'
                ),
                'Origin': 'https://global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        img_compress_type = zipfile.ZIP_STORED

        def fetch_image(img_url, label, img_data_url=None, log_failure=False,
                        request_cookies=None, candidates=None):
            raw = ExternalPipelineMixin._decode_image_data_url(img_data_url)
            if raw:
                return raw
            if data.get('_ntk_novel') and self._scraper and img_url:
                fetch_ntk_binary = getattr(self._scraper, 'fetch_ntk_binary', None)
                if fetch_ntk_binary:
                    raw = fetch_ntk_binary(img_url, data.get('bookUrl') or '')
                    if raw:
                        return raw
            if img_url and candidates:
                return ExternalPipelineMixin._download_image_mirrors(
                    self,
                    [img_url] + list(candidates), label, session=img_session,
                    request_cookies=request_cookies, log_failure=log_failure,
                )
            if img_url:
                return self._download_image_python(
                    img_url,
                    label,
                    session=img_session,
                    request_cookies=request_cookies,
                    log_success=False,
                    log_failure=log_failure,
                )
            return None

        try:
            self._log("  Saving CBZ image archive")
            written = 0
            with zipfile.ZipFile(filepath, "w") as zf:
                cover_url, cover_data = ExternalPipelineMixin._preferred_external_cover(
                    data, self._chapter_results
                )
                if cover_url:
                    cover_raw = fetch_image(
                        cover_url,
                        "Cover",
                        img_data_url=cover_data,
                        log_failure=True,
                    )
                    if cover_raw:
                        cover_ext = self._image_ext_from_bytes(cover_raw)
                        if compress_cover:
                            cover_raw = self._compress_image(
                                cover_raw, cover_quality, cover_format
                            )
                            cover_ext = self._format_ext(cover_format)
                        zf.writestr(
                            f"0000_cover.{cover_ext}",
                            cover_raw,
                            compress_type=img_compress_type,
                        )

                comic_info = f"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Title>{html.escape(data.get('bookname', title))}</Title>
  <Writer>{html.escape(data.get('author', author))}</Writer>
  <Summary>{html.escape(data.get('introduction', ''))}</Summary>
</ComicInfo>
"""
                zf.writestr(
                    "ComicInfo.xml",
                    comic_info,
                    compress_type=zipfile.ZIP_DEFLATED,
                )

                for i, ch_data in enumerate(self._chapter_results):
                    if ch_data is None or ch_data.get('_locked'):
                        continue
                    chapter_number = ExternalPipelineMixin._result_chapter_number(
                        self, i, ch_data
                    )
                    ch_name = ch_data.get(
                        'chapterName', f'Chapter {chapter_number}'
                    )
                    safe_ch = _sanitize_filename(ch_name)
                    images = ExternalPipelineMixin._chapter_images(ch_data)
                    if not images:
                        self._log(
                            f"  Skipping CBZ chapter {chapter_number}: "
                            f"{ch_name} (no images)"
                        )
                        continue
                    self._log(
                        f"  Building CBZ chapter {chapter_number}: {ch_name} "
                        f"({len(images)} image(s))"
                    )
                    chapter_start = time.time()
                    chapter_bytes = 0
                    failed_count = 0
                    prepared = []
                    for img_idx, img_info in enumerate(images, 1):
                        img_url = html.unescape(
                            img_info.get('url', '')
                        ).strip()
                        raw = fetch_image(
                            img_url,
                            f"Ch{chapter_number} image {img_idx}/{len(images)}",
                            img_data_url=img_info.get('data'),
                            log_failure=(img_idx <= 3),
                            request_cookies=(
                                img_info.get('_cookies')
                                or ch_data.get('_imageCookies')
                            ),
                            candidates=img_info.get('candidates'),
                        )
                        if not raw:
                            failed_count += 1
                            continue
                        panel = raw
                        ext = self._image_ext_from_bytes(raw)
                        if compress_images:
                            raw = self._compress_image(
                                raw, jpeg_quality, image_format
                            )
                            ext = self._format_ext(image_format)
                        archive_name = (
                            f"{chapter_number:04d}_{safe_ch}/"
                            f"{img_idx:04d}.{ext}"
                        )
                        zf.writestr(
                            archive_name,
                            raw,
                            compress_type=img_compress_type,
                        )
                        if image_entries is not None:
                            image_entries[(i, img_idx)] = archive_name
                        if on_chapter_ready is not None:
                            page = panel if preserve_panels else raw
                            prepared.append((img_idx, (self._image_ext_from_bytes(page), page)))
                        chapter_bytes += len(raw)
                        written += 1
                        if img_idx % 5 == 0 or img_idx == len(images):
                            self._log(
                                f"    Images ready: {img_idx}/"
                                f"{len(images)}"
                            )
                    if on_chapter_ready is not None and len(prepared) == len(images):
                        on_chapter_ready(i, ch_data, prepared)
                    elapsed = max(0.01, time.time() - chapter_start)
                    mb = chapter_bytes / (1024 * 1024)
                    ready_count = len(images) - failed_count
                    self._log(
                        f"    Images embedded: {ready_count}/"
                        f"{len(images)}, {mb:.1f} MB for this chapter "
                        f"in {elapsed:.1f}s"
                    )
                    if failed_count:
                        self._log(
                            f"    {failed_count} image(s) failed for "
                            "this CBZ chapter."
                        )
            if written:
                self._log(f"\u2705 Saved: {filepath}")
                return filepath
            else:
                self._log("\u274c CBZ generation failed: no images were downloaded.")
        except Exception as e:
            self._log(f"\u274c CBZ generation failed: {e}")
            self._log("Falling back to EPUB output...")
            self._generate_epub(title, author)
        finally:
            img_session.close()

    def _kpage_extra_css(self):
        """Collect original Kpage viewer CSS from downloaded chapters."""
        if not (self._book_data and self._book_data.get('_kpage')):
            return ''

        css_parts = ["""
body {
  background-color: #fff;
  color: #000;
}
.chapter > h1:first-child {
  display: none;
}
.kpage-source-heading {
  margin-top: 4vh !important;
  margin-bottom: 3vh !important;
  text-align: center !important;
}
.kpage-page-break {
  display: block;
  height: 0;
  line-height: 0;
  font-size: 0;
  margin: 0;
  padding: 0;
  page-break-after: always;
  break-after: page;
}
""".strip()]
        seen = set()
        for ch_data in self._chapter_results or []:
            if not ch_data or ch_data.get('_locked'):
                continue
            css = (ch_data.get('contentCss') or '').strip()
            if css and css not in seen:
                seen.add(css)
                css_parts.append(css)
        return '\n\n'.join(css_parts)

    def _chapter_extra_css(self):
        """Collect scraper-provided CSS from downloaded chapters."""
        css_parts = []
        seen = set()
        for ch_data in self._chapter_results or []:
            if not ch_data or ch_data.get('_locked'):
                continue
            css = (ch_data.get('contentCss') or '').strip()
            if css and css not in seen:
                seen.add(css)
                css_parts.append(css)
        return '\n\n'.join(css_parts)

    @staticmethod
    def _write_native_epub(filepath, payload):
        """Keep the publisher's chapter order, layout, cover, and TOC."""
        with zipfile.ZipFile(io.BytesIO(payload)) as source:
            if (source.namelist()[:1] != ['mimetype']
                    or source.read('mimetype') != b'application/epub+zip'
                    or source.testzip() is not None):
                raise ValueError('Rakuten Kobo Desktop EPUB is invalid.')
        output_dir = os.path.dirname(filepath)
        os.makedirs(output_dir, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode='wb', suffix='.epub', prefix='.kobo-',
                    dir=output_dir, delete=False) as target:
                temporary = target.name
                target.write(payload)
            os.replace(temporary, filepath)
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)

    def _generate_epub(self, title, author, *, image_entries=None, on_chapter_ready=None, preserve_panels=False):
        """Generate an EPUB file using the existing epub_generator."""
        native = [result['_nativeEpubBytes']
                  for result in self._chapter_results or []
                  if result and result.get('_nativeEpubBytes')]
        if native and (self._book_data or {}).get('_kobo_desktop'):
            if len(native) != 1:
                raise ValueError('Expected one Rakuten Kobo Desktop volume.')
            output_dir = self._get_output_dir()
            filepath = os.path.join(output_dir, f'{title}.epub')
            self._write_native_epub(filepath, native[0])
            self._log(f'✅ Saved: {filepath}')
            return
        try:
            from epub_generator import EpubGenerator
        except ImportError:
            self._log("❌ epub_generator.py not found. Falling back to TXT.")
            self._generate_txt(title, author)
            return

        output_dir = self._get_output_dir()
        filename = f"{title}.epub"
        filepath = os.path.join(output_dir, filename)

        # Read compression settings from parent GUI
        pg = self._parent_gui
        compress_images = getattr(pg, 'var_compress_images', None)
        compress_images = compress_images.get() if compress_images else False
        jpeg_quality = getattr(pg, 'var_jpeg_quality', None)
        jpeg_quality = jpeg_quality.get() if jpeg_quality else 80
        image_format = getattr(pg, 'var_image_format', None)
        image_format = image_format.get() if image_format else 'WEBP'

        # Cover-specific compression settings
        compress_cover = getattr(pg, 'var_compress_cover', None)
        compress_cover = compress_cover.get() if compress_cover else False
        cover_quality = getattr(pg, 'var_cover_quality', None)
        cover_quality = cover_quality.get() if cover_quality else 90
        cover_format = getattr(pg, 'var_cover_format', None)
        cover_format = cover_format.get() if cover_format else 'JPEG'

        if compress_images:
            self._log(
                f"  Image compression: ON ({image_format} q{jpeg_quality})"
            )
        else:
            self._log("  Image compression: OFF")
        if compress_cover:
            self._log(
                f"  Cover compression: ON ({cover_format} q{cover_quality})"
            )
        else:
            self._log("  Cover compression: OFF")

        data = self._book_data
        is_kpage = bool(data.get('_kpage'))

        # Default CSS for external novels
        css = """body { margin: 2%; }
p { overflow-wrap: break-word; }
h1, h2 { text-align: center; margin-bottom: 10%; margin-top: 10%; }
h3, h4, h5, h6 { text-align: center; margin-bottom: 15%; margin-top: 10%; }
img { display: block; max-width: 100%; max-height: 100%;
      margin-left: auto; margin-right: auto; margin-bottom: 2%; margin-top: 2%; }
"""
        chapter_css = self._chapter_extra_css()
        if chapter_css and not is_kpage:
            css = f"{css}\n\n/* Scraper-provided chapter CSS */\n{chapter_css}\n"
        kpage_css = self._kpage_extra_css()
        if kpage_css:
            css = f"{css}\n\n/* Kpage original viewer CSS */\n{kpage_css}\n"
        cover_url, cover_data = ExternalPipelineMixin._preferred_external_cover(
            data, self._chapter_results
        )

        metadata = {
            'title': data.get('bookname', title),
            'author': data.get('author', author),
            'description': data.get('introduction', ''),
            'tags': data.get('tags', []),
            'language': data.get('language', 'zh'),
            'source_url': data.get('bookUrl') or (self._url_var.get() if getattr(self, '_url_var', None) else ''),
        }

        import base64
        import requests as _req

        # Session for Python-side image downloads (reuse connections)
        img_session = _req.Session()
        img_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36',
            'Referer': data.get('bookUrl', ''),
        })
        if is_kpage:
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://page.\u006b\u0061\u006b\u0061\u006f.com/',
                'Origin': 'https://page.\u006b\u0061\u006b\u0061\u006f.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_rbooks'):
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/',
                'Origin': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_kobo'):
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://www.kobo.com/',
                'Origin': 'https://www.kobo.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_mpia'):
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com/',
                'Origin': 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        elif data.get('_global_npia'):
            img_session.headers.update({
                'Referer': (
                    data.get('bookUrl') or 'https://global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/'
                ),
                'Origin': 'https://global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        img_counter = [0]

        try:
            epub = EpubGenerator(metadata, filepath, css)

            # Download and add cover image via the correct API
            # (EpubGenerator looks for images named 'cover.*')
            cover_added = False
            if cover_url:
                cover_bytes = self._decode_image_data_url(cover_data)
                if cover_bytes:
                    self._log(
                        f"  📷 Cover: using startup cache "
                        f"({len(cover_bytes)} bytes)"
                    )
                if data.get('_ntk_novel') and self._scraper:
                    fetch_ntk_binary = getattr(
                        self._scraper, 'fetch_ntk_binary', None
                    )
                    if fetch_ntk_binary and not cover_bytes:
                        cover_bytes = fetch_ntk_binary(
                            cover_url,
                            data.get('bookUrl') or '',
                        )
                        if cover_bytes:
                            self._log(
                                f"  📷 Cover: OK ({len(cover_bytes)} bytes)"
                            )
                if not cover_bytes:
                    cover_bytes = self._download_image_python(
                        cover_url,
                        "Cover",
                        session=img_session,
                        log_failure=True,
                    )
                if cover_bytes:
                    ext = 'jpg'
                    if cover_bytes[:4] == b'\x89PNG':
                        ext = 'png'
                    elif cover_bytes[:4] == b'RIFF':
                        ext = 'webp'
                    elif cover_bytes[:4] == b'GIF8':
                        ext = 'gif'
                    elif cover_bytes[:12] == b'\x00\x00\x00\x18ftypavif':
                        ext = 'avif'
                    # Use cover-specific compression settings
                    if compress_cover:
                        cover_bytes = self._compress_image(
                            cover_bytes, cover_quality, cover_format
                        )
                        # Update ext to match the compressed format
                        ext = cover_format.lower()
                        if ext == 'jpeg':
                            ext = 'jpg'
                    epub.add_image(f'cover.{ext}', cover_bytes)
                    cover_added = True

            # Add introduction page if available (synopsis/description)
            intro_html = data.get('introductionHTML', '')
            status = data.get('status', '') if data.get('_npia') else ''
            status_html = (
                f'<p><strong>Status:</strong> {html.escape(status)}</p>'
                if status else ''
            )
            if intro_html or status:
                intro_page = f"""
<h2>{html.escape(data.get('bookname', title))}</h2>
<h3>{html.escape(data.get('author', author))}</h3>
{status_html}
<hr/>
{intro_html}
"""
                epub.add_extra_page('info.xhtml', intro_page)

            for i, ch_data in enumerate(self._chapter_results):
                if ExternalPipelineMixin._generation_stopped(self):
                    raise _OutputStopped()
                if ch_data is None or ch_data.get('_locked'):
                    continue
                chapter_number = ExternalPipelineMixin._result_chapter_number(
                    self, i, ch_data
                )
                ch_name = ch_data.get(
                    'chapterName', f'Chapter {chapter_number}'
                )
                is_notice = bool(ch_data.get('_is_notice'))
                content_html = ch_data.get('contentHtml', '')
                if ExternalPipelineMixin._blank_paragraph_lines_enabled(self):
                    content_html = ExternalPipelineMixin._space_html_paragraphs(
                        content_html
                    )
                img_total = len(ch_data.get('images') or [])
                if img_total:
                    self._log(
                        f"  Preparing EPUB chapter {chapter_number}: {ch_name} "
                        f"({img_total} image(s))"
                    )
                else:
                    self._log(
                        f"  Preparing EPUB chapter {chapter_number}: {ch_name}"
                    )

                # Process images: use browser-provided base64 data when
                # available, fall back to Python-side download otherwise.
                rename_map = {}  # original_name → compressed_name
                # A comic whose pages are <img> tags in its text (the generic engine's) takes this road too:
                # its pages fetched together, once, and handed to the Folder output as they arrive (it stitches
                # the chapter then, not after the EPUB is written). One at a time, inline, they came twice.
                chapter_images = ch_data.get('images') or (
                    ExternalPipelineMixin._chapter_images(ch_data) if ch_data.get('comic') else [])
                if chapter_images:
                    image_items = list(enumerate(chapter_images, 1))
                    image_workers = min(
                        32, max(1, self._var_ext_image_workers.get()),
                        len(image_items)
                    )
                    action_label = (
                        "Downloading/compressing"
                        if compress_images else "Downloading"
                    )
                    if len(image_items) > 1:
                        self._log(
                            f"    {action_label} {len(image_items)} "
                            f"image(s) with {image_workers} worker(s)..."
                        )
                    image_start = time.time()
                    image_bytes = 0
                    completed_image_bytes = 0

                    # ntk image hosts reset plain Python connections: fetch the
                    # chapter's images in Chrome first (on this thread, which
                    # drives Playwright); the workers download only the rest.
                    browser_images = {}
                    fetch_ntk_binaries = getattr(
                        self._scraper, 'fetch_ntk_binaries', None
                    ) if data.get('_ntk_novel') and self._scraper else None
                    if fetch_ntk_binaries:
                        browser_images = fetch_ntk_binaries([
                            html.unescape(info.get('url', '')).strip()
                            for _, info in image_items
                            # Mirror-hosted webtoon pages block Chrome's fetch (CORS).
                            if not info.get('data') and not info.get('candidates')
                        ], concurrency=image_workers)

                    # Reuse HTTP sessions across image tasks. Creating a new
                    # TLS connection for every Kpage image is expensive,
                    # especially when compression is disabled and downloading
                    # is the dominant cost.
                    session_pool = None
                    pooled_sessions = []
                    if image_workers > 1:
                        session_pool = queue.Queue()
                        for _ in range(image_workers):
                            s = _req.Session()
                            s.headers.update(img_session.headers)
                            s.cookies.update(img_session.cookies)
                            pooled_sessions.append(s)
                            session_pool.put(s)

                    def process_image(item):
                        img_idx, img_info = item
                        if ExternalPipelineMixin._generation_stopped(self):
                            return img_idx, None
                        img_url = html.unescape(img_info.get('url', '')).strip()
                        img_data_url = img_info.get('data')
                        raw = ExternalPipelineMixin._decode_image_data_url(
                            img_data_url
                        ) or browser_images.get(img_url)

                        # Fall back to Python-side download. Use one Session
                        # per worker. requests.Session should not be shared
                        # concurrently, but pooled per-worker reuse keeps
                        # connections warm.
                        if not raw and img_url:
                            dl_session = None
                            try:
                                if session_pool is not None:
                                    dl_session = session_pool.get()
                                else:
                                    dl_session = img_session
                                label = (
                                    f"Ch{chapter_number} image {img_idx}/"
                                    f"{len(image_items)}"
                                )
                                cookies = (
                                    img_info.get('_cookies')
                                    or ch_data.get('_imageCookies')
                                )
                                if img_info.get('candidates'):
                                    raw = ExternalPipelineMixin._download_image_mirrors(
                                        self,
                                        [img_url] + list(img_info['candidates']),
                                        label, session=dl_session,
                                        request_cookies=cookies,
                                        log_failure=(img_idx <= 3),
                                    )
                                else:
                                    raw = self._download_image_python(
                                        img_url, label, session=dl_session,
                                        request_cookies=cookies,
                                        log_success=False,
                                        log_failure=(img_idx <= 3),
                                    )
                            finally:
                                if session_pool is not None and dl_session:
                                    session_pool.put(dl_session)

                        if not raw:
                            return img_idx, None
                        panel = raw if preserve_panels else None

                        orig_name = img_info.get(
                            'name', f'img_{i}_{img_idx}.jpg'
                        )
                        safe_orig = re.sub(
                            r'[^A-Za-z0-9._-]+', '_', orig_name
                        ).strip('._') or f'img_{i}_{img_idx}.jpg'
                        base = safe_orig.rsplit('.', 1)[0]
                        ext = safe_orig.rsplit('.', 1)[1].lower() \
                            if '.' in safe_orig else 'jpg'
                        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif',
                                       'avif', 'bmp'):
                            # A disguised page file (.img): name it by its bytes.
                            ext = self._image_ext_from_bytes(raw)
                        img_name = (
                            f'ch{chapter_number:04d}_{img_idx:04d}_{base}.{ext}'
                        )

                        if compress_images:
                            raw = self._compress_image(
                                raw, jpeg_quality, image_format
                            )
                            new_ext = image_format.lower()
                            if new_ext == 'jpeg':
                                new_ext = 'jpg'
                            img_name = (
                                f'ch{chapter_number:04d}_{img_idx:04d}_'
                                f'{base}.{new_ext}'
                            )

                        return img_idx, {
                            'raw': raw,
                            'panel': panel,
                            'orig_name': orig_name,
                            'img_name': img_name,
                            'url': img_url,
                        }

                    image_results = {}
                    try:
                        if image_workers > 1:
                            with ThreadPoolExecutor(
                                max_workers=image_workers
                            ) as pool:
                                futures = {
                                    pool.submit(process_image, item): item[0]
                                    for item in image_items
                                }
                                done = 0
                                for fut in as_completed(futures):
                                    img_idx, result = fut.result()
                                    image_results[img_idx] = result
                                    done += 1
                                    if result:
                                        completed_image_bytes += len(
                                            result['raw']
                                        )
                                    if done % 5 == 0 or done == len(image_items):
                                        elapsed = max(
                                            0.01,
                                            time.time() - image_start,
                                        )
                                        speed = completed_image_bytes / elapsed
                                        self._log(
                                            f"    Images ready: {done}/"
                                            f"{len(image_items)} "
                                            f"({_format_size(completed_image_bytes)}, "
                                            f"{_format_size(int(speed))}/s)"
                                        )
                        else:
                            for item in image_items:
                                img_idx, result = process_image(item)
                                image_results[img_idx] = result
                                done = len(image_results)
                                if result:
                                    completed_image_bytes += len(result['raw'])
                                if done % 5 == 0 or done == len(image_items):
                                    elapsed = max(
                                        0.01,
                                        time.time() - image_start,
                                    )
                                    speed = completed_image_bytes / elapsed
                                    self._log(
                                        f"    Images ready: {done}/"
                                        f"{len(image_items)} "
                                        f"({_format_size(completed_image_bytes)}, "
                                        f"{_format_size(int(speed))}/s)"
                                    )
                    finally:
                        for s in pooled_sessions:
                            try:
                                s.close()
                            except Exception:
                                pass
                        session_pool = None

                    if ExternalPipelineMixin._generation_stopped(self):
                        raise _OutputStopped()
                    # Pages with mirrors were already tried on every host.
                    failed_items = [
                        item for item in image_items
                        if not image_results.get(item[0])
                        and not item[1].get('candidates')
                    ]
                    if failed_items and image_workers > 1:
                        self._log(
                            f"    Retrying {len(failed_items)} failed "
                            "image(s) sequentially..."
                        )
                        for item in failed_items:
                            img_idx, result = process_image(item)
                            image_results[img_idx] = result

                    for img_idx, result in sorted(image_results.items()):
                        if not result:
                            continue
                        img_counter[0] += 1
                        img_name = result['img_name']
                        image_bytes += len(result['raw'])
                        epub.add_image(img_name, result['raw'])
                        if image_entries is not None:
                            image_entries[(i, img_idx)] = img_name

                        orig_name = result['orig_name']
                        if img_name != orig_name:
                            rename_map[orig_name] = img_name

                        img_url = result['url']
                        if img_url and content_html:
                            content_html = content_html.replace(
                                img_url, f'../Images/{img_name}'
                            )
                            content_html = content_html.replace(
                                html.escape(img_url, quote=True),
                                f'../Images/{img_name}'
                            )
                    if (on_chapter_ready is not None and len(image_results) == len(image_items)
                            and all(image_results.values())
                            and (image_items or not ExternalPipelineMixin._chapter_images(ch_data))):
                        prepared = []
                        for position, result in sorted(image_results.items()):
                            page = result["panel"] or result["raw"]
                            prepared.append((position, (self._image_ext_from_bytes(page), page)))
                        on_chapter_ready(i, ch_data, prepared)
                    # A mirrored page every host failed would only stall the inline
                    # retry below one page at a time; leave it out of the chapter.
                    dropped = 0
                    for img_idx, img_info in image_items:
                        if image_results.get(img_idx) or not img_info.get('candidates'):
                            continue
                        img_url = html.unescape(img_info.get('url', '')).strip()
                        for form in {img_url, html.escape(img_url, quote=True)}:
                            content_html, count = re.subn(
                                r'<img\b[^>]*\bsrc="' + re.escape(form) + r'"[^>]*/?>',
                                '', content_html or '',
                            )
                            dropped += count
                    if dropped:
                        self._log(
                            f"    {dropped} page(s) could not be downloaded from "
                            "any mirror and were left out."
                        )
                    if image_items:
                        elapsed = max(0.01, time.time() - image_start)
                        mb = image_bytes / (1024 * 1024)
                        ready_count = sum(
                            1 for result in image_results.values() if result
                        )
                        failed_count = len(image_items) - ready_count - dropped
                        self._log(
                            f"    Chapter images ready: {ready_count}/"
                            f"{len(image_items)}, "
                            f"{mb:.1f} MB for this chapter in {elapsed:.1f}s "
                            f"({_format_size(int(image_bytes / elapsed))}/s)"
                        )
                        if failed_count:
                            self._log(
                                f"    {failed_count} image(s) were left for "
                                "inline fallback retry."
                            )

                # Also handle any <img src="..."> in contentHtml that
                # weren't in the images array (e.g. inline images)
                content_html = self._download_inline_images(
                    content_html, epub, img_session, img_counter,
                    compress_images, jpeg_quality, image_format,
                    chapter_number,
                    request_cookies=ch_data.get('_imageCookies'),
                )

                # Fix novel-downloader's img format for EPUB rendering:
                # cleanDOM outputs <img data-src-address="name.jpeg" alt="..."
                # title="..."> with NO src attribute.  EPUB readers need src.
                content_html = self._fix_nd_img_tags(
                    content_html, rename_map
                )

                toc_sections = ch_data.get('tocSections') or []
                epub.add_chapter(
                    ch_name if is_notice else ExternalPipelineMixin._numbered_title(
                        self, chapter_number, ch_data.get('chapterName')),
                    content_html, show_title=not is_kpage and not toc_sections,
                    is_notice=is_notice,
                    chapter_number=None if is_notice else chapter_number,
                    toc_sections=toc_sections,
                )

            duplicates = {}
            if is_kpage and self._var_kpage_dedupe_images.get():
                max_positions = max(
                    1, min(10, self._var_kpage_dedupe_leading_images.get())
                )
                duplicates = self._remove_kpage_duplicate_cover_pages(
                    epub, max_positions=max_positions
                ) or {}

            # Fallback: if no cover was found, use the first chapter
            # image larger than 400px as the cover.
            if not cover_added and epub.images:
                self._log("No cover URL found, scanning chapter images...")
                cover_added = self._try_cover_from_images(
                    epub, compress_images, jpeg_quality, image_format
                )

            self._log("Writing EPUB to disk…")
            epub.generate()
            self._log(f"✅ Saved: {filepath}")
            if image_entries is not None:
                for key, name in image_entries.items():
                    image_entries[key] = f"OEBPS/Images/{duplicates.get(name, name)}"
            return filepath

        except Exception as e:
            self._log(f"❌ EPUB generation failed: {e}")
            self._log("Falling back to TXT output...")
            self._generate_txt(title, author)
        finally:
            img_session.close()

    def _copy_browser_cookies_to_session(self, session):
        """Copy Playwright browser cookies into a requests session."""
        scraper = getattr(self, '_scraper', None)
        context = getattr(scraper, '_context', None)
        if not context:
            return

        try:
            cookies = context.cookies()
        except Exception as e:
            self._log(f"  Could not read browser cookies for images: {e}")
            return

        copied = 0
        for cookie in cookies or []:
            name = cookie.get('name')
            value = cookie.get('value')
            domain = cookie.get('domain')
            if not name or value is None:
                continue
            try:
                session.cookies.set(
                    name,
                    value,
                    domain=domain,
                    path=cookie.get('path') or '/',
                )
                copied += 1
            except Exception:
                pass

        if copied:
            self._log(f"  Using {copied} browser cookie(s) for image downloads.")

    def _remove_kpage_duplicate_cover_pages(self, epub, max_positions=2):
        """Remove repeated Kpage boilerplate images by exact bytes.

        This does not download or probe anything. It only looks at the
        configured number of leading images from Kpage image chapters, then
        removes later occurrences whose bytes are identical while keeping the
        first copy. This avoids extension-based false positives.
        """
        image_data = {
            img.get('filename'): img.get('data')
            for img in epub.images
            if img.get('filename') and not img.get('filename', '').startswith('cover.')
        }
        if not image_data:
            return

        occurrences_by_bytes = {}
        for chap_idx, chap in enumerate(epub.chapters):
            content = chap.get('content') or ''
            if 'kpage-image-chapter' not in content:
                continue
            matches = re.findall(
                r'<img\b[^>]*\bsrc=["\']\.\./Images/([^"\']+)["\']',
                content,
                flags=re.IGNORECASE,
            )
            for pos, src in enumerate(matches[:max_positions], 1):
                filename = html.unescape(src)
                data = image_data.get(filename)
                if data:
                    occurrences_by_bytes.setdefault(data, []).append({
                        'chapter_index': chap_idx,
                        'position': pos,
                        'filename': filename,
                    })

        duplicate_names = {}
        for occurrences in occurrences_by_bytes.values():
            chapter_count = len({item['chapter_index'] for item in occurrences})
            if chapter_count < 2:
                continue
            # Keep the first copy in reading order and remove later repeats.
            for item in occurrences[1:]:
                duplicate_names[item['filename']] = occurrences[0]['filename']

        if not duplicate_names:
            return

        for chap in epub.chapters:
            content = chap.get('content') or ''
            for name in duplicate_names:
                content = self._remove_image_page_by_filename(content, name)
            chap['content'] = content

        epub.images = [
            img for img in epub.images
            if img.get('filename') not in duplicate_names
        ]
        self._log(
            f"  Removed {len(duplicate_names)} duplicate Kpage "
            "boilerplate image occurrence(s), keeping the first copy."
        )
        return duplicate_names

    def _remove_image_page_by_filename(self, html_str, filename):
        """Remove a Kpage image-page wrapper, falling back to the img tag."""
        if not html_str or not filename:
            return html_str

        sources = {
            f'../Images/{filename}',
            f'../Images/{html.escape(filename, quote=True)}',
        }
        for src in sources:
            pattern = (
                r'\s*<div class="kpage-image-page">\s*'
                r'<img\b[^>]*\bsrc=["\']' + re.escape(src) +
                r'["\'][^>]*/?>\s*</div>'
            )
            html_str = re.sub(pattern, '', html_str, flags=re.IGNORECASE)
            img_pattern = (
                r'\s*<img\b[^>]*\bsrc=["\']' + re.escape(src) +
                r'["\'][^>]*/?>'
            )
            html_str = re.sub(img_pattern, '', html_str, flags=re.IGNORECASE)
        return html_str

    @staticmethod
    def _preferred_external_cover(data, chapter_results):
        cover_url = data.get('coverUrl', '')
        cover_data = data.get('_coverData')
        if data.get('_rbooks') or data.get('_kobo'):
            for result in chapter_results or []:
                if isinstance(result, dict) and result.get('coverUrl'):
                    return result['coverUrl'], (
                        result.get('_coverData') or cover_data
                    )
        return cover_url, cover_data

    @staticmethod
    def _decode_image_data_url(data_url):
        """Decode base64 or percent-encoded inline images, or read a `file:` page the
        scraper saved (ntk webtoon pages; None once that file is gone)."""
        if data_url and data_url.startswith('file:'):
            try:
                with open(data_url[len('file:'):], 'rb') as handle:
                    return handle.read() or None
            except OSError:
                return None
        if not data_url or ',' not in data_url:
            return None
        try:
            import base64
            import urllib.parse
            header, payload = data_url.split(',', 1)
            if ';base64' in header.lower():
                return base64.b64decode(payload)
            return urllib.parse.unquote_to_bytes(payload)
        except Exception:
            return None

    def _download_image_python(self, url, label="Image", session=None,
                               max_retries=3, log_success=True,
                               log_failure=False, request_cookies=None,
                               timeout=None, use_prefetch=True):
        """Download an image using requests (no CORS/mixed-content issues).

        Automatically upgrades http:// to https:// and retries on failure.
        Returns raw bytes or None.
        """
        import requests as _req

        url = html.unescape((url or '').strip())
        lookahead = getattr(self, "_kpage_images", None)
        if use_prefetch and lookahead:
            cached = lookahead.read(url)
            if cached:
                return cached

        # Upgrade http to https (many sites serve images on http but
        # support https — and some block http entirely)
        if url.startswith('http://'):
            url = url.replace('http://', 'https://', 1)

        sess = session or _req
        last_status = None
        last_length = None
        last_type = ''
        last_error = None
        for attempt in range(max_retries):
            try:
                request_options = {'timeout': timeout or request_timeout(15)}
                if (self._book_data or {}).get('_comix'):
                    request_options['headers'] = {'Referer': None}
                if request_cookies:
                    effective_cookies = dict(request_cookies)
                    cloudfront_names = {
                        'CloudFront-Policy',
                        'CloudFront-Key-Pair-Id',
                        'CloudFront-Signature',
                    }
                    if cloudfront_names.intersection(effective_cookies):
                        import urllib.parse
                        image_host = (
                            urllib.parse.urlparse(url).hostname or ''
                        ).lower()
                        if image_host != 'pv-gn.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com':
                            effective_cookies = {
                                key: value
                                for key, value in effective_cookies.items()
                                if key not in cloudfront_names
                            }
                    if effective_cookies:
                        request_options['cookies'] = effective_cookies
                r = sess.get(url, **request_options)
                last_status = r.status_code
                last_length = len(r.content)
                last_type = r.headers.get('content-type', '')
                if r.status_code == 200 and len(r.content) > 100:
                    if log_success:
                        self._log(
                            f"  📷 {label}: OK ({len(r.content)} bytes)"
                        )
                    return r.content
                # Try original http:// URL as fallback on last attempt
                if attempt == max_retries - 2 and url.startswith('https://'):
                    url = url.replace('https://', 'http://', 1)
            except Exception as e:
                last_error = e
                if attempt == max_retries - 1 and (log_success or log_failure):
                    self._log(f"  📷 {label}: FAILED ({e})")
        if log_failure and last_error is None:
            self._log(
                f"  Image {label}: FAILED "
                f"(HTTP {last_status}, {last_length or 0} bytes, "
                f"{last_type or 'unknown content-type'})"
            )
        return None

    def _download_image_mirrors(self, urls, label, session=None,
                                request_cookies=None, log_failure=False):
        """Download an image that several hosts serve (ntk webtoon pages).

        One mirror can reset every connection while another serves the same
        file, so try each host once instead of retrying one. Hosts that failed
        before go last, so a dead or hanging mirror costs one short
        timeout per run rather than one per page. Chrome already tried these
        pages, so a page every host fails is given up on quickly.
        """
        import urllib.parse

        failures = self.__dict__.setdefault('_mirror_failures', {})

        def host(url):
            return (urllib.parse.urlparse(url).hostname or '').lower()

        urls = sorted((url for url in urls if url),
                      key=lambda url: failures.get(host(url), 0))
        for index, url in enumerate(urls):
            raw = self._download_image_python(
                url, label, session=session, max_retries=1,
                log_success=False,
                log_failure=log_failure and index == len(urls) - 1,
                request_cookies=request_cookies, timeout=20,
            )
            if raw:
                return raw
            failures[host(url)] = failures.get(host(url), 0) + 1
        return None

    def _download_inline_images(self, html_str, epub, session, counter,
                                compress, quality, fmt, chapter_number,
                                request_cookies=None):
        """Find <img src="http..."> in HTML, download, and replace with
        local EPUB paths.  Skips images already pointing to ../Images/."""
        import re
        if not html_str:
            return html_str

        img_pat = re.compile(r'<img[^>]+src="(https?://[^"]+)"[^>]*/?>',
                             re.IGNORECASE)
        matches = img_pat.findall(html_str)
        if not matches:
            return html_str

        for url in matches:
            if '../Images/' in url:
                continue  # Already replaced
            raw = self._download_image_python(
                url,
                f"Ch{chapter_number} inline img",
                session=session,
                request_cookies=request_cookies,
            )
            if raw:
                counter[0] += 1
                ext = 'jpg'  # Default
                if raw[:4] == b'\x89PNG':
                    ext = 'png'
                elif raw[:4] == b'RIFF':
                    ext = 'webp'
                elif raw[:3] == b'GIF':
                    ext = 'gif'
                name = f'img_{chapter_number}_{counter[0]}.{ext}'
                if compress:
                    raw = self._compress_image(raw, quality, fmt)
                epub.add_image(name, raw)
                html_str = html_str.replace(url, f'../Images/{name}')

        return html_str

    def _fix_nd_img_tags(self, html_str, rename_map=None):
        """Fix novel-downloader's img tags for EPUB rendering.

        The novel-downloader's cleanDOM outputs images as:
          <img data-src-address="name.jpeg" alt="../Images/name.jpeg"
               title="../Images/name.jpeg">
        EPUB readers need a proper src attribute:
          <img src="../Images/name.jpeg" alt="name.jpeg" />

        If rename_map is provided (e.g. {'name.jpeg': 'name.webp'}),
        filenames will be updated to match compressed output.
        """
        if not html_str:
            return html_str

        rmap = rename_map or {}

        # Convert <img data-src-address="X" ...> to <img src="../Images/X" />
        def fix_img(m):
            tag = m.group(0)
            # Extract data-src-address value
            ds = re.search(r'data-src-address="([^"]+)"', tag)
            if ds:
                filename = ds.group(1)
                # Apply rename if compression changed the extension
                filename = rmap.get(filename, filename)
                src = f'../Images/{filename}'
                # Build a clean self-closing img tag
                return f'<img src="{src}" alt="{filename}" />'
            # If no data-src-address, check if src is missing but alt has path
            alt = re.search(r'alt="(\.\./Images/[^"]+)"', tag)
            if alt and 'src=' not in tag:
                return f'<img src="{alt.group(1)}" alt="" />'
            # Already has src — just ensure self-closing for XHTML
            if not tag.rstrip().endswith('/>'):
                tag = tag.rstrip().rstrip('>') + ' />'
            return tag

        html_str = re.sub(r'<img\b[^>]*>', fix_img, html_str,
                          flags=re.IGNORECASE)

        # Also fix bare <br> tags → <br/> for valid XHTML
        html_str = re.sub(r'<br\s*(?!/)>', '<br/>', html_str,
                          flags=re.IGNORECASE)

        return html_str

    def _try_cover_from_images(self, epub, compress, quality, fmt):
        """Scan existing EPUB images for one wider/taller than 400px.

        If found, add a copy named 'cover.ext' so EpubGenerator renders
        it on the cover page.  Returns True if a cover was added.
        """
        try:
            from PIL import Image as PILImage
            import io
        except ImportError:
            return False

        for img_entry in epub.images:
            if img_entry['filename'].startswith('cover.'):
                return True  # Already have one
            try:
                im = PILImage.open(io.BytesIO(img_entry['data']))
                w, h = im.size
                if w >= 400 or h >= 400:
                    ext = img_entry['filename'].rsplit('.', 1)[-1]
                    raw = img_entry['data']
                    if compress:
                        raw = self._compress_image(raw, quality, fmt)
                    epub.add_image(f'cover.{ext}', raw)
                    self._log(
                        f"  📷 Cover fallback: using {img_entry['filename']}"
                        f" ({w}x{h}px)"
                    )
                    return True
            except Exception:
                continue
        return False

    def _compress_image(self, raw_data, quality, fmt):
        """Compress an image using PIL, reusing parent GUI's settings."""
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw_data))
            buf = io.BytesIO()
            if fmt.upper() == 'WEBP':
                img.save(buf, 'WEBP', quality=quality)
            elif fmt.upper() == 'JPEG':
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                img.save(buf, 'JPEG', quality=quality)
            elif fmt.upper() == 'AVIF':
                img.save(buf, 'AVIF', quality=quality)
            else:
                img.save(buf, 'PNG')
            return buf.getvalue()
        except Exception:
            return raw_data  # Return original on failure

    def _generate_pdf(self, title, author):
        """Generate a PDF file using downloader_core's generate_pdf."""
        try:
            from downloader_core import DownloaderCore
        except ImportError:
            self._log("\u274c downloader_core.py not found. Falling back to TXT.")
            self._generate_txt(title, author)
            return

        output_dir = self._get_output_dir()
        filename = f"{title}.pdf"
        filepath = os.path.join(output_dir, filename)

        # Default CSS
        css = """body { margin: 2%; }
p { overflow-wrap: break-word; }
h1, h2 { text-align: center; margin-bottom: 10%; margin-top: 10%; }
h3, h4, h5, h6 { text-align: center; margin-bottom: 15%; margin-top: 10%; }
img { display: block; max-width: 100%; max-height: 100%;
      margin-left: auto; margin-right: auto; margin-bottom: 2%; margin-top: 2%; }
"""
        kpage_css = self._kpage_extra_css()
        if kpage_css:
            css = f"{css}\n\n/* Kpage original viewer CSS */\n{kpage_css}\n"

        data = self._book_data
        metadata = {
            'title': data.get('bookname', title),
            'author': data.get('author', author),
        }

        import base64
        import requests as _req

        image_session = _req.Session()
        image_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36',
            'Referer': data.get('bookUrl', ''),
        })
        if data.get('_kpage'):
            image_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://page.\u006b\u0061\u006b\u0061\u006f.com/',
                'Origin': 'https://page.\u006b\u0061\u006b\u0061\u006f.com',
            })
            self._copy_browser_cookies_to_session(image_session)
        elif data.get('_rbooks'):
            image_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/',
                'Origin': 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com',
            })
            self._copy_browser_cookies_to_session(image_session)
        elif data.get('_kobo'):
            image_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://www.kobo.com/',
                'Origin': 'https://www.kobo.com',
            })
            self._copy_browser_cookies_to_session(image_session)
        elif data.get('_mpia'):
            image_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com/',
                'Origin': 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com',
            })
            self._copy_browser_cookies_to_session(image_session)
        elif data.get('_global_npia'):
            image_session.headers.update({
                'Referer': (
                    data.get('bookUrl') or 'https://global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/'
                ),
                'Origin': 'https://global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com',
            })
            self._copy_browser_cookies_to_session(image_session)

        def fetch_pdf_asset(url, label, data_url=None, referer=None,
                            request_cookies=None):
            raw = ExternalPipelineMixin._decode_image_data_url(data_url)
            if raw:
                return raw
            if data.get('_ntk_novel') and self._scraper and url:
                fetch_ntk_binary = getattr(
                    self._scraper, 'fetch_ntk_binary', None
                )
                if fetch_ntk_binary:
                    raw = fetch_ntk_binary(
                        url,
                        referer or data.get('bookUrl') or '',
                    )
                    if raw:
                        return raw
            if url:
                return self._download_image_python(
                    url,
                    label,
                    session=image_session,
                    request_cookies=request_cookies,
                    log_success=False,
                    log_failure=True,
                )
            return None

        cover_image = None
        cover_url, cover_data = ExternalPipelineMixin._preferred_external_cover(
            data, self._chapter_results
        )
        if cover_url:
            cover_bytes = fetch_pdf_asset(
                cover_url,
                'PDF cover',
                data_url=cover_data,
                referer=data.get('bookUrl') or '',
            )
            if cover_bytes:
                cover_ext = self._image_ext_from_bytes(cover_bytes)
                cover_image = {
                    'filename': f'cover.{cover_ext}',
                    'data': cover_bytes,
                }
                self._log(f"  📷 PDF cover: OK ({len(cover_bytes)} bytes)")

        # Build chapter list and download every referenced image. The PDF
        # engine only embeds bytes present in image_map; unlike EPUB it cannot
        # resolve the scraper's ../Images paths on its own.
        chapters_for_pdf = []
        image_map = {}
        for i, ch_data in enumerate(self._chapter_results):
            if ch_data is None or ch_data.get('_locked'):
                continue
            chapter_number = ExternalPipelineMixin._result_chapter_number(
                self, i, ch_data
            )
            ch_name = ch_data.get(
                'chapterName', f'Chapter {chapter_number}'
            )
            content_html = ch_data.get('contentHtml', '')
            if ExternalPipelineMixin._blank_paragraph_lines_enabled(self):
                content_html = ExternalPipelineMixin._space_html_paragraphs(
                    content_html
                )
            rename_map = {}

            for img_index, img_info in enumerate(ch_data.get('images') or [], 1):
                img_url = html.unescape(img_info.get('url', '')).strip()
                raw = fetch_pdf_asset(
                    img_url,
                    f'PDF chapter {chapter_number} image {img_index}',
                    data_url=img_info.get('data'),
                    referer=(
                        ch_data.get('chapterUrl')
                        or data.get('bookUrl')
                        or ''
                    ),
                    request_cookies=(
                        img_info.get('_cookies')
                        or ch_data.get('_imageCookies')
                    ),
                )
                if not raw:
                    continue
                original_name = img_info.get('name') or (
                    f'img_{chapter_number}_{img_index}.'
                    f'{self._image_ext_from_bytes(raw)}'
                )
                img_name = os.path.basename(
                    original_name.replace('\\', '/')
                ) or f'img_{chapter_number}_{img_index}.jpg'
                if img_name in image_map and image_map[img_name] != raw:
                    img_name = (
                        f'ch{chapter_number:04d}_{img_index:04d}_{img_name}'
                    )
                image_map[img_name] = raw
                rename_map[original_name] = img_name
                if img_url:
                    content_html = content_html.replace(
                        img_url, f'../Images/{img_name}'
                    )
                    content_html = content_html.replace(
                        html.escape(img_url, quote=True),
                        f'../Images/{img_name}',
                    )

            # Include remote image tags not represented in the images array.
            inline_urls = re.findall(
                r'<img\b[^>]*\bsrc=["\'](https?://[^"\']+)["\']',
                content_html,
                flags=re.IGNORECASE,
            )
            for inline_index, inline_url in enumerate(inline_urls, 1):
                raw = fetch_pdf_asset(
                    html.unescape(inline_url),
                    f'PDF chapter {chapter_number} inline image {inline_index}',
                    referer=(
                        ch_data.get('chapterUrl')
                        or data.get('bookUrl')
                        or ''
                    ),
                    request_cookies=ch_data.get('_imageCookies'),
                )
                if not raw:
                    continue
                ext = self._image_ext_from_bytes(raw)
                img_name = (
                    f'pdf_ch{chapter_number:04d}_{inline_index:04d}.{ext}'
                )
                image_map[img_name] = raw
                content_html = content_html.replace(
                    inline_url, f'../Images/{img_name}'
                )

            content_html = self._fix_nd_img_tags(content_html, rename_map)

            chapters_for_pdf.append({
                "title": (ch_name if ch_data.get('_is_notice')
                          else ExternalPipelineMixin._numbered_title(
                              self, chapter_number, ch_data.get('chapterName'))),
                "html": content_html,
                "is_notice": bool(ch_data.get('_is_notice')),
            })

        # Read PDF settings from parent GUI
        pg = self._parent_gui
        show_toc = getattr(pg, 'var_pdf_toc', None)
        show_toc = show_toc.get() if show_toc else False
        show_pages = getattr(pg, 'var_pdf_page_numbers', None)
        show_pages = show_pages.get() if show_pages else False
        counter_layout = getattr(pg, 'var_pdf_counter_layout', None)
        counter_layout = counter_layout.get() if counter_layout else False

        try:
            downloader = DownloaderCore(None, self._log)
            downloader.generate_pdf(
                metadata,
                filepath,
                chapters_for_pdf,
                css,
                image_map=image_map,
                cover_image=cover_image,
                info_html=data.get('introductionHTML') or None,
                show_toc=show_toc,
                show_page_numbers=show_pages,
                use_counter_layout=counter_layout,
            )
            self._log(f"\u2705 Saved: {filepath}")
        except Exception as e:
            self._log(f"\u274c PDF generation failed: {e}")
            self._log("Falling back to TXT output...")
            self._generate_txt(title, author)
        finally:
            image_session.close()
