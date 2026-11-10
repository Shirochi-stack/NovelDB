"""Persistent Library refresh preferences and safe EPUB replacement."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from noveldb.core.library import epub_contents
from noveldb.core.settings.store import read_raw, write_raw


def source_key(url, source=None):
    if source and source != "reader":
        return source
    host = (urlsplit(url).hostname or url).lower()
    return ".".join(host.split(".")[-2:])


class LibraryUpdates:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self.data = {"enabled": True, "background": False, "uploadDrive": False, "books": {}}
        self.data.update(read_raw(path))

    def entry(self, path):
        return self.data["books"].get(str(Path(path).resolve()), {})

    def allowed(self, path):
        return bool(self.data["enabled"] and self.preferred(path))

    def preferred(self, path):
        entry = self.entry(path)
        return bool(entry.get("url") and
                    entry.get("autoUpdate", not entry.get("ranged", True)))

    def record(self, path, url, ranged=True):
        with self._lock:
            key = str(Path(path).resolve())
            old = self.data["books"].get(key, {})
            self.data["books"][key] = {**old, "url": url, "ranged": ranged}
            write_raw(self.data, self.path)

    def set_books(self, paths, enabled):
        with self._lock:
            for path in paths:
                self.data["books"].setdefault(str(Path(path).resolve()), {})["autoUpdate"] = enabled
            write_raw(self.data, self.path)

    def configure(self, **changes):
        with self._lock:
            self.data.update(changes)
            write_raw(self.data, self.path)

    def discover(self, books, jobs):
        changed = False
        with self._lock:
            for book in books:
                for file in book.files:
                    key = str(file.path.resolve())
                    if file.format != "EPUB" or key in self.data["books"]:
                        continue
                    history = next((j for j in reversed(jobs) if str(file.path) in j.outputs), None)
                    self.data["books"][key] = {
                        "url": book.url or (history.input if history else ""),
                        "ranged": history.download_ranged is not False if history else True,
                    }
                    changed = True
            if changed:
                write_raw(self.data, self.path)


def replace_epub(candidate, target):
    """Validate a complete replacement before atomically changing the saved book."""
    before = epub_contents(Path(target))["chapters"]
    after = epub_contents(Path(candidate))["chapters"]
    if not after or len(after) < len(before):
        raise ValueError("The refreshed EPUB has fewer chapters than the saved book")
    os.replace(candidate, target)
