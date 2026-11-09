"""Publisher OAuth, Drive transfer recovery, and the desktop settings entry point."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests

from noveldb.core.settings.desktop_config import export_config, import_config
from noveldb.core.settings.model import AppSettings
from noveldb.core.upload import google_drive as drive
from noveldb.core.upload.hosts import UploadContext, uploader
from noveldb.core.upload.transfer import NotSignedIn, Rejected, UploadCancelled


def reply(status=200, data=None, **headers):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data or {}).encode()
    response.headers.update(headers)
    return response


class Session:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def file(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"abcdefgh")
    return str(path)


def context(*replies):
    return UploadContext(session=Session(*replies), google_access_token=lambda force: "new" if force else "old")


def test_desktop_oauth_uses_browser_loopback_and_pkce(monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow

    monkeypatch.setattr(drive, "CLIENT_ID", "publisher.apps.googleusercontent.com")
    monkeypatch.setattr(drive, "CLIENT_SECRET", "desktop-public-client")
    seen = {}

    def from_config(config, **kwargs):
        seen.update(config=config, flow=kwargs)

        def run(**options):
            seen["browser"] = options
            return SimpleNamespace(refresh_token="user-refresh")

        return SimpleNamespace(run_local_server=run)

    monkeypatch.setattr(InstalledAppFlow, "from_client_config", from_config)
    auth = drive.GoogleDriveAuth(lambda: "")
    assert auth.sign_in() == "user-refresh"
    assert seen["flow"] == {"scopes": [drive.SCOPE], "autogenerate_code_verifier": True}
    assert seen["browser"]["host"] == "127.0.0.1" and seen["browser"]["port"] == 0
    assert seen["browser"]["access_type"] == "offline"
    assert seen["config"]["installed"]["client_id"] == drive.CLIENT_ID


def test_access_token_refresh_cache_force_and_disconnect(monkeypatch):
    from google.oauth2.credentials import Credentials

    monkeypatch.setattr(drive, "CLIENT_ID", "publisher.apps.googleusercontent.com")
    calls = []

    def refresh(self, request):
        from datetime import datetime, timedelta, timezone

        calls.append(self.refresh_token)
        self.token = f"access-{len(calls)}"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    saved = ["user-refresh"]
    auth = drive.GoogleDriveAuth(lambda: saved[0])
    assert auth.access_token() == auth.access_token() == "access-1"
    assert auth.access_token(True) == "access-2"
    saved[0] = ""
    with pytest.raises(NotSignedIn):
        auth.access_token()
    assert calls == ["user-refresh", "user-refresh"]


def test_drive_settings_round_trip_and_export_excludes_refresh_token():
    settings = AppSettings()
    settings = replace(settings, upload=replace(settings.upload, hosts=("gdrive",), google_drive_folder_id="folder"),
                       secrets=replace(settings.secrets, google_drive_refresh_token="user-refresh"))
    raw = export_config(settings)
    assert import_config(raw).upload == settings.upload
    assert import_config(raw).secrets.google_drive_refresh_token == "user-refresh"
    assert "upload_gdrive_refresh_token" not in export_config(settings, raw, include_secrets=False)
    assert import_config({}, settings).upload.google_drive_folder_id == "folder"
    assert import_config({}, settings).secrets.google_drive_refresh_token == "user-refresh"
    cleared = import_config({"upload_gdrive_folder_id": "", "upload_gdrive_refresh_token": ""}, settings)
    assert cleared.upload.google_drive_folder_id == cleared.secrets.google_drive_refresh_token == ""


def test_drive_upload_resumes_chunks_and_shares_the_completed_file(file):
    ctx = context(reply(Location=drive.GoogleDriveUploader.endpoint + "?upload_id=session"),
                  reply(308, Range="bytes=0-3"), reply(data={"id": "file-1"}), reply())
    ctx.google_drive_folder_id = "parent"
    host = uploader("gdrive")
    host = type(host)()
    host.chunk_size = 4
    progress = []
    result = host.upload(file, ctx, lambda sent, total: progress.append((sent, total)))
    assert result.link == "https://drive.google.com/file/d/file-1/view?usp=sharing"
    assert ctx.session.calls[0][2]["json"] == {"name": "book.epub", "parents": ["parent"]}
    assert ctx.session.calls[1][2]["data"] == b"abcd"
    assert ctx.session.calls[2][2]["data"] == b"efgh"
    assert ctx.session.calls[2][2]["headers"]["Content-Range"] == "bytes 4-7/8"
    assert ctx.session.calls[3][2]["json"] == {"role": "reader", "type": "anyone"}
    assert progress == [(4, 8), (8, 8)]
    assert all(call[2]["allow_redirects"] is False for call in ctx.session.calls)


def test_interrupted_upload_queries_server_offset_before_resending(file):
    ctx = context(reply(Location=drive.GoogleDriveUploader.endpoint + "?upload_id=session"),
                  requests.ConnectionError(), reply(308, Range="bytes=0-3"), reply(data={"id": "file-1"}), reply())
    host = drive.GoogleDriveUploader()
    host.chunk_size = 4
    host.upload(file, ctx)
    assert ctx.session.calls[2][2]["headers"]["Content-Range"] == "bytes */8"
    assert ctx.session.calls[3][2]["data"] == b"efgh"


def test_expired_access_token_is_refreshed_on_start_chunks_and_permission(file):
    ctx = context(reply(401), reply(Location=drive.GoogleDriveUploader.endpoint + "?upload_id=session"),
                  reply(401), reply(data={"id": "file-1"}), reply(401), reply())
    drive.GoogleDriveUploader().upload(file, ctx)
    assert [call[2]["headers"]["Authorization"] for call in ctx.session.calls] == ["Bearer old", "Bearer new"] * 3


def test_drive_rejects_redirects_and_never_sends_a_token_to_another_host(file):
    ctx = context(reply(Location="https://example.com/steal-token"))
    with pytest.raises(Rejected, match="invalid upload session"):
        drive.GoogleDriveUploader().upload(file, ctx)
    assert len(ctx.session.calls) == 1


def test_stalled_resume_has_a_bounded_retry_count(file):
    ctx = context(reply(Location=drive.GoogleDriveUploader.endpoint + "?upload_id=session"),
                  *[reply(308) for _ in range(6)])
    with pytest.raises(Rejected, match="too many interrupted chunks"):
        drive.GoogleDriveUploader().upload(file, ctx)


def test_upload_cancellation_stops_before_network(file):
    ctx = context()
    ctx.stopped = lambda: True
    with pytest.raises(UploadCancelled):
        drive.GoogleDriveUploader().upload(file, ctx)
    assert ctx.session.calls == []


def test_empty_file_completes(file):
    from pathlib import Path

    Path(file).write_bytes(b"")
    ctx = context(reply(Location=drive.GoogleDriveUploader.endpoint + "?upload_id=session"),
                  reply(data={"id": "empty"}), reply())
    drive.GoogleDriveUploader().upload(file, ctx)
    assert ctx.session.calls[1][2]["headers"]["Content-Range"] == "bytes */0"


def test_desktop_sign_in_button_saves_grant_and_uploads_use_it(tmp_path, monkeypatch, qtbot):
    from PySide6.QtWidgets import QPushButton
    from noveldb.qt.context import AppContext
    from noveldb.qt.pages.settings import upload_form

    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path))
    monkeypatch.setattr(drive, "CLIENT_ID", "publisher.apps.googleusercontent.com")
    ctx = AppContext(start_workers=False)
    monkeypatch.setattr(ctx.google_drive, "sign_in", lambda: "user-refresh")
    form = upload_form(ctx)
    qtbot.addWidget(form)
    button = next(b for b in form.findChildren(QPushButton) if b.text() == "Sign in to Google")
    button.click()
    qtbot.waitUntil(lambda: not ctx.google_drive_busy)
    assert ctx.store.load().secrets.google_drive_refresh_token == "user-refresh"
    assert ctx.upload_context().google_access_token is not None
    disconnect = next(b for b in form.findChildren(QPushButton) if b.text() == "Disconnect")
    disconnect.click()
    assert not ctx.google_drive.signed_in
    assert ctx.upload_context().google_access_token is None
    assert not ctx.store.load().secrets.google_drive_refresh_token
    ctx.shutdown()
