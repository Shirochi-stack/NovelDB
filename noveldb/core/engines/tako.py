"""NovelDB ports of Tako's MangaDex and MangaHere reader adapters.

Tako (MIT), copyright (c) 2025 oovz.
Reference: https://github.com/oovz/Tako/tree/34c37c0f33a6e52aa2fc51df9c02c704dec0a3be/src/site-integrations
"""

from html import escape
import json
import re
from urllib.parse import quote, urlencode, urlsplit

from noveldb.core.engines.generic import clean_url, dm5_chapter, dm5_images, soup_of, text_of


DEX = "mangadex.org"
HERE = "newm.mangahere.cc"
_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_DEX_PAGE = re.compile(rf"^/(title|chapter)/({_ID})(?:/|$)", re.I)
_HERE_PAGE = re.compile(r"^/manga/([^/]+)(?:/(?:v\d+/)?c[^/]+(?:/1\.html)?)?/?$")


def knows(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").removeprefix("www.")
    return bool((host == DEX and _DEX_PAGE.match(parsed.path))
                or (host in ("mangahere.cc", "mangahere.com", HERE) and _HERE_PAGE.fullmatch(parsed.path)))


def _translated(values, default=""):
    if not isinstance(values, dict):
        return default
    return values.get("en") or next((value for value in values.values() if value), default)


def _page_url(url):
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname and parsed.username is None and parsed.password is None and parsed.port is None:
        return url
    return ""


class TakoEngine:
    label = "Tako readers"
    knows = staticmethod(knows)

    def __init__(self, fetch, log=lambda _: None):
        self.fetch = fetch
        self.log = log

    def _get(self, url, referer="", *, is_json=False, headers=None):
        result = self.fetch(url, referer=referer, headers={"Accept": "application/json"} if is_json else headers)
        if not result or result["status"] != 200:
            raise RuntimeError(f"Tako could not load the site response ({result['status'] if result else 'no answer'})")
        content = result.get("text") or result.get("content") or ""
        if not is_json:
            return content
        data = json.loads(content)
        if not isinstance(data, dict) or data.get("result") != "ok":
            raise RuntimeError("Tako received an invalid MangaDex response")
        return data

    def _dex(self, path, query=()):
        url = f"https://api.{DEX}{path}" + (f"?{urlencode(query)}" if query else "")
        return self._get(url, f"https://{DEX}/", is_json=True)

    def _dex_book(self, route):
        kind, manga_id = route.groups()
        if kind.lower() == "chapter":
            chapter = self._dex(f"/chapter/{manga_id}")["data"]
            manga_id = next((r["id"] for r in chapter.get("relationships", []) if r.get("type") == "manga"), "")
            if not re.fullmatch(_ID, manga_id, re.I):
                return None
        address = f"https://{DEX}/title/{manga_id}"
        manga = self._dex(f"/manga/{manga_id}", [("includes[]", key) for key in ("author", "artist", "cover_art")])["data"]
        meta = manga["attributes"]
        chapters, seen = [], set()
        offset = 0
        for _ in range(200):
            feed = self._dex(f"/manga/{manga_id}/feed",
                             [("order[volume]", "asc"), ("order[chapter]", "asc"), ("limit", 500),
                              ("offset", offset), ("translatedLanguage[]", "en")])
            rows = feed.get("data") or []
            for row in rows:
                attrs = row["attributes"]
                ident = row.get("id") or ""
                if (not re.fullmatch(_ID, ident, re.I) or ident in seen or attrs.get("externalUrl")
                        or not attrs.get("pages") or not attrs.get("translatedLanguage")):
                    continue
                seen.add(ident)
                number = attrs.get("chapter")
                name = f"Chapter {number}" if number is not None else "Extra"
                if attrs.get("title"):
                    name += f": {attrs['title']}"
                chapters.append({"url": f"https://{DEX}/chapter/{ident}", "name": name,
                                 "sectionName": f"Volume {attrs['volume']}" if attrs.get("volume") else "",
                                 "bookUrl": address})
            offset += len(rows)
            if offset >= (feed.get("total") or 0):
                break
            if not rows:
                raise RuntimeError("MangaDex returned an incomplete chapter feed")
        else:
            raise RuntimeError("MangaDex chapter pagination exceeded its limit")
        relationships = manga.get("relationships", [])
        author = next((r.get("attributes", {}).get("name") for r in relationships if r.get("type") == "author"), "")
        cover = next((r.get("attributes", {}).get("fileName") for r in relationships if r.get("type") == "cover_art"), "")
        return self._book(address, _translated(meta.get("title"), "MangaDex"), chapters,
                          author=author or "", introduction=_translated(meta.get("description")),
                          coverUrl=f"https://uploads.{DEX}/covers/{manga_id}/{quote(cover, safe='')}" if cover else "",
                          tags=[_translated(tag.get("attributes", {}).get("name")) for tag in meta.get("tags", [])])

    def _here_book(self, route):
        series = route[1]
        address = f"https://{HERE}/manga/{series}/"
        soup = soup_of(self._get(address))
        chapters, seen = [], set()
        for link in soup.select(".detail-chapters-list a[href]"):
            url = clean_url(link["href"], address)
            path = re.fullmatch(rf"/manga/{re.escape(series)}/(?:v\d+/)?c[^/]+/(?:1\.html)?", urlsplit(url).path)
            if urlsplit(url).hostname != HERE or not path or path[0].removesuffix("1.html") in seen:
                continue
            seen.add(path[0].removesuffix("1.html"))
            chapters.append({"url": f"https://{HERE}{path[0]}", "name": text_of(link), "bookUrl": address})
        chapters.reverse()
        return self._book(address, text_of(soup.select_one(".detail-top-bar-info-title")), chapters,
                          introduction=text_of(soup.select_one(".detail-text-bar .fullcontent, .detail-text-bar p")))

    def _book(self, url, title, chapters, **meta):
        if not title or not chapters:
            return None
        for index, chapter in enumerate(chapters, 1):
            chapter["number"] = index
        return dict(meta, engine="tako", ruleName=self.label, bookUrl=url, bookname=title,
                    chapters=chapters, chapterCount=len(chapters))

    def parse_book(self, url):
        if not knows(url):
            return None
        path = urlsplit(url).path
        dex = _DEX_PAGE.match(path)
        return self._dex_book(dex) if dex else self._here_book(_HERE_PAGE.fullmatch(path))

    def _here_images(self, url, referer):
        html = self._get(url, referer)
        info = dm5_chapter(html) if len(html) <= 10_000_000 else None
        if not info or not 0 < info["count"] <= 2000:
            raise RuntimeError("Tako could not find the MangaHere page count")
        images = [clean_url(link, url) for link in info["images"]]
        if not images and info["key"]:
            endpoint = url.rsplit("/", 1)[0] + "/chapterfun.ashx"
            while len(images) < info["count"]:
                query = urlencode({"cid": info["cid"], "page": len(images) + 1, "key": info["key"]})
                listed = dm5_images(self._get(f"{endpoint}?{query}", url,
                                             headers={"X-Requested-With": "XMLHttpRequest"}), url)
                if not listed:
                    break
                images.extend(listed[:info["count"] - len(images)])
        if len(images) != info["count"] or any(
                not _page_url(image) or urlsplit(image).hostname not in {"zjcdn.mangahere.org", "fmcdn.mangahere.com"}
                or not urlsplit(image).path.startswith("/store/manga/") for image in images):
            raise RuntimeError("Tako could not verify the complete MangaHere image list")
        return images

    def parse_chapter(self, chapter, book):
        url = chapter.get("url") or ""
        if not knows(url):
            return None
        dex = _DEX_PAGE.match(urlsplit(url).path)
        if dex and dex[1].lower() == "chapter":
            reply = self._dex(f"/at-home/server/{dex[2]}")
            base = _page_url(reply.get("baseUrl") or "").rstrip("/")
            pages = reply.get("chapter") or {}
            if not base or not re.fullmatch(r"[0-9a-f]{32,64}", pages.get("hash") or "", re.I):
                raise RuntimeError("Tako received an invalid MangaDex image server")
            images = [f"{base}/data/{pages['hash']}/{quote(name, safe='')}" for name in pages.get("data", [])
                      if isinstance(name, str) and name and "/" not in name and "\\" not in name and name not in (".", "..")]
        else:
            path = urlsplit(url).path
            if not re.fullmatch(r"/manga/[^/]+/(?:v\d+/)?c[^/]+/(?:1\.html)?", path):
                return None
            url = f"https://{HERE}{path}"
            images = self._here_images(url, book.get("bookUrl") or url)
        if not images or len(images) > 2000:
            raise RuntimeError("Tako returned an empty or invalid chapter image list")
        return {"chapterName": chapter.get("name") or "", "contentText": "", "comic": True,
                "contentHtml": "".join(f'<p><img src="{escape(image)}" alt=""/></p>' for image in images),
                "imageCount": len(images), "imageReferer": url, "images": []}
