"""The mobile apps' Python engines: the desktop's engine modules, run by Pyodide in a hidden page.

lightnovel-crawler, FanFicFare and gallery-dl run as they do on the desktop (noveldb/core/engines/
lncrawl_engine.py, fanficfare_engine.py, gallerydl_engine.py, copied here unchanged by
scripts/fetch_python_host.py). What differs is around them:
- Every request goes to the app: `fetch` calls `NovelDBSyncTransport(json)` in the page, which asks
  the app synchronously (the app answers a request to the page's own `transport` address), so the
  engines' blocking calls work. `requests` (gallery-dl's sessions) is routed through it too.
- The app modules the engines import (`noveldb.core.engine_updates`, `paths`, `catalog.sites`)
  are small stand-ins: the bundles are the ones the app put in /bundles.

The page calls `parse_book(engine, url)` and `parse_chapter(engine, chapter_json)`, which return
the novel-downloader bridge's shapes as JSON text, or {error}.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import types
from pathlib import Path

BUNDLES = Path("/bundles")
CACHE = Path("/tmp/noveldb-cache")


# -- the network ------------------------------------------------------------------------------------

def _transport(request: dict) -> dict:
    from js import NovelDBSyncTransport  # the page's synchronous call to the app

    try:
        return json.loads(str(NovelDBSyncTransport(json.dumps(request))))
    except Exception as error:  # the app gave no answer
        return {"error": str(error)[:200]}


def fetch(url, method="GET", data=None, headers=None, binary=False, referer="", timeout=30.0, quick=False, **_kwargs):
    """The engines' fetch: {status, url, content, text, headers} or None (the desktop's engine_fetch shape)."""
    sent = {str(k): str(v) for k, v in (headers or {}).items()
            if str(k).lower() not in ("host", "content-length", "accept-encoding", "connection")}
    if referer and not any(k.lower() == "referer" for k in sent):
        sent["Referer"] = referer
    request = {"url": str(url), "method": str(method or "GET").upper(), "headers": sent,
               "responseType": "arraybuffer", "timeout": int((8 if quick else float(timeout or 30)) * 1000)}
    if data is not None:
        body = data if isinstance(data, bytes) else str(data).encode("utf-8")
        request["dataBase64"] = base64.b64encode(body).decode("ascii")
    reply = _transport(request)
    if not reply or reply.get("error"):
        return None
    content = base64.b64decode(reply.get("responseBase64") or "") if reply.get("responseBase64") else \
        str(reply.get("responseText") or "").encode("utf-8")
    header_text = str(reply.get("responseHeaders") or "").replace("\r\n", "\n")
    text = ""
    if not binary:
        found = re.search(r"charset=([\w-]+)", header_text, re.I)
        try:
            text = content.decode(found.group(1) if found else "utf-8", "replace")
        except LookupError:
            text = content.decode("utf-8", "replace")
    return {"status": int(reply.get("status") or 0), "url": reply.get("finalUrl") or url, "content": content,
            "text": text, "headers": header_text}


def _route_requests() -> None:
    """`requests` has no sockets here: every adapter sends through `fetch`."""
    import requests
    from requests.adapters import HTTPAdapter
    from requests.structures import CaseInsensitiveDict

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        seconds = timeout[0] if isinstance(timeout, tuple) else timeout
        result = fetch(request.url, method=request.method, data=request.body, headers=dict(request.headers),
                       binary=True, timeout=seconds or 30)
        if result is None:
            raise requests.ConnectionError(f"the app could not load {request.url}", request=request)
        response = requests.Response()
        response.status_code = result["status"]
        response._content = result["content"]
        headers = CaseInsensitiveDict()
        for line in result["headers"].splitlines():
            name, _, value = line.partition(":")
            if name.strip():
                headers[name.strip()] = value.strip()
        response.headers = headers
        response.url = result["url"]
        response.encoding = requests.utils.get_encoding_from_headers(headers)
        response.reason = ""
        response.request = request
        response.connection = self
        return response

    HTTPAdapter.send = send


# -- the app modules the engines import ---------------------------------------------------------------

class _Engine:
    def __init__(self, name: str) -> None:
        self.name = name


class _Bundle:
    def __init__(self, path: Path, manifest: dict) -> None:
        self.path = path
        self.manifest = manifest
        self.updated = False


def _current(engine) -> object:
    folder = BUNDLES / engine.name
    manifest_file = folder / "manifest.json"
    if not manifest_file.is_file():
        return None
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    path = folder / manifest.get("file", "")
    return _Bundle(path, manifest) if path.is_file() else None


_SEPARATORS = re.compile(r"\s+[-–—|·•]\s+|\s*\|\s*")


def _clean_page_title(title: str, url: str) -> str:
    """noveldb.core.catalog.sites.clean_page_title, without the site table: segments naming the site go."""
    from urllib.parse import urlsplit

    text = re.sub(r"\s+", " ", title or "").strip()
    if not text:
        return ""
    labels = [p for p in (urlsplit(url).hostname or "").lower().removeprefix("www.").split(".") if p]
    label = labels[-2] if len(labels) >= 2 else ""
    parts = [part.strip() for part in _SEPARATORS.split(text) if part.strip()]
    kept = [part for part in parts if not ((len(label) >= 3 and label in part.lower())
                                           or part.lower() in ("home", "official site"))]
    return (" - ".join(kept) if kept else text)[:200]


def _refuse(*_args, **_kwargs):
    raise RuntimeError("not available in the mobile apps")


def _unavailable_class(name: str) -> type:
    if name.startswith("__"):
        raise AttributeError(name)
    return type(name, (Exception,), {"__init__": _refuse})


def _install_stand_ins() -> None:
    def module(name: str, **values) -> types.ModuleType:
        found = sys.modules.get(name) or types.ModuleType(name)
        found.__dict__.update(values)
        sys.modules[name] = found
        return found

    module("noveldb", __path__=["/app/noveldb"])
    core = module("noveldb.core", __path__=["/app/noveldb/core"])
    updates = module("noveldb.core.engine_updates", current=_current, reject=lambda *_a, **_k: None,
                     LNCRAWL=_Engine("lncrawl"), FANFICFARE=_Engine("fanficfare"), GALLERYDL=_Engine("gallerydl"))
    paths = module("noveldb.core.paths", cache_dir=lambda: CACHE)
    core.engine_updates = updates
    core.paths = paths
    catalog = module("noveldb.core.catalog", __path__=[])
    catalog.sites = module("noveldb.core.catalog.sites", clean_page_title=_clean_page_title)
    # FanFicFare imports apsw for reading a desktop browser's cache, which the apps never do.
    # Any of its names is a class that refuses to be used (FanFicFare subclasses some at import).
    if "apsw" not in sys.modules:
        module("apsw", __getattr__=_unavailable_class)


# -- the page's calls ---------------------------------------------------------------------------------

_engines: dict = {}
_books: dict = {}  # engine name -> {book address: book}, so a chapter keeps what its book read


def _engine(name: str):
    if name not in _engines:
        if name == "lncrawl":
            from noveldb.core.engines.lncrawl_engine import LncrawlEngine as Engine
        elif name == "fanficfare":
            from noveldb.core.engines.fanficfare_engine import FanFicFareEngine as Engine
        elif name == "gallerydl":
            from noveldb.core.engines.gallerydl_engine import GalleryDlEngine as Engine
        else:
            raise ValueError(f"no engine {name}")
        _engines[name] = Engine(fetch=fetch)
    return _engines[name]


def _error(error: BaseException, **extra) -> str:
    return json.dumps(dict({"error": f"{type(error).__name__}: {error}"[:500]}, **extra))


def knows(name: str, url: str) -> bool:
    try:
        return bool(_engine(name).knows(url))
    except Exception:
        return False


def parse_book(name: str, url: str) -> str:
    try:
        engine = _engine(name)
        if not engine.knows(url):
            return json.dumps({"error": f"{name} has no source for this site", "noParser": True})
        book = engine.parse_book(url)
    except Exception as error:
        return _error(error)
    if not book or not book.get("chapters"):
        return json.dumps({"error": f"{name} found no chapters on this page", "noParser": True})
    known = _books.setdefault(name, {})
    known[book.get("bookUrl") or url] = book
    known[url] = book
    while len(known) > 12:
        known.pop(next(iter(known)))
    book["pageUrl"] = book.get("bookUrl") or url
    return json.dumps(book, ensure_ascii=False, default=str)


def parse_chapter(name: str, chapter_json: str) -> str:
    try:
        asked = json.loads(chapter_json)
        engine = _engine(name)
        books = _books.get(name, {})
        book = books.get(asked.get("bookUrl") or "") or next(
            (b for b in books.values() if any(c.get("url") == asked.get("url") for c in b.get("chapters", []))), None)
        if book is None and asked.get("bookUrl"):
            json.loads(parse_book(name, asked["bookUrl"]))
            book = books.get(asked["bookUrl"])
        book = book or {"bookUrl": asked.get("bookUrl") or ""}
        # The chapter as the book listed it (lightnovel-crawler keeps its own data on it).
        info = next((c for c in book.get("chapters", []) if c.get("url") == asked.get("url")), None) or asked
        result = engine.parse_chapter(dict(info, name=asked.get("name") or info.get("name", "")), book)
    except Exception as error:
        return _error(error)
    if not result:
        return json.dumps({"error": "the chapter could not be read"})
    return json.dumps(result, ensure_ascii=False, default=str)


def start() -> str:
    """Sets the stand-ins up and routes requests; the page calls it once Pyodide has the files."""
    CACHE.mkdir(parents=True, exist_ok=True)
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")
    _install_stand_ins()
    _route_requests()
    return "ready"
