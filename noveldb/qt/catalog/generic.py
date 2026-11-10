"""The Catalog's generic search page (desktop): the app side of noveldb/qt/catalog/generic/generic.js.

The page is a local file shown in the Catalog view; its script runs in the bridge's isolated world
with the LNReader bundle. The searching itself is `noveldb.core.catalog.generic_search`, run on
worker threads; cards go back to the page as they arrive. Requests are plain HTTP first; a page that
refuses them (a site's bot check) or builds its content with scripts is loaded in a hidden tab of the
Catalog's own browser profile instead, the way the Catalog shows any site (`BrowserFetcher`).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage

from noveldb.core import paths
from noveldb.core.catalog.generic_search import (USER_AGENT, GenericSearch, default_fetch, load_learned, save_learned,
                                                 top_sites)
from noveldb.core.catalog.live_reader import LiveReader
from noveldb.core.catalog.logins import SavedLogins
from noveldb.core.catalog.noveldb_index import NovelDBIndex
from noveldb.core.catalog.offline_cache import OfflineCache
from noveldb.core.catalog.site_search import SiteSearch
from noveldb.qt.catalog.reader_media import ReaderSession

log = logging.getLogger(__name__)
PAGE_DIR = Path(__file__).with_name("generic")
PAGE = PAGE_DIR / "index.html"
SCRIPT = PAGE_DIR / "generic.js"
REFERENCE_SCRIPT = PAGE_DIR / "references.js"
INSTANT_SCRIPT = PAGE_DIR / "instant.js"
SORTS = ("likes", "views", "chapters", "new", "updated", "title")  # noveldb_index.INDEX_SORTS
MESSAGE_TYPES = ("genericReady", "genericReaderStop", "genericSearch", "genericStop", "genericFetch", "genericImage", "genericDetails",
                 "genericDownload", "genericBook", "genericChapter", "genericTop", "genericLogin",
                 "genericCover", "genericCoverPut", "genericMore", "genericTopMore", "genericFullscreen", "genericNdbTop",
                 "genericNdbTags", "genericReaderClosed", "genericReaderOpened", "genericLocalPlace", "genericLibraryLive", "genericIndexSort", "genericSiteQuery", "genericInstant")
MAX_QUERIES = 6
# A country's domain and its language (the page's preferred-language setting; others count as English).
TLD_LANG = {"jp": "ja", "kr": "ko", "cn": "zh", "tw": "zh", "hk": "zh", "ru": "ru", "es": "es", "mx": "es", "br": "pt",
            "pt": "pt", "fr": "fr", "id": "id", "th": "th", "tr": "tr", "de": "de", "vn": "vi"}
COMIC_SITE = __import__("re").compile(r"(manga|manhwa|manhua|toon|comic|piccoma|mechacomic|booklive|toomics|scans?\b)", 2)
MANGA_SITE = __import__("re").compile(r"(manga(?!.*hwa)|fanfox|comick|jumpplus|shonenjump|mechacomic|piccoma|booklive|komiku|raw)", 2)
MANHWA_SITE = __import__("re").compile(r"(toon|manhwa|scans?\b|asura|reaper|flame|demonic|kingofshojo|mgeko|toomics)", 2)
BLOCKED = ("This page is blocked: the site may need you to sign in, or to buy the book. Sign in with Enter "
           "Browser (your login is saved), then try again.")
_reading = threading.local()  # the reader request a worker thread is answering (for its progress notes)
_CHALLENGE = ("just a moment", "attention required", "security check", "verify you are human")
_WAITING = ("verifying access", "checking your browser", "performing security verification",
            "enable javascript and cookies")  # text shown while a browser check is still running


def download_icon() -> str:
    """The app's download artwork as a data URL (the one catalog-bridge.js shows on NovelDB)."""
    import re

    try:
        text = (Path(__file__).with_name("catalog-bridge.js")).read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r"DOWNLOAD_ICON = '(data:image/[^']+)'", text)
    return found.group(1) if found else ""


def _short(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return (parts.hostname or "").removeprefix("www.") + (parts.path[:40] if len(parts.path) > 1 else "")


def is_generic_page(url: QUrl) -> bool:
    return url.isLocalFile() and Path(url.toLocalFile()).resolve() == PAGE.resolve()


# The page's Google Translate requests (the desktop's page can't ask Google itself: it is a local file).
# Google answers the `requests` library 429 "Sorry..." and the same request from urllib normally.
TRANSLATE_HOST = "translate.googleapis.com"


def _plain_request(url: str, method: str, body, headers: dict) -> Optional[dict]:
    """A request through urllib, in the fetch's result shape (the HTTP status of an error too)."""
    import urllib.error
    import urllib.request

    data = body.encode("utf-8") if isinstance(body, str) else body
    sent = {"User-Agent": USER_AGENT, **headers}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=sent, method=method), timeout=20) as response:
            return {"status": response.status, "url": response.geturl(), "content": response.read(),
                    "headers": "\n".join(f"{k}: {v}" for k, v in response.headers.items())}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "url": url, "content": error.read() or b"", "headers": ""}
    except Exception:
        return None


def _with_page_data(result: dict, image_url=None) -> str:
    """A downloaded chapter's HTML with the images the engine already fetched in place of their addresses
    (HaruNeko's pages: fetched again by the reader, some sites refused them and pages went missing)."""
    html = result.get("contentHtml") or ""
    pictures = [image for image in (result.get("images") or [])
                if isinstance(image, dict) and str(image.get("data") or "").startswith("data:image/")]
    if not pictures:
        return html
    by_url = {str(image.get("url") or ""): image["data"] for image in pictures}
    seen = [0]

    def swap(match):
        # By address, else the image in the same place (the n-th picture for the n-th image).
        position = seen[0]
        seen[0] += 1
        link = match.group(2).replace("&amp;", "&")
        data = by_url.get(link) or (pictures[position]["data"] if position < len(pictures) else None)
        if not data:
            return match.group(0)
        return match.group(1) + (image_url(data) if image_url is not None else data) + match.group(3)

    return re.sub(r'(<img\b[^>]*?\bsrc=")([^"]*)(")', swap, html)


def _file_path(url: str) -> Path:
    """The path of a file: address."""
    from urllib.parse import unquote

    raw = unquote(urlsplit(url).path)
    return Path(raw.lstrip("/") if os.name == "nt" else raw)


class ChapterNotOut(RuntimeError):
    """A chapter the site lists but has not published yet (no login would read it)."""


class ChapterUnavailable(RuntimeError):
    """The chapter engines failed to read the chapter."""


# The page's requests (covers, images, details, the reader's chapters): many at once, some slow (a chapter
# read by the downloader), so the pool is wide; idle threads are reused.
class _Workers:
    """Daemon threads taking jobs from a queue (a standard pool's threads would hold the app open at exit
    while a chapter is still being read); a new one starts only when none is idle, up to `most`."""

    def __init__(self, most: int) -> None:
        import queue

        self.jobs = queue.SimpleQueue()
        self.most = most
        self.lock = threading.Lock()
        self.idle = 0
        self.count = 0

    def submit(self, job) -> None:
        with self.lock:
            start = self.idle <= self.jobs.qsize() and self.count < self.most
            if start:
                self.count += 1
        self.jobs.put(job)
        if start:
            threading.Thread(target=self._work, name="generic-page", daemon=True).start()

    def _work(self) -> None:
        while True:
            with self.lock:
                self.idle += 1
            job = self.jobs.get()
            with self.lock:
                self.idle -= 1
            job()


_PAGE_WORKERS = _Workers(64)
_READER_WORKERS = _Workers(12)
# Google Translate's requests have threads of their own: behind a search's site requests and page renders in
# the page's pool they waited, and the cards translated many seconds later than on the phones.
_TRANSLATE_WORKERS = _Workers(8)

_translate_first = [0]  # the way that answered last: a blocked one (20 s to time out) is not asked first again


def _translate(url: str, method: str, body, headers: dict) -> Optional[dict]:
    """A Google Translate request: urllib to the page's host, then to translate.google.com's same address, then
    the app's plain fetch, starting with the one that answered last; each failure goes to the log (a silent
    failure left the cards untranslated with no clue why)."""
    tries = [lambda: _plain_request(url, method, body, headers),
             lambda: _plain_request(url.replace(TRANSLATE_HOST, "translate.google.com", 1), method, body, headers),
             lambda: default_fetch(url, method=method, data=body, headers=headers, binary=True)]
    first = _translate_first[0]
    result = None
    for index in list(range(first, len(tries))) + list(range(first)):
        try:
            result = tries[index]()
        except Exception as e:
            log.warning("Google Translate request failed: %s", e)
            result = None
        if result is not None and result.get("status") == 200:
            _translate_first[0] = index
            return result
        log.warning("Google Translate answered %s: %s", result and result.get("status"),
                    ((result or {}).get("content") or b"")[:160])
    return result


def parse_message(data: dict) -> Optional[dict]:
    """A generic-page message with the fields it needs, or None."""
    kind = data.get("type")
    if kind not in MESSAGE_TYPES:
        return None
    if kind == "genericMore" and not (isinstance(data.get("page"), int) and data["page"] >= 1):
        return None
    # NovelDB's page of the next results (0: none) and whether the sites' next pages come too.
    if kind == "genericMore" and "ndbPage" in data and not (isinstance(data["ndbPage"], int) and 0 <= data["ndbPage"] <= 10000):
        return None
    if kind == "genericMore" and not isinstance(data.get("sites", True), bool):
        return None
    if kind in ("genericSearch", "genericMore", "genericIndexSort"):
        # A search is one or more queries run together: the title, and each tag picked in the tag menu.
        asked = data.get("queries")
        if not isinstance(asked, list):
            asked = [{"query": data.get("query"), "mode": data.get("mode")}]
        queries = []
        for item in asked[:MAX_QUERIES]:
            query = item.get("query") if isinstance(item, dict) else None
            if isinstance(query, str) and query.strip() and len(query) <= 300:
                checked = {"query": query.strip(),
                           "mode": item.get("mode") if item.get("mode") in ("name", "tag", "id", "author") else "name"}
                if item.get("sort") in SORTS:
                    checked["sort"] = item["sort"]  # the page's sort (noveldb_index.INDEX_SORTS); else the default order
                only = item.get("only")  # the sites picked in the Sites panel: the search asks only them
                if isinstance(only, list):
                    picked = [site.strip().lower() for site in only[:40] if isinstance(site, str) and 0 < len(site.strip()) <= 100]
                    if picked:
                        checked["only"] = picked
                for switch in ("noveldb", "deep"):  # the settings' searches switched off (both on by default)
                    if item.get(switch) is False:
                        checked[switch] = False
                if item.get("full") is True:
                    checked["full"] = True
                queries.append(checked)
        if not queries:
            return None
        data["queries"] = queries
        data["query"], data["mode"] = queries[0]["query"], queries[0]["mode"]
    if kind in ("genericFetch", "genericImage", "genericBook", "genericChapter", "genericLibraryLive", "genericIndexSort", "genericSiteQuery", "genericInstant") and not isinstance(data.get("id"), int):
        return None
    if kind == "genericInstant" and not re.fullmatch(r"manifest|top|[bc]/[0-9]{1,6}", str(data.get("key", ""))):
        return None
    if kind == "genericInstant":
        if "token" in data and (not isinstance(data["token"], str) or len(data["token"]) > 300):
            return None
        if "rows" in data and (not isinstance(data["rows"], list) or len(data["rows"]) > 120
                               or any(type(number) is not int or not 0 <= number < 128 for number in data["rows"])):
            return None
    if kind == "genericSiteQuery":
        host = data.get("host")
        query = data.get("query")
        url = data.get("url")
        if data.get("web") is True:
            if not isinstance(query, str) or not 0 < len(query) <= 2000:
                return None
        elif url:
            if not isinstance(url, str) or len(url) > 2000 or urlsplit(url).scheme not in ("http", "https"):
                return None
        elif (not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host)
              or not isinstance(query, str) or not 0 < len(query) <= 300):
            return None
    if kind == "genericFetch":
        request = data.get("request")
        if not isinstance(request, dict) or not str(request.get("url", "")).startswith(("http://", "https://")):
            return None
    if kind == "genericImage" and not str(data.get("url", "")).startswith(("http://", "https://")):
        return None
    if kind == "genericLogin" and not str(data.get("url", "")).startswith(("http://", "https://")):
        return None
    if kind in ("genericBook", "genericChapter") and not str(data.get("url", "")).startswith(("http://", "https://", "file:")):
        return None
    if kind == "genericLibraryLive" and (not str(data.get("url", "")).startswith("file:")
            or not isinstance(data.get("after", 0), int) or not isinstance(data.get("session", ""), str)
            or ("key" in data and not isinstance(data["key"], str))):
        return None
    if kind == "genericLocalPlace" and not (str(data.get("url", "")).startswith("file:") and isinstance(data.get("chapter"), int)
                                            and isinstance(data.get("at"), (int, float))):
        return None
    if kind == "genericChapter" and not (isinstance(data.get("index"), int) and data["index"] >= 0):
        return None
    if kind == "genericChapter" and data.get("recovery") not in (None, "cloudflare", "images"):
        return None
    if kind in ("genericCover", "genericCoverPut") and not (str(data.get("key", "")) and len(str(data["key"])) < 3000):
        return None
    if kind == "genericCoverPut" and not str(data.get("dataUrl", "")).startswith("data:image/"):
        return None
    if kind == "genericTop" and data.get("kind") not in ("manga", "manhwa", "comic", "lightnovel", "webnovel"):
        return None
    if kind == "genericTop" and "sites" in data and not (
            isinstance(data["sites"], list) and len(data["sites"]) <= 50
            and all(isinstance(site, str) and 0 < len(site) < 200 for site in data["sites"])):
        return None
    if kind == "genericNdbTop" and not (
            isinstance(data.get("sources"), list) and 0 < len(data["sources"]) <= 12
            and all(isinstance(s, str) and re.fullmatch(r"[a-z0-9_]{1,20}", s) for s in data["sources"])
            and isinstance(data.get("page", 0), int) and 0 <= data.get("page", 0) <= 10000):
        return None
    if kind == "genericDownload" and not str(data.get("url", "")).startswith(("http://", "https://")):
        return None
    if kind == "genericDetails":
        card = data.get("card")
        if not isinstance(card, dict) or not str(card.get("url", "")).startswith(("http://", "https://")):
            return None
    return data


def _refused(result: Optional[dict]) -> bool:
    if result is None:
        return True
    if result["status"] in (401, 403, 418, 429, 503):
        return True
    head = (result.get("text") or "")[:3000].lower()
    return any(word in head for word in _CHALLENGE)



def _haruneko_site(url: str) -> bool:
    """HaruNeko's websites list the address's site (the downloader reads those with it)."""
    try:
        from noveldb.core.catalog import sites

        return sites.supported_site(url, ("haruneko",))
    except Exception:
        return False

class BrowserFetcher(QObject):
    """Loads pages in hidden tabs of the Catalog's browser profile, for worker threads.

    `fetch(url)` blocks the calling worker thread (never the GUI thread) until the page has loaded,
    passed the site's own check if it shows one, and run its scripts; it returns the rendered HTML.
    """

    _request = Signal(object)

    def __init__(self, profile, parent: QObject, tabs: int = 4) -> None:
        super().__init__(parent)
        self.profile = profile
        self.tabs = tabs
        self._queue: list = []
        self._active = 0
        self._idle: list = []  # tabs done with their page, kept for the next (one made and destroyed per page
        # stalled the window: Chromium builds and tears a tab down on the GUI thread)
        self._request.connect(self._enqueue)

    def fetch(self, url: str, timeout: float = 45.0, scroll: bool = False) -> Optional[dict]:
        if threading.current_thread() is threading.main_thread():
            return None
        job = {"url": url, "event": threading.Event(), "result": None, "deadline": time.monotonic() + timeout - 5,
               "scroll": scroll}
        self._request.emit(job)
        job["event"].wait(timeout)
        return job["result"]

    @Slot(object)
    def _enqueue(self, job) -> None:
        self._queue.append(job)
        self._next()

    def _next(self) -> None:
        while self._active < self.tabs and self._queue:
            job = self._queue.pop(0)
            if time.monotonic() > job["deadline"]:
                job["event"].set()
                continue
            self._active += 1
            page = self._idle.pop() if self._idle else self._new_tab()
            page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            page.job = job
            job["page"] = page
            QTimer.singleShot(int(max(1.0, job["deadline"] - time.monotonic()) * 1000), lambda job=job: self._finish(job, ""))
            page.load(QUrl(job["url"]))

    def _new_tab(self):
        from noveldb.qt.catalog.web import QuietPage

        page = QuietPage(self.profile, self)
        page.setAudioMuted(True)
        page.job = None
        # The tab's page loaded: checked a moment later (a blank page between jobs has no job).
        page.loadFinished.connect(lambda ok, page=page: page.job is not None
                                  and QTimer.singleShot(1200, lambda job=page.job: self._check(job)))
        return page

    def _check(self, job) -> None:
        page = job.get("page")
        if page is None:
            return
        title = (page.title() or "").lower()
        if any(word in title for word in _CHALLENGE) and time.monotonic() < job["deadline"]:
            QTimer.singleShot(1000, lambda: self._check(job))  # the site's check page reloads itself when done
            return
        # A page that checks the browser in its own text ("Verifying access..." where fenrirealm's chapter
        # goes) fills itself in once that passes.
        page.runJavaScript("(document.body && document.body.innerText || '').slice(0, 20000).toLowerCase()",
                           lambda text, job=job: self._checked(job, text))

    def _checked(self, job, text) -> None:
        page = job.get("page")
        if page is None:
            return
        if any(word in str(text or "") for word in _WAITING) and time.monotonic() < job["deadline"]:
            QTimer.singleShot(1000, lambda: self._check(job))
            return
        if job.get("scroll"):
            from noveldb.core.engines.generic import SCROLL_TO_END

            job["scroll"] = False
            page.runJavaScript(SCROLL_TO_END)
            QTimer.singleShot(700, lambda: self._scrolled(job))
            return
        page.toHtml(lambda html, job=job: self._finish(job, html))

    def _scrolled(self, job) -> None:
        """Waits for the scroll script to reach the end (or the job's time to run out), then takes the page."""
        page = job.get("page")
        if page is None:
            return

        def done(value):
            if value or time.monotonic() > job["deadline"] - 3:
                page.toHtml(lambda html, job=job: self._finish(job, html))
            else:
                QTimer.singleShot(700, lambda: self._scrolled(job))

        page.runJavaScript("!!window.__noveldbScrolled", done)

    def _finish(self, job, html: str) -> None:
        page = job.pop("page", None)
        if page is None:
            return  # already finished (timeout or page)
        if html:
            job["result"] = {"status": 200, "url": page.url().toString() or job["url"], "text": html,
                             "content": html.encode("utf-8"), "headers": "Content-Type: text/html; charset=utf-8"}
        job["event"].set()
        page.job = None
        # Frozen until the next job: the site's scripts stop (no self-made navigation reaching that job).
        page.triggerAction(QWebEnginePage.WebAction.Stop)
        page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
        self._idle.append(page)
        self._active -= 1
        self._next()


class GenericCatalog(QObject):
    """Answers the generic search page; `run_js` runs a script in the page's bridge world."""

    _cards = Signal(int, list)
    _done = Signal(int, dict)
    _detailed = Signal(dict)
    _fetched = Signal(int, object)
    _progress = Signal(int, dict)
    _more_done = Signal(int, dict)
    _ndb_top_done = Signal(int, dict)
    _ndb_tags = Signal(list)
    _reader_status = Signal(int, str)
    _reader_images = Signal(int, dict)
    _reader_chapters = Signal(int, list)

    DETAILS_DAYS = 3
    # The details cache's name: a new one drops details read by older code (link tags, HTML synopses).
    DETAILS = "details-v4"  # v4: none from before the engine left out menu links ("All Tags") and site-named authors

    def __init__(self, run_js: Callable[[str], None], profile, known_site: Callable[[str], bool], parent: QObject,
                 cache_mb: Callable[[], int] = lambda: 300):
        super().__init__(parent)
        self.cache = OfflineCache(cache_mb)
        self.run_js = run_js
        # The app's theme for the page (theme.web_theme); None leaves the page its own colours.
        self.theme: Callable[[], Optional[dict]] = lambda: None
        self.browser = BrowserFetcher(profile, self)
        # The user's logins go with the Catalog's own requests (its profile's, Enter Browser's and the
        # app's); the Catalog page sets `logins.secrets` to the app's.
        self.logins = SavedLogins()
        self._watch_cookies(profile)
        self.known_site = known_site
        self._stop = threading.Event()
        self._search_id = 0
        cache = paths.cache_dir() / "generic-search-sites.json"
        self.site_search = SiteSearch(self.fetch, cache_file=cache)
        self.site_search.render = self.browser.fetch
        self.reader = LiveReader(self.fetch, render=self._render, native_enabled=self._builtin_scrapers_enabled)
        # NovelDB's own 1.1 million books, read from its search index a few pieces at a time.
        self.noveldb = NovelDBIndex(default_fetch, cache_dir=paths.cache_dir())
        self._learned_file = paths.cache_dir() / "generic-id-templates.json"
        load_learned(self._learned_file)
        self._cards.connect(lambda sid, cards: self._call("add", cards, sid))
        self._done.connect(lambda sid, summary: self._call("done", sid, summary))
        self._detailed.connect(lambda card: self._call("details", card))
        self._fetched.connect(lambda fid, result: self._call("fetched", fid, result))
        self._progress.connect(lambda sid, item: self._call("progress", sid, item))
        self._more_done.connect(lambda sid, state: self._call("moreDone", sid, state))
        self._ndb_top_done.connect(lambda sid, state: self._call("ndbTopDone", sid, state))
        self._ndb_tags.connect(lambda names: self._call("ndbTags", names))
        self._top_sites: list = []  # the sites of the Top list shown, for its next pages
        self._reader_status.connect(lambda fid, text: self._call("readerStatus", fid, text))
        self._reader_images.connect(lambda fid, found: self._call("readerImages", fid, found))
        self._reader_chapters.connect(lambda fid, chapters: self._call("readerChapters", fid, chapters))
        self._reader_visible = True
        self.reader_session = ReaderSession()
        # The downloader's own browser worker (`context.browser.submit`), for books the quick engines
        # can't read: it has the saved sessions of Enter Browser. Set by the Catalog page.
        self.downloader: Optional[Callable] = None
        # The Settings' engine order and dynamic priority (EngineSettings), for the downloader's reads: its
        # scraper had the defaults (dynamic priority on), whatever Settings said. Set by the Catalog page.
        self.engine_settings: Callable = lambda: None
        self._downloader_books: dict = {}
        self._local_paths: dict = {}  # a Library book's file: address -> its path as the Library names it

    # -- requests ----------------------------------------------------------------------------------

    def _watch_cookies(self, profile) -> None:
        """Keeps `logins` up to date with the Catalog profile's cookies (those it has and every change)."""
        store = profile.cookieStore() if profile is not None else None
        if store is None:
            return

        def fields(cookie):
            return cookie.domain(), cookie.path() or "/", bytes(cookie.name()).decode("utf-8", "ignore")

        store.cookieAdded.connect(lambda cookie: self.logins.observe(*fields(cookie), bytes(cookie.value()).decode("utf-8", "ignore")))
        store.cookieRemoved.connect(lambda cookie: self.logins.forget(*fields(cookie)))
        store.loadAllCookies()

    def fetch(self, url: str, method: str = "GET", data=None, headers=None, binary: bool = False,
              referer: str = "", quick: bool = False, checkpoint=None, timeout: float = 15.0) -> Optional[dict]:
        """Plain HTTP with the user's logins for the site; a page that refuses it is read in a hidden
        Catalog tab instead (not for `quick` guesses)."""
        self._note(f"Fetching {_short(url)}…")
        if not any(name.lower() == "cookie" for name in (headers or {})):
            try:
                cookie = self.logins.header(url)
            except Exception as e:
                log.debug("logins for %s unreadable: %s", _short(url), e)
                cookie = ""
            if cookie:
                headers = dict(headers or {}, Cookie=cookie)
        work = getattr(_reading, "work", None)
        if work is not None:
            checkpoint = work.wait
        options = {"checkpoint": checkpoint} if checkpoint is not None else {}
        result = default_fetch(url, method=method, data=data, headers=headers, binary=binary, referer=referer, quick=quick, timeout=timeout,
                               **options)
        if method == "GET" and not binary and not quick and _refused(result):
            if checkpoint is not None and not checkpoint():
                return None
            self._note(f"{_short(url)} refused a plain request; loading it in a browser tab…")
            rendered = self.browser.fetch(url)
            if rendered is not None:
                return rendered
        return result

    # -- page messages -----------------------------------------------------------------------------

    def handle(self, data: dict) -> bool:
        data = parse_message(data)
        if data is None:
            return False
        kind = data["type"]
        if kind == "genericLibraryLive":
            if self._in_library(data["url"]):
                self._background(self._live_reply, data)
            else:
                self._fetched.emit(data["id"], {})
            return True
        if kind == "genericReady":
            self.init_page()
        elif kind == "genericSearch":
            self.search(int(data.get("id") or 0), data["queries"])
        elif kind == "genericIndexSort":
            self._background(self._index_sort, data)
        elif kind == "genericSiteQuery":
            self._background(self._site_query, data)
        elif kind == "genericInstant":
            self._background(self._instant_reply, data)
        elif kind == "genericMore":
            self.more(int(data.get("id") or 0), data["queries"], data["page"], data.get("ndbPage"), data.get("sites", True))
        elif kind == "genericTopMore":
            self.top_more(int(data.get("id") or 0))
        elif kind == "genericNdbTags":
            self._background(self.ndb_tags)
        elif kind == "genericNdbTop":
            self._background(self.ndb_top, int(data.get("id") or 0), data["sources"], int(data.get("page") or 0))
        elif kind == "genericStop":
            self._stop.set()
        elif kind == "genericReaderStop":
            self.stop_reader()
        elif kind == "genericLocalPlace" and self._in_library(data["url"]):
            # A Library book's place, kept where the Library keeps it (its "last read" order, the next opening).
            from noveldb.qt.pages.library_reader import _save_place
            key = self._local_paths.get(data["url"]) or str(_file_path(data["url"]))
            _save_place(key, data["chapter"], max(0.0, min(1.0, float(data["at"]))))
        elif kind == "genericFetch":
            self._background(self._transport, data["id"], data["request"],
                             workers=_TRANSLATE_WORKERS if urlsplit(data["request"]["url"]).hostname == TRANSLATE_HOST else None,
                             reader_work=self.reader_session if data.get("reader") is True else None)
        elif kind == "genericImage":
            reader = data.get("reader") is True
            self._background(self._image, data["id"], data["url"], str(data.get("referer") or ""), reader=reader,
                             reader_work=self.reader_session if reader else None)
        elif kind == "genericDetails":
            self._background(self._details, data["card"])
        elif kind == "genericCover":
            self._background(self._cover, data["id"], str(data["key"]))
        elif kind == "genericCoverPut":
            self._background(self._cover_put, str(data["key"]), data["dataUrl"])
        elif kind == "genericTop":
            self.top(int(data.get("id") or 0), data["kind"], str(data.get("language") or "all")[:5], data.get("sites") or [])
        elif kind in ("genericBook", "genericChapter") and data["url"].startswith("file:") \
                and not self._in_library(data["url"]):
            self._fetched.emit(data["id"], {"error": "only books in the Library can be read"})
        elif kind == "genericBook":
            fetch_id = data["id"]
            generation = getattr(self, "_reader_generation", 0)
            work = self.reader_session
            self._background(self._read, fetch_id, self.reader.book, data["url"],
                             on_chapters=lambda chapters: self._reader_chapters.emit(fetch_id, chapters),
                             active=lambda: work.wait() and generation == getattr(self, "_reader_generation", 0),
                             reader_work=work)
        elif kind == "genericChapter":
            self._background(self._read, data["id"], self._chapter, data["url"], data["index"],
                             force_downloader=data.get("recovery") == "cloudflare",
                             retry_images=data.get("recovery") == "images", reader_work=self.reader_session)
        return True

    def init_page(self) -> None:
        from noveldb.core.catalog.search_ranking import profile
        sites = top_sites()
        lnreader = [{"id": plugin, "site": site["site"], "comic": site["comic"]}
                    for site in sites for plugin in site.get("lnreader", [])[:1]]
        self._call("init", {"sites": [site["site"] for site in sites], "comic": [site["site"] for site in sites if site["comic"]],
                            "lnreader": lnreader, "searched": sum(1 for site in sites if site.get("searched", True)),
                            "responseProfile": profile(), "downloadIcon": download_icon()})
        if not self._reader_visible:
            self._call("readerVisibility", False)
        self.send_theme()

    def send_theme(self) -> None:
        """The page (and its reader's Auto look) in the app's theme."""
        colors = self.theme()
        if colors:
            self._call("theme", colors)

    def search(self, search_id: int, queries: list) -> None:
        """Runs every query at once; the page hears `done` once, when the last one finishes."""
        self._stop.set()
        self._stop = stop = threading.Event()
        self._search_id = search_id
        left = [len(queries)]
        totals: dict = {}
        lock = threading.Lock()

        def finished(summary: dict) -> None:
            with lock:
                for name, value in summary.items():
                    totals[name] = (totals.get(name) or 0) + value if isinstance(value, (int, float)) and not                         isinstance(value, bool) else (totals.get(name) or value)
                left[0] -= 1
                last = left[0] == 0
            if last:
                save_learned(self._learned_file)
                self._done.emit(search_id, totals)

        def run(query: str, mode: str, item: dict):
            engine = self._engine(item)
            if mode == "id":
                try:
                    engine.by_id(query, lambda cards: self._cards.emit(search_id, [dict(c, via="id") for c in cards]), stop,
                                 finished, progress=lambda item: self._progress.emit(search_id, item))
                except Exception as e:
                    log.warning("generic ID search failed: %s", e)
                    finished({"failed": 1})
                return
            try:
                # `via` tells the page which kind of query found a card (tag results must match the title too).
                engine.run(query, mode, lambda cards: self._cards.emit(search_id, [dict(card, via=mode) for card in cards]),
                           stop, finished, deadline=120.0 if engine.full else 45.0,
                           progress=lambda item: self._progress.emit(search_id, item))
            except Exception as e:
                log.warning("generic search failed: %s", e)
                finished({"failed": 1})

        for item in queries:
            threading.Thread(target=run, args=(item["query"], item["mode"], item), name="generic-search", daemon=True).start()

    def _engine(self, item: dict) -> GenericSearch:
        """A search's engine with the query's options: the page's sort (NovelDB's lists in that order where the
        index keeps one) and the settings' searches (NovelDB's index; the deep search of its sites)."""
        engine = GenericSearch(fetch=self.fetch, known_site=self.known_site, site_search=self.site_search,
                               noveldb=self.noveldb if item.get("noveldb", True) else None, render=self._render)
        engine.sort = str(item.get("sort") or "")
        engine.deep = item.get("deep", True) is not False
        engine.full = item.get("full", False) is True
        engine.only = set(item.get("only") or [])
        return engine

    def _index_sort(self, data):
        cards, has_more = [], False
        try:
            for item in data["queries"]:
                engine = self._engine(item)
                if engine.noveldb is None or item["mode"] == "id":
                    continue
                found, more = engine.noveldb.search_page(item["query"], item["mode"], 0, 40,
                                                       sources=engine.noveldb_sources(), sort=item.get("sort", ""))
                cards.extend(dict(card, via=item["mode"]) for card in found)
                has_more = has_more or more
            self._fetched.emit(data["id"], {"cards": cards, "more": has_more})
        except Exception:
            self._fetched.emit(data["id"], {"error": "The index could not be sorted"})

    def _instant_reply(self, data):
        from noveldb.core.catalog.instant import read_piece

        try:
            self._fetched.emit(data["id"], {"data": read_piece(data["key"], data.get("token"), data.get("prefix") is True, data.get("rows"))})
        except (OSError, ValueError, KeyError):
            self._fetched.emit(data["id"], {"data": None})

    def _site_query(self, data):
        from noveldb.core.catalog.generic_search import looks_like_book, is_no_results, web_search, GoogleUnavailable
        from noveldb.core.engines.generic import decode, extract_metadata

        cards = []
        try:
            engine = self._engine({})
            if data.get("web"):
                def google_fetch(url, **kwargs):
                    result = self.fetch(url, **kwargs)
                    if result and "/httpservice/retry/enablejs" in decode(result):
                        rendered = self._render(url, timeout=15)
                        if rendered:
                            return {"status": 200, "url": url, "text": rendered, "content": rendered.encode("utf-8")}
                    return result

                try:
                    cards = list(web_search(data["query"], google_fetch))
                except GoogleUnavailable:
                    self._fetched.emit(data["id"], {"cards": [], "googleBlocked": True})
                    return
            elif data.get("url"):
                result = self.fetch(data["url"], quick=True)
                if result and result.get("status") == 200:
                    url = result.get("url") or data["url"]
                    metadata = extract_metadata(decode(result), url)
                    if (looks_like_book(url, metadata.get("title", "")) and metadata.get("title")
                            and not is_no_results(metadata["title"], metadata.get("description", ""))):
                        card = dict(engine.card(url), **metadata, complete=True)
                        if engine.site_for(url) is None:
                            card["comic"] = None
                        cards.append(card)
            else:
                site = engine.site_for("https://" + data["host"] + "/")
                home = "https://" + (site or {}).get("host", data["host"]) + "/"
                for hit in self.site_search.search(home, data["query"])[:20]:
                    if looks_like_book(hit["url"], hit["title"]):
                        card = engine.card(hit["url"], hit["title"], hit.get("snippet", ""), "site", hit.get("cover", ""))
                        if site is None:
                            card["comic"] = None
                        cards.append(card)
            self._fetched.emit(data["id"], {"cards": cards})
        except Exception:
            self._fetched.emit(data["id"], {"cards": []})

    def top(self, search_id: int, kind: str, language: str = "all", only: Optional[list] = None) -> None:
        """The popular lists of the top sites of one kind (comics: the comic sites; novels: the rest), the
        sites of the preferred language first (a country's domain tells a site's language; .com and the
        like count as English). `only`: the sites picked in the page's Sites panel, in their order."""
        self._stop.set()
        self._stop = stop = threading.Event()
        comic = kind in ("manga", "manhwa", "comic")
        sites = [site for site in top_sites() if bool(site.get("comic")) == comic and not site.get("lnreader")
                 or comic and COMIC_SITE.search(site["site"])]
        if only:
            known = {site["site"]: site for site in top_sites()}
            sites = [known.get(name) or {"site": name, "host": name} for name in only]
        elif comic:
            # Each kind from the sites that publish it: manga sites, manhwa (webtoon and scanlation) sites,
            # and the other comic sites for comics.
            manga = [s for s in sites if MANGA_SITE.search(s["site"])]
            manhwa = [s for s in sites if MANHWA_SITE.search(s["site"]) and s not in manga]
            sites = {"manga": manga, "manhwa": manhwa}.get(kind) or [s for s in sites if s not in manga and s not in manhwa] or sites
        if language != "all" and not only:
            def fits(site: dict) -> bool:
                country = TLD_LANG.get(site["site"].rsplit(".", 1)[-1])
                return (country or "en") == language
            sites = [site for site in sites if fits(site)] + [site for site in sites if not fits(site)]
        from noveldb.core.catalog.search_ranking import ranked
        sites = ranked(sites, automatic=not only)[:50 if only else 12]
        self._top_sites = sites
        engine = GenericSearch(fetch=self.fetch, known_site=self.known_site, site_search=self.site_search)

        def run():
            try:
                engine.popular(sites, lambda cards: self._cards.emit(search_id, [dict(c, via="top") for c in cards]), stop,
                               lambda summary: self._done.emit(search_id, summary),
                               progress=lambda item: self._progress.emit(search_id, item))
            except Exception as e:
                log.warning("generic top list failed: %s", e)
                self._done.emit(search_id, {"failed": 1})

        threading.Thread(target=run, name="generic-top", daemon=True).start()

    def more(self, search_id: int, queries: list, page: int, ndb_page=None, sites: bool = True) -> None:
        """The search's next results, as the page is scrolled: NovelDB's page `ndb_page` (default `page`;
        0: none) and, with `sites`, each site's next results page. The page hears `moreDone` with
        {"more", "ndbPage", "ndbMore", "ndbOnly"} once every query has answered."""
        stop = self._stop
        ndb_page = page if ndb_page is None else ndb_page
        queries = [q for q in queries if q["mode"] in ("name", "tag", "author")]
        left, more, ndb_more, lock = [len(queries)], [False], [False], threading.Lock()

        def finished():
            self._more_done.emit(search_id, {"more": more[0], "ndbPage": ndb_page, "ndbMore": ndb_more[0], "ndbOnly": not sites})

        if not queries:
            finished()
            return

        def run(query: str, mode: str, item: dict):
            engine = self._engine(item)
            state = {}
            try:
                engine.more(query, mode, page, lambda cards: self._cards.emit(search_id, [dict(c, via=mode) for c in cards]),
                            stop, state.update, ndb_page=ndb_page, sites=sites)
            except Exception as e:
                log.warning("generic search (more) failed: %s", e)
            with lock:
                more[0] = more[0] or bool(state.get("more"))
                ndb_more[0] = ndb_more[0] or bool(state.get("ndbMore"))
                left[0] -= 1
                last = left[0] == 0
            if last:
                finished()

        for item in queries:
            threading.Thread(target=run, args=(item["query"], item["mode"], item), name="generic-more", daemon=True).start()

    def ndb_tags(self) -> None:
        """NovelDB's tag names, for the tag menu's filter (the page hears `ndbTags`)."""
        try:
            names = self.noveldb.tag_names() if self.noveldb is not None else []
        except Exception as e:
            log.warning("NovelDB tag list failed: %s", e)
            names = []
        self._ndb_tags.emit(names)

    def ndb_top(self, search_id: int, sources: list, page: int) -> None:
        """A Top list of NovelDB's sources: page `page` of their books, the most read first, from the
        index. The page hears the cards, then `ndbTopDone` with {"more"}."""
        try:
            cards, more = self.noveldb.search_page(",".join(sources), "source", page, 40)
        except Exception as e:
            log.warning("NovelDB top list failed: %s", e)
            cards, more = [], False
        start = page * 40
        self._cards.emit(search_id, [dict(card, via="top", siteRank=0, position=start + i, listLength=1000000)
                                     for i, card in enumerate(cards)])
        self._ndb_top_done.emit(search_id, {"more": more})

    def top_more(self, search_id: int) -> None:
        """The next page of the shown Top list's sites' popular lists."""
        stop, sites = self._stop, list(self._top_sites)
        engine = GenericSearch(fetch=self.fetch, known_site=self.known_site, site_search=self.site_search)

        def run():
            state = {}
            try:
                engine.popular_more(sites, lambda cards: self._cards.emit(search_id, [dict(c, via="top") for c in cards]),
                                    stop, state.update)
            except Exception as e:
                log.warning("generic top list (more) failed: %s", e)
            self._more_done.emit(search_id, {"more": bool(state.get("more"))})

        threading.Thread(target=run, name="generic-top-more", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _background(self, function, *args, workers: Optional[_Workers] = None, reader_work=None, **kwargs) -> None:
        """`function` on one of the page's worker threads. Kept and reused: a new thread for each message (a
        card's cover, an image) made the window wait for every one to start, and searches stuttered."""
        def run():
            _reading.work = reader_work
            try:
                if reader_work is None or reader_work.wait():
                    function(*args, **kwargs)
                else:
                    self._fetched.emit(args[0], {"error": "the reader was closed"})
            except Exception:
                log.exception("generic page: %s failed", getattr(function, "__name__", function))
            finally:
                _reading.work = None

        (workers or (_READER_WORKERS if reader_work else _PAGE_WORKERS)).submit(run)

    def _transport(self, fetch_id: int, request: dict) -> None:
        """An LNReader plugin's request, answered in the transport's result shape."""
        body = request.get("data")
        if request.get("dataBase64"):
            try:
                body = base64.b64decode(request["dataBase64"])
            except ValueError:
                body = None
        headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
        method = str(request.get("method") or "GET").upper()
        if urlsplit(request["url"]).hostname == TRANSLATE_HOST:
            result = _translate(request["url"], method, body, headers)
        else:
            result = self.fetch(request["url"], method=method, data=body, headers=headers, binary=True)
        if result is None:
            self._reader_answer(fetch_id, {"error": "the request failed"})
            return
        self._reader_answer(fetch_id, {
            "status": result["status"], "statusText": "", "finalUrl": result.get("url") or request["url"],
            "responseHeaders": result.get("headers") or "",
            "responseBase64": base64.b64encode(result.get("content") or b"").decode("ascii"),
        })

    def _render(self, url: str, *_args, scroll: bool = False, timeout: float = 45.0, **_kwargs) -> str:
        """A page's HTML after its scripts ran (the engines' `render`), from a hidden Catalog tab;
        `scroll`: scrolled to its end first (lists that load as you scroll)."""
        if scroll:
            self._note("Scrolling the chapter list to its end in a browser tab…")
        work = getattr(_reading, "work", None)
        if work is not None and not work.wait():
            return ""
        result = self.browser.fetch(url, timeout=timeout, scroll=scroll)
        return (result or {}).get("text") or ""

    def _chapter(self, url: str, index: int) -> dict:
        """A chapter for the reader; a manga chapter's images go to the page as they are found."""
        fetch_id = getattr(_reading, "id", None)
        work = getattr(_reading, "work", None)
        last = [0.0]

        def images(found, total):
            import time as clock

            if (fetch_id is not None and (clock.monotonic() - last[0] > 0.4 or len(found) >= total)
                    and (work is None or work.wait())):
                last[0] = clock.monotonic()
                images = [work.inline_image(url) if work and url.startswith("data:image/") else url for url in found]
                self._reader_images.emit(fetch_id, {"images": images, "total": total})

        options = {"fetch": lambda url, **kwargs: self.fetch(url, checkpoint=work.wait, **kwargs)} if work else {}
        return self.reader.chapter(url, index, on_images=images, **options)

    # The Library folder (books there open in the reader); set by the Catalog page.
    library_folder: Optional[Callable[[], Path]] = None

    def _in_library(self, url: str) -> bool:
        """A readable file inside the Library folder."""
        from noveldb.core.library import READER_FORMATS

        if self.library_folder is None:
            return False
        path = _file_path(url)
        try:
            folder = Path(self.library_folder()).resolve()
            target = path.resolve()
        except OSError:
            return False
        return target.suffix.lower() in READER_FORMATS and folder in target.parents and target.is_file()

    def open_book(self, book: dict) -> None:
        """Finds a Library book again ({title, url, author}): its site's page and a search for its title."""
        self._call("openBook", {"title": str(book.get("title") or ""), "url": str(book.get("url") or ""),
                                "author": str(book.get("author") or "")})

    def read_local(self, path: Path, title: str, chapter: int = -1) -> None:
        """Opens a Library book in the page's reader at a chapter or its last reading position."""
        from noveldb.qt.pages.library_reader import _places

        at = 0.0
        if chapter < 0:
            place = _places().get(str(Path(path)), {})
            chapter, at = int(place.get("chapter", 0)), float(place.get("at", 0.0))
        url = Path(path).resolve().as_uri()
        self._local_paths[url] = str(Path(path))  # the Library's own name for it, for the place kept
        self._call("readLocal", {"url": url, "title": title, "chapter": chapter, "at": at})

    def refresh_local(self, path: Path) -> None:
        self._call("refreshLocal", Path(path).resolve().as_uri())

    def _live_reply(self, data):
        result = self.library_live.query(data["url"], data.get("session", ""), data.get("after", 0), data.get("key")) \
            if getattr(self, "library_live", None) is not None else {}
        self._fetched.emit(data["id"], result)

    def _note(self, text: str) -> None:
        """A progress note for the reader request this thread is answering, if any."""
        fetch_id = getattr(_reading, "id", None)
        if fetch_id is not None:
            self._reader_status.emit(fetch_id, text)

    def _reader_answer(self, fetch_id: int, result) -> None:
        work = getattr(_reading, "work", None)
        if work is not None:
            if not work.wait():
                result = {"error": "the reader was closed"}
            elif isinstance(result, dict) and result.get("html"):
                result = dict(result, html=work.chapter_html(result["html"]))
        self._fetched.emit(fetch_id, result)

    def set_reader_visible(self, visible: bool) -> None:
        if self._reader_visible == visible:
            return
        self._reader_visible = visible
        if not visible:
            self.reader_session.set_visible(False)
        self._call("readerVisibility", visible)
        if visible:
            self.reader_session.set_visible(True)

    def _read(self, fetch_id: int, function, *args, force_downloader: bool = False, retry_images: bool = False,
              on_chapters=None, active=lambda: True) -> None:
        """The reader's book or chapter: the quick engines, then the downloader with its saved sessions;
        else {error, blocked} for the page to show (with Enter Browser)."""
        _reading.id = fetch_id

        def answer(result):
            self._reader_answer(fetch_id, result)

        work = getattr(_reading, "work", None)
        if work is not None and on_chapters is None:
            active = work.wait
        try:
            if not active():
                answer({"error": "the reader was closed"})
                return
            url = args[0]
            if url.startswith("file:"):
                answer(function(*args))
                return
            if retry_images:
                try:
                    answer(self._downloader_read(url, *args[1:], retry_images=True))
                except Exception as e:
                    answer({"error": str(e)[:300]})
                return
            if force_downloader:
                if self.downloader is None:
                    answer({"error": "Cloudflare blocked the chapter and no browser downloader is available",
                            "blocked": True, "message": BLOCKED})
                    return
                try:
                    self._note("Cloudflare detected; switching directly to the downloader browser…")
                    answer(self._downloader_read(url, *args[1:]))
                except Exception as e:
                    log.info("generic reader (Cloudflare recovery): %s", e)
                    answer({"error": f"Cloudflare recovery failed: {str(e)[:240]}",
                            "blocked": True, "message": BLOCKED})
                return
            streamed = False

            def publish(chapters):
                nonlocal streamed
                streamed = True
                on_chapters(chapters)

            from external_scraper import ExternalScraper

            mpia = ExternalScraper.is_mpia(url) and self._builtin_scrapers_enabled()
            if on_chapters is not None and self.downloader is not None and mpia:
                try:
                    answer(self._mpia_reader_book(url, publish, active, work))
                except Exception as e:
                    answer({"error": str(e)[:300], "blocked": True, "message": BLOCKED})
                return

            if on_chapters is not None and self.downloader is not None:
                from noveldb.core.npia.ids import SITE_DOMAIN, extract_novel_id

                novel_id = extract_novel_id(url) if urlsplit(url).hostname in (SITE_DOMAIN, "www." + SITE_DOMAIN) else None
                if novel_id:
                    try:
                        answer(self._npia_reader_book(url, novel_id, publish, active))
                        return
                    except Exception as e:
                        if streamed or not active():
                            answer({"error": str(e)[:300]})
                            return
                        log.info("generic reader (npia chapter list): %s", e)
            if url in self._downloader_books:
                # (An exception here once ended the thread: the page waited on "Scraping…" for good.)
                try:
                    answer(self._downloader_read(url, *args[1:]))
                except (ChapterNotOut, ChapterUnavailable) as e:
                    answer({"error": str(e)})
                except Exception as e:
                    log.info("generic reader (downloader): %s", e)
                    answer({"error": str(e)[:300], "blocked": True, "message": BLOCKED})
                return
            from noveldb.core.engines.comix import knows as comix_reader

            # Let the site-specific browser engines handle the reader before the quick generic parser.
            comix = comix_reader(url) and self._builtin_scrapers_enabled()
            if self.downloader is not None and mpia:
                try:
                    answer(self._downloader_read(url, *args[1:]))
                except (ChapterNotOut, ChapterUnavailable) as e:
                    answer({"error": str(e)})
                except Exception as e:
                    answer({"error": str(e)[:300], "blocked": True, "message": BLOCKED})
                return
            if self.downloader is not None and len(args) == 1 and (comix or _haruneko_site(url)):
                try:
                    answer(self._downloader_read(url))
                    return
                except Exception as e:
                    log.info("generic reader (site-specific browser engine): %s", e)
            try:
                kwargs = {"on_chapters": publish, "active": active} if on_chapters is not None else {}
                answer(function(*args, **kwargs))
                return
            except Exception as e:
                quick_error = str(e)[:300] or type(e).__name__
            if streamed or not active():
                answer({"error": quick_error})
                return
            if self.downloader is None:
                answer({"error": quick_error, "blocked": True, "message": BLOCKED})
                return
            try:
                answer(self._downloader_read(url, *args[1:]))
            except (ChapterNotOut, ChapterUnavailable) as e:
                answer({"error": str(e)})  # no login helps: not the blocked page
            except Exception as e:
                log.info("generic reader (downloader): %s", e)
                answer({"error": f"{quick_error}; the downloader: {str(e)[:200]}",
                        "blocked": True, "message": BLOCKED})
        finally:
            _reading.id = None

    def _npia_reader_book(self, url, novel_id, publish, active):
        """Load the list over HTTP so the browser worker is free to read the selected episode."""
        from external_scraper import ExternalScraper
        from noveldb.core.engines.generic import extract_metadata
        from noveldb.core.npia.ids import SITE_URL, novel_url

        book_url = novel_url(novel_id)
        page = self.fetch(book_url)
        if _refused(page) or page["status"] != 200:
            raise RuntimeError("the book page could not be read")
        source = page.get("text") or ""
        meta = extract_metadata(source, book_url)
        chapters = ExternalScraper._npia_parse_notice_html(source)
        count = 0
        seen = set()
        for number in range(1000):
            if not active():
                raise RuntimeError("the reader was closed")
            result = self.fetch(SITE_URL + "proc/episode_list", method="POST", referer=book_url,
                                data={"novel_no": novel_id, "sort": "DOWN", "page": number},
                                headers={"X-Requested-With": "XMLHttpRequest"})
            if not active():
                raise RuntimeError("the reader was closed")
            if _refused(result) or result["status"] != 200 or "Authentication required" in result.get("text", ""):
                raise RuntimeError(f"chapter list page {number + 1} could not be read")
            added = []
            for chapter in ExternalScraper._npia_parse_episode_html(result.get("text")):
                if chapter["id"] not in seen:
                    seen.add(chapter["id"])
                    count += 1
                    chapter["_npiaChapterNumber"] = count
                    added.append(chapter)
            if not added:
                break
            delta = added if number else chapters + added
            chapters = chapters + added
            self._downloader_books[url] = {"chapters": chapters, "bookUrl": book_url, "_npia": True,
                                           "_npia_reader_list": True, "_npia_novel_id": novel_id}
            publish(LiveReader._chapter_names(delta, len(chapters) - len(delta)))
        else:
            raise RuntimeError("the chapter list exceeded the page limit")
        if not count:
            raise RuntimeError("no chapters found")
        return {"title": meta.get("title") or "", "author": meta.get("author") or "", "engine": "npia",
                "chapters": LiveReader._chapter_names(chapters)}

    def _mpia_reader_book(self, url, publish, active, work):
        """Yield the browser worker after each TOC page so it can read the selected chapter."""
        iterator = None
        first_read = threading.Event()
        handoff = (url, first_read)
        self._mpia_reader_handoff = handoff

        def next_page(scraper):
            nonlocal iterator
            if not active():
                raise RuntimeError("the reader was closed")
            if iterator is None:
                self._apply_engine_settings(scraper)
                iterator = scraper._mpia_book_pages(url, reader=True)
            return next(iterator, None)

        count = 0
        try:
            while active():
                future = self.downloader(next_page)
                data = work.result(future) if work else future.result(timeout=90)
                if not active():
                    break
                if not data:
                    raise RuntimeError("no chapters found")
                self._downloader_books[url] = data
                chapters = data["chapters"]
                publish(LiveReader._chapter_names(chapters[count:], count))
                if data["_mpia_reader_complete"]:
                    return {"title": data["bookname"], "author": data["author"], "engine": "mpia",
                            "chapters": LiveReader._chapter_names(chapters)}
                if count == 0:
                    # Usually the displayed chapter is handed back immediately. Bound the wait
                    # because a saved position may be on a later TOC page.
                    first_read.wait(0.75)
                else:
                    time.sleep(0.3)
                count = len(chapters)
            raise RuntimeError("the reader was closed")
        finally:
            if getattr(self, "_mpia_reader_handoff", None) is handoff:
                self._mpia_reader_handoff = None
            if iterator is not None:
                self.downloader(lambda _scraper: iterator.close())

    def stop_reader(self) -> None:
        """The reader closed (or opened another book): the downloader stops the chapter it is reading for it, so
        the next book does not wait behind it (and the chapter read ahead is not started)."""
        self._reader_generation = getattr(self, "_reader_generation", 0) + 1  # queued reads for it are skipped
        work = self.reader_session
        work.stop()
        self.reader_session = ReaderSession(self._reader_visible)
        self._background(work.cleanup)
        scraper = getattr(self, "_reader_scraper", None)
        if scraper is not None:
            scraper._stop_requested = True
            log.info("generic reader: stopping the downloader's chapter (the reader closed)")

    def _builtin_scrapers_enabled(self) -> bool:
        from noveldb.core.settings.model import ENGINE_NATIVE

        settings = self.engine_settings()
        return settings is None or ENGINE_NATIVE in settings.enabled()

    def _apply_engine_settings(self, scraper) -> None:
        settings = self.engine_settings()
        if settings is None:
            return
        order = list(settings.enabled())
        if getattr(scraper, "engine_order", None) != order or getattr(scraper, "dynamic_priority", None) != settings.dynamic_priority:
            scraper.engine_order = order
            scraper.dynamic_priority = bool(settings.dynamic_priority)
            scraper._book_url = None  # a book read under the old order is read again under the new one

    def _downloader_read(self, url: str, index: Optional[int] = None, *, retry_images: bool = False) -> dict:
        # Its index is None for the book.
        """The book (index None) or one chapter through the downloader's engine chain and saved sessions."""
        # A book can have been opened successfully by the fast LNReader plugin and only fail when a
        # Cloudflare-protected chapter is requested.  In that case this is the downloader's first request
        # for the URL, so initialise its book state here before asking it for the matching chapter.
        work = getattr(_reading, "work", None)

        def submit(call):
            future = self.downloader(call)
            return work.result(future) if work else future.result(timeout=240)

        if index is None or url not in self._downloader_books:
            self._note("The quick engines couldn't read it; trying the downloader with your saved logins…")
            asked = getattr(self, "_reader_generation", 0)

            def load_or_reuse(scraper):
                if getattr(self, "_reader_generation", 0) != asked:
                    raise RuntimeError("the reader was closed")
                self._apply_engine_settings(scraper)
                current = getattr(scraper, "_book_data", None)
                if (
                    getattr(scraper, "_book_url", None) == url
                    and isinstance(current, dict)
                    and current.get("chapters")
                    and not current.get("_npia_reader_list")
                    and not current.get("_mpia_reader_list")
                ):
                    return current
                scraper.reader_work = work
                try:
                    return scraper.parse_book(url)
                finally:
                    scraper.reader_work = None

            data = submit(load_or_reuse)
            if not data or not data.get("chapters"):
                raise RuntimeError("no chapters (the site may need a login)")
            self._downloader_books[url] = data
            if index is None:
                return {"title": data.get("bookname") or "", "author": data.get("author") or "",
                        "engine": "downloader (saved logins)",
                        "chapters": [{"name": c.get("name") or f"Chapter {i + 1}", "section": c.get("sectionName") or ""}
                                     for i, c in enumerate(data["chapters"])]}
        data = self._downloader_books[url]
        if index >= len(data["chapters"]):
            raise RuntimeError("the chapter list changed; reopen the book and try again")
        chapter = data["chapters"][index]
        self._note(f"Scraping {chapter.get('name') or 'the chapter'} with the downloader…")

        fetch_id = getattr(_reading, "id", None)
        streamed = 0

        def page_in(link: str, data: str) -> None:
            # A page the engine fetched (HaruNeko's, as they come): shown at once, and logged.
            nonlocal streamed
            if work and not work.wait():
                return
            streamed += 1
            scraper_log(f"  Page {streamed} downloaded ({len(data) * 3 // 4 // 1024} KB)")
            if fetch_id is not None:
                self._reader_status.emit(fetch_id, f"Page {streamed} downloaded…")
                url = work.inline_image(data) if work else data
                self._reader_images.emit(fetch_id, {"images": [url], "from": streamed - 1, "total": streamed})

        scraper_log = lambda _text: None  # noqa: E731

        asked_in = getattr(self, "_reader_generation", 0)

        def read(scraper):
            if getattr(self, "_reader_generation", 0) != asked_in:
                raise RuntimeError("the reader was closed")  # queued behind a chapter of a book since closed
            nonlocal scraper_log
            scraper_log = getattr(scraper, "log", scraper_log)
            self._reader_scraper = scraper
            scraper._stop_requested = False  # a stop for an earlier book's chapter
            scraper.reader_work = work
            try:
                if work and not work.wait():
                    raise RuntimeError("the reader was closed")
                self._apply_engine_settings(scraper)
                if data.get("_npia_reader_list"):
                    if not scraper._start_npia_browser(data["bookUrl"]):
                        raise RuntimeError("the npia viewer could not be opened")
                    scraper._book_data, scraper._book_url = data, data["bookUrl"]
                elif data.get("_mpia_reader_list"):
                    handoff = getattr(self, "_mpia_reader_handoff", None)
                    if handoff and handoff[0] == url:
                        handoff[1].set()
                    if not scraper._start_mpia_browser(data["bookUrl"]):
                        raise RuntimeError("the mpia viewer could not be opened")
                    scraper._book_data, scraper._book_url = data, data["bookUrl"]
                elif getattr(scraper, "_book_url", None) != url:
                    scraper.parse_book(url)  # the worker may have read another book since
                scraper.on_image = page_in
                scraper.reader_progress = (lambda note: self._reader_status.emit(fetch_id, note)) if fetch_id is not None else None
                scraper_log(f"Reader: chapter {index + 1} ({chapter.get('name') or '?'})…")
                options = {"retry_images": True} if retry_images else {}
                done = scraper.parse_chapter(index, chapter, interval=0, **options)
            finally:
                scraper.reader_work = None
                scraper.on_image = None
                scraper.reader_progress = None
                self._reader_scraper = None
            pages = len((done or {}).get("images") or [])
            scraper_log(f"Reader: chapter {index + 1} " + (f"read, {pages} page(s)." if done and not done.get("_locked")
                                                           and not done.get("_readerError") else "could not be read."))
            return done

        result = submit(read)
        if result and result.get("_readerError"):
            raise ChapterUnavailable(result["_readerError"])
        if not result or result.get("_locked"):
            reason = (result or {}).get("_lockReason") or "login or purchase"
            if reason.startswith("not published yet"):
                raise ChapterNotOut(f"This chapter is {reason}.")
            raise RuntimeError(f"the chapter could not be read ({reason})")
        return {"name": result.get("chapterName") or chapter.get("name") or "",
                "html": _with_page_data(result, work.inline_image if work else None),
                "text": result.get("contentText") or "", "comic": bool(result.get("comic") or result.get("images"))
                and not result.get("contentText"), "imageReferer": result.get("imageReferer") or chapter.get("url") or url,
                "imageFallback": bool(result.get("imageFallback"))}

    def _image(self, fetch_id: int, url: str, referer: str, *, reader: bool = False) -> None:
        from noveldb.core.engines.comix import knows as comix_reader

        if reader and comix_reader(referer):
            referer = ""  # The Comix reader's CDN expects no Referer.
        headers = {"Accept": "image/*"}
        work = getattr(_reading, "work", None) if reader else None
        options = {"checkpoint": work.wait} if work else {}
        if reader:
            try:
                cookie = self.logins.header(url)
            except Exception:
                cookie = ""
            if cookie:
                headers["Cookie"] = cookie
        result = default_fetch(url, binary=True, referer=referer, headers=headers, **options)
        if reader and result and result["status"] in (401, 403) and referer:
            parts = urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}/"
            if parts.netloc and origin != referer:
                result = default_fetch(url, binary=True, referer=origin, headers=headers, **options)
        # A cover over 8 MB (an original upload, not a card image) is not handed to the page: as a data
        # address it is a third bigger again, and the page gets it through the app's main thread.
        if (not result or result["status"] >= 400 or not result.get("content")
                or (not reader and len(result["content"]) > 8 << 20)):
            self._reader_answer(fetch_id, None)
            return
        kind = ""
        for line in str(result.get("headers") or "").splitlines():
            if line.lower().startswith("content-type:") and "image/" in line.lower():
                kind = line.split(":", 1)[1].split(";")[0].strip()
                break
        if reader and not kind:
            from noveldb.core.engines.scraper_mixin import _image_kind

            kind = _image_kind(base64.b64encode(result["content"][:96]).decode("ascii"))
        if work:
            if not work.wait():
                return
            url = work.image(result["content"], kind or "image/jpeg")
            self._reader_answer(fetch_id, {"imageUrl": url, "bytes": len(result["content"])} if url else None)
        else:
            data = base64.b64encode(result["content"]).decode("ascii")
            self._fetched.emit(fetch_id, {"dataUrl": f"data:{kind or 'image/jpeg'};base64,{data}", "bytes": len(result["content"])})

    def _details(self, card: dict) -> None:
        """A book's details: from the offline cache when read in the last few days, else its page."""
        cached = self.cache.get_json(self.DETAILS, card["url"], max_age=self.DETAILS_DAYS * 86400)
        if cached:
            self._detailed.emit(dict(card, **cached, complete=True))
            return
        engine = GenericSearch(fetch=self.fetch, site_search=self.site_search, noveldb=self.noveldb, render=self._render)
        try:
            found = engine.details(card)
        except Exception:
            self._detailed.emit(dict(card, complete=True))
            return
        keep = {k: v for k, v in found.items() if k not in ("url", "site", "source", "via", "complete", "ms") and v}
        if keep:
            self.cache.put_json(self.DETAILS, card["url"], keep)
        self._detailed.emit(found)

    def _cover(self, fetch_id: int, key: str) -> None:
        """A cover the page made before (its address and compression settings are the key), or None."""
        data = self.cache.get("covers", key)
        self._fetched.emit(fetch_id, {"dataUrl": data.decode("ascii")} if data else None)

    def _cover_put(self, key: str, data_url: str) -> None:
        self.cache.put("covers", key, data_url.encode("ascii"))

    def _call(self, name: str, *args) -> None:
        payload = ", ".join(json.dumps(arg, ensure_ascii=False) for arg in args)
        self.run_js(f"window.NovelDBGeneric && NovelDBGeneric.{name}({payload});")
