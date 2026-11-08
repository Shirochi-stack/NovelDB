"""MangaK's public title feed and server-rendered reader, shared with Pyodide."""

from decimal import Decimal, InvalidOperation
from html import escape
import json
import re
from urllib.parse import urlencode, urljoin, urlsplit

from noveldb.core.engines.generic import decode, soup_of

HOST = "mangak.io"
BASE = f"https://{HOST}"
_SLUG = r"[a-z0-9][a-z0-9-]{0,220}"
_ROUTE = re.compile(rf"^/({_SLUG})(?:/(chapter-[a-z0-9][a-z0-9.-]{{0,220}}))?/?$")
_LISTINGS = {"about", "advanced-search", "api", "browse", "contact", "dmca", "genres", "home", "latest",
             "library", "login", "manga", "new", "popular", "privacy", "privacy-policy", "register",
             "search", "sitemap", "terms", "terms-of-service", "trending"}


def route(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.netloc.lower().removeprefix("www.") != HOST:
        return None
    match = _ROUTE.fullmatch(parts.path)
    return match if match and match[1] not in _LISTINGS else None


def knows(url):
    return route(url) is not None


def page_props(html):
    node = soup_of(html).find("script", id="__NEXT_DATA__")
    return json.loads(node.string or "{}").get("props", {}).get("pageProps", {}) if node else {}


def _image_url(value):
    if not isinstance(value, str) or not value:
        return ""
    parts = urlsplit(urljoin(BASE + "/", value))
    return parts.geturl() if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password else ""


def _chapter_number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else Decimal("Infinity")
    except InvalidOperation:
        return Decimal("Infinity")


class MangaKEngine:
    label = "MangaK reader"
    knows = staticmethod(knows)

    def __init__(self, fetch, log=lambda _: None):
        self.fetch = fetch

    def _get(self, url, referer="", *, api=False):
        headers = {"Accept": "application/json"} if api else None
        result = self.fetch(url, referer=referer, headers=headers)
        if not result or result["status"] != 200:
            raise RuntimeError(f"MangaK could not load the page ({result['status'] if result else 'no answer'}).")
        text = decode(result)
        return json.loads(text) if api else page_props(text)

    def parse_book(self, url):
        match = route(url)
        if not match:
            return None
        slug = match[1]
        book_url = f"{BASE}/{slug}"
        manga = self._get(book_url).get("initialManga") or {}
        if manga.get("slug") != slug or not manga.get("name"):
            return None
        preview = manga.get("chapters") or []
        rows = preview
        total = (manga.get("stats") or {}).get("chaptersCount")
        if not preview or not isinstance(total, int) or total > len(preview):
            ident = str(manga.get("id") or "")
            if not re.fullmatch(r"[a-zA-Z0-9]{1,64}", ident):
                raise RuntimeError("MangaK returned an invalid title ID.")
            query = urlencode({"cv": manga["cv"]}) if manga.get("cv") else ""
            endpoint = f"https://api.{HOST}/titles/{ident}/chapters"
            feed = self._get(endpoint + ("?" + query if query else ""), book_url, api=True)
            rows = (feed.get("data") or {}).get("chapters") or []
        by_url = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            address = urljoin(book_url, row.get("url") or "")
            chapter = route(address)
            if chapter and chapter[1] == slug and chapter[2]:
                canonical = f"{book_url}/{chapter[2]}"
                by_url[canonical] = (_chapter_number(row.get("number")), row.get("name") or chapter[2])
        first = route(urljoin(book_url, (manga.get("firstChapter") or {}).get("url") or ""))
        if not by_url or (first and first[2] and f"{book_url}/{first[2]}" not in by_url):
            raise RuntimeError("MangaK returned an incomplete chapter list.")
        ordered = sorted(by_url.items(), key=lambda item: item[1][0])
        chapters = [{"url": address, "name": name, "number": index, "bookUrl": book_url}
                    for index, (address, (_, name)) in enumerate(ordered, 1)]
        authors = [p["name"] for p in manga.get("authors") or [] if isinstance(p, dict) and isinstance(p.get("name"), str)]
        return {"bookUrl": book_url, "bookname": manga["name"], "author": ", ".join(authors),
                "introduction": manga.get("summary") or "", "coverUrl": _image_url(manga.get("cover")),
                "chapters": chapters, "chapterCount": len(chapters), "engine": "mangak", "ruleName": self.label}

    def parse_chapter(self, chapter, book):
        match = route(chapter.get("url") or "")
        if not match or not match[2]:
            return None
        url = f"{BASE}/{match[1]}/{match[2]}"
        data = self._get(url, book.get("bookUrl") or f"{BASE}/{match[1]}")
        result = data.get("initialChapter") or {}
        if result.get("slug") != match[2] or (data.get("initialManga") or {}).get("slug") != match[1]:
            raise RuntimeError("MangaK did not return the requested chapter.")
        links = result.get("images") or []
        images = [_image_url(link) for link in links]
        if not 0 < len(images) <= 2000 or not all(images):
            raise RuntimeError("MangaK did not return the chapter images.")
        return {"chapterName": result.get("name") or chapter.get("name") or "", "contentText": "",
                "comic": True, "imageCount": len(images), "imageReferer": url, "images": [],
                "contentHtml": "".join(f'<p><img src="{escape(image)}" alt=""/></p>' for image in images)}
