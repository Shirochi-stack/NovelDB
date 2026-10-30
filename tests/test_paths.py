"""Where config, logs and cache live: next to the app when writable, else the per-user folder."""

import os
import stat
import sys
from pathlib import Path

import pytest

from noveldb.core import paths


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    paths.reset_cache()
    yield
    paths.reset_cache()


def test_writable_app_folder_keeps_files_next_to_the_app(tmp_path):
    assert paths.config_path(tmp_path) == tmp_path / "config.json"
    assert paths.logs_dir(tmp_path) == tmp_path / "logs"
    assert paths.cache_dir(tmp_path) == tmp_path / ".cache"
    assert paths.portable(tmp_path)


def test_noveldb_home_holds_everything(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    assert paths.config_path() == tmp_path / "home" / "config.json"
    assert paths.logs_dir() == tmp_path / "home" / "logs"
    assert paths.data_dir() == tmp_path / "home"


@pytest.mark.parametrize(
    "platform, env, expected",
    [
        ("win32", {"LOCALAPPDATA": "C:/Users/me/AppData/Local"}, Path("C:/Users/me/AppData/Local/NovelDB")),
        ("linux", {"XDG_CONFIG_HOME": "/home/me/.config"}, Path("/home/me/.config/NovelDB")),
    ],
)
def test_user_config_dir_per_platform(monkeypatch, platform, env, expected):
    monkeypatch.setattr(sys, "platform", platform)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert paths.user_dir("config") == expected


def test_macos_user_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.user_dir("config") == tmp_path / "Library" / "Application Support" / "NovelDB"
    assert paths.user_dir("logs") == tmp_path / "Library" / "Logs" / "NovelDB"
    assert paths.user_dir("cache") == tmp_path / "Library" / "Caches" / "NovelDB"


def test_linux_logs_and_cache_follow_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert paths.user_dir("logs") == tmp_path / "state" / "NovelDB" / "logs"
    assert paths.user_dir("cache") == tmp_path / "cache" / "NovelDB"


def test_read_only_app_folder_falls_back_and_copies_the_old_config(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    (app / "config.json").write_text('{"ext_threads": 3}', encoding="utf-8")
    user = tmp_path / "user"
    monkeypatch.setattr(paths, "is_writable_dir", lambda path: Path(path) != app)
    monkeypatch.setattr(paths, "user_dir", lambda kind="config": user / kind)
    target = paths.config_path(app)
    assert target == user / "config" / "config.json"
    assert target.read_text(encoding="utf-8") == '{"ext_threads": 3}'
    assert paths.logs_dir(app) == user / "logs"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_read_only_config_file_is_not_portable(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    config.chmod(stat.S_IRUSR)
    try:
        assert not paths.portable(tmp_path)
    finally:
        config.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_frozen_macos_install_dir_is_next_to_the_app_bundle(monkeypatch, tmp_path):
    exe = tmp_path / "Apps" / "NovelDB97.app" / "Contents" / "MacOS" / "NovelDB97"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert paths.install_dir() == (tmp_path / "Apps").resolve()


def test_an_appimage_uses_the_xdg_folders_and_takes_its_old_files_along(monkeypatch, tmp_path):
    folder = tmp_path / "Downloads"  # writable, so an older build kept its files here
    folder.mkdir()
    image = folder / "NovelDB98.AppImage"
    image.write_text("", encoding="utf-8")
    (folder / "config.json").write_text('{"ext_threads": 3}', encoding="utf-8")
    (folder / "data").mkdir()
    (folder / "data" / "link-cart.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", str(image))
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert not paths.portable()
    config = paths.config_path()
    assert config == tmp_path / "config" / "NovelDB" / "config.json"
    assert config.read_text(encoding="utf-8") == '{"ext_threads": 3}'
    assert paths.data_dir() == tmp_path / "data" / "NovelDB"
    assert (paths.data_dir() / "link-cart.json").read_text(encoding="utf-8") == "[]"
    assert paths.cache_dir() == tmp_path / "cache" / "NovelDB"
    assert paths.logs_dir() == tmp_path / "state" / "NovelDB" / "logs"
    assert paths.default_output_dir() == tmp_path / "home" / "Downloads" / "NovelDB"
    # Nothing new is written next to the AppImage.
    assert sorted(p.name for p in folder.iterdir()) == ["NovelDB98.AppImage", "config.json", "data"]


def test_appimage_install_dir(monkeypatch, tmp_path):
    image = tmp_path / "bin" / "NovelDB97.AppImage"
    image.parent.mkdir()
    image.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", str(image))
    assert paths.install_dir() == image.parent.resolve()
