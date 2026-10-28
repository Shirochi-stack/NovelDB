"""HTTP plumbing for uploads: streamed multipart bodies with byte progress, and reply helpers."""

from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from urllib.parse import quote
from typing import Callable, Iterable, Optional

Progress = Callable[[int, int], None]  # (sent bytes, total bytes)

_CHUNK = 256 * 1024


class UploadError(Exception):
    """An upload failed; the message is shown to the user."""


class UploadCancelled(UploadError):
    pass


class TooLarge(UploadError):
    def __init__(self, host: str, limit: int) -> None:
        super().__init__(f"{host} accepts files up to {limit:,} bytes.")


class NotSignedIn(UploadError):
    def __init__(self, host: str) -> None:
        super().__init__(f"Sign in to {host} first.")


class Rejected(UploadError):
    def __init__(self, host: str, detail: str) -> None:
        super().__init__(f"{host} rejected the upload: {detail}")


def upload_name(path: str) -> str:
    """The display name of a file (NFC, so macOS-decomposed Hangul compares correctly)."""
    return unicodedata.normalize("NFC", os.path.basename(path))


def book_id_from_url(url: str) -> str:
    """An ASCII id for a book URL or input: the last number in the path (else in the query), else a short slug."""
    from urllib.parse import urlsplit

    value = (url or "").strip()
    if value.isdigit():
        return value
    parts = urlsplit(value) if "://" in value else None
    path = parts.path if parts else value
    numbers = re.findall(r"\d+", path)
    if numbers:
        return numbers[-1]
    numbers = re.findall(r"\d+", parts.query) if parts else []
    if numbers:
        return numbers[-1]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")[-40:]
    return slug or "book"


# A title must keep this many ASCII letters to name an upload. Shorter remains ("-_", "VR_") and
# bare numbers ("100" from "100일 후에 ...") are useless as links, so those uploads use
# "<source>_<book id>" instead.
MIN_NAME_LETTERS = 3
_MAX_STEM = 100


def _letter_count(text: str) -> int:
    return sum(1 for ch in text if ch.isascii() and ch.isalpha())


def _extension(name: str) -> str:
    ext = os.path.splitext(name)[1]
    return ext if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext) else ""


def ascii_safe_stem(stem: str) -> str:
    """A title with every run of non-ASCII characters replaced by "_" and the ends trimmed."""
    text = re.sub(r"[^ -~]+", "_", stem)  # anything but printable ASCII (space to tilde)
    text = re.sub(r"[ _]*_[ _]*", "_", text)
    return text.strip(" ._-")[:_MAX_STEM].rstrip(" ._-")


def ascii_upload_name(path: str, fallback_id: str = "", source: str = "") -> str:
    """The name sent to hosts: an ASCII-safe form of the original, else "<source>_<book id>.<ext>".

    Several hosts drop non-ASCII characters from the multipart file name (a Korean title became
    "epub.epub"), so the title is cleaned up here instead: a plain ASCII name stays as it is, other
    scripts become "_", and a title that keeps fewer than three ASCII letters (digits alone do not
    count) is replaced by the source codename and book id (`npia_123456.epub`).
    """
    name = upload_name(path)
    root = os.path.splitext(name)[0]
    ext = _extension(name)
    title = root if ext else name  # the part that must carry the letters and digits
    if name.isascii() and name.isprintable() and _letter_count(title) >= MIN_NAME_LETTERS:
        return name
    stem = ascii_safe_stem(title)
    if _letter_count(stem) >= MIN_NAME_LETTERS:
        return f"{stem}{ext}"
    ident = re.sub(r"[^A-Za-z0-9._-]+", "-", fallback_id or "").strip("-.") or "book"
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", source or "").strip("-")
    return f"{prefix}_{ident}{ext}" if prefix else f"{ident}{ext}"


class StreamBody:
    """A file-like request body made of byte strings and file ranges.

    `requests` sends it with a Content-Length (it has `__len__`), reading chunk by chunk, so the
    progress callback sees real byte counts and `stopped()` can abort mid-transfer.
    """

    def __init__(self, parts: Iterable, progress: Optional[Progress] = None,
                 stopped: Callable[[], bool] = lambda: False) -> None:
        self._parts = list(parts)
        self._total = sum(len(p) if isinstance(p, (bytes, bytearray)) else os.path.getsize(p) for p in self._parts)
        self._progress = progress
        self._stopped = stopped
        self._sent = 0
        self._index = 0
        self._handle = None
        self._buffer = b""

    def __len__(self) -> int:
        return self._total

    def _next_bytes(self, size: int) -> bytes:
        while self._index < len(self._parts):
            part = self._parts[self._index]
            if isinstance(part, (bytes, bytearray)):
                self._index += 1
                return bytes(part)
            if self._handle is None:
                self._handle = open(part, "rb")
            data = self._handle.read(size)
            if data:
                return data
            self._handle.close()
            self._handle = None
            self._index += 1
        return b""

    def read(self, size: int = -1) -> bytes:
        if self._stopped():
            self.close()
            raise UploadCancelled("Upload cancelled")
        size = _CHUNK if size is None or size < 0 else size
        while len(self._buffer) < size:
            data = self._next_bytes(size)
            if not data:
                break
            self._buffer += data
        out, self._buffer = self._buffer[:size], self._buffer[size:]
        self._sent += len(out)
        if self._progress and out:
            self._progress(self._sent, self._total)
        return out

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def multipart(field: str, path: str, fields: Iterable[tuple[str, str]] = (),
              progress: Optional[Progress] = None, stopped: Callable[[], bool] = lambda: False,
              name: Optional[str] = None):
    """(body, content type) for a multipart/form-data upload of one file plus text fields.

    `filename` carries the ASCII upload name and `filename*` (RFC 5987) the original UTF-8 name,
    which hosts that support it keep.
    """
    boundary = f"----NovelDB{uuid.uuid4().hex}"

    def quoted(value: str) -> str:
        return value.replace("\r", "").replace("\n", "").replace('"', "%22")

    parts: list = []
    for field_name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted(field_name)}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    ascii_name = name or ascii_upload_name(path)
    original = upload_name(path)
    extended = f"; filename*=UTF-8''{quote(original, safe='')}" if original != ascii_name else ""
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted(field)}"; '
         f'filename="{quoted(ascii_name)}"{extended}\r\nContent-Type: application/octet-stream\r\n\r\n').encode("utf-8")
    )
    parts.append(path)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return StreamBody(parts, progress, stopped), f"multipart/form-data; boundary={boundary}"


def snippet(response) -> str:
    text = (response.text or "").strip().replace("\n", " ")
    return text[:200] if text else f"HTTP {response.status_code}"


def require_success(response, host: str) -> None:
    if not 200 <= response.status_code < 300:
        raise UploadError(f"{host} returned HTTP {response.status_code}: {snippet(response)}")


def json_of(response):
    try:
        return json.loads(response.text)
    except (ValueError, TypeError):
        return None


def plain_link(response, host: str) -> str:
    """The first line of a plain-text reply when it is an http(s) URL."""
    require_success(response, host)
    line = (response.text or "").strip().splitlines()[0].strip() if (response.text or "").strip() else ""
    if not line.startswith("http"):
        raise Rejected(host, snippet(response))
    return line


def https(url: str) -> str:
    return "https://" + url[len("http://"):] if url.startswith("http://") else url
