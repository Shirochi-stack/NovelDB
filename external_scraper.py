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
import os
import sys
import time
from pathlib import Path

# When running from a PyInstaller bundle, point Playwright to the
# bundled Chromium browser so users don't need to install anything.
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _bundled_browsers = os.path.join(_base, 'ms-playwright')
    if os.path.exists(_bundled_browsers):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _bundled_browsers

from playwright.sync_api import sync_playwright, Page, Browser


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
        self._context = None      # BrowserContext (persistent)
        self._page = None
        self._worker_pages = []   # Additional pages for parallel downloads
        self._book_data = None
        self._book_url = None     # Stored for initialising worker pages

    @staticmethod
    def _get_user_data_dir():
        """Get persistent browser data directory for cookies/localStorage."""
        data_dir = os.path.join(_get_base_dir(), 'browser_data')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    def log(self, msg):
        """Safe logging that handles encoding issues on Windows consoles."""
        try:
            self._raw_log(msg)
        except UnicodeEncodeError:
            self._raw_log(msg.encode('ascii', 'replace').decode())

    def start(self):
        """Launch headless browser with persistent context."""
        if self._context:
            return

        self.log("Launching headless browser...")
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            self._get_user_data_dir(),
            headless=True,
            args=[
                '--disable-web-security',       # Allow cross-origin fetches
                '--disable-features=IsolateOrigins,site-per-process',
                '--allow-running-insecure-content',  # Allow HTTP images on HTTPS pages
                '--no-sandbox',
            ],
            ignore_https_errors=True,
        )
        self._page = self._context.new_page()
        # Suppress console noise but capture errors
        self._page.on("console", self._on_console)
        self.log("Browser ready.")

    def open_visible_browser(self, start_url="about:blank"):
        """Open a visible browser for manual login.

        Cookies and localStorage are saved to the persistent data dir.
        The browser blocks until the user closes it.
        """
        # Must close any existing context first (only one per data dir)
        self.cleanup()

        self.log("Opening browser for login...")
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            self._get_user_data_dir(),
            headless=False,
            args=[
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--allow-running-insecure-content',
                '--no-sandbox',
            ],
            ignore_https_errors=True,
        )
        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        page.goto(start_url)
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
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
        except Exception:
            pass

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

    def parse_book(self, url):
        """Navigate to the book URL and extract metadata + chapter list.

        Returns the parsed book dict or None on error.
        """
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

    def parse_chapter_batch(self, batch_info):
        """Parse multiple chapters concurrently via JS Promise.all.

        Args:
            batch_info: List of dicts with 'url', 'name', 'isVIP', 'isPaid'.

        Returns list of parsed chapter dicts (or None for failures).
        The browser fires all HTTP requests in parallel.
        """
        if self._stop_requested or not batch_info:
            return [None] * len(batch_info)

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
