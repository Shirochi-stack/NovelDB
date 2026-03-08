import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import os
import json
import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import io
import base64
import sys
import multiprocessing
import tempfile
import logging
from datetime import datetime

# ---------------------------------------------------------------------------
# File logging setup (freeze / .exe aware)
# Only runs in main process to avoid issues with multiprocessing child processes
# ---------------------------------------------------------------------------
def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

_logger = logging.getLogger('ND')
_LOG_FILE = ""

def _is_main_process():
    try:
        return multiprocessing.current_process().name == 'MainProcess'
    except Exception:
        return True

if _is_main_process():
    _LOG_DIR = os.path.join(_get_base_dir(), 'logs')
    os.makedirs(_LOG_DIR, exist_ok=True)
    _LOG_FILE = os.path.join(_LOG_DIR, f"nd_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    _fh = logging.FileHandler(_LOG_FILE, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    _logger.setLevel(logging.DEBUG)
    _logger.addHandler(_fh)
else:
    _logger.addHandler(logging.NullHandler())

try:
    from PIL import Image
except Exception:
    Image = None

# Internal modules (Assumed to exist based on provided context)
# Since this is a single file simulation, we assume these classes exist 
# or are imported. For this script to run standalone if you have the files, 
# I am keeping the imports exactly as you provided.
from novelpia_auth import NovelpiaAuth
from downloader_core import DownloaderCore, AccessBlockedError
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

def process_text_content(content_json):
    """Parse the viewer_data JSON into readable HTML paragraphs."""
    try:
        data = json.loads(content_json)
        segments = data.get("s")
        if not isinstance(segments, list):
            return f"<p>{html.escape(str(data))}</p>"

        paragraph_html = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "")
            if not text:
                continue
            if "cover-wrapper" in text:
                continue

            text = re.sub(r"<img.+?>", "", text)
            text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
            paragraph_html.append(text)

        if not paragraph_html:
            return "<p>[No text segments found in chapter]</p>"

        return "".join(paragraph_html)
    except Exception as e:
        return f"<p>[Failed to parse chapter: {html.escape(str(e))}]</p>"

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
        return "\n".join(lines)

def _download_image_with_progress(session, url, logger, label="Image", max_retries=3):
    """Stream-download an image, logging progress/speed. Returns bytes or None."""
    for attempt in range(max_retries):
        try:
            r = session.get(url, timeout=30, stream=True)
            if r.status_code != 200:
                logger(f"  ✗ {label}: HTTP {r.status_code}")
                if r.status_code >= 500 and attempt < max_retries - 1:
                    wait = (attempt + 1) * 2
                    logger(f"  ↻ Retrying {label} in {wait}s (attempt {attempt + 2}/{max_retries})...")
                    time.sleep(wait)
                    continue
                return None
            total = int(r.headers.get('content-length', 0))
            chunks, downloaded, t0, last_pct = [], 0, time.time(), 0
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int(downloaded / total * 100)
                        if pct >= last_pct + 10:
                            speed = _format_size(int(downloaded / max(time.time() - t0, 0.001))) + "/s"
                            logger(f"  ↓ {label}: {pct}% ({_format_size(downloaded)}/{_format_size(total)}) [{speed}]")
                            last_pct = pct
            data = b''.join(chunks)
            speed = _format_size(int(len(data) / max(time.time() - t0, 0.001))) + "/s"
            logger(f"  ✓ {label}: {_format_size(len(data))} [{speed}]")
            return data
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                logger(f"  ✗ {label}: {e}")
                logger(f"  ↻ Retrying {label} in {wait}s (attempt {attempt + 2}/{max_retries})...")
                time.sleep(wait)
            else:
                logger(f"  ✗ {label}: {e} (failed after {max_retries} attempts)")
                return None
        return None

def extract_chapter_content_and_images(content_json, font_mapper, session, compress_images, jpeg_quality, image_format, logger, next_image_no, chapter_title="", chapter_num=None):
    html_parts = []
    images = []
    _img_counter = [0]
    try:
        data = json.loads(content_json)
        segments = data.get("s")
        if not isinstance(segments, list):
            return f"<p>{html.escape(str(data))}</p>", images

        img_pat = re.compile(r"<img[^>]+src=\"([^\"]+)\"[^>]*>")

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text", "")
            if not text:
                continue
            if "cover-wrapper" in text:
                continue

            urls = img_pat.findall(text)
            if urls:
                # Use a single-pass regex substitution with a callback to handle each <img> sequentially.
                def handle_img_match(m):
                    url = m.group(1)
                    # Match gui.py behavior: if not starting with http, prefix with https:
                    if url.startswith("http://") or url.startswith("https://"):
                        url_dl = url
                    else:
                        url_dl = "https:" + url

                    _img_counter[0] += 1
                    ch_tag = f" [{chapter_title}]" if chapter_title else ""
                    ch_num = f"[{chapter_num}] " if chapter_num is not None else ""
                    lbl = f"{ch_num}Image #{_img_counter[0]}{ch_tag}"
                    try:
                        img_bytes = _download_image_with_progress(session, url_dl, logger, label=lbl)
                        if not img_bytes:
                            return ""
                        ext = "jpg"
                        lo = url_dl.lower()
                        if '.gif' in lo: ext = "gif"
                        elif '.png' in lo: ext = "png"
                        elif '.webp' in lo: ext = "webp"
                        original_size = len(img_bytes)
                        if compress_images and Image is not None:
                            try:
                                im = Image.open(io.BytesIO(img_bytes))
                                out = io.BytesIO()
                                if ext == "gif":
                                    im.save(out, format="GIF", save_all=True, optimize=True)
                                    img_bytes = out.getvalue()
                                else:
                                    if im.mode not in ("RGB", "L"):
                                        im = im.convert("RGB")
                                    if image_format == "WEBP":
                                        im.save(out, format="WEBP", quality=int(jpeg_quality))
                                        ext = "webp"
                                    elif image_format == "PNG":
                                        im.save(out, format="PNG", optimize=True)
                                        ext = "png"
                                    else:
                                        im.save(out, format="JPEG", quality=int(jpeg_quality), optimize=True)
                                        ext = "jpg"
                                    img_bytes = out.getvalue()
                            except Exception:
                                pass
                            if len(img_bytes) < original_size:
                                saved = (1 - len(img_bytes) / original_size) * 100
                                logger(f"  ⚙ {lbl}: {_format_size(original_size)} → {_format_size(len(img_bytes))} ({saved:.0f}% saved)")

                        n = next_image_no()
                        fname = f"{n}.{ext}"
                        images.append((fname, img_bytes))
                        return f'<img alt="{n}" src="../Images/{fname}" width="100%"/>'
                    except Exception as ex:
                        logger(f"  ✗ {lbl}: {ex}")
                        # Remove tag on exception to match gui.py
                        return ""

                text = img_pat.sub(handle_img_match, text)
                text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
                html_parts.append(f"<p>{text}</p>")
                continue

            text = re.sub(r"<p\s+style=['\"]height:\s*0px;[^>]*>.*?</p>", "", text, flags=re.DOTALL | re.IGNORECASE)
            # Remove only actual HTML tags (tags starting with ASCII letters)
            # This preserves Korean/other text in angle brackets like <주인공>
            text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
            # Remove newlines
            text = text.replace("\n", "")
            if not text:
                continue
            text = html.unescape(text)
            if font_mapper is not None:
                try:
                    text = font_mapper.decode(text)
                except Exception:
                    pass
            if text:
                # Escape the text for safe HTML output (this will convert < to &lt; and > to &gt;)
                html_parts.append(f"<p>{html.escape(text)}</p>")

        if not html_parts:
            return "<p>[No text segments found in chapter]</p>", images
        return "".join(html_parts), images
    except Exception as e:
        return f"<p>[Failed to parse chapter: {html.escape(str(e))}]</p>", images

def _run_webview_login(output_path):
    import traceback

    # Write debug log to the logs/ directory (already created by file logging setup)
    _log_dir = os.path.join(_get_base_dir(), 'logs')
    os.makedirs(_log_dir, exist_ok=True)
    debug_path = os.path.join(_log_dir, "webview_debug.log")
    def _dbg(msg):
        try:
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    _dbg("=== webview login started ===")

    # pythonnet 3.x requires explicit runtime init before clr.AddReference works
    try:
        from pythonnet import load
        load()
        _dbg("pythonnet loaded OK")
    except Exception as e:
        _dbg(f"pythonnet load (non-fatal): {e}")

    try:
        import webview
        _dbg(f"webview imported OK (version: {getattr(webview, '__version__', '?')})")
    except Exception as e:
        _dbg(f"webview import FAILED:\n{traceback.format_exc()}")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass
        return

    def extract_loginkey_js(cookie_str):
        """Extract LOGINKEY from document.cookie string."""
        if not cookie_str:
            return None
        try:
            for p in cookie_str.split(";"):
                s = p.strip()
                if s.startswith("LOGINKEY="):
                    v = s.split("=", 1)[1].strip()
                    if v:
                        return v
        except Exception:
            pass
        return None

    def get_loginkey():
        """Try multiple methods to read LOGINKEY from the webview."""
        # Method 1: window.get_cookies() — reads ALL cookies including HttpOnly
        try:
            cookies = window.get_cookies()
            if cookies:
                for cookie in cookies:
                    name = getattr(cookie, 'name', '')
                    if name == 'LOGINKEY':
                        v = getattr(cookie, 'value', '')
                        if v:
                            return v
        except Exception:
            pass
        # Method 2: document.cookie — only non-HttpOnly cookies
        try:
            js_cookies = window.evaluate_js("document.cookie")
            return extract_loginkey_js(js_cookies)
        except Exception:
            pass
        return None

    key_holder = {"key": None, "initial_key": None}

    def poll_for_key(_window=None):
        _dbg("poll_for_key started, waiting for page load...")
        time.sleep(3)

        initial_key = get_loginkey()
        key_holder["initial_key"] = initial_key
        _dbg(f"initial key: {repr(initial_key[:20] if initial_key else None)}")

        was_on_google = False

        for i in range(900):
            try:
                cur_url = ""
                try:
                    cur_url = window.get_current_url() or ""
                except Exception:
                    pass

                # Track when we navigate to Google OAuth
                if 'accounts.google.com' in cur_url:
                    if not was_on_google:
                        _dbg(f"poll #{i}: navigated to Google OAuth")
                        was_on_google = True

                # After Google OAuth, detect redirect back to novelpia.com
                # This means login succeeded — the SAME cookie is now authenticated
                if was_on_google and 'novelpia.com' in cur_url:
                    _dbg(f"poll #{i}: redirected back to novelpia: {cur_url[:80]}")
                    # Wait for the redirect chain to finish
                    time.sleep(3)

                    # Read the key — it's the same value but now it's authenticated
                    key = get_loginkey()
                    if not key:
                        try:
                            js_cookies = window.evaluate_js("document.cookie")
                            key = extract_loginkey_js(js_cookies)
                        except Exception:
                            pass

                    if key:
                        _dbg(f"Login successful! Key: {key[:20]}...")
                        key_holder["key"] = key
                        try:
                            with open(output_path, "w", encoding="utf-8") as f:
                                f.write(key)
                        except Exception:
                            pass
                        try:
                            window.destroy()
                        except Exception:
                            pass
                        return
                    else:
                        _dbg("Redirect detected but no cookie found, continuing...")
                        was_on_google = False  # Reset to detect next redirect

                if i < 5 or (i % 30 == 0):
                    _dbg(f"poll #{i}: url={cur_url[:60] if cur_url else None}, google={was_on_google}")

            except Exception as e:
                if i < 5:
                    _dbg(f"poll #{i} error: {e}")
            time.sleep(1)
        _dbg("poll_for_key timed out after 900s")

    # Use screen ratio for window size
    try:
        import ctypes
        user32 = ctypes.windll.user32
        _sw = user32.GetSystemMetrics(0)
        _sh = user32.GetSystemMetrics(1)
        _dbg(f"screen size: {_sw}x{_sh} (ctypes)")
    except Exception:
        try:
            import tkinter as _tk
            _r = _tk.Tk()
            _r.withdraw()
            _sw = _r.winfo_screenwidth()
            _sh = _r.winfo_screenheight()
            _r.destroy()
            _dbg(f"screen size: {_sw}x{_sh} (tkinter)")
        except Exception:
            _sw, _sh = 1200, 900
            _dbg(f"screen size: fallback {_sw}x{_sh}")

    try:
        window = webview.create_window(
            "Novelpia Google Login",
            "https://novelpia.com/",
            width=int(_sw * 0.6),
            height=int(_sh * 0.7),
        )
        _dbg("window created, calling webview.start()")
        webview.start(poll_for_key, window, debug=False)
        _dbg("webview.start() returned")
    except Exception as e:
        _dbg(f"webview.start FAILED:\n{traceback.format_exc()}")

    # Window was closed — try one final cookie read
    if key_holder["key"] is None:
        _dbg("Attempting final cookie read after window close...")
        try:
            final_key = get_loginkey()
            _dbg(f"final get_loginkey: {repr(final_key[:20] if final_key else None)}")
            if final_key and final_key != key_holder.get("initial_key"):
                key_holder["key"] = final_key
                try:
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(final_key)
                except Exception:
                    pass
                _dbg(f"LOGINKEY captured post-close: {final_key[:20]}...")
        except Exception as e:
            _dbg(f"final cookie read error: {e}")

    try:
        if key_holder["key"] is None:
            _dbg("No key captured, writing empty output")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("")
    except Exception:
        pass
    _dbg("=== webview login ended ===")


class NovelpiaGUI(tk.Tk):
    def __init__(self):
        # High DPI support - MUST be done before creating the window
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        
        super().__init__()

        # Disable mousewheel scroll on all Spinbox and Combobox widgets
        def _block_scroll(event):
            return "break"
        for widget_class in ("TSpinbox", "TCombobox"):
            self.bind_class(widget_class, "<MouseWheel>", _block_scroll)
            self.bind_class(widget_class, "<Button-4>", _block_scroll)
            self.bind_class(widget_class, "<Button-5>", _block_scroll)

        self.title("ND33")
        
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
        
        # Set window icon if an icon.ico file exists next to this script
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'icon.ico')
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        try:
            style = ttk.Style(self)
            # Try to match the clean look
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        
        # Logic instances
        self.auth = NovelpiaAuth()
        self.log_queue = queue.Queue()
        self.downloader = DownloaderCore(self.auth, self.log_message)
        
        # State variables
        self.var_email = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_loginkey = tk.StringVar()
        self.var_novel_id = tk.StringVar()
        self.var_compress_images = tk.BooleanVar(value=True)
        self.var_jpeg_quality = tk.IntVar(value=50)
        self.var_image_format = tk.StringVar(value="WEBP")  # WEBP, JPEG, PNG
        self.var_compress_cover = tk.BooleanVar(value=False)
        self.var_cover_quality = tk.IntVar(value=90)
        self.var_cover_format = tk.StringVar(value="JPEG")  # JPEG, PNG, WEBP
        self.var_zip_compress_images = tk.BooleanVar(value=False)  # ZIP_STORED by default
        self.var_threads = tk.IntVar(value=1)
        self.var_interval = tk.DoubleVar(value=0.5)
        
        # Range vars
        self.var_from_enabled = tk.BooleanVar(value=False)
        self.var_to_enabled = tk.BooleanVar(value=False)
        self.var_from_num = tk.IntVar(value=1)
        self.var_to_num = tk.IntVar(value=1)
        
        self.var_save_format = tk.StringVar(value="epub")
        self.var_font_path = tk.StringVar()
        self.var_include_notices = tk.BooleanVar(value=True)
        
        # New visual-only variables to match screenshot
        self.var_save_html = tk.BooleanVar(value=False)
        self.var_retry_chapters = tk.BooleanVar(value=False)
        self.var_pdf_toc = tk.BooleanVar(value=False)
        self.var_pdf_page_numbers = tk.BooleanVar(value=False)
        self.var_pdf_counter_layout = tk.BooleanVar(value=False)
        
        # Quick Options variables
        self.var_quick_enable = tk.BooleanVar(value=False)
        self.var_quick_path = tk.StringVar()
        self.var_naming_mode = tk.StringVar(value="title") # title or id
        self.var_append_range = tk.BooleanVar(value=False)
        self.var_use_cache = tk.BooleanVar(value=True)
        self.var_cache_images = tk.BooleanVar(value=False)

        # Runtime helpers
        self.font_mapper = None
        self.image_no = 1
        self.image_lock = threading.Lock()
        self._output_path = None
        self._output_format = "epub"
        self._stop_requested = False
        self._is_downloading = False
        
        self._build_ui()
        self._load_config()
        self._poll_log_queue()
        self._auto_login()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def log_message(self, message):
        _logger.info(message)
        self.log_queue.put(message)

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
        
        ttk.Label(thread_frame, text="sec").pack(side="right")
        ttk.Spinbox(thread_frame, from_=0.0, to=60.0, increment=0.1, textvariable=self.var_interval, width=5).pack(side="right", padx=5)
        ttk.Label(thread_frame, text="Interval").pack(side="right")

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

        # Novel ID
        ttk.Label(dl_inner, text="Novel ID").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(dl_inner, textvariable=self.var_novel_id).grid(row=1, column=1, columnspan=2, sticky="ew", padx=5)

        # Format
        ttk.Label(dl_inner, text="Format").grid(row=2, column=0, sticky="w", pady=5)
        fmt_frame = ttk.Frame(dl_inner)
        fmt_frame.grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(fmt_frame, text="EPUB", variable=self.var_save_format, value="epub").pack(side="left", padx=(5, 15))
        ttk.Radiobutton(fmt_frame, text="TXT", variable=self.var_save_format, value="txt").pack(side="left")
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.var_save_format, value="pdf").pack(side="left", padx=(15, 0))

        # Checkboxes
        ttk.Checkbutton(dl_inner, text="Save as HTML (instead of TXT)", variable=self.var_save_html).grid(row=3, column=0, columnspan=3, sticky="w", pady=2)
        
        comp_frame = ttk.Frame(dl_inner)
        comp_frame.grid(row=4, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(comp_frame, text="Compress Images", variable=self.var_compress_images).pack(side="left")
        ttk.Label(comp_frame, text="Quality").pack(side="left", padx=(15, 5))
        ttk.Spinbox(comp_frame, textvariable=self.var_jpeg_quality, from_=10, to=100, width=5).pack(side="left")
        ttk.Label(comp_frame, text="Format").pack(side="left", padx=(15, 5))
        ttk.Combobox(comp_frame, textvariable=self.var_image_format, values=["WEBP", "JPEG", "PNG"], state="readonly", width=7).pack(side="left")
        
        cover_frame = ttk.Frame(dl_inner)
        cover_frame.grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(cover_frame, text="Compress Cover", variable=self.var_compress_cover).pack(side="left")
        ttk.Label(cover_frame, text="Quality").pack(side="left", padx=(15, 5))
        ttk.Spinbox(cover_frame, textvariable=self.var_cover_quality, from_=10, to=100, width=5).pack(side="left")
        ttk.Label(cover_frame, text="Format").pack(side="left", padx=(15, 5))
        ttk.Combobox(cover_frame, textvariable=self.var_cover_format, values=["JPEG", "WEBP", "PNG"], state="readonly", width=7).pack(side="left")
        ttk.Checkbutton(cover_frame, text="ZIP Compress", variable=self.var_zip_compress_images).pack(side="left", padx=(15, 0))

        notices_frame = ttk.Frame(dl_inner)
        notices_frame.grid(row=6, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(notices_frame, text="Download Author Notices", variable=self.var_include_notices).pack(side="left")
        ttk.Checkbutton(notices_frame, text="Retry Chapters", variable=self.var_retry_chapters).pack(side="left", padx=15)
        chk_cache = ttk.Checkbutton(notices_frame, text="Use Cache", variable=self.var_use_cache)
        chk_cache.pack(side="left", padx=15)
        ToolTip(chk_cache, "Cache downloaded chapter data per novel.\nOn re-download only new chapters are fetched.")
        chk_cache_imgs = ttk.Checkbutton(notices_frame, text="Cache Images", variable=self.var_cache_images)
        chk_cache_imgs.pack(side="left", padx=(0, 15))
        ToolTip(chk_cache_imgs, "Also cache processed images (heavy).\nMakes re-downloads fully offline but uses more disk.")

        pdf_group = ttk.LabelFrame(dl_inner, text="PDF Settings", padding=(6, 4))
        pdf_group.grid(row=7, column=0, columnspan=3, sticky="w", pady=4)
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
        batch_btn_frame.grid(row=8, column=0, columnspan=3, sticky="e", pady=10)
        self.btn_tag_retrieval = ttk.Button(batch_btn_frame, text="Tag Retrieval", command=self.action_tag_retrieval)
        self.btn_tag_retrieval.pack(side="left", padx=(0, 5))
        ttk.Button(batch_btn_frame, text="Batch Download", command=self.action_batch_download).pack(side="left")

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
    # Common Novelpia tags: (korean_tag, english_label)
    COMMON_TAGS = [
        # -- Pinned --
        ("TS", "Genderbend"), ("약피폐", "Moderate Suffering"), ("먼치킨", "Munchkin"),
        ("GL", "Girls Love"), ("BL", "Boys Love"),
        ("백합", "Yuri"), ("추리", "Mystery"), ("로맨스", "Romance"),
        ("하렘", "Harem"), ("역하렘", "Reverse Harem"),
        # -- Recommended --
        ("노벨피아", "Novelpia"), ("판타지", "Fantasy"), ("현대", "Hyundai/Modern"),
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
        w = max(700, int(screen_w * 0.42))
        h = max(500, int(screen_h * 0.38))
        top.geometry(f"{w}x{h}+{(screen_w - w) // 2}+{(screen_h - h) // 2}")
        top.minsize(400, 400)

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
            var = tk.BooleanVar(value=False)
            tag_vars[tag_kr] = var
            cb = ttk.Checkbutton(tag_frame, text=f"{tag_kr} ({tag_en})", variable=var)
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=5, pady=2)
            tag_widgets[tag_kr] = cb

        # Custom tags entry
        custom_frame = ttk.Frame(main_f)
        custom_frame.pack(fill="x", pady=(5, 5))
        ttk.Label(custom_frame, text="Custom tags (comma-separated):").pack(side="left")
        custom_var = tk.StringVar()
        ttk.Entry(custom_frame, textvariable=custom_var).pack(side="left", fill="x", expand=True, padx=5)

        # Options row: AND/OR + Exclude R19
        opts_frame = ttk.Frame(main_f)
        opts_frame.pack(fill="x", pady=(2, 5))

        # AND / OR radio buttons
        mode_var = tk.StringVar(value=getattr(self, '_tag_mode', 'AND'))
        ttk.Label(opts_frame, text="Mode:").pack(side="left")
        ttk.Radiobutton(opts_frame, text="AND", variable=mode_var, value="AND").pack(side="left", padx=(5, 2))
        ttk.Radiobutton(opts_frame, text="OR", variable=mode_var, value="OR").pack(side="left", padx=(2, 15))

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

        # Result display
        result_frame = ttk.Frame(main_f)
        result_frame.pack(fill="x", pady=(5, 0))
        result_label = ttk.Label(result_frame, text="")
        result_label.pack(side="left")

        # Buttons
        btn_frame = ttk.Frame(main_f)
        btn_frame.pack(fill="x", pady=(10, 0))

        def do_retrieve():
            selected = [tag for tag, var in tag_vars.items() if var.get()]
            custom = [t.strip() for t in custom_var.get().split(",") if t.strip()]
            all_tags = selected + custom
            if not all_tags:
                messagebox.showwarning("No tags", "Please select at least one tag.", parent=top)
                return
            btn_go.config(state="disabled")
            result_label.config(text="Searching...")
            self._tag_mode = mode_var.get()
            self._tag_age_filter = AGE_MAP.get(age_var.get(), "")
            threading.Thread(
                target=self._tag_retrieval_worker,
                args=(all_tags, AGE_MAP.get(age_var.get(), ""), mode_var.get(), result_label, btn_go, top),
                daemon=True
            ).start()

        btn_go = ttk.Button(btn_frame, text="Retrieve Novel IDs", command=do_retrieve)
        btn_go.pack(ipadx=20, ipady=8)
        self._tag_btn_go = btn_go

    def _tag_retrieval_worker(self, tags, age_filter, mode, result_label, btn_go, dialog):
        """Background worker for tag-based novel ID retrieval."""
        # Manage stop/download state directly (don't use _set_downloading
        # which would also disable the tag retrieval button itself)
        self.downloader.stop_signal = False
        self.after(0, lambda: self.btn_download.config(state="disabled"))
        self.after(0, lambda: self.btn_stop.pack(pady=5, ipady=5))
        self._is_downloading = True

        delay = max(0.0, self.var_interval.get())
        try:
            novels = self.downloader.fetch_novels_by_tags(tags, delay=delay, age_filter=age_filter, mode=mode)
        except Exception as e:
            self.log_message(f"Tag retrieval failed: {e}")
            self.after(0, lambda: result_label.config(text=f"Error: {e}"))
            self.after(0, lambda: btn_go.config(state="normal"))
            self.after(0, lambda: self.btn_download.config(state="normal"))
            self.after(0, lambda: self.btn_stop.pack_forget())

            self._is_downloading = False
            return

        count = len(novels)
        stopped = self.downloader.stop_signal
        status = f"Found {count} novel(s)." + (" (stopped)" if stopped else "")
        self.after(0, lambda: result_label.config(text=status))
        self.after(0, lambda: btn_go.config(state="normal"))
        self.after(0, lambda: self.btn_download.config(state="normal"))
        self.after(0, lambda: self.btn_stop.pack_forget())
        self._is_downloading = False

        if count == 0:
            return

        # Save as batch file
        tag_label = "+".join(tags)
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
    def action_browse_font(self):
        path = filedialog.askopenfilename(title="Choose font mapping file", filetypes=[("Mapping files", "*.json;*.map;*.txt"), ("All files", "*")])
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
            if hasattr(self, '_tag_btn_go') and self._tag_btn_go:
                try:
                    self._tag_btn_go.config(state="disabled")
                except Exception:
                    pass
            self.btn_stop.pack(pady=5, ipady=5)
        else:
            self.btn_download.config(state="normal")
            self.btn_tag_retrieval.config(state="normal")
            if hasattr(self, '_tag_btn_go') and self._tag_btn_go:
                try:
                    self._tag_btn_go.config(state="normal")
                except Exception:
                    pass
            self.btn_stop.pack_forget()

    def action_download(self):
        if self._is_downloading:
            return
        self._set_downloading(True)
        threading.Thread(target=self._download_worker_wrapper, daemon=True).start()

    def _download_worker_wrapper(self):
        """Wrapper that ensures UI state is restored after download."""
        try:
            self._download_worker()
        finally:
            self.after(0, lambda: self._set_downloading(False))

    def action_batch_download(self):
        """Batch download multiple novels from a list file.

        Accepted line formats (one per line):
          Title,NovelID
          NovelID            (title is fetched automatically)
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

        if self._is_downloading:
            return
        self._set_downloading(True)
        threading.Thread(target=self._batch_download_wrapper, args=(list_path, output_dir), daemon=True).start()

    def _batch_download_wrapper(self, list_path, output_dir):
        """Wrapper that ensures UI state is restored after batch download."""
        try:
            self._batch_download_worker(list_path, output_dir)
        finally:
            self.after(0, lambda: self._set_downloading(False))

    @staticmethod
    def _parse_batch_line(line):
        """Parse a batch list line into (novel_id, title_or_none).

        Supports:
          Title,ID   -> ('ID', 'Title')
          ID         -> ('ID', None)
        Returns (None, None) if the line is unusable.
        """
        if "," in line:
            parts = line.split(",", 1)
            title = parts[0].strip()
            novel_id = parts[1].strip()
            return (novel_id, title) if novel_id else (None, None)
        # No comma — treat the whole line as a novel ID.
        novel_id = line.strip()
        return (novel_id, None) if novel_id else (None, None)

    def _batch_download_worker(self, list_path, output_dir):
        self.lbl_status.config(text="Batch downloading...")
        self.log_message(f"Batch download started: {list_path}")

        prev_quick_enable = self.var_quick_enable.get()
        prev_quick_path = self.var_quick_path.get()
        prev_novel_id = self.var_novel_id.get()
        succeeded, failed, skipped = 0, 0, 0

        try:
            if not os.path.exists(list_path):
                self.log_message(f"List file not found: {list_path}")
                return

            os.makedirs(output_dir, exist_ok=True)

            with open(list_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]

            total = len(lines)
            if total == 0:
                self.log_message("Batch list file is empty.")
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
                    self.log_message(f"[{idx}/{total}] Skipped (invalid): {line}")
                    skipped += 1
                    continue

                if not novel_id.isdigit():
                    self.log_message(f"[{idx}/{total}] Skipped (non-numeric ID '{novel_id}')")
                    skipped += 1
                    continue

                try:
                    if not title:
                        self.log_message(f"[{idx}/{total}] Fetching title for novel {novel_id}...")
                        meta = self.downloader.fetch_metadata(novel_id)
                        title = meta.get("title", novel_id) if meta else novel_id

                    self.log_message(f"[{idx}/{total}] Downloading: {title} ({novel_id})")
                    self.after(0, lambda i=idx, t=total: self.lbl_batch.config(text=f"Batch: {i}/{t}"))
                    self.var_novel_id.set(novel_id)
                    self._download_worker()
                    succeeded += 1
                except Exception as e:
                    failed += 1
                    self.log_message(f"[{idx}/{total}] FAILED novel {novel_id}: {e}")

                time.sleep(2)

            self.log_message(f"Batch complete! OK: {succeeded}, Failed: {failed}, Skipped: {skipped}")
        except Exception as e:
            self.log_message(f"Batch download failed: {e}")
        finally:
            self.var_quick_enable.set(prev_quick_enable)
            self.var_quick_path.set(prev_quick_path)
            self.var_novel_id.set(prev_novel_id)
            self._reset_progress()
            self.after(0, lambda: self.lbl_batch.config(text=""))
            self.lbl_status.config(text="Idle")

    def _download_worker(self):
        self.lbl_status.config(text="Analyzing...")
        # Reset image counter for this download session
        with self.image_lock:
            self.image_no = 1
        novel_id = self.var_novel_id.get().strip()
        if not novel_id:
            messagebox.showwarning("Missing Novel ID", "Please enter a Novel ID before downloading.")
            self.lbl_status.config(text="Idle")
            return

        # Save novel ID to config immediately
        self._save_config()

        # Cache setup (loaded early so metadata can be cached too)
        use_cache = self.var_use_cache.get()
        cache_images = use_cache and self.var_cache_images.get()
        cache_data = {}
        cache_path = None
        if use_cache:
            base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            cache_dir = os.path.join(base_dir, '.cache')
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f'{novel_id}.json')
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    chapter_count = sum(1 for k in cache_data if k != '_meta')
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
                self.lbl_status.config(text="Idle")
                return
            if use_cache:
                cache_data['_meta'] = meta

        # Determine output path
        self._output_format = self.var_save_format.get()
        default_name = meta.get('title', f"novel_{novel_id}") if self.var_naming_mode.get() == 'title' else f"{novel_id}"

        def clean_filename(name):
            return "".join(c for c in name if c not in '\\/:*?"<>|').strip()
        
        def format_ext(fmt):
            if fmt == "epub":
                return "epub"
            if fmt == "pdf":
                return "pdf"
            return "txt"

        if self.var_quick_enable.get() and self.var_quick_path.get():
            folder = self.var_quick_path.get()
            base = clean_filename(default_name)
            if self.var_append_range.get() and self.var_from_enabled.get() and self.var_to_enabled.get():
                base = f"{base}_{self.var_from_num.get()}-{self.var_to_num.get()}"
            ext = format_ext(self._output_format)
            filename = f"[{novel_id}] {base}.{ext}"
            self._output_path = os.path.join(folder, filename)
        else:
            ext = format_ext(self._output_format)
            suggested = f"[{novel_id}] {clean_filename(default_name)}.{ext}"
            path = filedialog.asksaveasfilename(defaultextension='.' + ext, initialfile=suggested, filetypes=[(ext.upper(), f"*.{ext}"), ("All files", "*")])
            if not path:
                self.lbl_status.config(text="Idle")
                return
            self._output_path = path

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
        chapters = self.downloader.fetch_chapter_list(novel_id)
        if not chapters:
            self.lbl_status.config(text="Idle")
            return

        start_idx = (self.var_from_num.get() - 1) if self.var_from_enabled.get() else 0
        end_idx = self.var_to_num.get() if self.var_to_enabled.get() else len(chapters)
        start_idx = max(0, start_idx)
        end_idx = min(len(chapters), end_idx)
        selected = chapters[start_idx:end_idx]
        if not selected:
            self.log_message("No chapters selected.")
            self.lbl_status.config(text="Idle")
            return

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
        save_as_epub = (self._output_format == 'epub')
        save_as_pdf = (self._output_format == 'pdf')
        epub = EpubGenerator(meta, self._output_path if save_as_epub else f"temp.epub", css, self.var_zip_compress_images.get()) if save_as_epub else None

        cover_image = None
        # cover
        if meta.get('cover_url'):
            try:
                r = self.auth.session.get(meta['cover_url'], timeout=15)
                if r.status_code == 200 and r.content:
                    data = r.content
                    cover_ext = "jpg"
                    # Use separate cover compression settings
                    if self.var_compress_cover.get() and Image is not None:
                        try:
                            im = Image.open(io.BytesIO(data))
                            if im.mode not in ("RGB", "L"):
                                im = im.convert("RGB")
                            out = io.BytesIO()
                            
                            # Use selected cover format
                            cover_fmt = self.var_cover_format.get()
                            if cover_fmt == "WEBP":
                                im.save(out, format="WEBP", quality=self.var_cover_quality.get())
                                cover_ext = "webp"
                            elif cover_fmt == "PNG":
                                im.save(out, format="PNG", optimize=True)
                                cover_ext = "png"
                            else:  # JPEG
                                im.save(out, format="JPEG", quality=self.var_cover_quality.get(), optimize=True)
                                cover_ext = "jpg"
                            
                            data = out.getvalue()
                        except Exception:
                            pass
                    cover_image = {"filename": f"cover.{cover_ext}", "data": data}
                    if save_as_epub:
                        epub.add_image(f'cover.{cover_ext}', data)
            except Exception:
                pass

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
            if save_as_epub:
                epub.add_extra_page('info.xhtml', info_html)
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

        threads = max(1, min(32, self.var_threads.get()))
        interval = max(0.0, min(60.0, self.var_interval.get()))
        if threads != self.var_threads.get():
            self.log_message(f"\u26a0 Threads clamped to {threads} (valid range: 1\u201332)")
        if interval != self.var_interval.get():
            self.log_message(f"\u26a0 Interval clamped to {interval}s (valid range: 0\u201360)")

        # Process cached chapters first
        uncached_indices = []
        if use_cache:
            for idx, chap in enumerate(selected_total):
                chap_id = chap['id']
                entry = cache_data.get(chap_id)
                if entry is not None:
                    try:
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
                        else:
                            # JSON-only cache hit — still need to process images
                            content_json = _cache_entry_to_json(entry)
                            hb, imgs = extract_chapter_content_and_images(
                                content_json, self.font_mapper, self.auth.session,
                                self.var_compress_images.get(), self.var_jpeg_quality.get(),
                                self.var_image_format.get(), self.log_message, next_image_no,
                                chapter_title=chap.get('title', ''),
                                chapter_num=self.progress_value + 1
                            )
                            results[idx] = (chap['title'], hb, imgs, chap.get('is_notice', False))
                            self.log_message(f"Cached: {chap['title']}")
                            # Upgrade entry to full cache if cache_images is now on
                            if cache_images:
                                cache_data[chap_id] = {
                                    'json': content_json,
                                    'html': hb,
                                    'images': [[n, base64.b64encode(d).decode('ascii')] for n, d in imgs]
                                }
                    except Exception as e:
                        self.log_message(f"Cache read error {chap.get('title', '?')}: {e}")
                        uncached_indices.append(idx)
                    self._update_progress(value=self.progress_value + 1)
                else:
                    uncached_indices.append(idx)
            cached_count = len(selected_total) - len(uncached_indices)
            if cached_count > 0:
                self.log_message(f"{cached_count} chapters from cache, {len(uncached_indices)} to download.")
        else:
            uncached_indices = list(range(len(selected_total)))

        # Download uncached chapters
        self.lbl_status.config(text="Downloading...")
        dl_stats = _DownloadStats()
        if uncached_indices:
            with ThreadPoolExecutor(max_workers=threads) as executor:
                for i in range(0, len(uncached_indices), threads):
                    if self._stop_requested:
                        self.log_message("Download stopped by user.")
                        break
                    batch = uncached_indices[i:i + threads]
                    f_map = {executor.submit(self.downloader.download_chapter_content, selected_total[x]['id']): x for x in batch}
                    for future in as_completed(f_map):
                        idx = f_map[future]
                        chap = selected_total[idx]
                        try:
                            content_json = future.result()
                            if content_json:
                                hb, imgs = extract_chapter_content_and_images(
                                    content_json, self.font_mapper, self.auth.session,
                                    self.var_compress_images.get(), self.var_jpeg_quality.get(),
                                    self.var_image_format.get(), self.log_message, next_image_no,
                                    chapter_title=chap.get('title', ''),
                                    chapter_num=self.progress_value + 1
                                )
                                results[idx] = (chap['title'], hb, imgs, chap.get('is_notice', False))
                                img_info = f" ({len(imgs)} images)" if imgs else ""
                                self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Downloaded: {chap['title']}{img_info}")
                                # Track stats
                                if imgs:
                                    img_bytes = sum(len(d) for _, d in imgs)
                                    dl_stats.add(len(imgs), img_bytes)
                                    self._img_downloaded_count += len(imgs)
                                    self.after(0, lambda c=self._img_downloaded_count: self.lbl_img_count.config(text=f"Images: {c}"))
                                # Store in cache
                                if use_cache:
                                    if cache_images:
                                        cache_data[chap['id']] = {
                                            'json': content_json,
                                            'html': hb,
                                            'images': [[n, base64.b64encode(d).decode('ascii')] for n, d in imgs]
                                        }
                                    else:
                                        cache_data[chap['id']] = content_json
                            else:
                                # None = failed after retries
                                dl_stats.add_failed(chap['id'], chap.get('title', '?'))
                                self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Failed: {chap.get('title', '?')}")
                        except AccessBlockedError:
                            dl_stats.add_blocked(chap['id'], chap.get('title', '?'))
                            self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Blocked: {chap.get('title', '?')}")
                        except Exception as e:
                            dl_stats.add_failed(chap['id'], chap.get('title', '?'))
                            self.log_message(f"[{self.progress_value + 1}/{self.progress_total}] Error {chap.get('title','?')}: {e}")

                        self._update_progress(value=self.progress_value + 1)

                    if interval > 0:
                        time.sleep(interval)


        # Save cache
        if use_cache and cache_path:
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False)
            except Exception as e:
                self.log_message(f"Cache save error: {e}")

        # Saving
        if save_as_epub:
            for res in results:
                if res:
                    t, h, imgs, notice = res
                    for name, data in imgs:
                        epub.add_image(name, data)
                    epub.add_chapter(t, h, is_notice=notice)
            try:
                epub.generate()
            except Exception as e:
                self.log_message(f"EPUB generation failed: {e}")
        elif save_as_pdf:
            chapters_for_pdf = []
            image_map = {}
            for res in results:
                if res:
                    t, h, imgs, notice = res
                    for name, data in imgs:
                        if name not in image_map:
                            image_map[name] = data
                    chapters_for_pdf.append({"title": t, "html": h, "is_notice": notice})
            try:
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
            except Exception as e:
                self.log_message(f"PDF generation failed: {e}")
        else:
            try:
                with open(self._output_path, 'w', encoding='utf-8') as f:
                    for res in results:
                        if res:
                            t, h, _, _ = res
                            if self.var_save_html.get():
                                f.write(f"<h2>{t}</h2>\n{h}\n\n")
                            else:
                                # Respect the source formatting: convert paragraphs to double newlines and <br> to single.
                                text = h
                                # 1. Convert <br> tags to single newlines.
                                text = re.sub(r'<br\\s*/?>', '\n', text, flags=re.IGNORECASE)
                                # 2. Convert paragraph endings to double newlines.
                                text = re.sub(r'</(p|div)>', '\n\n', text, flags=re.IGNORECASE)
                                # 3. Strip all other HTML tags.
                                text = re.sub(r'<[^>]+>', '', text)
                                # 4. Unescape HTML entities like &nbsp;
                                text = html.unescape(text)
                                # 5. Clean up excess blank lines to a maximum of one, preserving paragraphs.
                                plain = re.sub(r'\n\s*\n', '\n\n', text).strip()
                                f.write(f"{t}\n\n{plain}\n\n\n")
            except Exception as e:
                self.log_message(f"Save failed: {e}")

        # Final summary
        if dl_stats.total_images > 0:
            self.log_message(f"📊 {dl_stats.summary()}")
        warn_text = dl_stats.warnings_summary()
        if warn_text:
            self.log_message(warn_text)
        if self._stop_requested:
            self.log_message("Download stopped by user.")
        else:
            self.log_message("Download Complete!")
        if _LOG_FILE:
            self.log_message(f"Log saved: {_LOG_FILE}")
        self._reset_progress()
        self.lbl_status.config(text="Idle")

    def _load_config(self):
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                # Login settings
                self.var_email.set(cfg.get("email", ""))
                self.var_password.set(cfg.get("wd", ""))
                self.var_loginkey.set(cfg.get("loginkey", ""))
                
                # Thread and interval settings
                self.var_threads.set(cfg.get("thread_num", 1))
                self.var_interval.set(cfg.get("interval_num", 0.5))
                
                # Font mapping
                self.var_font_path.set(cfg.get("mapping_path", ""))
                if self.var_font_path.get():
                    self.font_mapper = FontMapper(self.var_font_path.get())
                
                # Download settings
                self.var_novel_id.set(cfg.get("novel_id", ""))
                self.var_compress_images.set(cfg.get("compress_images", True))
                self.var_jpeg_quality.set(cfg.get("jpeg_quality", 50))
                self.var_image_format.set(cfg.get("image_format", "WEBP"))
                self.var_compress_cover.set(cfg.get("compress_cover", False))
                self.var_cover_quality.set(cfg.get("cover_quality", 90))
                self.var_cover_format.set(cfg.get("cover_format", "JPEG"))
                self.var_zip_compress_images.set(cfg.get("zip_compress_images", False))
                self.var_include_notices.set(cfg.get("include_notices", True))
                self.var_save_format.set(cfg.get("save_format", "epub"))
                self.var_save_html.set(cfg.get("save_html", False))
                self.var_retry_chapters.set(cfg.get("retry_chapters", False))
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

                # Tag retrieval settings
                self._tag_age_filter = cfg.get("tag_age_filter", "")
                self._tag_mode = cfg.get("tag_mode", "AND")
            except: pass
    
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
        cfg = {
            # Login settings
            "email": self.var_email.get(),
            "wd": self.var_password.get(),
            "loginkey": self.var_loginkey.get(),
            
            # Thread and interval settings
            "thread_num": self.var_threads.get(),
            "interval_num": self.var_interval.get(),
            
            # Font mapping
            "mapping_path": self.var_font_path.get(),
            
            # Download settings
            "novel_id": self.var_novel_id.get(),
            "compress_images": self.var_compress_images.get(),
            "jpeg_quality": self.var_jpeg_quality.get(),
            "image_format": self.var_image_format.get(),
            "compress_cover": self.var_compress_cover.get(),
            "cover_quality": self.var_cover_quality.get(),
            "cover_format": self.var_cover_format.get(),
            "zip_compress_images": self.var_zip_compress_images.get(),
            "include_notices": self.var_include_notices.get(),
            "save_format": self.var_save_format.get(),
            "save_html": self.var_save_html.get(),
            "retry_chapters": self.var_retry_chapters.get(),
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

            # Tag retrieval settings
            "tag_age_filter": getattr(self, '_tag_age_filter', ''),
            "tag_mode": getattr(self, '_tag_mode', "AND"),
        }
        try:
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except: pass

    def _on_close(self):
        self._save_config()
        self.destroy()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = NovelpiaGUI()
    app.mainloop()