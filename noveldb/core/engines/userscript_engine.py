"""Userscript engines: a ``.user.js`` file added through Settings > Site engines > Add custom engine.

A userscript is written for a page somebody is looking at: it watches what the page shows and saves it from a
menu of its own. As an engine it reads a link with nobody watching. The link opens in the app's own browser
(hidden, with the downloader's logins) with the script running in it from the start of the page, the page is
scrolled or paged to its end, the script's own "select all" and "download" are pressed, and the files it saves
are the chapter (``userscript-host.js`` has the page's half; the browser's is ``browse``).

One link is one chapter: the page the script was given. The script's header is read for its name and its
sites, as a userscript manager reads it::

    // ==UserScript==
    // @name   Example reader
    // @match  *://reader.example.com/*
    // ==/UserScript==

A script that matches every site names none: it is asked last, after the generic fallback (it would answer for
every link otherwise). ``@match`` lines for the sites it should read put it ahead, as for any custom engine.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlsplit

HOST_SCRIPT = Path(__file__).with_name("userscript-host.js")
SUFFIX = ".user.js"
_HEADER = re.compile(r"//\s*==UserScript==(.*?)//\s*==/UserScript==", re.S)
_LINE = re.compile(r"^\s*//\s*@([\w:-]+)[ \t]*(.*?)\s*$", re.M)
_EVERYWHERE = {"*", "<all_urls>"}


def describe(source: str) -> dict:
    """{label, sites, everywhere, meta} of a userscript, from its header; ValueError when it has none."""
    header = _HEADER.search(source or "")
    if not header:
        raise ValueError("it has no ==UserScript== header")
    meta: dict = {}
    for key, value in _LINE.findall(header.group(1)):
        meta.setdefault(key, []).append(value)
    name = (meta.get("name") or [""])[0].strip()
    if not name:
        raise ValueError("its header has no @name")
    patterns = [p for p in (meta.get("match") or []) + (meta.get("include") or []) if p]
    sites, everywhere = [], not patterns
    for pattern in patterns:
        host = _pattern_host(pattern)
        if host is None:
            everywhere = True
        elif host and host not in sites:
            sites.append(host)
    return {"label": name, "sites": [] if everywhere else sites, "everywhere": everywhere,
            "meta": {"name": name, "version": (meta.get("version") or [""])[0], "matches": patterns,
                     "description": (meta.get("description") or [""])[0]}}


def _pattern_host(pattern: str) -> Optional[str]:
    """The site a @match or @include pattern names ("" when it names nothing usable); None for every site."""
    pattern = pattern.strip()
    if pattern in _EVERYWHERE:
        return None
    found = re.match(r"^(?:\*|https?\*?|http\*|file|ftp)://([^/]*)", pattern)
    if not found:
        return None if pattern.startswith("/") or "*" == pattern[:1] else ""
    host = found.group(1).lower()
    if host in ("", "*"):
        return None
    host = re.sub(r"^\*\.?", "", host).split(":")[0]
    host = host.removeprefix("www.")
    # "example.*" or a host with a wildcard inside names no one site: the part before the wildcard is kept
    # only when it is a whole site.
    if "*" in host:
        return None if host.replace("*", "").strip(".") == "" else ""
    return host


def is_userscript(path: str) -> bool:
    return str(path).lower().endswith(".js")


class Engine:
    """A userscript with the custom engines' interface."""

    def __init__(self, source: str, fetch: Callable = None, log: Callable[[str], None] = lambda _m: None,
                 browse: Optional[Callable] = None):
        self.source, self.fetch, self.log, self.browse = source, fetch, log, browse
        info = describe(source)
        self.label, self.sites, self.everywhere, self.meta = info["label"], info["sites"], info["everywhere"], info["meta"]

    def knows(self, url: str) -> bool:
        host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
        return bool(host) and (self.everywhere or any(host == s or host.endswith("." + s) for s in self.sites))

    def _title(self, url: str) -> str:
        """The page's title, from a plain request (the script itself runs once, for the chapter)."""
        try:
            answer = self.fetch(url) if self.fetch else None
            text = (answer or {}).get("text") or ""
            if not text and (answer or {}).get("content"):
                text = (answer["content"] or b"")[:200_000].decode("utf-8", "replace")
            found = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
            if found:
                from html import unescape

                return re.sub(r"\s+", " ", unescape(found.group(1))).strip()
        except Exception:
            pass
        return ""

    def parse_book(self, url: str) -> Optional[dict]:
        title = self._title(url) or (urlsplit(url).hostname or "Page")
        return {"bookname": title, "author": "", "introduction": "", "coverUrl": None, "tags": [],
                "ruleName": f"{self.label} (userscript)",
                "chapters": [{"url": url, "name": title, "number": 1, "bookUrl": url, "isVIP": False, "isPaid": False}]}

    def parse_chapter(self, chapter: dict, book: dict) -> Optional[dict]:
        if self.browse is None:
            raise ValueError("a userscript engine needs the app's browser (the Full build)")
        url = chapter.get("url") or ""
        self.log(f"  {self.label}: reading {url} in the hidden browser with the script running…")
        result = self.browse(url, self.source, self.meta) or {}
        files: List[dict] = result.get("files") or []
        if result.get("failed"):
            self.log(f"  {self.label}: the script stopped: {result['failed']}")
        if not files:
            raise ValueError("the script saved no images from this page")
        pressed = result.get("pressed") or []
        self.log(f"  {self.label}: {len(files)} image(s)" + (f" (pressed {', '.join(pressed)})" if pressed else "") + ".")
        images = [{"name": _file_name(index, item), "data": f"data:{item['mime']};base64," + (
            item["base64"] if "base64" in item else base64.b64encode(item["data"]).decode("ascii"))}
            for index, item in enumerate(files, 1)]
        return {"chapterName": chapter.get("fullName") or chapter.get("name") or result.get("title") or "",
                "contentHtml": "", "contentText": "", "images": images, "imageCount": len(images), "comic": True}


_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
               "image/avif": "avif", "image/bmp": "bmp"}


def _file_name(index: int, item: dict) -> str:
    return f"{index:04d}.{_EXTENSIONS.get(item.get('mime'), 'png')}"


def page_script(source: str, meta: dict) -> str:
    """What runs at the start of the page: the host, then the script with its GM_ functions."""
    import json

    return (HOST_SCRIPT.read_text(encoding="utf-8")
            + "\n;window.__NDB_USERSCRIPT.start(" + json.dumps(source) + ", " + json.dumps(meta) + ");")
