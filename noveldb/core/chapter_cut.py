"""Cut chapters out of files a download already saved, for "Upload chapters" (no network, no Qt).

A saved EPUB, chapter ZIP/CBZ or chapter folder names each chapter by its number in the book
("chapter0004.xhtml", "0004 Title/"), so the chapters of a range can be copied out of it into a
new file named after that range. Nothing is scraped again: what is not in the saved file is not
in the cut either. Formats that are one undivided document (PDF, single TXT, HTML) cannot be cut.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from noveldb.core import chapter_range

_NUMBER = re.compile(r"^\s*(\d+)")
_CHAPTER_DOC = re.compile(r"(?:chapter|chap|ch)[_\- ]?(\d+)\.x?html?$", re.I)
_LINK = re.compile(r"""(?:src|href|xlink:href)\s*=\s*["']([^"'#]+)""", re.I)
_ITEM = re.compile(r"<item\b[^>]*?/?>(?:\s*</item>)?", re.I)
_ITEMREF = re.compile(r"<itemref\b[^>]*?/?>(?:\s*</itemref>)?", re.I)
_NAVPOINT = re.compile(r"<navPoint\b(?:(?!<navPoint\b).)*?</navPoint>\s*", re.I | re.S)
_LIST_ITEM = re.compile(r"<li\b(?:(?!<li\b).)*?</li>\s*", re.I | re.S)
_ATTRIBUTE = r"""\b%s\s*=\s*["']([^"']*)["']"""


def _attribute(tag: str, name: str) -> str:
    found = re.search(_ATTRIBUTE % name, tag, re.I)
    return found.group(1) if found else ""


def _chapter_of(name: str) -> Optional[int]:
    found = _NUMBER.match(name)
    return int(found.group(1)) if found else None


def target_path(source: Path, low, high, listed: str) -> Path:
    """Where the cut of `source` goes: beside it, named after the book and the range."""
    source = Path(source)
    base = chapter_range.split_name(source.stem)[0]
    return source.with_name(f"{base} ({chapter_range.named(low, high, listed)}){source.suffix}")


def _cut_folder(source: Path, target: Path, wanted: Callable[[int], bool]) -> int:
    # One folder per chapter (pages), or one file per chapter (separate TXT).
    chapters = [(entry, _chapter_of(entry.name)) for entry in sorted(source.iterdir())]
    keep = [entry for entry, number in chapters if number is not None and wanted(number)]
    if not keep:
        return 0
    staging = target.with_name(target.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for entry in keep:
        if entry.is_dir():
            shutil.copytree(entry, staging / entry.name)
        else:
            shutil.copy2(entry, staging / entry.name)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return len(keep)


def _cut_archive(source: Path, target: Path, wanted: Callable[[int], bool]) -> int:
    """A ZIP or CBZ with one folder per chapter."""
    staging = target.with_name(target.name + ".part")
    kept = set()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(staging, "w") as output:
        for info in archive.infolist():
            if info.is_dir():
                continue
            # Files beside the chapter folders (a cover, the comic's info) stay.
            if "/" in info.filename:
                number = _chapter_of(info.filename.split("/", 1)[0])
                if number is None or not wanted(number):
                    continue
                kept.add(number)
            output.writestr(info, archive.read(info.filename), compress_type=info.compress_type)
    if not kept:
        staging.unlink(missing_ok=True)
        return 0
    os.replace(staging, target)
    return len(kept)


def _links(text: str, base: str) -> set:
    found = set()
    for link in _LINK.findall(text):
        if "://" in link or link.startswith(("data:", "mailto:")):
            continue
        found.add(posixpath.normpath(posixpath.join(base, link)))
    return found


def _cut_epub(source: Path, target: Path, wanted: Callable[[int], bool]) -> int:
    staging = target.with_name(target.name + ".part")
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        container = archive.read("META-INF/container.xml").decode("utf-8", "replace")
        package = _attribute(re.search(r"<rootfile\b[^>]*>", container, re.I).group(0), "full-path")
        base = posixpath.dirname(package)
        opf = archive.read(package).decode("utf-8", "replace")

        def member(href: str) -> str:
            return posixpath.normpath(posixpath.join(base, href))

        chapters = {}
        for tag in _ITEM.findall(opf):
            href = _attribute(tag, "href")
            found = _CHAPTER_DOC.search(posixpath.basename(href))
            if found:
                chapters[member(href)] = int(found.group(1))
        keep = {name for name, number in chapters.items() if wanted(number)}
        if not keep:
            return 0
        dropped = set(chapters) - keep
        # Files only the dropped chapters use (their pages' images) go with them.
        documents = [name for name in names if name.lower().endswith((".xhtml", ".html", ".htm", ".css", ".opf"))
                     and name not in dropped and name != package]
        used = set()
        for name in documents:
            used |= _links(archive.read(name).decode("utf-8", "replace"), posixpath.dirname(name))
        cover = {member(_attribute(tag, "href")) for tag in _ITEM.findall(opf)
                 if "cover" in (_attribute(tag, "id") + _attribute(tag, "properties")).lower()}
        gone = set(dropped)
        for name in dropped:
            gone |= _links(archive.read(name).decode("utf-8", "replace"), posixpath.dirname(name)) - used - cover - keep

        ids = set()

        def item(found):
            tag = found.group(0)
            if member(_attribute(tag, "href")) in gone:
                ids.add(_attribute(tag, "id"))
                return ""
            return tag

        opf = _ITEM.sub(item, opf)
        opf = _ITEMREF.sub(lambda found: "" if _attribute(found.group(0), "idref") in ids else found.group(0), opf)

        def pruned(name: str, text: str) -> str:
            folder = posixpath.dirname(name)

            def entry(found):
                return "" if _links(found.group(0), folder) & gone else found.group(0)

            if name.lower().endswith(".ncx"):
                return _NAVPOINT.sub(entry, text)
            return _LIST_ITEM.sub(entry, text)

        navigation = {name for name in names if name.lower().endswith(".ncx")}
        navigation |= {member(_attribute(tag, "href")) for tag in _ITEM.findall(opf)
                       if "nav" in _attribute(tag, "properties").lower().split()}
        with zipfile.ZipFile(staging, "w") as output:
            for info in archive.infolist():
                name = info.filename
                if name in gone:
                    continue
                data = archive.read(name)
                if name == package:
                    data = opf.encode("utf-8")
                elif name in navigation:
                    data = pruned(name, data.decode("utf-8", "replace")).encode("utf-8")
                output.writestr(info, data, compress_type=info.compress_type)
    os.replace(staging, target)
    return len(keep)


def cut(source, low, high, listed: str = "") -> Tuple[Optional[Path], int]:
    """Write the chapters `low..high` (and comma list) of the saved `source` beside it.

    Returns `(new path, chapters in it)`; `(None, 0)` when the format cannot be cut or holds none of
    those chapters. A file that already is a cut of exactly that name is written again (it is cheap,
    and the source may be newer).
    """
    source = Path(source)

    def wanted(number: int) -> bool:
        return chapter_range.selects(low, high, listed, number)

    target = target_path(source, low, high, listed)
    if target == source:
        return source, -1
    suffix = source.suffix.lower()
    try:
        if source.is_dir():
            count = _cut_folder(source, target, wanted)
        elif suffix == ".epub":
            count = _cut_epub(source, target, wanted)
        elif suffix in (".zip", ".cbz") and zipfile.is_zipfile(source):
            count = _cut_archive(source, target, wanted)
        else:
            return None, 0
    except (OSError, zipfile.BadZipFile, KeyError, AttributeError):
        part = target.with_name(target.name + ".part")
        if part.is_dir():
            shutil.rmtree(part, ignore_errors=True)
        else:
            part.unlink(missing_ok=True) if part.exists() else None
        return None, 0
    return (target, count) if count else (None, 0)


def cut_outputs(outputs, low, high, listed: str = "") -> Tuple[List[str], List[str]]:
    """Cut every saved output that still exists. Returns `(new files, names that could not be cut)`."""
    made: List[str] = []
    failed: List[str] = []
    for value in outputs:
        if not value or not os.path.exists(value):
            continue
        path, count = cut(value, low, high, listed)
        if path is None:
            failed.append(os.path.basename(str(value)))
        elif str(path) not in made:
            made.append(str(path))
    return made, failed
