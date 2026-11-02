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
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage

from noveldb.core import paths
from noveldb.core.catalog.generic_search import GenericSearch, default_fetch, top_sites
from noveldb.core.catalog.site_search import SiteSearch

log = logging.getLogger(__name__)
PAGE_DIR = Path(__file__).with_name("generic")
PAGE = PAGE_DIR / "index.html"
SCRIPT = PAGE_DIR / "generic.js"
MESSAGE_TYPES = ("genericReady", "genericSearch", "genericStop", "genericFetch", "genericImage", "genericDetails")
_CHALLENGE = ("just a moment", "attention required", "security check", "verify you are human")


def is_generic_page(url: QUrl) -> bool:
    return url.isLocalFile() and Path(url.toLocalFile()).resolve() == PAGE.resolve()


def parse_message(data: dict) -> Optional[dict]:
    """A generic-page message with the fields it needs, or None."""
    kind = data.get("type")
    if kind not in MESSAGE_TYPES:
        return None
    if kind == "genericSearch":
        query = data.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            return None
        data["mode"] = data.get("mode") if data.get("mode") in ("name", "tag") else "name"
    if kind in ("genericFetch", "genericImage") and not isinstance(data.get("id"), int):
        return None
    if kind == "genericFetch":
        request = data.get("request")
        if not isinstance(request, dict) or not str(request.get("url", "")).startswith(("http://", "https://")):
            return None
    if kind == "genericImage" and not str(data.get("url", "")).startswith(("http://", "https://")):
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

    def __init__(self, profile, parent: QObject, tabs: int = 3) -> None:
        super().__init__(parent)
        self.profile = profile
        self.tabs = tabs
        self._queue: list = []
        self._active = 0
        self._request.connect(self._enqueue)

    def fetch(self, url: str, timeout: float = 45.0) -> Optional[dict]:
        if threading.current_thread() is threading.main_thread():
            return None
        job = {"url": url, "event": threading.Event(), "result": None, "deadline": time.monotonic() + timeout - 5}
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
        page.toHtml(lambda html, job=job: self._finish(job, html))

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

    def __init__(self, run_js: Callable[[str], None], profile, known_site: Callable[[str], bool], parent: QObject):
        super().__init__(parent)
        self.run_js = run_js
        self.browser = BrowserFetcher(profile, self)
        self.known_site = known_site
        self._stop = threading.Event()
        self._search_id = 0
        cache = paths.cache_dir() / "generic-search-sites.json"
        self.site_search = SiteSearch(self.fetch, cache_file=cache)
        self.site_search.render = self.browser.fetch
        self._cards.connect(lambda sid, cards: self._call("add", cards, sid))
        self._done.connect(lambda sid, summary: self._call("done", sid, summary))
        self._detailed.connect(lambda card: self._call("details", card))
        self._fetched.connect(lambda fid, result: self._call("fetched", fid, result))

    # -- requests ----------------------------------------------------------------------------------

    def fetch(self, url: str, method: str = "GET", data=None, headers=None, binary: bool = False,
              referer: str = "") -> Optional[dict]:
        """Plain HTTP; a page that refuses it is read in a hidden Catalog tab instead."""
        result = default_fetch(url, method=method, data=data, headers=headers, binary=binary, referer=referer)
        if method == "GET" and not binary and _refused(result):
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
            self.search(int(data.get("id") or 0), data["query"], data["mode"])
        elif kind == "genericStop":
            self._stop.set()
        elif kind == "genericFetch":
            self._background(self._transport, data["id"], data["request"])
        elif kind == "genericImage":
            self._background(self._image, data["id"], data["url"], str(data.get("referer") or ""))
        elif kind == "genericDetails":
            self._background(self._details, data["card"])
        return True

    def init_page(self) -> None:
        sites = top_sites()
        lnreader = [{"id": plugin, "site": site["site"], "comic": site["comic"]}
                    for site in sites for plugin in site.get("lnreader", [])[:1]]
        self._call("init", {"sites": [site["site"] for site in sites], "lnreader": lnreader})

    def search(self, search_id: int, query: str, mode: str) -> None:
        self._stop.set()
        self._stop = stop = threading.Event()
        self._search_id = search_id
        engine = GenericSearch(fetch=self.fetch, known_site=self.known_site, site_search=self.site_search)

        def run():
            try:
                engine.run(query, mode, lambda cards: self._cards.emit(search_id, cards), stop,
                           lambda summary: self._done.emit(search_id, summary))
            except Exception as e:
                log.warning("generic search failed: %s", e)
                self._done.emit(search_id, {"failed": 1})

        threading.Thread(target=run, name="generic-search", daemon=True).start()

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
        self._fetched.emit(fetch_id, {"dataUrl": f"data:{kind};base64,{data}"})

    def _details(self, card: dict) -> None:
        engine = GenericSearch(fetch=self.fetch, site_search=self.site_search)
        try:
            self._detailed.emit(engine.details(card))
        except Exception:
            self._detailed.emit(dict(card, complete=True))

    def _call(self, name: str, *args) -> None:
        payload = ", ".join(json.dumps(arg, ensure_ascii=False) for arg in args)
        self.run_js(f"window.NovelDBGeneric && NovelDBGeneric.{name}({payload});")
