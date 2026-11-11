"""Optional catalogue installs preserve the active snapshot across failed updates."""
import gzip
import hashlib
import io
import json
from zipfile import ZipFile

from noveldb.core.catalog import local_data
from noveldb.core.catalog.instant import read_piece


def package(books):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("manifest.json.gz", gzip.compress(json.dumps({"version": 1, "books": books}).encode()))
    return output.getvalue()


def test_manual_install_update_failure_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(local_data, "root", lambda: tmp_path / "catalogue-data")
    assert local_data.status()["version"] == ""
    revision = "first"
    content = package(100)
    corrupt = False

    def fetch(url, **_):
        if url.endswith("noveldb-local.json"):
            entries = [{"name": f"noveldb-local-{revision}-{part}.zip", "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()} for part in range(2)]
            return io.BytesIO(json.dumps({"revision": revision, "built": "2026-11-11", "books": 100, "files": entries}).encode())
        return io.BytesIO(b"corrupt" if corrupt else content)

    monkeypatch.setattr(local_data, "urlopen", fetch)
    local_data._download()
    assert local_data.status()["version"] == "first"
    assert read_piece("manifest", version="first")["books"] == 100
    revision, corrupt = "second", True
    local_data._download()
    assert local_data.status()["version"] == "first"
    assert "checksum" in local_data.status()["error"]
    assert read_piece("manifest", version="first")["books"] == 100
    local_data.handle("remove")
    assert local_data.status()["version"] == ""
    assert not (tmp_path / "catalogue-data").exists()
