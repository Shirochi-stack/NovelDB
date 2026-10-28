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
