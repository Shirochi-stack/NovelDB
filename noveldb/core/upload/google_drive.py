"""Desktop Drive authorization through the system browser and resumable uploads."""

from __future__ import annotations

import mimetypes
import os
import re
import threading
from urllib.parse import quote, urlsplit

import requests

from noveldb.core.upload.transfer import NotSignedIn, Rejected, UploadCancelled, json_of, require_success

# CI supplies the publisher's installed-app client configuration before packaging.
try:
    from noveldb.core.upload.google_drive_client import CLIENT_ID, CLIENT_SECRET
except ImportError:
    CLIENT_ID = os.environ.get("NOVELDB_GOOGLE_DRIVE_CLIENT_ID", "")
    CLIENT_SECRET = os.environ.get("NOVELDB_GOOGLE_DRIVE_CLIENT_SECRET", "")
SCOPE = "https://www.googleapis.com/auth/drive.file"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleDriveAuth:
    def __init__(self, refresh_token):
        self._refresh_token = refresh_token
        self._credentials = None
        self._lock = threading.RLock()

    @property
    def configured(self):
        return bool(CLIENT_ID)

    @property
    def signed_in(self):
        return self.configured and bool(self._refresh_token())

    def sign_in(self):
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.configured:
            raise Rejected("Google Drive", "the publisher's OAuth registration is incomplete")
        flow = InstalledAppFlow.from_client_config({"installed": {
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": TOKEN_URI,
        }}, scopes=[SCOPE], autogenerate_code_verifier=True)
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, timeout_seconds=180,
            prompt="consent", access_type="offline",
            authorization_prompt_message="",
            success_message="NovelDB is connected to Google Drive. You can close this window.",
        )
        if not credentials.refresh_token:
            raise Rejected("Google Drive", "Google did not return a refresh token")
        with self._lock:
            self._credentials = credentials
        return credentials.refresh_token

    def access_token(self, force=False):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        with self._lock:
            refresh = self._refresh_token()
            if not self.configured or not refresh:
                self._credentials = None
                raise NotSignedIn("Google Drive")
            if self._credentials is None or self._credentials.refresh_token != refresh:
                self._credentials = Credentials(
                    None, refresh_token=refresh, token_uri=TOKEN_URI,
                    client_id=CLIENT_ID, client_secret=CLIENT_SECRET, scopes=[SCOPE],
                )
            if force or not self._credentials.valid:
                try:
                    self._credentials.refresh(Request())
                except RefreshError:
                    self._credentials = None
                    raise NotSignedIn("Google Drive") from None
            if self._refresh_token() != refresh:
                self._credentials = None
                raise NotSignedIn("Google Drive")
            return self._credentials.token


class GoogleDriveUploader:
    id, display_name = "gdrive", "Google Drive"
    max_bytes, retention_seconds, requires_account = 5_000_000_000_000, None, True
    chunk_size = 8 * 1024 * 1024
    endpoint = "https://www.googleapis.com/upload/drive/v3/files"
    files_endpoint = "https://www.googleapis.com/drive/v3/files"

    def is_ready(self, context):
        return context.google_access_token is not None

    def check(self, path):
        from noveldb.core.upload.transfer import TooLarge

        if os.path.getsize(path) > self.max_bytes:
            raise TooLarge(self.display_name, self.max_bytes)

    def _request(self, context, method, url, **kwargs):
        if context.stopped():
            raise UploadCancelled("Upload cancelled")
        auth = context.google_access_token
        if auth is None:
            raise NotSignedIn(self.display_name)
        headers = kwargs.pop("headers", {})
        for force in (False, True):
            reply = context.http().request(
                method, url, headers={**headers, "Authorization": f"Bearer {auth(force)}"},
                timeout=context.http_timeout(), allow_redirects=False, **kwargs,
            )
            if reply.status_code != 401:
                return reply
        raise NotSignedIn(self.display_name)

    def upload(self, path, context, progress=None, name=None):
        from noveldb.core.upload.hosts import UploadResult

        size = os.path.getsize(path)
        metadata = {"name": name or os.path.basename(path)}
        if context.google_drive_folder_id:
            metadata["parents"] = [context.google_drive_folder_id]
        started = self._request(context, "POST", self.endpoint, params={
            "uploadType": "resumable", "supportsAllDrives": "true", "fields": "id,webViewLink",
        }, json=metadata, headers={
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        })
        require_success(started, self.display_name)
        session = started.headers.get("Location", "")
        parts = urlsplit(session)
        if parts.scheme != "https" or parts.hostname != "www.googleapis.com":
            raise Rejected(self.display_name, "invalid upload session")
        result = self._chunks(path, size, session, context, progress)
        file_id = result.get("id")
        if not file_id:
            raise Rejected(self.display_name, "no file id")
        permission = self._request(
            context, "POST", f"{self.files_endpoint}/{quote(file_id, safe='')}/permissions",
            params={"supportsAllDrives": "true"}, json={"role": "reader", "type": "anyone"},
        )
        require_success(permission, self.display_name)
        link = result.get("webViewLink") or f"https://drive.google.com/file/d/{quote(file_id, safe='')}/view?usp=sharing"
        return UploadResult(self.id, os.path.basename(path), link)

    def _chunks(self, path, size, session, context, progress):
        offset, failures, query_status = 0, 0, False
        with open(path, "rb") as source:
            while True:
                source.seek(offset)
                data = b"" if query_status else source.read(self.chunk_size)
                content_range = f"bytes */{size}" if query_status or size == 0 else f"bytes {offset}-{offset + len(data) - 1}/{size}"
                try:
                    reply = self._request(context, "PUT", session, data=data, headers={
                        "Content-Type": "application/octet-stream", "Content-Range": content_range,
                    })
                except (requests.ConnectionError, requests.Timeout):
                    reply = None
                if reply is not None and reply.status_code in (200, 201):
                    if progress:
                        progress(size, size)
                    return json_of(reply) or {}
                if reply is not None and reply.status_code == 308:
                    match = re.fullmatch(r"bytes=0-(\d+)", reply.headers.get("Range", ""))
                    next_offset = int(match.group(1)) + 1 if match else 0
                    if not 0 <= next_offset <= size:
                        raise Rejected(self.display_name, "invalid resume offset")
                    failures = 0 if next_offset > offset else failures + 1
                    offset, query_status = next_offset, next_offset == size
                    if progress:
                        progress(offset, size)
                elif reply is None or reply.status_code >= 500:
                    failures += 1
                    query_status = True
                else:
                    require_success(reply, self.display_name)
                    raise Rejected(self.display_name, "unexpected upload response")
                if failures > 5:
                    raise Rejected(self.display_name, "too many interrupted chunks")
