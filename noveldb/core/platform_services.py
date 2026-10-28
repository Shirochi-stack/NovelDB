"""Operating-system actions: open files and folders, and shut down after downloads.

Child processes get `external_process_env()`: frozen Linux builds point `LD_LIBRARY_PATH` at the
bundle, which would break programs such as `xdg-open` or the browser they launch, so the
original value is restored for them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def external_process_env() -> dict:
    env = dict(os.environ)
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is not None:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def _hidden_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def open_path(path: os.PathLike | str) -> bool:
    """Open a file with its default app, or a folder in the file manager."""
    target = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target], env=external_process_env())
        return True
    except (OSError, ValueError):
        return False


def reveal_path(path: os.PathLike | str) -> bool:
    """Show a file selected in the file manager (its folder where selecting is unsupported)."""
    target = Path(path)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)], **_hidden_kwargs())
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
            return True
    except OSError:
        return False
    return open_path(target.parent if target.is_file() else target)


def _select_on_windows(folder: str, files: list[Path]) -> bool:
    """One Explorer window on `folder` with all of `files` selected (the shell API; `/select,` takes one)."""
    import ctypes
    from ctypes import wintypes

    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32  # type: ignore[attr-defined]
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
    shell32.ILFindLastID.restype = ctypes.c_void_p
    shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
    initialised = ole32.CoInitialize(None) in (0, 1)  # S_OK, S_FALSE: this thread now counts one more
    folder_id = shell32.ILCreateFromPathW(os.path.normpath(folder))
    full_ids = [shell32.ILCreateFromPathW(os.path.normpath(str(f))) for f in files]
    full_ids = [item for item in full_ids if item]
    try:
        if not folder_id or not full_ids:
            return False
        children = (ctypes.c_void_p * len(full_ids))(*[shell32.ILFindLastID(item) for item in full_ids])
        return shell32.SHOpenFolderAndSelectItems(folder_id, len(full_ids), children, 0) == 0
    finally:
        for item in [folder_id, *full_ids]:
            if item:
                shell32.ILFree(item)
        if initialised:
            ole32.CoUninitialize()


def reveal_paths(paths, max_folders: int = 5) -> bool:
    """Show files selected in their folders: one file-manager window per folder, all its files selected.

    Windows and macOS select every file; where the file manager cannot be told to select (Linux) the
    folder opens instead. At most `max_folders` windows open.
    """
    groups: dict[str, list[Path]] = {}
    for path in paths:
        target = Path(path)
        groups.setdefault(str(target.parent), []).append(target)
    shown = False
    for folder, files in list(groups.items())[:max_folders]:
        if len(files) == 1:
            shown = reveal_path(files[0]) or shown
        elif sys.platform == "win32":
            try:
                ok = _select_on_windows(folder, files)
            except (OSError, AttributeError, ValueError):
                ok = False
            shown = (ok or reveal_path(files[0])) or shown
        elif sys.platform == "darwin":
            try:
                subprocess.Popen(["open", "-R", *map(str, files)])
                shown = True
            except OSError:
                pass
        else:
            shown = open_path(folder) or shown
    return shown


def shutdown_command(delay_seconds: int = 60) -> Optional[list[str]]:
    """The command that shuts this computer down, or None when this platform cannot.

    Windows schedules it (cancel: `shutdown /a`); macOS and Linux shut down immediately, so the
    app runs its own countdown first.
    """
    if sys.platform == "win32":
        return ["shutdown", "/s", "/t", str(int(delay_seconds))]
    if sys.platform == "darwin":
        return ["osascript", "-e", 'tell application "System Events" to shut down']
    if shutil.which("systemctl"):
        return ["systemctl", "poweroff"]
    return None


def shutdown_supported() -> bool:
    return shutdown_command() is not None


def app_counts_down() -> bool:
    """True where the app must wait before running the shutdown command itself."""
    return sys.platform != "win32"


def schedule_shutdown(delay_seconds: int = 60) -> bool:
    command = shutdown_command(delay_seconds)
    if command is None:
        return False
    try:
        subprocess.Popen(command, env=external_process_env(), **_hidden_kwargs())
        return True
    except OSError:
        return False


def cancel_shutdown() -> bool:
    """Cancel a scheduled Windows shutdown (elsewhere the app's own countdown is cancelled)."""
    if sys.platform != "win32":
        return True
    try:
        subprocess.run(["shutdown", "/a"], check=False, **_hidden_kwargs())
        return True
    except OSError:
        return False
