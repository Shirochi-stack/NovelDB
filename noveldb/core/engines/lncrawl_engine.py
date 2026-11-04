"""lightnovel-crawler's sources as a site engine (https://github.com/lncrawl/lightnovel-crawler, GPL-3.0).

The bundle (`lncrawl/lncrawl-sources.zip`, built by scripts/update_lncrawl.py) holds the crawlers, their
core and NovelDB's request layer. It is unpacked once per build into the cache folder and put on
`sys.path`; every request the crawlers make goes through the app's transport (`fetch`). Sources load
by file, the way the build indexed them, and each book keeps its crawler, which remembers what it
read from the book page for the chapters.
"""

from __future__ import annotations

import html as html_module
import importlib.util
import json
import re
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

LABEL = "lightnovel-crawler"
_lock = threading.Lock()
_runtime: Optional[dict] = None  # {"folder", "key", "hosts": {host: [crawler entry]}}


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _host(url: str) -> str:
    try:
        return _strip_www((urlsplit(url).hostname or "").lower())
    except ValueError:
        return ""


def runtime() -> Optional[dict]:
    """Unpack the bundle in use (once) and return its index, or None when the build has none."""
    global _runtime
    from noveldb.core import engine_updates, paths

    bundle = engine_updates.current(engine_updates.LNCRAWL)
    if bundle is None:
        return None
    key = str(bundle.manifest.get("sha256") or "")[:16]
    with _lock:
        if _runtime is not None and _runtime["key"] == key:
            return _runtime
        folder = paths.cache_dir() / "lncrawl-runtime" / key
        if not (folder / "index.json").is_file():
            temp = folder.with_name(folder.name + ".tmp")
            with zipfile.ZipFile(bundle.path) as archive:
                for name in archive.namelist():
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise ValueError(f"unsafe name in the lightnovel-crawler bundle: {name}")
                archive.extractall(temp)
            if folder.exists():
                import shutil

                shutil.rmtree(folder, ignore_errors=True)
            temp.rename(folder)
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        hosts: Dict[str, List[dict]] = {}
        for entry in index.get("crawlers", []):
            if entry.get("disabled"):
                continue
            for url in entry.get("base_urls", []):
                host = _host(url if "//" in url else "https://" + url)
                if host:
                    hosts.setdefault(host, []).append(entry)
        # Another build's runtime may already be imported: start over with this one.
        if _runtime is not None and _runtime["folder"] != str(folder):
            for name in [n for n in sys.modules if n.split(".")[0] in ("lncrawl", "scraper", "sources")]:
                del sys.modules[name]
            if _runtime["folder"] in sys.path:
                sys.path.remove(_runtime["folder"])
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        bs4_compat()
        _runtime ={"folder": str(folder), "key": key, "hosts": hosts}
        return _runtime


def bs4_compat() -> None:
    """lightnovel-crawler's soup layer is written for BeautifulSoup 4.13: `new_tag` takes the tag's
    text (`string`, passed by position) and tags have `copy_self`. With 4.12 (a source install, or
    the mobile apps' Python) the first fails every chapter with a positional-argument error and the
    second silently skips text replacements. Both are added when missing; 4.13 is left alone."""
    import copy
    import inspect

    import bs4

    if not hasattr(bs4.Tag, "copy_self"):
        def copy_self(tag):
            clone = copy.copy(tag)
            clone.clear()
            return clone

        bs4.Tag.copy_self = copy_self
    if "string" not in inspect.signature(bs4.BeautifulSoup.new_tag).parameters:
        original = bs4.BeautifulSoup.new_tag

        def new_tag(self, name, namespace=None, nsprefix=None, attrs=None, sourceline=None, sourcepos=None,
                    string=None, **kwattrs):
            tag = original(self, name, namespace, nsprefix, attrs or {}, sourceline, sourcepos, **kwattrs)
            if string is not None:
                tag.string = string
            return tag

        bs4.BeautifulSoup.new_tag = new_tag


def knows(url: str) -> bool:
    try:
        found = runtime()
    except Exception:
        return False
    return bool(found and _host(url) in found["hosts"])


def entries_for(url: str) -> List[dict]:
    found = runtime()
    return list(found["hosts"].get(_host(url), [])) if found else []


def searchable_sites() -> List[dict]:
    """Crawler entries that answer search queries (for the Catalog's generic search)."""
    found = runtime()
    if not found:
        return []
    seen, out = set(), []
    for entries in found["hosts"].values():
        for entry in entries:
            key = (entry["file"], entry["name"])
            if entry.get("search") and key not in seen:
                seen.add(key)
                out.append(entry)
    return out


def _module_for(relative: str) -> str:
    parts = relative[:-3].split("/")
    return ".".join(parts[:-1] + [parts[-1].replace(".", "_").replace("-", "_")])


def _load_class(entry: dict):
    folder = Path(runtime()["folder"])
    name = _module_for(entry["file"])
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, folder / entry["file"])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        _patch(module)
    return getattr(module, entry["name"])


def _patch(module) -> None:
    """Fixes for upstream crawlers, applied as they load (the bundle keeps their files unchanged)."""
    _patch_soup()
    crawler = getattr(module, "WebToonsCrawler", None)
    if crawler is not None and not getattr(crawler, "_noveldb_patched", False):
        crawler.read_novel_info = _webtoons_novel_info
        crawler._noveldb_patched = True


def _patch_soup() -> None:
    """The runtime's PageSoup fixes for bundles built before them (scripts/lncrawl/runtime/scraper/soup.py):
    Beautiful Soup takes the tag inside a PageSoup it is handed (webtoons appended one to a plain tag:
    "property 'parent' of 'PageSoup' object has no setter"), and new_tag passes its arguments by name
    (Beautiful Soup 4.12 has no `string` argument)."""
    soup = sys.modules.get("scraper.soup")
    page_soup = getattr(soup, "PageSoup", None)
    if page_soup is None or getattr(page_soup, "_noveldb_patched", False):
        return
    page_soup._noveldb_patched = True
    from bs4 import Tag

    if not getattr(Tag.insert, "_unwraps_page_soup", False):
        insert = Tag.insert

        def unwrapping(self, position, new_child, *rest, **kwargs):
            if type(new_child).__name__ == "PageSoup":
                new_child = getattr(new_child, "_tag", None) or ""
            return insert(self, position, new_child, *rest, **kwargs)

        unwrapping._unwraps_page_soup = True
        Tag.insert = unwrapping

    def new_tag(self, name, namespace=None, nsprefix=None, attrs=None, sourceline=None, sourcepos=None,
                string=None, **kwargs):
        if not self.root:
            raise ValueError("Cannot create a new tag on an empty soup")
        tag = self.root.new_tag(name, namespace=namespace, nsprefix=nsprefix, attrs=attrs or {},
                                sourceline=sourceline, sourcepos=sourcepos, **kwargs)
        if string is not None:
            tag.string = string
        return page_soup(tag)

    page_soup.new_tag = new_tag


def _webtoons_novel_info(self) -> None:
    """webtoons: every page of the episode list, oldest first.

    Upstream took the last pagination link for the last page, but that link is "Next" (the next ten
    pages): a series of 286 episodes gave its newest 110, the first of them S2 Ep. 82. The pages are
    read until one adds no episode, and the episodes are ordered by their number."""
    module = sys.modules[type(self).__module__]
    soup = self.get_soup(self.novel_url)
    title = soup.select_one("h1.subj") or soup.select_one("h3.subj")
    self.novel_title = title.text.strip() if title else ""
    author = soup.select_one("a.author") or soup.select_one(".author_area")
    self.novel_author = author.text.strip() if author else ""
    cover = soup.select_one("meta[property='og:image']")
    if cover:
        self.novel_cover = self.absolute_url(cover["content"])
    base = re.sub(r"[?&]page=\d+", "", self.novel_url)
    joiner = "&" if "?" in base else "?"
    episodes: Dict[int, tuple] = {}
    for page in range(1, 400):
        page_soup = soup if page == 1 else self.get_soup(f"{base}{joiner}page={page}")
        added = 0
        for link in page_soup.select("#_listUl a"):
            href = str(link["href"] or "")
            number = re.search(r"episode_no=(\d+)", href)
            if not number or int(number.group(1)) in episodes:
                continue
            name = link.select_one("span.subj > span") or link.select_one("span.subj")  # not its "UP" badge
            episodes[int(number.group(1))] = (href, (name.text if name else "").strip())
            added += 1
        if not added:
            break
    for number in sorted(episodes):
        href, name = episodes[number]
        chapter_id = len(self.chapters) + 1
        volume_id = 1 + len(self.chapters) // 100
        if chapter_id % 100 == 1:
            self.volumes.append(module.Volume(id=volume_id))
        self.chapters.append(module.Chapter(id=chapter_id, volume=volume_id, title=name or f"Episode {number}",
                                            url=self.absolute_url(href)))


def _plain(text: str) -> str:
    from bs4 import BeautifulSoup

    if "<" not in (text or ""):
        return (text or "").strip()
    soup = BeautifulSoup(text, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "tr", "hr"]):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = (re.sub(r"\s+", " ", line).strip() for line in soup.get_text().split("\n"))
    return "\n".join(line for line in lines if line)


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


class LncrawlEngine:
    label = LABEL

    def __init__(self, fetch: Callable = None, log: Callable[[str], None] = lambda _m: None,
                 render: Optional[Callable[[str], Optional[str]]] = None):
        if runtime() is None:
            raise RuntimeError("the lightnovel-crawler sources are missing from this build")
        # Each crawler gets this engine's transport (the downloader and the Catalog each have one).
        self.fetch = fetch
        self.render = render
        self.log = log
        self._crawlers = {}
        self._lock = threading.Lock()

    @staticmethod
    def knows(url: str) -> bool:
        return knows(url)

    def _new_crawler(self, entry: dict, url: str):
        cls = _load_class(entry)
        origin = next((base for base in entry["base_urls"] if _host(base) == _host(url)), entry["base_urls"][0])
        crawler = cls(origin=origin)
        if self.fetch is not None:
            crawler.scraper.fetcher = self.fetch
        if self.render is not None:
            crawler.scraper.renderer = self.render
        crawler.initialize()
        return crawler

    def _crawler_for_book(self, url: str):
        with self._lock:
            return self._crawlers.get(url)

    def parse_book(self, url: str) -> Optional[dict]:
        from lncrawl.core.models import Novel

        errors = []
        for entry in entries_for(url):
            try:
                crawler = self._new_crawler(entry, url)
                crawler.novel_url = url
                novel = Novel(url=url)
                crawler.read_novel(novel)
            except Exception as e:
                errors.append(f"{entry['name']}: {type(e).__name__}: {e}")
                continue
            if not novel.chapters and not novel.title:
                continue
            with self._lock:
                self._crawlers[url] = crawler
            volumes = {volume.get("id"): volume.get("title") or "" for volume in novel.volumes}
            chapters = []
            for index, chapter in enumerate(novel.chapters):
                chapters.append({
                    "url": chapter.get("url") or "",
                    "name": html_module.unescape(str(chapter.get("title") or f"Chapter {index + 1}")).strip(),
                    "number": index + 1,
                    "sectionName": volumes.get(chapter.get("volume")) or None,
                    "bookUrl": url,
                    "lncrawl": _json_safe(dict(chapter)),
                    "isVIP": False,
                    "isPaid": False,
                })
            tags = novel.tags if isinstance(novel.tags, list) else str(novel.tags or "").split(",")
            return {
                "engine": "lncrawl",
                "ruleName": f"lightnovel-crawler {entry['name']}",
                "bookUrl": url,
                "bookname": html_module.unescape(str(novel.title or "")).strip(),
                "author": html_module.unescape(str(novel.author or "")).strip(),
                "introduction": _plain(str(novel.synopsis or "")),
                "introductionHTML": "",
                "coverUrl": novel.cover_url or None,
                "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:40],
                "language": entry.get("language") or "",
                "chapterCount": len(chapters),
                "chapters": chapters,
            }
        if errors:
            raise RuntimeError("; ".join(errors)[:500])
        return None

    def parse_chapter(self, chapter_info: dict, book: dict) -> Optional[dict]:
        from lncrawl.core.models import Chapter

        book_url = chapter_info.get("bookUrl") or book.get("bookUrl") or ""
        crawler = self._crawler_for_book(book_url)
        if crawler is None:
            self.parse_book(book_url)
            crawler = self._crawler_for_book(book_url)
            if crawler is None:
                raise RuntimeError("the book could not be read again")
        saved = dict(chapter_info.get("lncrawl") or {})
        saved.setdefault("id", chapter_info.get("number") or 1)
        saved.setdefault("url", chapter_info.get("url") or "")
        saved.setdefault("title", chapter_info.get("name") or "")
        chapter = Chapter(**saved)
        crawler.download_chapter(chapter)
        body = str(chapter.get("body") or "")
        text = _plain(body)
        if not text and "<img" not in body:
            raise ValueError("the chapter has no visible text")
        return {
            "chapterName": chapter_info.get("fullName") or chapter_info.get("name", ""),
            "contentHtml": body,
            "contentText": text,
            "imageCount": 0,
            "images": [],
        }

    def search(self, entry: dict, query: str) -> List[dict]:
        """The site's own search: [{name, url, info}]."""
        crawler = self._new_crawler(entry, entry["base_urls"][0])
        out = []
        for result in crawler.search(query):
            title = str(result.get("title") or "").strip()
            url = str(result.get("url") or "").strip()
            if title and url:
                out.append({"name": html_module.unescape(title), "url": url, "info": str(result.get("info") or "")})
        return out
