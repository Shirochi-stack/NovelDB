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
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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
        self.minsize(int(screen_w * 0.35), int(screen_h * 0.40))

        self._parent_gui = parent      # Access compression settings from main GUI
        self._scraper = None
        self._book_data = None
        self._chapter_results = []
        self._downloading = False
        self._start_time = None
        self._msg_queue = queue.Queue()
        self._paste_batch_text = ''      # persisted between dialog opens
        self._paste_batch_dialog = None

        # Single persistent worker thread for all Playwright calls.
        # Playwright's sync API is thread-bound, so fetch + download
        # must both run on the same thread.
        self._work_queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True
        )
        self._worker_thread.start()

        self._build_ui()

        # Hide on close instead of destroying — state is preserved.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_ext_config()
        self._poll_queue()

    def _center_child_window(self, child):
        """Center a child dialog relative to this external dialog."""
        try:
            child.update_idletasks()
            width = child.winfo_width()
            height = child.winfo_height()
            if width <= 1 or height <= 1:
                req_width = child.winfo_reqwidth()
                req_height = child.winfo_reqheight()
                width = max(width, req_width)
                height = max(height, req_height)
            parent_x = self.winfo_rootx()
            parent_y = self.winfo_rooty()
            parent_w = self.winfo_width()
            parent_h = self.winfo_height()
            x = parent_x + max(0, (parent_w - width) // 2)
            y = parent_y + max(0, (parent_h - height) // 2)
            screen_w = child.winfo_screenwidth()
            screen_h = child.winfo_screenheight()
            x = max(0, min(x, screen_w - width))
            y = max(0, min(y, screen_h - height))
            child.geometry(f"+{x}+{y}")
        except Exception:
            pass

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
        ttk.Radiobutton(fmt_frame, text="CBZ", variable=self._var_format,
                         value="cbz").pack(side="left", padx=5)

        # Threads
        thr_frame = ttk.LabelFrame(settings_frame, text="Threads", padding=4)
        thr_frame.pack(side="left", padx=(0, 10))
        self._var_ext_threads = tk.IntVar(value=4)
        ttk.Spinbox(thr_frame, from_=1, to=16, textvariable=self._var_ext_threads,
                     width=3).pack(side="left")

        # EPUB image workers
        img_thr_frame = ttk.LabelFrame(settings_frame, text="Image Workers", padding=4)
        img_thr_frame.pack(side="left", padx=(0, 10))
        self._var_ext_image_workers = tk.IntVar(value=8)
        ttk.Spinbox(
            img_thr_frame, from_=1, to=32,
            textvariable=self._var_ext_image_workers, width=3
        ).pack(side="left")

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

        # Skip paid
        self._var_skip_paid = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Skip paid",
            variable=self._var_skip_paid,
        ).pack(side="left", padx=(10, 0))
        self._var_ntk_novelpia_cover = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame,
            text="Prefer source cover",
            variable=self._var_ntk_novelpia_cover,
        ).pack(side="left", padx=(10, 0))
        self._var_kakao_skip_last_page = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Skip last page",
            variable=self._var_kakao_skip_last_page,
        ).pack(side="left", padx=(10, 0))
        self._var_kakao_keep_filler = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Keep filler",
            variable=self._var_kakao_keep_filler,
        ).pack(side="left", padx=(10, 0))
        dedupe_frame = ttk.Frame(settings_frame)
        dedupe_frame.pack(side="left", padx=(10, 0))
        dedupe_top_frame = ttk.Frame(dedupe_frame)
        dedupe_top_frame.pack(anchor="w")
        self._var_kakao_dedupe_images = tk.BooleanVar(value=True)
        self._var_kakao_dedupe_leading_images = tk.IntVar(value=2)
        ttk.Checkbutton(
            dedupe_top_frame, text="Dedupe images",
            variable=self._var_kakao_dedupe_images,
        ).pack(side="left")
        self._spn_kakao_dedupe_leading = ttk.Spinbox(
            dedupe_top_frame, from_=1, to=10,
            textvariable=self._var_kakao_dedupe_leading_images, width=3
        )
        self._spn_kakao_dedupe_leading.pack(side="left", padx=(3, 0))
        self._var_kakao_dedupe_images.trace_add(
            "write",
            lambda *_: self._spn_kakao_dedupe_leading.configure(
                state=(
                    "normal" if self._var_kakao_dedupe_images.get()
                    else "disabled"
                )
            )
        )
        self._var_long_image_layout = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            dedupe_frame,
            text="Long image layout",
            variable=self._var_long_image_layout,
        ).pack(anchor="w")
        self._var_syosetu_amazon_cover = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            dedupe_frame,
            text="Syosetu Amazon cover",
            variable=self._var_syosetu_amazon_cover,
        ).pack(anchor="w")

        # --- Action Buttons ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=(5, 2))

        self._btn_download = ttk.Button(btn_frame, text="Download",
                                         command=self._on_download)
        self._btn_download.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_stop = ttk.Button(btn_frame, text="Stop",
                                     command=self._on_stop, state="disabled")
        self._btn_stop.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_browser = ttk.Button(btn_frame, text="Enter Browser",
                                        command=self._on_enter_browser)
        self._btn_browser.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_sfacg_app = ttk.Button(btn_frame, text="Enter App",
                                          command=self._on_sfacg_app_login)
        self._btn_sfacg_app.pack(side="left", padx=(0, 5), ipady=3)

        self._var_regular_browser = tk.BooleanVar(value=False)
        self._chk_regular_browser = ttk.Checkbutton(
            btn_frame,
            text="Regular browser",
            variable=self._var_regular_browser,
        )
        self._chk_regular_browser.pack(side="left", padx=(0, 10))

        self._btn_paste_batch = ttk.Button(btn_frame, text="Paste Batch",
                                            command=self._on_paste_batch)
        self._btn_paste_batch.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_batch_file = ttk.Button(btn_frame, text="Batch Download",
                                           command=self._on_batch_file)
        self._btn_batch_file.pack(side="left", padx=(0, 5), ipady=3)

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

    def _apply_scraper_options(self):
        """Copy current dialog options into the scraper instance."""
        if not self._scraper:
            return
        self._scraper.kakao_skip_last_page = (
            self._var_kakao_skip_last_page.get()
        )
        self._scraper.kakao_keep_filler = self._var_kakao_keep_filler.get()
        self._scraper.ntk_prefer_novelpia_cover = (
            self._var_ntk_novelpia_cover.get()
        )
        self._scraper.syosetu_amazon_cover_fallback = (
            self._var_syosetu_amazon_cover.get()
        )

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
                elif kind == "sfacg_app_done":
                    self._on_sfacg_app_done(data)
                elif kind == "sfacg_android_done":
                    self._on_sfacg_android_done(data)
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
            elif kind == "sfacg_app_login":
                self._do_sfacg_app_login(payload)
            elif kind == "sfacg_android_open":
                self._do_sfacg_android_open()
            elif kind == "sfacg_android_import":
                self._do_sfacg_android_import()

    def _do_fetch(self, url):
        """Run parse_book on the worker thread."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
                if (not self._scraper.is_ntk_novel(url)
                        and not self._scraper.is_qidian(url)
                        and not self._scraper.is_yeduji(url)):
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
            self.after(0, lambda: self._btn_download.configure(state="normal"))

    def _do_download(self, chapters, start, end, interval, num_threads=1,
                      skip_paid=False):
        """Run chapter downloads on the worker thread.

        Chapters are split into batches of `num_threads` size.
        Each batch fires concurrent HTTP requests inside the browser
        via JS Promise.all — no Python threading needed.
        Failed chapters are retried individually after the main pass.
        """
        try:
            # Ensure the headless browser is alive.  After "Enter Browser"
            # the context is destroyed; start() re-launches it with fresh
            # cookies from browser_data (including any new login sessions).
            is_ntk = bool(
                self._book_data and self._book_data.get("_ntk_novel")
            )
            is_yeduji = bool(
                self._book_data and self._book_data.get("_yeduji")
            )
            if (self._scraper and not self._scraper._context
                    and not is_ntk and not is_yeduji
                    and not (self._book_data and self._book_data.get('_qidian'))):
                self._scraper.start()
                # Navigate to the book page so that JS fetch() calls
                # originate from the kakao.com domain.  The BFF API
                # returns 403 Forbidden for requests from about:blank.
                if (self._scraper._page and self._book_data
                        and self._book_data.get('_kakaopage')
                        and self._scraper._book_url):
                    try:
                        self._scraper._page.goto(
                            self._scraper._book_url,
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        self._scraper._page.wait_for_timeout(1000)
                    except Exception:
                        pass
            self._apply_scraper_options()

            selected = chapters[start:end]
            total = len(selected)
            results = [None] * total
            batch_size = max(1, num_threads)
            completed = 0

            # Pre-filter paid chapters if the user opted to skip them.
            # For Kakao, skip only rows that the product list marks as not
            # accessible to this account. Purchased/rented rows are kept.
            if skip_paid:
                skipped = 0
                for i, ch in enumerate(selected):
                    if (ch.get('isVIP', False)
                            and not ch.get('isAccessible', False)):
                        results[i] = {'_locked': True,
                                      'chapterName': ch.get('name', '')}
                        skipped += 1
                if skipped:
                    self._log(f"  Skipped {skipped} paid chapter(s).")

            for batch_start in range(0, total, batch_size):
                if not self._downloading:
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

                # Log which chapters we're fetching
                for i in batch_indices:
                    name = selected[i].get('name', f'Chapter {start + i + 1}')
                    self._log(f"  [{i + 1}/{total}] {name}")

                # Fire batch concurrently in JS
                batch_results = self._scraper.parse_chapter_batch(
                    batch, interval=interval
                )

                for j, data in enumerate(batch_results):
                    results[batch_indices[j]] = data

                completed += (batch_end - batch_start)
                self._msg_queue.put(("progress", (completed, total)))

                # Rate limiting between batches
                if interval > 0 and batch_end < total:
                    time.sleep(interval)

            # --- Retry failed chapters individually ---
            # Locked (paid) chapters are not retried — only truly failed ones.
            failed_indices = [
                i for i, r in enumerate(results)
                if r is None and self._downloading
            ]
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

            # --- Summary ---
            locked = sum(1 for r in results
                         if r and r.get('_locked'))
            succeeded = sum(1 for r in results
                            if r and not r.get('_locked'))
            failed = sum(1 for r in results if r is None)

            if locked:
                self._log(
                    f"Download complete: {succeeded} succeeded, "
                    f"{locked} locked (paid), {failed} failed."
                )
                self._log(
                    "⚠ Locked chapters require a subscription "
                    "or ticket purchase to access."
                )
            elif failed:
                self._log(
                    f"Download complete: {succeeded} succeeded, "
                    f"{failed} failed."
                )

            self._chapter_results = results
        except Exception as e:
            self._log(f"\u274c Download error: {e}")

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

    def _do_sfacg_app_login(self, payload):
        """Log in to SFACG's app API on the worker thread."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            cookie = payload.get("cookie", "")
            if cookie:
                ok = self._scraper.save_sfacg_app_cookie(cookie)
            else:
                ok = self._scraper.login_sfacg_app(
                    payload.get("username", ""),
                    payload.get("password", ""),
                )
            self._msg_queue.put(("sfacg_app_done", ok))
        except Exception as e:
            self._msg_queue.put(("error", f"SFACG app login error: {e}"))
            self._msg_queue.put(("sfacg_app_done", False))

    def _do_sfacg_android_open(self):
        """Open the configured Android emulator."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            ok = self._scraper.open_android_emulator()
            self._msg_queue.put(("sfacg_android_done", ok))
        except Exception as e:
            self._msg_queue.put(("error", f"Android emulator error: {e}"))
            self._msg_queue.put(("sfacg_android_done", False))

    def _do_sfacg_android_import(self):
        """Import SFACG app cookie from Android emulator app data."""
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
            ok = self._scraper.import_sfacg_app_cookie_from_android()
            self._msg_queue.put(("sfacg_app_done", ok))
        except Exception as e:
            self._msg_queue.put(("error", f"Android import error: {e}"))
            self._msg_queue.put(("sfacg_app_done", False))

    # ------------------------------------------------------------------
    # Enter Browser (manual login)
    # ------------------------------------------------------------------
    def _on_enter_browser(self):
        """Open a visible browser window for manual site login."""
        start_url = self._normalised_url()
        if start_url:
            self._url_var.set(start_url)
            self._save_ext_config()
        else:
            start_url = None

        self._btn_download.configure(state="disabled")
        self._btn_browser.configure(state="disabled")
        self._btn_sfacg_app.configure(state="disabled")
        self._chk_regular_browser.configure(state="disabled")
        self._btn_paste_batch.configure(state="disabled")
        self._btn_batch_file.configure(state="disabled")
        regular_browser = self._var_regular_browser.get()
        if regular_browser:
            self._append_log(
                "Opening regular browser for login... Close the browser "
                "when done."
            )
        else:
            self._append_log(
                "Opening browser for login... Close the browser when done."
            )
        self._work_queue.put(("browser", {
            "url": start_url,
            "regular": regular_browser,
        }))

    def _on_browser_closed(self):
        """Re-enable buttons after the visible browser session ends."""
        self._btn_browser.configure(state="normal")
        self._btn_sfacg_app.configure(state="normal")
        self._chk_regular_browser.configure(state="normal")
        self._btn_download.configure(state="normal")
        self._btn_paste_batch.configure(state="normal")
        self._btn_batch_file.configure(state="normal")
        self._append_log("Browser session ended. Session data saved.")

    def _on_sfacg_app_login(self):
        """Prompt for SFACG app credentials or an existing app cookie."""
        dlg = tk.Toplevel(self)
        dlg.title("SFACG App Session")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(True, False)

        body = ttk.Frame(dlg, padding=10)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Username:").grid(row=0, column=0, sticky="w")
        var_user = tk.StringVar()
        ent_user = ttk.Entry(body, textvariable=var_user, width=34)
        ent_user.grid(row=0, column=1, padx=(6, 0), pady=(0, 6))

        ttk.Label(body, text="Password:").grid(row=1, column=0, sticky="w")
        var_pass = tk.StringVar()
        ent_pass = ttk.Entry(body, textvariable=var_pass, width=34, show="*")
        ent_pass.grid(row=1, column=1, padx=(6, 0), pady=(0, 8))

        ttk.Label(body, text="App cookie:").grid(
            row=2, column=0, sticky="nw"
        )
        txt_cookie = tk.Text(body, width=46, height=4, wrap="word")
        txt_cookie.grid(row=2, column=1, padx=(6, 0), pady=(0, 8))

        ttk.Label(
            body,
            text=(
                "Use username/password, or paste .SFCommunity=...; "
                "session_APP=..."
            ),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))

        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky="e")

        def submit():
            username = var_user.get().strip()
            password = var_pass.get()
            cookie = txt_cookie.get("1.0", "end").strip()
            if not cookie and (not username or not password):
                messagebox.showwarning(
                    "SFACG App Session",
                    "Enter username/password or paste an app cookie.",
                    parent=dlg,
                )
                return
            dlg.destroy()
            if cookie:
                self._append_log("Importing SFACG app cookie...")
            else:
                self._append_log("Logging in to SFACG app API...")
            self._btn_download.configure(state="disabled")
            self._btn_browser.configure(state="disabled")
            self._btn_sfacg_app.configure(state="disabled")
            self._chk_regular_browser.configure(state="disabled")
            self._btn_paste_batch.configure(state="disabled")
            self._btn_batch_file.configure(state="disabled")
            self._work_queue.put(("sfacg_app_login", {
                "username": username,
                "password": password,
                "cookie": cookie,
            }))

        ttk.Button(btns, text="Save", command=submit).pack(
            side="right", padx=(5, 0)
        )
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        ttk.Button(
            btns,
            text="Import Android",
            command=lambda: (dlg.destroy(), self._on_sfacg_android_import()),
        ).pack(side="left")
        ttk.Button(
            btns,
            text="Open Android",
            command=lambda: self._on_sfacg_android_open(),
        ).pack(side="left", padx=(0, 5))
        ent_user.focus_set()
        dlg.bind("<Return>", lambda _e: submit())
        self._center_child_window(dlg)

    def _set_app_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self._btn_browser.configure(state=state)
        self._btn_sfacg_app.configure(state=state)
        self._chk_regular_browser.configure(state=state)
        self._btn_download.configure(state=state)
        self._btn_paste_batch.configure(state=state)
        self._btn_batch_file.configure(state=state)

    def _on_sfacg_android_open(self):
        self._append_log("Opening Android emulator...")
        self._set_app_buttons_enabled(False)
        self._work_queue.put(("sfacg_android_open", None))

    def _on_sfacg_android_import(self):
        self._append_log("Trying to import SFACG app session from Android...")
        self._set_app_buttons_enabled(False)
        self._work_queue.put(("sfacg_android_import", None))

    def _on_sfacg_android_done(self, ok):
        self._set_app_buttons_enabled(True)
        if ok:
            self._append_log(
                "Android emulator opened. Install/open SFACG, log in, "
                "then use Enter App > Import Android."
            )
        else:
            self._append_log("Android emulator action failed.")

    def _on_sfacg_app_done(self, ok):
        self._set_app_buttons_enabled(True)
        if ok:
            self._append_log("SFACG app session saved.")
        else:
            self._append_log("SFACG app login failed.")

    # ------------------------------------------------------------------
    # Fetch Info (internal helper — called from workers)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Download (combined fetch → download)
    # ------------------------------------------------------------------
    def _normalised_url(self):
        """Return the current URL field value with an http scheme if needed."""
        url = self._url_var.get().strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _on_download(self):
        url = self._normalised_url()
        if not url:
            self._append_log("Please enter a URL.")
            return
        self._url_var.set(url)
        self._save_ext_config()

        self._downloading = True
        self._btn_download.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._btn_browser.configure(state="disabled")
        self._btn_sfacg_app.configure(state="disabled")
        self._btn_paste_batch.configure(state="disabled")
        self._btn_batch_file.configure(state="disabled")
        self._progress['value'] = 0
        self._chapter_results = []
        self._start_time = time.time()

        self._lbl_title.configure(text="Fetching...")
        self._lbl_author.configure(text="—")
        self._lbl_chapters.configure(text="—")

        interval = self._var_interval.get()
        num_threads = max(1, self._var_ext_threads.get())
        skip_paid = self._var_skip_paid.get()

        self._work_queue.put(("fetch_and_download", (url, interval,
                                                      num_threads, skip_paid)))

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
        self._btn_download.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._btn_browser.configure(state="normal")
        self._btn_sfacg_app.configure(state="normal")
        self._btn_paste_batch.configure(state="normal")
        self._btn_batch_file.configure(state="normal")
        self._lbl_eta.configure(text="")

    # ------------------------------------------------------------------
    # Combined fetch + download worker
    # ------------------------------------------------------------------
    def _do_fetch_and_download(self, url, interval, num_threads, skip_paid):
        """Fetch metadata then download chapters — runs on worker thread."""
        # Step 1: Fetch metadata
        try:
            if self._scraper is None:
                self._scraper = ExternalScraper(logger=self._log)
                if (not self._scraper.is_ntk_novel(url)
                        and not self._scraper.is_qidian(url)
                        and not self._scraper.is_yeduji(url)):
                    self._scraper.start()
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

        # Step 2: Download chapters
        chapters = data.get('chapters', [])
        start = 0
        end = len(chapters)
        if self._var_from_enabled.get():
            start = max(0, self._var_from.get() - 1)
        if self._var_to_enabled.get():
            end = min(len(chapters), self._var_to.get())

        self._log(f"Starting download: chapters {start + 1}\u2013{end}, "
                  f"threads={num_threads}, interval={interval}s"
                  + (', skipping paid' if skip_paid else ''))

        self._do_download(chapters, start, end, interval, num_threads,
                          skip_paid)

        # Generate output and signal completion
        try:
            successes = sum(1 for r in self._chapter_results
                            if r is not None and not r.get('_locked'))
            if successes > 0:
                self._book_data = data
                self._log(f"Generating output from {successes} chapter(s)...")
                self._generate_output()
        except Exception as e:
            self._log(f"❌ Output generation error: {e}")
        finally:
            self._log("Finished.")
            self._msg_queue.put(("finished", None))

    # ------------------------------------------------------------------
    # Paste Batch
    # ------------------------------------------------------------------
    def _on_paste_batch(self):
        """Open a text dialog for pasting multiple URLs."""
        if self._paste_batch_dialog and self._paste_batch_dialog.winfo_exists():
            self._paste_batch_dialog.lift()
            return

        top = tk.Toplevel(self)
        top.title("Paste Batch — External Download")
        dw, dh = 600, 400
        sx = (top.winfo_screenwidth() - dw) // 2
        sy = (top.winfo_screenheight() - dh) // 2
        top.geometry(f"{dw}x{dh}+{sx}+{sy}")
        top.transient(self)
        self._paste_batch_dialog = top

        lbl = ttk.Label(top, text="Paste one URL per line:")
        lbl.pack(anchor="w", padx=10, pady=(10, 3))

        # Pin buttons to the bottom (packed BEFORE text so they're always visible)
        btns = ttk.Frame(top)
        btns.pack(side="bottom", fill="x", padx=10, pady=(5, 10))

        text = tk.Text(top, wrap="word", undo=True, maxundo=-1)
        text.pack(fill="both", expand=True, padx=10)
        if self._paste_batch_text:
            text.insert("1.0", self._paste_batch_text)
            text.edit_reset()  # clear undo stack so pre-fill isn't undoable

        def _snapshot():
            try:
                self._paste_batch_text = text.get('1.0', 'end-1c')
            except Exception:
                pass

        def _close():
            _snapshot()
            top.destroy()
            self._paste_batch_dialog = None

        def _clear():
            text.delete('1.0', 'end')
            self._paste_batch_text = ''

        def _start():
            raw = text.get("1.0", "end").strip()
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            if not lines:
                messagebox.showwarning(
                    "Empty Batch",
                    "Please paste at least one URL.",
                    parent=top,
                )
                return
            _snapshot()
            top.destroy()
            self._paste_batch_dialog = None
            self._start_batch(lines)

        top.protocol("WM_DELETE_WINDOW", _close)
        ttk.Button(btns, text="Start Batch", command=_start).pack(
            side="right")
        ttk.Button(btns, text="Close", command=_close).pack(
            side="right", padx=(0, 8))
        ttk.Button(btns, text="Clear", command=_clear).pack(
            side="right", padx=(0, 8))

    # ------------------------------------------------------------------
    # Batch Download (file-based)
    # ------------------------------------------------------------------
    def _on_batch_file(self):
        """Open a file picker and batch-download all URLs from the file."""
        path = filedialog.askopenfilename(
            title="Select batch file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = [ln.strip() for ln in f if ln.strip()
                         and not ln.strip().startswith('#')]
        except Exception as e:
            self._append_log(f"\u274c Failed to read batch file: {e}")
            return
        if not lines:
            self._append_log("Batch file is empty.")
            return
        self._start_batch(lines)

    # ------------------------------------------------------------------
    # Shared batch launcher
    # ------------------------------------------------------------------
    def _start_batch(self, urls):
        """Start batch processing a list of URLs."""
        self._downloading = True
        self._btn_download.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._btn_browser.configure(state="disabled")
        self._btn_sfacg_app.configure(state="disabled")
        self._btn_paste_batch.configure(state="disabled")
        self._btn_batch_file.configure(state="disabled")
        self._progress['value'] = 0
        self._start_time = time.time()

        interval = self._var_interval.get()
        num_threads = max(1, self._var_ext_threads.get())
        skip_paid = self._var_skip_paid.get()

        self._log(f"Starting batch: {len(urls)} URL(s), "
                  f"threads={num_threads}, interval={interval}s"
                  + (', skipping paid' if skip_paid else ''))

        self._work_queue.put(("batch", (urls, interval, num_threads,
                                         skip_paid)))

    # ------------------------------------------------------------------
    # Batch worker (runs on worker thread)
    # ------------------------------------------------------------------
    def _do_batch(self, payload):
        """Process multiple URLs sequentially on the worker thread."""
        urls, interval, num_threads, skip_paid = payload
        output_dir = self._get_output_dir()

        if self._scraper is None:
            self._scraper = ExternalScraper(logger=self._log)
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
                        and not self._scraper.is_qidian(url)
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
            end = len(chapters)
            if self._var_from_enabled.get():
                start = max(0, self._var_from.get() - 1)
            if self._var_to_enabled.get():
                end = min(len(chapters), self._var_to.get())

            self._chapter_results = []
            self._do_download(chapters, start, end, interval,
                              num_threads, skip_paid)

            # Generate output if any succeeded
            successes = sum(1 for r in self._chapter_results
                            if r is not None and not r.get('_locked'))
            if successes > 0:
                # Temporarily set _book_data for output generation
                old_book = self._book_data
                self._book_data = data
                try:
                    self._log(f"Generating output from {successes} chapter(s)...")
                    self._generate_output()
                except Exception as e:
                    self._log(f"❌ Output generation error: {e}")
                finally:
                    self._book_data = old_book

        self._log(f"\nBatch complete: processed {total_urls} URL(s).")
        self._log("Finished.")
        self._msg_queue.put(("finished", None))

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
        elif fmt == "cbz":
            self._generate_image_archive(title, author)
        elif self._var_long_image_layout.get() and self._has_long_image_chapters():
            self._generate_image_archive(title, author)
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
                    if ch_data is None or ch_data.get('_locked'):
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

    def _generate_image_archive(self, title, author):
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
        zip_compress = getattr(pg, 'var_zip_compress_images', None)
        zip_compress = zip_compress.get() if zip_compress else False

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
        img_compress_type = (
            zipfile.ZIP_DEFLATED if zip_compress else zipfile.ZIP_STORED
        )

        def fetch_image(img_url, label, img_data_url=None, log_failure=False):
            raw = None
            if img_data_url and ',' in img_data_url:
                _, b64 = img_data_url.split(',', 1)
                try:
                    raw = base64.b64decode(b64)
                except Exception:
                    raw = None
            if raw:
                return raw
            if data.get('_ntk_novel') and self._scraper and img_url:
                fetch_ntk_binary = getattr(self._scraper, 'fetch_ntk_binary', None)
                if fetch_ntk_binary:
                    raw = fetch_ntk_binary(img_url, data.get('bookUrl') or '')
                    if raw:
                        return raw
            if img_url:
                return self._download_image_python(
                    img_url,
                    label,
                    session=img_session,
                    log_success=False,
                    log_failure=log_failure,
                )
            return None

        try:
            self._log("  Long image layout: saving CBZ image archive")
            written = 0
            with zipfile.ZipFile(filepath, "w") as zf:
                cover_url = data.get('coverUrl', '')
                if cover_url:
                    cover_raw = fetch_image(cover_url, "Cover", log_failure=True)
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
                    ch_name = ch_data.get('chapterName', f'Chapter {i + 1}')
                    safe_ch = _sanitize_filename(ch_name)
                    images = ch_data.get('images') or []
                    if not images:
                        self._log(
                            f"  Skipping CBZ chapter {i + 1}: "
                            f"{ch_name} (no images)"
                        )
                        continue
                    self._log(
                        f"  Building CBZ chapter {i + 1}: {ch_name} "
                        f"({len(images)} image(s))"
                    )
                    chapter_start = time.time()
                    chapter_bytes = 0
                    failed_count = 0
                    for img_idx, img_info in enumerate(images, 1):
                        img_url = html.unescape(
                            img_info.get('url', '')
                        ).strip()
                        raw = fetch_image(
                            img_url,
                            f"Ch{i+1} image {img_idx}/{len(images)}",
                            img_data_url=img_info.get('data'),
                            log_failure=(img_idx <= 3),
                        )
                        if not raw:
                            failed_count += 1
                            continue
                        ext = self._image_ext_from_bytes(raw)
                        if compress_images:
                            raw = self._compress_image(
                                raw, jpeg_quality, image_format
                            )
                            ext = self._format_ext(image_format)
                        archive_name = (
                            f"{i+1:04d}_{safe_ch}/{img_idx:04d}.{ext}"
                        )
                        zf.writestr(
                            archive_name,
                            raw,
                            compress_type=img_compress_type,
                        )
                        chapter_bytes += len(raw)
                        written += 1
                        if img_idx % 5 == 0 or img_idx == len(images):
                            self._log(
                                f"    Images ready: {img_idx}/"
                                f"{len(images)}"
                            )
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
            else:
                self._log("\u274c CBZ generation failed: no images were downloaded.")
        except Exception as e:
            self._log(f"\u274c CBZ generation failed: {e}")
            self._log("Falling back to EPUB output...")
            self._generate_epub(title, author)
        finally:
            img_session.close()

    def _kakao_extra_css(self):
        """Collect original Kakao viewer CSS from downloaded chapters."""
        if not (self._book_data and self._book_data.get('_kakaopage')):
            return ''

        css_parts = ["""
body {
  background-color: #fff;
  color: #000;
}
.chapter > h1:first-child {
  display: none;
}
.kakao-source-heading {
  margin-top: 4vh !important;
  margin-bottom: 3vh !important;
  text-align: center !important;
}
.kakao-page-break {
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

    def _has_long_image_chapters(self):
        """Return True when chapters look like vertical image-strip content."""
        data = self._book_data or {}
        if data.get('_ntk_kind') == 'webtoon':
            return True
        for ch_data in self._chapter_results or []:
            if not ch_data or ch_data.get('_locked'):
                continue
            content_html = ch_data.get('contentHtml') or ''
            content_css = ch_data.get('contentCss') or ''
            if (
                'ntk-webtoon-page' in content_html
                or 'ntk-webtoon-page' in content_css
            ):
                return True
        return False

    def _long_image_layout_css(self):
        """CSS for webtoon-style chapters made of tall image strips."""
        return """
body {
  margin: 0;
  padding: 0;
}
.ntk-webtoon-page {
  margin: 0;
  padding: 0;
  text-align: center;
  line-height: 0;
}
.ntk-webtoon-page img,
.chapter > img,
p > img {
  display: block;
  width: 100%;
  max-width: 100%;
  max-height: none;
  height: auto;
  margin: 0 auto;
  padding: 0;
  page-break-inside: avoid;
  break-inside: avoid;
}
.ntk-webtoon-page + .ntk-webtoon-page {
  margin-top: 0;
}
""".strip()

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
        is_kakao = bool(data.get('_kakaopage'))
        use_long_image_layout = (
            bool(self._var_long_image_layout.get())
            and self._has_long_image_chapters()
        )

        # Default CSS for external novels
        css = """body { margin: 2%; }
p { overflow-wrap: break-word; }
h1, h2 { text-align: center; margin-bottom: 10%; margin-top: 10%; }
h3, h4, h5, h6 { text-align: center; margin-bottom: 15%; margin-top: 10%; }
img { display: block; max-width: 100%; max-height: 100%;
      margin-left: auto; margin-right: auto; margin-bottom: 2%; margin-top: 2%; }
"""
        chapter_css = self._chapter_extra_css()
        if chapter_css and not is_kakao:
            css = f"{css}\n\n/* Scraper-provided chapter CSS */\n{chapter_css}\n"
        if use_long_image_layout:
            css = (
                f"{css}\n\n/* Long image layout */\n"
                f"{self._long_image_layout_css()}\n"
            )
            self._log("  Long image layout: ON")
        kakao_css = self._kakao_extra_css()
        if kakao_css:
            css = f"{css}\n\n/* KakaoPage original viewer CSS */\n{kakao_css}\n"
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
        if is_kakao:
            img_session.headers.update({
                'Referer': data.get('bookUrl') or 'https://page.kakao.com/',
                'Origin': 'https://page.kakao.com',
            })
            self._copy_browser_cookies_to_session(img_session)
        img_counter = [0]

        try:
            epub = EpubGenerator(metadata, filepath, css, zip_compress)

            # Download and add cover image via the correct API
            # (EpubGenerator looks for images named 'cover.*')
            cover_added = False
            if cover_url:
                cover_bytes = None
                if data.get('_ntk_novel') and self._scraper:
                    fetch_ntk_binary = getattr(
                        self._scraper, 'fetch_ntk_binary', None
                    )
                    if fetch_ntk_binary:
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
            if intro_html:
                intro_page = f"""
<h2>{html.escape(data.get('bookname', title))}</h2>
<h3>{html.escape(data.get('author', author))}</h3>
<hr/>
{intro_html}
"""
                epub.add_extra_page('info.xhtml', intro_page)

            for i, ch_data in enumerate(self._chapter_results):
                if ch_data is None or ch_data.get('_locked'):
                    continue
                ch_name = ch_data.get('chapterName', f'Chapter {i + 1}')
                content_html = ch_data.get('contentHtml', '')
                img_total = len(ch_data.get('images') or [])
                if img_total:
                    self._log(
                        f"  Building EPUB chapter {i + 1}: {ch_name} "
                        f"({img_total} image(s))"
                    )
                else:
                    self._log(f"  Building EPUB chapter {i + 1}: {ch_name}")

                # Process images: use browser-provided base64 data when
                # available, fall back to Python-side download otherwise.
                rename_map = {}  # original_name → compressed_name
                if ch_data.get('images'):
                    image_items = list(enumerate(ch_data['images'], 1))
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

                    # Reuse HTTP sessions across image tasks. Creating a new
                    # TLS connection for every Kakao page image is expensive,
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
                        img_url = html.unescape(img_info.get('url', '')).strip()
                        img_data_url = img_info.get('data')
                        raw = None

                        # Try browser-provided base64 data first
                        if img_data_url and ',' in img_data_url:
                            _, b64 = img_data_url.split(',', 1)
                            try:
                                raw = base64.b64decode(b64)
                            except Exception:
                                pass

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
                                raw = self._download_image_python(
                                    img_url,
                                    f"Ch{i+1} image {img_idx}/"
                                    f"{len(image_items)}",
                                    session=dl_session,
                                    log_success=False,
                                    log_failure=(img_idx <= 3),
                                )
                            finally:
                                if session_pool is not None and dl_session:
                                    session_pool.put(dl_session)

                        if not raw:
                            return img_idx, None

                        orig_name = img_info.get(
                            'name', f'img_{i}_{img_idx}.jpg'
                        )
                        safe_orig = re.sub(
                            r'[^A-Za-z0-9._-]+', '_', orig_name
                        ).strip('._') or f'img_{i}_{img_idx}.jpg'
                        base = safe_orig.rsplit('.', 1)[0]
                        ext = safe_orig.rsplit('.', 1)[1].lower() \
                            if '.' in safe_orig else 'jpg'
                        img_name = f'ch{i+1:04d}_{img_idx:04d}_{base}.{ext}'

                        if compress_images:
                            raw = self._compress_image(
                                raw, jpeg_quality, image_format
                            )
                            new_ext = image_format.lower()
                            if new_ext == 'jpeg':
                                new_ext = 'jpg'
                            img_name = (
                                f'ch{i+1:04d}_{img_idx:04d}_{base}.{new_ext}'
                            )

                        return img_idx, {
                            'raw': raw,
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

                    failed_items = [
                        item for item in image_items
                        if not image_results.get(item[0])
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
                    if image_items:
                        elapsed = max(0.01, time.time() - image_start)
                        mb = image_bytes / (1024 * 1024)
                        ready_count = sum(
                            1 for result in image_results.values() if result
                        )
                        failed_count = len(image_items) - ready_count
                        self._log(
                            f"    Images embedded: {ready_count}/"
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
                    compress_images, jpeg_quality, image_format, i
                )

                # Fix novel-downloader's img format for EPUB rendering:
                # cleanDOM outputs <img data-src-address="name.jpeg" alt="..."
                # title="..."> with NO src attribute.  EPUB readers need src.
                content_html = self._fix_nd_img_tags(
                    content_html, rename_map
                )

                show_chapter_title = not is_kakao
                if use_long_image_layout and ch_data.get('images'):
                    show_chapter_title = False
                epub.add_chapter(
                    ch_name, content_html, show_title=show_chapter_title
                )

            if is_kakao and self._var_kakao_dedupe_images.get():
                max_positions = max(
                    1, min(10, self._var_kakao_dedupe_leading_images.get())
                )
                self._remove_kakao_duplicate_cover_pages(
                    epub, max_positions=max_positions
                )

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

    def _remove_kakao_duplicate_cover_pages(self, epub, max_positions=2):
        """Remove repeated Kakao boilerplate images by exact bytes.

        This does not download or probe anything. It only looks at the
        configured number of leading images from Kakao image chapters, then
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
            if 'kakao-image-chapter' not in content:
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

        duplicate_names = set()
        for occurrences in occurrences_by_bytes.values():
            chapter_count = len({item['chapter_index'] for item in occurrences})
            if chapter_count < 2:
                continue
            # Keep the first copy in reading order and remove later repeats.
            for item in occurrences[1:]:
                duplicate_names.add(item['filename'])

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
            f"  Removed {len(duplicate_names)} duplicate Kakao "
            "boilerplate image occurrence(s), keeping the first copy."
        )

    def _remove_image_page_by_filename(self, html_str, filename):
        """Remove a Kakao image-page wrapper, falling back to the img tag."""
        if not html_str or not filename:
            return html_str

        sources = {
            f'../Images/{filename}',
            f'../Images/{html.escape(filename, quote=True)}',
        }
        for src in sources:
            pattern = (
                r'\s*<div class="kakao-image-page">\s*'
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

    def _download_image_python(self, url, label="Image", session=None,
                               max_retries=3, log_success=True,
                               log_failure=False):
        """Download an image using requests (no CORS/mixed-content issues).

        Automatically upgrades http:// to https:// and retries on failure.
        Returns raw bytes or None.
        """
        import requests as _req

        url = html.unescape((url or '').strip())

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
                r = sess.get(url, timeout=15)
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
        kakao_css = self._kakao_extra_css()
        if kakao_css:
            css = f"{css}\n\n/* KakaoPage original viewer CSS */\n{kakao_css}\n"

        data = self._book_data
        metadata = {
            'title': data.get('bookname', title),
            'author': data.get('author', author),
        }

        # Build chapter list and image map
        chapters_for_pdf = []
        image_map = {}
        for i, ch_data in enumerate(self._chapter_results):
            if ch_data is None or ch_data.get('_locked'):
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
        """Called when the user clicks the X button — just hide."""
        self._save_ext_config()
        self.withdraw()

    def _on_app_exit(self):
        """Called when the main application is shutting down.

        Tears down the worker thread, Playwright browser, and destroys
        the window for real.
        """
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
            self._var_ext_image_workers.set(cfg.get("ext_image_workers", 8))
            self._var_interval.set(cfg.get("ext_interval", 0.5))
            self._var_from_enabled.set(cfg.get("ext_from_enabled", False))
            self._var_to_enabled.set(cfg.get("ext_to_enabled", False))
            self._var_from.set(cfg.get("ext_from", 1))
            self._var_to.set(cfg.get("ext_to", 1))
            self._var_skip_paid.set(cfg.get("ext_skip_paid", False))
            self._var_ntk_novelpia_cover.set(
                cfg.get("ext_ntk_novelpia_cover", False)
            )
            self._var_syosetu_amazon_cover.set(
                cfg.get("ext_syosetu_amazon_cover", False)
            )
            self._var_long_image_layout.set(
                cfg.get("ext_long_image_layout", False)
            )
            self._var_kakao_skip_last_page.set(
                cfg.get("ext_kakao_skip_last_page", False)
            )
            self._var_kakao_keep_filler.set(
                cfg.get("ext_kakao_keep_filler", False)
            )
            self._var_kakao_dedupe_images.set(
                cfg.get("ext_kakao_dedupe_images", True)
            )
            self._var_kakao_dedupe_leading_images.set(
                cfg.get("ext_kakao_dedupe_leading_images", 2)
            )
            self._spn_kakao_dedupe_leading.configure(
                state=(
                    "normal" if self._var_kakao_dedupe_images.get()
                    else "disabled"
                )
            )
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
        cfg["ext_url"] = self._url_var.get().strip()
        cfg["ext_format"] = self._var_format.get()
        cfg["ext_threads"] = self._var_ext_threads.get()
        cfg["ext_image_workers"] = self._var_ext_image_workers.get()
        cfg["ext_interval"] = self._var_interval.get()
        cfg["ext_from_enabled"] = self._var_from_enabled.get()
        cfg["ext_to_enabled"] = self._var_to_enabled.get()
        cfg["ext_from"] = self._var_from.get()
        cfg["ext_to"] = self._var_to.get()
        cfg["ext_skip_paid"] = self._var_skip_paid.get()
        cfg["ext_ntk_novelpia_cover"] = (
            self._var_ntk_novelpia_cover.get()
        )
        cfg["ext_syosetu_amazon_cover"] = (
            self._var_syosetu_amazon_cover.get()
        )
        cfg["ext_long_image_layout"] = self._var_long_image_layout.get()
        cfg["ext_kakao_skip_last_page"] = (
            self._var_kakao_skip_last_page.get()
        )
        cfg["ext_kakao_keep_filler"] = self._var_kakao_keep_filler.get()
        cfg["ext_kakao_dedupe_images"] = (
            self._var_kakao_dedupe_images.get()
        )
        cfg["ext_kakao_dedupe_leading_images"] = max(
            1, min(10, self._var_kakao_dedupe_leading_images.get())
        )
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass
