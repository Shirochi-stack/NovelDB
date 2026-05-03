"""
external_dialog.py — Tkinter dialog for downloading novels from non-Novelpia sites.

Provides a self-contained Toplevel window with:
  - URL input
  - Format selection (EPUB / TXT)
  - Interval control for rate limiting
  - Chapter range selection
  - Fetch Info / Download / Stop buttons
  - Book info panel and log console
  - Progress bar with ETA

Uses ExternalScraper (Playwright + novel-downloader JS rules) for fetching,
and the existing epub_generator for EPUB output.
"""

import os
import sys
import re
import html
import json
import time
import threading
import queue

import tkinter as tk
from tkinter import ttk

from external_scraper import ExternalScraper


def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _sanitize_filename(name):
    """Remove characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip('. ')
    return name or "novel"


class ExternalNovelDialog(tk.Toplevel):
    """Tkinter Toplevel dialog for downloading novels from non-Novelpia sites."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("External Novel Download")
        self.transient(parent)

        # Size window
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        w = int(screen_w * 0.50)
        h = int(screen_h * 0.60)
        x = (screen_w - w) // 2
        y = (screen_h - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(600, 500)

        self._parent_gui = parent      # Access compression settings from main GUI
        self._scraper = None
        self._book_data = None
        self._chapter_results = []
        self._downloading = False
        self._start_time = None
        self._msg_queue = queue.Queue()

        # Single persistent worker thread for all Playwright calls.
        # Playwright's sync API is thread-bound, so fetch + download
        # must both run on the same thread.
        self._work_queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True
        )
        self._worker_thread.start()

        self._build_ui()
        self._load_ext_config()
        self._poll_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        # --- URL Input ---
        url_frame = ttk.LabelFrame(self, text="Novel URL", padding=6)
        url_frame.pack(fill="x", padx=10, pady=(10, 5))
        self._url_var = tk.StringVar()
        ent_url = ttk.Entry(url_frame, textvariable=self._url_var)
        ent_url.pack(fill="x")

        # --- Settings Row ---
        settings_frame = ttk.Frame(self)
        settings_frame.pack(fill="x", padx=10, pady=5)

        # Format
        fmt_frame = ttk.LabelFrame(settings_frame, text="Format", padding=4)
        fmt_frame.pack(side="left", padx=(0, 10))
        self._var_format = tk.StringVar(value="epub")
        ttk.Radiobutton(fmt_frame, text="EPUB", variable=self._var_format,
                         value="epub").pack(side="left", padx=5)
        ttk.Radiobutton(fmt_frame, text="TXT", variable=self._var_format,
                         value="txt").pack(side="left", padx=5)
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self._var_format,
                         value="pdf").pack(side="left", padx=5)

        # Threads
        thr_frame = ttk.LabelFrame(settings_frame, text="Threads", padding=4)
        thr_frame.pack(side="left", padx=(0, 10))
        self._var_ext_threads = tk.IntVar(value=4)
        ttk.Spinbox(thr_frame, from_=1, to=16, textvariable=self._var_ext_threads,
                     width=3).pack(side="left")

        # Interval
        int_frame = ttk.LabelFrame(settings_frame, text="Rate Limiting", padding=4)
        int_frame.pack(side="left", padx=(0, 10))
        ttk.Label(int_frame, text="Interval (s):").pack(side="left", padx=(0, 3))
        self._var_interval = tk.DoubleVar(value=0.5)
        spn = ttk.Spinbox(int_frame, from_=0.0, to=30.0, increment=0.1,
                           textvariable=self._var_interval, width=5)
        spn.pack(side="left")

        # Range
        rng_frame = ttk.LabelFrame(settings_frame, text="Chapter Range", padding=4)
        rng_frame.pack(side="left")
        self._var_from_enabled = tk.BooleanVar(value=False)
        self._var_to_enabled = tk.BooleanVar(value=False)
        self._var_from = tk.IntVar(value=1)
        self._var_to = tk.IntVar(value=1)

        chk_from = ttk.Checkbutton(rng_frame, text="From:", variable=self._var_from_enabled)
        chk_from.pack(side="left")
        self._spn_from = ttk.Spinbox(rng_frame, from_=1, to=99999,
                                      textvariable=self._var_from, width=5,
                                      state="disabled")
        self._spn_from.pack(side="left", padx=(0, 10))
        self._var_from_enabled.trace_add("write", lambda *_: self._spn_from.configure(
            state="normal" if self._var_from_enabled.get() else "disabled"
        ))

        chk_to = ttk.Checkbutton(rng_frame, text="To:", variable=self._var_to_enabled)
        chk_to.pack(side="left")
        self._spn_to = ttk.Spinbox(rng_frame, from_=1, to=99999,
                                    textvariable=self._var_to, width=5,
                                    state="disabled")
        self._spn_to.pack(side="left")
        self._var_to_enabled.trace_add("write", lambda *_: self._spn_to.configure(
            state="normal" if self._var_to_enabled.get() else "disabled"
        ))

        # --- Buttons ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=5)

        self._btn_fetch = ttk.Button(btn_frame, text="Fetch Info",
                                      command=self._on_fetch)
        self._btn_fetch.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_download = ttk.Button(btn_frame, text="Download",
                                         command=self._on_download,
                                         state="disabled")
        self._btn_download.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_stop = ttk.Button(btn_frame, text="Stop",
                                     command=self._on_stop, state="disabled")
        self._btn_stop.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_browser = ttk.Button(btn_frame, text="Enter Browser",
                                        command=self._on_enter_browser)
        self._btn_browser.pack(side="left", padx=(0, 5), ipady=3)

        # --- Book Info ---
        info_frame = ttk.LabelFrame(self, text="Book Info", padding=6)
        info_frame.pack(fill="x", padx=10, pady=5)

        grid_info = ttk.Frame(info_frame)
        grid_info.pack(fill="x")

        ttk.Label(grid_info, text="Title:").grid(row=0, column=0, sticky="w")
        self._lbl_title = ttk.Label(grid_info, text="—", wraplength=500)
        self._lbl_title.grid(row=0, column=1, sticky="w", padx=5)

        ttk.Label(grid_info, text="Author:").grid(row=1, column=0, sticky="w")
        self._lbl_author = ttk.Label(grid_info, text="—")
        self._lbl_author.grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(grid_info, text="Chapters:").grid(row=2, column=0, sticky="w")
        self._lbl_chapters = ttk.Label(grid_info, text="—")
        self._lbl_chapters.grid(row=2, column=1, sticky="w", padx=5)

        grid_info.columnconfigure(1, weight=1)

        # --- Console ---
        self._console = tk.Text(self, state="disabled", wrap="word",
                                bg="#f0f0f0", relief="flat", height=12)
        self._console.pack(fill="both", expand=True, padx=10, pady=5)

        # --- Progress ---
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", padx=10, pady=(0, 10))
        self._progress = ttk.Progressbar(prog_frame, mode="determinate")
        self._progress.pack(side="left", fill="x", expand=True)
        self._lbl_eta = ttk.Label(prog_frame, text="")
        self._lbl_eta.pack(side="left", padx=(5, 0))

    # ------------------------------------------------------------------
    # Thread-safe logging via queue
    # ------------------------------------------------------------------
    def _log(self, text):
        """Thread-safe: enqueue a log message."""
        self._msg_queue.put(("log", text))

    def _poll_queue(self):
        """Drain the message queue and update the UI."""
        try:
            while True:
                kind, data = self._msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "book_parsed":
                    self._on_book_parsed(data)
                elif kind == "progress":
                    self._update_progress(*data)
                elif kind == "finished":
                    self._on_download_finished()
                elif kind == "browser_closed":
                    self._on_browser_closed()
                elif kind == "error":
                    self._append_log(f"\u274c Error: {data}")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll_queue)

    def _append_log(self, text):
        self._console.configure(state="normal")
        self._console.insert("end", text + "\n")
        self._console.see("end")
        self._console.configure(state="disabled")

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
            elif kind == "browser":
                self._do_open_browser()

    def _do_fetch(self, url):
        """Run parse_book on the worker thread."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
                self._scraper.start()

            data = self._scraper.parse_book(url)
            if data:
                self._msg_queue.put(("book_parsed", data))
            else:
                self._msg_queue.put(("error", "Failed to parse book info."))
        except Exception as e:
            self._msg_queue.put(("error", str(e)))
        finally:
            self.after(0, lambda: self._btn_fetch.configure(state="normal"))

    def _do_download(self, chapters, start, end, interval, num_threads=1):
        """Run chapter downloads on the worker thread.

        Chapters are split into batches of `num_threads` size.
        Each batch fires concurrent HTTP requests inside the browser
        via JS Promise.all — no Python threading needed.
        Failed chapters are retried individually after the main pass.
        """
        try:
            selected = chapters[start:end]
            total = len(selected)
            results = [None] * total
            batch_size = max(1, num_threads)
            completed = 0

            for batch_start in range(0, total, batch_size):
                if not self._downloading:
                    self._log("Download stopped by user.")
                    break

                batch_end = min(batch_start + batch_size, total)
                batch = selected[batch_start:batch_end]

                # Log which chapters we're fetching
                for i, ch in enumerate(batch):
                    idx = batch_start + i
                    name = ch.get('name', f'Chapter {start + idx + 1}')
                    self._log(f"  [{idx + 1}/{total}] {name}")

                # Fire batch concurrently in JS
                batch_results = self._scraper.parse_chapter_batch(
                    batch, interval=interval
                )

                for i, data in enumerate(batch_results):
                    results[batch_start + i] = data

                completed += len(batch)
                self._msg_queue.put(("progress", (completed, total)))

                # Rate limiting between batches
                if interval > 0 and batch_end < total:
                    time.sleep(interval)

            # --- Retry failed chapters individually ---
            failed_indices = [i for i, r in enumerate(results) if r is None
                              and self._downloading]
            if failed_indices and self._downloading:
                max_retries = 2
                for retry_pass in range(1, max_retries + 1):
                    if not failed_indices or not self._downloading:
                        break
                    self._log(
                        f"Retry pass {retry_pass}/{max_retries}: "
                        f"{len(failed_indices)} failed chapter(s)..."
                    )
                    still_failed = []
                    for idx in failed_indices:
                        if not self._downloading:
                            break
                        ch = selected[idx]
                        name = ch.get('name', f'Chapter {start + idx + 1}')
                        self._log(f"  Retrying [{idx + 1}/{total}] {name}")
                        # Use longer interval for retries
                        data = self._scraper.parse_chapter(
                            start + idx, ch, interval=max(interval, 1.0)
                        )
                        if data:
                            results[idx] = data
                            self._log(f"  Retry OK: {name}")
                        else:
                            still_failed.append(idx)
                    failed_indices = still_failed

                if failed_indices:
                    self._log(
                        f"WARNING: {len(failed_indices)} chapter(s) failed "
                        f"after all retries."
                    )

            self._chapter_results = results
            self._msg_queue.put(("finished", None))
        except Exception as e:
            self._msg_queue.put(("error", f"Download error: {e}"))
            self._msg_queue.put(("finished", None))

    def _do_open_browser(self):
        """Open a visible browser on the worker thread for manual login."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            self._scraper.open_visible_browser()
        except Exception as e:
            self._msg_queue.put(("error", f"Browser error: {e}"))
        finally:
            self._msg_queue.put(("browser_closed", None))

    # ------------------------------------------------------------------
    # Enter Browser (manual login)
    # ------------------------------------------------------------------
    def _on_enter_browser(self):
        """Open a visible browser window for manual site login."""
        self._btn_fetch.configure(state="disabled")
        self._btn_download.configure(state="disabled")
        self._btn_browser.configure(state="disabled")
        self._append_log("Opening browser for login... Close the browser when done.")
        self._work_queue.put(("browser", None))

    def _on_browser_closed(self):
        """Re-enable buttons after the visible browser session ends."""
        self._btn_fetch.configure(state="normal")
        self._btn_browser.configure(state="normal")
        if self._book_data:
            self._btn_download.configure(state="normal")
        self._append_log("Browser session ended. Session data saved.")

    # ------------------------------------------------------------------
    # Fetch Info
    # ------------------------------------------------------------------
    def _on_fetch(self):
        url = self._url_var.get().strip()
        if not url:
            self._append_log("Please enter a URL.")
            return
        if not url.startswith("http"):
            url = "https://" + url
            self._url_var.set(url)

        self._btn_fetch.configure(state="disabled")
        self._btn_download.configure(state="disabled")
        self._lbl_title.configure(text="Fetching...")
        self._lbl_author.configure(text="—")
        self._lbl_chapters.configure(text="—")

        self._fetch_worker(url)

    def _fetch_worker(self, url):
        """Dispatch fetch to the persistent worker thread."""
        self._work_queue.put(("fetch", url))

    def _on_book_parsed(self, data):
        self._book_data = data
        self._lbl_title.configure(text=data.get('bookname', '?'))
        self._lbl_author.configure(text=data.get('author', '?'))
        chapter_count = data.get('chapterCount', 0)
        self._lbl_chapters.configure(text=str(chapter_count))

        if chapter_count > 0:
            # Only auto-fill 'To' if the user hasn't explicitly set a range
            if not self._var_to_enabled.get():
                self._var_to.set(chapter_count)
            self._btn_download.configure(state="normal")

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def _on_download(self):
        if not self._book_data:
            return

        self._downloading = True
        self._btn_fetch.configure(state="disabled")
        self._btn_download.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._progress['value'] = 0
        self._chapter_results = []
        self._start_time = time.time()

        chapters = self._book_data.get('chapters', [])
        start = 0
        end = len(chapters)
        if self._var_from_enabled.get():
            start = max(0, self._var_from.get() - 1)
        if self._var_to_enabled.get():
            end = min(len(chapters), self._var_to.get())

        interval = self._var_interval.get()
        num_threads = max(1, self._var_ext_threads.get())
        self._log(f"Starting download: chapters {start + 1}\u2013{end}, "
                  f"threads={num_threads}, interval={interval}s")

        self._download_worker(chapters, start, end, interval, num_threads)

    def _download_worker(self, chapters, start, end, interval, num_threads):
        """Dispatch download to the persistent worker thread."""
        self._work_queue.put(("download", (chapters, start, end, interval, num_threads)))

    def _update_progress(self, current, total):
        if total > 0:
            pct = int(current / total * 100)
            self._progress['maximum'] = total
            self._progress['value'] = current

            if self._start_time and current > 0:
                elapsed = time.time() - self._start_time
                per_chapter = elapsed / current
                remaining = (total - current) * per_chapter
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                self._lbl_eta.configure(
                    text=f"{current}/{total} ({pct}%) — ETA: {mins}m {secs}s"
                )

    def _on_download_finished(self):
        self._downloading = False
        self._btn_fetch.configure(state="normal")
        self._btn_download.configure(state="normal" if self._book_data else "disabled")
        self._btn_stop.configure(state="disabled")
        self._lbl_eta.configure(text="")

        if not self._chapter_results:
            self._log("No chapters downloaded.")
            return

        successes = sum(1 for r in self._chapter_results if r is not None)
        failures = len(self._chapter_results) - successes
        self._log(f"Download complete: {successes} succeeded, {failures} failed.")

        if successes > 0:
            self._generate_output()

    # ------------------------------------------------------------------
    # Output Generation
    # ------------------------------------------------------------------
    def _generate_output(self):
        """Generate EPUB/TXT from downloaded chapter data."""
        data = self._book_data
        if not data:
            return

        title = _sanitize_filename(data.get('bookname', 'novel'))
        author = data.get('author', 'Unknown')

        fmt = self._var_format.get()
        if fmt == "txt":
            self._generate_txt(title, author)
        elif fmt == "pdf":
            self._generate_pdf(title, author)
        else:
            self._generate_epub(title, author)

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

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"{title}\n")
                f.write(f"Author: {author}\n")
                f.write("=" * 60 + "\n\n")

                for i, ch_data in enumerate(self._chapter_results):
                    if ch_data is None:
                        continue
                    ch_name = ch_data.get('chapterName', f'Chapter {i + 1}')
                    content = ch_data.get('contentText', '')
                    f.write(f"\n{'─' * 40}\n")
                    f.write(f"{ch_name}\n")
                    f.write(f"{'─' * 40}\n\n")
                    f.write(content + "\n")

            self._log(f"\u2705 Saved: {filepath}")
        except Exception as e:
            self._log(f"\u274c TXT generation failed: {e}")

    def _generate_epub(self, title, author):
        """Generate an EPUB file using the existing epub_generator."""
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
        zip_compress = getattr(pg, 'var_zip_compress_images', None)
        zip_compress = zip_compress.get() if zip_compress else False

        # Cover-specific compression settings
        compress_cover = getattr(pg, 'var_compress_cover', None)
        compress_cover = compress_cover.get() if compress_cover else False
        cover_quality = getattr(pg, 'var_cover_quality', None)
        cover_quality = cover_quality.get() if cover_quality else 90
        cover_format = getattr(pg, 'var_cover_format', None)
        cover_format = cover_format.get() if cover_format else 'JPEG'

        self._log(f"  Image compression: {'ON' if compress_images else 'OFF'}"
                  f" ({image_format} q{jpeg_quality})")
        self._log(f"  Cover compression: {'ON' if compress_cover else 'OFF'}"
                  f" ({cover_format} q{cover_quality})")

        # Default CSS for external novels
        css = """body { margin: 2%; }
p { overflow-wrap: break-word; }
h1, h2 { text-align: center; margin-bottom: 10%; margin-top: 10%; }
h3, h4, h5, h6 { text-align: center; margin-bottom: 15%; margin-top: 10%; }
img { display: block; max-width: 100%; max-height: 100%;
      margin-left: auto; margin-right: auto; margin-bottom: 2%; margin-top: 2%; }
"""

        data = self._book_data
        cover_url = data.get('coverUrl', '')

        metadata = {
            'title': data.get('bookname', title),
            'author': data.get('author', author),
            'description': data.get('introduction', ''),
            'tags': data.get('tags', []),
            'language': data.get('language', 'zh'),
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
        img_counter = [0]

        try:
            epub = EpubGenerator(metadata, filepath, css, zip_compress)

            # Download and add cover image via the correct API
            # (EpubGenerator looks for images named 'cover.*')
            cover_added = False
            if cover_url:
                cover_bytes = self._download_image_python(
                    cover_url, "Cover", session=img_session
                )
                if cover_bytes:
                    ext = 'jpg'
                    if cover_bytes[:4] == b'\x89PNG':
                        ext = 'png'
                    elif cover_bytes[:4] == b'RIFF':
                        ext = 'webp'
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
            if intro_html:
                intro_page = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
  "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Introduction</title>
<link href="../Styles/style.css" type="text/css" rel="stylesheet"/>
</head>
<body>
<h2>{html.escape(data.get('bookname', title))}</h2>
<h3>{html.escape(data.get('author', author))}</h3>
<hr/>
{intro_html}
</body>
</html>"""
                epub.add_extra_page('info.xhtml', intro_page)

            for i, ch_data in enumerate(self._chapter_results):
                if ch_data is None:
                    continue
                ch_name = ch_data.get('chapterName', f'Chapter {i + 1}')
                content_html = ch_data.get('contentHtml', '')

                # Process images: use browser-provided base64 data when
                # available, fall back to Python-side download otherwise.
                rename_map = {}  # original_name → compressed_name
                if ch_data.get('images'):
                    for img_info in ch_data['images']:
                        img_url = img_info.get('url', '')
                        img_data_url = img_info.get('data')
                        raw = None

                        # Try browser-provided base64 data first
                        if img_data_url and ',' in img_data_url:
                            _, b64 = img_data_url.split(',', 1)
                            try:
                                raw = base64.b64decode(b64)
                            except Exception:
                                pass

                        # Fall back to Python-side download
                        if not raw and img_url:
                            raw = self._download_image_python(
                                img_url,
                                f"Ch{i+1} image",
                                session=img_session
                            )

                        if raw:
                            img_counter[0] += 1
                            orig_name = img_info.get(
                                'name', f'img_{i}_{img_counter[0]}'
                            )
                            img_name = orig_name
                            # Apply compression if enabled
                            if compress_images:
                                raw = self._compress_image(
                                    raw, jpeg_quality, image_format
                                )
                                # Update extension to match compressed format
                                new_ext = image_format.lower()
                                if new_ext == 'jpeg':
                                    new_ext = 'jpg'
                                base = orig_name.rsplit('.', 1)[0]
                                img_name = f'{base}.{new_ext}'
                                if img_name != orig_name:
                                    rename_map[orig_name] = img_name
                            epub.add_image(img_name, raw)
                            # Replace the URL in content_html with the
                            # local EPUB path so the image actually renders
                            if img_url and content_html:
                                content_html = content_html.replace(
                                    img_url, f'../Images/{img_name}'
                                )

                # Also handle any <img src="..."> in contentHtml that
                # weren't in the images array (e.g. inline images)
                content_html = self._download_inline_images(
                    content_html, epub, img_session, img_counter,
                    compress_images, jpeg_quality, image_format, i
                )

                # Fix novel-downloader's img format for EPUB rendering:
                # cleanDOM outputs <img data-src-address="name.jpeg" alt="..."
                # title="..."> with NO src attribute.  EPUB readers need src.
                content_html = self._fix_nd_img_tags(
                    content_html, rename_map
                )

                epub.add_chapter(ch_name, content_html)

            # Fallback: if no cover was found, use the first chapter
            # image larger than 400px as the cover.
            if not cover_added and epub.images:
                self._log("No cover URL found, scanning chapter images...")
                cover_added = self._try_cover_from_images(
                    epub, compress_images, jpeg_quality, image_format
                )

            epub.generate()
            self._log(f"✅ Saved: {filepath}")

        except Exception as e:
            self._log(f"❌ EPUB generation failed: {e}")
            self._log("Falling back to TXT output...")
            self._generate_txt(title, author)
        finally:
            img_session.close()

    def _download_image_python(self, url, label="Image", session=None,
                               max_retries=3):
        """Download an image using requests (no CORS/mixed-content issues).

        Automatically upgrades http:// to https:// and retries on failure.
        Returns raw bytes or None.
        """
        import requests as _req

        # Upgrade http to https (many sites serve images on http but
        # support https — and some block http entirely)
        if url.startswith('http://'):
            url = url.replace('http://', 'https://', 1)

        sess = session or _req
        for attempt in range(max_retries):
            try:
                r = sess.get(url, timeout=15)
                if r.status_code == 200 and len(r.content) > 100:
                    self._log(f"  📷 {label}: OK ({len(r.content)} bytes)")
                    return r.content
                # Try original http:// URL as fallback on last attempt
                if attempt == max_retries - 2 and url.startswith('https://'):
                    url = url.replace('https://', 'http://', 1)
            except Exception as e:
                if attempt == max_retries - 1:
                    self._log(f"  📷 {label}: FAILED ({e})")
        return None

    def _download_inline_images(self, html_str, epub, session, counter,
                                compress, quality, fmt, ch_idx):
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
                url, f"Ch{ch_idx+1} inline img", session=session
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
                name = f'img_{ch_idx}_{counter[0]}.{ext}'
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

        data = self._book_data
        metadata = {
            'title': data.get('bookname', title),
            'author': data.get('author', author),
        }

        # Build chapter list and image map
        chapters_for_pdf = []
        image_map = {}
        for i, ch_data in enumerate(self._chapter_results):
            if ch_data is None:
                continue
            ch_name = ch_data.get('chapterName', f'Chapter {i + 1}')
            content_html = ch_data.get('contentHtml', '')

            # Process images
            if ch_data.get('images'):
                import base64
                for img_info in ch_data['images']:
                    img_data_url = img_info.get('data')
                    if img_data_url and ',' in img_data_url:
                        _, b64 = img_data_url.split(',', 1)
                        try:
                            raw = base64.b64decode(b64)
                            img_name = img_info.get('name', f'img_{i}')
                            image_map[img_name] = raw
                        except Exception:
                            pass

            chapters_for_pdf.append({
                "title": ch_name,
                "html": content_html,
                "is_notice": False,
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
                show_toc=show_toc,
                show_page_numbers=show_pages,
                use_counter_layout=counter_layout,
            )
            self._log(f"\u2705 Saved: {filepath}")
        except Exception as e:
            self._log(f"\u274c PDF generation failed: {e}")
            self._log("Falling back to TXT output...")
            self._generate_txt(title, author)

    # ------------------------------------------------------------------
    # Stop / Close
    # ------------------------------------------------------------------
    def _on_stop(self):
        self._downloading = False
        if self._scraper:
            self._scraper._stop_requested = True
        self._btn_stop.configure(state="disabled")
        self._log("Stop requested.")

    def _on_close(self):
        self._save_ext_config()
        self._downloading = False
        if self._scraper:
            self._scraper._stop_requested = True
        # Signal the worker thread to exit and clean up the browser
        self._work_queue.put(("quit", None))
        if self._scraper:
            threading.Thread(
                target=self._scraper.cleanup, daemon=True
            ).start()
        self.destroy()

    # ------------------------------------------------------------------
    # Settings Persistence
    # ------------------------------------------------------------------
    def _load_ext_config(self):
        """Load external dialog settings from config.json (ext_* keys)."""
        cfg_path = os.path.join(_get_base_dir(), "config.json")
        if not os.path.exists(cfg_path):
            return
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._url_var.set(cfg.get("ext_url", ""))
            self._var_format.set(cfg.get("ext_format", "epub"))
            self._var_ext_threads.set(cfg.get("ext_threads", 4))
            self._var_interval.set(cfg.get("ext_interval", 0.5))
            self._var_from_enabled.set(cfg.get("ext_from_enabled", False))
            self._var_to_enabled.set(cfg.get("ext_to_enabled", False))
            self._var_from.set(cfg.get("ext_from", 1))
            self._var_to.set(cfg.get("ext_to", 1))
        except Exception:
            pass

    def _save_ext_config(self):
        """Save external dialog settings into config.json (ext_* keys).

        Merges with the existing config so the main GUI's settings are
        not overwritten.
        """
        cfg_path = os.path.join(_get_base_dir(), "config.json")
        cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        cfg["ext_url"] = self._url_var.get()
        cfg["ext_format"] = self._var_format.get()
        cfg["ext_threads"] = self._var_ext_threads.get()
        cfg["ext_interval"] = self._var_interval.get()
        cfg["ext_from_enabled"] = self._var_from_enabled.get()
        cfg["ext_to_enabled"] = self._var_to_enabled.get()
        cfg["ext_from"] = self._var_from.get()
        cfg["ext_to"] = self._var_to.get()
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass
