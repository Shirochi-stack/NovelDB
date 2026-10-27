"""Where NovelDB keeps its files. Standard library only, so `dpi_setup` can use it before Qt loads.

The app stays portable: `config.json`, `logs/` and `.cache/` live next to the executable (or the
source checkout) whenever that folder is writable, exactly as before. When it is not - a macOS
`.app` in /Applications, an AppImage, a Program Files install - they move to the per-user folder:

    Windows  %LOCALAPPDATA%\\NovelDB
    macOS    ~/Library/Application Support/NovelDB   (logs: ~/Library/Logs, cache: ~/Library/Caches)
    Linux    $XDG_CONFIG_HOME/NovelDB                 (logs: $XDG_STATE_HOME, cache: $XDG_CACHE_HOME)

A read-only `config.json` found next to the app is copied to the per-user folder once.
`NOVELDB_HOME` puts everything in one folder (tests, portable USB installs).
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

APP_DIRNAME = "NovelDB"
CONFIG_NAME = "config.json"
HOME_ENV = "NOVELDB_HOME"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Read-only files shipped with the app (JS rules, fonts, icons)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parents[2]


def install_dir() -> Path:
    """The folder users see the app in: next to the exe, the AppImage, or the .app bundle."""
    if not is_frozen():
        return Path(__file__).resolve().parents[2]
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return Path(appimage).resolve().parent
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent.parent
    return exe.parent


def is_writable_dir(path: os.PathLike | str) -> bool:
    """True when files can be created in `path` (creating it if needed)."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".noveldb_write_probe_{os.getpid()}")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def _home() -> Path | None:
    value = os.environ.get(HOME_ENV, "").strip()
    return Path(value).expanduser() if value else None


def user_dir(kind: str = "config") -> Path:
    """The per-user folder for `kind`: config, data, logs or cache."""
    home = Path.home()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP_DIRNAME
        return {"logs": root / "logs", "cache": root / "cache"}.get(kind, root)
    if sys.platform == "darwin":
        library = home / "Library"
        return {
            "logs": library / "Logs" / APP_DIRNAME,
            "cache": library / "Caches" / APP_DIRNAME,
        }.get(kind, library / "Application Support" / APP_DIRNAME)
    xdg = {
        "config": ("XDG_CONFIG_HOME", home / ".config"),
        "data": ("XDG_DATA_HOME", home / ".local" / "share"),
        "logs": ("XDG_STATE_HOME", home / ".local" / "state"),
        "cache": ("XDG_CACHE_HOME", home / ".cache"),
    }
    env, default = xdg.get(kind, xdg["config"])
    base = Path(os.environ.get(env) or default) / APP_DIRNAME
    return base / "logs" if kind == "logs" else base


@lru_cache(maxsize=None)
def _portable(base: str) -> bool:
    folder = Path(base)
    if not is_writable_dir(folder):
        return False
    config = folder / CONFIG_NAME
    return not config.exists() or os.access(config, os.W_OK)


def portable(base: os.PathLike | str | None = None) -> bool:
    """Files live next to the app (the folder is writable, and so is any config there)."""
    if _home() is not None:
        return True
    return _portable(str(Path(base) if base is not None else install_dir()))


def config_path(base: os.PathLike | str | None = None) -> Path:
    """The `config.json` to read and write. `base` overrides the app folder (tests, old callers)."""
    home = _home()
    if home is not None:
        home.mkdir(parents=True, exist_ok=True)
        return home / CONFIG_NAME
    folder = Path(base) if base is not None else install_dir()
    if portable(folder):
        return folder / CONFIG_NAME
    target_dir = user_dir("config")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / CONFIG_NAME
    legacy = folder / CONFIG_NAME
    if not target.exists() and legacy.is_file():
        try:
            shutil.copy2(legacy, target)
        except OSError:
            pass
    return target


def _folder(name: str, kind: str, base: os.PathLike | str | None) -> Path:
    home = _home()
    if home is not None:
        path = home / name
    else:
        folder = Path(base) if base is not None else install_dir()
        path = folder / name if portable(folder) else user_dir(kind)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir(base: os.PathLike | str | None = None) -> Path:
    """App state such as the link cart, the queue and web view profiles."""
    home = _home()
    folder = Path(base) if base is not None else install_dir()
    if home is not None:
        path = home
    elif portable(folder):
        path = folder / "data"
    else:
        path = user_dir("data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(base: os.PathLike | str | None = None) -> Path:
    return _folder("logs", "logs", base)


def cache_dir(base: os.PathLike | str | None = None) -> Path:
    return _folder(".cache", "cache", base)


def default_output_dir(base: os.PathLike | str | None = None) -> Path:
    """Where downloads go when no folder is chosen: the app folder, else ~/Downloads/NovelDB."""
    folder = Path(base) if base is not None else install_dir()
    if is_writable_dir(folder):
        return folder
    path = Path.home() / "Downloads" / APP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def reset_cache() -> None:
    """Forget cached writability checks (tests change folders and permissions)."""
    _portable.cache_clear()
