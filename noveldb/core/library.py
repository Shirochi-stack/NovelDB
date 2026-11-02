"""The Library: the books in the save folder, for the Library page (no Qt here).

A book is every output that shares a name in the folder: "Title.epub", "Title.pdf", "Title.txt" and a
separate-TXT folder "Title/" are one book with four formats. EPUBs give the title, author and cover
(read from the package file, cached by size and date), the other formats only their file name.
"""

from __future__ import annotations

import posixpath
import re
import threading
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


def scan(folder: Path) -> List[LibraryBook]:
    """The books in `folder`, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    books: Dict[str, LibraryBook] = {}
    for path in folder.iterdir():
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
            book.cover = meta.get("cover") or book.cover
    return sorted(books.values(), key=lambda b: b.modified, reverse=True)


def _title_from_name(name: str) -> str:
    """A file name as a title: underscores as spaces, a trailing range "(1-10)" kept."""
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip() or name


def matches(book: LibraryBook, text: str) -> bool:
    text = (text or "").strip().lower()
    return not text or text in book.title.lower() or text in book.author.lower() or text in book.name.lower()
