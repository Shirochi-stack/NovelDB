"""
external_scraper.py — Playwright-based scraper for non-Novelpia sites.

Uses Playwright (headless Chromium) to navigate to novel pages and inject the
novel-downloader's compiled JavaScript rules (rules-lib.js + bridge.js).
The JS rules run natively in the browser, supporting all 100+ sites without
manual porting.

Data flows:
  1. Playwright navigates to the book URL
  2. Injects rules-lib.js + bridge.js
  3. Calls __ND_parseBook() → gets JSON metadata + chapter list
  4. For each chapter, calls __ND_parseChapter(url) → gets JSON content + images
  5. Results fed into the existing EPUB/PDF/TXT pipeline
"""

import json
import html
import base64
import hashlib
import hmac
import os
import re
import shutil
import shlex
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
if sys.platform == "win32":
    import ctypes

# When running from a PyInstaller bundle, point Playwright to the
# bundled Chromium browser so users don't need to install anything.
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _bundled_browsers = os.path.join(_base, 'ms-playwright')
    if os.path.exists(_bundled_browsers):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _bundled_browsers

from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
)

APP_DATA_NAME = "NpiaDownloader"


def _get_base_dir():
    """Get the directory where the exe lives (for user-created files like browser_data)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_bundle_dir():
    """Get the directory where bundled data files are extracted (JS files etc)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_app_data_dir():
    """Return a stable per-user data dir that survives app folder changes."""
    override = os.environ.get("NPIA_BROWSER_DATA_DIR")
    if override:
        return override

    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, APP_DATA_NAME)
    if sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            APP_DATA_NAME,
        )
    return os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        APP_DATA_NAME,
    )


def _is_writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(probe)
        return True
    except Exception:
        return False


def _dir_has_entries(path):
    try:
        return os.path.isdir(path) and any(os.scandir(path))
    except Exception:
        return False


def _load_js_file(filename):
    """Load a JavaScript file from the bundle directory."""
    path = os.path.join(_get_bundle_dir(), filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


class ExternalScraper:
    """Playwright-based scraper using novel-downloader JS rules.

    Usage:
        scraper = ExternalScraper(logger=print)
        scraper.start()
        book = scraper.parse_book("https://ncode.syosetu.com/n1234ab/")
        chapters = scraper.parse_all_chapters(book['chapters'])
        scraper.stop()
    """

    def __init__(self, logger=None):
        self._raw_log = logger or (lambda msg: None)
        self._stop_requested = False

        # Load JS files once
        try:
            self._gm_stubs_js = _load_js_file('gm_stubs.js')
            self._rules_js = _load_js_file('rules-lib.js')
            self._bridge_js = _load_js_file('bridge.js')
        except FileNotFoundError as e:
            self._gm_stubs_js = None
            self._rules_js = None
            self._bridge_js = None

        self._playwright = None
        self._browser = None
        self._context = None      # BrowserContext (persistent)
        self._page = None
        self._chrome_process = None
        self._ntk_temp_chrome = False
        self._worker_pages = []   # Additional pages for parallel downloads
        self._book_data = None
        self._book_url = None     # Stored for initialising worker pages
        self._ntk_api_state = None
        self.ntk_curl_command = os.environ.get("NPIA_NTK_CURL", "")
        if not self.ntk_curl_command:
            try:
                curl_path = os.path.join(_get_base_dir(), "ntk_curl.txt")
                if os.path.exists(curl_path):
                    with open(curl_path, "r", encoding="utf-8") as f:
                        self.ntk_curl_command = f.read().strip()
            except Exception:
                self.ntk_curl_command = ""
        self.ntk_prefer_novelpia_cover = False
        self._kakao_css_cache = {}
        self.kakao_keep_filler = False
        self.kakao_skip_last_page = False

    @staticmethod
    def _get_user_data_dir():
        """Get persistent browser data directory for cookies/localStorage.

        Older builds kept browser_data next to the exe. That breaks when a
        user extracts a new release into a different folder or runs from a
        read-only install location, so new builds use a stable per-user app
        data path and migrate the old folder once when possible.
        """
        legacy_dir = os.path.join(_get_base_dir(), 'browser_data')
        stable_dir = os.path.join(_get_app_data_dir(), 'browser_data')

        if (os.path.abspath(legacy_dir) != os.path.abspath(stable_dir)
                and _dir_has_entries(legacy_dir)
                and not _dir_has_entries(stable_dir)):
            try:
                os.makedirs(os.path.dirname(stable_dir), exist_ok=True)
                shutil.copytree(legacy_dir, stable_dir, dirs_exist_ok=True)
            except Exception:
                pass

        if _is_writable_dir(stable_dir):
            return stable_dir
        if _is_writable_dir(legacy_dir):
            return legacy_dir

        fallback = os.path.join(
            os.path.expanduser("~"), f".{APP_DATA_NAME}", "browser_data"
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback

    @classmethod
    def _get_storage_state_path(cls):
        return os.path.join(cls._get_user_data_dir(), 'nd_storage_state.json')

    @classmethod
    def _get_ntk_user_data_dir(cls):
        """Dedicated Chrome profile for NewToki Cloudflare/CDP sessions."""
        path = os.path.join(_get_app_data_dir(), 'ntk_chrome_profile')
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _find_chrome_executable():
        """Find an installed Chrome/Edge executable for anti-bot login pages."""
        env_path = os.environ.get("NPIA_CHROME_PATH")
        candidates = [env_path] if env_path else []
        if sys.platform == "win32":
            program_files = [
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            ]
            for root in filter(None, program_files):
                candidates.extend([
                    os.path.join(root, "Google", "Chrome", "Application",
                                 "chrome.exe"),
                    os.path.join(root, "Microsoft", "Edge", "Application",
                                 "msedge.exe"),
                ])
        elif sys.platform == "darwin":
            candidates.extend([
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ])
        else:
            candidates.extend([
                shutil.which("google-chrome"),
                shutil.which("google-chrome-stable"),
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
                shutil.which("microsoft-edge"),
            ])

        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    @staticmethod
    def _get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _wait_for_cdp(port, timeout=15):
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{port}/json/version"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.25)
        return False

    def _close_ntk_profile_chrome(self, user_data_dir):
        """Close Chrome instances that own the dedicated NewToki profile."""
        if sys.platform != "win32":
            return 0
        profile = os.path.abspath(user_data_dir).replace("'", "''")
        script = (
            "$needle = '" + profile + "'.ToLowerInvariant();\n"
            "$profileName = 'ntk_chrome_profile';\n"
            "$matches = @(Get-CimInstance Win32_Process -Filter "
            "\"name = 'chrome.exe'\" | Where-Object { "
            "$_.CommandLine -and "
            "($_.CommandLine.ToLowerInvariant().Contains($needle) -or "
            "$_.CommandLine.ToLowerInvariant().Contains($profileName)) "
            "});\n"
            "$count = $matches.Count;\n"
            "foreach ($p in $matches) { "
            "try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } "
            "catch {} "
            "};\n"
            "$count\n"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=8,
                errors="ignore",
            ).strip()
            count = int(output.splitlines()[-1]) if output else 0
        except Exception:
            return 0
        if count:
            time.sleep(1.0)
        return count

    def _close_ntk_temp_chrome_now(self):
        """Force-close the temporary NTK Chrome without waiting on storage APIs."""
        try:
            if self._chrome_process and self._chrome_process.poll() is None:
                self._chrome_process.terminate()
                try:
                    self._chrome_process.wait(timeout=1.5)
                except Exception:
                    self._chrome_process.kill()
        except Exception:
            pass
        closed = 0
        try:
            closed = self._close_ntk_profile_chrome(
                self._get_ntk_user_data_dir()
            )
        except Exception:
            closed = 0
        self._page = None
        self._context = None
        self._browser = None
        self._chrome_process = None
        self._ntk_temp_chrome = False
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        return closed

    def _focus_system_chrome_window(self):
        """Best-effort: bring the visible Chrome window to the foreground."""
        if sys.platform != "win32" or not self._chrome_process:
            return False
        try:
            user32 = ctypes.windll.user32
            target_pid = int(self._chrome_process.pid)
            found = []

            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )

            def _title(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return ""
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                title = _title(hwnd)
                if pid.value == target_pid or (
                    "Chrome" in title and (
                        "ntk" in title.lower()
                        or "뉴토끼" in title
                        or "용사파티" in title
                    )
                ):
                    found.append(hwnd)
                    return False
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            if not found:
                return False
            hwnd = found[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def _hide_ntk_chrome_windows(self):
        """Best-effort: hide windows created by the temporary NTK Chrome."""
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            profile = os.path.normcase(self._get_ntk_user_data_dir())
            chrome_pids = set()

            try:
                script = (
                    "$needle = '" + profile.replace("'", "''") + "'.ToLowerInvariant();"
                    "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle) } | "
                    "Select-Object -ExpandProperty ProcessId"
                )
                output = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", script],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    errors="ignore",
                )
                for line in output.splitlines():
                    try:
                        chrome_pids.add(int(line.strip()))
                    except Exception:
                        pass
            except Exception:
                pass
            if self._chrome_process:
                chrome_pids.add(int(self._chrome_process.pid))
            if not chrome_pids:
                return False

            hidden = []
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )

            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if int(pid.value) in chrome_pids:
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    hidden.append(hwnd)
                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
            return bool(hidden)
        except Exception:
            return False

    @staticmethod
    def _windows_click_screen(x, y):
        """Perform a real OS mouse click at screen coordinates."""
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.08)
            mouseeventf_leftdown = 0x0002
            mouseeventf_leftup = 0x0004
            user32.mouse_event(mouseeventf_leftdown, 0, 0, 0, 0)
            time.sleep(0.06)
            user32.mouse_event(mouseeventf_leftup, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _windows_clipboard_get_text():
        if sys.platform != "win32":
            return None
        try:
            import tkinter as _tk
            root = _tk.Tk()
            root.withdraw()
            try:
                return root.clipboard_get()
            except Exception:
                return ''
            finally:
                root.destroy()
        except Exception:
            return None

    @staticmethod
    def _windows_clipboard_set_text(text):
        if sys.platform != "win32":
            return False
        try:
            import tkinter as _tk
            root = _tk.Tk()
            root.withdraw()
            try:
                root.clipboard_clear()
                if text:
                    root.clipboard_append(text)
                root.update()
                return True
            finally:
                root.destroy()
        except Exception:
            return False

    def _open_system_chrome(self, start_url, remote_debugging=False,
                            user_data_dir=None, hidden=False):
        """Open installed Chrome as a normal process using our profile."""
        chrome_path = self._find_chrome_executable()
        if not chrome_path:
            return None, None

        user_data_dir = user_data_dir or self._get_user_data_dir()
        os.makedirs(user_data_dir, exist_ok=True)
        args = [
            chrome_path,
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--disable-background-mode",
            "--disable-features=Translate",
            "--new-window",
            start_url or "about:blank",
        ]
        port = None
        if remote_debugging:
            port = self._get_free_port()
            args.insert(2, f"--remote-debugging-port={port}")
            args.insert(3, "--remote-allow-origins=*")
        if hidden:
            insert_at = 4 if remote_debugging else 1
            hidden_args = [
                "--start-minimized",
                "--window-position=-32000,-32000",
                "--window-size=1,1",
            ]
            for offset, arg in enumerate(hidden_args):
                args.insert(insert_at + offset, arg)

        popen_kwargs = {}
        if hidden:
            popen_kwargs.update({
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            })
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                popen_kwargs["startupinfo"] = startupinfo
                popen_kwargs["creationflags"] = getattr(
                    subprocess,
                    "CREATE_NO_WINDOW",
                    0,
                )
            else:
                popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(args, **popen_kwargs)
        return proc, port

    def _backup_storage_state(self):
        """Persist cookies/localStorage as an extra guard against profile loss."""
        if not self._context:
            return
        try:
            self._context.storage_state(path=self._get_storage_state_path())
        except Exception:
            pass

    def _restore_storage_state(self):
        """Restore the explicit storage backup into the current context."""
        if not self._context:
            return
        path = self._get_storage_state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            return

        cookies = state.get('cookies') or []
        if cookies:
            try:
                self._context.add_cookies(cookies)
            except Exception:
                pass

        origins = state.get('origins') or []
        if origins:
            script = """
(() => {
  const origins = __ORIGINS__;
  const current = origins.find((entry) => entry.origin === location.origin);
  if (!current || !Array.isArray(current.localStorage)) {
    return;
  }
  for (const item of current.localStorage) {
    try {
      localStorage.setItem(item.name, item.value);
    } catch (e) {}
  }
})();
""".replace("__ORIGINS__", json.dumps(origins))
            try:
                self._context.add_init_script(script=script)
            except Exception:
                pass

    def log(self, msg):
        """Safe logging that handles encoding issues on Windows consoles."""
        try:
            self._raw_log(msg)
        except UnicodeEncodeError:
            self._raw_log(msg.encode('ascii', 'replace').decode())

    def start(self):
        """Launch headless browser with persistent context."""
        if self._context:
            if self._page:
                try:
                    self._page.evaluate("1")
                    return
                except Exception:
                    self._page = None
            try:
                self._page = self._context.new_page()
                self._page.on("console", self._on_console)
                self.log("Browser ready.")
                return
            except Exception:
                self.cleanup()

        user_data_dir = self._get_user_data_dir()
        self.log("Launching headless browser...")
        self.log(f"Browser profile: {user_data_dir}")
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
            args=[
                '--disable-web-security',       # Allow cross-origin fetches
                '--disable-features=IsolateOrigins,site-per-process',
                '--allow-running-insecure-content',  # Allow HTTP images on HTTPS pages
                '--no-sandbox',
            ],
            ignore_https_errors=True,
        )
        self._restore_storage_state()
        self._page = self._context.new_page()
        # Suppress console noise but capture errors
        self._page.on("console", self._on_console)
        self.log("Browser ready.")

    def _start_ntk_browser(self, start_url):
        """Launch normal visible Chrome and attach over CDP for NewToki."""
        if self._context and self._page:
            try:
                self._page.evaluate("1")
                return True
            except Exception:
                self.cleanup()

        self.cleanup()
        self.log("Launching background Chrome for NewToki...")
        user_data_dir = self._get_ntk_user_data_dir()
        self.log(f"Browser profile: {user_data_dir}")
        closed = self._close_ntk_profile_chrome(user_data_dir)
        if closed:
            self.log(
                f"[NewToki] Closed {closed} stale Chrome process(es) using "
                "the dedicated ntk profile."
            )
        proc, port = self._open_system_chrome(
            start_url,
            remote_debugging=True,
            user_data_dir=user_data_dir,
            hidden=True,
        )
        if not proc or not port:
            self.log(
                "ERROR: Installed Chrome/Edge was not found. NewToki's "
                "Cloudflare challenge usually will not pass in bundled "
                "headless Chromium."
            )
            return False

        self._chrome_process = proc
        self._ntk_temp_chrome = True
        if not self._wait_for_cdp(port):
            exit_code = proc.poll()
            if exit_code is not None:
                self.log(
                    "ERROR: Chrome exited before remote debugging started "
                    f"(exit code {exit_code})."
                )
            else:
                self.log(
                    "ERROR: Chrome remote debugging endpoint did not start."
                )
            self.log(
                "Close any NewToki Chrome windows opened by the app and try "
                "again. The scraper now uses a dedicated ntk profile to avoid "
                "Chrome profile locking."
            )
            return False
        if self._ntk_temp_chrome:
            self._hide_ntk_chrome_windows()

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._page.on("console", self._on_console)
            if self._ntk_temp_chrome:
                self._hide_ntk_chrome_windows()
            self.log("Chrome session ready.")
            return True
        except Exception as e:
            self.log(f"ERROR: Could not attach to Chrome: {e}")
            return False

    def open_visible_browser(self, start_url="about:blank",
                             regular_browser=False):
        """Open a visible browser for manual login.

        Cookies and localStorage are saved to the persistent data dir.
        The browser blocks until the user closes it.
        """
        # Must close any existing context first (only one per data dir)
        self.cleanup()

        use_regular = regular_browser or self.is_ntk_novel(start_url)
        if use_regular and self.is_ntk_novel(start_url):
            user_data_dir = self._get_ntk_user_data_dir()
        else:
            user_data_dir = self._get_user_data_dir()
        self.log("Opening browser for login...")
        self.log(f"Browser profile: {user_data_dir}")
        if use_regular:
            proc, _ = self._open_system_chrome(
                start_url,
                remote_debugging=False,
                user_data_dir=user_data_dir,
            )
            if proc:
                self.log(
                    "Using normal installed Chrome for browser session."
                )
                self.log(
                    "Browser opened. Complete any login or verification, "
                    "then close this Chrome window."
                )
                try:
                    proc.wait()
                except Exception:
                    pass
                self.log("Browser closed. Session data saved.")
                return
            self.log(
                "Normal Chrome was not found; falling back to Playwright "
                "browser."
            )

        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir,
                channel="chrome",
                headless=False,
                ignore_https_errors=True,
            )
            self.log("Using installed Google Chrome for login.")
        except Exception as chrome_error:
            self.log(
                "Installed Chrome unavailable for login; "
                "falling back to bundled Chromium."
            )
            self.log(f"Chrome launch warning: {chrome_error}")
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                args=[
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--allow-running-insecure-content',
                    '--no-sandbox',
                ],
                ignore_https_errors=True,
            )
        self._restore_storage_state()
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page = page
        try:
            page.goto(start_url)
        except Exception as e:
            msg = str(e)
            benign = (
                'ERR_ABORTED' in msg
                or 'frame was detached' in msg
                or 'Target page, context or browser has been closed' in msg
                or 'Browser has been closed' in msg
            )
            if not benign:
                self.log(f"Browser navigation warning: {e}")
        self.log("Browser opened. Login to sites as needed, then close the browser.")

        # Wait for the browser to fully close.  We listen on the *context*
        # "close" event rather than the page – this fires only after
        # Chromium has completely exited and flushed all session data
        # (cookies, localStorage, IndexedDB) to the persistent profile
        # directory.  Using page.wait_for_event("close") previously caused
        # a race: cleanup() would call context.close() while Chromium was
        # still mid-shutdown, interrupting the disk flush and losing the
        # login session.
        try:
            self._context.wait_for_event("close", timeout=0)
        except Exception:
            pass

        # Chromium's persistent profile should already be flushed at this
        # point. Take one explicit storage snapshot after close/disconnect as
        # a best-effort fallback, but do not poll storage_state() while the
        # visible browser is open because it can disturb live tabs.
        self._backup_storage_state()

        self.log("Browser closed. Session data saved.")
        # The context already disconnected, so just reset Python refs
        # without calling context.close() again (which would error or
        # race with the flush).
        self._page = None
        self._context = None
        try:
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
        except Exception:
            self._playwright = None

    def _on_console(self, msg):
        """Forward JS console messages to Python logger."""
        text = msg.text
        if 'whoas.xyz/collect' in text:
            return
        # Show bridge messages, fetch retries, init info, and actual errors
        if "[ND-Bridge]" in text or "[ND-Fetch]" in text or "[Init]" in text:
            self.log(f"[JS] {text}")
        elif msg.type == "error" and "Failed to load resource" not in text:
            self.log(f"[JS] {text}")

    def stop(self):
        """Request cancellation and close browser."""
        self._stop_requested = True
        self.cleanup()

    def cleanup(self):
        """Release browser resources."""
        self._backup_storage_state()
        for wp in self._worker_pages:
            try:
                wp.close()
            except Exception:
                pass
        self._worker_pages = []
        try:
            if self._page:
                self._page.close()
                self._page = None
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
                self._context = None
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
                self._browser = None
        except Exception:
            self._browser = None
        try:
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
        except Exception:
            pass
        try:
            if self._chrome_process and self._chrome_process.poll() is None:
                self._chrome_process.terminate()
                try:
                    self._chrome_process.wait(timeout=5)
                except Exception:
                    self._chrome_process.kill()
            self._chrome_process = None
        except Exception:
            self._chrome_process = None
        if self._ntk_temp_chrome:
            try:
                closed = self._close_ntk_profile_chrome(
                    self._get_ntk_user_data_dir()
                )
                if closed:
                    self.log(
                        f"[NewToki] Closed {closed} temporary Chrome "
                        "process(es)."
                    )
            except Exception:
                pass
            self._ntk_temp_chrome = False

    # ------------------------------------------------------------------
    # Multi-page support for parallel chapter downloads
    # ------------------------------------------------------------------
    def create_worker_pages(self, count):
        """Create additional browser pages for parallel chapter downloads.

        Each page navigates to the book URL, gets stubs/rules/bridge
        injected, and has its own rule instance ready for chapterParse.

        Must be called AFTER parse_book() succeeds.
        """
        if count <= 0 or not self._book_url:
            return
        if self._book_data and self._book_data.get('_ntk_novel'):
            return

        # Close any existing worker pages
        for wp in self._worker_pages:
            try:
                wp.close()
            except Exception:
                pass
        self._worker_pages = []

        self.log(f"Creating {count} worker pages...")
        for i in range(count):
            try:
                page = self._context.new_page()
                page.on("console", self._on_console)
                page.goto(self._book_url, wait_until="domcontentloaded",
                          timeout=30000)
                page.evaluate(self._gm_stubs_js)
                page.evaluate(self._rules_js)
                page.evaluate(self._bridge_js)
                # Initialise the rule instance on this page
                page.evaluate("window.__ND_parseBook()")
                self._worker_pages.append(page)
            except Exception as e:
                self.log(f"  Worker page {i} failed: {e}")
        self.log(f"{len(self._worker_pages)} worker pages ready.")

    def get_page(self, index):
        """Get a page by index. 0 = primary, 1..N = worker pages."""
        if index == 0:
            return self._page
        wi = index - 1
        if wi < len(self._worker_pages):
            return self._worker_pages[wi]
        return self._page  # fallback to primary

    def _ensure_page(self):
        """Verify the primary page is alive; restart if needed.

        Returns True if the page is usable, False if recovery failed.
        Called before any page.evaluate() to handle browser crashes
        or disconnected pages mid-download.
        """
        if self._page is not None:
            # Quick liveness check
            try:
                self._page.evaluate("1")
                return True
            except Exception:
                self.log("Page disconnected, attempting recovery...")
                self._page = None

        # Page is None — try to recover
        if self._context is None:
            try:
                self.start()
            except Exception as e:
                self.log(f"ERROR: Could not restart browser: {e}")
                return False

        # Create a new page in the existing context
        try:
            self._page = self._context.new_page()
            self._page.on("console", self._on_console)
        except Exception as e:
            self.log(f"ERROR: Could not create new page: {e}")
            return False

        # Re-navigate and re-inject JS if we have a book URL
        if self._book_url:
            try:
                self._page.goto(self._book_url,
                                wait_until="domcontentloaded", timeout=30000)
                if self._book_data and self._book_data.get('_ntk_novel'):
                    self.log("Page recovered for NewToki scraper.")
                    return True
                self._page.evaluate(self._gm_stubs_js)
                self._page.evaluate(self._rules_js)
                self._page.evaluate(self._bridge_js)
                # Re-initialise the rule instance
                self._page.evaluate("window.__ND_parseBook()")
                self.log("Page recovered and bridge re-injected.")
                return True
            except Exception as e:
                self.log(f"ERROR: Page recovery failed: {e}")
                return False

        self.log("Page recovered (no book URL to re-inject).")
        return True

    # ------------------------------------------------------------------
    # KakaoPage native scraper (fallback for unsupported JS rules)
    # ------------------------------------------------------------------
    @staticmethod
    def is_kakaopage(url):
        """Check if the URL is a KakaoPage content URL."""
        return bool(url and re.match(
            r'https?://page\.kakao\.com/content/\d+', url
        ))

    @staticmethod
    def is_ntk_novel(url):
        """Check if the URL is a NewToki/ntk novel or webtoon page."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.netloc or '').lower()
        return bool(
            re.fullmatch(r'(?:www\.)?ntk\d+\.com', host)
            and re.match(r'^/(?:novel|webtoon)/\d+(?:/\d+)?/?$', parsed.path or '')
        )

    @staticmethod
    def is_yeduji(url):
        """Check if the URL is a Yeduji (夜读集) novel page."""
        try:
            parsed = urllib.parse.urlparse(url or '')
        except Exception:
            return False
        host = (parsed.netloc or '').lower()
        return bool(
            re.fullmatch(r'(?:www\.)?yeduji\.com', host)
            and re.match(r'^/book/\d+', parsed.path or '')
        )

    @staticmethod
    def _ntk_content_kind_from_url(url):
        try:
            match = re.search(r'^/(novel|webtoon)/', urllib.parse.urlparse(url or '').path)
            return match.group(1) if match else 'novel'
        except Exception:
            return 'novel'

    def _ntk_is_challenge_page(self, page):
        """Detect Cloudflare's interstitial so users get a useful message."""
        try:
            title = (page.title() or '').strip().lower()
            if 'just a moment' in title:
                return True
        except Exception:
            pass
        try:
            text = page.locator('body').inner_text(timeout=3000).lower()
            return (
                'enable javascript and cookies to continue' in text
                or 'checking your browser' in text
                or 'cf-challenge' in text
            )
        except Exception:
            return False

    def _ntk_wait_for_access(self, page, timeout=180):
        """Wait for the visible Chrome window to clear Cloudflare."""
        if not self._ntk_is_challenge_page(page):
            return True
        self.log(
            "[NewToki] Waiting for Cloudflare verification in the visible "
            "Chrome window..."
        )
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop_requested:
            if not self._ntk_is_challenge_page(page):
                self.log("[NewToki] Verification cleared.")
                return True
            try:
                page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1)
        return not self._ntk_is_challenge_page(page)

    @staticmethod
    def _ntk_b64url_encode(data):
        return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

    @staticmethod
    def _ntk_b64url_decode(data):
        padding = '=' * ((4 - len(data) % 4) % 4)
        return base64.urlsafe_b64decode(data + padding)

    @staticmethod
    def _ntk_novel_id_from_url(url):
        try:
            match = re.search(r'/(?:novel|webtoon)/(\d+)', urllib.parse.urlparse(url).path)
            return match.group(1) if match else ''
        except Exception:
            return ''

    @staticmethod
    def _ntk_episode_id_from_url(url):
        try:
            path = urllib.parse.urlparse(url or '').path.rstrip('/')
            return path.rsplit('/', 1)[-1]
        except Exception:
            return ''

    @staticmethod
    def _ntk_requests_module():
        try:
            from curl_cffi import requests as curl_requests
            return curl_requests, True
        except Exception:
            import requests as std_requests
            return std_requests, False

    @staticmethod
    def _ntk_parse_curl_command(curl_command):
        headers = {}
        cookies = {}
        if not curl_command:
            return headers, cookies
        try:
            args = shlex.split(curl_command)
        except ValueError:
            return headers, cookies
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ('-H', '--header') and i + 1 < len(args):
                header = args[i + 1]
                if ':' in header:
                    key, value = header.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    if key.lower() != 'accept-encoding':
                        headers[key] = value
                i += 2
                continue
            if arg in ('-b', '--cookie') and i + 1 < len(args):
                for item in args[i + 1].split(';'):
                    if '=' in item:
                        key, value = item.strip().split('=', 1)
                        cookies[key] = value
                i += 2
                continue
            i += 1

        cookie_key = next(
            (key for key in headers if key.lower() == 'cookie'),
            None,
        )
        if cookie_key:
            cookie_value = headers.pop(cookie_key)
            for item in cookie_value.split(';'):
                if '=' in item:
                    key, value = item.strip().split('=', 1)
                    cookies[key] = value
        return headers, cookies

    @staticmethod
    def _ntk_windows_dpapi_unprotect(data):
        if sys.platform != "win32" or not data:
            return None
        try:
            class DATA_BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", ctypes.c_uint),
                    ("pbData", ctypes.POINTER(ctypes.c_char)),
                ]

            in_buffer = ctypes.create_string_buffer(data, len(data))
            in_blob = DATA_BLOB(
                len(data),
                ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)),
            )
            out_blob = DATA_BLOB()
            ok = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(in_blob),
                None,
                None,
                None,
                None,
                0,
                ctypes.byref(out_blob),
            )
            if not ok:
                return None
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        except Exception:
            return None

    def _ntk_chrome_master_key(self, profile_dir):
        local_state = os.path.join(profile_dir, "Local State")
        try:
            with open(local_state, "r", encoding="utf-8") as f:
                data = json.load(f)
            encrypted_key = data.get("os_crypt", {}).get("encrypted_key", "")
            raw = base64.b64decode(encrypted_key)
            if raw.startswith(b"DPAPI"):
                raw = raw[5:]
            return self._ntk_windows_dpapi_unprotect(raw)
        except Exception:
            return None

    def _ntk_decrypt_chrome_cookie(self, host_key, encrypted_value, key):
        if not encrypted_value:
            return ""
        try:
            if encrypted_value.startswith((b"v10", b"v11")) and key:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                nonce = encrypted_value[3:15]
                ciphertext = encrypted_value[15:]
                plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
                host_hash = hashlib.sha256((host_key or "").encode()).digest()
                if plaintext.startswith(host_hash):
                    plaintext = plaintext[32:]
                return plaintext.decode("utf-8", "ignore")
            if sys.platform == "win32":
                plaintext = self._ntk_windows_dpapi_unprotect(encrypted_value)
                return plaintext.decode("utf-8", "ignore") if plaintext else ""
        except Exception:
            return ""
        return ""

    def _ntk_load_profile_cookies(self, url):
        """Read Cloudflare/NewToki cookies from the dedicated Chrome profile."""
        profile_dir = self._get_ntk_user_data_dir()
        cookies_db = os.path.join(profile_dir, "Default", "Network", "Cookies")
        if not os.path.exists(cookies_db):
            return {}
        key = self._ntk_chrome_master_key(profile_dir)
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lstrip(".")
        if not host:
            return {}
        now_chrome = int((time.time() + 11644473600) * 1000000)
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="ntk_cookies_", suffix=".db")
            os.close(fd)
            shutil.copy2(cookies_db, tmp_path)
            conn = sqlite3.connect(tmp_path)
            try:
                rows = conn.execute(
                    """
                    SELECT host_key, name, value, encrypted_value, expires_utc
                    FROM cookies
                    WHERE host_key = ? OR host_key = ? OR host_key LIKE ?
                    """,
                    (host, "." + host, "%." + host),
                ).fetchall()
            finally:
                conn.close()
            out = {}
            for host_key, name, value, encrypted_value, expires_utc in rows:
                if expires_utc and expires_utc < now_chrome:
                    continue
                cookie_value = value or self._ntk_decrypt_chrome_cookie(
                    host_key,
                    encrypted_value,
                    key,
                )
                if name and cookie_value:
                    out[name] = cookie_value
            return out
        except Exception as e:
            # Chrome locks its Cookies DB while the visible clearance window is
            # open. That is expected during the automatic fallback; live browser
            # cookies are collected separately.
            if getattr(e, "winerror", None) != 32:
                self.log(f"[NewToki] Chrome profile cookie import failed: {e}")
            return {}
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _ntk_default_headers_and_cookies(self, url):
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
        accept_language = 'en-US,en;q=0.9'
        sec_ch_ua = '"Chromium";v="120", "Google Chrome";v="120", "Not?A_Brand";v="99"'
        sec_ch_mobile = '?0'
        sec_ch_platform = '"Windows"'
        curl_headers, curl_cookies = self._ntk_parse_curl_command(
            self.ntk_curl_command
        )
        profile_cookies = self._ntk_load_profile_cookies(url)
        merged_cookies = dict(profile_cookies)
        merged_cookies.update(curl_cookies)

        def header(name, fallback):
            for key, value in curl_headers.items():
                if key.lower() == name.lower():
                    return value
            return fallback

        user_agent = header('user-agent', user_agent)
        accept_language = header('accept-language', accept_language)
        sec_ch_ua = header('sec-ch-ua', sec_ch_ua)
        sec_ch_mobile = header('sec-ch-ua-mobile', sec_ch_mobile)
        sec_ch_platform = header('sec-ch-ua-platform', sec_ch_platform)

        doc_headers = {
            'User-Agent': user_agent,
            'Accept': header(
                'accept',
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8',
            ),
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'upgrade-insecure-requests': '1',
        }
        api_headers = {
            'User-Agent': user_agent,
            'Accept': '*/*',
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-novel-client': 'shadow-v2',
            'Content-Type': 'application/json',
        }
        return {
            'origin': origin,
            'host': parsed.netloc,
            'cookies': merged_cookies,
            'user_agent': user_agent,
            'doc_headers': {k: v for k, v in doc_headers.items() if v},
            'api_headers': {k: v for k, v in api_headers.items() if v},
            'from_curl': bool(curl_headers or curl_cookies),
            'from_profile_cookies': bool(profile_cookies),
        }

    def _ntk_browser_headers_and_cookies(self, url):
        """Collect the verified visible Chrome session for API requests."""
        if not self._page or not self._context:
            return None
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        try:
            cookies = {
                c.get('name'): c.get('value')
                for c in self._context.cookies(origin)
                if c.get('name') and c.get('value') is not None
            }
        except Exception:
            cookies = {}
        try:
            ua_info = self._page.evaluate(r"""
() => {
  const uaData = navigator.userAgentData || null;
  const brands = uaData && Array.isArray(uaData.brands)
    ? uaData.brands.map((b) => `"${b.brand}";v="${b.version}"`).join(', ')
    : '';
  return {
    userAgent: navigator.userAgent || '',
    language: navigator.language || 'en-US,en;q=0.9',
    brands,
    mobile: uaData ? (uaData.mobile ? '?1' : '?0') : '?0',
    platform: uaData && uaData.platform ? `"${uaData.platform}"` : '"Windows"'
  };
}
            """) or {}
        except Exception:
            ua_info = {}
        user_agent = ua_info.get('userAgent') or (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        accept_language = ua_info.get('language') or 'en-US,en;q=0.9'
        sec_ch_ua = ua_info.get('brands') or '"Chromium";v="120", "Google Chrome";v="120"'
        sec_ch_mobile = ua_info.get('mobile') or '?0'
        sec_ch_platform = ua_info.get('platform') or '"Windows"'

        doc_headers = {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'upgrade-insecure-requests': '1',
        }
        api_headers = {
            'User-Agent': user_agent,
            'Accept': '*/*',
            'Accept-Language': accept_language,
            'sec-ch-ua': sec_ch_ua,
            'sec-ch-ua-mobile': sec_ch_mobile,
            'sec-ch-ua-platform': sec_ch_platform,
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-novel-client': 'shadow-v2',
            'Content-Type': 'application/json',
        }
        return {
            'origin': origin,
            'host': parsed.netloc,
            'cookies': cookies,
            'user_agent': user_agent,
            'doc_headers': {k: v for k, v in doc_headers.items() if v},
            'api_headers': {k: v for k, v in api_headers.items() if v},
        }

    def _ntk_create_api_session(self, state):
        req, is_curl = self._ntk_requests_module()
        try:
            session = req.Session(impersonate='chrome120') if is_curl else req.Session()
        except TypeError:
            session = req.Session()
        host = state.get('host') or 'ntk01.com'
        for name, value in (state.get('cookies') or {}).items():
            if name and name.lower() != 'nv':
                try:
                    session.cookies.set(name, value, domain=host)
                except Exception:
                    session.cookies.set(name, value)
        return session

    def _ntk_issue_nv(self, state, referer):
        session = state.get('session')
        if not session:
            return False
        headers = dict(state['api_headers'])
        headers['Referer'] = referer or state.get('index_url') or state['origin']
        try:
            session.post(
                state['origin'] + '/api/nv-issue',
                headers=headers,
                timeout=15,
            )
            return bool(session.cookies.get('nv'))
        except Exception as e:
            self.log(f"  [NewToki] nv issue failed: {e}")
            return False

    def _ntk_prepare_api_state(self, index_url, novel_id=None):
        state = None
        if self._page and self._context and not self.ntk_curl_command:
            state = self._ntk_browser_headers_and_cookies(index_url)
            if state:
                state['from_live_browser'] = True
        if not state:
            state = self._ntk_default_headers_and_cookies(index_url)
        state['novel_id'] = novel_id or self._ntk_novel_id_from_url(index_url)
        state['index_url'] = index_url
        if state.get('from_live_browser') and self._ntk_temp_chrome:
            self.log("[NewToki] Captured live Chrome session cookies; closing Chrome.")
            closed = self._close_ntk_temp_chrome_now()
            self.log(
                f"[NewToki] Closed {closed} temporary Chrome process(es)."
            )
        state['session'] = self._ntk_create_api_session(state)
        self._ntk_issue_nv(state, index_url)
        if state.get('from_profile_cookies'):
            names = ', '.join(sorted((state.get('cookies') or {}).keys()))
            self.log(f"[NewToki] Imported Chrome profile cookies: {names}")
        self._ntk_api_state = state
        return state

    def _ntk_clone_api_state(self):
        """Create an independent API session for a worker thread."""
        base = self._ntk_api_state
        if not base:
            return None
        state = {
            key: value
            for key, value in base.items()
            if key != 'session'
        }
        state['session'] = self._ntk_create_api_session(state)
        self._ntk_issue_nv(state, state.get('index_url') or state['origin'])
        return state

    def _ntk_request_get(self, session, url, headers):
        return session.get(url, headers=headers, timeout=30)

    def _ntk_request_post_json(self, session, url, payload, headers):
        return session.post(url, json=payload, headers=headers, timeout=30)

    def _ntk_clean_plaintext(self, text):
        text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
        text = text.replace('\u200b', '').replace('\ufeff', '')
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.split('\n')]
        chunks = []
        blank = False
        for line in lines:
            if not line:
                if chunks and not blank:
                    chunks.append('')
                blank = True
                continue
            chunks.append(line)
            blank = False
        return '\n'.join(chunks).strip()

    def _ntk_build_text_chapter(self, title, plaintext, selector):
        text = self._ntk_clean_plaintext(plaintext)
        if not text:
            return None
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        if not paragraphs:
            paragraphs = [line.strip() for line in text.split('\n') if line.strip()]
        content_html = '\n'.join(
            f'<p>{html.escape(p).replace(chr(10), "<br/>")}</p>'
            for p in paragraphs
        )
        return {
            'chapterName': title or 'Chapter',
            'sourceChapterName': title or 'Chapter',
            'contentText': text,
            'contentHtml': content_html,
            '_debugSelector': selector,
        }

    def _ntk_html_attrs(self, tag):
        attrs = {}
        for name, _quote, value in re.findall(
            r'([:\w-]+)\s*=\s*(["\'])(.*?)\2',
            tag or '',
            re.S,
        ):
            attrs[name.lower()] = html.unescape(value).strip()
        return attrs

    def _ntk_meta_contents(self, html_text, names):
        wanted = {name.lower() for name in names}
        values = []
        for tag in re.findall(r'<meta\b[^>]*>', html_text or '', re.I | re.S):
            attrs = self._ntk_html_attrs(tag)
            keys = {
                (attrs.get('name') or '').lower(),
                (attrs.get('property') or '').lower(),
                (attrs.get('itemprop') or '').lower(),
            }
            content = attrs.get('content') or ''
            if content and wanted.intersection(keys):
                values.append(content.strip())
        return values

    def _ntk_meta_content(self, html_text, names):
        values = self._ntk_meta_contents(html_text, names)
        return values[0] if values else ''

    def _ntk_plain_fragment(self, value):
        value = re.sub(r'<[^>]+>', ' ', value or '')
        return html.unescape(re.sub(r'\s+', ' ', value)).strip()

    def _ntk_abs_url(self, base_url, value):
        value = html.unescape((value or '').strip())
        value = value.replace('\\/', '/').replace('\\\\/', '/')
        value = value.strip(' \t\r\n"\'\\')
        if not value or value.startswith(('data:', 'javascript:')):
            return ''
        if ',' in value and re.search(r'\s+\d+[wx](?:,|$)', value):
            value = value.split(',', 1)[0].strip()
        if ' ' in value and re.search(r'\s+\d+[wx]$', value):
            value = value.split()[0].strip()
        return urllib.parse.urljoin(base_url, value)

    def _ntk_is_site_image(self, url):
        url = (url or '').lower()
        return any(
            token in url
            for token in (
                'og-default',
                'favicon',
                'apple-touch-icon',
                'newtoki-logo',
                '/logo',
                'logo.',
            )
        )

    def _ntk_extract_cover_url(self, html_text, index_url):
        cover = self._ntk_meta_content(
            html_text,
            ('og:image', 'twitter:image', 'twitter:image:src', 'image'),
        )
        if cover:
            cover = self._ntk_abs_url(index_url, cover)
            if cover and not self._ntk_is_site_image(cover):
                return cover

        for match in re.finditer(
            r'https?:\\?/\\?/[^"\'\s<>]+(?:novel|webtoon)_thumb[^"\'\s<>]+',
            html_text or '',
            re.I,
        ):
            cover = self._ntk_abs_url(index_url, match.group(0))
            if cover and not self._ntk_is_site_image(cover):
                return cover

        for match in re.finditer(
            r'"(?:image|thumbnail|cover)"\s*:\s*"([^"]+)"',
            html_text or '',
            re.I,
        ):
            cover = self._ntk_abs_url(index_url, match.group(1).replace('\\/', '/'))
            if cover:
                return cover

        candidates = []
        first_content_image = ''
        for tag in re.findall(r'<img\b[^>]*>', html_text or '', re.I | re.S):
            attrs = self._ntk_html_attrs(tag)
            src = (
                attrs.get('data-src')
                or attrs.get('data-original')
                or attrs.get('data-lazy-src')
                or attrs.get('srcset')
                or attrs.get('src')
                or ''
            )
            src = self._ntk_abs_url(index_url, src)
            if not src:
                continue
            if self._ntk_is_site_image(src):
                continue
            src_lower = src.lower()
            haystack = ' '.join(
                str(attrs.get(k) or '')
                for k in ('class', 'id', 'alt', 'title', 'src', 'data-src')
            ).lower()
            if any(word in haystack for word in ('logo', 'icon', 'avatar', 'profile')):
                continue
            if any(word in src_lower for word in ('logo', 'icon', 'avatar', 'profile', 'favicon', 'emoji')):
                continue
            if not first_content_image:
                first_content_image = src
            score = 0
            if any(word in haystack for word in ('cover', 'poster', 'thumbnail', 'thumb', 'book', 'novel')):
                score += 4
            if any(word in src_lower for word in ('cover', 'poster', 'thumbnail', 'thumb', 'book', 'novel', 'upload', '/data/', '/file/')):
                score += 2
            candidates.append((score, src))
        candidates.sort(reverse=True)
        if candidates and candidates[0][0] > 0:
            return candidates[0][1]
        return first_content_image

    def _ntk_novelpia_id_from_cover_url(self, cover_url):
        match = re.search(r'/novel_thumb/(\d+)', cover_url or '', re.I)
        return match.group(1) if match else ''

    def _ntk_fetch_novelpia_cover_url(self, novelpia_id):
        if not novelpia_id:
            return ''
        url = f'https://novelpia.com/novel/{novelpia_id}'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://novelpia.com/',
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                text = response.read().decode('utf-8', 'ignore')
        except Exception as e:
            self.log(
                f"[NewToki] Novelpia cover lookup failed for "
                f"{novelpia_id}: {e}"
            )
            return ''
        patterns = (
            r'"(//images\.novelpia\.com/imagebox/original/[^"]+)"',
            r'"(//images\.novelpia\.com/imagebox/cover/[^"]+)"',
            r'(https?:)?(//images\.novelpia\.com/imagebox/original/[^\s"\'<>]+)',
            r'(https?:)?(//images\.novelpia\.com/imagebox/cover/[^\s"\'<>]+)',
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if not match:
                continue
            if len(match.groups()) >= 2:
                value = (match.group(1) or 'https:') + match.group(2)
            else:
                value = 'https:' + match.group(1)
            return html.unescape(value).replace('\\/', '/')
        return ''

    def _ntk_prefer_novelpia_cover_url(self, cover_url):
        if not self.ntk_prefer_novelpia_cover:
            return cover_url
        novelpia_id = self._ntk_novelpia_id_from_cover_url(cover_url)
        if not novelpia_id:
            return cover_url
        novelpia_cover = self._ntk_fetch_novelpia_cover_url(novelpia_id)
        if novelpia_cover:
            self.log(
                f"[NewToki] Using Novelpia cover for mapped ID "
                f"{novelpia_id}: {novelpia_cover}"
            )
            return novelpia_cover
        self.log(
            f"[NewToki] Novelpia cover unavailable for mapped ID "
            f"{novelpia_id}; using NewToki cover."
        )
        return cover_url

    def _ntk_clean_tag(self, value):
        value = self._ntk_plain_fragment(value).strip(' #,./\\|[](){}')
        value = value.replace('\\n', ' ').replace('\\r', ' ')
        value = re.sub(r'\s+', ' ', value).strip()
        if not value or len(value) > 40:
            return ''
        if re.fullmatch(r'[\d\s.,:;+\-]+', value):
            return ''
        if re.search(r'https?://|ntk\d+\.com|newtoki', value, re.I):
            return ''
        return value

    def _ntk_extract_tags(self, html_text):
        tags = []

        def add(value):
            value = self._ntk_clean_tag(value)
            if value and value not in tags:
                tags.append(value)

        for meta_value in self._ntk_meta_contents(
            html_text,
            ('keywords', 'news_keywords', 'article:tag', 'book:tag'),
        ):
            for part in re.split(r'[,#|/]+', meta_value):
                add(part)

        for match in re.finditer(
            r'<a\b[^>]*href=["\'][^"\']*(?:tag|genre|keyword)[^"\']*["\'][^>]*>(.*?)</a>',
            html_text or '',
            re.I | re.S,
        ):
            add(match.group(1))

        for match in re.finditer(
            r'(?:href|to)=\\?["\'][^"\']*(?:/novel\?g=|genre|tag|keyword)[^"\']*\\?["\'][^{}]{0,300}?children\\?["\']?\s*:\s*(?:\[\\?["\']#\\?["\']\s*,\s*)?\\?["\']([^"\'\\]+)',
            html_text or '',
            re.I | re.S,
        ):
            add(match.group(1))

        for match in re.finditer(
            r'hero-v2-tag[^{}]{0,500}?children\\?["\']?\s*:\s*(?:\[\\?["\']#\\?["\']\s*,\s*)?\\?["\']([^"\'\\]+)',
            html_text or '',
            re.I | re.S,
        ):
            add(match.group(1))

        page_text = self._ntk_plain_fragment(html_text)
        for match in re.finditer(r'(?:^|\s)#([^\s#]{1,40})', page_text):
            add(match.group(1))
        return tags[:20]

    def _ntk_extract_author(self, html_text):
        author = self._ntk_meta_content(
            html_text,
            ('author', 'article:author', 'book:author'),
        )
        if author:
            return self._ntk_clean_tag(author)
        page_text = self._ntk_plain_fragment(html_text)
        for pattern in (
            r'(?:작가|저자)\s*[:：]\s*([^\n\r|·ㆍ]{1,60})',
            r'\bby\s+([^\n\r|·ㆍ]{1,60})',
        ):
            match = re.search(pattern, page_text, re.I)
            if match:
                author = self._ntk_clean_tag(match.group(1))
                if author:
                    return author
        return ''

    def _ntk_merge_book_metadata(self, primary, fallback):
        if not primary or not fallback:
            return primary
        for key in ('author', 'coverUrl', 'introduction', 'introductionHTML'):
            if not primary.get(key) and fallback.get(key):
                primary[key] = fallback[key]
        if not primary.get('tags') and fallback.get('tags'):
            primary['tags'] = fallback['tags']
        return primary

    def _ntk_parse_index_html(self, html_text, index_url):
        novel_id = self._ntk_novel_id_from_url(index_url)
        raw_title = self._ntk_meta_content(
            html_text,
            ('og:title', 'twitter:title', 'title'),
        )
        if not raw_title:
            title_match = re.search(r'<title>([^<]+)</title>', html_text, re.I)
            raw_title = html.unescape(title_match.group(1)).strip() if title_match else f'Novel_{novel_id}'
        raw_title = re.split(r'\s+[-|]\s+', raw_title)[0].strip()

        intro = self._ntk_meta_content(
            html_text,
            ('description', 'og:description', 'twitter:description'),
        )
        cover = self._ntk_extract_cover_url(html_text, index_url)
        tags = self._ntk_extract_tags(html_text)
        author = self._ntk_extract_author(html_text)

        ep_blocks = re.findall(
            r'<li\s+data-ep=["\'](\d+)["\'][^>]*>.*?href=["\']([^"\']+)["\'].*?<span\s+class=["\']ne-title["\']>(.*?)</span>',
            html_text,
            re.I | re.S,
        )
        if not ep_blocks:
            ep_blocks = re.findall(
                r'data-ep\\?["\']?\s*[:=]\s*(\d+).*?href\\?["\']?\s*[:=]\s*\\?["\']([^"\']+).*?className\\?["\']?\s*:\s*\\?["\'][^"\']*(?:ne-title|ep-title)[^"\']*.*?children\\?["\']?\s*:\s*\\?["\']([^"\\]+)',
                html_text,
                re.I | re.S,
            )
        kind = self._ntk_content_kind_from_url(index_url)
        if not ep_blocks and kind == 'webtoon':
            base_id = self._ntk_novel_id_from_url(index_url)
            webtoon_links = []
            for match in re.finditer(
                rf'((?:https?:\\?/\\?/[^"\'\s<>]+)?/webtoon/{re.escape(base_id)}/(\d+))',
                html_text or '',
                re.I,
            ):
                href = self._ntk_abs_url(index_url, match.group(1))
                ep_id = match.group(2)
                if href and ep_id:
                    webtoon_links.append((ep_id, href))
            seen_links = set()
            for idx, (ep_id, href) in enumerate(webtoon_links, 1):
                if href in seen_links:
                    continue
                seen_links.add(href)
                ep_blocks.append((str(idx), href, f'{idx}\ud654'))
        chapters = []
        seen = set()
        for ep_num, href, ep_title in ep_blocks:
            url = urllib.parse.urljoin(index_url, html.unescape(href))
            ep_id = self._ntk_episode_id_from_url(url)
            if not ep_id or ep_id in seen:
                continue
            seen.add(ep_id)
            title = re.sub(r'<[^>]+>', '', ep_title)
            title = html.unescape(re.sub(r'\s+', ' ', title)).strip()
            try:
                number = int(ep_num)
            except Exception:
                number = len(chapters) + 1
            name = title or f'{number}\ud654'
            chapters.append({
                'url': url,
                'name': name,
                'fullName': name,
                'number': number,
                'episodeId': ep_id,
                'kind': kind,
                'isVIP': False,
                'isPaid': False,
            })
        chapters.sort(key=lambda ch: (ch.get('number') or 10**12, ch.get('episodeId') or ''))
        return {
            'bookUrl': index_url,
            'bookname': raw_title,
            'author': author,
            'coverUrl': cover,
            'introduction': intro,
            'introductionHTML': f'<p>{html.escape(intro)}</p>' if intro else '',
            'language': 'ko',
            'tags': tags,
            'chapterCount': len(chapters),
            'chapters': chapters,
            '_ntk_kind': kind,
        }

    def _ntk_fetch_index_via_api_session(self, url, state):
        session = state.get('session')
        headers = dict(state['doc_headers'])
        headers['Referer'] = state['origin'] + '/'
        try:
            response = self._ntk_request_get(session, url, headers)
        except Exception as e:
            self.log(f"ERROR: [NewToki] Index request failed: {e}")
            return None
        cf_keywords = ('cf-browser-verification', 'Just a moment', 'Ray ID')
        if response.status_code in (403, 503) or any(k in response.text for k in cf_keywords):
            self.log(
                f"[NewToki] API index request blocked "
                f"(HTTP {response.status_code})."
            )
            return None
        if response.status_code != 200:
            self.log(
                f"ERROR: [NewToki] Index request returned HTTP "
                f"{response.status_code}."
            )
            return None
        return response.text

    def _ntk_refresh_cloudflare_session(self, url):
        """Automated fallback: refresh CF cookies in real Chrome, then reuse API."""
        self.log(
            "[NewToki] Refreshing Cloudflare clearance with installed Chrome..."
        )
        if not self._start_ntk_browser(url):
            return False
        try:
            if self._page:
                try:
                    self._page.goto(url, wait_until="domcontentloaded",
                                    timeout=30000)
                except Exception:
                    pass
                try:
                    self._page.wait_for_timeout(2500)
                except Exception:
                    time.sleep(2.5)
            return True
        except Exception as e:
            self.log(f"[NewToki] Chrome clearance refresh failed: {e}")
            return False

    def _ntk_fetch_chapter_api(self, chapter_url, chapter_name, state=None):
        state = state or self._ntk_api_state
        if not state:
            state = self._ntk_prepare_api_state(self._book_url or chapter_url)
        if not state or not state.get('session'):
            self.log("  [NewToki] API session is not ready.")
            return None

        session = state['session']
        novel_id = state.get('novel_id') or self._ntk_novel_id_from_url(chapter_url)
        episode_id = self._ntk_episode_id_from_url(chapter_url)
        if not novel_id or not episode_id:
            self.log(f"  [NewToki] Invalid chapter URL: {chapter_url}")
            return None

        for attempt in range(1, 7):
            if self._stop_requested:
                return None
            try:
                doc_headers = dict(state['doc_headers'])
                doc_headers['Referer'] = state.get('index_url') or self._book_url or state['origin']
                cache_bust = int(time.time() * 1000)
                chapter_page_url = f"{chapter_url}?cb={cache_bust}"
                chapter_res = self._ntk_request_get(session, chapter_page_url, doc_headers)
                if (
                    chapter_res.status_code != 200
                    or 'Just a moment' in chapter_res.text
                    or 'cf-browser-verification' in chapter_res.text
                ):
                    self.log(
                        f"  [NewToki] Chapter HTML blocked/failed "
                        f"(attempt {attempt}/6, HTTP {chapter_res.status_code})."
                    )
                    time.sleep(0.5)
                    continue

                if '\ubcf8\ubb38\uc774 \uc544\uc9c1 \uc900\ube44\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4' in chapter_res.text:
                    self.log(f"  [NewToki] Chapter not ready: {chapter_name}")
                    return None

                token_match = re.search(r'\\"token\\":\\"([^\\"]+)\\"', chapter_res.text)
                if not token_match:
                    token_match = re.search(r'"token"\s*:\s*"([^"]+)"', chapter_res.text)
                if not token_match:
                    self.log(f"  [NewToki] No content token found (attempt {attempt}/6).")
                    time.sleep(0.4)
                    continue
                token = token_match.group(1)

                nv_cookie = session.cookies.get('nv')
                if not nv_cookie:
                    self._ntk_issue_nv(state, chapter_url)
                    nv_cookie = session.cookies.get('nv')
                if not nv_cookie:
                    self.log(f"  [NewToki] nv cookie missing (attempt {attempt}/6).")
                    time.sleep(0.4)
                    continue

                nonce = self._ntk_b64url_encode(os.urandom(24))
                message = f"{token}.{nonce}.{state['user_agent']}".encode('utf-8')
                proof = self._ntk_b64url_encode(
                    hmac.new(nv_cookie.encode('utf-8'), message, hashlib.sha256).digest()
                )
                payload = {
                    'novelId': novel_id,
                    'episodeId': episode_id,
                    'token': token,
                    'nonce': nonce,
                    'proof': proof,
                }
                api_headers = dict(state['api_headers'])
                api_headers['Referer'] = chapter_url
                content_res = self._ntk_request_post_json(
                    session,
                    state['origin'] + '/api/novel-content',
                    payload,
                    api_headers,
                )
                try:
                    content_json = content_res.json()
                except Exception:
                    self.log(f"  [NewToki] Content API returned non-JSON (attempt {attempt}/6).")
                    time.sleep(0.4)
                    continue

                if not content_json.get('ok'):
                    error = content_json.get('error') or 'unknown'
                    if error == 'expired':
                        self._ntk_issue_nv(state, chapter_url)
                    elif error == 'blocked':
                        self.log("  [NewToki] Content API reported blocked.")
                        return None
                    else:
                        self.log(f"  [NewToki] Content API error: {error}")
                    time.sleep(0.5)
                    continue

                encrypted_payload = content_json.get('payload') or ''
                key_text = nv_cookie.split('.')[0]
                key = self._ntk_b64url_decode(key_text)
                encrypted = self._ntk_b64url_decode(encrypted_payload)
                decrypted = bytearray(len(encrypted))
                for i, value in enumerate(encrypted):
                    decrypted[i] = value ^ key[i % len(key)]
                plaintext = decrypted.decode('utf-8')
                data = self._ntk_build_text_chapter(
                    chapter_name,
                    plaintext,
                    'ntk api/novel-content',
                )
                if data and len(data.get('contentText', '')) >= 40:
                    return data
                self.log(f"  [NewToki] Decrypted content was empty/short (attempt {attempt}/6).")
            except Exception as e:
                self.log(f"  [NewToki] API chapter attempt {attempt}/6 failed: {e}")
            time.sleep(0.5)
        return None

    def _ntk_image_url_candidates(self, html_text, base_url):
        urls = []

        def add(value):
            value = self._ntk_abs_url(base_url, value)
            if not value or self._ntk_is_site_image(value):
                return
            lower = value.lower()
            if not re.search(r'\.(?:jpg|jpeg|png|webp|gif|avif)(?:[?#]|$)', lower):
                return
            if any(skip in lower for skip in ('favicon', 'avatar', 'profile', 'emoji')):
                return
            if value not in urls:
                urls.append(value)

        for tag in re.findall(r'<img\b[^>]*>', html_text or '', re.I | re.S):
            attrs = self._ntk_html_attrs(tag)
            for key in ('data-src', 'data-original', 'data-lazy-src', 'srcset', 'src'):
                if attrs.get(key):
                    add(attrs[key])
                    break

        for match in re.finditer(
            r'https?:\\?/\\?/[^"\'\s<>]+(?:toonflix|ntk|newtoki|imagebox)[^"\'\s<>]+',
            html_text or '',
            re.I,
        ):
            add(match.group(0))

        for match in re.finditer(
            r'["\']((?:/[^"\'\s<>]+)?/(?:data|upload|uploads|webtoon|toon|image|images|file)/[^"\'\s<>]+\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^"\'\s<>]*)?)["\']',
            html_text or '',
            re.I,
        ):
            add(match.group(1))

        page_images = [
            url for url in urls
            if not re.search(r'(?:novel|webtoon)_thumb|og-default', url, re.I)
        ]
        return page_images or urls

    def _ntk_build_image_chapter(self, title, image_urls, selector):
        if not image_urls:
            return None
        images = []
        html_parts = []
        for idx, img_url in enumerate(image_urls, 1):
            parsed = urllib.parse.urlparse(img_url)
            name = os.path.basename(parsed.path) or f'page_{idx:04d}.jpg'
            name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._')
            if '.' not in name:
                name += '.jpg'
            name = f'ntk_webtoon_{idx:04d}_{name}'
            images.append({'url': img_url, 'name': name})
            html_parts.append(
                f'<div class="ntk-webtoon-page">'
                f'<img src="{html.escape(img_url, quote=True)}" alt="page {idx}" />'
                f'</div>'
            )
        return {
            'chapterName': title or 'Chapter',
            'sourceChapterName': title or 'Chapter',
            'contentText': '',
            'contentHtml': '\n'.join(html_parts),
            'images': images,
            'contentCss': (
                '.ntk-webtoon-page { margin: 0; padding: 0; text-align: center; } '
                '.ntk-webtoon-page img { display: block; width: 100%; '
                'max-width: 100%; height: auto; margin: 0 auto; }'
            ),
            '_debugSelector': selector,
        }

    def _ntk_fetch_webtoon_chapter(self, chapter_url, chapter_name, state=None):
        state = state or self._ntk_api_state
        if not state:
            state = self._ntk_prepare_api_state(self._book_url or chapter_url)
        if not state or not state.get('session'):
            self.log("  [NewToki] API session is not ready.")
            return None
        session = state['session']
        headers = dict(state['doc_headers'])
        headers['Referer'] = state.get('index_url') or self._book_url or state['origin']
        for attempt in range(1, 5):
            try:
                url = f"{chapter_url}?cb={int(time.time() * 1000)}"
                response = self._ntk_request_get(session, url, headers)
                if (
                    response.status_code != 200
                    or 'Just a moment' in response.text
                    or 'cf-browser-verification' in response.text
                ):
                    self.log(
                        f"  [NewToki] Webtoon HTML blocked/failed "
                        f"(attempt {attempt}/4, HTTP {response.status_code})."
                    )
                    time.sleep(0.5)
                    continue
                image_urls = self._ntk_image_url_candidates(
                    response.text,
                    chapter_url,
                )
                data = self._ntk_build_image_chapter(
                    chapter_name,
                    image_urls,
                    'ntk webtoon image scrape',
                )
                if data:
                    return data
                self.log(
                    f"  [NewToki] No webtoon images found "
                    f"(attempt {attempt}/4)."
                )
            except Exception as e:
                self.log(
                    f"  [NewToki] Webtoon chapter attempt "
                    f"{attempt}/4 failed: {e}"
                )
            time.sleep(0.5)
        return None

    def fetch_ntk_binary(self, url, referer=None):
        """Fetch a NewToki binary asset with the verified API session."""
        state = self._ntk_api_state
        if not state or not state.get('session') or not url:
            return None
        headers = dict(state.get('doc_headers') or {})
        headers.update({
            'Accept': (
                'image/avif,image/webp,image/apng,image/svg+xml,'
                'image/*,*/*;q=0.8'
            ),
            'Referer': referer or state.get('index_url') or state.get('origin') or '',
            'sec-fetch-dest': 'image',
            'sec-fetch-mode': 'no-cors',
            'sec-fetch-site': 'same-origin',
        })
        try:
            response = self._ntk_request_get(state['session'], url, headers)
            content_type = response.headers.get('content-type', '')
            if (
                response.status_code == 200
                and len(response.content) > 100
                and (
                    content_type.startswith('image/')
                    or response.content[:4] == b'\x89PNG'
                    or response.content[:3] == b'\xff\xd8\xff'
                    or response.content[:4] == b'RIFF'
                    or response.content[:4] == b'GIF8'
                    or response.content[:12] == b'\x00\x00\x00\x18ftypavif'
                )
            ):
                return response.content
            self.log(
                f"  [NewToki] Cover asset fetch failed "
                f"(HTTP {response.status_code}, {len(response.content)} bytes, "
                f"{content_type or 'unknown content-type'})."
            )
        except Exception as e:
            self.log(f"  [NewToki] Cover asset fetch failed: {e}")
        return None

    def _ntk_dump_debug_page(self, page, label):
        """Save rendered NewToki page state for debugging only."""
        try:
            logs_dir = os.path.join(_get_base_dir(), 'logs')
            os.makedirs(logs_dir, exist_ok=True)
            safe_label = re.sub(r'[^A-Za-z0-9._-]+', '_', label or 'chapter')
            safe_label = safe_label.strip('._')[:80] or 'chapter'
            stamp = time.strftime('%Y%m%d_%H%M%S')
            html_path = os.path.join(logs_dir, f'ntk_debug_{stamp}_{safe_label}.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(page.content() if page else '')
            self.log(f"  [NewToki] Debug dump saved: {html_path}")
            return html_path
        except Exception as e:
            self.log(f"  [NewToki] Debug dump failed: {e}")
            return None

    def _ntk_parse_book(self, url):
        """Scrape ntk metadata using the site's encrypted content API."""
        self._stop_requested = False
        kind = self._ntk_content_kind_from_url(url)
        self.log(
            f"[NewToki] Detected ntk {kind} URL, using curl_cffi API scraper."
        )
        self.log(f"[NewToki] Refreshing Chrome clearance before API fetch: {url}")

        novel_id = self._ntk_novel_id_from_url(url)
        index_html = None
        if not self._ntk_refresh_cloudflare_session(url):
            self.log("ERROR: [NewToki] Could not refresh Chrome clearance.")
            return None
        state = self._ntk_prepare_api_state(url, novel_id)
        try:
            self.cleanup()
        except Exception:
            pass
        try:
            closed = self._close_ntk_profile_chrome(
                self._get_ntk_user_data_dir()
            )
            if closed:
                self.log(
                    f"[NewToki] Closed {closed} temporary Chrome "
                    "process(es)."
                )
        except Exception:
            pass
        if not state:
            self.log("ERROR: [NewToki] Could not create API session.")
            return None
        self.log("[NewToki] Fetching index via verified Chrome session.")
        index_html = self._ntk_fetch_index_via_api_session(url, state)
        if not index_html:
            self.log(
                "ERROR: [NewToki] API index fetch failed after Chrome "
                "clearance refresh. If Cloudflare is stricter on your IP, "
                "set NPIA_NTK_CURL to a copied Chrome cURL request and retry."
            )
            return None
        data = self._ntk_parse_index_html(index_html, url)
        chapters = data.get('chapters') or []
        if not chapters:
            self.log("ERROR: [NewToki] No episode links found on page.")
            try:
                logs_dir = os.path.join(_get_base_dir(), 'logs')
                os.makedirs(logs_dir, exist_ok=True)
                kind = data.get('_ntk_kind') or self._ntk_content_kind_from_url(url)
                stamp = time.strftime('%Y%m%d_%H%M%S')
                path = os.path.join(
                    logs_dir,
                    f'ntk_{kind}_index_debug_{stamp}_{novel_id}.html',
                )
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(index_html or '')
                self.log(f"[NewToki] Index debug saved: {path}")
            except Exception:
                pass
            return None

        data['coverUrl'] = self._ntk_prefer_novelpia_cover_url(
            data.get('coverUrl', '')
        )
        if data.get('coverUrl'):
            self.log(f"[NewToki] Cover URL: {data.get('coverUrl')}")
        else:
            self.log("[NewToki] Cover URL not found on index page.")

        data['_ntk_novel'] = True
        data['_ntk_api'] = True
        data['_ntk_novel_id'] = novel_id
        data['_ntk_kind'] = kind
        self._book_data = data
        self._book_url = url
        self.log(
            f"[NewToki] Book: {data.get('bookname', '?')} - "
            f"{len(chapters)} chapters"
        )
        return data

    def _ntk_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Fetch one ntk novel/webtoon chapter."""
        if self._book_data and self._book_data.get('_ntk_kind') == 'webtoon':
            data = self._ntk_fetch_webtoon_chapter(chapter_url, chapter_name)
            if data:
                debug_selector = data.pop('_debugSelector', '')
                self.log("  [NewToki] Used webtoon image scrape.")
                if debug_selector:
                    self.log(f"  [NewToki] Content selector: {debug_selector}")
                return data
            self.log(f"  [NewToki] Webtoon chapter fetch failed: {chapter_name}")
            return None
        data = self._ntk_fetch_chapter_api(chapter_url, chapter_name)
        if data:
            debug_selector = data.pop('_debugSelector', '')
            self.log("  [NewToki] Used encrypted content API.")
            if debug_selector:
                self.log(f"  [NewToki] Content selector: {debug_selector}")
            return data
        self.log(f"  [NewToki] API chapter fetch failed: {chapter_name}")
        return None

    def _kakao_parse_book(self, url):
        """Scrape book metadata + episode list from a KakaoPage content page.

        Uses Playwright DOM scraping instead of novel-downloader JS rules.
        Returns the standard book data dict or None on error.
        """
        if not self._page:
            self.start()

        self._stop_requested = False
        self.log(f"[KakaoPage] Navigating to: {url}")

        # Extract series ID from URL
        m = re.search(r'/content/(\d+)', url)
        series_id = m.group(1) if m else ''

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for the SPA to render content
            self._page.wait_for_timeout(3000)
        except Exception as e:
            self.log(f"ERROR: Page load failed: {e}")
            return None

        self.log("[KakaoPage] Extracting metadata...")

        # --- Extract metadata via JS ---
        try:
            meta = self._page.evaluate("""
                (function() {
                    var og = function(prop) {
                        var el = document.querySelector('meta[property="og:' + prop + '"]');
                        return el ? el.content : '';
                    };
                    // Title: try og:title first, then first h2
                    var title = og('title') || '';
                    // Clean "- 웹소설 | 카카오페이지" suffix from og:title
                    title = title.replace(/\\s*[-–]\\s*(웹소설|웹툰).*$/i, '').trim();
                    if (!title) {
                        var h2 = document.querySelector('h2');
                        if (h2) title = h2.innerText.trim();
                    }
                    // Author: look for text near the title
                    var author = '';
                    var spans = document.querySelectorAll('span, div, a');
                    for (var i = 0; i < spans.length; i++) {
                        var s = spans[i];
                        var t = s.innerText.trim();
                        // Author is typically a short name appearing after the title
                        if (s.previousElementSibling) {
                            var prev = s.previousElementSibling;
                            if (prev.tagName === 'H2' ||
                                (prev.innerText && prev.innerText.trim() === title)) {
                                if (t.length > 0 && t.length < 50 && !t.includes('|')) {
                                    author = t;
                                    break;
                                }
                            }
                        }
                    }
                    // Fallback: look for author pattern in page text
                    if (!author) {
                        var bodyText = document.body.innerText;
                        // Pattern: title followed by author name on next line
                        var idx = bodyText.indexOf(title);
                        if (idx >= 0) {
                            var after = bodyText.substring(idx + title.length, idx + title.length + 200);
                            var lines = after.split('\\n').filter(function(l) {
                                return l.trim().length > 0;
                            });
                            if (lines.length > 0 && lines[0].trim().length < 50) {
                                author = lines[0].trim();
                            }
                        }
                    }
                    var cover = og('image') || '';
                    var desc = og('description') || '';

                    // Total episode count: look for "전체 NNN" text
                    var totalText = document.body.innerText;
                    var totalMatch = totalText.match(/전체\\s+(\\d+)/);
                    var totalEpisodes = totalMatch ? parseInt(totalMatch[1]) : 0;

                    return {
                        title: title,
                        author: author,
                        cover: cover,
                        description: desc,
                        totalEpisodes: totalEpisodes
                    };
                })()
            """)
        except Exception as e:
            self.log(f"ERROR: Metadata extraction failed: {e}")
            return None

        title = meta.get('title', '')
        author = meta.get('author', '')
        cover = meta.get('cover', '')
        description = meta.get('description', '')
        total_episodes = meta.get('totalEpisodes', 0)

        if not title:
            self.log("ERROR: Could not extract title from KakaoPage.")
            return None

        self.log(f"[KakaoPage] Title: {title}, Author: {author}, "
                 f"Episodes: {total_episodes}")

        # --- Fetch episode list via BFF API ---
        # The DOM only shows ~5-6 episodes initially and expanding is
        # unreliable.  Instead, call the BFF API directly from the
        # browser context to get ALL episodes in a single request.
        self.log("[KakaoPage] Fetching episode list via API...")
        try:
            episodes = self._page.evaluate("""
                async (seriesId) => {
                    const url = `https://bff-page.kakao.com/api/gateway/api/v2/content/product/list?series_id=${seriesId}&cursor_index=0&cursor_direction=ANCHOR&window_size=10000&sort_opt=asc`;
                    const resp = await fetch(url);
                    const data = await resp.json();
                    const list = (data.result || {}).list || [];
                    const hasUserAccessFlag = (obj) => {
                        const seen = new Set();
                        const walk = (value, path) => {
                            if (!value || typeof value !== 'object') return false;
                            if (seen.has(value)) return false;
                            seen.add(value);
                            for (const [rawKey, v] of Object.entries(value)) {
                                const key = String(rawKey || '').toLowerCase();
                                const full = path ? `${path}.${key}` : key;
                                if (/price|count|blocked|lock|free_change/.test(full)) {
                                    continue;
                                }
                                const accessKey = /purchas|bought|owned|rented|rental|ticket|pass|usable|useable|access|viewable|readable/.test(full);
                                if (accessKey) {
                                    if (v === true) return true;
                                    if (typeof v === 'number' && v > 0) return true;
                                    if (typeof v === 'string') {
                                        const s = v.trim().toLowerCase();
                                        if (/^(yes|y|true|1|buy|bought|purchase|purchased|yes[_-]?purchase|yes[_-]?purchased|rent|rented|rental|yes[_-]?rent|yes[_-]?rented|ticket|pass|owned|available|viewable|readable|accessible)$/.test(s)) {
                                            return true;
                                        }
                                        if (/^(false|no|n|none|null|0|not[_-]?purchased|not[_-]?rented|no[_-]?purchase|no[_-]?rent|unowned|unavailable|expired)$/.test(s)) {
                                            continue;
                                        }
                                    }
                                }
                                if (v && typeof v === 'object' && walk(v, full)) return true;
                            }
                            return false;
                        };
                        return walk(obj, '');
                    };
                    return list.map(entry => {
                        const item = entry.item || {};
                        const fullTitle = item.title || '';
                        const epMatch = fullTitle.match(/([0-9]+화.*)$/);
                        const shortName = epMatch ? epMatch[1]
                            : (fullTitle || 'Episode ' + (item.order_value || 0));
                        const isFree = !!item.is_free;
                        const isAccessible = isFree || hasUserAccessFlag(item);
                        return {
                            url: `/content/${seriesId}/viewer/${item.product_id}`,
                            name: shortName,
                            fullName: fullTitle,
                            isVIP: !isAccessible,
                            isPaid: !isAccessible,
                            isFree: isFree,
                            isAccessible: isAccessible,
                            order: item.order_value || 0,
                            productId: item.product_id || 0,
                            slideType: item.slide_type || '',
                        };
                    });
                }
            """, series_id)
        except Exception as e:
            self.log(f"ERROR: Episode extraction failed: {e}")
            return None

        if not episodes:
            self.log("WARNING: No episodes found on page.")

        # Sort oldest-to-newest like Kakao's first-episode-first option. Some Kakao
        # responses mix cursor order, missing order_value, and title-only
        # numbering, so use every stable signal we have.
        episodes.sort(key=self._kakao_episode_sort_key)

        # Fix relative URLs to absolute
        for ep in episodes:
            if ep['url'] and not ep['url'].startswith('http'):
                ep['url'] = 'https://page.kakao.com' + ep['url']

        self.log(f"[KakaoPage] Found {len(episodes)} episodes.")
        paid_accessible = sum(
            1 for ep in episodes
            if ep.get('isAccessible') and not ep.get('isFree')
        )
        if paid_accessible:
            self.log(
                f"[KakaoPage] Detected {paid_accessible} rented/purchased "
                "episode(s) as accessible."
            )

        # --- Extract tags from the About tab ---
        tags = []
        try:
            about_url = re.sub(r'(\?.*)?$', '?tab_type=about', url)
            self._page.goto(about_url, wait_until="domcontentloaded",
                            timeout=15000)
            self._page.wait_for_timeout(2000)
            tags = self._page.evaluate("""
                (function() {
                    // Tags appear as "#태그" links under the 키워드 heading
                    var tags = [];
                    var links = document.querySelectorAll('a, span, div');
                    for (var i = 0; i < links.length; i++) {
                        var t = links[i].innerText.trim();
                        if (t.startsWith('#') && t.length > 1 && t.length < 30) {
                            var tag = t.substring(1);  // strip leading #
                            if (tags.indexOf(tag) === -1) tags.push(tag);
                        }
                    }
                    return tags;
                })()
            """) or []
            if tags:
                self.log(f"[KakaoPage] Tags: {', '.join(tags)}")
        except Exception:
            pass  # Tags are optional

        data = {
            'bookname': title,
            'author': author,
            'coverUrl': cover,
            'introduction': description,
            'introductionHTML': f'<p>{description}</p>' if description else '',
            'bookUrl': url,
            'chapterCount': len(episodes),
            'chapters': episodes,
            'language': 'ko',
            'tags': tags,
            '_kakaopage': True,  # Flag for chapter parser
        }

        self._book_data = data
        self._book_url = url
        return data

    @staticmethod
    def _kakao_title_number(title):
        """Best-effort episode number from Korean/Arabic Kakao titles."""
        if not title:
            return 0
        m = re.search(r'(\d+)\s*화', str(title))
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 0
        return 0

    def _kakao_episode_sort_key(self, ep):
        """Oldest-to-newest sort key for Kakao episode rows."""
        order = ep.get('order') or 0
        if not order:
            order = self._kakao_title_number(
                ep.get('fullName') or ep.get('name') or ''
            )
        product_id = ep.get('productId') or 0
        return (order or 10**12, product_id)

    def _kakao_build_image_chapter(self, image_files, chapter_name):
        """Build standard chapter data from Kakao ImageViewerData files."""
        images = []
        html_parts = ['<div class="kakao-image-chapter">']
        text_parts = []

        ordered = sorted(
            image_files or [],
            key=lambda f: (f.get('no') or 0, f.get('url') or '')
        )
        for idx, info in enumerate(ordered, 1):
            img_url = html.unescape((info.get('url') or '').strip())
            if not img_url:
                continue

            filename = urllib.parse.unquote(info.get('filename') or '')
            filename = filename.split('?', 1)[0].split('&', 1)[0]
            filename = re.sub(r'[^A-Za-z0-9._-]+', '_', filename).strip('._')
            if not filename or '.' not in filename:
                filename = f'kakao_image_{idx:04d}.jpg'

            images.append({'url': img_url, 'name': filename})
            alt = f"{chapter_name} image {idx}"
            width = info.get('width') or ''
            height = info.get('height') or ''
            size_attrs = ''
            if width:
                size_attrs += f' width="{int(width)}"'
            if height:
                size_attrs += f' height="{int(height)}"'
            html_parts.append(
                '<div class="kakao-image-page">'
                f'<img src="{html.escape(img_url, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}"{size_attrs}/>'
                '</div>'
            )
            text_parts.append(f'[Image {idx}]')

        html_parts.append('</div>')
        if not images:
            return None

        return {
            'chapterName': chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': '\n'.join(text_parts),
            'contentHtml': '\n'.join(html_parts),
            'contentCss': (
                '.kakao-image-chapter { text-align: center; }\n'
                '.kakao-image-page { margin: 0 auto 0.5rem; '
                'page-break-inside: avoid; }\n'
                '.kakao-image-page img { display: block; max-width: 100%; '
                'height: auto; margin: 0 auto; }'
            ),
            'images': images,
        }

    def _kakao_load_all_episodes(self, expected_count):
        """Expand the episode list on a KakaoPage content page.

        Clicks the expand chevron and scrolls until all episodes are visible.
        """
        max_attempts = 100  # Safety limit for expansion clicks/scrolls
        last_count = 0

        for attempt in range(max_attempts):
            if self._stop_requested:
                break

            # Count current visible episode links
            count = self._page.evaluate(
                "document.querySelectorAll('a[href*=\"/viewer/\"]').length"
            )

            if expected_count > 0 and count >= expected_count:
                break
            if count == last_count and attempt > 5:
                # No new episodes loaded after several attempts
                break
            last_count = count

            # Try clicking expand/chevron/load-more buttons
            clicked = self._page.evaluate("""
                (function() {
                    // Look for SVG chevron-down or expand button near episode list
                    var svgs = document.querySelectorAll('svg');
                    for (var i = 0; i < svgs.length; i++) {
                        var svg = svgs[i];
                        var parent = svg.closest('button') || svg.closest('a')
                                     || svg.parentElement;
                        if (!parent) continue;
                        var rect = parent.getBoundingClientRect();
                        // The expand chevron is typically centered below the
                        // last visible episode, with a small height
                        if (rect.height > 10 && rect.height < 80 &&
                            rect.width > 10 && rect.width < 200 &&
                            rect.top > 300) {
                            // Check if it looks like a down-arrow area
                            var path = svg.querySelector('path');
                            if (path) {
                                parent.click();
                                return true;
                            }
                        }
                    }
                    // Fallback: look for a "more" or expand button by text
                    var buttons = document.querySelectorAll('button');
                    for (var j = 0; j < buttons.length; j++) {
                        var b = buttons[j];
                        var t = b.innerText.trim();
                        if (t.includes('더보기') || t.includes('전체') ||
                            t.includes('펼치기')) {
                            b.click();
                            return true;
                        }
                    }
                    return false;
                })()
            """)

            if clicked:
                self._page.wait_for_timeout(1000)
            else:
                # Scroll down to trigger lazy loading
                self._page.evaluate(
                    "window.scrollBy(0, window.innerHeight)"
                )
                self._page.wait_for_timeout(800)

    def _kakao_parse_chapter(self, chapter_url, chapter_name, page=None):
        """Scrape a single KakaoPage chapter from the viewer.

        Uses a fast API-only approach:
          1. Call the BFF viewer data API to get sdownload resource URLs.
          2. Fetch all content JSONs in parallel via Promise.all.
          3. Parse the paragraphList from each JSON chunk.

        No page navigation needed — runs ~15x faster than loading the
        full React SPA for each chapter.

        Returns the standard chapter data dict or None on error.
        """
        target = page or self._page
        if target is None:
            return None

        # Extract series_id and product_id from the chapter URL
        m = re.search(r'/content/(\d+)/viewer/(\d+)', chapter_url)
        if not m:
            self.log(f"  [KakaoPage] Invalid viewer URL: {chapter_url}")
            return None
        s_id, p_id = m.group(1), m.group(2)
        # Ensure the page is on the kakao.com domain so that fetch()
        # sends the correct cookies.  After "Enter Browser" restarts the
        # headless browser, the page is on about:blank — the BFF API
        # returns 403 Forbidden for requests from a null origin.
        try:
            current_url = target.url or ''
        except Exception:
            current_url = ''
        if 'kakao.com' not in current_url:
            try:
                target.goto(
                    f'https://page.kakao.com/content/{s_id}',
                    wait_until="domcontentloaded", timeout=30000,
                )
                target.wait_for_timeout(1000)
            except Exception as e:
                self.log(f"  [KakaoPage] Failed to navigate to book page: {e}")

        # ---- Strategy 1: Direct API fetch (fast, no navigation) ----
        api_result = None
        try:
            api_result = target.evaluate("""
            async ([seriesId, productId]) => {
                const vResp = await fetch(
                    `https://bff-page.kakao.com/api/gateway/api/v1/viewer/data`
                    + `?series_id=${seriesId}&product_id=${productId}`
                );
                const httpStatus = vResp.status;
                let vData;
                try { vData = await vResp.json(); } catch(e) {
                    return { locked: true, chunks: [], httpStatus,
                             msg: 'Non-JSON response', pageUrl: location.href };
                }
                const vd = vData.viewerData || {};
                const baseUrl = vd.atsServerUrl || '';
                const contents = vd.contentsList || [];
                const imageData = vd.imageDownloadData || {};
                const imageFiles = imageData.files || [];
                const msg = vData.message || vData.msg || null;

                // No text chunks and no image files means locked/no access.
                if ((!baseUrl || !contents.length) && !imageFiles.length)
                    return { locked: true, chunks: [], httpStatus, msg,
                             pageUrl: location.href };

                if (imageFiles.length) {
                    return {
                        locked: false,
                        chunks: [],
                        imageFiles: imageFiles.map((f, idx) => ({
                            no: f.no || idx + 1,
                            url: f.secureUrl || '',
                            width: f.width || 0,
                            height: f.height || 0,
                            filename: ((f.secureUrl || '').match(/filename=([^&]+)/) || [])[1] || ''
                        })).filter(f => f.url),
                        httpStatus, msg, pageUrl: location.href
                    };
                }

                const fetches = contents.map(async (c) => {
                    if (!c.secureUrl) return null;
                    try {
                        const r = await fetch(baseUrl + c.secureUrl);
                        const ct = r.headers.get('content-type') || '';
                        if (!ct.includes('json')) return null;
                        return await r.text();
                    } catch(e) { return null; }
                });
                const results = (await Promise.all(fetches)).filter(r => r !== null);
                return { locked: false, chunks: results, imageFiles: [],
                         httpStatus, msg, pageUrl: location.href };
            }
            """, [s_id, p_id])
        except Exception as e:
            self.log(f"  [KakaoPage] API fetch error: {e}")

        if api_result:
            http_st = api_result.get('httpStatus', '?')
            page_url = api_result.get('pageUrl', '?')
            api_msg = api_result.get('msg', '')
            if api_result.get('locked'):
                self.log(
                    f"  [KakaoPage] LOCKED: {chapter_name} "
                    f"(HTTP {http_st}, msg={api_msg}, page={page_url})"
                )
                return {'_locked': True, 'chapterName': chapter_name}

            image_files = api_result.get('imageFiles') or []
            if image_files:
                return self._kakao_build_image_chapter(
                    image_files, chapter_name)

            raw_chunks = api_result.get('chunks', [])
            json_chunks = [r.encode('utf-8') for r in raw_chunks]
            para_tuples, content_css = self._kakao_extract_from_json(
                json_chunks)
            if para_tuples:
                full_text, content_html = self._kakao_build_output(
                    para_tuples)
                display_name = self._kakao_heading_title(
                    para_tuples, chapter_name)
                return {
                    'chapterName': display_name,
                    'sourceChapterName': chapter_name,
                    'contentText': full_text,
                    'contentHtml': content_html,
                    'contentCss': content_css,
                    'images': [],
                }

        # ---- Strategy 2: Full page load fallback ----
        # Only used when the API approach fails (e.g. auth issues).
        json_chunks_fb = []

        def _capture_response(response):
            try:
                url = response.url
                ct = response.headers.get('content-type', '')
                if ('sdownload/resource' in url and 'json' in ct):
                    body = response.body()
                    if body:
                        json_chunks_fb.append(body)
            except Exception:
                pass

        target.on('response', _capture_response)
        try:
            target.goto(chapter_url, wait_until="domcontentloaded",
                        timeout=30000)
            target.wait_for_timeout(4000)
        except Exception as e:
            self.log(f"  [KakaoPage] Fallback page load error: {e}")
            target.remove_listener('response', _capture_response)
            return None
        target.remove_listener('response', _capture_response)

        para_tuples, content_css = self._kakao_extract_from_json(
            json_chunks_fb)
        if para_tuples:
            full_text, content_html = self._kakao_build_output(
                para_tuples)
            display_name = self._kakao_heading_title(
                para_tuples, chapter_name)
            return {
                'chapterName': display_name,
                'sourceChapterName': chapter_name,
                'contentText': full_text,
                'contentHtml': content_html,
                'contentCss': content_css,
                'images': [],
            }

        # ---- Strategy 3: Shadow DOM extraction ----
        shadow_text = self._kakao_extract_from_shadow(target)
        if shadow_text:
            paragraphs = [p.strip() for p in shadow_text.split('\n')
                          if p.strip()]
            full_text = '\n'.join(paragraphs)
            html_parts = [f'<p>{p}</p>' for p in paragraphs]
            content_html = '\n'.join(html_parts)
            return {
                'chapterName': chapter_name,
                'contentText': full_text,
                'contentHtml': content_html,
                'images': [],
            }

        self.log(f"  [KakaoPage] No text extracted from: {chapter_name}")
        return None

    @staticmethod
    def _kakao_strip_headings(para_tuples):
        """Remove redundant episode heading paragraphs from chapter text.

        KakaoPage chapters start with a heading like '제1화' or '제103화'
        that duplicates info already in the chapter title.  Strip these
        and any surrounding &nbsp; spacers from the very beginning.

        para_tuples: list of (plain_text, html_fragment, type) tuples.
        Returns the trimmed list (may be unchanged).
        """
        import re
        cleaned = list(para_tuples)
        while cleaned:
            text = cleaned[0][0].strip()
            if not text or text == '&nbsp;':
                cleaned.pop(0)
            elif re.fullmatch(r'제\d+화\.?', text):
                cleaned.pop(0)
            else:
                break
        return cleaned

    @staticmethod
    def _kakao_build_output_legacy(para_tuples):
        """Convert (plain_text, html_fragment, type, style) tuples to
        full text and HTML output.

        Uses paragraph type to choose the appropriate HTML tag:
        - HEAD/HEADING → <h3>
        - Everything else → <p>
        Paragraph-level style (text-align) is applied via inline CSS.
        """
        text_parts = []
        html_parts = []
        for item in para_tuples:
            plain, html_frag, p_type = item[0], item[1], item[2]
            p_style = item[3] if len(item) > 3 else {}
            if not plain.strip():
                continue
            text_parts.append(plain)

            # Paragraph-level inline CSS (text-align)
            align = (p_style.get('textAlign') or p_style.get('align')
                     or '') if p_style else ''
            style_attr = f' style="text-align:{align.lower()}"' if align else ''

            if p_type in ('HEAD', 'HEADING', 'TITLE'):
                html_parts.append(f'<h3{style_attr}>{html_frag}</h3>')
            else:
                html_parts.append(f'<p{style_attr}>{html_frag}</p>')
        return '\n'.join(text_parts), '\n'.join(html_parts)

    @staticmethod
    def _kakao_heading_title(para_tuples, fallback):
        """Use the first source heading as the EPUB title/TOC label."""
        for item in para_tuples:
            plain = (item[0] or '').strip()
            p_type = (item[2] or '').lower()
            if plain and (p_type in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
                          or p_type in {'head', 'heading', 'title'}):
                return plain
        return fallback

    @staticmethod
    def _kakao_build_output(para_tuples):
        """Convert parsed Kakao paragraph tuples to full text and HTML."""
        from html import escape as _esc

        def _attrs_to_html(attrs, style, extra_class=''):
            attrs = attrs or {}
            style = style or {}
            parts = []

            cls = attrs.get('class') or attrs.get('className') or ''
            if extra_class:
                cls = f'{cls} {extra_class}'.strip()
            if cls:
                parts.append(f' class="{_esc(str(cls), quote=True)}"')

            style_parts = []
            raw_style = attrs.get('style') or ''
            if raw_style:
                style_parts.append(str(raw_style).strip().rstrip(';'))

            align = attrs.get('align') or ''
            if align:
                style_parts.append(f'text-align:{str(align).lower()}')

            color = (style.get('color') or style.get('fontColor')
                     or style.get('textColor') or '')
            if color:
                if not color.startswith('#') and not color.startswith('rgb'):
                    color = '#' + color
                style_parts.append(f'color:{color}')

            bg = style.get('backgroundColor') or style.get('highlight') or ''
            if bg:
                if not bg.startswith('#') and not bg.startswith('rgb'):
                    bg = '#' + bg
                style_parts.append(f'background-color:{bg}')

            fs = style.get('fontSize') or style.get('size') or ''
            if fs:
                style_parts.append(f'font-size:{fs}px'
                                   if isinstance(fs, (int, float))
                                   else f'font-size:{fs}')

            text_align = style.get('textAlign') or style.get('align') or ''
            if text_align:
                style_parts.append(f'text-align:{str(text_align).lower()}')

            if (style.get('lineThrough') or style.get('strikethrough')
                    or style.get('strike')):
                style_parts.append('text-decoration:line-through')

            if style_parts:
                safe_style = _esc('; '.join(
                    p for p in style_parts if p), quote=True)
                parts.append(f' style="{safe_style}"')

            return ''.join(parts)

        def _tag_for(p_type):
            tag = (p_type or '').lower()
            if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                       'p', 'div', 'blockquote', 'li'}:
                return tag
            if tag in {'head', 'heading', 'title'}:
                return 'h3'
            return 'p'

        def _append_page_break(parts):
            if parts and parts[-1] != '<div class="kakao-page-break">&#160;</div>':
                parts.append('<div class="kakao-page-break">&#160;</div>')

        text_parts = []
        html_parts = []
        previous_chunk = None
        first_heading_seen = False
        pending_heading_break = False
        inserted_heading_break = False
        for item in para_tuples:
            plain, html_frag, p_type = item[0], item[1], item[2]
            p_style = item[3] if len(item) > 3 else {}
            p_attrs = item[4] if len(item) > 4 else {}
            p_meta = item[5] if len(item) > 5 else {}
            if not plain.strip() and '<br' not in html_frag.lower():
                continue
            tag = _tag_for(p_type)

            chunk_index = p_meta.get('chunkIndex') if p_meta else None
            if (previous_chunk is not None and chunk_index is not None
                    and chunk_index != previous_chunk):
                _append_page_break(html_parts)

            is_blank = not plain.strip() and '<br' in html_frag.lower()
            is_heading = tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
            if (pending_heading_break and not inserted_heading_break
                    and not is_blank and not is_heading):
                _append_page_break(html_parts)
                inserted_heading_break = True
                pending_heading_break = False

            text_plain = plain
            extra_class = ''
            if is_heading and not first_heading_seen:
                first_heading_seen = True
                pending_heading_break = True
                extra_class = 'kakao-source-heading'

            text_parts.append(text_plain)
            attr_html = _attrs_to_html(p_attrs, p_style, extra_class)
            html_parts.append(f'<{tag}{attr_html}>{html_frag}</{tag}>')

            if chunk_index is not None:
                previous_chunk = chunk_index
        return '\n'.join(text_parts), '\n'.join(html_parts)

    @staticmethod
    def _is_colophon_chunk(paragraphs_text):
        """Check if a chunk's combined text looks like publisher boilerplate.

        KakaoPage embeds a copyright/colophon page in every chapter EPUB
        with ISBN, copyright notices, and publisher info.  These share
        contentId=0 with the actual chapter-title paragraphs, so we
        can't filter by ID alone — we detect them by content markers.

        Args:
            paragraphs_text: concatenated text of all paragraphs in a chunk.
        Returns True if the chunk is publisher boilerplate.
        """
        text = paragraphs_text or ''
        compact = re.sub(r'\s+', '', text)
        lower = text.lower()
        lower_compact = compact.lower()

        # Strong legal/identifier markers. One of these plus ordinary
        # publisher/contact labels is enough to identify a colophon page.
        strong_markers = [
            'ISBN', 'UCI', 'ⓒ', '©', '저작권법',
            '재가공할 수 없습니다', '서면 허락',
            '이 책의 내용을 이용하지 못합니다',
        ]
        publisher_markers = [
            '발행인', '발행처', '펴낸곳', '펴낸 곳',
            '기획/편집', '기획 / 편집', '책임편집',
            '표지', '주소',
        ]
        contact_markers = [
            '블로그', '트위터', '투고', 'blog.naver.com',
            'dreambook', 'samyangcnc',
        ]

        def _has(marker):
            marker_compact = re.sub(r'\s+', '', marker)
            return (marker in text or marker_compact in compact
                    or marker.lower() in lower
                    or marker_compact.lower() in lower_compact)

        strong_hits = sum(1 for marker in strong_markers if _has(marker))
        publisher_hits = sum(1 for marker in publisher_markers if _has(marker))
        contact_hits = sum(1 for marker in contact_markers if _has(marker))

        if strong_hits >= 1 and (publisher_hits + contact_hits) >= 2:
            return True
        if publisher_hits >= 3 and contact_hits >= 1:
            return True
        if publisher_hits >= 2 and contact_hits >= 2:
            return True
        return False

    def _kakao_fetch_css_resource(self, style_info):
        """Download a Kakao EPUB CSS resource referenced by styleList."""
        src = (style_info or {}).get('src') or ''
        if not src:
            return ''
        file_name = (style_info or {}).get('fileName') or 'style.css'
        cache_key = f'{src}|{file_name}'
        if cache_key in self._kakao_css_cache:
            return self._kakao_css_cache[cache_key]

        if src.startswith('http://') or src.startswith('https://'):
            url = src
        else:
            kid = urllib.parse.quote(src, safe='/')
            fname = urllib.parse.quote(file_name)
            url = (
                'https://dn-img-page.kakao.com/download/resource'
                f'?kid={kid}&filename={fname}'
            )

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                'Referer': 'https://page.kakao.com/',
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
            css = raw.decode('utf-8-sig', errors='replace')
            css = re.sub(r'@charset\s+["\']UTF-8["\'];?', '', css,
                         flags=re.IGNORECASE)
            css = css.replace('\r\n', '\n').replace('\r', '\n').strip()
        except Exception as e:
            self.log(f"  [KakaoPage] CSS fetch failed: {file_name}: {e}")
            css = ''

        self._kakao_css_cache[cache_key] = css
        return css

    def _kakao_extract_from_json(self, json_chunks):
        """Parse paragraphList from intercepted JSON API responses.

        Each JSON chunk has structure:
          { contentInfo: {
              paragraphList: [{
                id, type, text,
                style: { bold, italic, underline, ... },
                childParagraphList: [...]
              }, ...]
          } }
        Text nodes can be nested arbitrarily deep (e.g. P → SPAN → TEXT).
        Returns a list of (plain_text, html_fragment) tuples sorted by
        content order.
        """
        import json as _json
        from html import escape as _esc
        from html import unescape as _unesc

        def _safe_escape(text):
            """Unescape first, then re-escape to avoid double-encoding.

            The API text may already contain HTML entities like &lt; &gt;
            &nbsp;.  A plain html.escape() would turn & into &amp;,
            producing &amp;lt; in the output.
            """
            return _esc(_unesc(text))

        def _style_to_css(style):
            """Convert KakaoPage style dict to an inline CSS string."""
            css_parts = []
            # Color — check multiple possible field names
            color = (style.get('color') or style.get('fontColor')
                     or style.get('textColor') or '')
            if color:
                # Ensure # prefix for hex colors
                if color and not color.startswith('#') and not color.startswith('rgb'):
                    color = '#' + color
                css_parts.append(f'color:{color}')

            # Background / highlight
            bg = style.get('backgroundColor') or style.get('highlight') or ''
            if bg:
                if bg and not bg.startswith('#') and not bg.startswith('rgb'):
                    bg = '#' + bg
                css_parts.append(f'background-color:{bg}')

            # Font size
            fs = style.get('fontSize') or style.get('size') or ''
            if fs:
                css_parts.append(f'font-size:{fs}px' if isinstance(fs, (int, float))
                                 else f'font-size:{fs}')

            # Text alignment (paragraph-level, will be applied via wrapper)
            align = style.get('textAlign') or style.get('align') or ''
            if align:
                css_parts.append(f'text-align:{align.lower()}')

            # Line-through / strikethrough via CSS
            decoration = str(
                style.get('textDecoration') or style.get('text-decoration')
                or style.get('textDecorationLine')
                or style.get('text-decoration-line') or ''
            ).lower()
            line_through = (style.get('lineThrough')
                            or style.get('line-through')
                            or style.get('strikethrough')
                            or style.get('strike')
                            or 'line-through' in decoration)
            if line_through:
                css_parts.append('text-decoration:line-through')

            # Underline via CSS (backup if not using <u>)
            # (handled separately with <u> tag below)

            return '; '.join(css_parts)

        def _apply_tags(h, style):
            """Wrap HTML fragment in semantic tags for bold/italic/underline."""
            if style.get('bold') or style.get('fontWeight') == 'bold':
                h = f'<b>{h}</b>'
            if style.get('italic') or style.get('fontStyle') == 'italic':
                h = f'<i>{h}</i>'
            if style.get('underline'):
                h = f'<u>{h}</u>'
            return h

        def _wrap_with_style(h, style, attrs=None):
            """Apply inline CSS and semantic tags to an HTML fragment."""
            attrs = attrs or {}
            css_parts = []
            attr_style = attrs.get('style') or ''
            if attr_style:
                css_parts.append(str(attr_style).strip().rstrip(';'))
            css = _style_to_css(style)
            if css:
                css_parts.append(css)
            h = _apply_tags(h, style)
            attr_parts = []
            cls = attrs.get('class') or attrs.get('className') or ''
            if cls:
                attr_parts.append(f' class="{_esc(str(cls), quote=True)}"')
            if css_parts:
                safe_css = _esc('; '.join(css_parts), quote=True)
                attr_parts.append(f' style="{safe_css}"')
            if attr_parts:
                h = f'<span{"".join(attr_parts)}>{h}</span>'
            return h

        def _collect_html(node, is_root=False):
            """Recursively collect HTML from a paragraph node and children."""
            n_type = (node.get('type') or '').upper()
            if n_type == 'BR':
                return '<br />'
            if n_type == 'IMG':
                return ''

            text = (node.get('text') or '')
            children = node.get('childParagraphList') or []
            style = node.get('style') or {}
            attrs = node.get('attributes') or {}

            # Leaf node with text
            if text and not children:
                h = _safe_escape(text)
                h = _wrap_with_style(h, style, None if is_root else attrs)
                if n_type in {'S', 'STRIKE', 'DEL'}:
                    h = f'<s>{h}</s>'
                return h

            # Parent node: collect children
            parts = []
            if text:
                parts.append(_safe_escape(text))
            for child in children:
                ch = _collect_html(child)
                if ch:
                    parts.append(ch)
            result = ''.join(parts)

            # Apply style to the whole group
            result = _wrap_with_style(result, style, None if is_root else attrs)
            if n_type in {'S', 'STRIKE', 'DEL'}:
                result = f'<s>{result}</s>'
            return result

        def _collect_text(node):
            """Recursively collect plain text."""
            parts = []
            text = (node.get('text') or '').strip()
            if text:
                parts.append(_unesc(text))
            for child in (node.get('childParagraphList') or []):
                parts.extend(_collect_text(child))
            return parts

        def _node_has_image(node):
            if (node.get('type') or '').upper() == 'IMG':
                return True
            return any(_node_has_image(child)
                       for child in (node.get('childParagraphList') or []))

        parsed_chunks = []
        css_parts = []
        seen_css = set()
        for chunk_index, raw in enumerate(json_chunks):
            try:
                data = _json.loads(raw)
                info = data.get('contentInfo', {})
                content_id = info.get('contentId', 0)
                para_list = info.get('paragraphList', [])
                for style_info in (info.get('styleList') or []):
                    css = self._kakao_fetch_css_resource(style_info)
                    if css and css not in seen_css:
                        seen_css.add(css)
                        css_parts.append(css)

                chunk_paras = []
                for p in para_list:
                    p_id = int(p.get('id', 0))
                    p_type = (p.get('type') or '').upper()
                    p_style = p.get('style') or {}
                    p_attrs = p.get('attributes') or {}
                    plain = ''.join(_collect_text(p))
                    html_frag = _collect_html(p, is_root=True)
                    if _node_has_image(p) and not plain.strip():
                        continue
                    if plain.strip() or '<br' in html_frag.lower():
                        chunk_paras.append(
                            (chunk_index, content_id, p_id, plain,
                             html_frag, p_type, p_style, p_attrs))

                if chunk_paras:
                    parsed_chunks.append((chunk_index, chunk_paras))
            except Exception:
                continue

        skip_last_index = None
        if (self.kakao_skip_last_page and not self.kakao_keep_filler
                and len(parsed_chunks) > 1):
            skip_last_index = parsed_chunks[-1][0]

        all_paras = []
        for chunk_index, chunk_paras in parsed_chunks:
            combined = ' '.join(t for _, _, _, t, _, _, _, _ in chunk_paras)
            if not self.kakao_keep_filler:
                if skip_last_index is not None and chunk_index == skip_last_index:
                    continue

                # Skip publisher colophon/copyright chunks
                if self._is_colophon_chunk(combined):
                    continue

            all_paras.extend(chunk_paras)

        if not all_paras:
            return [], '\n\n'.join(css_parts)

        # Sort by chunk order first. Some Kakao chapters have multiple
        # contentId=0 resources (cover, then body), so contentId alone can
        # interleave unrelated pages.
        all_paras.sort(key=lambda x: (x[0], x[1], x[2]))
        return ([
                    (p[3], p[4], p[5], p[6], p[7],
                     {'chunkIndex': p[0], 'contentId': p[1], 'paragraphId': p[2]})
                    for p in all_paras
                ],
                '\n\n'.join(css_parts))

    def _kakao_extract_from_shadow(self, target):
        """Extract text from the KakaoPage viewer's Shadow DOM.

        The viewer renders EPUB content inside a shadow root with
        class DC1CN/DC2CN. The full chapter HTML is in this shadow DOM,
        paginated via CSS columns with overflow:hidden.
        """
        try:
            text = target.evaluate("""
                (function() {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        if (!all[i].shadowRoot) continue;
                        var sr = all[i].shadowRoot;
                        // Get all text-bearing elements from the shadow DOM
                        var els = sr.querySelectorAll(
                            'p, h1, h2, h3, h4, h5, h6, div.cover'
                        );
                        if (els.length === 0) continue;
                        var texts = [];
                        for (var j = 0; j < els.length; j++) {
                            var el = els[j];
                            // Skip cover image div
                            if (el.classList.contains('cover')) continue;
                            var t = (el.innerText || el.textContent || '')
                                    .trim();
                            // Skip empty and non-breaking-space-only
                            if (t && t !== '\\u00a0' && t.length > 0) {
                                texts.push(t);
                            }
                        }
                        if (texts.length > 0) return texts.join('\\n');
                    }
                    return '';
                })()
            """)
            return text if text else ''
        except Exception:
            return ''


    # ------------------------------------------------------------------
    # Yeduji (夜读集) native scraper
    # ------------------------------------------------------------------
    _YEDUJI_UA = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )

    def _yeduji_fetch(self, url):
        """Fetch a Yeduji page via urllib and return the HTML."""
        req = urllib.request.Request(url, headers={
            'User-Agent': self._YEDUJI_UA,
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode('utf-8', errors='replace')

    def _yeduji_parse_book(self, url):
        """Scrape Yeduji book metadata and chapter list.

        Fetches the book index page and the chapter list page.
        Returns the standard book data dict or None on error.
        """
        self._stop_requested = False
        self.log(f"[Yeduji] Navigating to: {url}")

        # Extract book ID from URL
        m = re.search(r'/book/(\d+)', url)
        if not m:
            self.log("ERROR: [Yeduji] Could not extract book ID from URL.")
            return None
        book_id = m.group(1)

        # Normalise URL
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        index_url = f"{origin}/book/{book_id}/"

        try:
            index_html = self._yeduji_fetch(index_url)
        except Exception as e:
            self.log(f"ERROR: [Yeduji] Index page fetch failed: {e}")
            return None

        # --- Metadata ---
        # Title from <h1>
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', index_html, re.S)
        title = html.unescape(title_match.group(1).strip()) if title_match else ''

        # Extract structured fields from <dl><dt>Key</dt><dd>Value</dd></dl>
        # in the <div class="info"> block.
        info_match = re.search(
            r'<div\s+class=["\']info["\'][^>]*>(.*?)</div>',
            index_html, re.S,
        )
        info_html = info_match.group(1) if info_match else ''

        def _dl_text(label):
            """Extract plain text from <dl><dt>label</dt><dd>text</dd></dl>."""
            m = re.search(
                r'<dt>\s*' + re.escape(label)
                + r'\s*</dt>\s*<dd>(.*?)</dd>',
                info_html, re.S,
            )
            if not m:
                return ''
            return html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()

        def _dl_links(label):
            """Extract link texts from <dl><dt>label</dt><dd><a>...</a>...</dd>."""
            m = re.search(
                r'<dt>\s*' + re.escape(label)
                + r'\s*</dt>\s*<dd>(.*?)</dd>',
                info_html, re.S,
            )
            if not m:
                return []
            return [
                html.unescape(t.strip())
                for t in re.findall(r'<a[^>]*>(.*?)</a>', m.group(1), re.S)
                if t.strip()
            ]

        # Author — prefer structured <dl> field, fall back to <title>
        author = _dl_text('作者')
        if not author:
            title_tag = re.search(r'<title>(.*?)</title>', index_html, re.S)
            if title_tag:
                author_m = re.search(r'-\s*(.+?)\s*著\s*-', title_tag.group(1))
                if author_m:
                    author = author_m.group(1).strip()
        if not title:
            title_tag = re.search(r'<title>(.*?)</title>', index_html, re.S)
            if title_tag:
                parts = title_tag.group(1).split(' - ')
                title = parts[0].strip() if parts else ''

        # Tags
        tags = _dl_links('标签')
        # Category
        category = _dl_links('分类')
        # Status
        status = _dl_text('状态')

        # Cover image
        cover_url = ''
        cover_match = re.search(
            r'<img[^>]+src=["\']([^"\'/][^"\']*?/data/cover/[^"\']*)["\'\s]',
            index_html, re.I,
        )
        if not cover_match:
            cover_match = re.search(
                r'<img[^>]+src=["\']([^"\']*?/data/cover/[^"\']*)["\'\s]',
                index_html, re.I,
            )
        if cover_match:
            cover_url = urllib.parse.urljoin(index_url, cover_match.group(1))

        # Description (synopsis text)
        description = ''
        desc_html = ''
        desc_match = re.search(
            r'<div\s+class=["\']desc["\'][^>]*>(.*?)</div>',
            index_html, re.S,
        )
        if desc_match:
            raw_desc = desc_match.group(1)
            # Strip the "desc-more" image and the "desc-content" wrapper
            inner = re.sub(r'<img[^>]*>', '', raw_desc)
            inner_m = re.search(
                r'<p[^>]*class=["\'][^"\']*desc-content[^"\']*["\'][^>]*>'
                r'(.*?)</p>',
                inner, re.S,
            )
            if inner_m:
                inner = inner_m.group(1)
            desc_text = html.unescape(
                re.sub(r'<[^>]+>', '', inner)
            ).strip()
            description = desc_text
            # Preserve <br> as HTML for the EPUB intro page
            desc_html = re.sub(
                r'<(?!br\s*/?)[^>]+>', '', inner
            ).strip()

        self.log(f"[Yeduji] Title: {title}")
        self.log(f"[Yeduji] Author: {author or 'Unknown'}")
        if tags:
            self.log(
                f"[Yeduji] Tags: {', '.join(tags[:8])}"
                + ('...' if len(tags) > 8 else '')
            )
        if category:
            self.log(f"[Yeduji] Category: {', '.join(category)}")

        # --- Chapter list (from /book/{id}/list/) ---
        list_url = f"{origin}/book/{book_id}/list/"
        try:
            list_html = self._yeduji_fetch(list_url)
        except Exception as e:
            self.log(f"ERROR: [Yeduji] Chapter list fetch failed: {e}")
            return None

        chapters = []
        # Pattern: <a data-chapterId="..." href="/book/ID/CHAP.html">
        #            <h4>ChapterName</h4>
        #            <small class="text-muted">VIP|免费</small>
        #          </a>
        for m in re.finditer(
            r'<a\s+data-chapterId=["\']([^"\']*)["\'\s][^>]*'
            r'href=["\']([^"\']*?/book/\d+/\d+\.html)["\'\s][^>]*>'
            r'(.*?)</a>',
            list_html, re.S,
        ):
            chapter_id = m.group(1)
            href = m.group(2)
            inner = m.group(3)

            # Chapter name from <h4>
            name_match = re.search(r'<h4[^>]*>(.*?)</h4>', inner, re.S)
            name = html.unescape(
                re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
            ) if name_match else f'Chapter {chapter_id}'

            # VIP status from <small>VIP</small>
            is_vip = bool(re.search(
                r'<small[^>]*>\s*VIP\s*</small>', inner, re.I
            ))

            full_url = urllib.parse.urljoin(list_url, href)
            chapters.append({
                'url': full_url,
                'name': name,
                'fullName': name,
                'isVIP': is_vip,
                'isPaid': is_vip,
                'isAccessible': not is_vip,
                '_chapterId': chapter_id,
            })

        if not chapters:
            self.log("ERROR: [Yeduji] No chapters found on list page.")
            return None

        free_count = sum(1 for ch in chapters if not ch['isVIP'])
        vip_count = sum(1 for ch in chapters if ch['isVIP'])
        self.log(
            f"[Yeduji] Found {len(chapters)} chapters "
            f"({free_count} free, {vip_count} VIP)"
        )

        data = {
            'bookname': title or f'Book {book_id}',
            'author': author or 'Unknown',
            'coverUrl': cover_url,
            'description': description,
            'introduction': description,
            'introductionHTML': desc_html.replace('\n', '<br/>\n')
                                if desc_html else '',
            'tags': tags,
            'category': category,
            'status': status,
            'chapterCount': len(chapters),
            'chapters': chapters,
            '_yeduji': True,
            '_yeduji_origin': origin,
            '_yeduji_book_id': book_id,
        }
        self._book_data = data
        self._book_url = index_url
        return data

    def _yeduji_parse_chapter(self, chapter_url, chapter_name,
                              is_paid=False):
        """Fetch one Yeduji chapter's content.

        For VIP chapters, only the preview paragraphs are available.
        The VIP notice text is stripped from the output.

        Returns the standard chapter data dict or None on error.
        """
        try:
            ch_html = self._yeduji_fetch(chapter_url)
        except Exception as e:
            self.log(f"  [Yeduji] Chapter fetch error: {e}")
            return None

        # Title from <h1 class="title">
        title_match = re.search(
            r'<h1[^>]*class=["\']title["\'][^>]*>(.*?)</h1>',
            ch_html, re.S,
        )
        if not title_match:
            title_match = re.search(r'<h1[^>]*>(.*?)</h1>', ch_html, re.S)
        ch_title = html.unescape(
            title_match.group(1).strip()
        ) if title_match else chapter_name

        # Content from <div class="content">...</div>
        content_match = re.search(
            r'<div\s+class=["\']content["\'][^>]*>(.*?)</div>',
            ch_html, re.S,
        )
        if not content_match:
            self.log(
                f"  [Yeduji] No content div found for: {chapter_name}"
            )
            return None

        content_html_raw = content_match.group(1)

        # Detect if this is a VIP preview by looking for the notice.
        # The VIP notice is a <p> containing "以下内容为VIP专属".
        has_vip_notice = bool(re.search(
            r'以下内容为VIP专属', content_html_raw
        ))
        # Also detect the site promo line
        has_promo = bool(re.search(
            r'夜读集由爱发电', content_html_raw
        ))

        # Extract paragraphs, stripping VIP notice and promo lines
        paragraphs = []
        for p_match in re.finditer(
            r'<p[^>]*>(.*?)</p>', content_html_raw, re.S
        ):
            p_text = p_match.group(1).strip()
            plain = html.unescape(re.sub(r'<[^>]+>', '', p_text)).strip()
            # Skip VIP notice and promo lines
            if '以下内容为VIP专属' in plain:
                continue
            if '夜读集由爱发电' in plain:
                continue
            if '请先' in plain and '登录' in plain and '后查看完整内容' in plain:
                continue
            if plain:
                paragraphs.append((plain, p_text))

        if not paragraphs:
            self.log(
                f"  [Yeduji] No text content extracted: {chapter_name}"
            )
            return None

        # Log VIP preview warning
        if has_vip_notice or is_paid:
            self.log(
                f"  [Yeduji] ⚠ PREVIEW ONLY (VIP content): {chapter_name} "
                f"({len(paragraphs)} paragraphs)"
            )

        # Build output
        full_text = '\n'.join(p[0] for p in paragraphs)
        html_parts = []
        for plain, raw_html in paragraphs:
            # Use the original HTML fragment (preserving any inline formatting)
            html_parts.append(f'<p>{raw_html}</p>')
        content_html = '\n'.join(html_parts)

        result = {
            'chapterName': ch_title or chapter_name,
            'sourceChapterName': chapter_name,
            'contentText': full_text,
            'contentHtml': content_html,
            'images': [],
        }
        if has_vip_notice or is_paid:
            result['_preview'] = True
        return result


    def parse_book(self, url):
        """Navigate to the book URL and extract metadata + chapter list.

        Returns the parsed book dict or None on error.
        """
        # KakaoPage: use native scraper instead of JS rules
        if self.is_kakaopage(url):
            self.log("[KakaoPage] Detected KakaoPage URL, using native scraper.")
            return self._kakao_parse_book(url)
        if self.is_ntk_novel(url):
            return self._ntk_parse_book(url)
        if self.is_yeduji(url):
            self.log("[Yeduji] Detected Yeduji URL, using native scraper.")
            return self._yeduji_parse_book(url)

        if not self._gm_stubs_js or not self._rules_js or not self._bridge_js:
            self.log(
                "ERROR: gm_stubs.js, rules-lib.js, or bridge.js not found. "
                "Build the novel-downloader bundle first."
            )
            return None

        if not self._page:
            self.start()

        self._stop_requested = False
        self.log(f"Navigating to: {url}")

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            self.log(f"ERROR: Page load failed: {e}")
            return None

        self.log("Page loaded. Injecting stubs and rules...")

        # Inject GM API stubs first (required by the rules bundle)
        try:
            self._page.evaluate(self._gm_stubs_js)
        except Exception as e:
            self.log(f"ERROR: GM stubs injection failed: {e}")
            return None

        # Inject the rules library
        try:
            self._page.evaluate(self._rules_js)
        except Exception as e:
            self.log(f"ERROR: Rules injection failed: {e}")
            return None

        # Inject the bridge
        try:
            self._page.evaluate(self._bridge_js)
        except Exception as e:
            self.log(f"ERROR: Bridge injection failed: {e}")
            return None

        # Check bridge is ready
        ready = self._page.evaluate("window.__ND_BRIDGE_READY === true")
        if not ready:
            self.log("ERROR: Bridge not ready after injection.")
            return None

        self.log("Rules injected. Parsing book...")

        # Call bookParse (async, returns promise)
        try:
            result_json = self._page.evaluate(
                "window.__ND_parseBook()"
            )
        except Exception as e:
            self.log(f"ERROR: bookParse failed: {e}")
            return None

        if not result_json:
            self.log("ERROR: bookParse returned empty result.")
            return None

        try:
            data = json.loads(result_json)
        except (json.JSONDecodeError, TypeError) as e:
            self.log(f"ERROR: Failed to parse bookParse result: {e}")
            return None

        if "error" in data:
            self.log(f"ERROR: bookParse error: {data['error']}")
            if "stack" in data:
                self.log(f"  Stack: {data['stack'][:200]}")
            return None

        self._book_data = data
        self._book_url = url
        self.log(
            f"Book: {data.get('bookname', '?')} by {data.get('author', '?')} "
            f"— {data.get('chapterCount', 0)} chapters"
        )
        return data

    def parse_chapter(self, index, chapter_info, interval=0.5, page=None):
        """Parse a single chapter's content.

        Args:
            index: Chapter index (0-based).
            chapter_info: Dict with 'url', 'name', 'isVIP', 'isPaid'.
            interval: Delay in seconds after fetching (rate limiting).
            page: Specific Playwright page to use (for parallel downloads).
                  Defaults to the primary page.

        Returns the parsed chapter dict or None on error.
        """
        if self._stop_requested:
            return None

        # KakaoPage: use native viewer scraping
        if self._book_data and self._book_data.get('_kakaopage'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('name', '')
            full_name = chapter_info.get('fullName', '') or name
            target_page = page or self._page
            result = self._kakao_parse_chapter(url, full_name,
                                               page=target_page)
            if interval > 0:
                time.sleep(interval)
            return result
        if self._book_data and self._book_data.get('_ntk_novel'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('fullName', '') or chapter_info.get('name', '')
            target_page = page or self._page
            result = self._ntk_parse_chapter(url, name, page=target_page)
            if interval > 0:
                time.sleep(min(interval, 0.15))
            return result
        if self._book_data and self._book_data.get('_yeduji'):
            url = chapter_info.get('url', '')
            name = chapter_info.get('fullName', '') or chapter_info.get('name', '')
            is_paid = chapter_info.get('isPaid', False)
            result = self._yeduji_parse_chapter(url, name, is_paid=is_paid)
            if interval > 0:
                time.sleep(interval)
            return result

        target_page = page or self._page
        if target_page is None:
            # Primary page lost — try to recover
            if page is None and self._ensure_page():
                target_page = self._page
            else:
                self.log(f"  [{index + 1}] No browser page available.")
                return None

        url = chapter_info.get('url', '')
        name = chapter_info.get('name', '')
        is_vip = chapter_info.get('isVIP', False)
        is_paid = chapter_info.get('isPaid', False)

        # Escape strings for JS (handle quotes and backslashes)
        def js_escape(s):
            return (s or '').replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '')

        script = (
            f"window.__ND_parseChapter("
            f"'{js_escape(url)}', '{js_escape(name)}', "
            f"{'true' if is_vip else 'false'}, "
            f"{'true' if is_paid else 'false'}"
            f")"
        )

        try:
            result_json = target_page.evaluate(script)
        except Exception as e:
            self.log(f"  [{index + 1}] Parse error: {e}")
            return None

        if interval > 0:
            time.sleep(interval)

        if not result_json:
            self.log(f"  [{index + 1}] Empty result for: {name}")
            return None

        try:
            data = json.loads(result_json)
        except (json.JSONDecodeError, TypeError) as e:
            self.log(f"  [{index + 1}] JSON parse error for {name}: {e}")
            return None

        if "error" in data:
            self.log(f"  [{index + 1}] Error: {data['error']}")
            return None

        return data

    def parse_chapter_batch(self, batch_info, interval=0.5):
        """Parse multiple chapters concurrently via JS Promise.all.

        Args:
            batch_info: List of dicts with 'url', 'name', 'isVIP', 'isPaid'.
            interval: Delay between chapters (seconds). Used by KakaoPage
                      sequential fallback; normal batch uses JS Promise.all.

        Returns list of parsed chapter dicts (or None for failures).
        The browser fires all HTTP requests in parallel.
        """
        if self._stop_requested or not batch_info:
            return [None] * len(batch_info)

        # KakaoPage: no batch API — fall back to sequential downloads
        if self._book_data and self._book_data.get('_kakaopage'):
            results = []
            for i, ch in enumerate(batch_info):
                if self._stop_requested:
                    results.append(None)
                    continue
                data = self._kakao_parse_chapter(
                    ch.get('url', ''),
                    ch.get('fullName', '') or ch.get('name', ''),
                    page=self._page
                )
                results.append(data)
                if interval > 0 and i < len(batch_info) - 1:
                    time.sleep(interval)
            return results
        # Yeduji: sequential urllib fetches (no browser needed)
        if self._book_data and self._book_data.get('_yeduji'):
            results = []
            for i, ch in enumerate(batch_info):
                if self._stop_requested:
                    results.append(None)
                    continue
                data = self._yeduji_parse_chapter(
                    ch.get('url', ''),
                    ch.get('fullName', '') or ch.get('name', ''),
                    is_paid=ch.get('isPaid', False),
                )
                results.append(data)
                if interval > 0 and i < len(batch_info) - 1:
                    time.sleep(interval)
            return results
        if self._book_data and self._book_data.get('_ntk_novel'):
            from concurrent.futures import ThreadPoolExecutor

            def fetch_one(ch):
                if self._stop_requested:
                    return None
                state = self._ntk_clone_api_state() or self._ntk_api_state
                if self._book_data.get('_ntk_kind') == 'webtoon':
                    return self._ntk_fetch_webtoon_chapter(
                        ch.get('url', ''),
                        ch.get('fullName', '') or ch.get('name', ''),
                        state=state,
                    )
                return self._ntk_fetch_chapter_api(
                    ch.get('url', ''),
                    ch.get('fullName', '') or ch.get('name', ''),
                    state=state,
                )

            max_workers = max(1, min(5, len(batch_info)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                return list(executor.map(fetch_one, batch_info))

        # Ensure the browser page is still alive
        if not self._ensure_page():
            self.log("  Batch aborted: no browser page available.")
            return [None] * len(batch_info)

        # Build JSON payload for the JS batch function
        payload = json.dumps(batch_info, ensure_ascii=False)

        try:
            result_json = self._page.evaluate(
                f"window.__ND_parseChapterBatch('{self._js_escape(payload)}')"
            )
        except Exception as e:
            self.log(f"  Batch parse error: {e}")
            return [None] * len(batch_info)

        if not result_json:
            return [None] * len(batch_info)

        try:
            # result_json is a JSON string containing an array of JSON strings
            raw_results = json.loads(result_json)
        except (json.JSONDecodeError, TypeError) as e:
            self.log(f"  Batch JSON error: {e}")
            return [None] * len(batch_info)

        # Parse each individual result
        parsed = []
        for r in raw_results:
            if not r:
                parsed.append(None)
                continue
            try:
                data = json.loads(r) if isinstance(r, str) else r
                if "error" in data:
                    self.log(f"  Chapter error: {data['error']}")
                    parsed.append(None)
                else:
                    parsed.append(data)
            except (json.JSONDecodeError, TypeError):
                parsed.append(None)

        return parsed

    @staticmethod
    def _js_escape(s):
        """Escape a string for embedding in a JS single-quoted string."""
        return (s or '').replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '')

    def parse_all_chapters(self, chapters, interval=0.5,
                           start_idx=0, end_idx=None,
                           progress_callback=None):
        """Parse all chapters sequentially.

        Args:
            chapters: List of chapter info dicts from parse_book().
            interval: Delay between chapter fetches (seconds).
            start_idx: First chapter index (0-based).
            end_idx: Last chapter index (exclusive). None = all.
            progress_callback: Optional fn(current, total) for progress updates.

        Returns list of parsed chapter dicts (None entries for failures).
        """
        if end_idx is None:
            end_idx = len(chapters)

        selected = chapters[start_idx:end_idx]
        total = len(selected)
        results = []

        self.log(f"Downloading {total} chapters (interval: {interval}s)...")

        for i, ch in enumerate(selected):
            if self._stop_requested:
                self.log("Download stopped by user.")
                break

            name = ch.get('name', f'Chapter {start_idx + i + 1}')
            self.log(f"  [{i + 1}/{total}] {name}")

            data = self.parse_chapter(start_idx + i, ch, interval=interval)
            results.append(data)

            if progress_callback:
                progress_callback(i + 1, total)

        return results
