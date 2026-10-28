"""A stand-in upload host for tests: records what it was sent and never touches the network."""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from noveldb.core.upload import catalog, hosts
from noveldb.core.upload.transfer import Rejected


@dataclass
class Sent:
    """One upload as the host received it."""

    name: str  # the file name the host was told
    path: str  # where the app had put the file
    data: bytes

    @property
    def is_zip(self) -> bool:
        return self.data[:4] == b"PK\x03\x04"

    @property
    def archive(self) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(self.data))

    @property
    def entry_names(self) -> list:
        return self.archive.namelist()


@dataclass
class FakeHost(hosts.Uploader):
    id: str = "fake"
    display_name: str = "Fake"
    max_bytes: Optional[int] = None
    reject: Callable[[str], bool] = lambda name: False
    sent: list = field(default_factory=list)

    def upload(self, path, context, progress=None, name=None):
        name = name or os.path.basename(path)
        if self.reject(name):
            raise Rejected(self.display_name, f"no thanks for {name}")
        with open(path, "rb") as handle:
            data = handle.read()
        if progress:
            progress(len(data) // 2, len(data))
            progress(len(data), len(data))
        self.sent.append(Sent(name, path, data))
        return self.result(path, f"https://fake.test/{len(self.sent)}/{name}")


def install(monkeypatch, host: FakeHost) -> FakeHost:
    """Make `catalog` find `host` under its id (the real hosts stay untouched)."""
    monkeypatch.setattr(catalog, "uploader", lambda host_id: host if host_id == host.id else None)
    return host


def make_file(folder, name: str, size: int, fill: bytes = b"x") -> str:
    path = os.path.join(str(folder), name)
    with open(path, "wb") as handle:
        handle.write((fill * size)[:size])
    return path
