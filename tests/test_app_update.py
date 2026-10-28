"""App self-updates: build info, the release manifest, checking, verified downloads and the install helpers."""
import hashlib
import json
import os
import subprocess
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest

from noveldb.core import app_update as au
from scripts import update_manifest

PYTHON = sys.executable  # tests below point sys.executable at a fake app
INFO = au.BuildInfo(build=1000, tag="v98", label="none", variant="full", os="windows", arch="x86_64", kind="exe")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NOVELDB_UPDATE_BASE", raising=False)
    return tmp_path / "home"


def _asset(body=b"new build", **extra):
    entry = {"name": "NovelDB98-windows-x86_64_hotfix1.exe", "variant": "full", "arch": "x86_64", "kind": "exe",
             "build": 2000, "label": "hotfix1", "commit": "c" * 40, "size": len(body),
             "sha256": hashlib.sha256(body).hexdigest()}
    entry.update(extra)
    return entry


def _manifest(*assets, **extra):
    data = {"schema": 1, "platform": "windows", "tag": "v98",
            "release_url": "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", "assets": list(assets),
            "future_field": {"ignored": True}}
    data.update(extra)
    return json.dumps(data).encode()


# -- the manifest -------------------------------------------------------------------------------------

def test_a_newer_build_of_this_copy_is_an_update():
    update = au.find_update(INFO, _manifest(_asset()))
    assert update is not None and update.title == "v98 hotfix1"
    assert update.url == ("https://github.com/Shirochi-stack/NovelDB/releases/download/v98/"
                          "NovelDB98-windows-x86_64_hotfix1.exe")


def test_only_this_variant_arch_and_kind_count():
    other = [_asset(variant="lite"), _asset(arch="arm64"), _asset(kind="zip")]
    assert au.find_update(INFO, _manifest(*other)) is None
    assert au.find_update(INFO, _manifest(_asset(build=1000))) is None  # the same build
    assert au.find_update(INFO, _manifest(_asset(build=900))) is None  # an older one


@pytest.mark.parametrize("change", [
    {"schema": 2}, {"platform": "macos"}, {"tag": "../v98"},
])
def test_manifests_this_app_cannot_read_are_ignored(change):
    assert au.find_update(INFO, _manifest(_asset(), **change)) is None
    assert au.find_update(INFO, b"not json") is None


@pytest.mark.parametrize("bad", [
    {"name": "../evil.exe"}, {"name": "a/b.exe"}, {"sha256": "0" * 63}, {"size": 0}, {"build": "2000"},
])
def test_bad_entries_are_skipped(bad):
    assert au.find_update(INFO, _manifest(_asset(**bad))) is None


# -- checking -----------------------------------------------------------------------------------------

def test_development_builds_and_copies_that_cannot_replace_themselves_never_check():
    def fetch(url, timeout):
        raise AssertionError("no network")
    assert au.check(au.BuildInfo(), fetch).status == "off"
    assert "Development build" in au.check(au.BuildInfo(), fetch).message
    assert au.check(au.BuildInfo(build=5, os="linux", kind=""), fetch).status == "off"


def test_check_reads_the_latest_releases_manifest(monkeypatch):
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return _manifest(_asset())
    result = au.check(INFO, fetch)
    assert result.status == "update" and result.update.asset.build == 2000
    assert seen == ["https://github.com/Shirochi-stack/NovelDB/releases/latest/download/update-windows.json"]
    monkeypatch.setenv("NOVELDB_UPDATE_BASE", "http://127.0.0.1:8123/rel")
    au.check(INFO, fetch)
    assert seen[-1] == "http://127.0.0.1:8123/rel/latest/download/update-windows.json"


def test_check_results():
    assert au.check(INFO, lambda u, t: _manifest(_asset(build=1000))).status == "current"

    def missing(url, timeout):
        raise FileNotFoundError(url)

    def offline(url, timeout):
        raise OSError("offline")
    assert au.check(INFO, missing).status == "none"
    failed = au.check(INFO, offline)
    assert failed.status == "failed" and "offline" in failed.message


def test_the_automatic_check_runs_once_a_day():
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return _manifest()
    assert au.check_if_due(INFO, fetch=fetch) is not None
    assert au.check_if_due(INFO, fetch=fetch) is None
    assert len(calls) == 1
    assert au.last_checked() > time.time() - 60
    assert au.check_if_due(au.BuildInfo(), fetch=fetch) is None


# -- downloading --------------------------------------------------------------------------------------

def _update(body=b"new build", **extra):
    return au.find_update(INFO, _manifest(_asset(body, **extra)))


def test_a_download_is_kept_only_when_size_and_sha256_match(tmp_path):
    body = b"x" * 3_000_000
    seen = []
    path = au.download(_update(body), tmp_path, progress=lambda d, t: seen.append((d, t)),
                       stream=lambda url, timeout: [body[:1_000_000], body[1_000_000:]])
    assert path.read_bytes() == body and path.name == "NovelDB98-windows-x86_64_hotfix1.exe"
    assert seen[-1] == (3_000_000, 3_000_000)


@pytest.mark.parametrize("served", [b"tampered", b"new build plus more", b"new"])
def test_a_download_that_does_not_match_is_deleted(tmp_path, served):
    with pytest.raises(au.UpdateError, match="did not match its checksum"):
        au.download(_update(b"new build"), tmp_path, stream=lambda url, timeout: [served])
    assert list(tmp_path.iterdir()) == []


def test_a_download_can_be_cancelled(tmp_path):
    with pytest.raises(au.UpdateCancelled):
        au.download(_update(), tmp_path, cancelled=lambda: True, stream=lambda url, timeout: [b"new ", b"build"])
    assert list(tmp_path.iterdir()) == []


# -- this copy ----------------------------------------------------------------------------------------

def test_build_info_from_source_is_a_development_build():
    assert au.build_info() == au.BuildInfo()


@pytest.mark.parametrize("system,mode,appimage,kind", [
    ("windows", "onefile", "", "exe"), ("windows", "onedir", "", "zip"),
    ("linux", "onedir", "/opt/NovelDB.AppImage", "appimage"), ("linux", "onedir", "", ""),
])
def test_build_info_of_a_release_build(monkeypatch, system, mode, appimage, kind):
    module = types.SimpleNamespace(VARIANT="lite", BUILD_MODE=mode, BUILD=1793193554, TAG="v98", LABEL="hotfix2",
                                   COMMIT="abc", OS=system, ARCH="x86_64")
    monkeypatch.setitem(sys.modules, "_build_info", module)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    if appimage:
        monkeypatch.setenv("APPIMAGE", appimage)
    else:
        monkeypatch.delenv("APPIMAGE", raising=False)
    info = au.build_info()
    assert (info.build, info.variant, info.os, info.kind) == (1793193554, "lite", system, kind)
    assert info.describe() == "v98 hotfix2, build 1793193554"
    assert info.can_update == bool(kind)


# -- installing ---------------------------------------------------------------------------------------

def _run_helper(script: Path) -> None:
    """Run a PowerShell helper for real, minus the final relaunch of the app."""
    text = script.read_text(encoding="utf-8-sig")
    text = "\n".join(line for line in text.splitlines() if not line.startswith("Start-Process"))
    script.write_text(text, encoding="utf-8-sig")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                    str(script)], check=True, timeout=120, capture_output=True)


def _exited_pid() -> int:
    process = subprocess.Popen([PYTHON, "-c", "pass"])
    process.wait()
    return process.pid


@pytest.mark.skipif(sys.platform != "win32", reason="Windows helper")
def test_the_windows_exe_helper_replaces_the_program(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    exe = app / "NovelDB98-windows-x86_64.exe"
    exe.write_bytes(b"old build")
    (app / "config.json").write_text("{}")
    monkeypatch.setattr(sys, "executable", str(exe))
    new = au.updates_dir()
    new.mkdir(parents=True)
    (new / "download.exe").write_bytes(b"new build")
    plan = au.prepare_install(_update(), new / "download.exe", INFO, pid=_exited_pid())
    assert plan.command[0] == "powershell.exe" and "Start-Process" in Path(plan.command[-1]).read_text("utf-8-sig")
    _run_helper(Path(plan.command[-1]))
    assert exe.read_bytes() == b"new build"
    assert (app / "config.json").read_text() == "{}"  # the portable settings stay
    assert not (new / "download.exe").exists()
    assert "Updated" in (au.paths.logs_dir() / "update.log").read_text(encoding="utf-8")


def _folder_build(path: Path, top: str, exe_name: str, marker: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{top}/{exe_name}", f"exe {marker}")
        archive.writestr(f"{top}/_internal/lib.dll", f"lib {marker}")
        archive.writestr(f"{top}/_internal/sub/data.txt", f"data {marker}")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows helper")
def test_the_windows_folder_helper_replaces_the_program_and_its_libraries(tmp_path, monkeypatch):
    app = tmp_path / "NovelDB98"
    (app / "_internal").mkdir(parents=True)
    exe = app / "NovelDB98.exe"
    exe.write_text("exe old")
    (app / "_internal" / "stale.dll").write_text("stale")
    (app / "config.json").write_text("{}")
    monkeypatch.setattr(sys, "executable", str(exe))
    archive = tmp_path / "update.zip"
    _folder_build(archive, "NovelDB99", "NovelDB99.exe", "new")
    zip_info = au.BuildInfo(**{**INFO.__dict__, "kind": "zip"})
    plan = au.prepare_install(_update(), archive, zip_info, pid=_exited_pid())
    _run_helper(Path(plan.command[-1]))
    assert exe.read_text() == "exe new"  # the program keeps its name
    assert (app / "_internal" / "lib.dll").read_text() == "lib new"
    assert (app / "_internal" / "sub" / "data.txt").read_text() == "data new"
    assert not (app / "_internal" / "stale.dll").exists() and not (app / "_internal.old").exists()
    assert (app / "config.json").read_text() == "{}"
    assert not (au.updates_dir() / "staging").exists()


def test_an_archive_that_is_not_a_folder_build_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NovelDB98.exe"))
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("readme.txt", "hello")
    with pytest.raises(au.UpdateError):
        au.prepare_install(_update(), archive, au.BuildInfo(**{**INFO.__dict__, "kind": "zip"}))


def test_the_appimage_helper_script(tmp_path, monkeypatch):
    appimage = tmp_path / "My Apps" / "NovelDB98-linux-x86_64.AppImage"
    monkeypatch.setenv("APPIMAGE", str(appimage))
    info = au.BuildInfo(**{**INFO.__dict__, "os": "linux", "kind": "appimage"})
    plan = au.prepare_install(_update(), tmp_path / "new.AppImage", info, pid=4321)
    text = Path(plan.command[1]).read_text(encoding="utf-8")
    assert plan.command[0] == "/bin/sh"
    assert "while kill -0 4321" in text
    assert "'" + str(appimage) + "'" in text  # quoted: the path has a space
    assert "chmod +x" in text and "nohup" in text


# -- the release side ---------------------------------------------------------------------------------

def test_update_manifest_describes_release_files():
    d = update_manifest.describe
    assert d("NovelDB98-windows-x86_64.exe", "windows") == {"variant": "full", "arch": "x86_64", "kind": "exe"}
    assert d("NovelDB98-windows-x86_64_hotfix2.zip", "windows")["kind"] == "zip"
    assert d("NovelDB98_TurboLite-macos-arm64.dmg", "macos") == {"variant": "turbolite", "arch": "arm64",
                                                                 "kind": "dmg"}
    assert d("NovelDB98_Lite-linux-x86_64.AppImage", "linux")["kind"] == "appimage"
    assert d("NovelDB-universal_hotfix3.apk", "android") == {"variant": "universal", "arch": "universal",
                                                             "kind": "apk"}
    assert d("NovelDB-unsigned.ipa", "ios")["kind"] == "ipa"
    assert d("NovelDB98-windows-x86_64.exe", "linux") is None
    assert d("SHA256SUMS-windows.txt", "windows") is None


def test_update_manifest_round_trips_through_the_app(tmp_path):
    exe = tmp_path / "NovelDB98-windows-x86_64_hotfix1.exe"
    exe.write_bytes(b"new build")
    lite = tmp_path / "NovelDB98_Lite-windows-x86_64_hotfix1.exe"
    lite.write_bytes(b"lite build")
    old = update_manifest.build_manifest("windows", "v98", 1500, "none", "a", [exe, lite])
    # A later run that rebuilds only Full keeps the Lite entry.
    manifest = update_manifest.build_manifest("windows", "v98", 2000, "hotfix1", "c" * 40, [exe], existing=old)
    builds = {e["variant"]: e["build"] for e in manifest["assets"]}
    assert builds == {"full": 2000, "lite": 1500}
    update = au.find_update(INFO, json.dumps(manifest).encode())
    assert update is not None and update.asset.sha256 == hashlib.sha256(b"new build").hexdigest()
    path = au.download(update, tmp_path / "dl", stream=lambda url, timeout: [exe.read_bytes()])
    assert path.read_bytes() == b"new build"


def test_update_manifest_command_line(tmp_path):
    apk = tmp_path / "NovelDB-universal.apk"
    apk.write_bytes(b"apk")
    out = tmp_path / "update-android.json"
    assert update_manifest.main(["--platform", "android", "--tag", "v98", "--build", "1793193554",
                                 "--commit", "abc", "--out", str(out), str(apk)]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["platform"] == "android" and manifest["assets"][0]["kind"] == "apk"
    assert manifest["assets"][0]["label"] == "none"
    with pytest.raises(SystemExit):
        update_manifest.main(["--platform", "ios", "--tag", "v98", "--build", "1", "--out", str(out), str(apk)])
