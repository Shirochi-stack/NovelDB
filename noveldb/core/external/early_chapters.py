"""Folder and ZIP chapters written (and stitched) while the chapter lists are still being read."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from threading import Lock

from noveldb.core.chapter_folder import ChapterWrites


class _Left(Exception):
    """The chapter is left for the export step (a page is not here yet, or Stop was pressed)."""


class EarlyChapters:
    """Writes a chapter's Folder/ZIP files as soon as its pages are in.

    The export step used to begin only after the last chapter was read, so a long comic was stitched
    in one go at the very end. Here every chapter that was read is handed over (`offer`), and one
    writer thread takes them a chapter at a time: it waits for the chapter's pages (the ones its
    source saved, or the image lookahead's), then writes and stitches it exactly as the export step
    would. That step continues with `writes` and only handles what was left: a chapter with a page
    that could not be fetched here is not written, and stays the export's to do.

    One chapter's pages are in memory at a time: the queue holds chapters, not their images.
    """

    def __init__(self, job, output_dir):
        self._job = job
        self._lock = Lock()
        self._futures = []
        _links_only, self._height, self._folders = job._folder_options()
        self._stitch = job._stitch_compression() if self._height is not None else None
        parent = job._parent_gui
        compress = getattr(parent, "var_compress_images", None)
        self._compress = bool(compress and compress.get())
        self._quality = parent.var_jpeg_quality.get() if self._compress else None
        self._format = parent.var_image_format.get() if self._compress else None
        # The export step moves this folder into place; cleaning up an absent one is not an error.
        self._folder = tempfile.TemporaryDirectory(dir=output_dir, prefix=".noveldb-chapters-",
                                                   ignore_cleanup_errors=True)
        self._pool = ThreadPoolExecutor(max_workers=1)
        # What the export step goes on with: its `submitted` are the chapters written here.
        self.writes = ChapterWrites(Path(self._folder.name), self._pool)

    def offer(self, index, chapter):
        """A chapter that was just read (any thread): queued once."""
        if not chapter or chapter.get("_locked"):
            return
        with self._lock:
            if index in self.writes.submitted:
                return
            self.writes.submitted.add(index)
            self._futures.append(self._pool.submit(self._write, index, chapter))

    def _write(self, index, chapter):
        job = self._job
        try:
            lookahead = getattr(job, "_kpage_images", None)
            pages = []
            for position, info in enumerate(job._chapter_images(chapter), 1):
                if job._download_cancelled:
                    raise _Left()
                raw = job._decode_image_data_url(info.get("data"))
                url = unescape(info.get("url") or "").strip()
                if not raw and url and lookahead is not None and not info.get("candidates"):
                    raw = lookahead.read(url)  # waits for the download when it is still on its way
                if not raw:
                    raise _Left()
                if self._compress and not self._stitch:
                    raw = job._compress_image(raw, self._quality, self._format)
                pages.append((position, (job._image_ext_from_bytes(raw), raw)))
            if job._download_cancelled:
                raise _Left()
            job._write_export_chapter(self.writes.root, index, chapter, pages, (), self._height, self._folders,
                                      self._stitch)
        except Exception as error:
            with self._lock:
                self.writes.submitted.discard(index)
            if not isinstance(error, _Left):
                job._log(f"  Could not prepare chapter {index + 1} early ({error}); it is written during export.")

    def waiting(self):
        """How many chapters are still queued or being written."""
        with self._lock:
            return sum(1 for future in self._futures if not future.done())

    def finish(self):
        """Waits for every chapter handed over so far; `writes.submitted` is final after it."""
        with self._lock:
            futures = list(self._futures)
        for future in futures:
            try:
                future.result()
            except Exception:
                pass

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._folder.cleanup()
