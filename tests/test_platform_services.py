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


def make_books(folder, *names):
    folder.mkdir(exist_ok=True)
    paths = []
    for name in names:
        (folder / name).write_text("x", encoding="utf-8")
        paths.append(folder / name)
    return paths


def test_reveal_paths_selects_all_files_of_a_folder_on_macos(monkeypatch, popen, tmp_path):
    first, second = make_books(tmp_path, "a.epub", "b.epub")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ps.reveal_paths([first, second])
    assert popen[0][0] == ["open", "-R", str(first), str(second)]


def test_reveal_paths_opens_one_window_per_folder_and_a_lone_file_is_selected(monkeypatch, popen, tmp_path):
    one, two = make_books(tmp_path / "x", "a.epub", "b.epub")
    (three,) = make_books(tmp_path / "y", "c.epub")
    monkeypatch.setattr(sys, "platform", "darwin")
    ps.reveal_paths([one, three, two])
    assert [args for args, _ in popen] == [["open", "-R", str(one), str(two)], ["open", "-R", str(three)]]


def test_reveal_paths_opens_the_folder_on_linux(monkeypatch, popen, tmp_path):
    first, second = make_books(tmp_path, "a.epub", "b.epub")
    monkeypatch.setattr(sys, "platform", "linux")
    assert ps.reveal_paths([first, second])
    assert [args for args, _ in popen] == [["xdg-open", str(tmp_path)]]


def test_reveal_paths_on_windows_uses_the_shell_selection_and_falls_back(monkeypatch, popen, tmp_path):
    first, second = make_books(tmp_path, "a.epub", "b.epub")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ps, "_hidden_kwargs", lambda: {})
    selected = []
    monkeypatch.setattr(ps, "_select_on_windows", lambda folder, files: selected.append((folder, files)) or True)
    assert ps.reveal_paths([first, second])
    assert selected == [(str(tmp_path), [first, second])] and popen == []
    monkeypatch.setattr(ps, "_select_on_windows", lambda folder, files: False)  # the shell call failed
    assert ps.reveal_paths([first, second])
    assert popen[0][0] == ["explorer", "/select,", str(first)]


def test_reveal_paths_limits_the_number_of_windows(monkeypatch, popen, tmp_path):
    files = [make_books(tmp_path / f"f{i}", "a.epub")[0] for i in range(8)]
    monkeypatch.setattr(sys, "platform", "darwin")
    ps.reveal_paths(files)
    assert len(popen) == 5
    assert ps.reveal_paths([]) is False


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
