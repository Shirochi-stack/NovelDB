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
import re
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

    # ------------------------------------------------------------------
    # KakaoPage native scraper (fallback for unsupported JS rules)
    # ------------------------------------------------------------------
    @staticmethod
    def is_kakaopage(url):
        """Check if the URL is a KakaoPage content URL."""
        return bool(url and re.match(
            r'https?://page\.kakao\.com/content/\d+', url
        ))

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

        # --- Expand the episode list ---
        # The page initially shows ~5 episodes. We need to click the
        # expand chevron and scroll to load all episodes.
        self.log("[KakaoPage] Loading episode list...")
        try:
            self._kakao_load_all_episodes(total_episodes)
        except Exception as e:
            self.log(f"WARNING: Episode list expansion failed: {e}")

        # --- Extract episode links ---
        try:
            episodes = self._page.evaluate("""
                (function() {
                    var links = document.querySelectorAll('a[href*="/viewer/"]');
                    var result = [];
                    var seen = {};
                    for (var i = 0; i < links.length; i++) {
                        var a = links[i];
                        var href = a.getAttribute('href') || '';
                        if (seen[href]) continue;
                        seen[href] = true;
                        var text = a.innerText.trim();
                        // Parse episode name and free/paid status
                        var lines = text.split('\\n').filter(function(l) {
                            return l.trim().length > 0;
                        });
                        var name = lines[0] || ('Episode ' + (result.length + 1));
                        // Check for free marker (무료) or paid marker (coin icon)
                        var isFree = text.includes('무료');
                        result.push({
                            url: href,
                            name: name,
                            isVIP: !isFree,
                            isPaid: null
                        });
                    }
                    return result;
                })()
            """)
        except Exception as e:
            self.log(f"ERROR: Episode extraction failed: {e}")
            return None

        if not episodes:
            self.log("WARNING: No episodes found on page.")

        # Fix relative URLs to absolute
        for ep in episodes:
            if ep['url'] and not ep['url'].startswith('http'):
                ep['url'] = 'https://page.kakao.com' + ep['url']

        self.log(f"[KakaoPage] Found {len(episodes)} episodes.")

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
            'tags': [],
            '_kakaopage': True,  # Flag for chapter parser
        }

        self._book_data = data
        self._book_url = url
        return data

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

        Uses two extraction strategies:
          1. Intercept the JSON API responses (sdownload/resource) that carry
             the structured paragraphList with the actual text.
          2. Fallback: read the Shadow DOM innerHTML after the EPUB viewer
             renders the content.

        Returns the standard chapter data dict or None on error.
        """
        target = page or self._page
        if target is None:
            return None

        # Collect JSON content responses during page load
        json_chunks = []

        def _capture_response(response):
            try:
                url = response.url
                ct = response.headers.get('content-type', '')
                if ('sdownload/resource' in url and 'json' in ct):
                    body = response.body()
                    if body:
                        json_chunks.append(body)
            except Exception:
                pass

        target.on('response', _capture_response)

        try:
            target.goto(chapter_url, wait_until="networkidle",
                        timeout=45000)
            target.wait_for_timeout(3000)
        except Exception as e:
            self.log(f"  [KakaoPage] Page load error: {e}")
            target.remove_listener('response', _capture_response)
            return None

        # Click right a few times to trigger lazy-loaded content chunks
        for _ in range(5):
            if self._stop_requested:
                target.remove_listener('response', _capture_response)
                return None
            try:
                target.evaluate("""(function(){
                    var vw = window.innerWidth;
                    var vh = window.innerHeight;
                    var el = document.elementFromPoint(vw*0.85, vh*0.5);
                    if(el) el.click();
                })()""")
                target.wait_for_timeout(1500)
            except Exception:
                break

        target.remove_listener('response', _capture_response)

        # ---- Strategy 1: Parse intercepted JSON paragraphs ----
        paragraphs = self._kakao_extract_from_json(json_chunks)
        if paragraphs:
            full_text = '\n'.join(paragraphs)
            html_parts = [f'<p>{p}</p>' for p in paragraphs if p.strip()]
            content_html = '\n'.join(html_parts)
            return {
                'chapterName': chapter_name,
                'contentText': full_text,
                'contentHtml': content_html,
                'images': [],
            }

        # ---- Strategy 2: Extract from Shadow DOM ----
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

    def _kakao_extract_from_json(self, json_chunks):
        """Parse paragraphList from intercepted JSON API responses.

        Each JSON chunk has structure:
          { contentInfo: {
              paragraphList: [{
                id, type, text,
                childParagraphList: [{id, type, text, ...}]
              }, ...]
          } }
        The actual text is in childParagraphList[].text (the parent's
        text field is empty).
        Returns a list of text paragraphs sorted by content order.
        """
        import json as _json

        all_paras = []
        for raw in json_chunks:
            try:
                data = _json.loads(raw)
                info = data.get('contentInfo', {})
                content_id = info.get('contentId', 0)
                para_list = info.get('paragraphList', [])
                for p in para_list:
                    p_id = int(p.get('id', 0))
                    p_type = p.get('type', '')
                    # Text can be on the paragraph itself or in children
                    text = p.get('text', '').strip()
                    if not text:
                        children = p.get('childParagraphList') or []
                        parts = []
                        for child in children:
                            ct = child.get('text', '').strip()
                            if ct:
                                parts.append(ct)
                        text = ''.join(parts)
                    if text:
                        all_paras.append((content_id, p_id, text, p_type))
            except Exception:
                continue

        if not all_paras:
            return []

        # Sort by (contentId, paragraphId) to maintain correct order
        all_paras.sort(key=lambda x: (x[0], x[1]))
        return [p[2] for p in all_paras]

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



    def parse_book(self, url):
        """Navigate to the book URL and extract metadata + chapter list.

        Returns the parsed book dict or None on error.
        """
        # KakaoPage: use native scraper instead of JS rules
        if self.is_kakaopage(url):
            self.log("[KakaoPage] Detected KakaoPage URL, using native scraper.")
            return self._kakao_parse_book(url)

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
            target_page = page or self._page
            result = self._kakao_parse_chapter(url, name, page=target_page)
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
                    ch.get('url', ''), ch.get('name', ''),
                    page=self._page
                )
                results.append(data)
                if interval > 0 and i < len(batch_info) - 1:
                    time.sleep(interval)
            return results

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
