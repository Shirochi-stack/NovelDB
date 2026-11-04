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
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage

from noveldb.core import paths
from noveldb.core.catalog.generic_search import GenericSearch, default_fetch, load_learned, save_learned, top_sites
from noveldb.core.catalog.live_reader import LiveReader
from noveldb.core.catalog.noveldb_index import NovelDBIndex
from noveldb.core.catalog.offline_cache import OfflineCache
from noveldb.core.catalog.site_search import SiteSearch

log = logging.getLogger(__name__)
PAGE_DIR = Path(__file__).with_name("generic")
PAGE = PAGE_DIR / "index.html"
SCRIPT = PAGE_DIR / "generic.js"
MESSAGE_TYPES = ("genericReady", "genericSearch", "genericStop", "genericFetch", "genericImage", "genericDetails",
                 "genericDownload", "genericBook", "genericChapter", "genericTop", "genericLogin",
                 "genericCover", "genericCoverPut", "genericMore", "genericTopMore", "genericFullscreen", "genericNdbTop")
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
    if kind in ("genericSearch", "genericMore"):
        # A search is one or more queries run together: the title, and each tag picked in the tag menu.
        asked = data.get("queries")
        if not isinstance(asked, list):
            asked = [{"query": data.get("query"), "mode": data.get("mode")}]
        queries = []
        for item in asked[:MAX_QUERIES]:
            query = item.get("query") if isinstance(item, dict) else None
            if isinstance(query, str) and query.strip() and len(query) <= 300:
                queries.append({"query": query.strip(),
                                "mode": item.get("mode") if item.get("mode") in ("name", "tag", "id", "author") else "name"})
        if not queries:
            return None
        data["queries"] = queries
        data["query"], data["mode"] = queries[0]["query"], queries[0]["mode"]
    if kind in ("genericFetch", "genericImage", "genericBook", "genericChapter") and not isinstance(data.get("id"), int):
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
    if kind == "genericChapter" and not (isinstance(data.get("index"), int) and data["index"] >= 0):
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
            page = QWebEnginePage(self.profile, self)
            page.setAudioMuted(True)
            job["page"] = page
            page.loadFinished.connect(lambda ok, job=job: QTimer.singleShot(1200, lambda: self._check(job)))
            QTimer.singleShot(int(max(1.0, job["deadline"] - time.monotonic()) * 1000), lambda job=job: self._finish(job, ""))
            page.load(QUrl(job["url"]))

    def _check(self, job) -> None:
        page = job.get("page")
        if page is None:
            return
        title = (page.title() or "").lower()
        if any(word in title for word in _CHALLENGE) and time.monotonic() < job["deadline"]:
            QTimer.singleShot(1000, lambda: self._check(job))  # the site's check page reloads itself when done
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
        page.deleteLater()
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
    _reader_status = Signal(int, str)
    _reader_images = Signal(int, dict)

    DETAILS_DAYS = 3
    # The details cache's name: a new one drops details read by older code (link tags, HTML synopses).
    DETAILS = "details-v2"

    def __init__(self, run_js: Callable[[str], None], profile, known_site: Callable[[str], bool], parent: QObject,
                 cache_mb: Callable[[], int] = lambda: 300):
        super().__init__(parent)
        self.cache = OfflineCache(cache_mb)
        self.run_js = run_js
        self.browser = BrowserFetcher(profile, self)
        self.known_site = known_site
        self._stop = threading.Event()
        self._search_id = 0
        cache = paths.cache_dir() / "generic-search-sites.json"
        self.site_search = SiteSearch(self.fetch, cache_file=cache)
        self.site_search.render = self.browser.fetch
        self.reader = LiveReader(self.fetch, render=self._render)
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
        self._top_sites: list = []  # the sites of the Top list shown, for its next pages
        self._reader_status.connect(lambda fid, text: self._call("readerStatus", fid, text))
        self._reader_images.connect(lambda fid, found: self._call("readerImages", fid, found))
        # The downloader's own browser worker (`context.browser.submit`), for books the quick engines
        # can't read: it has the saved sessions of Enter Browser. Set by the Catalog page.
        self.downloader: Optional[Callable] = None
        self._downloader_books: dict = {}

    # -- requests ----------------------------------------------------------------------------------

    def fetch(self, url: str, method: str = "GET", data=None, headers=None, binary: bool = False,
              referer: str = "", quick: bool = False) -> Optional[dict]:
        """Plain HTTP; a page that refuses it is read in a hidden Catalog tab instead (not for `quick` guesses)."""
        self._note(f"Fetching {_short(url)}…")
        result = default_fetch(url, method=method, data=data, headers=headers, binary=binary, referer=referer, quick=quick)
        if method == "GET" and not binary and not quick and _refused(result):
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
        if kind == "genericReady":
            self.init_page()
        elif kind == "genericSearch":
            self.search(int(data.get("id") or 0), data["queries"])
        elif kind == "genericMore":
            self.more(int(data.get("id") or 0), data["queries"], data["page"], data.get("ndbPage"), data.get("sites", True))
        elif kind == "genericTopMore":
            self.top_more(int(data.get("id") or 0))
        elif kind == "genericNdbTop":
            self._background(self.ndb_top, int(data.get("id") or 0), data["sources"], int(data.get("page") or 0))
        elif kind == "genericStop":
            self._stop.set()
        elif kind == "genericFetch":
            self._background(self._transport, data["id"], data["request"])
        elif kind == "genericImage":
            self._background(self._image, data["id"], data["url"], str(data.get("referer") or ""))
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
            self._background(self._read, data["id"], self.reader.book, data["url"])
        elif kind == "genericChapter":
            self._background(self._read, data["id"], self._chapter, data["url"], data["index"])
        return True

    def init_page(self) -> None:
        sites = top_sites()
        lnreader = [{"id": plugin, "site": site["site"], "comic": site["comic"]}
                    for site in sites for plugin in site.get("lnreader", [])[:1]]
        self._call("init", {"sites": [site["site"] for site in sites], "comic": [site["site"] for site in sites if site["comic"]],
                            "lnreader": lnreader,
                            "downloadIcon": download_icon()})

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

        def run(query: str, mode: str):
            engine = GenericSearch(fetch=self.fetch, known_site=self.known_site, site_search=self.site_search,
                                   noveldb=self.noveldb)
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
                           stop, finished, progress=lambda item: self._progress.emit(search_id, item))
            except Exception as e:
                log.warning("generic search failed: %s", e)
                finished({"failed": 1})

        for item in queries:
            threading.Thread(target=run, args=(item["query"], item["mode"]), name="generic-search", daemon=True).start()

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
        sites = sites[:30]
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

        def run(query: str, mode: str):
            engine = GenericSearch(fetch=self.fetch, known_site=self.known_site, site_search=self.site_search,
                                   noveldb=self.noveldb)
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
            threading.Thread(target=run, args=(item["query"], item["mode"]), name="generic-more", daemon=True).start()

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

    def _background(self, function, *args) -> None:
        threading.Thread(target=function, args=args, name="generic-page", daemon=True).start()

    def _transport(self, fetch_id: int, request: dict) -> None:
        """An LNReader plugin's request, answered in the transport's result shape."""
        body = request.get("data")
        if request.get("dataBase64"):
            try:
                body = base64.b64decode(request["dataBase64"])
            except ValueError:
                body = None
        headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
        result = self.fetch(request["url"], method=str(request.get("method") or "GET").upper(), data=body,
                            headers=headers, binary=True)
        if result is None:
            self._fetched.emit(fetch_id, {"error": "the request failed"})
            return
        self._fetched.emit(fetch_id, {
            "status": result["status"], "statusText": "", "finalUrl": result.get("url") or request["url"],
            "responseHeaders": result.get("headers") or "",
            "responseBase64": base64.b64encode(result.get("content") or b"").decode("ascii"),
        })

    def _render(self, url: str, *_args, scroll: bool = False, **_kwargs) -> str:
        """A page's HTML after its scripts ran (the engines' `render`), from a hidden Catalog tab;
        `scroll`: scrolled to its end first (lists that load as you scroll)."""
        if scroll:
            self._note("Scrolling the chapter list to its end in a browser tab…")
        result = self.browser.fetch(url, timeout=75.0 if scroll else 45.0, scroll=scroll)
        return (result or {}).get("text") or ""

    def _chapter(self, url: str, index: int) -> dict:
        """A chapter for the reader; a manga chapter's images go to the page as they are found."""
        fetch_id = getattr(_reading, "id", None)
        last = [0.0]

        def images(found, total):
            import time as clock

            if fetch_id is not None and (clock.monotonic() - last[0] > 0.4 or len(found) >= total):
                last[0] = clock.monotonic()
                self._reader_images.emit(fetch_id, {"images": list(found), "total": total})

        return self.reader.chapter(url, index, on_images=images)

    # The Library folder (books there open in the reader); set by the Catalog page.
    library_folder: Optional[Callable[[], Path]] = None

    def _in_library(self, url: str) -> bool:
        """A file: address of an EPUB in the Library folder (the page may ask for nothing else)."""
        from urllib.parse import unquote, urlsplit

        if self.library_folder is None:
            return False
        raw = unquote(urlsplit(url).path)
        path = Path(raw.lstrip("/") if __import__("os").name == "nt" else raw)
        try:
            folder = Path(self.library_folder()).resolve()
            target = path.resolve()
        except OSError:
            return False
        return target.suffix.lower() == ".epub" and folder in target.parents

    def read_local(self, path: Path, title: str) -> None:
        """Opens a Library EPUB in the page's reader."""
        self._call("readLocal", {"url": Path(path).resolve().as_uri(), "title": title})

    def _note(self, text: str) -> None:
        """A progress note for the reader request this thread is answering, if any."""
        fetch_id = getattr(_reading, "id", None)
        if fetch_id is not None:
            self._reader_status.emit(fetch_id, text)

    def _read(self, fetch_id: int, function, *args) -> None:
        """The reader's book or chapter: the quick engines, then the downloader with its saved sessions;
        else {error, blocked} for the page to show (with Enter Browser)."""
        _reading.id = fetch_id
        try:
            url = args[0]
            if url.startswith("file:"):
                self._fetched.emit(fetch_id, function(*args))  # a Library EPUB: no downloader
                return
            if url in self._downloader_books:
                self._fetched.emit(fetch_id, self._downloader_read(url, *args[1:]))
                return
            try:
                self._fetched.emit(fetch_id, function(*args))
                return
            except Exception as e:
                quick_error = str(e)[:300] or type(e).__name__
            if self.downloader is None:
                self._fetched.emit(fetch_id, {"error": quick_error, "blocked": True, "message": BLOCKED})
                return
            try:
                self._fetched.emit(fetch_id, self._downloader_read(url, *args[1:]))
            except Exception as e:
                log.info("generic reader (downloader): %s", e)
                self._fetched.emit(fetch_id, {"error": f"{quick_error}; the downloader: {str(e)[:200]}",
                                              "blocked": True, "message": BLOCKED})
        finally:
            _reading.id = None

    def _downloader_read(self, url: str, index: Optional[int] = None) -> dict:
        """The book (index None) or one chapter through the downloader's engine chain and saved sessions."""
        if index is None:
            self._note("The quick engines couldn't read it; trying the downloader with your saved logins…")
            data = self.downloader(lambda scraper: scraper.parse_book(url)).result(timeout=240)
            if not data or not data.get("chapters"):
                raise RuntimeError("no chapters (the site may need a login)")
            self._downloader_books[url] = data
            return {"title": data.get("bookname") or "", "author": data.get("author") or "",
                    "engine": "downloader (saved logins)",
                    "chapters": [{"name": c.get("name") or f"Chapter {i + 1}", "section": c.get("sectionName") or ""}
                                 for i, c in enumerate(data["chapters"])]}
        data = self._downloader_books[url]
        chapter = data["chapters"][index]
        self._note(f"Scraping {chapter.get('name') or 'the chapter'} with the downloader…")

        def read(scraper):
            if getattr(scraper, "_book_url", None) != url:
                scraper.parse_book(url)  # the worker may have read another book since
            return scraper.parse_chapter(index, chapter, interval=0)

        result = self.downloader(read).result(timeout=240)
        if not result:
            raise RuntimeError("the chapter could not be read (it may need a login or a purchase)")
        return {"name": result.get("chapterName") or chapter.get("name") or "", "html": result.get("contentHtml") or "",
                "text": result.get("contentText") or ""}

    def _image(self, fetch_id: int, url: str, referer: str) -> None:
        result = default_fetch(url, binary=True, referer=referer, headers={"Accept": "image/*"})
        if not result or result["status"] >= 400 or not result.get("content"):
            self._fetched.emit(fetch_id, None)
            return
        kind = "image/jpeg"
        for line in str(result.get("headers") or "").splitlines():
            if line.lower().startswith("content-type:") and "image/" in line:
                kind = line.split(":", 1)[1].split(";")[0].strip()
        data = base64.b64encode(result["content"]).decode("ascii")
        self._fetched.emit(fetch_id, {"dataUrl": f"data:{kind};base64,{data}", "bytes": len(result["content"])})

    def _details(self, card: dict) -> None:
        """A book's details: from the offline cache when read in the last few days, else its page."""
        cached = self.cache.get_json(self.DETAILS, card["url"], max_age=self.DETAILS_DAYS * 86400)
        if cached:
            self._detailed.emit(dict(card, **cached, complete=True))
            return
        engine = GenericSearch(fetch=self.fetch, site_search=self.site_search, noveldb=self.noveldb)
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
