"""Unpacked chapter output, with staged replacement of an existing export."""
from __future__ import annotations

import html
import io
import re
import shutil
import struct
import zlib
from collections import deque
from contextlib import contextmanager
from itertools import chain, islice
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


@contextmanager
def folder_export(path, *, also_zip=False, prepared=None):
    target = Path(path)
    archive = target.suffix.lower() == ".zip"
    if target.suffix.lower() not in {".folder", ".zip"}:
        raise ValueError("Chapter output requires a .folder or .zip path")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise ValueError("Folder output cannot replace a link")
    if target.exists() and target.is_dir() == archive:
        raise ValueError("The output path is occupied by a different file type")
    zip_target = target if archive else target.with_suffix(".zip") if also_zip else None
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
        previous = target.with_name(f".{target.name}.{uuid4().hex}.previous")
        if target.exists():
            target.rename(previous)
        try:
            stage.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise
        if previous.exists():
            shutil.rmtree(previous)
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


def write_chapter(root, number, title, text, pages, links=(), *, max_height=None, chapter_folders=False,
                  compression=None):
    """Write one chapter without reordering or splitting its source images."""
    if not (text or pages or links):
        return
    if max_height is not None and compression:
        cap = {"WEBP": 16_383, "AVIF": 16_383, "JPEG": 65_500}.get(compression[0])
        if cap:
            max_height = min(max_height or cap, cap)
    full_strip = max_height == 0 and len(pages) > 1
    if max_height and len(pages) > 1:
        strips = stitch_panels((page for _, page in pages), max_height, compression=compression)
        first = list(islice(strips, 2))
        chapter_folders = chapter_folders or len(first) > 1
        pages = enumerate(chain(first, strips), 1)
        compression = None
    folder = chapter_directory(root, number, title) if chapter_folders else root
    prefix = "" if chapter_folders else chapter_name(number, title) + " - "

    def target(filename):
        path = folder / (prefix + filename)
        counter = 2
        while path.exists():
            stem, ext = filename.rsplit(".", 1)
            path = folder / f"{prefix}{stem} ({counter}).{ext}"
            counter += 1
        return path

    if text:
        target("chapter.txt").write_text(text + "\n", encoding="utf-8")
    if links:
        target("images.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
    if full_strip and _write_full_strip(target("001.png"), [page for _, page in pages]):
        return
    for position, page in pages:
        extension, raw = _compress_panel(page, compression)
        target(f"{position:03d}.{extension}").write_bytes(raw)


def _compress_panel(page, compression):
    if not compression:
        return page
    from noveldb.core.images import Image, _encode_image_bytes

    if Image is None:
        return page
    try:
        with Image.open(io.BytesIO(page[1])) as source:
            data, ext = _encode_image_bytes(source, *compression)
            return ext, data
    except (OSError, ValueError, MemoryError, Image.DecompressionBombError):
        return page


def chapter_text(markup: str) -> str:
    text = re.sub(r"<(?:br\b|/(?:p|div|li|h[1-6])\b)[^>]*>", "\n", markup, flags=re.I)
    return html.unescape(re.sub(r"<[^>]*>", "", text)).strip()


def _write_full_strip(path, pages):
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


def stitch_panels(pages, max_height=16_000, *, compression=None):
    """Combine adjacent slices within a bounded image size."""
    from PIL import Image

    def render(group, width, height):
        if len(group) == 1:
            return [_compress_panel(group[0][0], compression)]
        try:
            with Image.new("RGB", (width, height), "white") as strip:
                top = 0
                for (_, data), size in group:
                    with Image.open(io.BytesIO(data)) as source:
                        origin = ((width - source.width) // 2, top)
                        if source.mode == "RGB":
                            strip.paste(source, origin)
                        else:
                            with source.convert("RGBA") as panel:
                                strip.paste(panel, origin, panel.getchannel("A"))
                    top += size
                if compression:
                    from noveldb.core.images import _encode_image_bytes

                    data, ext = _encode_image_bytes(strip, *compression)
                    return [(ext, data)]
                encoded = io.BytesIO()
                strip.save(encoded, "PNG")
                return [("png", encoded.getvalue())]
        except (OSError, ValueError, MemoryError):
            return [_compress_panel(page, compression) for page, _ in group]

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
            yield from render(group, width, height)
            group, width, height = [], 0, 0
            limit = min(max_height, 24_000_000 // max(w, 1))
        if w <= 0 or h <= 0 or h > limit:
            yield _compress_panel(page, compression)
            continue
        width = max(width, w)
        group.append((page, h))
        height += h
    if group:
        yield from render(group, width, height)
