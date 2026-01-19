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

try:
    from PIL import Image
except Exception:
    Image = None

# Internal modules (Assumed to exist based on provided context)
# Since this is a single file simulation, we assume these classes exist 
# or are imported. For this script to run standalone if you have the files, 
# I am keeping the imports exactly as you provided.
from novelpia_auth import NovelpiaAuth
from downloader_core import DownloaderCore
from epub_generator import EpubGenerator
from font_mapper import FontMapper

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

def extract_chapter_content_and_images(content_json, font_mapper, session, compress_images, jpeg_quality, image_format, logger, next_image_no):
    html_parts = []
    images = []
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

                    try:
                        r = session.get(url_dl, timeout=15)
                        if r.status_code != 200 or not r.content:
                            logger(f"Image fetch failed: {url_dl}")
                            # Remove the tag on failure (match gui.py)
                            return ""
                        img_bytes = r.content
                        ext = "jpg"
                        if compress_images and Image is not None:
                            try:
                                im = Image.open(io.BytesIO(img_bytes))
                                if im.mode not in ("RGB", "L"):
                                    im = im.convert("RGB")
                                out = io.BytesIO()
                                
                                # Use selected format
                                if image_format == "WEBP":
                                    im.save(out, format="WEBP", quality=int(jpeg_quality))
                                    ext = "webp"
                                elif image_format == "PNG":
                                    im.save(out, format="PNG", optimize=True)
                                    ext = "png"
                                else:  # JPEG
                                    im.save(out, format="JPEG", quality=int(jpeg_quality), optimize=True)
                                    ext = "jpg"
                                
                                img_bytes = out.getvalue()
                            except Exception:
                                pass

                        n = next_image_no()
                        fname = f"{n}.{ext}"
                        images.append((fname, img_bytes))
                        replacement = f"<img alt=\"{n}\" src=\"../Images/{fname}\" width=\"100%\"/>"
                        return replacement
                    except Exception as ex:
                        logger(f"Image error: {ex}")
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

class NovelpiaGUI(tk.Tk):
    def __init__(self):
        # High DPI support - MUST be done before creating the window
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        
        super().__init__()
        self.title("Novelpia Downloader V5.0")
        
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
        self.var_threads = tk.IntVar(value=4)
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
        
        # Quick Options variables
        self.var_quick_enable = tk.BooleanVar(value=False)
        self.var_quick_path = tk.StringVar()
        self.var_naming_mode = tk.StringVar(value="title") # title or id
        self.var_append_range = tk.BooleanVar(value=False)

        # Runtime helpers
        self.font_mapper = None
        self.image_no = 1
        self.image_lock = threading.Lock()
        self._output_path = None
        self._output_format = "epub"
        
        self._build_ui()
        self._load_config()
        self._poll_log_queue()
        self._auto_login()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def log_message(self, message):
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

        # Batch Download Button (Bottom Right of DL frame)
        # Using grid weight to push it down/right
        btn_batch = ttk.Button(dl_inner, text="Batch Download", state="disabled") # Placeholder functionality
        btn_batch.grid(row=7, column=2, sticky="e", pady=10)

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
        
        btn_download = ttk.Button(dl_btns, text="Download", width=12, command=self.action_download)
        btn_download.pack(pady=(40, 5), ipady=10) # Large button
        
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
        
        # The screenshot has "Idle" at bottom left.
        # It also has V5.0 at bottom right.
        ttk.Label(status_frame, text="V5.0").pack(side="right")
        
        # Progress bar (Hidden in screenshot or thin? Added for functionality)
        self.progress = ttk.Progressbar(status_frame, mode='determinate')
        self.progress.pack(side="left", fill="x", expand=True, padx=20)
        self.progress_value = 0
        self.progress_total = 0

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
        
        ttk.Button(row_radios, text="Reset", command=lambda: self.var_quick_path.set("")).pack(side="right") # Placeholder for Reset
        ttk.Button(row_radios, text="Clear", command=lambda: self.var_quick_path.set("")).pack(side="right", padx=5)

        ttk.Checkbutton(main_f, text="Append chapter range to title for ongoing novels", variable=self.var_append_range).pack(anchor="w", pady=5)

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
    def action_browse_font(self):
        path = filedialog.askopenfilename(title="Choose font mapping file", filetypes=[("Mapping files", "*.json;*.map;*.txt"), ("All files", "*")])
        if path:
            self.var_font_path.set(path)
            try:
                self.font_mapper = FontMapper(path)
                self.log_message(f"Loaded font mapping: {os.path.basename(path)}")
            except Exception as e:
                self.log_message(f"Failed to load font mapping: {e}")

    def action_download(self):
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        self.lbl_status.config(text="Analyzing...")
        novel_id = self.var_novel_id.get().strip()
        if not novel_id:
            messagebox.showwarning("Missing Novel ID", "Please enter a Novel ID before downloading.")
            self.lbl_status.config(text="Idle")
            return

        meta = self.downloader.fetch_metadata(novel_id)
        if not meta:
            self.lbl_status.config(text="Idle")
            return

        # Determine output path
        self._output_format = self.var_save_format.get()
        default_name = meta.get('title', f"novel_{novel_id}") if self.var_naming_mode.get() == 'title' else f"{novel_id}"

        def clean_filename(name):
            return "".join(c for c in name if c not in '\\/:*?"<>|').strip()

        if self.var_quick_enable.get() and self.var_quick_path.get():
            folder = self.var_quick_path.get()
            base = clean_filename(default_name)
            if self.var_append_range.get() and self.var_from_enabled.get() and self.var_to_enabled.get():
                base = f"{base}_{self.var_from_num.get()}-{self.var_to_num.get()}"
            ext = 'epub' if self._output_format == 'epub' else 'txt'
            filename = f"[{novel_id}] {base}.{ext}"
            self._output_path = os.path.join(folder, filename)
        else:
            ext = 'epub' if self._output_format == 'epub' else 'txt'
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
        save_as_epub = (self._output_format == 'epub')
        epub = EpubGenerator(meta, self._output_path if save_as_epub else f"temp.epub", css, self.var_zip_compress_images.get())

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
                    epub.add_image(f'cover.{cover_ext}', data)
            except Exception:
                pass

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
            epub.add_extra_page('info.xhtml', info_html)
        except Exception:
            pass

        def next_image_no():
            with self.image_lock:
                n = self.image_no
                self.image_no += 1
                return n

        for c in selected:
            c.setdefault('is_notice', False)
        selected_total = (notice_items + selected) if notice_items else selected
        results = [None] * len(selected_total)

        self.progress_total = len(selected_total)
        self.progress_value = 0
        self.after(0, lambda: self.progress.configure(value=0))

        threads = self.var_threads.get()
        interval = self.var_interval.get()

        self.lbl_status.config(text="Downloading...")
        with ThreadPoolExecutor(max_workers=threads) as executor:
            for i in range(0, len(selected_total), threads):
                batch = range(i, min(i + threads, len(selected_total)))
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
                                self.var_image_format.get(), self.log_message, next_image_no
                            )
                            results[idx] = (chap['title'], hb, imgs, chap.get('is_notice', False))
                            self.log_message(f"Downloaded: {chap['title']}")
                    except Exception as e:
                        self.log_message(f"Error {chap.get('title','?')}: {e}")

                    self.progress_value += 1
                    try:
                        pct = int(self.progress_value / self.progress_total * 100)
                    except Exception:
                        pct = 0
                    self.after(0, lambda p=pct: self.progress.configure(value=p))

                if interval > 0:
                    time.sleep(interval)

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
        else:
            try:
                with open(self._output_path, 'w', encoding='utf-8') as f:
                    for res in results:
                        if res:
                            t, h, _, _ = res
                            if self.var_save_html.get():
                                f.write(f"<h2>{t}</h2>\n{h}\n\n")
                            else:
                                plain = re.sub(r"</?[^>]+>", "", h)
                                f.write(f"{t}\n\n{html.unescape(plain)}\n\n")
            except Exception as e:
                self.log_message(f"Save failed: {e}")

        self.log_message("Download Complete!")
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
                self.var_threads.set(cfg.get("thread_num", 4))
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

    def _on_close(self):
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
            
            # Range settings
            "from_enabled": self.var_from_enabled.get(),
            "to_enabled": self.var_to_enabled.get(),
            "from_num": self.var_from_num.get(),
            "to_num": self.var_to_num.get(),
            
            # Quick download options
            "quick_enable": self.var_quick_enable.get(),
            "quick_path": self.var_quick_path.get(),
            "naming_mode": self.var_naming_mode.get(),
            "append_range": self.var_append_range.get()
        }
        try:
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except: pass
        self.destroy()

if __name__ == "__main__":
    app = NovelpiaGUI()
    app.mainloop()