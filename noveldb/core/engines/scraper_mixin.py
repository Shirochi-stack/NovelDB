"""How `ExternalScraper` drives the extra site engines.

Page-script engines (LNReader, HaruNeko) are injected into the browser page like the WebToEpub
bridge. Their requests to other sites go through `_engine_transport`, which uses the app's HTTP
client with the browser's cookies and, when a site refuses that, makes the request from a browser
tab on that site. Python engines (lightnovel-crawler, FanFicFare, the generic fallback) fetch through
`engine_fetch`, which works the same way, so they work with or without the Full build's browser.

Browser work cannot happen inside a page binding (Playwright's sync API deadlocks), so a binding that
needs a tab queues a job and answers `{pending: id}`; the page script waits for that id. The engines'
calls run through `_pump`, which starts the call, runs queued jobs between checks and hands each
result back to the page that asked (`window.__noveldb_async`).

The methods expect the `ExternalScraper` attributes they use (`log`, `_page`, `_context`,
`_book_data`, `_book_url`, `_load_engine_bundle`, `_gm_xmlhttp_request`, ...).
"""

from __future__ import annotations

import json
import re
import threading
import time
from noveldb.core.chapter_timing import timed_chapter, browser_chapter_timing
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit

from noveldb.core.settings.model import (ENGINE_FFF, ENGINE_GDL, ENGINE_GENERIC, ENGINE_HN, ENGINE_LNC,
                                        ENGINE_LNR, ENGINE_MANGAPEAK, ENGINE_TAKO)

TRANSPORT_BINDING = "__noveldb_engine_transport"
WINDOW_BINDING = "__noveldb_window_script"
# Started calls and waits for queued browser jobs, in the page (installed before each engine call).
_ASYNC_HELPER = """(() => {
    if (window.__noveldb_async) return;
    const calls = {}, waiters = {}, early = {};
    let next = 0;
    window.__noveldb_async = {
        start(name, args) {
            const id = ++next;
            calls[id] = {done: false};
            Promise.resolve().then(() => window[name](...args)).then(
                value => { calls[id] = {done: true, value: value}; },
                error => { calls[id] = {done: true, error: String((error && error.message) || error)}; });
            return id;
        },
        poll(id) {
            const call = calls[id];
            if (call && call.done) { delete calls[id]; return call; }
            return null;
        },
        wait(id) {
            if (id in early) { const value = early[id]; delete early[id]; return Promise.resolve(value); }
            return new Promise(resolve => { waiters[id] = resolve; });
        },
        resolve(id, value) {
            const waiter = waiters[id];
            delete waiters[id];
            if (waiter) waiter(value); else early[id] = value;
        }
    };
})()"""
# Statuses that mean "not for this client": a bot check, a refusal or a busy server.
_REFUSED = {401, 403, 418, 429, 503}
_BROWSER_FETCH_SECONDS = 25
_READER_TIMEOUT_STATUSES = {408, 504}
_READER_TIMEOUT = (5, 15)  # connect and read, so a stalled page leaves time for the next engine
# Headers a page cannot set; the browser sends its own.
_BROWSER_SETS = {"cookie", "user-agent", "referer", "host", "content-length", "accept-encoding", "connection",
                 "origin"}
_IN_PAGE_FETCH = """async (request) => {
    const init = {method: request.method, headers: request.headers, credentials: "include", redirect: "follow"};
    if (request.bodyBase64) {
        init.body = Uint8Array.from(atob(request.bodyBase64), c => c.charCodeAt(0));
    } else if (request.body != null && request.method !== "GET") {
        init.body = request.body;
    }
    const response = await fetch(request.url, init);
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    const headers = [];
    response.headers.forEach((value, name) => headers.push(name + ": " + value));
    return {status: response.status, statusText: response.statusText, finalUrl: response.url,
            responseHeaders: headers.join("\\n"), responseBase64: btoa(binary)};
}"""


@dataclass(frozen=True)
class PageScript:
    engine: str   # settings name
    prefix: str   # the bundle's window.* entry points: <prefix>_parseBook and so on
    flag: str     # set on the book dict, so chapters go back to the same engine
    label: str
    bundle: Optional[str]   # the engine_updates.Engine attribute name, for updatable bundles


PAGE_SCRIPTS = {
    "comix": PageScript("comix", "__COMIX", "_comix", "Comix reader", None),
    ENGINE_LNR: PageScript(ENGINE_LNR, "__LNR", "_lnreader", "LNReader", "LNREADER"),
    ENGINE_HN: PageScript(ENGINE_HN, "__HN", "_haruneko", "HaruNeko", "HARUNEKO"),
}
PYTHON_ENGINES = (ENGINE_LNC, ENGINE_FFF, ENGINE_GDL, ENGINE_MANGAPEAK, ENGINE_TAKO, ENGINE_GENERIC)
PYTHON_FLAGS = {ENGINE_LNC: "_lncrawl", ENGINE_FFF: "_fanficfare", ENGINE_GDL: "_gallerydl",
                ENGINE_MANGAPEAK: "_mangapeak", ENGINE_TAKO: "_tako", "mangak": "_mangak", ENGINE_GENERIC: "_generic"}


def _image_kind(data_base64: str) -> str:
    """The image type of base64 bytes, by their signature ("" when not a plain image): sites send pages as
    application/octet-stream, and a scrambled page (decoded by the engine later) matches none."""
    import base64
    import binascii

    try:
        head = base64.b64decode(data_base64[:32])
    except (binascii.Error, ValueError):
        return ""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return ""


def _with_text(result: dict) -> dict:
    """A transport result with `responseText` filled in from its bytes (for `_looks_refused`)."""
    if result and not result.get("responseText") and result.get("responseBase64"):
        import base64

        head = base64.b64decode(result["responseBase64"][:8000] + "=" * (-len(result["responseBase64"][:8000]) % 4))
        return dict(result, responseText=head.decode("utf-8", "replace"))
    return result or {}


# Sites that show an age check unless a cookie says it was answered: their registered domain -> the cookies.
# syosetu's adult site (novel18) sends every visitor to nl.syosetu.com/redirect/ageauth without `over18`.
# The sites the generic engine reads best: their desktop pages with every page of a chapter (gallery-dl and HaruNeko
# list them, but their pages come up missing there). It goes first for them, as on the phones
# (SourceCatalog.genericFirst, SiteEngineRouter.genericFirst).
# rawkuma.net: its book page lists every chapter and its chapter pages every picture.
GENERIC_FIRST = ("\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com", "fanfox.net", "mangahere.cc", "mangahere.com", "webtoons.com", "rawkuma.net",
                 "\u0062\u006f\u006f\u006b\u006b\u006f\u0072.com")
ASURA_COMICS = ("asurascans.com", "asuracomic.net")


def generic_first(url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if host.replace("www.", "", 1) in ASURA_COMICS and parts.path.startswith(("/comics/", "/series/")):
        return True  # Asura's comics (its novels are lightnovel-crawler's, which read a comic as a novel)
    return any(host == site or host.endswith("." + site) for site in GENERIC_FIRST)


# fanfox and mangahere: isAdult answers their adult warning; MHNMM keeps a phone-sized browser on the desktop
# pages (the mobile site's chapters say "licensed").
_DM5_COOKIES = {"isAdult": "1", "MHNMM": "1"}
AGE_COOKIES = {"syosetu.com": {"over18": "yes"}, "fanfox.net": _DM5_COOKIES, "mangahere.cc": _DM5_COOKIES,
               "mangahere.com": _DM5_COOKIES,
               # webtoons.com: its age gate and consent questions, answered.
               "webtoons.com": {"ageGatePass": "true", "needGDPR": "false", "needCCPA": "false", "needCOPPA": "false"}}


def age_cookies(url: str) -> dict:
    """The age-check cookies `url`'s site wants ({} for most sites)."""
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    for domain, cookies in AGE_COOKIES.items():
        if host == domain or host.endswith("." + domain):
            return dict(cookies)
    return {}


def give_age_cookies(context, url: str) -> None:
    """The age-check cookies for `url`'s site, set in a browser context (no-op for most sites)."""
    from urllib.parse import urlsplit

    cookies = age_cookies(url)
    host = (urlsplit(url).hostname or "").lower()
    domain = next((d for d in AGE_COOKIES if host == d or host.endswith("." + d)), "")
    if not cookies or context is None:
        return
    try:
        context.add_cookies([{"name": k, "value": v, "domain": "." + domain, "path": "/"} for k, v in cookies.items()])
    except Exception:
        pass


# A bot check's page, whatever its title (Cloudflare's newer ones show the site's domain): its markers.
BOT_CHECK_JS = """() => {
  const title = (document.title || '').toLowerCase();
  if (/just a moment|attention required|checking your browser|security check/.test(title)) return true;
  const html = document.documentElement ? document.documentElement.innerHTML.slice(0, 60000).toLowerCase() : '';
  return html.includes('performing security verification') || html.includes('cf-chl-')
    || html.includes('challenges.cloudflare.com/turnstile') && html.length < 60000;
}"""


def bot_check_shown(page) -> bool:
    """`page` shows a bot check (Cloudflare's interstitial), not the site."""
    try:
        return page.evaluate(BOT_CHECK_JS) is True
    except Exception:
        return False


def _looks_refused(result: dict) -> bool:
    status = int(result.get("status") or 0)
    if status in _REFUSED:
        return True
    text = (result.get("responseText") or "")[:4000].lower()
    return status == 200 and ("just a moment..." in text or "cf-chl-" in text) and len(text) < 40000


class SiteEnginesMixin:
    """The extra engines' parse_book / parse_chapter for `ExternalScraper`."""

    # -- transport -------------------------------------------------------------------------------

    def _install_engine_transport(self, page) -> None:
        for name, callback in ((TRANSPORT_BINDING, self._engine_transport), (WINDOW_BINDING, self._window_binding)):
            try:
                page.expose_binding(name, callback)
            except Exception as e:
                if "already" not in str(e).lower() and "registered" not in str(e).lower():
                    self.log(f"Engine transport warning: {e}")

    def _queue_job(self, source, kind: str, payload: dict) -> dict:
        """Queue browser work asked for by a page binding; the page waits for {pending: id}."""
        jobs = self.__dict__.setdefault("_engine_jobs", [])
        self.__dict__["_engine_job_id"] = job_id = self.__dict__.get("_engine_job_id", 0) + 1
        page = source.get("page") if isinstance(source, dict) else getattr(source, "page", None)
        jobs.append((job_id, kind, payload, page))
        return {"pending": job_id}

    def _engine_transport(self, source, details):
        """The engines' requests: the app's client first, then the browser when the site refuses it."""
        details = dict(details or {})
        if details.get("op") == "window":
            return self._window_binding(source, details) if source is not None else self._window_script(details)
        reader_get = self.__dict__.get("on_image") is not None and str(details.get("method") or "GET").upper() == "GET"
        if reader_get:
            # Reader chapters run through _pump. Retry there so the binding never starts browser work.
            return self._queue_job(source, "reader-request", details) if source is not None else self._reader_request(details)
        result = self._gm_xmlhttp_request(source, details)
        if isinstance(result, dict) and (result.get("error") or _looks_refused(result)):
            if source is not None:  # inside a page binding: the browser work waits for _pump
                return self._queue_job(source, "request", details)
            page_result = self._browser_request(details)
            if page_result is not None:
                self._saw_response(details, page_result)
                return page_result
        self._saw_response(details, result)
        return result

    def _reader_request(self, details: dict) -> dict:
        """Retry transient reader GETs with a bounded timeout and fetch refused images by their actual URL."""
        from urllib.parse import urlsplit

        work = self.__dict__.get("reader_work")
        if getattr(self, "_stop_requested", False) or (work is not None and not work.wait()):
            return {"error": "the reader was closed"}
        result = self._gm_xmlhttp_request(None, details, timeout_override=_READER_TIMEOUT)
        for attempt in (2, 3):
            timed_out = (result.get("status") in _READER_TIMEOUT_STATUSES
                         or re.search(r"timeout|timed out|ERR_CONNECTION_RESET|connection reset", str(result.get("error") or ""), re.I))
            if not timed_out or getattr(self, "_stop_requested", False) or (work is not None and not work.wait()):
                break
            note = f"Reader request timed out; retrying ({attempt}/3)…"
            self.log(note)
            progress = self.__dict__.get("reader_progress")
            if progress:
                progress(note)
            result = self._gm_xmlhttp_request(None, details, timeout_override=_READER_TIMEOUT)
        if getattr(self, "_stop_requested", False) or (work is not None and not work.wait()):
            return {"error": "the reader was closed"}
        if _looks_refused(_with_text(result)):
            url = details.get("url") or ""
            headers = details.get("headers") or {}
            image = (re.search(r"\.(?:avif|gif|jpe?g|png|webp)$", urlsplit(url).path, re.I)
                     or any(k.lower() == "accept" and "image/" in v for k, v in headers.items()))
            if image and self._context is not None:
                try:
                    response = self._context.request.get(url, headers=headers, timeout=15000)
                    try:
                        if response.ok:
                            import base64

                            result = {"status": response.status, "finalUrl": response.url,
                                      "responseHeaders": "\n".join(f"{k}: {v}" for k, v in response.headers.items()),
                                      "responseBase64": base64.b64encode(response.body()).decode("ascii")}
                    finally:
                        response.dispose()
                except Exception as e:
                    self.log(f"  Reader image browser request failed: {str(e).splitlines()[0][:200]}")
            elif not image:
                result = self._browser_request(details) or result
        self._saw_response(details, result)
        return result

    def _saw_response(self, details: dict, result) -> None:
        """An image the page's engine fetched, to `on_image(url, data address)` when the reader watches
        (HaruNeko hands a chapter's pages over only once all are in; the reader shows each as it comes)."""
        watcher = self.__dict__.get("on_image")
        if watcher is None or not isinstance(result, dict) or not result.get("responseBase64"):
            return
        if int(result.get("status") or 0) >= 400:
            return
        kind = _image_kind(result["responseBase64"])
        if not kind:
            return
        try:
            watcher(str(details.get("url") or ""), f"data:{kind};base64,{result['responseBase64']}")
        except Exception as e:
            self.log(f"  Reader page hand-over failed: {e}")

    def _window_binding(self, source, request):
        """HaruNeko's FetchWindowScript: open an address in a tab and run a script there (queued)."""
        return self._queue_job(source, "window", dict(request or {}))

    def _run_job(self, kind: str, payload: dict):
        if kind == "reader-request":
            return self._reader_request(payload)
        if kind == "request":
            return self._browser_request(payload) or {"error": "the browser could not load the page"}
        return self._window_script(payload)

    def _window_script(self, request: dict) -> dict:
        url, script = request.get("url") or "", request.get("script") or ""
        context = getattr(self, "_context", None)
        if not url or context is None:
            return {"error": "no browser"}
        if not script.strip():
            # HaruNeko's warm-up visit: the app's tabs already carry the site's cookies.
            return {"value": None}
        page = None
        session = None
        temporary = True
        try:
            address = urlsplit(url)
            if address.hostname in ("scribblehub.com", "www.scribblehub.com"):
                # Keep its verified series document for the TOC POST and its existing reader document.
                series = re.fullmatch(r"/series/\d+/", address.path)
                page = next((opened for opened in context.pages
                             if (opened.url.rstrip("/") == url.rstrip("/") or series and opened.url.startswith(url))
                             and opened.locator(".fic_title, .chp_raw").count()), None)
                temporary = page is None
            if page is None:
                page = context.new_page()
            if request.get("preload"):
                page.add_init_script(request["preload"])
            if temporary:
                page.goto(url, wait_until="domcontentloaded", timeout=_BROWSER_FETCH_SECONDS * 1000)
            deadline = time.monotonic() + _BROWSER_FETCH_SECONDS
            while time.monotonic() < deadline:
                title = (page.title() or "").lower()
                if "just a moment" not in title and "attention required" not in title:
                    break
                page.wait_for_timeout(1000)
            page.wait_for_timeout(int(request.get("delay") or 0))
            session = context.new_cdp_session(page)
            # Like Electron's executeJavaScript: statements allowed, the last value returned, promises awaited.
            reply = session.send("Runtime.evaluate", {"expression": script, "awaitPromise": True,
                                                     "returnByValue": True,
                                                     "timeout": int(request.get("timeout") or 60000)})
            if reply.get("exceptionDetails"):
                details = reply["exceptionDetails"]
                return {"error": str((details.get("exception") or {}).get("description") or details.get("text"))[:500]}
            return {"value": (reply.get("result") or {}).get("value")}
        except Exception as e:
            return {"error": str(e).splitlines()[0][:300]}
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
            if page is not None and temporary:
                try:
                    page.close()
                except Exception:
                    pass

    def _pump(self, page, function: str, args: list, timeout: float = 600.0):
        """Call `window[function](...args)` in `page`, running the browser jobs it queues meanwhile."""
        if function.endswith("_parseChapterBatch") or function.endswith("_parseChapter"):
            single = function.removesuffix("Batch")
            browser_chapter_timing(page, single, getattr(self, "chapter_timing", False))
        page.evaluate(_ASYNC_HELPER)
        call_id = page.evaluate("([name, args]) => window.__noveldb_async.start(name, args)", [function, args])
        work = self.__dict__.get("reader_work")
        clock = work.clock if work is not None else time.monotonic
        deadline = clock() + timeout
        while True:
            jobs = self.__dict__.setdefault("_engine_jobs", [])
            if work is not None and not work.wait():
                jobs.clear()
                raise RuntimeError("the reader was closed")
            while jobs:
                if work is not None and not work.wait():
                    jobs.clear()
                    raise RuntimeError("the reader was closed")
                job_id, kind, payload, origin = jobs.pop(0)
                result = self._run_job(kind, payload)
                if kind == "request":
                    self._saw_response(payload, result)
                try:
                    (origin or page).evaluate("([id, value]) => window.__noveldb_async.resolve(id, value)",
                                              [job_id, result])
                except Exception as e:
                    self.log(f"  Could not hand a result back to the page: {e}")
            state = page.evaluate("(id) => window.__noveldb_async.poll(id)", call_id)
            if state:
                if state.get("error"):
                    raise RuntimeError(state["error"])
                return state.get("value")
            if clock() > deadline:
                raise TimeoutError(f"{function} did not finish in {int(timeout)} s")
            if getattr(self, "_stop_requested", False):
                raise RuntimeError("stopped")
            page.wait_for_timeout(40)

    def _site_page(self, url: str):
        """A browser tab on `url`'s site (opened at its home page once, the way a reader arrives)."""
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        pages = self.__dict__.setdefault("_site_pages", {})
        page = pages.get(origin)
        if page is not None:
            try:
                if not page.is_closed() and page.url.startswith(origin):
                    return page
            except Exception:
                pass
            pages.pop(origin, None)
        context = getattr(self, "_context", None)
        if context is None:
            self.start()  # the Python engines run before any page is open
            context = getattr(self, "_context", None)
        page = context.new_page()
        page.goto(origin + "/", wait_until="domcontentloaded", timeout=_BROWSER_FETCH_SECONDS * 1000)
        deadline = time.monotonic() + _BROWSER_FETCH_SECONDS
        # A site's own check page replaces itself once the browser has passed it.
        while time.monotonic() < deadline:
            title = (page.title() or "").lower()
            if "just a moment" not in title and "attention required" not in title:
                break
            page.wait_for_timeout(1000)
        pages[origin] = page
        return page

    def _browser_request(self, details: dict) -> Optional[dict]:
        """Make the request from a tab on the site itself, so it carries what the browser has there."""
        url = details.get("url") or ""
        if not url.startswith(("http://", "https://")):
            return None
        lock = self.__dict__.setdefault("_browser_fetch_lock", threading.Lock())
        with lock:
            try:
                page = self._site_page(url)
                self._give_browser_cookies(page, url, details.get("headers") or {})
                method = str(details.get("method") or "GET").upper()
                result = page.evaluate(_IN_PAGE_FETCH, {
                    "url": url,
                    "method": method,
                    "headers": {k: str(v) for k, v in (details.get("headers") or {}).items()
                                if k.lower() not in _BROWSER_SETS},
                    "body": details.get("data"),
                    "bodyBase64": details.get("dataBase64"),
                })
                raw = True
                if method == "GET" and _looks_refused(_with_text(result)):
                    # The page itself is behind the site's check: open it, as a reader would.
                    result, raw = self._passed_check(page, url, details, result)
                if method == "GET" and not raw and not getattr(self, "_rbooks_chrome", False):
                    # A check the headless browser can't pass (or passes only for the page, not for the
                    # site's own requests): installed Chrome (off-screen, the downloader's profile)
                    # usually does, and the site's later requests use it too.
                    self.log("  The site's bot check stops the headless browser; trying installed Chrome...")
                    self.__dict__.pop("_site_pages", None)  # their tabs belong to the browser being replaced
                    if self._start_rbooks_browser(url, site="Site"):
                        result, raw = self._passed_check(self._site_page(url), url, details, result)
                return result
            except Exception as e:
                self.log(f"  Could not load {url} in the browser: {str(e).splitlines()[0][:200]}")
                return None

    @staticmethod
    def _give_browser_cookies(page, url: str, headers: dict) -> None:
        """A request's own Cookie header (a page fetch can't send one): its cookies set in the browser for
        the site, so the request carries them (an engine's settings cookie, like a list's sort order)."""
        header = next((str(v) for k, v in headers.items() if k.lower() == "cookie"), "")
        pairs = [part.strip().split("=", 1) for part in header.split(";") if "=" in part]
        if not pairs:
            return
        try:
            page.context.add_cookies([{"name": name.strip(), "value": value.strip(), "url": url} for name, value in pairs
                                      if name.strip()])
        except Exception:
            pass

    def _passed_check(self, page, url: str, details: dict, earlier: Optional[dict] = None):
        """`url` opened in `page` past the site's check, then asked for again from there: (result, raw).
        `raw`: the result is the HTML the site sends (a script-built page can differ: a chapter table cut
        to its first 20 rows, a list in another order); otherwise it is the opened page (or `earlier`)."""
        opened = self._open_in_tab(page, url) or earlier
        if not opened or _looks_refused(_with_text(opened)):
            return opened, False
        try:
            again = page.evaluate(_IN_PAGE_FETCH, {
                "url": url, "method": "GET",
                "headers": {k: str(v) for k, v in (details.get("headers") or {}).items()
                            if k.lower() not in _BROWSER_SETS},
                "body": None, "bodyBase64": None,
            })
        except Exception:
            again = None
        if isinstance(again, dict) and not _looks_refused(_with_text(again)):
            return again, True
        return opened, False

    def _open_in_tab(self, page, url: str) -> Optional[dict]:
        response = page.goto(url, wait_until="domcontentloaded", timeout=_BROWSER_FETCH_SECONDS * 1000)
        deadline = time.monotonic() + _BROWSER_FETCH_SECONDS
        while time.monotonic() < deadline and bot_check_shown(page):
            page.wait_for_timeout(1000)
        # The last document load's status: a passed check reloads the page with the real one.
        status = page.evaluate("(performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0")
        if not status:
            status = response.status if response is not None else 200
        html = page.content()
        title = (page.title() or "").lower()
        challenge = ("just a moment" in title or "attention required" in title
                     or "performing security verification" in html.lower()
                     or "cf-chl-" in html.lower())
        # `page.goto()` is the initial 403 challenge response. Cloudflare then replaces it with a
        # successful document, but Chromium can keep the first navigation's status in the timing entry.
        # The final non-challenge document is the response the engine actually receives.
        if status in _REFUSED and not challenge:
            status = 200
        return {"status": status, "statusText": "", "finalUrl": page.url,
                "responseHeaders": "Content-Type: text/html; charset=utf-8", "responseText": html}

    def render_html(self, url: str, scroll: bool = False, real_browser: bool = False) -> Optional[str]:
        """The page as the browser renders it (for sites that build their pages with scripts), or None.
        `scroll`: scrolled to its end first, "Load more" clicked, for lists that load as you scroll.
        `real_browser`: in installed Chrome (off-screen, the downloader's profile) rather than the
        headless one, for sites that send a headless browser other text (bookkor)."""
        lock = self.__dict__.setdefault("_browser_fetch_lock", threading.Lock())
        with lock:
            if real_browser and not getattr(self, "_rbooks_chrome", False):
                self.__dict__.pop("_site_pages", None)  # their tabs belong to the browser being replaced
                if not self._start_rbooks_browser(url, site="Generic"):
                    return None
            try:
                page = self._site_page(url)
                result = self._open_in_tab(page, url)
                if scroll and result:
                    from noveldb.core.engines.generic import SCROLL_TO_END

                    page.evaluate(SCROLL_TO_END)
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline and not page.evaluate("!!window.__noveldbScrolled"):
                        page.wait_for_timeout(700)
                    result["responseText"] = page.content()
            except Exception as e:
                self.log(f"  Could not render {url} in the browser: {str(e).splitlines()[0][:200]}")
                return None
        return (result or {}).get("responseText") or None

    def _browser_fetch(self, url: str) -> Optional[dict]:
        """GET `url` from a browser tab on its site, as a transport result."""
        return self._browser_request({"url": url, "method": "GET"}) if url else None

    def engine_fetch(self, url: str, *, method: str = "GET", data=None, headers: Optional[dict] = None,
                     binary: bool = False, referer: str = "") -> Optional[dict]:
        """One request for the Python engines: {status, url, text | content, headers}, or None."""
        work = self.__dict__.get("reader_work")
        if work is not None and not work.wait():
            return None
        details = {"url": url, "method": method, "headers": dict(headers or {}),
                   # Keep original bytes until the engine reads the page's charset. requests.text
                   # assumes Latin-1 for text/html without a charset and corrupts UTF-8 NBSP.
                   "responseType": "arraybuffer", "timeout": 30000}
        extra = age_cookies(url)
        if extra:
            # An age check's answer goes with the request (it joins the request's own cookies).
            key = next((k for k in details["headers"] if k.lower() == "cookie"), "Cookie")
            have = details["headers"].get(key, "")
            names = {part.split("=", 1)[0].strip() for part in have.split(";") if "=" in part}
            added = "; ".join(f"{k}={v}" for k, v in extra.items() if k not in names)
            if added:
                details["headers"][key] = f"{have}; {added}" if have else added
        if referer:
            details["headers"].setdefault("Referer", referer)
        if data is not None:
            details["data"] = data
        result = self._engine_transport(None, details)
        if not isinstance(result, dict) or result.get("error"):
            return None
        out = {"status": int(result.get("status") or 0), "url": result.get("finalUrl") or url,
               "headers": result.get("responseHeaders") or ""}
        if result.get("responseBase64"):
            import base64

            out["content"] = base64.b64decode(result["responseBase64"])
            from noveldb.core.engines.generic import decode

            out["text"] = decode(out) if not binary else ""
        else:
            out["text"] = result.get("responseText") or ""
            out["content"] = out["text"].encode("utf-8")
        return out

    # -- page-script engines -----------------------------------------------------------------

    def _script_inject(self, page, script: PageScript) -> bool:
        from noveldb.core import engine_updates

        self._install_bridge_bindings(page)
        self._install_engine_transport(page)
        if script.engine == "comix":
            from noveldb.core.engines import comix

            try:
                page.evaluate(comix.script())
                page.evaluate("""() => {
                    window.__COMIX_transport = async request => {
                        const result = await window.__noveldb_window_script(request);
                        return result.pending ? window.__noveldb_async.wait(result.pending) : result;
                    };
                }""")
            except Exception as e:
                self.log(f"{script.label} failed to load: {e}")
                return False
            self.__dict__.setdefault("_script_labels", {})[script.engine] = "built in"
            return True
        engine = getattr(engine_updates, script.bundle)
        bundle = self._load_engine_bundle(
            page, engine,
            "() => ({ready: window.%s_READY === true && window.%s_API === %d,"
            " error: (window.%s_LOAD_ERROR || {}).message || null})"
            % (script.prefix, script.prefix, engine.api, script.prefix),
        )
        if bundle is None:
            return False
        self.__dict__.setdefault("_script_labels", {})[script.engine] = bundle.label
        return True

    def _script_parse_book(self, engine: str, url: str) -> Optional[dict]:
        script = PAGE_SCRIPTS[engine]
        page = self._page
        if page is None:
            return None
        if (engine == "lnreader" and urlsplit(url).hostname in ("scribblehub.com", "www.scribblehub.com")
                and page.context == self._context):
            # Keep parser bindings in their own context so the site's documents retain their browser session.
            self._page = page = self._browser.new_context().new_page()
        self.log(f"Injecting the {script.label} engine...")
        if not self._script_inject(page, script):
            return None
        try:
            raw = self._pump(page, script.prefix + "_parseBook", [url])
            data = json.loads(raw or "null")
        except Exception as e:
            self.log(f"{script.label}: parsing the book failed: {e}")
            return None
        if not isinstance(data, dict):
            self.log(f"{script.label}: the book page gave no result.")
            return None
        if "error" in data:
            self.log(f"{script.label}: {data['error']}")
            if data.get("stack") and not data.get("noParser"):
                self.log(f"  Stack: {str(data['stack'])[:300]}")
            return None
        data[script.flag] = True
        self._book_data = data
        self._book_url = url
        label = self.__dict__.get("_script_labels", {}).get(engine, "")
        self.log(
            f"Book: {data.get('bookname') or '?'} by {data.get('author') or '?'} "
            f"— {data.get('chapterCount', 0)} chapters ({data.get('ruleName', script.label)}, {label})"
        )
        for warning in data.get("warnings") or []:
            self.log(f"  {script.label}: {warning}")
        return data

    def _script_for_book(self) -> Optional[PageScript]:
        data = self._book_data or {}
        for script in PAGE_SCRIPTS.values():
            if data.get(script.flag):
                return script
        return None

    @staticmethod
    def _script_item(chapter_info: dict) -> dict:
        item = {key: chapter_info.get(key) for key in ("url", "path", "plugin", "id", "mangaId", "bookUrl")
                if chapter_info.get(key)}
        item["name"] = chapter_info.get("fullName") or chapter_info.get("name", "")
        return item

    @staticmethod
    def _script_result(raw):
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError) as e:
            return None, f"bad result: {e}"
        if not isinstance(data, dict):
            return None, "empty result"
        if "error" in data:
            return None, data["error"]
        return data, ""

    def _script_page_ready(self, page, script: PageScript) -> bool:
        try:
            if page.evaluate("window.%s_READY === true" % script.prefix):
                return True
        except Exception:
            pass
        return self._script_inject(page, script)

    def _script_parse_chapter(self, script: PageScript, index, chapter_info, interval=0.5, page=None,
                              log_errors=True, interval_max=None):
        target = page or self._page
        if target is None and not self._ensure_page():
            if log_errors:
                self.log(f"  [{index + 1}] No browser page available.")
            return None
        target = target or self._page
        if not self._script_page_ready(target, script):
            return None
        error = "empty result"
        try:
            raw = self._pump(target, script.prefix + "_parseChapter",
                             [json.dumps(self._script_item(chapter_info), ensure_ascii=False)], timeout=300)
        except Exception as e:
            raw = None
            error = str(e)
        self._sleep_interval(interval, interval_max)
        if raw is None:
            if log_errors:
                self.log(f"  [{index + 1}] Parse error: {error}")
            return None
        data, error = self._script_result(raw)
        if data is None and log_errors:
            self.log(f"  [{index + 1}] {script.label}: {error}")
        return data

    def _script_parse_chapter_batch(self, script: PageScript, batch_info, report_success: Callable):
        if not self._ensure_page() or not self._script_page_ready(self._page, script):
            self.log("  Batch aborted: no browser page available.")
            return [None] * len(batch_info)
        items = [self._script_item(chapter) for chapter in batch_info]
        try:
            raw = self._pump(self._page, script.prefix + "_parseChapterBatch",
                             [json.dumps(items, ensure_ascii=False)], timeout=300 + 60 * len(items))
            results = json.loads(raw or "[]")
        except Exception as e:
            self.log(f"  Batch parse error: {e}")
            return [None] * len(batch_info)
        if not isinstance(results, list):
            return [None] * len(batch_info)
        parsed = []
        for index, entry in enumerate(results):
            data, _error = self._script_result(entry)
            parsed.append(data)
            report_success(index, data)
        return (parsed + [None] * len(batch_info))[:len(batch_info)]

    # -- Python engines ------------------------------------------------------------------------

    def _python_engine(self, engine: str):
        engines = self.__dict__.setdefault("_python_engines", {})
        if engine.startswith("custom:"):
            # A user's engine file: one engine for the whole download, made again when the file changes.
            from noveldb.core.engines import custom_engines
            stamp = custom_engines.stamp(engine)
            cached = engines.get(engine)
            if not cached or cached[0] != stamp:
                engines[engine] = (stamp, custom_engines.load(engine, fetch=self.engine_fetch, log=self.log,
                                                               render=self.render_html))
            return engines[engine][1]
        if engine not in engines:
            if engine == ENGINE_LNC:
                from noveldb.core.engines.lncrawl_engine import LncrawlEngine
                engines[engine] = LncrawlEngine(fetch=self.engine_fetch, log=self.log, render=self.render_html)
            elif engine == ENGINE_GDL:
                from noveldb.core.engines.gallerydl_engine import GalleryDlEngine
                engines[engine] = GalleryDlEngine(fetch=self.engine_fetch, log=self.log)
            elif engine == ENGINE_FFF:
                from noveldb.core.engines.fanficfare_engine import FanFicFareEngine
                engines[engine] = FanFicFareEngine(fetch=self.engine_fetch, log=self.log)
            elif engine == ENGINE_MANGAPEAK:
                from noveldb.core.engines.mangapeak import MangaPeakEngine
                engines[engine] = MangaPeakEngine(fetch=self.engine_fetch, log=self.log)
            elif engine == "mangak":
                from noveldb.core.engines.mangak import MangaKEngine
                engines[engine] = MangaKEngine(fetch=self.engine_fetch)
            elif engine == ENGINE_TAKO:
                from noveldb.core.engines.tako import TakoEngine
                engines[engine] = TakoEngine(fetch=self.engine_fetch, log=self.log)
            else:
                from noveldb.core.engines.generic import GenericEngine
                engines[engine] = GenericEngine(fetch=self.engine_fetch, log=self.log, render=self.render_html)
        return engines[engine]

    def _python_parse_book(self, engine: str, url: str) -> Optional[dict]:
        try:
            runner = self._python_engine(engine)
        except Exception as e:  # a missing dependency in this build
            self.log(f"{engine}: not available in this build ({e}).")
            return None
        if not runner.knows(url):
            return None
        try:
            data = runner.parse_book(url)
        except Exception as e:
            self.log(f"{runner.label}: {e}")
            return None
        if not data:
            return None
        if engine in PYTHON_FLAGS:
            data[PYTHON_FLAGS[engine]] = True
        else:
            data["_custom_engine"] = engine  # its chapters go back to the same custom engine
        self._book_data = data
        self._book_url = url
        self.log(
            f"Book: {data.get('bookname') or '?'} by {data.get('author') or '?'} "
            f"— {data.get('chapterCount', len(data.get('chapters') or []))} chapters ({data.get('ruleName', runner.label)})"
        )
        for warning in data.get("warnings") or []:
            self.log(f"  {runner.label}: {warning}")
        return data

    def _python_engine_for_book(self) -> Optional[str]:
        data = self._book_data or {}
        if data.get("_custom_engine"):
            return data["_custom_engine"]
        for engine, flag in PYTHON_FLAGS.items():
            if data.get(flag):
                return engine
        return None

    @timed_chapter
    def _python_parse_chapter(self, engine: str, index, chapter_info, interval=0.5, log_errors=True,
                              interval_max=None):
        runner = self._python_engine(engine)
        started = time.perf_counter()
        try:
            data = runner.parse_chapter(chapter_info, self._book_data or {})
        except Exception as e:
            data = None
            if log_errors:
                self.log(f"  [{index + 1}] {runner.label}: {e}")
        if isinstance(data, dict) and getattr(self, "chapter_timing", False):
            data["_scrape_seconds"] = time.perf_counter() - started
        self._sleep_interval(interval, interval_max)
        return data
