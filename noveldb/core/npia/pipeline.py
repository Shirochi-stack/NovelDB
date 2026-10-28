"""The native npia download pipeline (moved verbatim from gui.py's NpiaGUI).

`NpiaPipelineMixin` holds the download, batch, cache and output logic. It reads its options
through `var_*` objects with `.get()`/`.set()` (Tk variables in the old window, plain values in
queue jobs) and reports through the `_ui_*` hooks below, so the same code runs under Tk, Qt, or
headless. The hooks' defaults here are headless; interfaces override them.
"""

import base64
import html
import io
import json
import logging
import os
import random
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from downloader_core import (
    AccessBlockedError,
    DownloaderCore,
    InvalidChapterPayloadError,
    validate_chapter_payload,
)
from epub_generator import EpubGenerator
from noveldb.core.images import (
    Image,
    _detect_source_ext,
    _download_image_with_progress,
    _DownloadStats,
    _encode_image_bytes,
    _format_size,
)
from noveldb.core.npia.ids import extract_novel_id
from noveldb.core.text import extract_chapter_content_and_images
from text_output import write_text_output

_logger = logging.getLogger('ND')


def _writable_data_dir(kind):
    """`logs` or `.cache` folder (see noveldb.core.paths); None if it cannot be created."""
    try:
        from noveldb.core import paths
        return str(paths.logs_dir() if kind == 'logs' else paths.cache_dir())
    except Exception:
        return None


class NpiaPipelineMixin:
    """npia downloads: one novel, batches, caching and every output format."""

    # -- hooks (headless defaults; the Tk window and Qt jobs override them) -------------

    def _ui_status(self, text):
        """Short status text ("Downloading...", "Idle")."""

    def _ui_batch(self, index, total):
        """Batch position; (0, 0) clears it."""

    def _ui_images(self, count):
        """Images downloaded so far in this novel."""

    def _ui_warn(self, title, message, **kwargs):
        self.log_message(f"{title}: {message}")

    def _ui_confirm(self, title, message, **kwargs):
        """Yes/no question; headless answers no."""
        return False

    def _ui_ask_save_path(self, **kwargs):
        """Where to save (filedialog.asksaveasfilename keywords); headless: initialdir/initialfile."""
        initial = kwargs.get("initialfile") or ""
        folder = kwargs.get("initialdir") or ""
        return os.path.join(folder, initial) if initial else ""

    def _persist_settings(self):
        """Save the interface's settings before a download starts (the old window wrote config.json)."""

    def _session_log_path(self):
        return ""

    def _update_progress(self, value=None, total=None, status_text=None):
        if value is not None:
            self.progress_value = value
        if total is not None:
            self.progress_total = total
        if status_text is not None:
            self._ui_status(status_text)

    def _reset_progress(self):
        self.progress_value = 0
        self.progress_total = 0
        self._progress_start_time = None
        self._img_downloaded_count = 0

    # -- moved from gui.py -------------------------------------------------------------

    def _schedule_shutdown_after_success(self, result):
        """Schedule a Windows shutdown only after an entirely clean run."""
        if not self.var_shutdown_after_success.get():
            return
        if not result or result.get("status") != "ok":
            self.log_message("Shutdown skipped because the download did not finish successfully.")
            return
        if self._batch_result_has_warnings(result):
            self.log_message("Shutdown skipped because the download finished with warnings.")
            return
        if os.name != "nt":
            self.log_message("Shutdown after download is currently supported on Windows only.")
            return
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["shutdown.exe", "/s", "/t", "60"],
                creationflags=creationflags,
            )
            self.log_message(
                "Successful download complete. Windows shutdown scheduled in 60 seconds. "
                "Run 'shutdown.exe /a' to cancel."
            )
        except Exception as e:
            self.log_message(f"Could not schedule Windows shutdown: {e}")

    @staticmethod
    def _parse_batch_line(line):
        """Parse a batch list line into (novel_id, title_or_none).

        Supports:
          Title,ID    -> ('ID', 'Title')
          Title,URL   -> ('ID', 'Title')
          ID          -> ('ID', None)
          URL         -> ('ID', None)
        Returns (None, None) if the line is unusable.
        """
        if "," in line:
            parts = line.split(",", 1)
            title = parts[0].strip()
            novel_id = extract_novel_id(parts[1].strip())
            return (novel_id, title) if novel_id else (None, None)
        # No comma \u2014 resolve the whole line (accepts URL or numeric ID).
        novel_id = extract_novel_id(line.strip())
        return (novel_id, None) if novel_id else (None, None)

    @staticmethod
    def _load_batch_lines(batch_source):
        """Return a list of non-empty, stripped lines from a file path or list.

        Files are opened with a sequence of encoding fallbacks so non-UTF-8
        lists (e.g. cp1252 exports) parse cleanly.
        """
        if isinstance(batch_source, (list, tuple)):
            return [str(ln).strip() for ln in batch_source if str(ln).strip()]

        if isinstance(batch_source, str):
            if not os.path.exists(batch_source):
                raise FileNotFoundError(f"List file not found: {batch_source}")
            for enc in ("utf-8-sig", "utf-8", "cp1252"):
                try:
                    with open(batch_source, "r", encoding=enc) as f:
                        return [ln.strip() for ln in f if ln.strip()]
                except UnicodeDecodeError:
                    continue
            with open(batch_source, "r", encoding="utf-8", errors="replace") as f:
                return [ln.strip() for ln in f if ln.strip()]

        raise ValueError("Unsupported batch source.")

    @staticmethod
    def _batch_result_has_warnings(result):
        return bool(
            result.get("image_failures")
            or result.get("failed_chapters")
            or result.get("blocked_chapters")
        )

    @staticmethod
    def _batch_result_warning_summary(result):
        parts = []
        image_failures = int(result.get("image_failures") or 0)
        failed_chapters = result.get("failed_chapters") or []
        blocked_chapters = result.get("blocked_chapters") or []
        if image_failures:
            parts.append(f"{image_failures} image failure(s)")
        if failed_chapters:
            parts.append(f"{len(failed_chapters)} chapter failure(s)")
        if blocked_chapters:
            parts.append(f"{len(blocked_chapters)} blocked chapter(s)")
        return ", ".join(parts) if parts else "no warnings"

    def _batch_download_worker(self, batch_source, output_dir, source_label):
        self._ui_status("Batch downloading...")
        self.log_message(f"Batch download started: {source_label}")
        self.log_message("Batch mode: novels are processed strictly one at a time.")

        prev_quick_enable = self.var_quick_enable.get()
        prev_quick_path = self.var_quick_path.get()
        prev_novel_id = self.var_novel_id.get()
        succeeded, failed, skipped = 0, 0, 0
        failed_items = []
        warning_items = []
        batch_result = None

        try:
            os.makedirs(output_dir, exist_ok=True)
            try:
                lines = self._load_batch_lines(batch_source)
            except FileNotFoundError as e:
                self.log_message(str(e))
                return

            total = len(lines)
            if total == 0:
                self.log_message("Batch list is empty.")
                return

            self.var_quick_enable.set(True)
            self.var_quick_path.set(output_dir)

            for idx, line in enumerate(lines, start=1):
                if self._stop_requested:
                    self.log_message(f"Batch stopped by user after {succeeded} novel(s).")
                    break

                if line.startswith('#'):
                    self.log_message(f"[{idx}/{total}] Skipped comment")
                    skipped += 1
                    continue

                novel_id, title = self._parse_batch_line(line)
                if not novel_id:
                    self.log_message(f"[{idx}/{total}] Skipped (invalid ID/URL): {line}")
                    skipped += 1
                    continue

                try:
                    if not title:
                        self.log_message(f"[{idx}/{total}] Fetching title for novel {novel_id}...")
                        meta = self.downloader.fetch_metadata(novel_id)
                        title = meta.get("title", novel_id) if meta else novel_id

                    self.log_message(f"[{idx}/{total}] Starting novel: {title} ({novel_id})")
                    self._ui_batch(idx, total)
                    self.var_novel_id.set(novel_id)
                    result = self._download_worker() or self._last_download_result or {}
                    result.setdefault("novel_id", novel_id)
                    result.setdefault("title", title)
                    result_title = result.get("title") or title or novel_id
                    status = result.get("status") or "ok"
                    if status == "failed":
                        failed += 1
                        failed_items.append(result)
                        err = result.get("error") or "download failed"
                        self.log_message(f"[{idx}/{total}] FAILED novel {novel_id}: {err}")
                    else:
                        succeeded += 1
                        if self._batch_result_has_warnings(result):
                            warning_items.append(result)
                            warning_text = self._batch_result_warning_summary(result)
                            self.log_message(
                                f"[{idx}/{total}] Finished novel with warnings: "
                                f"{result_title} ({novel_id}) - {warning_text}"
                            )
                        else:
                            self.log_message(f"[{idx}/{total}] Finished novel: {result_title} ({novel_id})")
                    if idx < total and not self._stop_requested:
                        self.log_message(f"[{idx + 1}/{total}] Waiting to start the next novel...")
                except Exception as e:
                    failed += 1
                    failed_items.append({
                        "novel_id": novel_id,
                        "title": title or novel_id,
                        "error": str(e),
                    })
                    self.log_message(f"[{idx}/{total}] FAILED novel {novel_id}: {e}")

                time.sleep(2)

            self.log_message(
                f"Batch complete! OK: {succeeded}, Failed: {failed}, "
                f"Warnings: {len(warning_items)}, Skipped: {skipped}"
            )
            if failed_items:
                self.log_message("Failed novels:")
                for item in failed_items:
                    nid = item.get("novel_id", "?")
                    item_title = item.get("title") or nid
                    err = item.get("error") or "download failed"
                    self.log_message(f"  - [{nid}] {item_title}: {err}")
            if warning_items:
                self.log_message("Novels with warnings:")
                for item in warning_items:
                    nid = item.get("novel_id", "?")
                    item_title = item.get("title") or nid
                    warning_text = self._batch_result_warning_summary(item)
                    self.log_message(f"  - [{nid}] {item_title}: {warning_text}")
                    for cid, chap_title, count in item.get("image_failure_chapters") or []:
                        self.log_message(f"      image [{cid}] {chap_title}: {count} failed")
            batch_result = {
                "status": (
                    "ok"
                    if succeeded > 0
                    and not self._stop_requested
                    and failed == 0
                    and not warning_items
                    else "failed"
                ),
                "failed_chapters": [],
                "blocked_chapters": [],
                "image_failures": 0,
            }
        except Exception as e:
            self.log_message(f"Batch download failed: {e}")
            batch_result = {"status": "failed", "error": str(e)}
        finally:
            self.var_quick_enable.set(prev_quick_enable)
            self.var_quick_path.set(prev_quick_path)
            self.var_novel_id.set(prev_novel_id)
            self._reset_progress()
            self._ui_batch(0, 0)
            self._ui_status("Idle")
        return batch_result

    def _download_worker(self):
        self._ui_status("Analyzing...")
        # Reset image counter for this download session
        with self.image_lock:
            self.image_no = 1
        novel_input = self.var_novel_id.get().strip()
        novel_id = extract_novel_id(novel_input)
        if not novel_id:
            self._last_download_result = {
                "status": "failed",
                "novel_id": novel_input,
                "title": novel_input or "(empty)",
                "error": "Missing Novel ID",
            }
            self._ui_warn(
                "Missing Novel ID",
                "Please enter a Novel ID or Npia URL before downloading.",
            )
            self._ui_status("Idle")
            return self._last_download_result
        output_formats = self._selected_output_formats()
        if not output_formats:
            self._last_download_result = {
                "status": "failed",
                "novel_id": novel_id,
                "title": novel_id,
                "error": "No output format selected",
            }
            self.log_message("No output format selected.")
            self._ui_status("Idle")
            return self._last_download_result
        if novel_input != novel_id:
            self.log_message(f"Resolved input to Novel ID: {novel_id}")
            self.var_novel_id.set(novel_id)

        # Save novel ID to config immediately
        self._persist_settings()

        # Per-download log file: logs/nd_<novel_id>_<timestamp>.log
        # Every log_message call made during this download is mirrored into
        # this file via the root 'ND' logger, so if something goes wrong for
        # a specific novel you have a self-contained transcript.
        per_novel_handler = self._begin_per_novel_log(novel_id)
        try:
            self._last_download_result = self._download_worker_body(novel_id)
            return self._last_download_result
        finally:
            self._end_per_novel_log(per_novel_handler)

    def _begin_per_novel_log(self, novel_id):
        """Attach a FileHandler writing to <logs_dir>/nd_<novel_id>_<timestamp>.log.

        Uses the same writable-dir resolver as startup logging so it works
        even when the app is installed in a read-only location (e.g. a
        macOS .app bundle in /Applications).
        """
        try:
            log_dir = _writable_data_dir('logs')
            if not log_dir:
                self.log_message(
                    "\u26a0 No writable log directory available; "
                    "per-novel log not created."
                )
                return None
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(log_dir, f"nd_{novel_id}_{stamp}.log")
            handler = logging.FileHandler(path, encoding='utf-8')
            handler.setFormatter(logging.Formatter(
                '[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
            ))
            handler.setLevel(logging.DEBUG)
            _logger.addHandler(handler)
            self._current_novel_log = path
            self.log_message(f"\U0001f4dd Per-novel log: {path}")
            return handler
        except Exception as e:
            self.log_message(f"\u26a0 Could not create per-novel log: {e}")
            return None

    def _end_per_novel_log(self, handler):
        """Flush and detach the per-novel log handler added by _begin_per_novel_log."""
        if handler is None:
            return
        try:
            handler.flush()
            _logger.removeHandler(handler)
            handler.close()
        except Exception:
            pass
        self._current_novel_log = None

    def _download_worker_body(self, novel_id):
        """The original _download_worker body, extracted so _download_worker
        can wrap it with per-novel log setup/teardown."""
        output_error = None

        # Cache setup (loaded early so metadata can be cached too)
        use_cache = self.var_use_cache.get()
        cache_images = use_cache and self.var_cache_images.get()
        cache_data = {}
        cache_path = None
        # Compute a fingerprint of every setting that influences processed
        # chapter HTML, image bytes, or image filenames. If it doesn't match
        # what the cache was written with, invalidate the processed
        # html + images from each entry but keep the raw chapter JSON so
        # we don't have to re-download the text.
        current_fp = self._image_cache_fingerprint()
        if use_cache:
            cache_dir = _writable_data_dir('.cache')
            if not cache_dir:
                self.log_message(
                    "\u26a0 No writable cache directory available; "
                    "proceeding without on-disk cache."
                )
                use_cache = False
                cache_images = False
            else:
                cache_path = os.path.join(cache_dir, f'{novel_id}.json')
                if os.path.exists(cache_path):
                    try:
                        with open(cache_path, 'r', encoding='utf-8') as f:
                            cache_data = json.load(f)
                        stored_fp = cache_data.get('_image_fingerprint')
                        has_full_image_cache = self._has_cached_images(cache_data)
                        if stored_fp and stored_fp != current_fp:
                            self._invalidate_cached_images(cache_data)
                            self.log_message(
                                "\u26a0 Chapter processing settings changed since last cache "
                                "write; dropping cached images and re-processing "
                                "from cached JSON."
                            )
                        elif not stored_fp and has_full_image_cache:
                            self._invalidate_cached_images(cache_data)
                            self.log_message(
                                "\u26a0 Legacy image cache has no settings fingerprint; "
                                "dropping cached images and re-processing from cached JSON."
                            )
                        reserved = {'_meta', '_image_fingerprint', '_cover_image'}
                        chapter_count = sum(1 for k in cache_data if k not in reserved)
                        self.log_message(f"Cache loaded ({chapter_count} chapters cached).")
                    except Exception:
                        cache_data = {}

        # Metadata (use cache if available)
        if use_cache and '_meta' in cache_data:
            meta = cache_data['_meta']
            self.log_message(f"Metadata from cache: {meta.get('title', '?')} by {meta.get('author', '?')}")
        else:
            meta = self.downloader.fetch_metadata(novel_id)
            if not meta:
                self._ui_status("Idle")
                return {
                    "status": "failed",
                    "novel_id": novel_id,
                    "title": novel_id,
                    "error": "Failed to fetch metadata",
                }
            if use_cache:
                cache_data['_meta'] = meta

        # Determine output paths. For multiple formats the save dialog picks
        # one shared base name, then sibling files receive their normal
        # extensions (for example book.epub, book.txt, and book.pdf).
        self._output_formats = self._selected_output_formats()
        self._active_separate_txt = self.var_separate_txt.get()
        self._output_format = self._output_formats[0]
        self.var_save_format.set(self._output_format)
        default_name = meta.get('title', f"novel_{novel_id}") if self.var_naming_mode.get() == 'title' else f"{novel_id}"

        def clean_filename(name):
            return "".join(c for c in name if c not in '\\/:*?"<>|').strip()
        
        if self.var_quick_enable.get() and self.var_quick_path.get():
            folder = self.var_quick_path.get()
            base = clean_filename(default_name)
            if self.var_append_range.get() and self.var_from_enabled.get() and self.var_to_enabled.get():
                base = f"{base}_{self.var_from_num.get()}-{self.var_to_num.get()}"
            anchor = os.path.join(folder, f"[{novel_id}] {base}")
            self._output_paths = {
                fmt: f"{anchor}.{fmt}" for fmt in self._output_formats
            }
        else:
            primary = self._output_formats[0]
            suggested = f"[{novel_id}] {clean_filename(default_name)}.{primary}"
            dialog_title = (
                "Choose output file"
                if len(self._output_formats) == 1
                else "Choose base name for output files"
            )
            path = self._ui_ask_save_path(
                title=dialog_title,
                defaultextension='.' + primary,
                initialfile=suggested,
                filetypes=[(primary.upper(), f"*.{primary}"), ("All files", "*")],
            )
            if not path:
                self._ui_status("Idle")
                return
            self._output_paths = self._output_paths_from_anchor(
                path, self._output_formats
            )
            sibling_overwrites = []
            for fmt, candidate in self._output_paths.items():
                if fmt == 'txt' and self._active_separate_txt:
                    candidate = os.path.splitext(candidate)[0]
                if (
                    os.path.normcase(os.path.abspath(candidate))
                    != os.path.normcase(os.path.abspath(path))
                    and os.path.exists(candidate)
                ):
                    sibling_overwrites.append(candidate)
            if sibling_overwrites:
                names = "\n".join(
                    f"  • {os.path.basename(candidate)}"
                    for candidate in sibling_overwrites
                )
                if not self._ui_confirm(
                    "Replace Existing Output Files?",
                    "These additional output files already exist:\n\n"
                    f"{names}\n\nReplace them?",
                ):
                    self._ui_status("Idle")
                    return
        self._output_path = self._output_paths[self._output_format]

        try:
            max_retries = max(1, int(self.var_max_retries.get() or 5))
        except Exception:
            max_retries = 5
        try:
            image_retry_delay = max(0.0, min(60.0, float(self.var_interval.get() or 0.0)))
        except Exception:
            image_retry_delay = 0.5

        # Notices
        notice_items = []
        if self.var_include_notices.get():
            try:
                notice_items = self.downloader.fetch_notice_ids(novel_id) or []
                for n in notice_items:
                    n['is_notice'] = True
            except Exception:
                notice_items = []

        # Chapter list
        chapters = self.downloader.fetch_chapter_list(novel_id, max_retries=max_retries)
        if not chapters:
            self._ui_status("Idle")
            return {
                "status": "failed",
                "novel_id": novel_id,
                "title": meta.get('title', novel_id),
                "error": "No chapters found",
            }

        start_idx = (self.var_from_num.get() - 1) if self.var_from_enabled.get() else 0
        end_idx = self.var_to_num.get() if self.var_to_enabled.get() else len(chapters)
        start_idx = max(0, start_idx)
        end_idx = min(len(chapters), end_idx)
        selected = chapters[start_idx:end_idx]
        if not selected:
            self.log_message("No chapters selected.")
            self._ui_status("Idle")
            return {
                "status": "failed",
                "novel_id": novel_id,
                "title": meta.get('title', novel_id),
                "error": "No chapters selected",
            }

        css = """div.svg_outer {
   display: block;
   margin-bottom: 0;
   margin-left: 0;
   margin-right: 0;
   margin-top: 0;
   padding-bottom: 0;
   padding-left: 0;
   padding-right: 0;
   padding-top: 0;
   text-align: left;
}
div.svg_inner {
   display: block;
   text-align: center;
}
h1, h2 {
   text-align: center;
   margin-bottom: 10%;
   margin-top: 10%;
}
h3, h4, h5, h6 {
   text-align: center;
   margin-bottom: 15%;
   margin-top: 10%;
}
ol, ul {
   padding-left: 8%;
}
body {
  margin: 2%;
}
p {
  overflow-wrap: break-word;
}
dd, dt, dl {
  padding: 0;
  margin: 0;
}
img {
   display: block;
   min-height: 1em;
   max-height: 100%;
   max-width: 100%;
   padding-bottom: 0;
   padding-left: 0;
   padding-right: 0;
   padding-top: 0;
   margin-left: auto;
   margin-right: auto;
   margin-bottom: 2%;
   margin-top: 2%;
}
img.inline {
   display: inline;
   min-height: 1em;
   margin-bottom: 0;
   margin-top: 0;
}
.thumbcaption {
  display: block;
  font-size: 0.9em;
  padding-right: 5%;
  padding-left: 5%;
}
hr {
   color: black;
   background-color: black;
   height: 2px;
}
a:link {
   text-decoration: none;
   color: #0B0080;
}
a:visited {
   text-decoration: none;
}
a:hover {
   text-decoration: underline;
}
a:active {
   text-decoration: underline;
}table {
   width: 90%;
   border-collapse: collapse;
}
table, th, td {
   border: 1px solid black;
 }
"""
        total_items = len(notice_items) + len(selected)
        self.log_message(f"Preparing download: {len(selected)} chapter(s) + {len(notice_items)} notice(s) = {total_items} item(s)")
        save_image_urls_only = self.var_save_image_urls_only.get()
        if save_image_urls_only:
            self.log_message(
                "Image URL-only mode enabled: keeping remote image previews "
                "without downloading image files."
            )
        # Note: the EPUB/PDF is assembled from scratch by _build_output() just
        # before writing. We only gather cover bytes + info_html here; those
        # are passed into _build_output so that the target-size recompression
        # pass can rebuild a fresh file without any lingering state.

        cover_image = None
        # cover
        if meta.get('cover_url') and save_image_urls_only:
            self.log_message(
                f"Keeping remote cover image URL: {meta['cover_url']}"
            )
        elif meta.get('cover_url'):
            cover_fp = self._cover_cache_fingerprint()
            cached_cover = cache_data.get('_cover_image') if (use_cache and cache_images) else None
            if isinstance(cached_cover, dict):
                try:
                    if (
                        cached_cover.get('url') == meta.get('cover_url')
                        and cached_cover.get('fingerprint') == cover_fp
                    ):
                        filename = cached_cover.get('filename') or ''
                        data = base64.b64decode(cached_cover.get('data') or '')
                        if filename and data:
                            cover_image = {"filename": filename, "data": data}
                            self.log_message(
                                f"Cached cover image: {filename} ({_format_size(len(data))})"
                            )
                except Exception as cov_cache_err:
                    self.log_message(f"\u26a0 Cached cover image invalid; refetching ({cov_cache_err})")

            if cover_image is None:
                try:
                    self._ui_status("Fetching cover...")
                    self.log_message(f"Fetching cover image: {meta['cover_url']}")
                    data = _download_image_with_progress(
                        self.auth.session,
                        meta['cover_url'],
                        self.log_message,
                        label="Cover image",
                        max_retries=max_retries,
                        retry_delay=image_retry_delay,
                    )
                    if data:
                        cover_ext = _detect_source_ext(data, meta.get('cover_url', ''))
                        # Use separate cover compression settings
                        if self.var_compress_cover.get() and Image is not None:
                            try:
                                im = Image.open(io.BytesIO(data))
                                cover_fmt = self.var_cover_format.get()
                                # Shared encoder: preserves animation when source is an
                                # animated WEBP/AVIF and target supports it.
                                data, cover_ext = _encode_image_bytes(
                                    im, cover_fmt, self.var_cover_quality.get(),
                                    logger=self.log_message,
                                    static_only=self.var_static_only.get(),
                                )
                            except Exception as cov_err:
                                self.log_message(f"\u26a0 Cover conversion failed: {cov_err}")
                        self.log_message(
                            f"  Cover image: cover.{cover_ext} ({_format_size(len(data))})"
                        )
                        cover_image = {"filename": f"cover.{cover_ext}", "data": data}
                        if use_cache and cache_images:
                            cache_data['_cover_image'] = {
                                'url': meta.get('cover_url', ''),
                                'fingerprint': cover_fp,
                                'filename': cover_image['filename'],
                                'data': base64.b64encode(cover_image['data']).decode('ascii'),
                            }
                    else:
                        self.log_message("\u26a0 Cover fetch failed; continuing without EPUB cover.")
                except Exception as e:
                    self.log_message(f"\u26a0 Cover fetch failed: {e}")

        info_html = None
        # Add info.xhtml with metadata below the cover (matches original repo layout)
        try:
            title = meta.get('title', '')
            author = meta.get('author', '')
            tags = meta.get('tags', []) or []
            tags_str = ', '.join([str(t) for t in tags]) if tags else ''
            status = meta.get('status', '')
            description = meta.get('description', '') or ''

            info_parts = []
            info_parts.append(f"  <h1>{html.escape(title)}</h1>\n")
            info_parts.append(f"  <p><strong>Author:</strong> {html.escape(author)}</p>\n")
            if tags_str:
                info_parts.append(f"  <p><strong>Tags:</strong> {html.escape(tags_str)}</p>\n")
            if status:
                info_parts.append(f"  <p><strong>Status:</strong> {html.escape(status)}</p>\n")
            info_parts.append('\n')
            info_parts.append('  <h2 class="sigil_not_in_toc">Synopsis</h2>\n')
            # Split description into paragraphs and preserve line breaks inside paragraphs
            if description:
                # Normalize CRLF and split on blank lines
                paras = re.split(r"\r?\n\s*\r?\n", description.strip())
                for para in paras:
                    para = para.strip()
                    if not para:
                        continue
                    safe = html.escape(para).replace('\n', '<br/>')
                    info_parts.append(f"  <p>{safe}</p>\n")

            info_html = "\n".join(info_parts)
        except Exception:
            pass

        def next_image_no():
            with self.image_lock:
                n = self.image_no
                self.image_no += 1
                return n

        def _cache_entry_to_json(entry):
            """Extract raw content_json from a cache entry (string or dict)."""
            if isinstance(entry, str):
                return entry
            if isinstance(entry, dict):
                return entry.get('json', '')
            return ''

        def _has_full_cache(entry):
            """Check if a cache entry contains processed html + images."""
            return isinstance(entry, dict) and 'html' in entry and 'images' in entry

        for c in selected:
            c.setdefault('is_notice', False)
        selected_total = (notice_items + selected) if notice_items else selected
        results = [None] * len(selected_total)

        self._progress_start_time = time.time()
        self._update_progress(value=0, total=len(selected_total))

        try:
            raw_threads = int(self.var_threads.get() or 1)
        except Exception:
            raw_threads = 1
        try:
            raw_image_compression_workers = int(self.var_image_compression_workers.get() or 1)
        except Exception:
            raw_image_compression_workers = 1
        try:
            raw_interval = float(self.var_interval.get() or 0.0)
        except Exception:
            raw_interval = 0.5
        try:
            raw_interval_max = float(self.var_interval_max.get() or 0.0)
        except Exception:
            raw_interval_max = raw_interval

        threads = max(1, min(32, raw_threads))
        image_compression_workers = max(1, min(32, raw_image_compression_workers))
        interval = max(0.0, min(300.0, raw_interval))
        interval_max = max(0.0, min(300.0, raw_interval_max))
        if interval_max < interval:
            interval, interval_max = interval_max, interval
        compress_images = self.var_compress_images.get()
        effective_image_compression_workers = (
            image_compression_workers
            if compress_images and not save_image_urls_only
            else 1
        )
        if threads != raw_threads:
            self.log_message(f"\u26a0 Threads clamped to {threads} (valid range: 1\u201332)")
        if image_compression_workers != raw_image_compression_workers:
            self.log_message(f"\u26a0 Image compression workers clamped to {image_compression_workers} (valid range: 1\u201332)")
        if interval != raw_interval or interval_max != raw_interval_max:
            self.log_message(
                f"\u26a0 Interval range normalized to {interval:g}\u2013{interval_max:g}s "
                "(valid range: 0\u2013300)"
            )
        self.log_message(
            f"Chapter interval: {interval:g}\u2013{interval_max:g}s"
            if interval != interval_max
            else f"Chapter interval: {interval:g}s (fixed)"
        )
        if save_image_urls_only:
            self.log_message(
                f"Workers: {threads} chapter request(s). Remote image URLs "
                "require no image download or compression jobs."
            )
        else:
            self.log_message(
                f"Workers: {threads} chapter request(s), "
                f"{effective_image_compression_workers} image compression job(s). "
                "Image compression workers do not change chapter scraping."
            )
        image_quality = self.var_jpeg_quality.get()
        image_format = self.var_image_format.get()
        convert_gifs = self.var_convert_gifs.get()
        static_only = self.var_static_only.get()
        strip_leading_spaces = self.var_strip_leading_spaces.get()
        remove_newlines = self.var_remove_newlines.get()

        # Process cached chapters first
        uncached_indices = []
        cached_json_jobs = []
        full_cache_hits = 0
        json_cache_hits = 0
        if use_cache:
            for idx, chap in enumerate(selected_total):
                chap_id = chap['id']
                entry = cache_data.get(chap_id)
                if entry is not None:
                    try:
                        content_json = _cache_entry_to_json(entry)
                        try:
                            validate_chapter_payload(content_json)
                        except InvalidChapterPayloadError as cache_error:
                            cache_data.pop(chap_id, None)
                            uncached_indices.append(idx)
                            self.log_message(
                                f"Rejected invalid cached chapter [{chap_id}]: "
                                f"{cache_error}. Downloading it again."
                            )
                            continue

                        # Full cache hit (html + images stored) — skip all processing
                        if cache_images and _has_full_cache(entry):
                            cached_html = entry['html']
                            cached_imgs = [
                                (name, base64.b64decode(b64))
                                for name, b64 in entry.get('images', [])
                            ]
                            # Advance image counter past cached filenames
                            for fname, _ in cached_imgs:
                                try:
                                    num = int(fname.rsplit('.', 1)[0])
                                    with self.image_lock:
                                        if num >= self.image_no:
                                            self.image_no = num + 1
                                except (ValueError, IndexError):
                                    pass
                            results[idx] = (chap['title'], cached_html, cached_imgs, chap.get('is_notice', False))
                            self.log_message(f"Cached: {chap['title']}")
                            self._update_progress(value=self.progress_value + 1)
                            full_cache_hits += 1
                        else:
                            # JSON-only cache hit — still need to process images
                            if content_json:
                                cached_json_jobs.append((idx, content_json))
                                json_cache_hits += 1
                            else:
                                uncached_indices.append(idx)
                    except Exception as e:
                        self.log_message(f"Cache read error {chap.get('title', '?')}: {e}")
                        uncached_indices.append(idx)
                else:
                    uncached_indices.append(idx)
            cached_count = full_cache_hits + json_cache_hits
            if cached_count > 0:
                self.log_message(
                    f"Cache hits: {full_cache_hits} full, "
                    f"{json_cache_hits} JSON-only/reprocess images; "
                    f"{len(uncached_indices)} chapter(s) need network download."
                )
        else:
            uncached_indices = list(range(len(selected_total)))

        # Download uncached chapters
        self._ui_status("Downloading...")
        dl_stats = _DownloadStats()
        image_futures = {}

        def _process_chapter_images(idx, content_json):
            chap = selected_total[idx]
            hb, imgs, img_fails = extract_chapter_content_and_images(
                content_json, self.font_mapper, self.auth.session,
                compress_images, image_quality,
                image_format, self.log_message, next_image_no,
                chapter_title=chap.get('title', ''),
                chapter_num=idx + 1,
                convert_gifs=convert_gifs,
                static_only=static_only,
                save_image_urls_only=save_image_urls_only,
                strip_leading_spaces=strip_leading_spaces,
                remove_newlines=remove_newlines,
                max_retries=max_retries,
                retry_delay=interval,
            )
            return idx, content_json, hb, imgs, img_fails

        def _queue_image_job(image_executor, idx, content_json, source_label):
            future = image_executor.submit(_process_chapter_images, idx, content_json)
            image_futures[future] = (idx, content_json, source_label)

        def _finish_image_future(future):
            idx, content_json, source_label = image_futures.pop(future)
            chap = selected_total[idx]
            try:
                _idx, content_json, hb, imgs, img_fails = future.result()
                results[idx] = (chap['title'], hb, imgs, chap.get('is_notice', False))
                remote_image_count = (
                    hb.count('class="remote-image"')
                    if save_image_urls_only
                    else 0
                )
                image_count = remote_image_count or len(imgs)
                if remote_image_count:
                    img_info = f" ({remote_image_count} remote image previews)"
                else:
                    img_info = f" ({len(imgs)} images)" if imgs else ""
                if img_fails:
                    dl_stats.add_image_failures(chap['id'], chap.get('title', '?'), img_fails)
                    img_info += f", {img_fails} image failure(s)"
                self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] {source_label}: {chap['title']}{img_info}")

                if image_count:
                    img_bytes = sum(len(d) for _, d in imgs)
                    dl_stats.add(image_count, img_bytes)
                    self._img_downloaded_count += image_count
                    self._ui_images(self._img_downloaded_count)

                if use_cache:
                    if cache_images and img_fails == 0:
                        cache_data[chap['id']] = {
                            'json': content_json,
                            'html': hb,
                            'images': [[n, base64.b64encode(d).decode('ascii')] for n, d in imgs]
                        }
                    else:
                        cache_data[chap['id']] = content_json
                        if cache_images and img_fails:
                            self.log_message(
                                f"\u26a0 {chap['title']}: {img_fails} image(s) failed; "
                                f"storing JSON-only cache so images will be retried next run."
                            )
            except Exception as e:
                dl_stats.add_failed(chap['id'], chap.get('title', '?'))
                if use_cache and content_json:
                    cache_data[chap['id']] = content_json
                self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Error processing images for {chap.get('title','?')}: {e}")
            finally:
                self._update_progress(value=self.progress_value + 1)

        def _drain_finished_image_futures(wait_for_all=False):
            futures = list(image_futures)
            iterable = as_completed(futures) if wait_for_all else [f for f in futures if f.done()]
            for future in iterable:
                if future in image_futures:
                    _finish_image_future(future)

        if cached_json_jobs or uncached_indices:
            with ThreadPoolExecutor(max_workers=effective_image_compression_workers) as image_executor:
                for idx, content_json in cached_json_jobs:
                    _queue_image_job(image_executor, idx, content_json, "Cached")

                if uncached_indices:
                    access_blocked_detected = False
                    with ThreadPoolExecutor(max_workers=threads) as chapter_executor:
                        for i in range(0, len(uncached_indices), threads):
                            if self._stop_requested:
                                self.log_message("Download stopped by user.")
                                break
                            batch = uncached_indices[i:i + threads]
                            f_map = {
                                chapter_executor.submit(
                                    self.downloader.download_chapter_content,
                                    selected_total[x]['id'],
                                    max_retries,
                                ): x
                                for x in batch
                            }
                            for future in as_completed(f_map):
                                idx = f_map[future]
                                chap = selected_total[idx]
                                try:
                                    content_json = future.result()
                                    if content_json:
                                        _queue_image_job(image_executor, idx, content_json, "Downloaded")
                                    else:
                                        dl_stats.add_failed(chap['id'], chap.get('title', '?'))
                                        self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Failed: {chap.get('title', '?')}")
                                        self._update_progress(value=self.progress_value + 1)
                                except AccessBlockedError:
                                    dl_stats.add_blocked(chap['id'], chap.get('title', '?'))
                                    self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Blocked: {chap.get('title', '?')}")
                                    self._update_progress(value=self.progress_value + 1)
                                    access_blocked_detected = True
                                    self.downloader.stop_signal = True
                                except Exception as e:
                                    dl_stats.add_failed(chap['id'], chap.get('title', '?'))
                                    self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Error {chap.get('title','?')}: {e}")
                                    self._update_progress(value=self.progress_value + 1)
                                _drain_finished_image_futures(wait_for_all=False)

                            if access_blocked_detected:
                                self.log_message(
                                    "Access/rate-limit response detected. Stopping immediately "
                                    "to avoid repeated requests."
                                )
                                break

                            if interval_max > 0:
                                delay = random.uniform(interval, interval_max)
                                self.log_message(f"Next chapter request in {delay:.1f}s...")
                                time.sleep(delay)
                            _drain_finished_image_futures(wait_for_all=False)

                _drain_finished_image_futures(wait_for_all=True)


        # Save cache
        if use_cache and cache_path:
            try:
                # Stamp the fingerprint so the next run knows which image
                # settings the cached images/html were encoded with.
                cache_data['_image_fingerprint'] = current_fp
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False)
            except Exception as e:
                self.log_message(f"Cache save error: {e}")

        # Generate every requested output from the same downloaded results.
        # Recompression receives the original results for each rich format, so
        # lowering image quality for one file never leaks into another.
        output_errors = []
        for output_format in self._output_formats:
            self._output_format = output_format
            self._output_path = self._output_paths[output_format]
            try:
                if output_format in ('epub', 'pdf'):
                    self._build_output(
                        results, meta, css, cover_image, info_html,
                        chapter_start=start_idx + 1,
                        notice_count=len(notice_items),
                    )
                else:
                    self._write_text_output(
                        results, remove_newlines,
                        chapter_start=start_idx + 1,
                        notice_count=len(notice_items),
                    )
            except Exception as e:
                error = f"{output_format.upper()} generation failed: {e}"
                output_errors.append(error)
                self.log_message(error)
                continue

            # Optional: keep re-encoding images at lower quality until the
            # final EPUB/PDF fits the user's target size.
            if (
                output_format in ('epub', 'pdf')
                and self.var_recompress_target_enable.get()
            ):
                try:
                    target_mb = float(self.var_recompress_target_mb.get() or 0)
                except (TypeError, ValueError):
                    target_mb = 0
                if target_mb > 0:
                    self._recompress_to_target_size(
                        results, meta, css, cover_image, info_html,
                        int(target_mb * 1024 * 1024),
                        chapter_start=start_idx + 1,
                        notice_count=len(notice_items),
                    )

            try:
                self.log_message(
                    f"Output file: {self._output_path} "
                    f"({_format_size(os.path.getsize(self._output_path))})"
                )
            except OSError:
                pass

        if output_errors:
            output_error = "; ".join(output_errors)

        # Preserve the historical single-path fields for callers that only
        # inspect `output_path`, while also returning every generated path.
        self._output_format = self._output_formats[0]
        self._output_path = self._output_paths[self._output_format]

        # Final summary
        if dl_stats.total_images > 0:
            self.log_message(f"📊 {dl_stats.summary()}")
        warn_text = dl_stats.warnings_summary()
        if warn_text:
            self.log_message(warn_text)
        failed_count = len(dl_stats.failed_chapters)
        blocked_count = len(dl_stats.blocked_chapters)
        if self._stop_requested:
            status = "stopped"
            self.log_message(
                f"FINAL SUMMARY: STOPPED | failed chapters: {failed_count} | "
                f"blocked chapters: {blocked_count}"
            )
            self.log_message("Download stopped by user.")
        elif output_error or failed_count or blocked_count:
            status = "failed"
            self.log_message(
                f"FINAL SUMMARY: FAILED | failed chapters: {failed_count} | "
                f"blocked chapters: {blocked_count}"
            )
            self.log_message("Download finished with errors.")
        else:
            status = "ok"
            self.log_message(
                "FINAL SUMMARY: SUCCESS | failed chapters: 0 | "
                "blocked chapters: 0"
            )
            self.log_message("Download Complete!")
        if self._session_log_path():
            self.log_message(f"Log saved: {self._session_log_path()}")
        result = {
            "status": status,
            "novel_id": novel_id,
            "title": meta.get('title', novel_id),
            "output_path": self._output_path,
            "output_paths": dict(self._output_paths),
            "error": output_error,
            "total_images": dl_stats.total_images,
            "total_image_bytes": dl_stats.total_bytes,
            "image_failures": dl_stats.image_failures,
            "image_failure_chapters": list(dl_stats.image_failure_chapters),
            "failed_chapters": list(dl_stats.failed_chapters),
            "blocked_chapters": list(dl_stats.blocked_chapters),
        }
        self._reset_progress()
        self._ui_status("Idle")
        return result

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def _image_cache_fingerprint(self):
        """Return a short hash of settings that influence cached chapter output.

        This covers processed HTML as well as image bytes and filenames.
        """
        import hashlib
        parts = [
            "v3",
            str(bool(self.var_compress_images.get())),
            str(bool(self.var_save_image_urls_only.get())),
            str(int(self.var_jpeg_quality.get() or 0)),
            str(self.var_image_format.get() or ""),
            str(bool(self.var_convert_gifs.get())),
            str(bool(self.var_static_only.get())),
            str(bool(self.var_strip_leading_spaces.get())),
            str(bool(self.var_remove_newlines.get())),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]

    def _cover_cache_fingerprint(self):
        """Return a short hash of settings that influence cached cover bytes."""
        import hashlib
        compress_cover = bool(self.var_compress_cover.get())
        parts = [
            "v1",
            str(compress_cover),
        ]
        if compress_cover:
            parts.extend([
                str(int(self.var_cover_quality.get() or 0)),
                str(self.var_cover_format.get() or ""),
                str(bool(self.var_static_only.get())),
            ])
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _has_cached_images(cache_data):
        reserved = {'_meta', '_image_fingerprint', '_cover_image'}
        for k, entry in cache_data.items():
            if k in reserved:
                continue
            if isinstance(entry, dict) and 'html' in entry and 'images' in entry:
                return True
        return False

    @staticmethod
    def _invalidate_cached_images(cache_data):
        """Strip html+images from each chapter entry, keeping only raw json.

        After this call, a full-cache entry `{json, html, images}` becomes
        the JSON-only form (a plain string). Chapters that were already
        JSON-only are left untouched. Reserved keys (`_meta`,
        `_image_fingerprint`) are preserved.
        """
        reserved = {'_meta', '_image_fingerprint', '_cover_image'}
        for k, entry in list(cache_data.items()):
            if k in reserved:
                continue
            if isinstance(entry, dict):
                # Keep the raw JSON only; drop processed images/html.
                cache_data[k] = entry.get('json', '')

    # ------------------------------------------------------------------
    # Output builders / target-size recompressor
    # ------------------------------------------------------------------
    def _selected_output_formats(self):
        """Return checked output formats in a stable, user-facing order."""
        return [
            fmt
            for fmt, variable in (
                ('epub', self.var_save_epub),
                ('txt', self.var_save_txt),
                ('pdf', self.var_save_pdf),
            )
            if variable.get()
        ]

    @staticmethod
    def _output_paths_from_anchor(anchor_path, output_formats):
        """Build one path per format from a Save As filename.

        A single selection keeps the exact path for backward compatibility.
        Multiple selections share a base name and use canonical extensions.
        """
        formats = list(output_formats)
        if len(formats) == 1:
            return {formats[0]: anchor_path}

        base, extension = os.path.splitext(anchor_path)
        if extension.lower().lstrip('.') not in {'epub', 'txt', 'pdf'}:
            base = anchor_path
        return {fmt: f"{base}.{fmt}" for fmt in formats}

    def _numbered_title(self, chapter_number, title, is_notice=False):
        """Use the full-book position for headings without changing cached titles."""
        if is_notice:
            return title
        if not title:
            return f"Chapter {chapter_number}"
        setting = getattr(self, 'var_number_chapters', None)
        if setting is None or not setting.get():
            return title
        return f"{chapter_number}. {title}"

    def _write_text_output(self, results, remove_newlines,
                           chapter_start=1, notice_count=0):
        """Write the TXT/HTML representation to the active output path."""
        def entries():
            for position, res in enumerate(results, 1):
                if not res:
                    continue
                title, html_body, _, notice = res
                chapter_number = chapter_start + position - 1 - notice_count
                file_number = position if notice else chapter_number
                title = NpiaPipelineMixin._numbered_title(
                    self, chapter_number, title, notice,
                )
                if self.var_save_html.get():
                    yield file_number, title, f"<h2>{title}</h2>\n{html_body}\n\n"
                    continue

                # Respect source formatting: paragraphs become double
                # newlines and <br> elements become single newlines.
                text = re.sub(
                    r'<br\s*/?>', '\n', html_body, flags=re.IGNORECASE
                )
                text = re.sub(
                    r'</(p|div)>', '\n\n', text, flags=re.IGNORECASE
                )
                text = re.sub(r'<[^>]+>', '', text)
                text = html.unescape(text)
                if remove_newlines:
                    plain = re.sub(r'\n\s*\n', '\n\n', text).strip()
                else:
                    plain = text.strip("\r\n")
                yield file_number, title, f"{title}\n\n{plain}\n\n\n"
        setting = getattr(self, 'var_separate_txt', None)
        separate = getattr(self, '_active_separate_txt', None)
        if separate is None:
            separate = bool(setting and setting.get())
        location = write_text_output(self._output_path, entries(), separate)
        if separate:
            self._output_path = location
            if hasattr(self, '_output_paths'):
                self._output_paths['txt'] = location
            self.log_message(f"TXT output folder: {location}")


    def _build_output(self, results, meta, css, cover_image, info_html,
                      chapter_start=1, notice_count=0):
        """Write the current output file (EPUB or PDF) from the given results.

        Called once normally, and again by `_recompress_to_target_size` after
        the images have been re-encoded at lower quality.
        """
        if self._output_format == 'epub':
            epub = EpubGenerator(
                meta,
                self._output_path,
                css,
                self.var_zip_compress_images.get(),
                remote_cover_url=(
                    DownloaderCore.normalize_chapter_image_url(meta.get('cover_url'))
                    if self.var_save_image_urls_only.get()
                    else None
                ),
            )
            if cover_image and cover_image.get('data'):
                epub.add_image(cover_image['filename'], cover_image['data'])
            if info_html:
                epub.add_extra_page('info.xhtml', info_html)
            for result_index, res in enumerate(results):
                if res:
                    t, h, imgs, notice = res
                    for name, data in imgs:
                        epub.add_image(name, data)
                    chapter_number = None
                    if not notice:
                        chapter_number = (
                            chapter_start + result_index - notice_count
                        )
                    t = NpiaPipelineMixin._numbered_title(self, chapter_number, t, notice)
                    epub.add_chapter(
                        t, h, is_notice=notice,
                        chapter_number=chapter_number,
                    )
            epub.generate()
        elif self._output_format == 'pdf':
            chapters_for_pdf = []
            image_map = {}
            for result_index, res in enumerate(results):
                if res:
                    t, h, imgs, notice = res
                    t = NpiaPipelineMixin._numbered_title(
                        self, chapter_start + result_index - notice_count,
                        t, notice,
                    )
                    for name, data in imgs:
                        if name not in image_map:
                            image_map[name] = data
                    chapters_for_pdf.append({"title": t, "html": h, "is_notice": notice})
            self.downloader.generate_pdf(
                meta,
                self._output_path,
                chapters_for_pdf,
                css,
                image_map=image_map,
                cover_image=cover_image,
                info_html=info_html,
                show_toc=self.var_pdf_toc.get(),
                show_page_numbers=self.var_pdf_page_numbers.get(),
                use_counter_layout=self.var_pdf_counter_layout.get(),
            )

    @staticmethod
    def _format_from_ext(fname):
        """Map a filename's extension to a Pillow image format identifier."""
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        return {
            'jpg': 'JPEG', 'jpeg': 'JPEG',
            'png': 'PNG', 'webp': 'WEBP', 'avif': 'AVIF', 'gif': 'GIF',
        }.get(ext)

    def _reencode_bytes(self, data, fname, quality, static_only):
        """Re-encode an already-compressed image at a lower quality.

        Returns (new_bytes, fname). GIF and PNG are returned unchanged
        (GIF has no meaningful quality knob and PNG is lossless). The
        filename is always preserved so HTML `<img src=>` references from
        the first pass stay valid even if _encode_image_bytes decides to
        fall back (e.g. AVIF -> WEBP when the plugin disappears mid-run).
        """
        fmt = self._format_from_ext(fname)
        if fmt in (None, 'GIF', 'PNG'):
            return data, fname
        if Image is None:
            return data, fname
        try:
            im = Image.open(io.BytesIO(data))
            new_bytes, _new_ext = _encode_image_bytes(
                im, fmt, quality,
                logger=self.log_message, static_only=static_only,
            )
            return new_bytes, fname
        except Exception:
            return data, fname

    def _recompress_to_target_size(self, results, meta, css, cover_image,
                                   info_html, target_bytes, chapter_start=1,
                                   notice_count=0):
        """Iteratively lower image quality and rebuild until size <= target.

        Uses a simple ratio-based heuristic (quality *= sqrt(target/size))
        capped at a 10-quality floor and at most 4 passes so the feedback
        loop stays snappy. Stops early if we can't reduce quality further.
        """
        try:
            current_size = os.path.getsize(self._output_path)
        except OSError:
            return
        if current_size <= target_bytes:
            return

        self.log_message(
            f"\U0001f4c1 Output {_format_size(current_size)} exceeds target "
            f"{_format_size(target_bytes)}; starting recompression passes..."
        )

        quality = max(10, int(self.var_jpeg_quality.get() or 50))
        static_only = self.var_static_only.get()
        max_passes = 4
        min_quality = 10

        for attempt in range(1, max_passes + 1):
            ratio = target_bytes / max(current_size, 1)
            # Quality-vs-size relationship is sub-linear; sqrt is a reasonable
            # first-order approximation that converges fast without overshooting.
            new_quality = max(min_quality, int(quality * (ratio ** 0.5)))
            # Ensure we actually step down even when the ratio is already close.
            if new_quality >= quality:
                new_quality = max(min_quality, quality - 10)
            if new_quality == quality:
                self.log_message(
                    "  Cannot reduce quality further; stopping recompression."
                )
                break
            quality = new_quality
            self.log_message(
                f"  Pass {attempt}: re-encoding images at quality={quality}..."
            )

            # Re-encode every image in every chapter, preserving the filename
            # so the in-document <img src="../Images/N.ext"/> references don't
            # need to change (_encode_image_bytes only returns a new ext when
            # we hit the AVIF->WEBP fallback, which is rare in this loop).
            new_results = []
            for res in results:
                if not res:
                    new_results.append(res)
                    continue
                title, html_body, imgs, is_notice = res
                new_imgs = []
                for name, data in imgs:
                    new_data, new_name = self._reencode_bytes(
                        data, name, quality, static_only
                    )
                    new_imgs.append((new_name, new_data))
                new_results.append((title, html_body, new_imgs, is_notice))
            results = new_results

            # Re-encode the cover too if present (respects Compress Cover's
            # format choice via the filename extension).
            if cover_image and cover_image.get('data'):
                new_data, new_name = self._reencode_bytes(
                    cover_image['data'], cover_image['filename'],
                    quality, static_only,
                )
                cover_image = {'filename': new_name, 'data': new_data}

            try:
                self._build_output(
                    results, meta, css, cover_image, info_html,
                    chapter_start=chapter_start,
                    notice_count=notice_count,
                )
            except Exception as e:
                self.log_message(f"  Rebuild failed on pass {attempt}: {e}")
                return

            try:
                current_size = os.path.getsize(self._output_path)
            except OSError:
                return
            self.log_message(
                f"  Pass {attempt} result: {_format_size(current_size)} "
                f"(target {_format_size(target_bytes)})"
            )
            if current_size <= target_bytes:
                self.log_message("\u2705 Target size reached.")
                return

        self.log_message(
            f"\u26a0 Could not reach target size after {max_passes} passes. "
            f"Final output: {_format_size(current_size)}."
        )
