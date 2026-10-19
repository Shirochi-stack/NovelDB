import builtins
import errno
import gzip
import json
from pathlib import Path

import pytest

from scripts import build_npia_top as builder
from scripts import metadata_common


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(builder, "__file__", str(tmp_path / "scripts" / "build_npia_top.py"))
    row = [123, "Original"] + [0] * 18
    row[10] = 1
    (data / "novels.json").write_text(json.dumps([row]), encoding="utf-8")
    (data / "titles_en.txt").write_text("123|||Original|||English\n", encoding="utf-8")
    (data / "descriptions.txt").write_text("123|||Original|||Description\n", encoding="utf-8")
    (data / "npia_top.json.gz").write_bytes(gzip.compress(b'{"previous":true}'))
    (data / "npia_top.json").write_text("legacy", encoding="utf-8")
    return data


def test_build_replaces_output_without_opening_existing_file_for_truncation(data_dir, monkeypatch):
    target = data_dir / "npia_top.json.gz"
    original_open = builtins.open

    def guarded_open(path, mode="r", *args, **kwargs):
        if Path(path) == target and mode == "wb":
            raise OSError(errno.EINVAL, "Invalid argument", str(path))
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    builder.main()
    result = json.loads(gzip.decompress(target.read_bytes()))
    assert len(result["novels"]) == 1
    assert result["translations"] == {"123": "English"}
    assert result["descriptions"] == {"123": "Description"}
    assert not (data_dir / "npia_top.json").exists()


def test_failed_replacement_preserves_previous_output_and_cleans_temp(data_dir, monkeypatch):
    target = data_dir / "npia_top.json.gz"
    before = target.read_bytes()

    def fail_replace(*args):
        raise PermissionError("output locked")

    monkeypatch.setattr(metadata_common.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="output locked"):
        builder.main()
    assert target.read_bytes() == before
    assert (data_dir / "npia_top.json").exists()
    assert not list(data_dir.glob("npia_top.json.gz.*"))
