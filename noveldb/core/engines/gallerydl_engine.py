"""The gallery-dl engine: manga sites through gallery-dl's extractors.

gallery-dl (https://github.com/mikf/gallery-dl, GPL-2.0) has extractors for many manga sites (and
for galleries this app doesn't download). Only its manga and chapter extractors are used: a series
page gives the chapter list (gallery-dl's "queue" of chapters), a chapter gives its images in page
order. The book's title, author, cover and synopsis come from its page (`generic.extract_metadata`).

gallery-dl makes its own requests (plain HTTP, its own session), so the engine runs on any thread.
It ships as a bundle packed from its PyPI release (`gallerydl/gallerydl.zip`, built by
scripts/update_gallerydl.py), unpacked and imported ahead of an installed copy, like FanFicFare.
"""

from __future__ import annotations

import re
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

LABEL = "gallery-dl extractors"
_lock = threading.Lock()
_folder: Optional[str] = None


def _use_bundle() -> None:
    """Import gallery-dl from the bundle in use, unpacked once; an installed copy stays the fallback.
    A bundle that fails to import is set aside."""
    global _folder
    from noveldb.core import engine_updates, paths

    with _lock:
        if _folder is not None or "gallery_dl" in sys.modules:
            return
        _folder = ""
        bundle = engine_updates.current(engine_updates.GALLERYDL)
        if bundle is None:
            return
        folder = paths.cache_dir() / "gallerydl-runtime" / str(bundle.manifest.get("sha256") or "")[:16]
        try:
            if not (folder / "gallery_dl" / "__init__.py").is_file():
                temp = folder.with_name(folder.name + ".tmp")
                with zipfile.ZipFile(bundle.path) as archive:
                    for name in archive.namelist():
                        if name.startswith("/") or ".." in Path(name).parts or not name.startswith("gallery_dl/"):
                            raise ValueError(f"unexpected name in the gallery-dl bundle: {name}")
                    archive.extractall(temp)
                temp.rename(folder)
            sys.path.insert(0, str(folder))
            import gallery_dl.extractor  # noqa: F401  (fails here, not mid-download, if the bundle is broken)
            _folder = str(folder)
        except Exception as e:
            if str(folder) in sys.path:
                sys.path.remove(str(folder))
            for name in [n for n in sys.modules if n.split(".")[0] == "gallery_dl"]:
                del sys.modules[name]
            if bundle.updated:
                engine_updates.reject(bundle, str(e))


def _extractor(url: str):
    """gallery-dl's manga or chapter extractor for the URL, or None."""
    _use_bundle()
    try:
        from gallery_dl import extractor
        from gallery_dl.extractor.common import ChapterExtractor, MangaExtractor
    except Exception:
        return None
    try:
        found = extractor.find(url)
    except Exception:
        return None
    return found if isinstance(found, (MangaExtractor, ChapterExtractor)) else None


def knows(url: str) -> bool:
    """gallery-dl has a manga or chapter extractor for the URL (False when it isn't available)."""
    return _extractor(url) is not None


def _chapter_name(data: dict, index: int) -> str:
    number = data.get("chapter")
    minor = str(data.get("chapter_minor") or "")
    title = str(data.get("title") or "").strip()
    label = f"Chapter {number}{minor}" if number not in (None, "") else f"Chapter {index + 1}"
    volume = data.get("volume")
    if volume not in (None, "", 0):
        label = f"Vol. {volume} {label}"
    return f"{label}: {title}" if title and title.lower() != label.lower() else label


class GalleryDlEngine:
    label = LABEL

    def __init__(self, fetch: Callable = None, log: Callable[[str], None] = lambda _m: None):
        self.fetch = fetch
        self.log = log
        # A chapter's images as they are found (the reader shows them while the rest load).
        self.on_images: Callable[[List[str], int], None] = lambda _images, _total: None

    @staticmethod
    def knows(url: str) -> bool:
        return knows(url)

    def _series(self, url: str):
        """The series (manga) extractor: the URL's own, or the one above a chapter's address."""
        from gallery_dl.extractor.common import MangaExtractor
        from noveldb.core.engines.generic import parent_addresses

        found = _extractor(url)
        if isinstance(found, MangaExtractor):
            return url, found
        for parent in parent_addresses(url):
            above = _extractor(parent)
            if isinstance(above, MangaExtractor):
                self.log(f"gallery-dl: {url} is a chapter; reading the series page {parent}.")
                return parent, above
        return url, found

    def parse_book(self, url: str) -> Optional[dict]:
        from gallery_dl.extractor.message import Message

        book_url, found = self._series(url)
        if found is None:
            return None
        chapters, title = [], ""
        from gallery_dl.extractor.common import ChapterExtractor

        if isinstance(found, ChapterExtractor):
            chapters.append({"url": url, "name": "Chapter 1", "data": {}})  # a chapter with no series page
        else:
            for message in found:
                if message[0] == Message.Queue:
                    data = message[2] if len(message) > 2 and isinstance(message[2], dict) else {}
                    title = title or str(data.get("manga") or "")
                    chapters.append({"url": message[1], "name": _chapter_name(data, len(chapters)), "data": data})
        if not chapters:
            return None
        numbered = [c for c in chapters if isinstance(c["data"].get("chapter"), (int, float))]
        if len(numbered) == len(chapters):
            chapters.sort(key=lambda c: (float(c["data"]["chapter"]), str(c["data"].get("chapter_minor") or "")))
        meta = {}
        if self.fetch is not None:
            try:
                from noveldb.core.engines.generic import decode, extract_metadata

                result = self.fetch(book_url)
                if result and result["status"] < 400:
                    meta = extract_metadata(decode(result), result.get("url") or book_url)
            except Exception:
                meta = {}
        listed = [{"url": c["url"], "name": c["name"], "number": i + 1, "bookUrl": book_url, "isVIP": False,
                   "isPaid": False} for i, c in enumerate(chapters)]
        return {
            "engine": "gallerydl",
            "ruleName": f"gallery-dl {found.category}",
            "bookUrl": book_url,
            "bookname": title or meta.get("title") or found.category,
            # A page's "author" that is the site's own name is no author.
            "author": "" if re.sub(r"\W", "", (meta.get("author") or "").lower()) in (found.category, "") else meta["author"],
            "introduction": meta.get("description") or "",
            "introductionHTML": "",
            "coverUrl": meta.get("cover") or None,
            "tags": meta.get("tags") or [],
            "chapterCount": len(listed),
            "chapters": listed,
        }

    def parse_chapter(self, chapter_info: dict, book: dict) -> Optional[dict]:
        from gallery_dl.extractor.message import Message

        url = chapter_info.get("url", "")
        found = _extractor(url)
        if found is None:
            raise RuntimeError("gallery-dl has no chapter extractor for this address")
        images, total = [], 0
        for message in found:
            if message[0] == Message.Directory and isinstance(message[1], dict):
                total = int(message[1].get("count") or 0)
            elif message[0] == Message.Url:
                image = message[1]
                if image.startswith("//"):
                    image = "https:" + image
                if re.match(r"https?://", image) and image not in images:
                    images.append(image)
                    self.on_images(list(images), max(total, len(images)))
        if not images:
            raise ValueError("the chapter has no images (it may need a login)")
        html = "".join(f'<p><img src="{image}" alt=""/></p>' for image in images)
        return {"chapterName": chapter_info.get("fullName") or chapter_info.get("name", ""), "contentHtml": html,
                "contentText": "", "imageCount": len(images), "images": []}


def manga_hosts() -> List[str]:
    """Hosts gallery-dl's manga and chapter extractors match (for the site index), read from their
    patterns: each extractor's `root` (or the first host in its pattern)."""
    _use_bundle()
    try:
        from gallery_dl import extractor
        from gallery_dl.extractor.common import ChapterExtractor, MangaExtractor
    except Exception:
        return []
    hosts = set()
    for cls in extractor._list_classes():
        if not issubclass(cls, (MangaExtractor, ChapterExtractor)):
            continue
        root = getattr(cls, "root", "") or ""
        match = re.match(r"https?://(?:www\.)?([^/]+)", root)
        if match:
            hosts.add(re.sub(r"^(m|www\d*|ww\d+|w\d+|mobile)\.", "", match.group(1).lower()))
            continue
        pattern = getattr(cls, "pattern", "") or ""
        pattern = pattern.pattern if hasattr(pattern, "pattern") else str(pattern)
        literal = re.search(r"([a-z0-9-]+(?:\\\.[a-z0-9-]+)+)", pattern)
        if literal:
            hosts.add(literal.group(1).replace("\\.", ".").lower().removeprefix("www."))
    return sorted(hosts)
