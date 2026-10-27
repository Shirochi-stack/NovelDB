"""
external_dialog.py — Tkinter dialog for browser-based novel downloads.

Provides a self-contained Toplevel window with:
  - URL input
  - Multi-format selection (EPUB / TXT / PDF / CBZ)
  - Minimum/maximum interval controls for randomized rate limiting
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
import hashlib
import base64
import time
import threading
import queue
import subprocess
import tempfile
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from external_scraper import ExternalScraper, write_text_output
from scripts.source_names import dump as dump_source_json
from noveldb.core.external.pipeline import (  # noqa: F401 - re-exported for callers and tests
    ExternalPipelineMixin,
    _external_cache_dir,
    _format_size,
    _sanitize_filename,
)


def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _config_file():
    """config.json next to the app, or in the per-user folder when that is read-only."""
    try:
        from noveldb.core import paths as _paths
        return str(_paths.config_path(_get_base_dir()))
    except Exception:
        return os.path.join(_get_base_dir(), "config.json")


class ExternalNovelDialog(ExternalPipelineMixin, tk.Toplevel):
    """Tkinter downloader for Npia and external novel sites."""

    def __init__(self, parent, retry_variable=None):
        super().__init__(parent)
        self.title("External / Npia Novel Download")
        self.transient(parent)

        # Size window
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        w = int(screen_w * 0.67)
        h = int(screen_h * 0.60)
        x = (screen_w - w) // 2
        y = (screen_h - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(int(screen_w * 0.35), int(screen_h * 0.40))

        self._parent_gui = parent      # Access compression settings from main GUI
        self._retry_variable = retry_variable
        self._scraper = None
        self._book_data = None
        self._chapter_results = []
        self._chapter_number_start = 1
        self._downloading = False
        self._download_cancelled = False
        self._active_output_formats = None
        self._active_generate_on_stop = False
        self._start_time = None
        self._msg_queue = queue.Queue()
        self._log_link_counter = 0
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
        self._var_regular_browser.trace_add(
            "write", lambda *_: self._save_ext_config()
        )
        self._var_kobo_horizontal_layout.trace_add(
            "write", lambda *_: self._save_ext_config()
        )
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
        # Independent format flags allow the scraped chapters to feed more
        # than one writer. Keep _var_format as a legacy primary value for old
        # config files and older builds.
        self._var_format = tk.StringVar(value="epub")
        self._var_format_epub = tk.BooleanVar(value=True)
        self._var_format_txt = tk.BooleanVar(value=False)
        self._var_separate_txt = tk.BooleanVar(value=False)
        self._var_format_pdf = tk.BooleanVar(value=False)
        self._var_format_cbz = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            fmt_frame, text="EPUB", variable=self._var_format_epub
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            fmt_frame, text="TXT", variable=self._var_format_txt
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            fmt_frame, text="PDF", variable=self._var_format_pdf
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            fmt_frame, text="CBZ", variable=self._var_format_cbz
        ).pack(side="left", padx=5)

        ttk.Checkbutton(
            fmt_frame, text="Separate TXT files", variable=self._var_separate_txt
        ).pack(side="left", padx=5)

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
        ttk.Label(int_frame, text="Min:").pack(side="left", padx=(0, 3))
        self._var_interval = tk.DoubleVar(value=0.5)
        ttk.Spinbox(
            int_frame, from_=0.0, to=300.0, increment=0.1,
            textvariable=self._var_interval, width=5,
        ).pack(side="left")
        ttk.Label(int_frame, text="Max:").pack(
            side="left", padx=(6, 3)
        )
        self._var_interval_max = tk.DoubleVar(value=0.5)
        ttk.Spinbox(
            int_frame, from_=0.0, to=300.0, increment=0.1,
            textvariable=self._var_interval_max, width=5,
        ).pack(side="left")
        ttk.Label(int_frame, text="s").pack(side="left", padx=(3, 0))

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
        # Prefix chapter titles with their position in the book ("2. ...")
        self._var_number_chapters = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Number chapters",
            variable=self._var_number_chapters,
        ).pack(side="left", padx=(10, 0))
        self._var_ntk_npia_cover = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame,
            text="Prefer source cover",
            variable=self._var_ntk_npia_cover,
        ).pack(side="left", padx=(10, 0))
        self._var_kpage_skip_last_page = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Skip last page",
            variable=self._var_kpage_skip_last_page,
        ).pack(side="left", padx=(10, 0))
        self._var_kpage_keep_filler = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_frame, text="Keep filler",
            variable=self._var_kpage_keep_filler,
        ).pack(side="left", padx=(10, 0))
        dedupe_frame = ttk.Frame(settings_frame)
        dedupe_frame.pack(side="left", padx=(10, 0))
        dedupe_top_frame = ttk.Frame(dedupe_frame)
        dedupe_top_frame.pack(anchor="w")
        self._var_kpage_dedupe_images = tk.BooleanVar(value=True)
        self._var_kpage_dedupe_leading_images = tk.IntVar(value=2)
        ttk.Checkbutton(
            dedupe_top_frame, text="Dedupe images",
            variable=self._var_kpage_dedupe_images,
        ).pack(side="left")
        self._spn_kpage_dedupe_leading = ttk.Spinbox(
            dedupe_top_frame, from_=1, to=10,
            textvariable=self._var_kpage_dedupe_leading_images, width=3
        )
        self._spn_kpage_dedupe_leading.pack(side="left", padx=(3, 0))
        self._var_kpage_dedupe_images.trace_add(
            "write",
            lambda *_: self._spn_kpage_dedupe_leading.configure(
                state=(
                    "normal" if self._var_kpage_dedupe_images.get()
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

        self._var_blank_paragraph_lines = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self, text="Blank line after each paragraph",
            variable=self._var_blank_paragraph_lines,
        ).pack(anchor="w", padx=15)
        self._var_kobo_horizontal_layout = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self, text="Horizontal Japanese text (Rakuten Kobo)",
            variable=self._var_kobo_horizontal_layout,
        ).pack(anchor="w", padx=15)

        # --- Action Buttons ---
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=(5, 2))

        self._btn_download = ttk.Button(btn_frame, text="Download",
                                         command=self._on_download)
        self._btn_download.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_stop = ttk.Button(btn_frame, text="Stop",
                                     command=self._on_stop, state="disabled")
        self._btn_stop.pack(side="left", padx=(0, 5), ipady=3)

        self._var_generate_on_stop = tk.BooleanVar(value=False)
        self._chk_generate_on_stop = ttk.Checkbutton(
            btn_frame,
            text="Generate on stop",
            variable=self._var_generate_on_stop,
        )
        self._chk_generate_on_stop.pack(side="left", padx=(0, 10))

        self._var_use_cache = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            btn_frame, text="Use Cache", variable=self._var_use_cache,
        ).pack(side="left", padx=(0, 5))
        self._var_cache_images = tk.BooleanVar(value=False)
        self._chk_cache_images = ttk.Checkbutton(
            btn_frame, text="Cache Images", variable=self._var_cache_images,
        )
        self._chk_cache_images.pack(side="left", padx=(0, 10))
        self._var_use_cache.trace_add(
            "write",
            lambda *_: self._chk_cache_images.configure(
                state="normal" if self._var_use_cache.get() else "disabled"
            ),
        )
        ttk.Button(
            btn_frame,
            text="Open Cache Folder",
            command=self._open_external_cache_folder,
        ).pack(side="left", padx=(0, 10), ipady=3)

        self._btn_browser = ttk.Button(btn_frame, text="Enter Browser",
                                        command=self._on_enter_browser)
        self._btn_browser.pack(side="left", padx=(0, 5), ipady=3)

        self._btn_sfc_app = ttk.Button(btn_frame, text="Enter App",
                                          command=self._on_sfc_app_login)
        self._btn_sfc_app.pack(side="left", padx=(0, 5), ipady=3)

        self._var_regular_browser = tk.BooleanVar(value=False)
        self._chk_regular_browser = ttk.Checkbutton(
            btn_frame,
            text="Regular login browser",
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


    def _ui_fetch_done(self):
        self.after(0, lambda: self._btn_download.configure(state="normal"))

    def _poll_queue(self):
        """Drain the message queue and update the UI."""
        deadline = time.perf_counter() + 0.01
        handled = 0
        try:
            while handled < 100 and time.perf_counter() < deadline:
                kind, data = self._msg_queue.get_nowait()
                handled += 1
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
                elif kind == "sfc_app_done":
                    self._on_sfc_app_done(data)
                elif kind == "sfc_android_done":
                    self._on_sfc_android_done(data)
                elif kind == "error":
                    self._append_log(f"\u274c Error: {data}")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(10 if handled == 100 or time.perf_counter() >= deadline
                       else 100, self._poll_queue)

    def _append_log(self, text):
        self._console.configure(state="normal")
        saved_prefix = "✅ Saved: "
        if text.startswith(saved_prefix):
            self._console.insert("end", saved_prefix)
            file_path = text[len(saved_prefix):].strip()
            tag = f"saved_path_{self._log_link_counter}"
            self._log_link_counter += 1
            self._console.insert("end", file_path, (tag,))
            self._console.tag_configure(
                tag, foreground="#0563c1", underline=True
            )
            self._console.tag_bind(
                tag,
                "<Button-1>",
                lambda _event, path=file_path: self._open_saved_file(path),
            )
            self._console.tag_bind(
                tag, "<Button-3>",
                lambda event, path=file_path: self._show_saved_file_menu(
                    event, path),
            )
            self._console.tag_bind(
                tag, "<Enter>",
                lambda _event: self._console.configure(cursor="hand2"),
            )
            self._console.tag_bind(
                tag, "<Leave>",
                lambda _event: self._console.configure(cursor=""),
            )
            self._console.insert("end", "\n")
        else:
            self._console.insert("end", text + "\n")
        self._console.see("end")
        self._console.configure(state="disabled")

    def _open_saved_file(self, file_path):
        """Open a generated file or output folder with the default application."""
        path = os.path.abspath(os.path.expanduser(file_path))
        try:
            if not (os.path.isfile(path) or os.path.isdir(path)):
                raise FileNotFoundError(path)
            if sys.platform == 'win32':
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as exc:
            self._append_log(f"❌ Could not open saved output: {exc}")

    def _show_saved_file_menu(self, event, file_path):
        menu = getattr(self, '_saved_file_menu', None)
        if menu is None:
            menu = self._saved_file_menu = tk.Menu(self, tearoff=False)
        menu.delete(0, 'end')
        menu.add_command(
            label='Open file location',
            command=lambda: self._open_saved_location(file_path),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return 'break'

    def _open_saved_location(self, file_path):
        """Reveal a saved file, or open the saved output directory."""
        path = os.path.normpath(os.path.abspath(os.path.expanduser(file_path)))
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            if not os.path.isdir(folder):
                raise FileNotFoundError(folder)
            if sys.platform == 'win32':
                if os.path.isfile(path):
                    subprocess.Popen(['explorer.exe', '/select,', path])
                else:
                    os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', '-R', path] if os.path.isfile(path)
                                 else ['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        except Exception as exc:
            self._append_log(f"❌ Could not open saved output location: {exc}")


    def _open_external_cache_folder(self):
        """Open the external downloader cache directory in the file explorer."""
        cache_dir = _external_cache_dir()
        try:
            os.makedirs(cache_dir, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(cache_dir)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', cache_dir])
            else:
                subprocess.Popen(['xdg-open', cache_dir])
            self._log(f"Opened cache folder: {cache_dir}")
        except Exception as exc:
            self._log(f"Failed to open cache folder ({cache_dir}): {exc}")


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
        self._btn_sfc_app.configure(state="disabled")
        self._chk_regular_browser.configure(state="disabled")
        self._btn_paste_batch.configure(state="disabled")
        self._btn_batch_file.configure(state="disabled")
        regular_browser = self._var_regular_browser.get()
        if start_url and ExternalScraper.is_npia(start_url):
            regular_browser = True
            self._var_regular_browser.set(True)
            self._append_log(
                "[Npia] Enter Browser uses the External Downloader's "
                "regular installed-Chrome profile. Log in normally, then "
                "close that window."
            )
        elif start_url and ExternalScraper.is_mpia(start_url):
            regular_browser = True
            self._var_regular_browser.set(True)
            self._append_log(
                "[Mpia] Enter Browser uses the External Downloader's "
                "saved browser profile. Log in to access your purchased "
                "chapters, then close that window. Skip paid selects "
                "only free chapters."
            )
        elif start_url and any((
            ExternalScraper.is_floo(start_url),
            ExternalScraper.is_fenrir(start_url),
            ExternalScraper.is_qdn(start_url),
            ExternalScraper.is_rbooks(start_url),
            ExternalScraper.is_kobo(start_url),
        )):
            regular_browser = True
            self._var_regular_browser.set(True)
            self._append_log(
                "Enter Browser uses the saved installed-Chrome profile for "
                "this site. Sign in, then close that window before Download."
            )
        if regular_browser:
            self._append_log(
                "Opening regular login browser... Close the browser when "
                "done."
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
        self._btn_sfc_app.configure(state="normal")
        self._chk_regular_browser.configure(state="normal")
        self._btn_download.configure(state="normal")
        self._btn_paste_batch.configure(state="normal")
        self._btn_batch_file.configure(state="normal")
        self._append_log("Browser session ended.")

    def _on_sfc_app_login(self):
        """Prompt for SFC app credentials or an existing app cookie."""
        dlg = tk.Toplevel(self)
        dlg.title("SFC App Session")
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
                    "SFC App Session",
                    "Enter username/password or paste an app cookie.",
                    parent=dlg,
                )
                return
            dlg.destroy()
            if cookie:
                self._append_log("Importing SFC app cookie...")
            else:
                self._append_log("Logging in to SFC app API...")
            self._btn_download.configure(state="disabled")
            self._btn_browser.configure(state="disabled")
            self._btn_sfc_app.configure(state="disabled")
            self._chk_regular_browser.configure(state="disabled")
            self._btn_paste_batch.configure(state="disabled")
            self._btn_batch_file.configure(state="disabled")
            self._work_queue.put(("sfc_app_login", {
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
            command=lambda: (dlg.destroy(), self._on_sfc_android_import()),
        ).pack(side="left")
        ttk.Button(
            btns,
            text="Open Android",
            command=lambda: self._on_sfc_android_open(),
        ).pack(side="left", padx=(0, 5))
        ent_user.focus_set()
        dlg.bind("<Return>", lambda _e: submit())
        self._center_child_window(dlg)

    def _set_app_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self._btn_browser.configure(state=state)
        self._btn_sfc_app.configure(state=state)
        self._chk_regular_browser.configure(state=state)
        self._btn_download.configure(state=state)
        self._btn_paste_batch.configure(state=state)
        self._btn_batch_file.configure(state=state)

    def _on_sfc_android_open(self):
        self._append_log("Opening Android emulator...")
        self._set_app_buttons_enabled(False)
        self._work_queue.put(("sfc_android_open", None))

    def _on_sfc_android_import(self):
        self._append_log("Trying to import SFC app session from Android...")
        self._set_app_buttons_enabled(False)
        self._work_queue.put(("sfc_android_import", None))

    def _on_sfc_android_done(self, ok):
        self._set_app_buttons_enabled(True)
        if ok:
            self._append_log(
                "Android emulator opened. Install/open SFC, log in, "
                "then use Enter App > Import Android."
            )
        else:
            self._append_log("Android emulator action failed.")

    def _on_sfc_app_done(self, ok):
        self._set_app_buttons_enabled(True)
        if ok:
            self._append_log("SFC app session saved.")
        else:
            self._append_log("SFC app login failed.")

    # ------------------------------------------------------------------
    # Fetch Info (internal helper — called from workers)
    # ------------------------------------------------------------------
    def _on_book_parsed(self, data):
        self._book_data = data
        self._lbl_title.configure(text=data.get('bookname', '?'))
        self._lbl_author.configure(text=data.get('author', '?'))
        chapter_count = data.get('chapterCount', 0)
        notice_count = data.get('noticeCount', 0)
        chapter_label = str(chapter_count)
        if notice_count:
            chapter_label += f" (+{notice_count} author notices)"
        self._lbl_chapters.configure(text=chapter_label)

        if chapter_count > 0:
            # Only auto-fill 'To' if the user hasn't explicitly set a range
            if not self._var_to_enabled.get():
                self._var_to.set(chapter_count)


    def _on_download(self):
        url = self._normalised_url()
        if not url:
            self._append_log("Please enter a URL.")
            return
        output_formats = self._selected_output_formats()
        if not output_formats:
            messagebox.showwarning(
                "No Output Format",
                "Select at least one output format (EPUB, TXT, PDF, or CBZ).",
                parent=self,
            )
            return
        self._active_separate_txt = self._var_separate_txt.get()
        self._active_output_formats = output_formats
        self._active_generate_on_stop = bool(
            self._var_generate_on_stop.get()
        )
        self._url_var.set(url)
        self._save_ext_config()

        self._downloading = True
        self._download_cancelled = False
        self._btn_download.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._chk_generate_on_stop.configure(state="disabled")
        self._btn_browser.configure(state="disabled")
        self._btn_sfc_app.configure(state="disabled")
        self._btn_paste_batch.configure(state="disabled")
        self._btn_batch_file.configure(state="disabled")
        self._progress['value'] = 0
        self._chapter_results = []
        self._start_time = time.time()

        self._lbl_title.configure(text="Fetching...")
        self._lbl_author.configure(text="—")
        self._lbl_chapters.configure(text="—")

        interval, interval_max = self._get_interval_range()
        num_threads = max(1, self._var_ext_threads.get())
        skip_paid = self._var_skip_paid.get()
        retry_passes = self._get_retry_passes()

        self._work_queue.put((
            "fetch_and_download",
            (
                url, interval, num_threads, skip_paid, retry_passes,
                interval_max,
            ),
        ))

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
        self._active_output_formats = None
        self._active_generate_on_stop = False
        self._btn_download.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._chk_generate_on_stop.configure(state="normal")
        self._btn_browser.configure(state="normal")
        self._btn_sfc_app.configure(state="normal")
        self._btn_paste_batch.configure(state="normal")
        self._btn_batch_file.configure(state="normal")
        self._lbl_eta.configure(text="")


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
        output_formats = self._selected_output_formats()
        if not output_formats:
            messagebox.showwarning(
                "No Output Format",
                "Select at least one output format (EPUB, TXT, PDF, or CBZ).",
                parent=self,
            )
            return
        self._active_separate_txt = self._var_separate_txt.get()
        self._active_output_formats = output_formats
        self._active_generate_on_stop = bool(
            self._var_generate_on_stop.get()
        )
        self._downloading = True
        self._download_cancelled = False
        self._btn_download.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._chk_generate_on_stop.configure(state="disabled")
        self._btn_browser.configure(state="disabled")
        self._btn_sfc_app.configure(state="disabled")
        self._btn_paste_batch.configure(state="disabled")
        self._btn_batch_file.configure(state="disabled")
        self._progress['value'] = 0
        self._start_time = time.time()

        interval, interval_max = self._get_interval_range()
        num_threads = max(1, self._var_ext_threads.get())
        skip_paid = self._var_skip_paid.get()
        retry_passes = self._get_retry_passes()

        interval_display = self._format_interval_range(
            interval, interval_max
        )
        self._log(f"Starting batch: {len(urls)} URL(s), "
                  f"threads={num_threads}, interval={interval_display}"
                  + (', skipping paid' if skip_paid else ''))

        self._work_queue.put((
            "batch",
            (
                urls, interval, num_threads, skip_paid, retry_passes,
                interval_max,
            ),
        ))


    # ------------------------------------------------------------------
    # Stop / Close
    # ------------------------------------------------------------------
    def _on_stop(self):
        self._downloading = False
        self._download_cancelled = True
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
        cfg_path = _config_file()
        if not os.path.exists(cfg_path):
            return
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._url_var.set(cfg.get("ext_url", ""))
            configured_formats = cfg.get("ext_formats")
            if not isinstance(configured_formats, list):
                configured_formats = [cfg.get("ext_format", "epub")]
            configured_formats = [
                fmt for fmt in configured_formats
                if fmt in ("epub", "txt", "pdf", "cbz")
            ]
            if not configured_formats:
                configured_formats = ["epub"]
            self._var_format_epub.set("epub" in configured_formats)
            self._var_format_txt.set("txt" in configured_formats)
            self._var_format_pdf.set("pdf" in configured_formats)
            self._var_format_cbz.set("cbz" in configured_formats)
            self._var_format.set(configured_formats[0])
            if hasattr(self, "_var_separate_txt"):
                self._var_separate_txt.set(cfg.get("ext_separate_txt", False))
            self._var_ext_threads.set(cfg.get("ext_threads", 4))
            self._var_ext_image_workers.set(cfg.get("ext_image_workers", 8))
            self._var_interval.set(cfg.get("ext_interval", 0.5))
            self._var_interval_max.set(
                cfg.get("ext_interval_max", cfg.get("ext_interval", 0.5))
            )
            self._var_from_enabled.set(cfg.get("ext_from_enabled", False))
            self._var_to_enabled.set(cfg.get("ext_to_enabled", False))
            self._var_from.set(cfg.get("ext_from", 1))
            self._var_to.set(cfg.get("ext_to", 1))
            self._var_skip_paid.set(cfg.get("ext_skip_paid", False))
            self._var_number_chapters.set(
                cfg.get("ext_number_chapters", False)
            )
            self._var_generate_on_stop.set(
                cfg.get("ext_generate_on_stop", False)
            )
            if hasattr(self, '_var_blank_paragraph_lines'):
                self._var_blank_paragraph_lines.set(
                    cfg.get('ext_blank_paragraph_lines', False)
                )
            self._var_kobo_horizontal_layout.set(
                cfg.get('ext_kobo_horizontal_layout', True)
            )
            self._var_use_cache.set(cfg.get("ext_use_cache", True))
            self._var_cache_images.set(cfg.get("ext_cache_images", False))
            self._chk_cache_images.configure(
                state="normal" if self._var_use_cache.get() else "disabled"
            )
            self._var_regular_browser.set(
                cfg.get("ext_regular_browser", False)
            )
            self._var_ntk_npia_cover.set(
                cfg.get("ext_ntk_npia_cover", False)
            )
            self._var_syosetu_amazon_cover.set(
                cfg.get("ext_syosetu_amazon_cover", False)
            )
            self._var_long_image_layout.set(
                cfg.get("ext_long_image_layout", False)
            )
            self._var_kpage_skip_last_page.set(
                cfg.get("ext_kpage_skip_last_page", False)
            )
            self._var_kpage_keep_filler.set(
                cfg.get("ext_kpage_keep_filler", False)
            )
            self._var_kpage_dedupe_images.set(
                cfg.get("ext_kpage_dedupe_images", True)
            )
            self._var_kpage_dedupe_leading_images.set(
                cfg.get("ext_kpage_dedupe_leading_images", 2)
            )
            self._spn_kpage_dedupe_leading.configure(
                state=(
                    "normal" if self._var_kpage_dedupe_images.get()
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
        cfg_path = _config_file()
        cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        selected_formats = self._selected_output_formats()
        primary_format = selected_formats[0] if selected_formats else "epub"
        self._var_format.set(primary_format)
        cfg["ext_url"] = self._url_var.get().strip()
        # Keep the single value for backward compatibility; new builds restore
        # all checkbox states from ext_formats.
        cfg["ext_format"] = primary_format
        cfg["ext_formats"] = selected_formats
        cfg["ext_separate_txt"] = self._var_separate_txt.get()
        cfg["ext_threads"] = self._var_ext_threads.get()
        cfg["ext_image_workers"] = self._var_ext_image_workers.get()
        # Keep ext_interval as the minimum so older builds/config files retain
        # their former fixed-delay behavior.
        cfg["ext_interval"] = self._var_interval.get()
        cfg["ext_interval_max"] = self._var_interval_max.get()
        cfg["ext_from_enabled"] = self._var_from_enabled.get()
        cfg["ext_to_enabled"] = self._var_to_enabled.get()
        cfg["ext_from"] = self._var_from.get()
        cfg["ext_to"] = self._var_to.get()
        cfg["ext_skip_paid"] = self._var_skip_paid.get()
        cfg["ext_number_chapters"] = self._var_number_chapters.get()
        cfg["ext_generate_on_stop"] = self._var_generate_on_stop.get()
        cfg['ext_blank_paragraph_lines'] = (
            self._var_blank_paragraph_lines.get()
        )
        cfg['ext_kobo_horizontal_layout'] = (
            self._var_kobo_horizontal_layout.get()
        )
        cfg["ext_use_cache"] = self._var_use_cache.get()
        cfg["ext_cache_images"] = self._var_cache_images.get()
        cfg["ext_regular_browser"] = self._var_regular_browser.get()
        cfg["ext_ntk_npia_cover"] = (
            self._var_ntk_npia_cover.get()
        )
        cfg["ext_syosetu_amazon_cover"] = (
            self._var_syosetu_amazon_cover.get()
        )
        cfg["ext_long_image_layout"] = self._var_long_image_layout.get()
        cfg["ext_kpage_skip_last_page"] = (
            self._var_kpage_skip_last_page.get()
        )
        cfg["ext_kpage_keep_filler"] = self._var_kpage_keep_filler.get()
        cfg["ext_kpage_dedupe_images"] = (
            self._var_kpage_dedupe_images.get()
        )
        cfg["ext_kpage_dedupe_leading_images"] = max(
            1, min(10, self._var_kpage_dedupe_leading_images.get())
        )
        try:
            from noveldb.core.settings.store import write_raw
            write_raw(cfg, cfg_path)
        except Exception:
            try:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    dump_source_json(cfg, f, indent=2)
            except Exception:
                pass
