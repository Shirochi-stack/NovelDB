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
                elif name == "fanficfare":
                    from noveldb.core.engines.fanficfare_engine import FanFicFareEngine
                    self._engines[name] = FanFicFareEngine(fetch=self.fetch, log=self.log)
                else:
                    from noveldb.core.engines.generic import GenericEngine
                    self._engines[name] = GenericEngine(fetch=self.fetch, log=self.log, render=self.render)
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
        return names + ["generic"]

    def book(self, url: str) -> dict:
        """{title, author, engine, chapters: [{name, section}]}; raises when no engine can read it."""
        errors = []
        for name in self._candidates(url):
            try:
                engine = self._engine(name)
                data = engine.parse_book(url)
            except Exception as e:  # a missing dependency in this build, or the site refused
                errors.append(f"{name}: {e}")
                continue
            if data and data.get("chapters"):
                with self._lock:
                    self._books[url] = (name, data)
                    self._books.move_to_end(url)
                    while len(self._books) > KEEP_BOOKS:
                        self._books.popitem(last=False)
                return {
                    "title": data.get("bookname") or "",
                    "author": data.get("author") or "",
                    "engine": data.get("ruleName") or name,
                    "chapters": [{"name": chapter.get("name") or f"Chapter {index + 1}",
                                  "section": chapter.get("sectionName") or ""}
                                 for index, chapter in enumerate(data["chapters"])],
                }
            errors.append(f"{name}: no chapters found")
        raise RuntimeError("; ".join(errors)[:400] or "no engine could read the book")

    def chapter(self, url: str, index: int) -> dict:
        """{name, html} of the book's chapter at `index` (0-based), scraped now."""
        with self._lock:
            known = self._books.get(url)
        if known is None:
            self.book(url)
            with self._lock:
                known = self._books.get(url)
        name, data = known
        chapters = data["chapters"]
        if not 0 <= index < len(chapters):
            raise IndexError("no such chapter")
        info = chapters[index]
        result = self._engine(name).parse_chapter(info, data)
        if not result:
            raise RuntimeError("the chapter could not be read")
        return {"name": result.get("chapterName") or info.get("name") or "",
                "html": result.get("contentHtml") or "",
                "text": result.get("contentText") or ""}
