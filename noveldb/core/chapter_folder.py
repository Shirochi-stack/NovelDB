"""Unpacked chapter output, with staged replacement of an existing export."""
from __future__ import annotations

import html
import io
import os
import re
import shutil
import struct
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from itertools import chain, islice
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


def _chapter_key(entry: Path) -> str:
    """"0004 Title" for a chapter's folder and for each of its files ("0004 Title - 001.jpg")."""
    return entry.name if entry.is_dir() else entry.name.rsplit(" - ", 1)[0]


def _carry_over(existing: Path, stage: Path, moved: list) -> None:
    """Move what `existing` holds and `stage` does not into `stage`: the chapters of earlier
    downloads of the book. A chapter written again replaces the saved one. `moved` gets each
    (old place, new place), so a failed export can put them back."""
    fresh = {_chapter_key(entry) for entry in stage.iterdir()}
    for entry in sorted(existing.iterdir()):
        if _chapter_key(entry) in fresh:
            continue
        entry.rename(stage / entry.name)
        moved.append((entry, stage / entry.name))


MOVE_TRIES = 4       # a virus scanner or the thumbnail maker holds a new file for a moment
MOVE_PAUSE = 0.35    # seconds between tries


def _set_aside(target: Path):
    """Move the existing export out of the way; its new place, or None when it cannot be moved.

    Windows refuses to move a folder while a program has it, or a file inside it, open ("Access
    is denied"). A short hold passes by itself, so the move is tried a few times.
    """
    previous = target.with_name(f".{target.name}.{uuid4().hex}.previous")
    for attempt in range(MOVE_TRIES):
        try:
            target.rename(previous)
            return previous
        except OSError:
            if attempt + 1 < MOVE_TRIES:
                time.sleep(MOVE_PAUSE)
    return None


def _remove(entry: Path) -> None:
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def _update_in_place(target: Path, stage: Path, keep_others: bool) -> None:
    """Put the staged chapters into `target` itself, each in place of its saved copy.

    For a folder that cannot be moved as a whole. A chapter whose saved copy is open in another
    program keeps that copy; every other chapter is replaced, and the ones left are named in
    the error. Without `keep_others` (a whole-book export) what the stage does not hold goes,
    unless it is open too.
    """
    token = uuid4().hex
    fresh, saved = {}, {}
    for entry in sorted(stage.iterdir()):
        fresh.setdefault(_chapter_key(entry), []).append(entry)
    for entry in sorted(target.iterdir()):
        saved.setdefault(_chapter_key(entry), []).append(entry)
    held = []
    for key, entries in fresh.items():
        aside = []
        try:
            for old in saved.pop(key, []):
                hidden = old.with_name(f".{old.name}.{token}.previous")
                old.rename(hidden)
                aside.append((old, hidden))
            for entry in entries:
                entry.rename(target / entry.name)
        except OSError:
            for old, hidden in aside:  # the saved copy stays whole
                try:
                    hidden.rename(old)
                except OSError:
                    pass
            held.append(key)
            continue
        for _, hidden in aside:
            try:
                _remove(hidden)
            except OSError:
                pass
    if not keep_others:
        for entries in saved.values():
            for old in entries:
                try:
                    _remove(old)
                except OSError:
                    pass  # open elsewhere and not part of this export: it simply stays
    if held:
        named = ", ".join(held[:5]) + (f" and {len(held) - 5} more" if len(held) > 5 else "")
        raise PermissionError(
            f"{len(held)} item(s) in {target.name} are open in another program and were not replaced: {named}. "
            "The rest was saved. Close the window or viewer showing them, then download again.")


@contextmanager
def folder_export(path, *, also_zip=False, prepared=None, merge=False, zip_path=None):
    """Stage a chapter export and put it in place of the one at `path` when the block ends.

    With `merge` (a download of some chapters), the chapters the existing folder holds stay: the
    book keeps one folder that every ranged download adds to. `also_zip` packs the staged
    chapters as well, beside the folder or at `zip_path`.
    """
    target = Path(path)
    archive = target.suffix.lower() == ".zip"
    if target.suffix.lower() not in {".folder", ".zip"}:
        raise ValueError("Chapter output requires a .folder or .zip path")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise ValueError("Folder output cannot replace a link")
    if target.exists() and target.is_dir() == archive:
        raise ValueError("The output path is occupied by a different file type")
    zip_target = target if archive else Path(zip_path or target.with_suffix(".zip")) if also_zip else None
    if zip_target is not None and (zip_target.is_dir() or zip_target.is_symlink()):
        raise ValueError("ZIP output requires a regular file path")
    with TemporaryDirectory(dir=target.parent, prefix=".noveldb-folder-") as temp:
        stage = Path(prepared) if prepared is not None else Path(temp) / "book"
        stage.mkdir(exist_ok=prepared is not None)
        yield stage
        if not any(stage.iterdir()):
            raise ValueError("No chapter content was available for Folder output")
        if zip_target is not None:
            packed = Path(temp) / "book.zip"
            with ZipFile(packed, "w", ZIP_DEFLATED) as output:
                for file in sorted(stage.rglob("*")):
                    if file.is_file():
                        method = ZIP_DEFLATED if file.suffix == ".txt" else ZIP_STORED
                        output.write(file, file.relative_to(stage).as_posix(), compress_type=method)
        if archive:
            packed.replace(zip_target)
            return
        # After the ZIP is packed: the ZIP holds this download's chapters only.
        previous = _set_aside(target) if target.exists() else None
        if target.exists():
            # The folder cannot be moved: something has it open (a window showing it, a viewer
            # with one of its pages). Its chapters are replaced one by one where they are.
            _update_in_place(target, stage, keep_others=merge)
        else:
            carried = []
            try:
                if merge and previous is not None:
                    _carry_over(previous, stage, carried)
                stage.rename(target)
            except BaseException:
                # The existing folder comes back as it was, with the chapters taken out of it.
                for old, new in reversed(carried):
                    try:
                        new.rename(old)
                    except OSError:
                        pass
                if previous is not None and not target.exists():
                    previous.rename(target)
                raise
            if previous is not None:
                shutil.rmtree(previous, ignore_errors=True)
        if zip_target is not None:
            packed.replace(zip_target)


class ChapterWrites:
    """Serialize writes and keep at most one chapter waiting behind the active write."""

    def __init__(self, root, pool):
        self.root = root
        self.pool = pool
        self.submitted = set()
        self.pending = deque()

    def submit(self, index, write, *args):
        if len(self.pending) == 2:
            self.pending[0].result()
            self.pending.popleft()
        self.pending.append(self.pool.submit(write, self.root, index, *args))
        self.submitted.add(index)

    def finish(self):
        while self.pending:
            self.pending[0].result()
            self.pending.popleft()


def chapter_directory(root: Path, number: int, title: str) -> Path:
    name = chapter_name(number, title)
    path = root / name
    suffix = 2
    while path.exists():
        path = root / f"{name} ({suffix})"
        suffix += 1
    path.mkdir()
    return path


def chapter_name(number: int, title: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:60].rstrip(" .")
    return f"{max(1, number):04d} {safe or 'Chapter'}"


class ExportStopped(Exception):
    """Stop was pressed while a chapter was written: what it had written so far is removed."""


def write_chapter(root, number, title, text, pages, links=(), *, max_height=None, chapter_folders=False,
                  compression=None, stopped=None):
    """Write one chapter without reordering or splitting its source images.

    `stopped`: asked before every strip and file; once it answers True the chapter's files are
    removed and `ExportStopped` is raised, so a Stop does not wait for the chapter to be stitched.
    """
    if not (text or pages or links):
        return
    written = []
    created = []
    try:
        _write_chapter(root, number, title, text, pages, links, max_height, chapter_folders, compression,
                       stopped or (lambda: False), written, created)
    except ExportStopped:
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        for folder in created:
            try:
                folder.rmdir()
            except OSError:
                pass
        raise


def _write_chapter(root, number, title, text, pages, links, max_height, chapter_folders, compression, stopped,
                   written, created):
    def check():
        if stopped():
            raise ExportStopped()

    check()
    if max_height is not None and compression:
        cap = {"WEBP": 16_383, "AVIF": 16_383, "JPEG": 65_500}.get(compression[0])
        if cap:
            max_height = min(max_height or cap, cap)
    full_strip = max_height == 0 and len(pages) > 1
    if max_height and len(pages) > 1:
        strips = stitch_panels((page for _, page in pages), max_height, compression=compression, stopped=check)
        first = list(islice(strips, 2))
        chapter_folders = chapter_folders or len(first) > 1
        pages = enumerate(chain(first, strips), 1)
        compression = None
    if chapter_folders:
        folder = chapter_directory(root, number, title)
        created.append(folder)
    else:
        folder = root
    prefix = "" if chapter_folders else chapter_name(number, title) + " - "

    def target(filename):
        path = folder / (prefix + filename)
        counter = 2
        while path.exists():
            stem, ext = filename.rsplit(".", 1)
            path = folder / f"{prefix}{stem} ({counter}).{ext}"
            counter += 1
        written.append(path)
        return path

    if text:
        target("chapter.txt").write_text(text + "\n", encoding="utf-8")
    if links:
        target("images.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
    if full_strip and _write_full_strip(target("001.png"), [page for _, page in pages], check):
        return
    for position, page in pages:
        check()
        extension, raw = _compress_panel(page, compression)
        target(f"{position:03d}.{extension}").write_bytes(raw)


def _compress_panel(page, compression):
    if not compression:
        return page
    from noveldb.core.images import Image, _encode_with

    if Image is None:
        return page
    try:
        with Image.open(io.BytesIO(page[1])) as source:
            data, ext = _encode_with(source, compression)
            return ext, data
    except (OSError, ValueError, MemoryError, Image.DecompressionBombError):
        return page


def chapter_text(markup: str) -> str:
    text = re.sub(r"<(?:br\b|/(?:p|div|li|h[1-6])\b)[^>]*>", "\n", markup, flags=re.I)
    return html.unescape(re.sub(r"<[^>]*>", "", text)).strip()


def _write_full_strip(path, pages, stopped=None):
    """Stream one PNG panel at a time, keeping the complete canvas off the heap."""
    from PIL import Image

    width = height = 0
    for _, data in pages:
        try:
            with Image.open(io.BytesIO(data)) as source:
                width = max(width, source.width)
                height += source.height
        except (OSError, ValueError, Image.DecompressionBombError):
            return False
    if max(width, height) > 0x7FFFFFFF:
        raise ValueError("This chapter exceeds PNG's maximum dimensions.")

    with path.open("wb") as output:
        def chunk(kind, data=b""):
            output.write(struct.pack(">I", len(data)))
            output.write(kind)
            output.write(data)
            output.write(struct.pack(">I", zlib.crc32(data, zlib.crc32(kind))))

        output.write(b"\x89PNG\r\n\x1a\n")
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        compressor = zlib.compressobj(3)
        for _, data in pages:
            if stopped:
                stopped()
            with Image.open(io.BytesIO(data)) as source:
                if source.mode == "RGB" and source.width == width:
                    pixels = source.tobytes()
                else:
                    with source.convert("RGBA") as panel, Image.new("RGB", (width, source.height), "white") as aligned:
                        aligned.paste(panel, ((width - panel.width) // 2, 0), panel.getchannel("A"))
                        pixels = aligned.tobytes()
            stride = width * 3
            for start in range(0, len(pixels), stride):
                encoded = compressor.compress(b"\0" + pixels[start:start + stride])
                if encoded:
                    chunk(b"IDAT", encoded)
        chunk(b"IDAT", compressor.flush())
        chunk(b"IEND")
    return True


def stitch_workers():
    """How many strips of a chapter are rendered at once. Encoding a tall strip is nearly all of the
    stitching time (WebP: over a second each), and it runs outside the interpreter lock, so strips
    side by side are that many times faster. Each one holds its canvas (up to about 75 MB) while it
    is rendered, hence the small number."""
    return min(4, max(1, (os.cpu_count() or 2) // 2))


def stitch_panels(pages, max_height=16_000, *, compression=None, stopped=None, workers=None):
    """Combine adjacent slices within a bounded image size, in order. `stopped` is called before
    every strip and page (it raises to end the stitching). `workers`: strips rendered at once
    (`stitch_workers()` by default); one renders them as they are asked for."""
    from PIL import Image

    def render(group, width, height):
        if stopped:
            stopped()
        if len(group) == 1:
            return [_compress_panel(group[0][0], compression)]
        try:
            with Image.new("RGB", (width, height), "white") as strip:
                top = 0
                for (_, data), size in group:
                    if stopped:
                        stopped()
                    with Image.open(io.BytesIO(data)) as source:
                        origin = ((width - source.width) // 2, top)
                        if source.mode == "RGB":
                            strip.paste(source, origin)
                        else:
                            with source.convert("RGBA") as panel:
                                strip.paste(panel, origin, panel.getchannel("A"))
                    top += size
                if compression:
                    from noveldb.core.images import _encode_with

                    data, ext = _encode_with(strip, compression)
                    return [(ext, data)]
                encoded = io.BytesIO()
                strip.save(encoded, "PNG")
                return [("png", encoded.getvalue())]
        except (OSError, ValueError, MemoryError):
            return [_compress_panel(page, compression) for page, _ in group]

    def single(page):
        if stopped:
            stopped()
        return [_compress_panel(page, compression)]

    def strips():
        """The strips to make, in order, each as a call that returns its files."""
        group = []
        width = height = 0
        for page in pages:
            try:
                with Image.open(io.BytesIO(page[1])) as image:
                    w, h = image.size
            except (OSError, ValueError, Image.DecompressionBombError):
                w, h = 0, 0
            limit = min(max_height, 24_000_000 // max(width, w, 1))
            if group and (w <= 0 or h <= 0 or height + h > limit):
                yield partial(render, group, width, height)
                group, width, height = [], 0, 0
                limit = min(max_height, 24_000_000 // max(w, 1))
            if w <= 0 or h <= 0 or h > limit:
                yield partial(single, page)
                continue
            width = max(width, w)
            group.append((page, h))
            height += h
        if group:
            yield partial(render, group, width, height)

    workers = stitch_workers() if workers is None else max(1, workers)
    if workers == 1:
        for strip in strips():
            yield from strip()
        return
    # A few strips ahead of the one being written, never the whole chapter: a strip that is done waits
    # as its (compressed) file, one in the works holds its canvas.
    pool = ThreadPoolExecutor(max_workers=workers)
    waiting = deque()
    try:
        for strip in strips():
            waiting.append(pool.submit(strip))
            if len(waiting) >= workers * 2:
                yield from waiting.popleft().result()
        while waiting:
            yield from waiting.popleft().result()
    finally:
        # A Stop (or a failed strip) ends here: the strips not started are dropped, the ones in the
        # works end at their own stop check.
        pool.shutdown(wait=True, cancel_futures=True)
