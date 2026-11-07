"""Reading a book from the Catalog's generic search without downloading it: its chapter list, then
one chapter at a time, scraped when the reader asks for it.

The Python engines read the book (lightnovel-crawler, then FanFicFare, for the sites they know, then
the generic fallback for any other site); sites with an LNReader plugin are read by the page itself.
Books stay in memory while they are being read (the last few), so turning pages doesn't re-read the
chapter list. No Qt here: the calls block, so the app makes them on worker threads.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from copy import copy
from typing import Callable, List, Optional

KEEP_BOOKS = 6


class LiveReader:
    def __init__(self, fetch: Callable, render: Optional[Callable] = None,
                 log: Callable[[str], None] = lambda _m: None):
        self.fetch = fetch
        self.render = render
        self.log = log
        self._books: "OrderedDict[str, tuple]" = OrderedDict()
        self._engines: dict = {}
        self._lock = threading.Lock()

    def _engine(self, name: str):
        with self._lock:
            if name not in self._engines:
                if name == "lncrawl":
                    from noveldb.core.engines.lncrawl_engine import LncrawlEngine
                    self._engines[name] = LncrawlEngine(fetch=self.fetch, log=self.log, render=self.render)
                elif name == "gallerydl":
                    from noveldb.core.engines.gallerydl_engine import GalleryDlEngine
                    self._engines[name] = GalleryDlEngine(fetch=self.fetch, log=self.log)
                elif name == "fanficfare":
                    from noveldb.core.engines.fanficfare_engine import FanFicFareEngine
                    self._engines[name] = FanFicFareEngine(fetch=self.fetch, log=self.log)
                else:
                    from noveldb.core.engines.generic import GenericEngine
                    self._engines[name] = GenericEngine(fetch=self.fetch, log=self.log, render=self.render, parallel=True)
            return self._engines[name]

    def _candidates(self, url: str) -> List[str]:
        names = []
        try:
            from noveldb.core.engines import lncrawl_engine
            if lncrawl_engine.knows(url):
                names.append("lncrawl")
        except Exception:
            pass
        try:
            from noveldb.core.engines import fanficfare_engine
            if fanficfare_engine.knows(url):
                names.append("fanficfare")
        except Exception:
            pass
        try:
            from noveldb.core.engines import gallerydl_engine
            if gallerydl_engine.knows(url):
                names.append("gallerydl")
        except Exception:
            pass
        return names + ["generic"]

    @staticmethod
    def _local(url: str):
        """A Library EPUB (a file: address), or None."""
        if not url.startswith("file:"):
            return None
        from urllib.parse import unquote, urlsplit
        from pathlib import Path

        path = Path(unquote(urlsplit(url).path).lstrip("/") if __import__("os").name == "nt" else unquote(urlsplit(url).path))
        return path if path.suffix.lower() == ".epub" and path.is_file() else None

    @staticmethod
    def _chapter_names(chapters, start=0):
        return [{"name": chapter.get("name") or f"Chapter {index + 1}",
                 "section": chapter.get("sectionName") or ""}
                for index, chapter in enumerate(chapters, start)]

    def _remember(self, url, name, data):
        with self._lock:
            self._books[url] = (name, data)
            self._books.move_to_end(url)
            while len(self._books) > KEEP_BOOKS:
                self._books.popitem(last=False)

    def book(self, url: str, *, on_chapters=None, active=lambda: True) -> dict:
        """{title, author, engine, chapters: [{name, section}]}; raises when no engine can read it."""
        local = self._local(url)
        if local is not None:
            from noveldb.core import library

            contents = library.epub_contents(local)
            with self._lock:
                self._books[url] = ("epub", contents)
            return {"title": contents["title"], "author": contents["author"], "engine": "EPUB",
                    "chapters": [{"name": c["name"], "section": ""} for c in contents["chapters"]]}
        errors = []
        for name in self._candidates(url):
            if not active():
                raise RuntimeError("the reader was closed")
            published = 0

            def publish(chapters):
                nonlocal published
                if not active():
                    return
                self._remember(url, name, {"chapters": chapters, "bookUrl": chapters[0]["bookUrl"]})
                on_chapters(self._chapter_names(chapters[published:], published))
                published = len(chapters)

            try:
                engine = self._engine(name)
                if name == "generic" and on_chapters is not None:
                    data = engine.parse_book(url, on_chapters=publish, active=active)
                else:
                    data = engine.parse_book(url)
            except Exception as e:  # a missing dependency in this build, or the site refused
                if published:
                    raise
                errors.append(f"{name}: {e}")
                continue
            if data and data.get("chapters"):
                if not active():
                    raise RuntimeError("the reader was closed")
                self._remember(url, name, data)
                return {
                    "title": data.get("bookname") or "",
                    "author": data.get("author") or "",
                    "engine": data.get("ruleName") or name,
                    "chapters": self._chapter_names(data["chapters"]),
                }
            errors.append(f"{name}: no chapters found")
        raise RuntimeError("; ".join(errors)[:400] or "no engine could read the book")

    def chapter(self, url: str, index: int, on_images: Optional[Callable[[List[str], int], None]] = None) -> dict:
        """{name, html} of the book's chapter at `index` (0-based), scraped now. A manga chapter's images
        reach `on_images(images so far, pages)` as its pages are read."""
        with self._lock:
            known = self._books.get(url)
        if known is None:
            self.book(url)
            with self._lock:
                known = self._books.get(url)
        name, data = known
        chapters = data["chapters"]
        if name == "epub":
            from noveldb.core import library

            return library.epub_chapter(self._local(url), chapters[index]["href"])
        if not 0 <= index < len(chapters):
            raise IndexError("no such chapter")
        info = chapters[index]
        engine = self._engine(name)
        if on_images is not None and hasattr(engine, "on_images"):
            engine = copy(engine)
            engine.on_images = on_images
        result = engine.parse_chapter(info, data)
        if not result:
            raise RuntimeError("the chapter could not be read")
        return {"name": result.get("chapterName") or info.get("name") or "",
                "html": result.get("contentHtml") or "",
                "text": result.get("contentText") or ""}
