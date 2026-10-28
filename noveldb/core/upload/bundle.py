"""Bundling several files into a few ZIPs, so one upload gives one link instead of one per book.

`plan_bundles` packs the files first-fit decreasing under a size limit, `bundle_zip_name` names each
ZIP after what it holds, and `build_zip` writes it (original UTF-8 file names, EPUB/CBZ/PDF/ZIP
entries stored because they are already compressed). Nothing here talks to a host.
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from noveldb.core.upload.transfer import UploadCancelled, upload_name

MEGABYTE = 1024 * 1024
# What each ZIP entry costs beyond its file size (headers, directory record, rounding).
ENTRY_OVERHEAD = 1024
# Already compressed; deflating them again only costs time.
STORED_EXTENSIONS = (".epub", ".cbz", ".pdf", ".zip")
ZIP_PREFIX = "NovelDB"
_UTF8_NAME_FLAG = 0x800
_CHUNK = 1024 * 1024

Prepare = Callable[[int, int], None]  # (bytes written, bytes in total)


@dataclass(frozen=True)
class UploadItem:
    """One file (or a folder of separate TXT files) to upload, with what names it."""

    path: str
    book_id: str = ""
    source: str = ""
    title: str = ""

    @property
    def label(self) -> str:
        """The book's title, else the file name without its extension."""
        return self.title or os.path.splitext(upload_name(self.path))[0]


@dataclass
class Bundle:
    """One upload: a lone file, or a ZIP (`zip_name`) holding two or more."""

    items: list[UploadItem]
    zip_name: str = ""
    size: int = 0  # bytes of the files, before the ZIP overhead

    @property
    def is_zip(self) -> bool:
        return bool(self.zip_name)

    @property
    def all_epubs(self) -> bool:
        return all(is_epub(item.path) for item in self.items)

    @property
    def titles(self) -> list[str]:
        return [item.label for item in self.items]


def is_epub(path: str) -> bool:
    return path.lower().endswith(".epub") and not os.path.isdir(path)


def item_size(path: str) -> int:
    """Bytes of a file, or of every file inside a folder; 0 when it cannot be read."""
    try:
        if os.path.isdir(path):
            return sum(os.path.getsize(os.path.join(root, name))
                       for root, _dirs, names in os.walk(path) for name in names)
        return os.path.getsize(path)
    except OSError:
        return 0


def describe(name: str, entries: int = 1, epubs: bool = False) -> str:
    """"NovelDB_3_EPUBs.zip (3 books)" for a ZIP; just the name for a lone file."""
    if entries < 2:
        return name
    return f"{name} ({entries} {'books' if epubs else 'files'})"


def bundle_zip_name(items: Sequence[UploadItem], taken: set[str]) -> str:
    """`NovelDB_<n>_EPUBs.zip` (every entry an EPUB) or `NovelDB_<n>_files.zip`; `_2`, `_3`... if used."""
    kind = "EPUBs" if all(is_epub(item.path) for item in items) else "files"
    base = f"{ZIP_PREFIX}_{len(items)}_{kind}"
    name, number = f"{base}.zip", 2
    while name in taken:
        name = f"{base}_{number}.zip"
        number += 1
    taken.add(name)
    return name


def plan_bundles(items: Sequence[UploadItem], limit_bytes: int, enabled: bool = True) -> list[Bundle]:
    """Group `items` into as few uploads as possible, each ZIP under `limit_bytes`.

    Files go largest first into the first ZIP with room (file size plus `ENTRY_OVERHEAD` each). A
    file that cannot fit even alone, and a ZIP that would hold a single file, upload as the file
    itself. With bundling off or fewer than two files every file is its own upload. Uploads keep the
    order of their first file in `items`, and the files inside a ZIP keep theirs.
    """
    items = list(items)
    sizes = [item_size(item.path) for item in items]
    if not enabled or len(items) < 2:
        return [Bundle([item], size=size) for item, size in zip(items, sizes)]
    bins: list[list[int]] = []
    totals: list[int] = []
    for index in sorted(range(len(items)), key=lambda i: (-sizes[i], i)):
        cost = sizes[index] + ENTRY_OVERHEAD
        if cost > limit_bytes:
            bins.append([index])  # too big for any ZIP: goes up alone, as the file
            totals.append(limit_bytes)  # ...and nothing else joins it
            continue
        for slot, used in enumerate(totals):
            if used + cost <= limit_bytes:
                bins[slot].append(index)
                totals[slot] += cost
                break
        else:
            bins.append([index])
            totals.append(cost)
    bundles: list[Bundle] = []
    taken: set[str] = set()
    for members in sorted((sorted(b) for b in bins), key=lambda b: b[0]):
        group = [items[i] for i in members]
        size = sum(sizes[i] for i in members)
        name = bundle_zip_name(group, taken) if len(group) > 1 else ""
        bundles.append(Bundle(group, name, size))
    return bundles


class _Utf8Info(zipfile.ZipInfo):
    """A ZIP entry whose name is always UTF-8 with the language-encoding flag set.

    `zipfile` sets that flag only for names that are not ASCII (and resets `flag_bits` when it opens
    an entry for writing), so ASCII names would go out without it.
    """

    def _encodeFilenameFlags(self):
        return self.filename.encode("utf-8"), self.flag_bits | _UTF8_NAME_FLAG


def entry_compression(name: str) -> int:
    return zipfile.ZIP_STORED if name.lower().endswith(STORED_EXTENSIONS) else zipfile.ZIP_DEFLATED


def unique_entry_names(names: Iterable[str]) -> list[str]:
    """`names` with repeats made distinct ("Book.epub", "Book (2).epub"), compared ignoring case."""
    seen: set[str] = set()
    result = []
    for name in names:
        candidate, number = name, 2
        stem, ext = os.path.splitext(name)
        while candidate.lower() in seen:
            candidate = f"{stem} ({number}){ext}"
            number += 1
        seen.add(candidate.lower())
        result.append(candidate)
    return result


def zip_folder(folder: str, staging: str) -> str:
    """Folder outputs (separate TXT files) upload as one zip named after the folder."""
    target = os.path.join(staging, os.path.basename(os.path.normpath(folder)) + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                full = os.path.join(root, name)
                archive.write(full, os.path.relpath(full, os.path.dirname(os.path.normpath(folder))))
    return target


def build_zip(
    bundle: Bundle,
    directory: str,
    progress: Optional[Prepare] = None,
    stopped: Callable[[], bool] = lambda: False,
) -> str:
    """Write `bundle` as `directory/<zip name>`: one entry per file under its original name, no folders.

    A folder item goes in as its own ZIP (`<folder>.zip`, stored). `progress` gets bytes written out
    of the files' total; `stopped()` aborts with `UploadCancelled`, and a partial ZIP is deleted.
    """
    target = os.path.join(directory, bundle.zip_name)
    sources: list[str] = []
    for index, item in enumerate(bundle.items):
        if os.path.isdir(item.path):
            nested = os.path.join(directory, f"folder-{index}")
            os.makedirs(nested, exist_ok=True)
            sources.append(zip_folder(item.path, nested))
        else:
            sources.append(item.path)
    names = unique_entry_names(upload_name(source) for source in sources)
    total, written = max(bundle.size, 1), 0
    try:
        with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
            for source, name in zip(sources, names):
                info = _Utf8Info.from_file(source, name, strict_timestamps=False)
                info.compress_type = entry_compression(name)
                with open(source, "rb") as reader, archive.open(info, "w") as writer:
                    while True:
                        if stopped():
                            raise UploadCancelled("Upload cancelled")
                        chunk = reader.read(_CHUNK)
                        if not chunk:
                            break
                        writer.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(min(written, total), total)
        if progress:
            progress(total, total)
    except BaseException:
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    return target
