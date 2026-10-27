"""Cloud upload: names, multipart bodies with progress, host replies, fallback and history."""

import json
import os
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from noveldb.core.settings import desktop_config as dc
from noveldb.core.settings.model import AppSettings
from noveldb.core.upload import catalog, hosts
from noveldb.core.upload.hosts import UploadContext
from noveldb.core.upload.transfer import (
    StreamBody,
    UploadCancelled,
    ascii_upload_name,
    book_id_from_url,
    multipart,
)

KOREAN = "재앙급 진조는 방구석에서 꿀 빱니다.epub"


def test_ascii_names_stay_and_other_scripts_use_the_book_id(tmp_path):
    plain = tmp_path / "My Book.epub"
    plain.write_bytes(b"x")
    korean = tmp_path / KOREAN
    korean.write_bytes(b"x")
    assert ascii_upload_name(str(plain), "419368") == "My Book.epub"
    assert ascii_upload_name(str(korean), "419368") == "419368.epub"
    assert ascii_upload_name(str(korean), "") == "book.epub"


def test_book_id_from_url():
    assert book_id_from_url("419368") == "419368"
    assert book_id_from_url("https://example.com/novel/419368") == "419368"
    assert book_id_from_url("https://example.com/book/abc-def") == "book-abc-def"


def test_multipart_sends_ascii_filename_and_utf8_filename_star(tmp_path):
    path = tmp_path / KOREAN
    path.write_bytes(b"EPUBDATA")
    seen = []
    body, content_type = multipart("file", str(path), [("expire", "60")], progress=lambda s, t: seen.append((s, t)),
                                   name="419368.epub")
    data = b""
    while True:
        chunk = body.read(7)
        if not chunk:
            break
        data += chunk
    text = data.decode("utf-8")
    assert len(data) == len(body)
    assert 'filename="419368.epub"' in text
    star = text.split("filename*=UTF-8''")[1].split("\r\n")[0]
    assert unquote(star) == KOREAN
    assert 'name="expire"\r\n\r\n60' in text and "EPUBDATA" in text
    assert content_type.startswith("multipart/form-data; boundary=")
    assert seen[-1] == (len(body), len(body))


def test_stream_body_can_be_cancelled(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 10)
    body = StreamBody([str(path)], stopped=lambda: True)
    with pytest.raises(UploadCancelled):
        body.read(4)


class FakeResponse:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = json.dumps(payload) if payload is not None else text


class FakeSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def _next(self, method, url, **kwargs):
        body = kwargs.get("data")
        if body is not None and hasattr(body, "read"):
            while body.read(65536):
                pass
        self.calls.append((method, url, kwargs.get("headers") or {}))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._next("PUT", url, **kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)


@pytest.fixture
def book(tmp_path):
    path = tmp_path / KOREAN
    path.write_bytes(b"data")
    return str(path)


def test_tempsh_returns_the_plain_link(book):
    session = FakeSession([FakeResponse(text="https://temp.sh/abc/419368.epub\n")])
    result = hosts.TempShUploader().upload(book, UploadContext(session=session), name="419368.epub")
    assert result.link == "https://temp.sh/abc/419368.epub"
    assert result.file_name == KOREAN
    assert result.expires_at is not None


def test_gofile_parses_the_download_page_and_sends_the_token(book):
    session = FakeSession([FakeResponse(payload={"status": "ok", "data": {"downloadPage": "https://gofile.io/d/x"}})])
    result = hosts.GofileUploader().upload(book, UploadContext(session=session, gofile_token="t"))
    assert result.link == "https://gofile.io/d/x"
    assert session.calls[0][2]["Authorization"] == "Bearer t"


def test_pixeldrain_needs_a_key_and_uses_the_ascii_name(book):
    assert not hosts.PixeldrainUploader().is_ready(UploadContext())
    session = FakeSession([FakeResponse(status=201, payload={"id": "abc"})])
    result = hosts.PixeldrainUploader().upload(book, UploadContext(session=session, pixeldrain_key="k"), name="419368.epub")
    assert result.link == "https://pixeldrain.com/u/abc"
    assert session.calls[0][1].endswith("/419368.epub")


def test_sendnow_finds_the_server_and_file_code(book):
    session = FakeSession([
        FakeResponse(text='<form action="https://abc123.send.now/cgi-bin/upload.cgi">'),
        FakeResponse(payload=[{"file_code": "xyz", "file_status": "OK"}]),
    ])
    assert hosts.SendNowUploader().upload(book, UploadContext(session=session)).link == "https://send.now/xyz"
    assert session.calls[1][1].startswith("https://abc123.send.now/cgi-bin/upload.cgi")


def test_catalog_falls_back_to_the_next_host_and_records_history(book, tmp_path):
    session = FakeSession([
        FakeResponse(status=503, text="busy"),
        FakeResponse(payload={"status": "ok", "data": {"downloadPage": "https://gofile.io/d/y"}}),
    ])
    tried = []
    history = catalog.UploadHistory(str(tmp_path / "history.json"))
    result = catalog.upload(book, ["tempsh", "gofile"], UploadContext(session=session), on_host=tried.append,
                            history=history, book_id="419368")
    assert result.link == "https://gofile.io/d/y"
    assert tried == ["temp.sh", "Gofile"]
    assert 'filename="419368.epub"' not in str(session.calls)  # names travel in the body, not headers
    assert [item.link for item in history.load()] == ["https://gofile.io/d/y"]


def test_catalog_skips_account_hosts_without_credentials(book):
    with pytest.raises(Exception) as error:
        catalog.upload(book, ["pixeldrain"], UploadContext(session=FakeSession([])))
    assert "Sign in to Pixeldrain" in str(error.value)


def test_folders_upload_as_one_zip(tmp_path):
    folder = tmp_path / "Book (TXT)"
    folder.mkdir()
    (folder / "001.txt").write_text("one", encoding="utf-8")
    zip_path = catalog.zip_folder(str(folder), str(tmp_path))
    assert os.path.basename(zip_path) == "Book (TXT).zip"
    import zipfile

    assert zipfile.ZipFile(zip_path).namelist() == ["Book (TXT)/001.txt"]


def test_upload_settings_round_trip_and_secrets_stay_out_of_exports():
    s = AppSettings()
    s = replace(s, upload=replace(s.upload, hosts=("gofile", "catbox"), agreement_accepted=True),
                secrets=replace(s.secrets, pixeldrain_key="secret"))
    out = dc.export_config(s)
    assert out["upload_hosts"] == ["gofile", "catbox"]
    assert out["share_agreement_accepted"] is True
    assert out["upload_pixeldrain_key"] == "secret"
    again = dc.import_config(out)
    assert again.upload.hosts == ("gofile", "catbox") and again.upload.agreement_accepted
    assert again.secrets.pixeldrain_key == "secret"
    assert "upload_pixeldrain_key" not in dc.export_config(s, include_secrets=False)
