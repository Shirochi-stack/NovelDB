"""Desktop reader images served to WebEngine as binary files."""

import base64
import hashlib
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler


SCHEME = b"noveldb-reader"
_ADDRESS = re.compile(r"([0-9a-f]{32})/([0-9a-f]{64})\.([a-z0-9][a-z0-9.+-]{0,31})")
_INLINE = re.compile(r"""\b(?:src|data-src|data-original)\s*=\s*(["'])(data:image/[a-z0-9.+-]+;base64,)""", re.I)
_SUBTYPE = re.compile(r"[a-z0-9][a-z0-9.+-]{0,31}")


def register_scheme() -> None:
    if not QWebEngineUrlScheme.schemeByName(SCHEME).name():
        scheme = QWebEngineUrlScheme(SCHEME)
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
        scheme.setFlags(QWebEngineUrlScheme.Flag.LocalScheme)
        QWebEngineUrlScheme.registerScheme(scheme)


class ReaderSession:
    def __init__(self, visible: bool = True):
        self.id = uuid.uuid4().hex
        self._visible = visible
        self._stopped = False
        self._state = threading.Condition()
        self._hidden_at = None if visible else time.monotonic()
        self._hidden_time = 0.0
        self._files_lock = threading.Lock()
        self._directory = None

    def set_visible(self, visible: bool) -> None:
        with self._state:
            if visible == self._visible:
                return
            now = time.monotonic()
            if visible:
                self._hidden_time += now - self._hidden_at
                self._hidden_at = None
            else:
                self._hidden_at = now
            self._visible = visible
            self._state.notify_all()

    def clock(self) -> float:
        with self._state:
            return (self._hidden_at or time.monotonic()) - self._hidden_time

    def result(self, future):
        deadline = self.clock() + 240
        while self.wait():
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("the reader browser did not answer")
            try:
                return future.result(timeout=min(1, remaining))
            except TimeoutError:
                if future.done():
                    return future.result()
        future.cancel()
        raise RuntimeError("the reader was closed")

    def wait(self) -> bool:
        """Wait for the tab to return, or let a worker abandon a closed reader."""
        with self._state:
            self._state.wait_for(lambda: self._visible or self._stopped)
            return not self._stopped

    def stop(self) -> None:
        with self._state:
            self._stopped = True
            self._state.notify_all()

    def cleanup(self) -> None:
        with self._files_lock:
            if self._directory is not None:
                self._directory.cleanup()
                self._directory = None

    def image(self, data: bytes, mime: str) -> str:
        subtype = mime.lower().removeprefix("image/")
        if not _SUBTYPE.fullmatch(subtype):
            subtype = "jpeg"
        name = hashlib.sha256(data).hexdigest() + "." + subtype
        with self._files_lock:
            if self._stopped:
                return ""
            if self._directory is None:
                self._directory = tempfile.TemporaryDirectory(prefix="noveldb-reader-", ignore_cleanup_errors=True)
            file = Path(self._directory.name) / name
            if not file.is_file():
                try:
                    file.write_bytes(data)
                except OSError:
                    file.unlink(missing_ok=True)
                    raise
        return f"{SCHEME.decode()}:{self.id}/{name}"

    def inline_image(self, address: str) -> str:
        if not self.wait():
            return ""
        header, _, content = address.partition(",")
        try:
            return self.image(base64.b64decode(content), header[5:].split(";", 1)[0])
        except ValueError:
            return ""

    def chapter_html(self, html: str) -> str:
        parts = []
        cursor = 0
        while match := _INLINE.search(html, cursor):
            end = html.find(match[1], match.end())
            if end < 0:
                break
            parts.append(html[cursor:match.start(2)])
            parts.append(self.inline_image(html[match.start(2):end]))
            cursor = end
        if not parts:
            return html
        parts.append(html[cursor:])
        return "".join(parts)

    def file_path(self, name: str):
        directory = self._directory
        return Path(directory.name) / name if directory is not None else None


class ReaderMedia(QWebEngineUrlSchemeHandler):
    def __init__(self, session, parent):
        super().__init__(parent)
        self.session = session

    def requestStarted(self, request):
        match = _ADDRESS.fullmatch(request.requestUrl().path())
        session = self.session()
        if request.requestMethod() != b"GET" or not match or match[1] != session.id:
            request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        path = session.file_path(f"{match[2]}.{match[3]}")
        if path is None:
            request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        file = QFile(str(path), request)
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            request.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        request.reply(f"image/{match[3]}".encode("ascii"), file)
