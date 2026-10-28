"""Share-link hosts, ported from the mobile apps' `Hosts.kt` (which ported bot.py's helpers).

Limits and retention match the mobile catalog (checked October 2026). Each uploader takes a
file path, the upload context and a progress callback, and returns an `UploadResult`.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote, urljoin, urlsplit

import requests

from noveldb.core.upload.transfer import (
    NotSignedIn,
    Rejected,
    StreamBody,
    TooLarge,
    ascii_upload_name,
    https,
    json_of,
    multipart,
    plain_link,
    require_success,
    snippet,
    upload_name,
)

DAY = 86_400


@dataclass
class UploadResult:
    host_id: str
    file_name: str
    link: str
    delete_link: Optional[str] = None
    expires_at: Optional[float] = None  # epoch seconds
    uploaded_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()


@dataclass
class UploadContext:
    """Credentials and transport for uploads. `stopped()` cancels a transfer in progress."""

    pixeldrain_key: str = ""
    gofile_token: str = ""
    catbox_userhash: str = ""
    session: Optional[requests.Session] = None
    stopped: Callable[[], bool] = lambda: False
    timeout: tuple = (30, 300)

    def http(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session()
        return self.session


class Uploader:
    id = ""
    display_name = ""
    max_bytes: Optional[int] = None
    retention_seconds: Optional[int] = None
    requires_account = False

    def is_ready(self, context: UploadContext) -> bool:
        return True

    def check(self, path: str) -> None:
        if self.max_bytes is not None and os.path.getsize(path) > self.max_bytes:
            raise TooLarge(self.display_name, self.max_bytes)

    def upload(self, path: str, context: UploadContext, progress=None, name: Optional[str] = None) -> UploadResult:
        raise NotImplementedError

    def result(self, path: str, link: str, delete_link: Optional[str] = None) -> UploadResult:
        now = time.time()
        return UploadResult(
            host_id=self.id, file_name=upload_name(path), link=link, delete_link=delete_link,
            expires_at=(now + self.retention_seconds) if self.retention_seconds else None, uploaded_at=now,
        )

    def _post(self, url: str, field_name: str, path: str, context: UploadContext, progress=None,
              fields=(), headers: Optional[dict] = None, name: Optional[str] = None):
        body, content_type = multipart(field_name, path, fields, progress, context.stopped, name)
        try:
            return context.http().post(url, data=body, headers={"Content-Type": content_type, **(headers or {})},
                                       timeout=context.timeout)
        finally:
            body.close()


class TempShUploader(Uploader):
    """`curl -F file=@x https://temp.sh/upload`; 4 GB, files expire after 3 days."""

    id, display_name = "tempsh", "temp.sh"
    max_bytes, retention_seconds = 4_000_000_000, 3 * DAY
    ENDPOINT = "https://temp.sh/upload"

    def upload(self, path, context, progress=None, name=None):
        return self.result(path, plain_link(self._post(self.ENDPOINT, "file", path, context, progress, name=name), "temp.sh"))


class GofileUploader(Uploader):
    """Gofile's global upload endpoint; guest uploads need no token (removed after ~10 idle days)."""

    id, display_name = "gofile", "Gofile"
    retention_seconds = 10 * DAY
    ENDPOINT = "https://upload.gofile.io/uploadfile"

    def upload(self, path, context, progress=None, name=None):
        headers = {"Authorization": f"Bearer {context.gofile_token}"} if context.gofile_token else {}
        reply = self._post(self.ENDPOINT, "file", path, context, progress, headers=headers, name=name)
        data = json_of(reply)
        if not isinstance(data, dict):
            require_success(reply, "Gofile")
            raise Rejected("Gofile", snippet(reply))
        page = (data.get("data") or {}).get("downloadPage") if isinstance(data.get("data"), dict) else None
        if data.get("status") != "ok" or not page:
            raise Rejected("Gofile", str(data.get("status") or snippet(reply)))
        return self.result(path, page)


class CatboxUploader(Uploader):
    """catbox.moe user API; 200 MB per file, kept permanently."""

    id, display_name = "catbox", "Catbox"
    max_bytes = 200_000_000
    ENDPOINT = "https://catbox.moe/user/api.php"

    def upload(self, path, context, progress=None, name=None):
        fields = [("reqtype", "fileupload")]
        if context.catbox_userhash:
            fields.append(("userhash", context.catbox_userhash))
        reply = self._post(self.ENDPOINT, "fileToUpload", path, context, progress, fields=fields, name=name)
        return self.result(path, plain_link(reply, "Catbox"))


class PixeldrainUploader(Uploader):
    """Pixeldrain needs an API key (Basic auth password); free files go 90 days after the last view."""

    id, display_name = "pixeldrain", "Pixeldrain"
    retention_seconds, requires_account = 90 * DAY, True
    BASE = "https://pixeldrain.com/api/file/"

    def is_ready(self, context):
        return bool(context.pixeldrain_key)

    def upload(self, path, context, progress=None, name=None):
        if not context.pixeldrain_key:
            raise NotSignedIn(self.display_name)
        url = self.BASE + quote(name or ascii_upload_name(path), safe="!$&'()*+,=@")
        body = StreamBody([path], progress, context.stopped)
        try:
            reply = context.http().put(url, data=body, auth=("", context.pixeldrain_key),
                                       headers={"Content-Type": "application/octet-stream"}, timeout=context.timeout)
        finally:
            body.close()
        data = json_of(reply)
        file_id = data.get("id") if isinstance(data, dict) else None
        if not (200 <= reply.status_code < 300) or not file_id:
            if reply.status_code == 401:
                raise NotSignedIn("Pixeldrain")
            message = (data or {}).get("message") or (data or {}).get("value") if isinstance(data, dict) else None
            raise Rejected("Pixeldrain", message or snippet(reply))
        return self.result(path, f"https://pixeldrain.com/u/{file_id}")


class TmpfilesUploader(Uploader):
    """tmpfiles.org API; 100 MB, kept up to 48 hours."""

    id, display_name = "tmpfiles", "tmpfiles"
    max_bytes, retention_seconds = 100_000_000, 2 * DAY
    ENDPOINT = "https://tmpfiles.org/api/v1/upload"

    def upload(self, path, context, progress=None, name=None):
        reply = self._post(self.ENDPOINT, "file", path, context, progress, fields=[("expire", str(2 * DAY))], name=name)
        require_success(reply, "tmpfiles")
        data = json_of(reply)
        url = ((data or {}).get("data") or {}).get("url") if isinstance(data, dict) else None
        return self.result(path, https(url) if url else https(plain_link(reply, "tmpfiles")))


class KrakenFilesUploader(Uploader):
    """KrakenFiles anonymous uploads: ask for a server, then post `files[]`. 1 GB, 30 idle days."""

    id, display_name = "krakenfiles", "KrakenFiles"
    max_bytes, retention_seconds = 1_000_000_000, 30 * DAY
    SERVER_LOOKUP = "https://krakenfiles.com/api/server/available"
    SITE = "https://krakenfiles.com"

    def upload(self, path, context, progress=None, name=None):
        lookup = context.http().get(self.SERVER_LOOKUP, headers={"Accept": "application/json"}, timeout=context.timeout)
        require_success(lookup, "KrakenFiles")
        server = ((json_of(lookup) or {}).get("data") or {}).get("url")
        if not server:
            raise Rejected("KrakenFiles", f"no upload server: {snippet(lookup)}")
        parts = urlsplit(server)
        endpoint = f"{parts.scheme}://{parts.netloc}/_uploader/gallery/upload"
        reply = self._post(endpoint, "files[]", path, context, progress, name=name)
        require_success(reply, "KrakenFiles")
        files = (json_of(reply) or {}).get("files") or []
        entry = files[0] if files and isinstance(files[0], dict) else None
        if entry is None:
            raise Rejected("KrakenFiles", snippet(reply))
        if entry.get("error"):
            raise Rejected("KrakenFiles", str(entry["error"]))
        if not entry.get("url"):
            raise Rejected("KrakenFiles", snippet(reply))
        return self.result(path, urljoin(self.SITE, entry["url"]))


class SendNowUploader(Uploader):
    """send.now (formerly send.cm): the home page names the upload server. 100 GB, 15 idle days."""

    id, display_name = "sendnow", "Send.now"
    max_bytes, retention_seconds = 100_000_000_000, 15 * DAY
    HOME = "https://send.now/"
    _SERVERS = (
        re.compile(r"https://([A-Za-z0-9-]+)\.send\.now/cgi-bin/upload\.cgi"),
        re.compile(r"https://([A-Za-z0-9]{4,})\.send\.now"),
    )

    def upload(self, path, context, progress=None, name=None):
        page = context.http().get(self.HOME, headers={"User-Agent": "Mozilla/5.0"}, timeout=context.timeout)
        require_success(page, "Send.now")
        server = None
        for pattern in self._SERVERS:
            match = pattern.search(page.text or "")
            if match:
                server = match.group(1)
                break
        if not server:
            raise Rejected("Send.now", "upload server not found on the home page")
        endpoint = f"https://{server}.send.now/cgi-bin/upload.cgi?upload_type=file&utype=anon"
        reply = self._post(endpoint, "file_0", path, context, progress, name=name)
        require_success(reply, "Send.now")
        data = json_of(reply)
        entry = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
        if entry is None:
            raise Rejected("Send.now", snippet(reply))
        code, status = entry.get("file_code"), entry.get("file_status")
        if not code or code == "undef" or (status or "OK").upper() != "OK":
            raise Rejected("Send.now", str(status or snippet(reply)))
        return self.result(path, f"https://send.now/{code}")


ALL = (
    TempShUploader(), GofileUploader(), CatboxUploader(), PixeldrainUploader(),
    TmpfilesUploader(), KrakenFilesUploader(), SendNowUploader(),
)
ALIASES = {"sendcm": "sendnow"}


def uploader(host_id: str) -> Optional[Uploader]:
    key = ALIASES.get(host_id, host_id)
    return next((u for u in ALL if u.id == key), None)
