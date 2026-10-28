"""OS actions dispatch to the right command on each platform."""

import sys

import pytest

from noveldb.core import platform_services as ps


@pytest.fixture
def popen(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(ps.subprocess, "Popen", FakePopen)
    return calls


def test_open_on_macos_and_linux(monkeypatch, popen, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ps.open_path(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    assert ps.open_path(tmp_path)
    assert [args[0] for args, _ in popen] == ["open", "xdg-open"]


def test_reveal_selects_the_file_on_windows_and_macos(monkeypatch, popen, tmp_path):
    target = tmp_path / "book.epub"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ps, "_hidden_kwargs", lambda: {})
    ps.reveal_path(target)
    monkeypatch.setattr(sys, "platform", "darwin")
    ps.reveal_path(target)
    assert popen[0][0] == ["explorer", "/select,", str(target)]
    assert popen[1][0] == ["open", "-R", str(target)]


def test_reveal_on_linux_opens_the_folder(monkeypatch, popen, tmp_path):
    target = tmp_path / "book.epub"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "linux")
    ps.reveal_path(target)
    assert popen[0][0] == ["xdg-open", str(tmp_path)]


@pytest.mark.parametrize(
    "platform, which, expected",
    [
        ("win32", None, ["shutdown", "/s", "/t", "60"]),
        ("darwin", None, ["osascript", "-e", 'tell application "System Events" to shut down']),
        ("linux", "/usr/bin/systemctl", ["systemctl", "poweroff"]),
        ("linux", None, None),
    ],
)
def test_shutdown_command_per_platform(monkeypatch, platform, which, expected):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(ps.shutil, "which", lambda name: which)
    assert ps.shutdown_command() == expected
    assert ps.shutdown_supported() is (expected is not None)


def test_frozen_linux_children_get_the_original_library_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    assert ps.external_process_env()["LD_LIBRARY_PATH"] == "/usr/local/lib"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in ps.external_process_env()
