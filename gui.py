# IMPORTANT: configure DPI scaling BEFORE importing anything that touches Tk
# or Qt. dpi_setup only sets env vars and makes the process DPI-aware at
# this point — no GUI toolkits are imported here, so it is safe.
import dpi_setup
dpi_setup.configure()

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import os
import json
import html
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import io
import base64
import sys
import multiprocessing
import tempfile
import logging
import subprocess
from datetime import datetime

from text_output import write_text_output
from scripts.source_names import dump as dump_source_json
from app_version import APP_NAME
from npia_search_terms import DEFAULT_SEARCH_QUERY_COUNT

# ---------------------------------------------------------------------------
# File logging setup (freeze / .exe aware)
# Only runs in main process to avoid issues with multiprocessing child processes
# ---------------------------------------------------------------------------
def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _writable_data_dir(kind):
    """Return a writable directory path for `kind` ('logs' or '.cache').

    Delegates to `noveldb.core.paths`: `<base_dir>/<kind>` while the app folder is writable,
    otherwise the per-user folder on every OS. The older notes below describe the fallback
    chain this replaces; it is still used if the paths module cannot be imported.

    Windows / Linux: uses `<base_dir>/<kind>` like before. If os.makedirs
    fails (unusual — Program Files install, read-only filesystem, etc.)
    we return None instead of crashing so the caller can skip file I/O
    gracefully.

    macOS: frozen .app bundles in /Applications are read-only, so we try
    an ordered fallback chain and return the first writable candidate.
    If every candidate fails we return None (same contract as above).

    The caller is expected to treat None as "no file I/O for this kind".
    """
    try:
        from noveldb.core import paths as _paths
        if kind == "logs":
            return str(_paths.logs_dir(_get_base_dir()))
        if kind == ".cache":
            return str(_paths.cache_dir(_get_base_dir()))
    except Exception:
        pass
    if sys.platform != "darwin":
        # Windows / Linux: single candidate, same as before the fix — but
        # swallow makedirs errors so the app still launches.
        path = os.path.join(_get_base_dir(), kind)
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:
            return None

    # macOS: try base_dir first (dev / signed non-sandboxed builds),
    # then ~/Library/... (standard location for read-only bundles),
    # then a temp dir as absolute last resort.
    home = os.path.expanduser("~")
    if kind == "logs":
        mac_standard = os.path.join(home, "Library", "Logs", APP_NAME)
    elif kind == ".cache":
        mac_standard = os.path.join(home, "Library", "Caches", APP_NAME)
    else:
        mac_standard = os.path.join(home, "Library", "Application Support", APP_NAME, kind)

    candidates = [
        os.path.join(_get_base_dir(), kind),
        mac_standard,
        os.path.join(tempfile.gettempdir(), f"{APP_NAME}_{kind.lstrip('.')}"),
    ]
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_probe")
            with open(probe, "w") as f:
                f.write("")
            os.remove(probe)
            return path
        except Exception:
            continue
    return None


_logger = logging.getLogger('ND')
_LOG_FILE = ""

def _is_main_process():
    try:
        return multiprocessing.current_process().name == 'MainProcess'
    except Exception:
        return True

# Module-level log setup. On macOS .app bundles installed in /Applications
# the bundle is read-only, so os.makedirs / FileHandler would raise
# PermissionError at import time and crash the whole app before the GUI
# appears. Everything below is wrapped so that the worst case is "no file
# logging this session" instead of "app won't launch".
if _is_main_process():
    _LOG_DIR = _writable_data_dir('logs')
    if _LOG_DIR:
        try:
            _LOG_FILE = os.path.join(
                _LOG_DIR, f"nd_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            )
            _fh = logging.FileHandler(_LOG_FILE, encoding='utf-8')
            _fh.setFormatter(logging.Formatter(
                '[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
            ))
            _logger.setLevel(logging.DEBUG)
            _logger.addHandler(_fh)
        except Exception:
            _LOG_FILE = ""
            _logger.addHandler(logging.NullHandler())
    else:
        _logger.addHandler(logging.NullHandler())
else:
    _logger.addHandler(logging.NullHandler())

try:
    from PIL import Image
except Exception:
    Image = None

# Register AVIF codec with Pillow (if pillow-avif-plugin is installed).
# Importing the plugin side-effect registers AVIF support in PIL.
try:
    import pillow_avif  # noqa: F401
    _AVIF_AVAILABLE = True
except Exception:
    _AVIF_AVAILABLE = False

# Internal modules (Assumed to exist based on provided context)
# Since this is a single file simulation, we assume these classes exist 
# or are imported. For this script to run standalone if you have the files, 
# I am keeping the imports exactly as you provided.
from npia_auth import NpiaAuth
from downloader_core import (
    DownloaderCore,
    AccessBlockedError,
    InvalidChapterPayloadError,
    validate_chapter_payload,
)
from epub_generator import EpubGenerator
from font_mapper import FontMapper

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("tahoma", "8", "normal"),
        )
        label.pack(ipadx=6, ipady=3)
        self.tipwindow = tw

    def _hide(self, _event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None

# Tk-free helpers live in noveldb.core now; re-exported here for existing callers and tests.
from noveldb.core.npia.ids import extract_novel_id
from noveldb.core.images import (
    _count_images_in_json,
    _detect_source_ext,
    _download_image_with_progress,
    _DownloadStats,
    _encode_image_bytes,
    _format_size,
)
from noveldb.core.text import (
    _strip_leading_whitespace,
    extract_chapter_content_and_images,
    process_text_content,
)
from noveldb.login.webview_login import _run_webview_login
from noveldb.core.npia.pipeline import NpiaPipelineMixin


class NpiaGUI(NpiaPipelineMixin, tk.Tk):
    def __init__(self):
        # High DPI support — dpi_setup handles process-level DPI-awareness
        # (already invoked at module load). We apply the configured scale
        # factor to the Tk root below, right after creation.
        super().__init__()
        try:
            dpi_setup.apply_to_tk(self)
        except Exception:
            pass

        # Disable mousewheel scroll on all Spinbox and Combobox widgets
        def _block_scroll(event):
            return "break"
        for widget_class in ("TSpinbox", "TCombobox"):
            self.bind_class(widget_class, "<MouseWheel>", _block_scroll)
            self.bind_class(widget_class, "<Button-4>", _block_scroll)
            self.bind_class(widget_class, "<Button-5>", _block_scroll)

        self.title(APP_NAME)
        
        # Get screen dimensions and calculate window size as percentage
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        
        # Use 60% of screen width and 65% of screen height (adjustable ratios)
        window_width = int(screen_width * 0.60)
        window_height = int(screen_height * 0.65)
        
        # Minimum size: 50% of screen dimensions
        min_width = int(screen_width * 0.50)
        min_height = int(screen_height * 0.45)
        
        # Center the window on screen
        x_position = (screen_width - window_width) // 2
        y_position = (screen_height - window_height) // 2
        
        self.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        self.minsize(min_width, min_height)
        
        # Set window icon (cross-platform: .ico on Windows, .icns/.png on macOS)
        try:
            base_dir = os.path.dirname(__file__)
            if sys.platform == 'darwin':
                # macOS: try .icns first, then .png via PhotoImage
                icns_path = os.path.join(base_dir, 'icon.icns')
                png_path = os.path.join(base_dir, 'icon.png')
                if os.path.exists(icns_path):
                    self.iconbitmap(icns_path)
                elif os.path.exists(png_path):
                    self._icon_img = tk.PhotoImage(file=png_path)
                    self.iconphoto(True, self._icon_img)
            else:
                icon_path = os.path.join(base_dir, 'icon.ico')
                if os.path.exists(icon_path):
                    self.iconbitmap(icon_path)
        except Exception:
            pass

        try:
            style = ttk.Style(self)
            # Try to match the native look per platform
            if "aqua" in style.theme_names():
                style.theme_use("aqua")    # macOS native
            elif "vista" in style.theme_names():
                style.theme_use("vista")   # Windows native
            elif "clam" in style.theme_names():
                style.theme_use("clam")    # Fallback
        except Exception:
            pass
        
        # Logic instances
        self.auth = NpiaAuth()
        self.log_queue = queue.Queue()
        self.downloader = DownloaderCore(self.auth, self.log_message)
        
        # State variables
        self.var_email = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_loginkey = tk.StringVar()
        self.var_novel_id = tk.StringVar()
        self.var_compress_images = tk.BooleanVar(value=True)
        self.var_save_image_urls_only = tk.BooleanVar(value=False)
        self.var_jpeg_quality = tk.IntVar(value=50)
        self.var_image_format = tk.StringVar(value="WEBP")  # WEBP, JPEG, PNG, AVIF
        # When False (default) GIF sources are preserved as .gif (animation kept).
        # When True, GIFs are re-encoded to the selected image_format
        # (animation preserved when format supports it: WEBP/AVIF/APNG).
        self.var_convert_gifs = tk.BooleanVar(value=False)
        # When True, every animated source (GIF, animated WebP, APNG, animated
        # AVIF) is flattened to its first frame regardless of the target format.
        # Dramatically reduces file size at the cost of motion.
        self.var_static_only = tk.BooleanVar(value=False)
        # When True, after the initial EPUB/PDF is written the app will keep
        # re-encoding images at progressively lower quality until the output
        # file is <= var_recompress_target_mb MB (or we hit the min quality).
        self.var_recompress_target_enable = tk.BooleanVar(value=False)
        self.var_recompress_target_mb = tk.DoubleVar(value=20.0)
        self.var_compress_cover = tk.BooleanVar(value=False)
        self.var_cover_quality = tk.IntVar(value=90)
        self.var_cover_format = tk.StringVar(value="JPEG")  # JPEG, PNG, WEBP
        self.var_zip_compress_images = tk.BooleanVar(value=False)  # ZIP_STORED by default
        self.var_threads = tk.IntVar(value=1)
        self.var_image_compression_workers = tk.IntVar(value=1)
        self.var_interval = tk.DoubleVar(value=0.5)
        self.var_interval_max = tk.DoubleVar(value=0.5)
        self.var_shutdown_after_success = tk.BooleanVar(value=False)
        
        # Range vars
        self.var_from_enabled = tk.BooleanVar(value=False)
        self.var_to_enabled = tk.BooleanVar(value=False)
        self.var_from_num = tk.IntVar(value=1)
        self.var_to_num = tk.IntVar(value=1)
        
        # Output formats are independent so one chapter download can be reused
        # to produce any combination of EPUB, TXT, and PDF files. Keep the
        # legacy StringVar as the primary format for config compatibility.
        self.var_save_format = tk.StringVar(value="epub")
        self.var_save_epub = tk.BooleanVar(value=True)
        self.var_save_txt = tk.BooleanVar(value=False)
        self.var_separate_txt = tk.BooleanVar(value=False)
        self.var_save_pdf = tk.BooleanVar(value=False)
        self.var_font_path = tk.StringVar()
        self.var_include_notices = tk.BooleanVar(value=True)
        self.var_number_chapters = tk.BooleanVar(value=False)
        self.var_strip_leading_spaces = tk.BooleanVar(value=False)
        self.var_remove_newlines = tk.BooleanVar(value=False)
        
        # New visual-only variables to match screenshot
        self.var_save_html = tk.BooleanVar(value=False)
        # Maximum retry attempts per chapter (was a dead "Retry Chapters" checkbox).
        # 5 is the default that matches downloader_core.DEFAULT_MAX_RETRIES.
        self.var_max_retries = tk.IntVar(value=5)
        self.var_pdf_toc = tk.BooleanVar(value=False)
        self.var_pdf_page_numbers = tk.BooleanVar(value=False)
        self.var_pdf_counter_layout = tk.BooleanVar(value=False)
        
        # Quick Options variables
        self.var_quick_enable = tk.BooleanVar(value=False)
        self.var_quick_path = tk.StringVar()
        self.var_naming_mode = tk.StringVar(value="title") # title or id
        self.var_append_range = tk.BooleanVar(value=False)
        self.var_auto_login = tk.BooleanVar(value=True)
        self.var_use_cache = tk.BooleanVar(value=True)
        self.var_cache_images = tk.BooleanVar(value=False)

        # DPI / UI scaling (read by dpi_setup at startup, editable in Quick Options).
        # Changes take effect after the app is restarted.
        self.var_auto_dpi_scale = tk.BooleanVar(
            value=True  # overridden by _load_config
        )
        self.var_gui_scale_factor = tk.DoubleVar(
            value=round(float(dpi_setup.get_scale_factor()), 2)
        )

        # Runtime helpers
        self.font_mapper = None
        self.image_no = 1
        self.image_lock = threading.Lock()
        self._output_path = None
        self._output_format = "epub"
        self._output_paths = {}
        self._output_formats = ["epub"]
        self._stop_requested = False
        self._is_downloading = False
        self._last_download_result = None
        
        self._build_ui()
        self._load_config()
        self._ensure_config_exists()
        # Auto-save toggles that change encoder behaviour so the user never
        # loses their preference if the app crashes before normal save paths run.
        self.var_convert_gifs.trace_add("write", lambda *_: self._save_config())
        self.var_static_only.trace_add("write", lambda *_: self._save_config())
        self._poll_log_queue()
        if self.var_auto_login.get():
            self._auto_login()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _sync_image_url_option_states(self, *_args):
        """Gray out image-byte settings while remote URL mode is active."""
        disabled = bool(self.var_save_image_urls_only.get())
        for widget, enabled_state in getattr(
            self, "_image_url_disabled_widgets", ()
        ):
            try:
                widget.configure(
                    state="disabled" if disabled else enabled_state
                )
            except (tk.TclError, AttributeError):
                pass

    def log_message(self, message):
        _logger.info(message)
        self.log_queue.put(message)

    # -- NpiaPipelineMixin hooks, implemented with Tk ----------------------------------

    def _ui_status(self, text):
        self.lbl_status.config(text=text)

    def _ui_batch(self, index, total):
        text = f"Batch: {index}/{total}" if total else ""
        self.after(0, lambda: self.lbl_batch.config(text=text))

    def _ui_images(self, count):
        self.after(0, lambda c=count: self.lbl_img_count.config(text=f"Images: {c}"))

    def _ui_warn(self, title, message, **kwargs):
        messagebox.showwarning(title, message, **kwargs)

    def _ui_confirm(self, title, message, **kwargs):
        return messagebox.askyesno(title, message, **kwargs)

    def _ui_ask_save_path(self, **kwargs):
        return filedialog.asksaveasfilename(**kwargs)

    def _persist_settings(self):
        self._save_config()

    def _session_log_path(self):
        return _LOG_FILE

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.console_text.configure(state='normal')
                self.console_text.insert('end', msg + "\n")
                self.console_text.see('end')
                self.console_text.configure(state='disabled')
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_log_queue)

    def _build_ui(self):
        # Main layout: Left Panel (Fixed/Resize), Right Panel (Expand)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # === LEFT PANEL ===
        left_panel = ttk.Frame(self, padding=(10, 10))
        left_panel.grid(row=0, column=0, sticky="ns")

        # 1. Login Group
        login_frame = ttk.LabelFrame(left_panel, text="Login", padding=(10, 5))
        login_frame.pack(fill="x", pady=(0, 10))
        login_frame.grid_columnconfigure(1, weight=1)

        # Email
        ttk.Label(login_frame, text="Email").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(login_frame, textvariable=self.var_email, width=25).grid(row=0, column=1, sticky="ew", padx=5)
        
        # Password
        ttk.Label(login_frame, text="Password").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(login_frame, textvariable=self.var_password, show="*", width=25).grid(row=1, column=1, sticky="ew", padx=5)
        
        # Login Button (Email)
        btn_login = ttk.Button(login_frame, text="Login", command=self.action_login)
        btn_login.grid(row=0, column=2, rowspan=2, padx=5, sticky="ns")

        # Login Key
        ttk.Label(login_frame, text="LOGINKEY").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(login_frame, textvariable=self.var_loginkey).grid(row=2, column=1, sticky="ew", padx=5)
        
        # Login Button (Key) - Mapped to set key
        btn_key = ttk.Button(login_frame, text="Login", command=self.action_set_key)
        btn_key.grid(row=2, column=2, padx=5)

        # Google Login Button (opens browser, then prompt for LOGINKEY)
        btn_google = ttk.Button(login_frame, text="Login with Google", command=self.action_google_login)
        btn_google.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        # Auto-Login toggle
        ttk.Checkbutton(login_frame, text="Auto-login on startup",
                        variable=self.var_auto_login).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # 2. Font & Threads Group (Visual separation like image)
        # Font Mapping
        font_frame = ttk.Frame(left_panel)
        font_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(font_frame, text="Font Mapping").pack(side="left")
        ttk.Button(font_frame, text="Open...", width=8, command=self.action_browse_font).pack(side="right")
        ttk.Entry(font_frame, textvariable=self.var_font_path).pack(side="right", fill="x", expand=True, padx=5)

        # Threads & Interval
        thread_frame = ttk.Frame(left_panel)
        thread_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(thread_frame, text="Threads").pack(side="left")
        ttk.Spinbox(thread_frame, from_=1, to=32, textvariable=self.var_threads, width=5).pack(side="left", padx=(5, 15))
        lbl_image_workers = ttk.Label(thread_frame, text="Image Compression Workers")
        lbl_image_workers.pack(side="left")
        img_workers_spin = ttk.Spinbox(thread_frame, from_=1, to=32, textvariable=self.var_image_compression_workers, width=5)
        img_workers_spin.pack(side="left", padx=(5, 15))
        ToolTip(
            img_workers_spin,
            "Only affects chapter image compression concurrency.\n"
            "Chapter scraping uses Threads. If Compress Images is off, this has no effect.",
        )
        
        ttk.Label(thread_frame, text="sec").pack(side="right")
        ttk.Spinbox(thread_frame, from_=0.0, to=300.0, increment=0.1, textvariable=self.var_interval_max, width=5).pack(side="right", padx=5)
        ttk.Label(thread_frame, text="Max").pack(side="right")
        ttk.Spinbox(thread_frame, from_=0.0, to=300.0, increment=0.1, textvariable=self.var_interval, width=5).pack(side="right", padx=5)
        ttk.Label(thread_frame, text="Interval Min").pack(side="right")

        # 3. Download Group
        dl_frame = ttk.LabelFrame(left_panel, text="Download", padding=(10, 10))
        dl_frame.pack(fill="both", expand=True)

        # Internal grid for Download settings
        dl_inner = ttk.Frame(dl_frame)
        dl_inner.pack(fill="both", expand=True)

        # Range
        range_frame = ttk.Frame(dl_inner)
        range_frame.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(range_frame, text="Download Range", variable=self.var_from_enabled).pack(side="left")
        ttk.Spinbox(range_frame, textvariable=self.var_from_num, width=5, from_=1, to=99999).pack(side="left", padx=5)
        ttk.Label(range_frame, text="From").pack(side="left", padx=(0, 15))
        ttk.Checkbutton(range_frame, text="", variable=self.var_to_enabled).pack(side="left") # Checkbox without text like image
        ttk.Label(range_frame, text="To").pack(side="left")
        ttk.Spinbox(range_frame, textvariable=self.var_to_num, width=5, from_=1, to=99999).pack(side="left", padx=5)
        chk_number_chapters = ttk.Checkbutton(
            range_frame, text="Number chapters", variable=self.var_number_chapters,
        )
        chk_number_chapters.pack(side="left", padx=(10, 0))
        ToolTip(chk_number_chapters, "Prefix chapter titles with their position in the full book.\nAuthor notices are not numbered.")

        # Novel ID / URL
        ttk.Label(dl_inner, text="Novel ID / URL").grid(row=1, column=0, sticky="w", pady=5)
        ent_novel_id = ttk.Entry(dl_inner, textvariable=self.var_novel_id)
        ent_novel_id.grid(row=1, column=1, columnspan=2, sticky="ew", padx=5)
        ToolTip(ent_novel_id, "Enter a numeric Novel ID (e.g. 123456) or a full Npia URL\n(e.g. https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/123456). URLs are auto-resolved.")

        # Format
        ttk.Label(dl_inner, text="Format").grid(row=2, column=0, sticky="w", pady=5)
        fmt_frame = ttk.Frame(dl_inner)
        fmt_frame.grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(fmt_frame, text="EPUB", variable=self.var_save_epub).pack(side="left", padx=(5, 15))
        ttk.Checkbutton(fmt_frame, text="TXT", variable=self.var_save_txt).pack(side="left")
        ttk.Checkbutton(fmt_frame, text="PDF", variable=self.var_save_pdf).pack(side="left", padx=(15, 0))
        ToolTip(
            fmt_frame,
            "Select one or more output formats. Chapters are downloaded once,\n"
            "then a separate file is generated for every selected format.",
        )

        ttk.Checkbutton(fmt_frame, text="Separate TXT files",
                        variable=self.var_separate_txt).pack(side="left", padx=(15, 0))

        # Checkboxes
        misc_chk_frame = ttk.Frame(dl_inner)
        misc_chk_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(misc_chk_frame, text="Save as HTML (instead of TXT)", variable=self.var_save_html).pack(side="left")
        chk_strip = ttk.Checkbutton(misc_chk_frame, text="Remove Leading Spaces", variable=self.var_strip_leading_spaces)
        chk_strip.pack(side="left", padx=(15, 0))
        ToolTip(chk_strip, "Strip the leading whitespace (\u00a0 / &nbsp;) that\nNpia adds to the beginning of every paragraph.")
        chk_newlines = ttk.Checkbutton(
            misc_chk_frame,
            text="Remove Newlines / <br>",
            variable=self.var_remove_newlines,
        )
        chk_newlines.pack(side="left", padx=(15, 0))
        ToolTip(
            chk_newlines,
            "Off (default): preserve source newlines and <br> line breaks.\n"
            "On: remove them from chapter text.",
        )
        
        image_url_frame = ttk.Frame(dl_inner)
        image_url_frame.grid(row=4, column=0, columnspan=3, sticky="w", pady=2)
        chk_image_urls = ttk.Checkbutton(
            image_url_frame,
            text="Save Image URLs Only",
            variable=self.var_save_image_urls_only,
        )
        chk_image_urls.pack(side="left")
        ToolTip(
            chk_image_urls,
            "Keep website image URLs as remote <img src=\"...\"> elements.\n"
            "No cover or chapter image files are downloaded or embedded.\n"
            "Intended for EPUBs that will be extracted and uploaded to a website.",
        )

        comp_frame = ttk.Frame(dl_inner)
        comp_frame.grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
        chk_compress_images = ttk.Checkbutton(comp_frame, text="Compress Images", variable=self.var_compress_images)
        chk_compress_images.pack(side="left")
        lbl_image_quality = ttk.Label(comp_frame, text="Quality")
        lbl_image_quality.pack(side="left", padx=(15, 5))
        spn_image_quality = ttk.Spinbox(comp_frame, textvariable=self.var_jpeg_quality, from_=10, to=100, width=5)
        spn_image_quality.pack(side="left")
        lbl_image_format = ttk.Label(comp_frame, text="Format")
        lbl_image_format.pack(side="left", padx=(15, 5))
        img_fmt_cb = ttk.Combobox(comp_frame, textvariable=self.var_image_format, values=["WEBP", "JPEG", "PNG", "AVIF"], state="readonly", width=7)
        img_fmt_cb.pack(side="left")
        ToolTip(img_fmt_cb, "Output format for chapter images.\nAVIF requires 'pillow-avif-plugin'.\nGIF sources are preserved as .gif unless 'Convert GIFs' is enabled.")
        chk_convert_gifs = ttk.Checkbutton(comp_frame, text="Convert GIFs", variable=self.var_convert_gifs)
        chk_convert_gifs.pack(side="left", padx=(15, 0))
        ToolTip(
            chk_convert_gifs,
            "Off (default): GIF sources are saved as .gif and animation is preserved.\n"
            "On: GIFs are re-encoded to the selected image format. Animation is\n"
            "preserved for WEBP/AVIF/APNG; JPEG always flattens to the first frame.",
        )
        chk_static_only = ttk.Checkbutton(comp_frame, text="Static images only", variable=self.var_static_only)
        chk_static_only.pack(side="left", padx=(15, 0))
        ToolTip(
            chk_static_only,
            "Off (default): animation is preserved when the target format supports it.\n"
            "On: EVERY animated source (GIF, animated WEBP, APNG, animated AVIF) is\n"
            "flattened to its first frame regardless of format. Smallest files possible.",
        )

        cover_frame = ttk.Frame(dl_inner)
        cover_frame.grid(row=6, column=0, columnspan=3, sticky="w", pady=2)
        chk_compress_cover = ttk.Checkbutton(cover_frame, text="Compress Cover", variable=self.var_compress_cover)
        chk_compress_cover.pack(side="left")
        lbl_cover_quality = ttk.Label(cover_frame, text="Quality")
        lbl_cover_quality.pack(side="left", padx=(15, 5))
        spn_cover_quality = ttk.Spinbox(cover_frame, textvariable=self.var_cover_quality, from_=10, to=100, width=5)
        spn_cover_quality.pack(side="left")
        lbl_cover_format = ttk.Label(cover_frame, text="Format")
        lbl_cover_format.pack(side="left", padx=(15, 5))
        cov_fmt_cb = ttk.Combobox(cover_frame, textvariable=self.var_cover_format, values=["JPEG", "WEBP", "PNG", "AVIF"], state="readonly", width=7)
        cov_fmt_cb.pack(side="left")
        ToolTip(cov_fmt_cb, "Output format for the cover image.\nAVIF requires 'pillow-avif-plugin'.")
        chk_zip_compress = ttk.Checkbutton(cover_frame, text="ZIP Compress", variable=self.var_zip_compress_images)
        chk_zip_compress.pack(side="left", padx=(15, 0))

        # --- Target-size recompression row ---
        target_frame = ttk.Frame(dl_inner)
        target_frame.grid(row=10, column=0, columnspan=3, sticky="w", pady=2)
        chk_target = ttk.Checkbutton(
            target_frame,
            text="Recompress to meet target size",
            variable=self.var_recompress_target_enable,
        )
        chk_target.pack(side="left")
        ToolTip(
            chk_target,
            "After the EPUB/PDF is generated, if it exceeds the target size\n"
            "below, re-encode all images at progressively lower quality and\n"
            "rebuild until the output fits (or quality hits the floor).\n"
            "No effect on text-only TXT output or lossless PNG images.",
        )
        lbl_target_size = ttk.Label(target_frame, text="Target (MB)")
        lbl_target_size.pack(side="left", padx=(15, 5))
        spn_target = ttk.Spinbox(
            target_frame, from_=1.0, to=2048.0, increment=1.0,
            textvariable=self.var_recompress_target_mb, width=7,
        )
        spn_target.pack(side="left")
        ToolTip(spn_target, "Desired maximum size of the final EPUB/PDF, in megabytes.")

        shutdown_frame = ttk.Frame(dl_inner)
        shutdown_frame.grid(row=11, column=0, columnspan=3, sticky="w", pady=2)
        chk_shutdown = ttk.Checkbutton(
            shutdown_frame,
            text="Shut down computer after successful download",
            variable=self.var_shutdown_after_success,
        )
        chk_shutdown.pack(side="left")
        ToolTip(
            chk_shutdown,
            "Windows only. Schedules shutdown 60 seconds after every requested output\n"
            "is generated successfully. It will not run after errors, blocked chapters,\n"
            "warnings, or a user-requested stop. Cancel with: shutdown.exe /a",
        )

        notices_frame = ttk.Frame(dl_inner)
        notices_frame.grid(row=7, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(notices_frame, text="Download Author Notices", variable=self.var_include_notices).pack(side="left")
        ttk.Label(notices_frame, text="Retries").pack(side="left", padx=(15, 3))
        spn_retries = ttk.Spinbox(
            notices_frame, from_=1, to=20, textvariable=self.var_max_retries, width=4
        )
        spn_retries.pack(side="left")
        ToolTip(
            spn_retries,
            "Max download attempts per chapter/image before giving up.\n"
            "Npia's viewer endpoint can be flaky; 5 is the sensible default.\n"
            "Chapter retries use downloader backoff; image retries use Interval as the backoff base.",
        )
        chk_cache = ttk.Checkbutton(notices_frame, text="Use Cache", variable=self.var_use_cache)
        chk_cache.pack(side="left", padx=15)
        ToolTip(chk_cache, "Cache downloaded chapter data per novel.\nOn re-download only new chapters are fetched.")
        chk_cache_imgs = ttk.Checkbutton(notices_frame, text="Cache Images", variable=self.var_cache_images)
        chk_cache_imgs.pack(side="left", padx=(0, 5))
        ToolTip(chk_cache_imgs, "Also cache processed chapter images and cover images (heavy).\nMakes re-downloads fully offline but uses more disk.")

        # URL-only exports keep remote <img> elements, so all byte download,
        # encoding, compression, and image-cache controls are inapplicable.
        # Preserve each widget's normal state so readonly comboboxes are
        # restored correctly when URL-only mode is turned back off.
        self._image_url_disabled_widgets = [
            (lbl_image_workers, "normal"),
            (img_workers_spin, "normal"),
            (chk_compress_images, "normal"),
            (lbl_image_quality, "normal"),
            (spn_image_quality, "normal"),
            (lbl_image_format, "normal"),
            (img_fmt_cb, "readonly"),
            (chk_convert_gifs, "normal"),
            (chk_static_only, "normal"),
            (chk_compress_cover, "normal"),
            (lbl_cover_quality, "normal"),
            (spn_cover_quality, "normal"),
            (lbl_cover_format, "normal"),
            (cov_fmt_cb, "readonly"),
            (chk_zip_compress, "normal"),
            (chk_target, "normal"),
            (lbl_target_size, "normal"),
            (spn_target, "normal"),
            (chk_cache_imgs, "normal"),
        ]
        self.var_save_image_urls_only.trace_add(
            "write", self._sync_image_url_option_states
        )
        self._sync_image_url_option_states()

        btn_open_cache = ttk.Button(
            notices_frame, text="Open Cache Folder", command=self.action_open_cache_folder
        )
        btn_open_cache.pack(side="left", padx=(0, 15))
        ToolTip(
            btn_open_cache,
            "Open the .cache directory in your file explorer.\n"
            "Delete per-novel JSON files here to force a fresh re-download.",
        )

        pdf_group = ttk.LabelFrame(dl_inner, text="PDF Settings", padding=(6, 4))
        pdf_group.grid(row=8, column=0, columnspan=3, sticky="w", pady=4)
        chk_pdf_toc = ttk.Checkbutton(pdf_group, text="Table of Contents", variable=self.var_pdf_toc)
        chk_pdf_toc.pack(side="left")
        chk_pdf_toc_pages = ttk.Checkbutton(pdf_group, text="TOC Page Numbers", variable=self.var_pdf_counter_layout)
        chk_pdf_toc_pages.pack(side="left", padx=15)
        chk_pdf_pages = ttk.Checkbutton(pdf_group, text="Page Numbers", variable=self.var_pdf_page_numbers)
        chk_pdf_pages.pack(side="left", padx=15)

        ToolTip(chk_pdf_toc, "Insert a TOC section after the synopsis page.")
        ToolTip(chk_pdf_toc_pages, "Show page numbers in the TOC (uses PDF counters).")
        ToolTip(chk_pdf_pages, "Show page numbers in the footer on each page.")

        # Batch Download Button (Bottom Right of DL frame)
        batch_btn_frame = ttk.Frame(dl_inner)
        batch_btn_frame.grid(row=9, column=0, columnspan=3, sticky="e", pady=10)
        self.btn_tag_retrieval = ttk.Button(batch_btn_frame, text="Tag Retrieval", command=self.action_tag_retrieval)
        self.btn_tag_retrieval.pack(side="left", padx=(0, 5))
        # Paste Batch: paste a list of IDs/URLs inline instead of using a file.
        self.btn_paste_batch = ttk.Button(batch_btn_frame, text="Paste Batch", command=self.action_paste_batch)
        self.btn_paste_batch.pack(side="left", padx=(0, 5))
        ToolTip(
            self.btn_paste_batch,
            "Open a window to paste multiple Novel IDs or URLs,\n"
            "then download them one after another.\n"
            "Reuses the same engine as Batch Download.",
        )
        self.btn_batch_download = ttk.Button(batch_btn_frame, text="Batch Download", command=self.action_batch_download)
        self.btn_batch_download.pack(side="left")
        ToolTip(
            self.btn_batch_download,
            "Download many novels in one run from a list file.\n"
            "Click the '?' button for full usage details.",
        )
        # Help button that shows a dialog explaining how batch downloading works.
        self.btn_batch_help = ttk.Button(
            batch_btn_frame, text="?", width=2, command=self.action_show_batch_help
        )
        self.btn_batch_help.pack(side="left", padx=(5, 0))
        ToolTip(self.btn_batch_help, "How the batch downloader works")

        # Big Buttons (Right side of DL Frame)
        # We create a sub-frame for the buttons on the right column of the DL group
        btn_frame = ttk.Frame(dl_frame)
        btn_frame.place(relx=1.0, rely=0.0, anchor="ne", x=0, y=50) # Absolute positioning relative to frame to match look
        
        # Actually, using grid is safer. Let's adjust dl_inner to have a column for buttons.
        # But the screenshot shows them spanning height. 
        # Simpler: Put buttons in a frame to the right of the inputs inside dl_frame
        
        # Re-layout dl_frame:
        # Left: Inputs (dl_inner), Right: Buttons
        dl_inner.pack_forget() # reset
        
        dl_inputs = ttk.Frame(dl_frame)
        dl_inputs.pack(side="left", fill="both", expand=True)
        
        # Move inputs to dl_inputs (re-parenting widgets is messy in tk, better to build them there initially)
        # Since I already built them in dl_inner, I'll just pack dl_inner to left
        dl_inner.pack(side="left", fill="both", expand=True, padx=(0, 10))
        
        dl_btns = ttk.Frame(dl_frame)
        dl_btns.pack(side="right", fill="y")
        
        self.btn_download = ttk.Button(dl_btns, text="Download", width=12, command=self.action_download)
        self.btn_download.pack(pady=(40, 5), ipady=10) # Large button
        
        self.btn_stop = ttk.Button(dl_btns, text="Stop", width=12, command=self.action_stop)
        self.btn_stop.pack(pady=5, ipady=5)
        self.btn_stop.pack_forget()  # Hidden initially
        
        btn_options = ttk.Button(dl_btns, text="Quick\nDownload\nOptions", width=12, command=self.open_quick_options)
        btn_options.pack(pady=5)

        btn_external = ttk.Button(dl_btns, text="External\nNovel", width=12, command=self._open_external_dialog)
        btn_external.pack(pady=5)
        ToolTip(
            btn_external,
            "Download Npia or external novels through the persistent "
            "browser scraper\n(syosetu, kakuyomu, biquge, etc.).",
        )

        # === RIGHT PANEL (Console) ===
        right_panel = ttk.Frame(self)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)
        
        self.console_text = tk.Text(right_panel, state='disabled', wrap="word", bg="#f0f0f0", relief="flat")
        self.console_text.pack(fill="both", expand=True)
        
        # Status Bar / Progress
        status_frame = ttk.Frame(self)
        status_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
        
        self.lbl_status = ttk.Label(status_frame, text="Idle")
        self.lbl_status.pack(side="left")
        
        # Progress info label: "12/45 (27%) — ETA: 1m 30s"
        self.lbl_progress_info = ttk.Label(status_frame, text="")
        self.lbl_progress_info.pack(side="right", padx=(10, 10))
        
        # Batch progress label (shown during batch downloads)
        self.lbl_batch = ttk.Label(status_frame, text="")
        self.lbl_batch.pack(side="right", padx=(5, 5))
        
        # Image counter label (running total during downloads)
        self.lbl_img_count = ttk.Label(status_frame, text="")
        self.lbl_img_count.pack(side="right", padx=(5, 5))
        self._img_downloaded_count = 0
        
        # Progress bar
        self.progress = ttk.Progressbar(status_frame, mode='determinate')
        self.progress.pack(side="left", fill="x", expand=True, padx=(20, 5))
        self.progress_value = 0
        self.progress_total = 0
        self._progress_start_time = None

    def _update_progress(self, value=None, total=None, status_text=None):
        """Consolidated progress update: bar, percentage label, and ETA."""
        if value is not None:
            self.progress_value = value
        if total is not None:
            self.progress_total = total
        if status_text is not None:
            self.lbl_status.config(text=status_text)

        if self.progress_total > 0:
            pct = int(self.progress_value / self.progress_total * 100)
            info = f"{self.progress_value}/{self.progress_total} ({pct}%)"
            # ETA calculation
            if self._progress_start_time and self.progress_value > 0:
                elapsed = time.time() - self._progress_start_time
                rate = self.progress_value / max(elapsed, 0.001)  # items per second
                remaining = self.progress_total - self.progress_value
                if rate > 0 and remaining > 0:
                    eta_secs = int(remaining / rate)
                    eta_m, eta_s = divmod(eta_secs, 60)
                    if eta_m > 0:
                        info += f" \u2014 ETA: {eta_m}m {eta_s}s"
                    else:
                        info += f" \u2014 ETA: {eta_s}s"
                elif remaining == 0:
                    info += " \u2014 Done"
        else:
            pct = 0
            info = ""
        def _apply():
            self.progress.configure(value=pct)
            self.lbl_progress_info.config(text=info)
        self.after(0, _apply)

    def _reset_progress(self):
        """Reset progress bar and info label to idle state."""
        self.progress_value = 0
        self.progress_total = 0
        self._progress_start_time = None
        self._img_downloaded_count = 0
        def _apply():
            self.progress.configure(value=0)
            self.lbl_progress_info.config(text="")
            self.lbl_img_count.config(text="")
        self.after(0, _apply)

    # --- Tag Retrieval ---
    # Common Npia tags: (korean_tag, english_label)
    COMMON_TAGS = [
        # -- Pinned --
        ("TS", "Genderbend"), ("약피폐", "Moderate Suffering"), ("먼치킨", "Munchkin"),
        ("GL", "Girls Love"), ("BL", "Boys Love"),
        ("백합", "Yuri"), ("추리", "Mystery"), ("로맨스", "Romance"),
        ("하렘", "Harem"), ("역하렘", "Reverse Harem"),
        # -- Recommended --
        ("npia", "Npia"), ("판타지", "Fantasy"), ("현대", "Hyundai/Modern"),
        ("오리지널", "Original"), ("순애", "Pure Love"), ("일상", "Slice of Life"), ("용사", "Hero"),
        # -- Popular --
        ("학교", "School"), ("19금", "R-19"), ("수녀", "Nun"),
        ("학원", "Academy"), ("썰만화", "Ssulmanhwa"), ("방송", "Broadcast"),
        # -- Genre / Trope --
        ("SF", "Sci-Fi"), ("비극", "Tragedy"), ("악피폐", "Dark Suffering"), ("회귀", "Regression"), ("빙의", "Possession"),
        ("환생", "Reincarnation"), ("헌터", "Hunter"),
        ("현대판타지", "Modern Fantasy"), ("로맨스판타지", "Romance Fantasy"), ("무협", "Martial Arts"),
        ("게임판타지", "Game Fantasy"), ("호러", "Horror"), ("스포츠", "Sports"),
        ("라이트노벨", "Light Novel"), ("팬픽", "Fanfic"),
        ("성장", "Growth"), ("후회물", "Regret"), ("집착물", "Obsession"), ("피카레스크", "Picaresque"),
        ("착각", "Misunderstanding"), ("나데나데", "Nade Nade"),
    ]

    def open_quick_options(self):
        """Quick download options dialog with ratio-based sizing."""
        top = tk.Toplevel(self)
        top.title("Quick Options")
        
        # Use ratio-based sizing relative to screen
        screen_width = top.winfo_screenwidth()
        screen_height = top.winfo_screenheight()
        dialog_width = int(screen_width * 0.25)  # 25% of screen width
        dialog_height = int(screen_height * 0.25)  # 25% of screen height
        
        # Minimum size constraints
        dialog_width = max(dialog_width, 450)
        dialog_height = max(dialog_height, 220)
        
        # Center the dialog
        x_pos = (screen_width - dialog_width) // 2
        y_pos = (screen_height - dialog_height) // 2
        
        top.geometry(f"{dialog_width}x{dialog_height}+{x_pos}+{y_pos}")
        top.resizable(True, True)
        top.minsize(450, 220)
        
        main_f = ttk.Frame(top, padding=10)
        main_f.pack(fill="both", expand=True)
        
        ttk.Checkbutton(main_f, text="Enable Quick Download (No Save Prompt)", variable=self.var_quick_enable).pack(anchor="w", pady=2)
        
        # Save To
        row_path = ttk.Frame(main_f)
        row_path.pack(fill="x", pady=5)
        ttk.Label(row_path, text="Save To:").pack(side="left")
        ttk.Entry(row_path, textvariable=self.var_quick_path).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(row_path, text="Browse...", command=lambda: self.var_quick_path.set(filedialog.askdirectory())).pack(side="right")
        
        # File Naming
        group_naming = ttk.LabelFrame(main_f, text="File Naming", padding=5)
        group_naming.pack(fill="x", pady=5)
        
        row_radios = ttk.Frame(group_naming)
        row_radios.pack(fill="x")
        ttk.Radiobutton(row_radios, text="Save as Title", variable=self.var_naming_mode, value="title").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(row_radios, text="Save as ID", variable=self.var_naming_mode, value="id").pack(side="left")
        
        ttk.Button(row_radios, text="Reset", command=lambda: self.var_quick_path.set("")).pack(side="right")
        ttk.Button(row_radios, text="Clear", command=lambda: self.var_quick_path.set("")).pack(side="right", padx=5)

        ttk.Checkbutton(main_f, text="Append chapter range to title for ongoing novels", variable=self.var_append_range).pack(anchor="w", pady=5)

        # -- DPI / UI scaling --
        dpi_group = ttk.LabelFrame(main_f, text="UI Scaling (applies after restart)", padding=5)
        dpi_group.pack(fill="x", pady=5)

        auto_chk = ttk.Checkbutton(
            dpi_group,
            text="Auto-scale based on screen resolution",
            variable=self.var_auto_dpi_scale,
        )
        auto_chk.pack(anchor="w")
        ToolTip(
            auto_chk,
            "On (default): dpi_setup picks a sensible scale from the primary\n"
            "monitor's resolution.\n"
            "Off: the manual scale factor below is used.",
        )

        scale_row = ttk.Frame(dpi_group)
        scale_row.pack(fill="x", pady=(3, 0))
        ttk.Label(scale_row, text="Manual scale factor (0.5 \u2013 3.0):").pack(side="left")
        scale_spin = ttk.Spinbox(
            scale_row,
            from_=0.5,
            to=3.0,
            increment=0.05,
            textvariable=self.var_gui_scale_factor,
            width=6,
        )
        scale_spin.pack(side="left", padx=5)

        def _sync_scale_state(*_):
            try:
                scale_spin.configure(state=("disabled" if self.var_auto_dpi_scale.get() else "normal"))
            except Exception:
                pass
        _sync_scale_state()
        self.var_auto_dpi_scale.trace_add("write", _sync_scale_state)

        detected = (
            f"Detected: target={dpi_setup.get_scale_factor():.2f}, "
            f"system={dpi_setup.get_system_scale():.2f}"
        )
        ttk.Label(dpi_group, text=detected, foreground="#666").pack(anchor="w", pady=(3, 0))

    def action_tag_retrieval(self):
        """Open tag selection dialog and retrieve novel IDs."""
        # Reuse existing dialog if still open
        if hasattr(self, '_tag_dialog') and self._tag_dialog and self._tag_dialog.winfo_exists():
            self._tag_dialog.lift()
            self._tag_dialog.focus_force()
            return

        top = tk.Toplevel(self)
        self._tag_dialog = top
        top.title("Novel ID Tag Retrieval")
        top.resizable(True, True)

        screen_w = top.winfo_screenwidth()
        screen_h = top.winfo_screenheight()
        w = max(780, int(screen_w * 0.48))
        h = max(500, int(screen_h * 0.38))
        top.geometry(f"{w}x{h}+{(screen_w - w) // 2}+{(screen_h - h) // 2}")
        top.minsize(400, 400)

        # Will be set later once widgets exist
        self._tag_dialog_save_fn = None

        def _on_dialog_close():
            if self._tag_dialog_save_fn:
                self._tag_dialog_save_fn()
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", _on_dialog_close)

        main_f = ttk.Frame(top, padding=10)
        main_f.pack(fill="both", expand=True)

        ttk.Label(main_f, text="Select tags to search for novels:").pack(anchor="w")

        # Scrollable tag grid
        tag_canvas = tk.Canvas(main_f, height=200)
        tag_scrollbar = ttk.Scrollbar(main_f, orient="vertical", command=tag_canvas.yview)
        tag_frame = ttk.Frame(tag_canvas)

        tag_frame.bind("<Configure>", lambda e: tag_canvas.configure(scrollregion=tag_canvas.bbox("all")))
        tag_canvas.create_window((0, 0), window=tag_frame, anchor="nw")
        tag_canvas.configure(yscrollcommand=tag_scrollbar.set)

        tag_canvas.pack(side="left", fill="both", expand=True, pady=(5, 5))
        tag_scrollbar.pack(side="right", fill="y", pady=(5, 5))

        # Create checkbutton vars for each tag
        tag_vars = {}
        cols = 3
        tag_widgets = {}  # store checkbox widgets for enabling/disabling
        for i, (tag_kr, tag_en) in enumerate(self.COMMON_TAGS):
            saved_tags = getattr(self, '_tag_selected_tags', set())
            var = tk.BooleanVar(value=(tag_kr in saved_tags))
            tag_vars[tag_kr] = var
            cb = ttk.Checkbutton(tag_frame, text=f"{tag_kr} ({tag_en})", variable=var)
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=5, pady=2)
            tag_widgets[tag_kr] = cb

        # Custom tags entry
        custom_frame = ttk.Frame(main_f)
        custom_frame.pack(fill="x", pady=(5, 5))
        ttk.Label(custom_frame, text="Custom tags (comma-separated):").pack(side="left")
        custom_var = tk.StringVar(value=getattr(self, '_tag_custom_tags', ''))
        ttk.Entry(custom_frame, textvariable=custom_var).pack(side="left", fill="x", expand=True, padx=5)

        # Query builder section
        qb_frame = ttk.LabelFrame(main_f, text="Query Builder (groups are AND'd, tags within a group are OR'd)")
        qb_frame.pack(fill="x", pady=(5, 5))

        # Group management buttons
        qb_btn_frame = ttk.Frame(qb_frame)
        qb_btn_frame.pack(fill="x", padx=5, pady=(5, 2))

        query_groups = list(getattr(self, '_tag_query_groups', []))  # list of lists of tag strings
        groups_display = ttk.Frame(qb_frame)
        groups_display.pack(fill="x", padx=5, pady=(0, 5))

        def _refresh_groups_display():
            for w in groups_display.winfo_children():
                w.destroy()
            if not query_groups:
                ttk.Label(groups_display, text="No groups defined. Select tags and click 'Add Group'.",
                          foreground="gray").pack(anchor="w")
                return
            for gi, group in enumerate(query_groups):
                row = ttk.Frame(groups_display)
                row.pack(fill="x", pady=1)
                if gi > 0:
                    ttk.Label(row, text="AND", foreground="#888", font=("", 8, "bold")).pack(side="left", padx=(0, 5))
                else:
                    ttk.Label(row, text="     ", font=("", 8)).pack(side="left", padx=(0, 5))
                group_text = " OR ".join(group)
                ttk.Label(row, text=f"Group {gi+1}: {group_text}").pack(side="left")
                rm_btn = ttk.Button(row, text="✕", width=3,
                                     command=lambda i=gi: _remove_group(i))
                rm_btn.pack(side="right", padx=2)

        def _add_group():
            selected = [tag for tag, var in tag_vars.items() if var.get()]
            custom = [t.strip() for t in custom_var.get().split(",") if t.strip()]
            group = selected + custom
            if not group:
                messagebox.showinfo("No tags", "Select at least one tag to add as a group.", parent=top)
                return
            query_groups.append(group)
            # Uncheck tags after adding
            for var in tag_vars.values():
                var.set(False)
            custom_var.set("")
            _refresh_groups_display()

        def _remove_group(idx):
            if 0 <= idx < len(query_groups):
                query_groups.pop(idx)
                _refresh_groups_display()

        def _clear_groups():
            query_groups.clear()
            _refresh_groups_display()

        ttk.Button(qb_btn_frame, text="Add Selected as Group", command=_add_group).pack(side="left", padx=(0, 5), ipadx=10)
        ttk.Button(qb_btn_frame, text="Clear All", command=_clear_groups).pack(side="left")
        ttk.Label(qb_btn_frame, text="  (select tags above, click 'Add' to create a group)",
                  foreground="gray").pack(side="left", padx=(10, 0))

        _refresh_groups_display()

        # Options row: Age filter
        opts_frame = ttk.Frame(main_f)
        opts_frame.pack(fill="x", pady=(2, 5))

        # Age rating dropdown
        AGE_OPTIONS = ["All", "Non-adult only", "Adult only"]
        AGE_MAP = {"All": "", "Non-adult only": "15", "Adult only": "19"}
        saved_age = getattr(self, '_tag_age_filter', '')
        initial_age = {v: k for k, v in AGE_MAP.items()}.get(saved_age, "All")
        age_var = tk.StringVar(value=initial_age)

        ttk.Label(opts_frame, text="Age:").pack(side="left")
        age_combo = ttk.Combobox(opts_frame, textvariable=age_var, values=AGE_OPTIONS,
                                 state="readonly", width=15)
        age_combo.pack(side="left", padx=(5, 0))

        def on_age_change(*_):
            self._tag_age_filter = AGE_MAP.get(age_var.get(), "")
            if age_var.get() != "All":
                if "19금" in tag_vars:
                    tag_vars["19금"].set(False)
                if "19금" in tag_widgets:
                    tag_widgets["19금"].config(state="disabled")
            else:
                if "19금" in tag_widgets:
                    tag_widgets["19금"].config(state="normal")

        age_combo.bind("<<ComboboxSelected>>", on_age_change)
        on_age_change()  # Apply initial state

        # Scrape all toggle
        scrape_all_var = tk.BooleanVar(value=getattr(self, '_tag_scrape_all', False))
        tag_filter_widgets = []  # widgets to disable when scrape-all is on
        scrape_q_widgets = []    # widgets to enable only when scrape-all is on

        scrape_all_frame = ttk.Frame(main_f)
        scrape_all_frame.pack(fill="x", pady=(5, 0))

        def on_scrape_all_toggle():
            is_all = scrape_all_var.get()
            tag_state = "disabled" if is_all else "normal"
            q_state = "normal" if is_all else "disabled"
            for w in tag_filter_widgets:
                try:
                    w.config(state=tag_state)
                except Exception:
                    pass
            for w in scrape_q_widgets:
                try:
                    w.config(state=q_state)
                except Exception:
                    pass

        scrape_all_cb = ttk.Checkbutton(scrape_all_frame, text="Scrape all novels",
                                         variable=scrape_all_var, command=on_scrape_all_toggle)
        scrape_all_cb.pack(side="left")

        # Scrape queries row (below scrape all toggle)
        scrape_q_frame = ttk.Frame(main_f)
        scrape_q_frame.pack(fill="x", pady=(2, 0))
        scrape_q_label = ttk.Label(scrape_q_frame, text="Search queries:")
        scrape_q_label.pack(side="left")
        try:
            initial_scrape_queries = int(getattr(self, '_scrape_max_queries', DEFAULT_SEARCH_QUERY_COUNT))
        except (TypeError, ValueError):
            initial_scrape_queries = DEFAULT_SEARCH_QUERY_COUNT
        initial_scrape_queries = max(1, min(initial_scrape_queries, DEFAULT_SEARCH_QUERY_COUNT))
        scrape_queries_var = tk.IntVar(value=initial_scrape_queries)
        scrape_queries_spin = ttk.Spinbox(scrape_q_frame, from_=1, to=DEFAULT_SEARCH_QUERY_COUNT,
                                          textvariable=scrape_queries_var, width=6,
                                          state="disabled")
        scrape_queries_spin.pack(side="left", padx=(5, 0))

        scrape_desc_frame = ttk.Frame(main_f)
        scrape_desc_frame.pack(fill="x", pady=(0, 5))
        scrape_desc_label = ttk.Label(scrape_desc_frame,
                  text="More queries = better coverage but slower.\n"
                       f"{DEFAULT_SEARCH_QUERY_COUNT} = full current tag + sweep coverage",
                  foreground="gray", justify="left", state="disabled")
        scrape_desc_label.pack(side="left", padx=(2, 0))

        scrape_q_widgets.extend([scrape_q_label, scrape_queries_spin, scrape_desc_label])

        # Collect widgets to disable when scrape-all is on
        for w in tag_widgets.values():
            tag_filter_widgets.append(w)
        tag_filter_widgets.append(custom_frame.winfo_children()[-1])  # Entry widget

        # Apply initial scrape-all state now that all widgets are collected
        on_scrape_all_toggle()

        # Result display
        result_frame = ttk.Frame(main_f)
        result_frame.pack(fill="x", pady=(5, 0))
        result_label = ttk.Label(result_frame, text="")
        result_label.pack(side="left")

        # Buttons
        btn_frame = ttk.Frame(main_f)
        btn_frame.pack(fill="x", pady=(10, 0))

        def _save_dialog_state():
            self._tag_selected_tags = {tag for tag, var in tag_vars.items() if var.get()}
            self._tag_custom_tags = custom_var.get()
            self._tag_scrape_all = scrape_all_var.get()
            self._scrape_max_queries = scrape_queries_var.get()
            self._tag_query_groups = [list(g) for g in query_groups]
            self._tag_age_filter = AGE_MAP.get(age_var.get(), "")

        # Register save function for dialog close
        self._tag_dialog_save_fn = _save_dialog_state

        def do_retrieve():
            _save_dialog_state()
            if scrape_all_var.get():
                max_q = scrape_queries_var.get()
                btn_go.config(state="disabled")
                result_label.config(text=f"Scraping all novels ({max_q} queries)...")
                threading.Thread(
                    target=self._tag_retrieval_worker,
                    args=(None, AGE_MAP.get(age_var.get(), ""), None, result_label, btn_go, top),
                    daemon=True
                ).start()
                return

            if not query_groups:
                # Fall back: use selected tags + custom as a single OR group
                selected = [tag for tag, var in tag_vars.items() if var.get()]
                custom = [t.strip() for t in custom_var.get().split(",") if t.strip()]
                all_tags = selected + custom
                if not all_tags:
                    messagebox.showwarning("No query", "Add at least one group to the query builder,\nor select tags directly.", parent=top)
                    return
                groups_to_search = [[t] for t in all_tags]  # Each tag is AND'd
            else:
                groups_to_search = query_groups

            btn_go.config(state="disabled")
            result_label.config(text="Searching...")
            threading.Thread(
                target=self._tag_retrieval_worker,
                args=(groups_to_search, AGE_MAP.get(age_var.get(), ""), "GROUPS", result_label, btn_go, top),
                daemon=True
            ).start()

        def do_retrieve_top100():
            _save_dialog_state()
            # Ask single vs separate BEFORE fetching
            choice = messagebox.askyesnocancel(
                "Save Rankings",
                "Save all rankings in a single file?\n\n"
                "Yes = Single file (with section headers)\n"
                "No = Separate file per ranking",
                parent=top,
            )
            if choice is None:
                return  # Cancel
            btn_go.config(state="disabled")
            btn_top100.config(state="disabled")
            result_label.config(text="Fetching Top 100 rankings...")
            threading.Thread(
                target=self._top100_worker,
                args=(AGE_MAP.get(age_var.get(), ""), choice, result_label, btn_go, btn_top100, top),
                daemon=True
            ).start()

        btn_go = ttk.Button(btn_frame, text="Retrieve Novel IDs", command=do_retrieve)
        btn_go.pack(side="left", ipadx=20, ipady=8)
        self._tag_btn_go = btn_go

        btn_top100 = ttk.Button(btn_frame, text="Retrieve Top 100", command=do_retrieve_top100)
        btn_top100.pack(side="left", padx=(10, 0), ipadx=20, ipady=8)
        self._tag_btn_top100 = btn_top100

    def _tag_retrieval_worker(self, tags, age_filter, mode, result_label, btn_go, dialog):
        """Background worker for tag-based novel ID retrieval."""
        # Manage stop/download state directly (don't use _set_downloading
        # which would also disable the tag retrieval button itself)
        self.downloader.stop_signal = False
        self.after(0, lambda: self.btn_download.config(state="disabled"))
        self.after(0, lambda: self.btn_stop.pack(pady=5, ipady=5))
        if hasattr(self, '_tag_btn_top100') and self._tag_btn_top100:
            try:
                self.after(0, lambda: self._tag_btn_top100.config(state="disabled"))
            except Exception:
                pass
        self._is_downloading = True

        try:
            delay = max(0.0, self.var_interval.get())
            if tags is None:
                # Scrape all novels mode
                max_q = getattr(self, '_scrape_max_queries', DEFAULT_SEARCH_QUERY_COUNT)
                num_threads = max(1, self.var_threads.get())
                novels = self.downloader.fetch_all_novels(delay=delay, age_filter=age_filter, max_queries=max_q, threads=num_threads)
            else:
                num_threads = max(1, self.var_threads.get())
                novels = self.downloader.fetch_novels_by_tags(tags, delay=delay, age_filter=age_filter, mode=mode, threads=num_threads)
        except Exception as e:
            self.log_message(f"Tag retrieval failed: {e}")
            self.after(0, lambda: result_label.config(text=f"Error: {e}"))
            self.after(0, lambda: btn_go.config(state="normal"))
            self.after(0, lambda: self.btn_download.config(state="normal"))
            self.after(0, lambda: self.btn_stop.pack_forget())
            if hasattr(self, '_tag_btn_top100') and self._tag_btn_top100:
                try:
                    self.after(0, lambda: self._tag_btn_top100.config(state="normal"))
                except Exception:
                    pass

            self._is_downloading = False
            return

        count = len(novels)
        stopped = self.downloader.stop_signal
        status = f"Found {count} novel(s)." + (" (stopped)" if stopped else "")
        self.after(0, lambda: result_label.config(text=status))
        self.after(0, lambda: btn_go.config(state="normal"))
        self.after(0, lambda: self.btn_download.config(state="normal"))
        self.after(0, lambda: self.btn_stop.pack_forget())
        if hasattr(self, '_tag_btn_top100') and self._tag_btn_top100:
            try:
                self.after(0, lambda: self._tag_btn_top100.config(state="normal"))
            except Exception:
                pass
        self._is_downloading = False

        if count == 0:
            return

        # Save as batch file
        if tags is None:
            tag_label = "all_novels"
        elif mode == "GROUPS":
            tag_to_en = {kr: en for kr, en in self.COMMON_TAGS}
            group_labels = []
            for group in tags:
                parts = [t if t.isascii() else tag_to_en.get(t, t).replace(" ", "_") for t in group]
                group_labels.append("_OR_".join(parts))
            tag_label = "+".join(group_labels)
        else:
            tag_to_en = {kr: en for kr, en in self.COMMON_TAGS}
            tag_label = "+".join(
                t if t.isascii() else tag_to_en.get(t, t).replace(" ", "_")
                for t in tags
            )
        if self.var_quick_enable.get() and self.var_quick_path.get():
            # Auto-save to quick path directory
            save_path = os.path.join(self.var_quick_path.get(), f"tag_{tag_label}.txt")
        else:
            save_path = filedialog.asksaveasfilename(
                title="Save novel ID list",
                initialfile=f"tag_{tag_label}.txt",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*")],
            )
            if not save_path:
                return

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                for novel_id in novels:
                    f.write(f"{novel_id}\n")
            self.log_message(f"Saved {count} novel IDs to: {save_path}")
        except Exception as e:
            self.log_message(f"Failed to save: {e}")

    def _top100_worker(self, age_filter, single_file, result_label, btn_go, btn_top100, dialog):
        """Background worker for Top 100 ranking retrieval.

        Args:
            single_file: True = save all in one file, False = separate files per ranking
        """
        self.downloader.stop_signal = False
        self.after(0, lambda: self.btn_download.config(state="disabled"))
        self.after(0, lambda: self.btn_stop.pack(pady=5, ipady=5))
        self._is_downloading = True

        try:
            self.log_message("--- Fetching Top 100 Rankings ---")
            rankings = self.downloader.fetch_top100_rankings(age_filter=age_filter)
        except Exception as e:
            self.log_message(f"Top 100 retrieval failed: {e}")
            self.after(0, lambda: result_label.config(text=f"Error: {e}"))
            self.after(0, lambda: btn_go.config(state="normal"))
            self.after(0, lambda: btn_top100.config(state="normal"))
            self.after(0, lambda: self.btn_download.config(state="normal"))
            self.after(0, lambda: self.btn_stop.pack_forget())
            self._is_downloading = False
            return

        stopped = self.downloader.stop_signal

        # Collect all unique IDs and per-category counts
        all_ids = set()
        category_counts = {}
        for label, ids in rankings.items():
            all_ids.update(ids)
            category_counts[label] = len(ids)

        # Build multi-line summary grouped by audience
        status_lines = [f"Top 100: {len(all_ids)} unique novels"]
        # Group by audience (last word of label)
        audiences_seen = {}
        for label, count in category_counts.items():
            parts = label.split()  # e.g. "daily all" -> ["daily", "all"]
            audience = parts[-1] if len(parts) > 1 else "all"
            period = parts[0] if parts else label
            audiences_seen.setdefault(audience, []).append(f"{period}: {count}")
        for audience, periods in audiences_seen.items():
            status_lines.append(f"  {audience}: {', '.join(periods)}")
        if stopped:
            status_lines.append("(stopped)")
        status = "\n".join(status_lines)
        self.after(0, lambda: result_label.config(text=status))
        self.after(0, lambda: btn_go.config(state="normal"))
        self.after(0, lambda: btn_top100.config(state="normal"))
        self.after(0, lambda: self.btn_download.config(state="normal"))
        self.after(0, lambda: self.btn_stop.pack_forget())
        self._is_downloading = False

        if not all_ids:
            return

        if single_file:
            # --- Single file ---
            lines = []
            for label, ids in rankings.items():
                lines.append(f"# {label.title()} Top 100\n")
                for novel_id in ids:
                    lines.append(f"{novel_id}\n")
                lines.append("\n")

            if self.var_quick_enable.get() and self.var_quick_path.get():
                save_path = os.path.join(self.var_quick_path.get(), "top100_rankings.txt")
            else:
                save_path = filedialog.asksaveasfilename(
                    title="Save Top 100 rankings",
                    initialfile="top100_rankings.txt",
                    defaultextension=".txt",
                    filetypes=[("Text files", "*.txt"), ("All files", "*")],
                )
                if not save_path:
                    return

            try:
                with open(save_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                self.log_message(f"Saved {len(all_ids)} unique novel IDs to: {save_path}")
            except Exception as e:
                self.log_message(f"Failed to save: {e}")
        else:
            # --- Separate files ---
            if self.var_quick_enable.get() and self.var_quick_path.get():
                out_dir = self.var_quick_path.get()
            else:
                out_dir = filedialog.askdirectory(title="Select folder for ranking files")
                if not out_dir:
                    return

            saved = 0
            for label, ids in rankings.items():
                if not ids:
                    continue
                filename = label.replace(" ", "_") + "_top100.txt"
                path = os.path.join(out_dir, filename)
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        for novel_id in ids:
                            f.write(f"{novel_id}\n")
                    saved += 1
                except Exception as e:
                    self.log_message(f"Failed to save {filename}: {e}")
            self.log_message(f"Saved {saved} ranking file(s) to: {out_dir}")

    def action_login(self):
        """Spawns a thread for login to avoid freezing UI."""
        threading.Thread(target=self._login_worker, daemon=True).start()

    def _login_worker(self):
        self.log_message("Attempting login...")
        if self.auth.login(self.var_email.get(), self.var_password.get()):
            self.log_message(f"Login Successful! KEY: {self.auth.loginkey}")
            self.var_loginkey.set(self.auth.loginkey)
        else:
            self.log_message("Login Failed.")

    def action_set_key(self):
        """Set LOGINKEY manually from the text field."""
        self.auth.set_manual_key(self.var_loginkey.get())
        self.log_message("Login Key set manually.")

    def action_google_login(self):
        """Launch embedded webview for Google login and auto-capture LOGINKEY."""
        # Run webview in a separate process to avoid interfering with Tk dialogs/default browser
        with tempfile.NamedTemporaryFile(delete=False, suffix=".loginkey") as tmp:
            key_path = tmp.name

        def poll_file():
            for _ in range(900):
                try:
                    if os.path.exists(key_path):
                        with open(key_path, "r", encoding="utf-8") as f:
                            key = f.read().strip()
                        if key:
                            self.var_loginkey.set(key)
                            self.auth.set_manual_key(self.var_loginkey.get())
                            self.log_message("Google login: LOGINKEY captured.")
                            try:
                                os.remove(key_path)
                            except Exception:
                                pass
                            return
                except Exception:
                    pass
                time.sleep(1)
            self.log_message("Google login: LOGINKEY not captured.")
            debug_log = os.path.join(_get_base_dir(), 'logs', 'webview_debug.log')
            if os.path.exists(debug_log):
                self.log_message(f"Debug log: {debug_log}")
                try:
                    with open(debug_log, "r", encoding="utf-8") as f:
                        for line in f.read().strip().splitlines()[-10:]:
                            self.log_message(f"  {line}")
                except Exception:
                    pass

        try:
            proc = multiprocessing.Process(target=_run_webview_login, args=(key_path,), daemon=True)
            proc.start()
        except Exception as e:
            messagebox.showerror("Google Login Failed", f"Could not start webview process: {e}")
            return

        threading.Thread(target=poll_file, daemon=True).start()
    def action_open_cache_folder(self):
        """Open the per-novel cache directory in the OS file explorer.

        The real location depends on write permissions — see
        `_writable_data_dir('.cache')` for the fallback chain. On Windows
        and Linux this is always `<base_dir>/.cache` (historical
        behaviour). On macOS we fall back to `~/Library/Caches/<APP_NAME>` if
        the bundle location is read-only.
        """
        cache_dir = _writable_data_dir('.cache')
        if not cache_dir:
            self.log_message("Could not create cache folder (no writable location).")
            return

        try:
            if sys.platform == 'win32':
                os.startfile(cache_dir)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', cache_dir])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', cache_dir])
            self.log_message(f"Opened cache folder: {cache_dir}")
        except Exception as e:
            self.log_message(f"Failed to open cache folder ({cache_dir}): {e}")

    def action_browse_font(self):
        path = filedialog.askopenfilename(title="Choose font mapping file", filetypes=[("Mapping files", "*.json *.map *.txt"), ("All files", "*")])
        if path:
            self.var_font_path.set(path)
            try:
                self.font_mapper = FontMapper(path)
                self.log_message(f"Loaded font mapping: {os.path.basename(path)}")
            except Exception as e:
                self.log_message(f"Failed to load font mapping: {e}")

    def action_stop(self):
        """Request cancellation of the current download."""
        if self._is_downloading:
            self._stop_requested = True
            self.downloader.stop_signal = True
            self.log_message("\u26a0 Stop requested \u2014 finishing current operation...")
            self.lbl_status.config(text="Stopping...")

    def _set_downloading(self, active):
        """Toggle UI state for download in progress."""
        self._is_downloading = active
        if active:
            self._stop_requested = False
            self.downloader.stop_signal = False
            self.btn_download.config(state="disabled")
            self.btn_tag_retrieval.config(state="disabled")
            if hasattr(self, 'btn_paste_batch'):
                try:
                    self.btn_paste_batch.config(state="disabled")
                except Exception:
                    pass
            if hasattr(self, 'btn_batch_download'):
                try:
                    self.btn_batch_download.config(state="disabled")
                except Exception:
                    pass
            if hasattr(self, '_tag_btn_go') and self._tag_btn_go:
                try:
                    self._tag_btn_go.config(state="disabled")
                except Exception:
                    pass
            if hasattr(self, '_tag_btn_top100') and self._tag_btn_top100:
                try:
                    self._tag_btn_top100.config(state="disabled")
                except Exception:
                    pass
            self.btn_stop.pack(pady=5, ipady=5)
        else:
            self.btn_download.config(state="normal")
            self.btn_tag_retrieval.config(state="normal")
            if hasattr(self, 'btn_paste_batch'):
                try:
                    self.btn_paste_batch.config(state="normal")
                except Exception:
                    pass
            if hasattr(self, 'btn_batch_download'):
                try:
                    self.btn_batch_download.config(state="normal")
                except Exception:
                    pass
            if hasattr(self, '_tag_btn_go') and self._tag_btn_go:
                try:
                    self._tag_btn_go.config(state="normal")
                except Exception:
                    pass
            if hasattr(self, '_tag_btn_top100') and self._tag_btn_top100:
                try:
                    self._tag_btn_top100.config(state="normal")
                except Exception:
                    pass
            self.btn_stop.pack_forget()

    def action_download(self):
        if self._is_downloading:
            return
        if not self._selected_output_formats():
            messagebox.showwarning(
                "No Output Format",
                "Select at least one output format (EPUB, TXT, or PDF).",
            )
            return
        self._set_downloading(True)
        threading.Thread(target=self._download_worker_wrapper, daemon=True).start()

    def _download_worker_wrapper(self):
        """Wrapper that ensures UI state is restored after download."""
        try:
            result = self._download_worker()
            self._schedule_shutdown_after_success(result)
        except Exception as e:
            self.log_message(f"Download failed: {e}")
            self.lbl_status.config(text="Idle")
        finally:
            self.after(0, lambda: self._set_downloading(False))

    def action_show_batch_help(self):
        """Display a dialog explaining how the batch downloader works."""
        help_text = (
            "Batch Download / Paste Batch \u2014 How it works\n"
            "===========================================\n\n"
            "Two ways to feed the batch engine:\n"
            "  \u2022 Batch Download: pick a .txt / .csv file with one entry per line.\n"
            "  \u2022 Paste Batch: paste the entries directly into a dialog window.\n\n"
            "Accepted line formats (same for both):\n"
            "   NovelID                                   -> title fetched automatically\n"
            "   https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/NovelID        -> URL auto-resolved\n"
            "   Title,NovelID                             -> title used for filename\n"
            "   Title,https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/NovelID\n"
            "   # comment                                 -> skipped\n\n"
            "If 'Quick Download' is enabled, its folder is used as the output\n"
            "directory. Otherwise you will be prompted to pick one.\n\n"
            "For each entry, the app reuses ALL the settings currently shown\n"
            "in the main window \u2014 format(s) (EPUB/TXT/PDF), range, compression,\n"
            "image format (WEBP/JPEG/PNG/AVIF), cover format, notices, cache,\n"
            "threads/image compression workers/interval, etc. So configure those first, then start the batch.\n\n"
            "Behavior and safety:\n"
            "   \u2022 Blank lines and lines starting with '#' are skipped.\n"
            "   \u2022 Entries that cannot be resolved to a numeric ID are skipped.\n"
            "   \u2022 Novels are processed strictly one at a time (sequential).\n"
            "   \u2022 A 2-second pause is inserted between novels to reduce load.\n"
            "   \u2022 Press 'Stop' to cancel; the current novel finishes then exits.\n"
            "   \u2022 Each novel saves to '[<id>] <title>.<ext>' in the output folder.\n"
            "   \u2022 A summary 'OK / Failed / Warnings / Skipped' is logged at the end.\n\n"
            "Tip: 'Tag Retrieval' can generate a list automatically (by tag or Top 100)\n"
            "which you can feed straight into Batch Download."
        )
        messagebox.showinfo("Batch Download \u2014 Help", help_text)

    def action_paste_batch(self):
        """Open a dialog where the user can paste IDs/URLs for a sequential batch.

        The pasted text is remembered in-memory (self._paste_batch_text) and
        restored whenever the dialog is reopened — closing no longer clears it.
        """
        if getattr(self, '_paste_batch_dialog', None) and self._paste_batch_dialog.winfo_exists():
            self._paste_batch_dialog.lift()
            self._paste_batch_dialog.focus_force()
            return

        top = tk.Toplevel(self)
        self._paste_batch_dialog = top
        top.title("Paste Batch")
        top.resizable(True, True)

        screen_w = top.winfo_screenwidth()
        screen_h = top.winfo_screenheight()
        w = max(640, int(screen_w * 0.36))
        h = max(420, int(screen_h * 0.38))
        top.geometry(f"{w}x{h}+{(screen_w - w) // 2}+{(screen_h - h) // 2}")
        top.minsize(500, 320)

        main_f = ttk.Frame(top, padding=10)
        main_f.pack(fill="both", expand=True)

        ttk.Label(
            main_f,
            text=(
                "Paste one Novel ID or Npia URL per line.\n"
                "You can also use 'Title,ID' or 'Title,URL'. '#' lines are comments."
            ),
            wraplength=w - 40,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # Pin buttons to bottom (packed BEFORE text so always visible)
        btns = ttk.Frame(main_f)
        btns.pack(side="bottom", fill="x", pady=(10, 0))

        text = tk.Text(main_f, wrap="word", height=14, undo=True, maxundo=-1)
        text.pack(fill="both", expand=True)

        # Ctrl+Z / Ctrl+Y undo/redo
        def _undo(e):
            try:
                text.edit_undo()
            except tk.TclError:
                pass
            return "break"

        def _redo(e):
            try:
                text.edit_redo()
            except tk.TclError:
                pass
            return "break"

        text.bind("<Control-z>", _undo)
        text.bind("<Control-y>", _redo)
        text.bind("<Control-Z>", _undo)
        text.bind("<Control-Y>", _redo)

        # Restore any text the user had previously entered.
        saved = getattr(self, '_paste_batch_text', '') or ''
        if saved:
            text.insert('1.0', saved)
            text.edit_reset()  # Clear undo history from the restore insert
        text.focus_set()



        def _snapshot_text():
            """Copy the widget's current content to the instance attribute so
            we can restore it next time the dialog is opened."""
            try:
                self._paste_batch_text = text.get('1.0', 'end-1c')
            except Exception:
                # Widget already gone (destroyed) — nothing to capture.
                pass

        def _close():
            _snapshot_text()
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
                    "Please paste at least one Novel ID or Npia URL.",
                    parent=top,
                )
                return

            if self.var_quick_enable.get() and self.var_quick_path.get().strip():
                output_dir = self.var_quick_path.get().strip()
            else:
                output_dir = filedialog.askdirectory(title="Select output directory", parent=top)
                if not output_dir:
                    return

            # Preserve the list so it's still there if the user reopens the dialog.
            _snapshot_text()
            top.destroy()
            self._paste_batch_dialog = None
            self._start_batch_download(lines, output_dir, "pasted batch")

        # Also snapshot when the user hits the window's X button.
        top.protocol("WM_DELETE_WINDOW", _close)

        ttk.Button(btns, text="Start Batch", command=_start).pack(side="right")
        ttk.Button(btns, text="Close", command=_close).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="Clear", command=_clear).pack(side="right", padx=(0, 8))

    def action_batch_download(self):
        """Batch download multiple novels from a list file.

        Accepted line formats (one per line):
          Title,NovelID
          Title,URL
          NovelID            (title is fetched automatically)
          URL                (title is fetched automatically)
          # comment          (skipped)

        The current UI settings (format, range, compression, image/cover format,
        threads, image compression workers, cache, notices, etc.) are applied to every novel in the list.
        If Quick Download is enabled, its folder is used as the output directory;
        otherwise the user is prompted.
        """
        list_path = filedialog.askopenfilename(
            title="Select batch list file",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*")],
        )
        if not list_path:
            return

        # Use Quick Download path if enabled, otherwise ask
        if self.var_quick_enable.get() and self.var_quick_path.get().strip():
            output_dir = self.var_quick_path.get().strip()
        else:
            output_dir = filedialog.askdirectory(title="Select output directory")
            if not output_dir:
                return

        self._start_batch_download(list_path, output_dir, f"batch file: {os.path.basename(list_path)}")

    def _start_batch_download(self, batch_source, output_dir, source_label):
        """Shared entry point: kicks off the batch worker from either a file or list."""
        if self._is_downloading:
            return
        if not self._selected_output_formats():
            messagebox.showwarning(
                "No Output Format",
                "Select at least one output format (EPUB, TXT, or PDF).",
            )
            return
        self._set_downloading(True)
        threading.Thread(
            target=self._batch_download_wrapper,
            args=(batch_source, output_dir, source_label),
            daemon=True,
        ).start()

    def _batch_download_wrapper(self, batch_source, output_dir, source_label):
        """Wrapper that ensures UI state is restored after batch download."""
        try:
            result = self._batch_download_worker(batch_source, output_dir, source_label)
            self._schedule_shutdown_after_success(result)
        finally:
            self.after(0, lambda: self._set_downloading(False))


    def _load_config(self):
        cfg_path = self._config_path()
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # Login settings
                self.var_email.set(cfg.get("email", ""))
                self.var_password.set(cfg.get("wd", ""))
                self.var_loginkey.set(cfg.get("loginkey", ""))
                
                # Thread and interval settings
                self.var_threads.set(cfg.get("thread_num", 1))
                self.var_image_compression_workers.set(cfg.get("image_compression_workers", 1))
                self.var_interval.set(cfg.get("interval_num", 0.5))
                self.var_interval_max.set(
                    cfg.get("interval_max_num", cfg.get("interval_num", 0.5))
                )
                self.var_shutdown_after_success.set(
                    cfg.get("shutdown_after_success", False)
                )
                
                # Font mapping
                self.var_font_path.set(cfg.get("mapping_path", ""))
                if self.var_font_path.get():
                    self.font_mapper = FontMapper(self.var_font_path.get())
                
                # Download settings
                self.var_novel_id.set(cfg.get("novel_id", ""))
                self.var_compress_images.set(cfg.get("compress_images", True))
                self.var_save_image_urls_only.set(cfg.get("save_image_urls_only", False))
                self.var_jpeg_quality.set(cfg.get("jpeg_quality", 50))
                self.var_image_format.set(cfg.get("image_format", "WEBP"))
                self.var_convert_gifs.set(cfg.get("convert_gifs", False))
                self.var_static_only.set(cfg.get("static_only", False))
                self.var_recompress_target_enable.set(cfg.get("recompress_target_enable", False))
                try:
                    self.var_recompress_target_mb.set(float(cfg.get("recompress_target_mb", 20.0)))
                except (TypeError, ValueError):
                    self.var_recompress_target_mb.set(20.0)
                self.var_compress_cover.set(cfg.get("compress_cover", False))
                self.var_cover_quality.set(cfg.get("cover_quality", 90))
                self.var_cover_format.set(cfg.get("cover_format", "JPEG"))
                self.var_zip_compress_images.set(cfg.get("zip_compress_images", False))
                self.var_include_notices.set(cfg.get("include_notices", True))
                self.var_number_chapters.set(cfg.get("number_chapters", False))
                self.var_strip_leading_spaces.set(cfg.get("strip_leading_spaces", False))
                self.var_remove_newlines.set(cfg.get("remove_newlines", False))
                configured_formats = cfg.get("save_formats")
                if not isinstance(configured_formats, list):
                    configured_formats = [cfg.get("save_format", "epub")]
                configured_formats = [
                    fmt for fmt in configured_formats
                    if fmt in ("epub", "txt", "pdf")
                ]
                if not configured_formats:
                    configured_formats = ["epub"]
                self.var_save_epub.set("epub" in configured_formats)
                self.var_save_txt.set("txt" in configured_formats)
                self.var_save_pdf.set("pdf" in configured_formats)
                self.var_save_format.set(configured_formats[0])
                self.var_save_html.set(cfg.get("save_html", False))
                self.var_separate_txt.set(cfg.get("separate_txt", False))
                # "retry_chapters" was a dead boolean; migrate silently to the new
                # "max_retries" integer (default 5 if not present).
                try:
                    self.var_max_retries.set(int(cfg.get("max_retries", 5)))
                except (TypeError, ValueError):
                    self.var_max_retries.set(5)
                self.var_use_cache.set(cfg.get("use_cache", False))
                self.var_cache_images.set(cfg.get("cache_images", False))
                self.var_pdf_toc.set(cfg.get("pdf_toc", False))
                self.var_pdf_page_numbers.set(cfg.get("pdf_page_numbers", False))
                self.var_pdf_counter_layout.set(cfg.get("pdf_counter_layout", False))
                
                # Range settings
                self.var_from_enabled.set(cfg.get("from_enabled", False))
                self.var_to_enabled.set(cfg.get("to_enabled", False))
                self.var_from_num.set(cfg.get("from_num", 1))
                self.var_to_num.set(cfg.get("to_num", 1))
                
                # Quick download options
                self.var_quick_enable.set(cfg.get("quick_enable", False))
                self.var_quick_path.set(cfg.get("quick_path", ""))
                self.var_naming_mode.set(cfg.get("naming_mode", "title"))
                self.var_append_range.set(cfg.get("append_range", False))
                self.var_auto_login.set(cfg.get("auto_login", True))

                # DPI / UI scaling (consumed by dpi_setup at next startup).
                self.var_auto_dpi_scale.set(cfg.get("auto_dpi_scale", True))
                try:
                    raw_scale = float(cfg.get("gui_scale_factor",
                                              dpi_setup.get_scale_factor()))
                except (TypeError, ValueError):
                    raw_scale = float(dpi_setup.get_scale_factor())
                self.var_gui_scale_factor.set(round(raw_scale, 2))

                # Tag retrieval settings
                self._tag_age_filter = cfg.get("tag_age_filter", "")
                self._tag_query_groups = cfg.get("tag_query_groups", [])
                self._tag_selected_tags = set(cfg.get("tag_selected_tags", []))
                self._tag_custom_tags = cfg.get("tag_custom_tags", "")
                self._tag_scrape_all = cfg.get("tag_scrape_all", False)
                try:
                    scrape_max_queries = int(cfg.get("scrape_max_queries", DEFAULT_SEARCH_QUERY_COUNT))
                except (TypeError, ValueError):
                    scrape_max_queries = DEFAULT_SEARCH_QUERY_COUNT
                if scrape_max_queries == 100:
                    scrape_max_queries = DEFAULT_SEARCH_QUERY_COUNT
                self._scrape_max_queries = scrape_max_queries
            except Exception as e:
                self.log_message(f"Could not load config.json: {e}")

    def _config_path(self):
        try:
            from noveldb.core import paths as _paths
            return str(_paths.config_path(_get_base_dir()))
        except Exception:
            return os.path.join(_get_base_dir(), "config.json")

    def _ensure_config_exists(self):
        """Create config.json on first launch using current default settings."""
        if os.path.exists(self._config_path()):
            return
        self._save_config()
    
    def _auto_login(self):
        """Automatically login on startup if credentials are available."""
        # Prefer a real login when email/password are saved so the session is refreshed.
        if self.var_email.get() and self.var_password.get():
            threading.Thread(target=self._login_worker, daemon=True).start()
        # If only a login key is available, fall back to injecting it.
        elif self.var_loginkey.get():
            self.auth.set_manual_key(self.var_loginkey.get())
            self.log_message("Auto-login: Using saved login key.")

    def _save_config(self):
        """Save current settings to config.json."""
        selected_output_formats = self._selected_output_formats()
        primary_output_format = (
            selected_output_formats[0] if selected_output_formats else "epub"
        )
        self.var_save_format.set(primary_output_format)
        cfg = {
            # Login settings
            "email": self.var_email.get(),
            "wd": self.var_password.get(),
            "loginkey": self.var_loginkey.get(),
            
            # Thread and interval settings
            "thread_num": self.var_threads.get(),
            "image_compression_workers": self.var_image_compression_workers.get(),
            "interval_num": self.var_interval.get(),
            "interval_max_num": self.var_interval_max.get(),
            "shutdown_after_success": self.var_shutdown_after_success.get(),
            
            # Font mapping
            "mapping_path": self.var_font_path.get(),
            
            # Download settings
            "novel_id": self.var_novel_id.get(),
            "compress_images": self.var_compress_images.get(),
            "save_image_urls_only": self.var_save_image_urls_only.get(),
            "jpeg_quality": self.var_jpeg_quality.get(),
            "image_format": self.var_image_format.get(),
            "convert_gifs": self.var_convert_gifs.get(),
            "static_only": self.var_static_only.get(),
            "recompress_target_enable": self.var_recompress_target_enable.get(),
            "recompress_target_mb": round(float(self.var_recompress_target_mb.get() or 0), 2),
            "compress_cover": self.var_compress_cover.get(),
            "cover_quality": self.var_cover_quality.get(),
            "cover_format": self.var_cover_format.get(),
            "zip_compress_images": self.var_zip_compress_images.get(),
            "include_notices": self.var_include_notices.get(),
            "number_chapters": self.var_number_chapters.get(),
            "strip_leading_spaces": self.var_strip_leading_spaces.get(),
            "remove_newlines": self.var_remove_newlines.get(),
            # `save_format` is retained for older app/bot versions; new GUI
            # builds restore the full checkbox selection from `save_formats`.
            "save_format": primary_output_format,
            "save_formats": selected_output_formats,
            "separate_txt": self.var_separate_txt.get(),
            "save_html": self.var_save_html.get(),
            "max_retries": int(self.var_max_retries.get() or 5),
            "use_cache": self.var_use_cache.get(),
            "cache_images": self.var_cache_images.get(),
            "pdf_toc": self.var_pdf_toc.get(),
            "pdf_page_numbers": self.var_pdf_page_numbers.get(),
            "pdf_counter_layout": self.var_pdf_counter_layout.get(),
            
            # Range settings
            "from_enabled": self.var_from_enabled.get(),
            "to_enabled": self.var_to_enabled.get(),
            "from_num": self.var_from_num.get(),
            "to_num": self.var_to_num.get(),
            
            # Quick download options
            "quick_enable": self.var_quick_enable.get(),
            "quick_path": self.var_quick_path.get(),
            "naming_mode": self.var_naming_mode.get(),
            "append_range": self.var_append_range.get(),
            "auto_login": self.var_auto_login.get(),

            # DPI / UI scaling (read by dpi_setup on next startup)
            "auto_dpi_scale": self.var_auto_dpi_scale.get(),
            "gui_scale_factor": round(float(self.var_gui_scale_factor.get()), 2),

            # Tag retrieval settings
            "tag_age_filter": getattr(self, '_tag_age_filter', ''),
            "tag_query_groups": getattr(self, '_tag_query_groups', []),
            "tag_selected_tags": list(getattr(self, '_tag_selected_tags', set())),
            "tag_custom_tags": getattr(self, '_tag_custom_tags', ''),
            "tag_scrape_all": getattr(self, '_tag_scrape_all', False),
            "scrape_max_queries": getattr(self, '_scrape_max_queries', DEFAULT_SEARCH_QUERY_COUNT),
        }
        try:
            cfg_path = self._config_path()
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            # Read existing config to preserve ext_* keys from external dialog
            existing = {}
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    pass
            existing.update(cfg)
            tmp_path = cfg_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                dump_source_json(existing, f, indent=2)
            os.replace(tmp_path, cfg_path)
        except Exception as e:
            self.log_message(f"Could not save config.json: {e}")

    def _open_external_dialog(self):
        """Open the External Novel download dialog."""
        try:
            # Reuse an existing dialog if it's still alive (just hidden)
            dlg = getattr(self, '_external_dlg', None)
            if dlg is not None and dlg.winfo_exists():
                dlg.deiconify()
                dlg.lift()
                dlg.focus_force()
                return

            from external_dialog import ExternalNovelDialog
            self._external_dlg = ExternalNovelDialog(
                self, retry_variable=self.var_max_retries
            )
        except ImportError as e:
            self.log_message(f"External novel dialog unavailable: {e}")
            self.log_message(
                "Ensure external_scraper.py, external_dialog.py, "
                "rules-lib.js, and bridge.js are present."
            )
        except Exception as e:
            self.log_message(f"Error opening external dialog: {e}")

    def _on_close(self):
        self._save_config()
        # Clean up the external dialog's worker thread / browser if alive
        dlg = getattr(self, '_external_dlg', None)
        if dlg is not None:
            try:
                dlg._on_app_exit()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = NpiaGUI()
    app.mainloop()
