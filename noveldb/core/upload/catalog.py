"""Upload a finished book to the first configured host that accepts it, and remember the link."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import zipfile
from dataclasses import asdict
from typing import Callable, Iterable, Optional

from noveldb.core.upload.hosts import UploadContext, UploadResult, uploader
from noveldb.core.upload.transfer import NotSignedIn, UploadCancelled, UploadError, ascii_upload_name

DEFAULT_HOSTS = ("tempsh", "gofile")


def zip_folder(folder: str, staging: str) -> str:
    """Folder outputs (separate TXT files) upload as one zip named after the folder."""
    target = os.path.join(staging, os.path.basename(os.path.normpath(folder)) + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                full = os.path.join(root, name)
                archive.write(full, os.path.relpath(full, os.path.dirname(os.path.normpath(folder))))
    return target


def upload(
    path: str,
    hosts: Iterable[str],
    context: UploadContext,
    progress: Optional[Callable[[int, int], None]] = None,
    on_host: Optional[Callable[[str], None]] = None,
    history: Optional["UploadHistory"] = None,
    book_id: str = "",
) -> UploadResult:
    """Try each host in order; `on_host` gets each host's name just before it is tried.

    `book_id` names the upload when the title is not plain ASCII (see `ascii_upload_name`).
    """
    staging = None
    payload = path
    if os.path.isdir(path):
        staging = tempfile.mkdtemp(prefix="noveldb-upload-")
        payload = zip_folder(path, staging)
    try:
        last: Exception = UploadError("No upload host is configured.")
        for host_id in hosts or DEFAULT_HOSTS:
            host = uploader(host_id)
            if host is None:
                continue
            if not host.is_ready(context):
                last = NotSignedIn(host.display_name)
                continue
            try:
                host.check(payload)
                if on_host:
                    on_host(host.display_name)
                result = host.upload(payload, context, progress, name=ascii_upload_name(payload, book_id))
                if history is not None:
                    history.add(result)
                return result
            except UploadCancelled:
                raise
            except Exception as exc:  # fall back to the next host
                last = exc
        raise last
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


class UploadHistory:
    """Past uploads (newest first) in `upload-history.json`, like the mobile Uploads tab."""

    def __init__(self, path: str, limit: int = 500) -> None:
        self._path = path
        self._limit = limit
        self._lock = threading.Lock()

    def load(self) -> list:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        return [UploadResult(**item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def add(self, result: UploadResult) -> None:
        with self._lock:
            items = [result] + self.load()
            from noveldb.core.settings.store import write_raw

            write_raw([asdict(item) for item in items[: self._limit]], self._path)

    def clear(self) -> None:
        with self._lock:
            from noveldb.core.settings.store import write_raw

            write_raw([], self._path)
