"""Credentials never reach the engine bundles or the repository (scripts/secret_guard.py)."""
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import secret_guard  # noqa: E402

KEY = "AIza" + "B" * 35  # the shape of a Google API key, built here so the source holds none


def test_api_keys_are_blanked_in_bundles():
    text, found = secret_guard.redact(f'headers:{{"X-Goog-API-Key":"{KEY}"}}', "lib.js")
    assert KEY not in text and '"X-Goog-API-Key":""' in text
    assert found == ["Google API key x1 in lib.js"]


def test_a_private_key_or_token_stops_the_build():
    with pytest.raises(secret_guard.SecretFound):
        secret_guard.redact("-----BEGIN RSA " + "PRIVATE KEY-----\nabc")
    with pytest.raises(secret_guard.SecretFound):
        secret_guard.redact("token = 'ghp_" + "a" * 36 + "'")


def test_zip_bundles_are_cleaned_member_by_member(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sources/a.py", f"KEY = '{KEY}'\n")
        archive.writestr("data.bin", b"\x00\xff")
    cleaned, found = secret_guard.redact_zip(buffer.getvalue(), "sources.zip")
    with zipfile.ZipFile(io.BytesIO(cleaned)) as archive:
        assert KEY not in archive.read("sources/a.py").decode() and archive.read("data.bin") == b"\x00\xff"
    assert found
    (tmp_path / "s.zip").write_bytes(buffer.getvalue())
    assert secret_guard.scan_paths([tmp_path / "s.zip"]) == [f"{tmp_path / 's.zip'}: Google API key"]


def test_the_shipped_bundles_hold_no_credentials():
    root = Path(__file__).resolve().parent.parent
    bundles = [root / p for p in ("lnreader/lnreader-lib.js", "haruneko/haruneko-lib.js", "webtoepub/webtoepub-lib.js",
                                  "rules-lib.js", "lncrawl/lncrawl-sources.zip", "fanficfare/fanficfare.zip")]
    assert secret_guard.scan_paths([b for b in bundles if b.exists()]) == []
