"""The Catalog's offline cache: book details and small cover images, kept on disk within a size limit.

Entries are files under the cache folder (`catalog-cache/`, any platform), named by a hash of their
kind and key; a cover's key carries the compression settings it was made with, so new settings make
new covers. Reading an entry marks it used; when the folder grows past its limit the least recently
used entries go first. A limit of 0 turns the cache off.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from noveldb.core import paths

FOLDER = "catalog-cache"
DEFAULT_MB = 300


class OfflineCache:
    def __init__(self, limit_mb: Callable[[], int], folder: Optional[Path] = None):
        self.limit_mb = limit_mb
        self.folder = Path(folder) if folder is not None else paths.cache_dir() / FOLDER
        self._lock = threading.Lock()
        self._writes = 0

    def _path(self, kind: str, key: str) -> Path:
        digest = hashlib.sha1(f"{kind}\n{key}".encode("utf-8")).hexdigest()
        return self.folder / kind / digest[:2] / digest

    @property
    def enabled(self) -> bool:
        return (self.limit_mb() or 0) > 0

    def get(self, kind: str, key: str, max_age: Optional[float] = None) -> Optional[bytes]:
        if not self.enabled:
            return None
        path = self._path(kind, key)
        try:
            stat = path.stat()
            if max_age is not None and time.time() - stat.st_mtime > max_age:
                return None
            data = path.read_bytes()
            os.utime(path, (time.time(), stat.st_mtime))  # used now (access time), written then (mtime)
            return data
        except OSError:
            return None

    def put(self, kind: str, key: str, data: bytes) -> None:
        if not self.enabled or not data:
            return
        path = self._path(kind, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            return
        with self._lock:
            self._writes += 1
            check = self._writes % 50 == 1
        if check:
            threading.Thread(target=self.trim, name="catalog-cache-trim", daemon=True).start()

    def get_json(self, kind: str, key: str, max_age: Optional[float] = None) -> Optional[dict]:
        data = self.get(kind, key, max_age)
        try:
            return json.loads(data) if data else None
        except ValueError:
            return None

    def put_json(self, kind: str, key: str, value) -> None:
        self.put(kind, key, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def size(self) -> int:
        total = 0
        for path in self.folder.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                pass
        return total

    def trim(self) -> None:
        """The least recently used entries go until the folder is within its limit."""
        limit = max(0, int(self.limit_mb() or 0)) * 1024 * 1024
        entries = []
        for path in self.folder.rglob("*"):
            try:
                if path.is_file():
                    stat = path.stat()
                    entries.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
            except OSError:
                pass
        total = sum(size for _used, size, _path in entries)
        for _used, size, path in sorted(entries):
            if total <= limit:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass

    def clear(self) -> None:
        import shutil

        shutil.rmtree(self.folder, ignore_errors=True)
