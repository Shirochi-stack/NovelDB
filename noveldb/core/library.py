"""The Library: the books in the save folder, for the Library page (no Qt here).

A book is every output that shares a name in the folder: "Title.epub", "Title.pdf", "Title.txt" and a
separate-TXT folder "Title/" are one book with four formats. EPUBs give the title, author and cover
(read from the package file, cached by size and date), the other formats only their file name.
"""

from __future__ import annotations

import posixpath
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

BOOK_FORMATS = (".epub", ".pdf", ".txt", ".html", ".htm", ".cbz", ".zip")
# A folder is a book (separate TXT, or a folder of pages) when it holds files of these kinds.
FOLDER_FILES = (".txt", ".html", ".htm", ".jpg", ".jpeg", ".png", ".webp")
_meta_cache: Dict[Tuple[str, int, float], dict] = {}
_lock = threading.Lock()


@dataclass
class LibraryFile:
    path: Path
    format: str        # "EPUB", "PDF", "TXT", "TXT folder", ...
    size: int
    modified: float


@dataclass
class LibraryBook:
    name: str                          # the shared file name, without extension
    title: str
    author: str = ""
    files: List[LibraryFile] = field(default_factory=list)
    cover: Optional[bytes] = None       # the EPUB's cover image, when it has one

    @property
    def modified(self) -> float:
        return max((f.modified for f in self.files), default=0.0)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def preferred(self) -> Optional[LibraryFile]:
        """The file Open opens: EPUB, then PDF, then the rest."""
        order = ["EPUB", "PDF", "CBZ", "HTML", "TXT", "TXT folder", "Folder", "ZIP"]
        return min(self.files, key=lambda f: order.index(f.format) if f.format in order else 99, default=None)


def _folder_size(path: Path) -> Tuple[int, float]:
    size, newest = 0, 0.0
    for item in path.rglob("*"):
        if item.is_file():
            stat = item.stat()
            size += stat.st_size
            newest = max(newest, stat.st_mtime)
    return size, newest


def _epub_meta(path: Path, size: int, modified: float) -> dict:
    key = (str(path), size, modified)
    with _lock:
        if key in _meta_cache:
            return _meta_cache[key]
    meta: dict = {}
    try:
        with zipfile.ZipFile(path) as archive:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
            opf = ElementTree.fromstring(archive.read(rootfile))
            base = posixpath.dirname(rootfile)
            for element in opf.iter():
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "title" and not meta.get("title") and (element.text or "").strip():
                    meta["title"] = element.text.strip()
                elif tag == "creator" and not meta.get("author") and (element.text or "").strip():
                    meta["author"] = element.text.strip()
                elif tag == "description" and not meta.get("description") and (element.text or "").strip():
                    meta["description"] = re.sub(r"<[^>]+>", " ", element.text).strip()
            items = {e.get("id"): e for e in opf.iter() if e.tag.endswith("item")}
            cover_id = next((e.get("content") for e in opf.iter()
                             if e.tag.endswith("meta") and e.get("name") == "cover"), None)
            cover = items.get(cover_id) if cover_id else None
            if cover is None:
                cover = next((e for e in items.values() if "cover-image" in (e.get("properties") or "")), None)
            if cover is None:
                cover = next((e for e in items.values() if (e.get("media-type") or "").startswith("image/")
                              and "cover" in (e.get("href") or "").lower()), None)
            if cover is not None:
                href = posixpath.normpath(posixpath.join(base, cover.get("href") or ""))
                data = archive.read(href)
                if len(data) < 8_000_000:
                    meta["cover"] = data
    except Exception:
        pass
    with _lock:
        if len(_meta_cache) > 2000:
            _meta_cache.clear()
        _meta_cache[key] = meta
    return meta


def _format_of(path: Path) -> Optional[str]:
    if path.is_dir():
        kinds = {item.suffix.lower() for item in path.iterdir() if item.is_file()}
        if not kinds or not kinds & set(FOLDER_FILES):
            return None
        return "TXT folder" if kinds <= {".txt"} else "Folder"
    suffix = path.suffix.lower()
    if suffix not in BOOK_FORMATS:
        return None
    return {".htm": "HTML"}.get(suffix, suffix[1:].upper())


def scan(folder: Path, progress=None, found=None) -> List[LibraryBook]:
    """The books in `folder`, newest first; `progress(done, total)` hears how far the reading is, and
    `found(books)` gets the books read so far (newest first) about twice a second, to show them early."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    books: Dict[str, LibraryBook] = {}
    entries = list(folder.iterdir())
    told = time.monotonic()
    for done, path in enumerate(entries, 1):
        if progress is not None and (done % 10 == 0 or done == len(entries)):
            progress(done, len(entries))
        if found is not None and books and time.monotonic() - told > 0.5:
            told = time.monotonic()
            found(sorted(books.values(), key=lambda b: b.modified, reverse=True))
        if path.name.startswith(".") or path.name.endswith((".part", ".tmp")):
            continue
        try:
            kind = _format_of(path)
        except OSError:
            continue
        if kind is None:
            continue
        try:
            if path.is_dir():
                size, modified = _folder_size(path)
                name = path.name
            else:
                stat = path.stat()
                size, modified = stat.st_size, stat.st_mtime
                name = path.stem
        except OSError:
            continue
        book = books.setdefault(name.lower(), LibraryBook(name=name, title=_title_from_name(name)))
        book.files.append(LibraryFile(path, kind, size, modified))
        if kind == "EPUB":
            meta = _epub_meta(path, size, modified)
            book.title = meta.get("title") or book.title
            book.author = meta.get("author") or book.author
    return sorted(books.values(), key=lambda b: b.modified, reverse=True)


def description(book: LibraryBook) -> str:
    """The synopsis the EPUB carries, or ""."""
    epub = next((f for f in book.files if f.format == "EPUB"), None)
    return _epub_meta(epub.path, epub.size, epub.modified).get("description", "") if epub else ""


def chapter_source(path: Path, href: str) -> Tuple[str, str]:
    """(body HTML, the chapter's folder in the EPUB) for a reader that loads the images itself."""
    with zipfile.ZipFile(path) as archive:
        text = archive.read(href).decode("utf-8", "replace")
    body = re.search(r"<body[^>]*>(.*)</body>", text, re.S | re.I)
    return (body.group(1) if body else text), posixpath.dirname(href)


def read_member(path: Path, name: str) -> Optional[bytes]:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(name)
    except (KeyError, OSError, zipfile.BadZipFile):
        return None


def cover(book: LibraryBook) -> Optional[bytes]:
    """The book's EPUB cover image (read when it is shown, not when the folder is scanned)."""
    epub = next((f for f in book.files if f.format == "EPUB"), None)
    if epub is None:
        return None
    return _epub_meta(epub.path, epub.size, epub.modified).get("cover")


# -- reading an EPUB in the app's reader ---------------------------------------------------------------

def _resolve(base: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(base, href.split("#")[0]))


def epub_contents(path: Path) -> dict:
    """{title, author, chapters: [{name, href}]}: the spine in reading order, named from the table of
    contents (nav or NCX) where it names them."""
    with zipfile.ZipFile(path) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
        opf = ElementTree.fromstring(archive.read(rootfile))
        base = posixpath.dirname(rootfile)
        items = {e.get("id"): e for e in opf.iter() if e.tag.endswith("item")}
        spine = [items[e.get("idref")] for e in opf.iter() if e.tag.endswith("itemref") and e.get("idref") in items]
        names: Dict[str, str] = {}
        nav = next((e for e in items.values() if "nav" in (e.get("properties") or "")), None)
        ncx = next((e for e in items.values() if (e.get("media-type") or "") == "application/x-dtbncx+xml"), None)
        try:
            if nav is not None:
                href = _resolve(base, nav.get("href"))
                tree = ElementTree.fromstring(archive.read(href))
                for anchor in tree.iter():
                    if anchor.tag.endswith("}a") or anchor.tag == "a":
                        target = anchor.get("href")
                        text = "".join(anchor.itertext()).strip()
                        if target and text:
                            names.setdefault(_resolve(posixpath.dirname(href), target), text)
            elif ncx is not None:
                href = _resolve(base, ncx.get("href"))
                tree = ElementTree.fromstring(archive.read(href))
                for point in tree.iter():
                    if point.tag.endswith("navPoint"):
                        label = next(("".join(e.itertext()).strip() for e in point.iter() if e.tag.endswith("text")), "")
                        content = next((e.get("src") for e in point.iter() if e.tag.endswith("content")), None)
                        if label and content:
                            names.setdefault(_resolve(posixpath.dirname(href), content), label)
        except Exception:
            pass
        meta = {}
        for element in opf.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in ("title", "creator") and tag not in meta and (element.text or "").strip():
                meta[tag] = element.text.strip()
    chapters = []
    for item in spine:
        href = _resolve(base, item.get("href") or "")
        if not (item.get("media-type") or "").endswith(("xhtml+xml", "html")):
            continue
        name = names.get(href) or posixpath.splitext(posixpath.basename(href))[0]
        if re.fullmatch(r"(cover|titlepage|title|nav|toc)", name, re.I) and len(spine) > 2:
            continue
        chapters.append({"name": name, "href": href})
    return {"title": meta.get("title", path.stem), "author": meta.get("creator", ""), "chapters": chapters}


def epub_chapter(path: Path, href: str) -> dict:
    """A chapter's body as HTML, its images inlined (data: addresses), for the reader."""
    import base64
    import mimetypes

    with zipfile.ZipFile(path) as archive:
        text = archive.read(href).decode("utf-8", "replace")
        body = re.search(r"<body[^>]*>(.*)</body>", text, re.S | re.I)
        html = body.group(1) if body else text
        folder = posixpath.dirname(href)

        def inline(match):
            source = match.group(2)
            if source.startswith(("data:", "http:", "https:")):
                return match.group(0)
            name = _resolve(folder, source)
            try:
                data = archive.read(name)
            except KeyError:
                return match.group(0)
            kind = mimetypes.guess_type(name)[0] or "image/jpeg"
            return f'{match.group(1)}"data:{kind};base64,{base64.b64encode(data).decode("ascii")}"'

        html = re.sub(r'(<img\b[^>]*?\bsrc=)"([^"]+)"', inline, html, flags=re.I)
        html = re.sub(r'(<image\b[^>]*?\bhref=)"([^"]+)"', inline, html, flags=re.I)
    title = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    return {"name": (title.group(1).strip() if title else ""), "html": html}


def _title_from_name(name: str) -> str:
    """A file name as a title: underscores as spaces, a trailing range "(1-10)" kept."""
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip() or name


def matches(book: LibraryBook, text: str) -> bool:
    text = (text or "").strip().lower()
    return not text or text in book.title.lower() or text in book.author.lower() or text in book.name.lower()
