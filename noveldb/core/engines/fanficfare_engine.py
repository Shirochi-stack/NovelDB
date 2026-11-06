"""FanFicFare's site adapters as a site engine (https://github.com/JimmXinu/FanFicFare, Apache-2.0).

FanFicFare (the `fanficfare` package) knows 100+ fan-fiction archives and forums. Its adapters fetch
pages themselves; this reads a story's metadata and chapter list, then each chapter's text, and
returns them in the shapes the other engines use. FanFicFare's own image handling and EPUB writers are
not used: chapter images stay absolute `<img src>` addresses for the app's image downloader.
"""

from __future__ import annotations

import re
import threading
from io import StringIO
from typing import Callable, Optional

LABEL = "FanFicFare"


_bundle_lock = __import__("threading").Lock()
_bundle_folder = None


def _use_bundle() -> None:
    """Import FanFicFare from the bundle in use (fanficfare/fanficfare.zip, or a downloaded update), unpacked
    once; the copy the app was built with stays the fallback. A bundle that fails to import is set aside."""
    global _bundle_folder
    import sys
    import zipfile
    from pathlib import Path

    from noveldb.core import engine_updates, paths

    with _bundle_lock:
        if _bundle_folder is not None or "fanficfare" in sys.modules:
            return
        _bundle_folder = ""
        bundle = engine_updates.current(engine_updates.FANFICFARE)
        if bundle is None:
            return
        folder = paths.cache_dir() / "fanficfare-runtime" / str(bundle.manifest.get("sha256") or "")[:16]
        try:
            if not (folder / "fanficfare" / "__init__.py").is_file():
                temp = folder.with_name(folder.name + ".tmp")
                with zipfile.ZipFile(bundle.path) as archive:
                    for name in archive.namelist():
                        if name.startswith("/") or ".." in Path(name).parts or not name.startswith("fanficfare/"):
                            raise ValueError(f"unexpected name in the FanFicFare bundle: {name}")
                    archive.extractall(temp)
                temp.rename(folder)
            sys.path.insert(0, str(folder))
            import fanficfare.adapters  # noqa: F401  (fails here, not mid-download, if the bundle is broken)
            _bundle_folder = str(folder)
        except Exception as e:
            if str(folder) in sys.path:
                sys.path.remove(str(folder))
            for name in [n for n in sys.modules if n.split(".")[0] == "fanficfare"]:
                del sys.modules[name]
            if bundle.updated:
                engine_updates.reject(bundle, str(e))


def _inert_cloudscraper() -> None:
    """FanFicFare imports cloudscraper (a Cloudflare workaround) for a setting NovelDB keeps off. The
    built app leaves that package out (desktop_build/noveldb.spec), so FanFicFare gets a stand-in that
    refuses to work instead."""
    import sys
    import types

    if "cloudscraper" in sys.modules:
        return
    try:
        import cloudscraper  # noqa: F401  (a development install has it; it stays unused)
        return
    except ImportError:
        pass
    module = types.ModuleType("cloudscraper")
    exceptions = types.ModuleType("cloudscraper.exceptions")

    class CloudflareException(Exception):
        pass

    def refuse(*_args, **_kwargs):
        raise RuntimeError("NovelDB does not include cloudscraper")

    exceptions.CloudflareException = CloudflareException
    module.exceptions = exceptions
    module.CloudScraper = module.CipherSuiteAdapter = module.create_scraper = refuse
    sys.modules["cloudscraper"] = module
    sys.modules["cloudscraper.exceptions"] = exceptions


def _prepare() -> None:
    """Before FanFicFare is imported: the cloudscraper stand-in, then the bundle in use."""
    _inert_cloudscraper()
    _use_bundle()


def knows(url: str) -> bool:
    """FanFicFare has an adapter for the URL's site (False when the package is missing)."""
    try:
        _prepare()
        from fanficfare import adapters
    except Exception:
        return False
    try:
        return adapters._get_class_for(url)[0] is not None
    except Exception:
        return False


def _fetcher_class():
    """A FanFicFare fetcher that sends its requests through the app (see scraper_mixin.engine_fetch)."""
    _prepare()
    from fanficfare import exceptions
    from fanficfare.fetchers.base_fetcher import Fetcher, FetcherResponse

    class NovelDBFetcher(Fetcher):
        def __init__(self, getConfig_fn, getConfigList_fn, fetch):
            super().__init__(getConfig_fn, getConfigList_fn)
            self._fetch = fetch

        def request(self, method, url, headers=None, parameters=None, json=None):
            if method not in ("GET", "POST"):
                raise NotImplementedError()
            headers = dict(headers or {})
            headers.pop("User-Agent", None)  # the app's own client says who it is
            cookie = self._jar_cookies(url)
            if cookie and not any(name.lower() == "cookie" for name in headers):
                headers["Cookie"] = cookie  # what the adapter set (scribblehub's oldest-first contents)
            data = parameters
            if json is not None:
                import json as json_module

                data = json_module.dumps(json)
                headers["Content-Type"] = "application/json"
            elif isinstance(parameters, dict):
                from urllib.parse import urlencode

                data = urlencode(parameters, doseq=True)
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            result = self._fetch(url, method=method, data=data, headers=headers,
                                 binary=headers.get("Accept") == "image/*")
            if result is None:
                raise exceptions.HTTPErrorFFF(url, 0, "the request failed")
            if result["status"] >= 400:
                raise exceptions.HTTPErrorFFF(url, result["status"], f"HTTP {result['status']}", result["content"])
            return FetcherResponse(result["content"], result["url"], False, None)

        def _jar_cookies(self, url):
            """The cookies FanFicFare's own jar holds for `url` (adapters set some, like a sort order)."""
            if self.cookiejar is None:
                return ""
            try:
                import urllib.request

                probe = urllib.request.Request(url)
                self.cookiejar.add_cookie_header(probe)
                return probe.get_header("Cookie") or ""
            except Exception:
                return ""

    return NovelDBFetcher


def _configuration(url: str):
    from importlib import resources

    _prepare()
    from fanficfare import adapters
    from fanficfare.configurable import Configuration

    configuration = Configuration(adapters.getConfigSectionsFor(url), "html")
    defaults = resources.files("fanficfare").joinpath("defaults.ini").read_text(encoding="utf-8")
    configuration.read_file(StringIO(defaults))
    try:
        configuration.add_section("overrides")
    except Exception:
        pass
    # The app downloads images itself and has no use for FanFicFare's title page or cover art.
    for key, value in (("include_images", "false"), ("never_make_cover", "true"),
                       ("include_titlepage", "false"), ("include_tocpage", "false"),
                       ("continue_on_chapter_error", "false"), ("is_adult", "true")):
        configuration.set("overrides", key, value)
    return configuration


def _plain(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "tr", "hr"]):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = (re.sub(r"\s+", " ", line).strip() for line in soup.get_text().split("\n"))
    return "\n".join(line for line in lines if line)


class FanFicFareEngine:
    label = LABEL

    def __init__(self, fetch: Callable = None, log: Callable[[str], None] = lambda _m: None):
        _prepare()
        import fanficfare  # noqa: F401  (fails early when the package is missing)

        self.fetch = fetch
        self.log = log
        self._adapters = {}
        self._lock = threading.Lock()

    @staticmethod
    def knows(url: str) -> bool:
        return knows(url)

    def _adapter(self, url: str):
        from fanficfare import adapters

        with self._lock:
            adapter = self._adapters.get(url)
            if adapter is None:
                configuration = _configuration(url)
                if self.fetch is not None:
                    configuration.fetcher = _fetcher_class()(configuration.getConfig, configuration.getConfigList,
                                                             self.fetch)
                adapter = adapters.getAdapter(configuration, url)
                covers = []

                def cover(_story_url, image_url):
                    covers.append(image_url)
                    return (None, None)

                adapter.setCoverImage = cover
                adapter._noveldb_covers = covers
                self._adapters[url] = adapter
            return adapter

    def parse_book(self, url: str) -> Optional[dict]:
        adapter = self._adapter(url)
        story = adapter.getStoryMetadataOnly(get_cover=True)
        meta = story.getMetadata
        chapters = []
        for index, chapter in enumerate(adapter.get_chapters()):
            chapters.append({
                "url": chapter.get("url", ""),
                "name": (chapter.get("title") or f"Chapter {index + 1}").strip(),
                "number": index + 1,
                "bookUrl": url,
                "isVIP": False,
                "isPaid": False,
            })
        tags = []
        for key in ("genre", "category", "characters", "ships", "freeformtags", "warnings"):
            try:
                tags.extend(item.strip() for item in story.getList(key) if item and item.strip())
            except Exception:
                pass
        covers = getattr(adapter, "_noveldb_covers", [])
        description = meta("description") or ""
        return {
            "engine": "fanficfare",
            "ruleName": f"FanFicFare {adapter.getSiteDomain()}",
            "bookUrl": adapter.url or url,
            "bookname": meta("title") or "",
            "author": meta("author") or "",
            "introduction": _plain(description) if "<" in description else description.strip(),
            "introductionHTML": "",
            "coverUrl": covers[0] if covers else None,
            "tags": list(dict.fromkeys(tags))[:40],
            "status": meta("status") or "",
            "language": meta("language") or "",
            "chapterCount": len(chapters),
            "chapters": chapters,
        }

    def parse_chapter(self, chapter_info: dict, book: dict) -> Optional[dict]:
        book_url = chapter_info.get("bookUrl") or book.get("bookUrl") or ""
        adapter = self._adapter(book_url)
        if not adapter.metadataDone:
            adapter.getStoryMetadataOnly(get_cover=False)
        url = chapter_info.get("url", "")
        index = max(0, int(chapter_info.get("number") or 1) - 1)
        known = [chapter.get("url") for chapter in adapter.get_chapters()]
        if url in known:
            index = known.index(url)
        html = adapter.getChapterTextNum(url, index)
        if html is None:
            return None
        html = str(html)
        text = _plain(html)
        if not text and "<img" not in html:
            raise ValueError("the chapter has no visible text")
        return {
            "chapterName": chapter_info.get("fullName") or chapter_info.get("name", ""),
            "contentHtml": html,
            "contentText": text,
            "imageCount": 0,
            "images": [],
        }
