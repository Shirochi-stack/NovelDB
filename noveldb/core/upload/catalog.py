"""Upload finished books to the first configured host that accepts them, and remember the links."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Callable, Iterable, Optional, Sequence

from noveldb.core.settings.model import UPLOAD_ZIP_MB_DEFAULT
from noveldb.core.upload.bundle import (  # noqa: F401  (zip_folder is re-exported)
    MEGABYTE,
    Bundle,
    UploadItem,
    build_zip,
    describe,
    plan_bundles,
    zip_folder,
)
from noveldb.core.upload.hosts import UploadContext, UploadResult, uploader
from noveldb.core.upload.transfer import NotSignedIn, UploadCancelled, UploadError, ascii_upload_name, upload_name

DEFAULT_HOSTS = ("tempsh", "gofile")
CANCELLED = "Cancelled"


def upload(
    path: str,
    hosts: Iterable[str],
    context: UploadContext,
    progress: Optional[Callable[[int, int], None]] = None,
    on_host: Optional[Callable[[str], None]] = None,
    history: Optional["UploadHistory"] = None,
    book_id: str = "",
    source: str = "",
    entries: int = 1,
    titles: Sequence[str] = (),
    epubs: bool = False,
) -> UploadResult:
    """Try each host in order; `on_host` gets each host's name just before it is tried.

    `book_id` and `source` name the upload when the title is not usable ASCII (see
    `ascii_upload_name`). `entries`, `titles` and `epubs` describe what a ZIP holds, for the result
    and the history.
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
                result = host.upload(payload, context, progress, name=ascii_upload_name(payload, book_id, source))
                result = replace(result, entries=entries, titles=list(titles), epubs=epubs)
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


@dataclass
class UploadOutcome:
    """What became of one upload: a lone file, or a ZIP of several."""

    name: str  # the file name, or the ZIP's name
    entries: int = 1
    titles: list = field(default_factory=list)
    epubs: bool = False
    result: Optional[UploadResult] = None
    error: str = ""

    @property
    def link(self) -> str:
        return self.result.link if self.result else ""

    @property
    def is_zip(self) -> bool:
        return self.entries > 1

    @property
    def label(self) -> str:
        return describe(self.name, self.entries, self.epubs)


def links_text(outcomes: Iterable[UploadOutcome]) -> str:
    """The text "Copy links" puts on the clipboard.

    Just the links, one per line, when every upload was a single file. When a ZIP is involved each
    link comes with what it holds (`NovelDB_3_EPUBs.zip (3 books)`), blocks separated by a blank line.
    """
    done = [outcome for outcome in outcomes if outcome.link]
    if not any(outcome.is_zip for outcome in done):
        return "\n".join(outcome.link for outcome in done)
    return "\n\n".join(f"{outcome.label}\n{outcome.link}" for outcome in done)


def host_limit(hosts: Iterable[str], context: UploadContext) -> Optional[int]:
    """The per-file maximum of the host an upload goes to first (the first one that is ready), if known."""
    for host_id in hosts or DEFAULT_HOSTS:
        host = uploader(host_id)
        if host is not None and host.is_ready(context):
            return host.max_bytes
    return None


def zip_limit(max_mb: float, hosts: Iterable[str], context: UploadContext) -> int:
    """The setting in bytes, or the first host's own maximum when that is smaller."""
    limit = int(max_mb * MEGABYTE)
    host = host_limit(hosts, context)
    return min(limit, host) if host else limit


def upload_items(
    items: Sequence[UploadItem],
    hosts: Iterable[str],
    context: UploadContext,
    *,
    bundle: bool = True,
    max_mb: float = UPLOAD_ZIP_MB_DEFAULT,
    history: Optional["UploadHistory"] = None,
    on_prepare: Optional[Callable[[int, int, str, int, int], None]] = None,
    on_host: Optional[Callable[[int, int, str], None]] = None,
    progress: Optional[Callable[[int, int, int, int], None]] = None,
    on_outcome: Optional[Callable[[UploadOutcome], None]] = None,
    temp_dir: Optional[str] = None,
) -> list[UploadOutcome]:
    """Upload `items`, packing two or more into ZIPs (see `plan_bundles`) when `bundle` is on.

    One upload (and link) per lone file or ZIP. Callbacks get the upload's index and count first:
    `on_prepare(index, count, zip name, bytes written, bytes in total)` while a ZIP is built,
    `on_host(index, count, host name)` when a host is tried and `progress(index, count, sent, total)`
    while it transfers. ZIPs are built one at a time in `temp_dir` and deleted as soon as their upload
    ends, whether or not it worked. A failed upload does not stop the others; cancelling does.
    """
    hosts = tuple(hosts) or DEFAULT_HOSTS
    plan = plan_bundles(items, zip_limit(max_mb, hosts, context), bundle)
    count = len(plan)
    outcomes: list[UploadOutcome] = []
    staging = tempfile.mkdtemp(prefix="noveldb-upload-", dir=temp_dir)
    try:
        for index, unit in enumerate(plan):
            if context.stopped():
                break
            outcome = _upload_one(unit, index, count, hosts, context, history, staging,
                                  on_prepare, on_host, progress)
            outcomes.append(outcome)
            if on_outcome:
                on_outcome(outcome)
            if outcome.error == CANCELLED:
                break
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return outcomes


def _upload_one(unit: Bundle, index: int, count: int, hosts, context, history, staging, on_prepare, on_host, progress):
    first = unit.items[0]
    outcome = UploadOutcome(unit.zip_name or upload_name(first.path), len(unit.items), unit.titles, unit.all_epubs)
    work = tempfile.mkdtemp(dir=staging)
    try:
        if unit.is_zip:
            def preparing(done, total):
                if on_prepare:
                    on_prepare(index, count, unit.zip_name, done, total)

            payload, book_id, source = build_zip(unit, work, preparing, context.stopped), "", ""
        else:
            payload, book_id, source = first.path, first.book_id, first.source
        outcome.result = upload(
            payload, hosts, context,
            (lambda sent, total: progress(index, count, sent, total)) if progress else None,
            (lambda host: on_host(index, count, host)) if on_host else None,
            history, book_id, source, outcome.entries, outcome.titles, outcome.epubs,
        )
        outcome.name = outcome.result.file_name
    except UploadCancelled:
        outcome.error = CANCELLED
    except Exception as exc:
        outcome.error = str(exc) or type(exc).__name__
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return outcome


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
        if not isinstance(data, list):
            return []
        known = {f.name for f in fields(UploadResult)}  # a newer app's extra keys must not lose the list
        return [UploadResult(**{k: v for k, v in item.items() if k in known}) for item in data if isinstance(item, dict)]

    def add(self, result: UploadResult) -> None:
        with self._lock:
            items = [result] + self.load()
            from noveldb.core.settings.store import write_raw

            write_raw([asdict(item) for item in items[: self._limit]], self._path)

    def clear(self) -> None:
        with self._lock:
            from noveldb.core.settings.store import write_raw

            write_raw([], self._path)
