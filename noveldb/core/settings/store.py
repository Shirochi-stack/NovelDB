"""The one place `config.json` is read and written.

Every save re-reads the file and merges into it, so keys owned by other tools (the bot, older
builds, the mobile export) survive. Writes are atomic - a temporary file in the same folder,
flushed and fsynced, then `os.replace` - and go through `scripts.source_names.dump`, which stores
provider names as escapes. On Windows `os.replace` is retried briefly because antivirus scanners
and other readers can hold the file for a moment.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from noveldb.core import paths
from noveldb.core.settings.desktop_config import export_config, import_config
from noveldb.core.settings.model import AppSettings

try:
    from scripts.source_names import dump as _dump
except ImportError:  # pragma: no cover - scripts ships with the app
    def _dump(obj, handle, **kwargs):
        json.dump(obj, handle, **kwargs)


def read_raw(path: Optional[os.PathLike | str] = None) -> dict:
    """The config as a dict; {} when it is missing or unreadable."""
    target = Path(path) if path is not None else paths.config_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_raw(data: dict, path: Optional[os.PathLike | str] = None, retries: int = 3) -> Path:
    """Atomically replace the config with `data`."""
    target = Path(path) if path is not None else paths.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            _dump(data, handle, indent=2)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        for attempt in range(retries + 1):
            try:
                os.replace(temp, target)
                break
            except PermissionError:
                if attempt == retries:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return target


class ConfigStore:
    """Loads `AppSettings` from config.json and saves them back, merging with what is on disk."""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        # Resolved once: a store never changes files later (a save that fires after NOVELDB_HOME
        # or the working folder changed must not land in another config.json).
        self._path = Path(path) if path is not None else paths.config_path()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> AppSettings:
        with self._lock:
            return import_config(read_raw(self.path))

    def save(self, settings: AppSettings) -> Path:
        with self._lock:
            merged = export_config(settings, read_raw(self.path))
            return write_raw(merged, self.path)

    def ensure_exists(self, settings: Optional[AppSettings] = None) -> None:
        """Create config.json with defaults on first launch (`_ensure_config_exists`)."""
        with self._lock:
            if not self.exists():
                self.save(settings or AppSettings())

    def update_raw(self, changes: dict) -> Path:
        """Merge raw keys into the file (for code that still owns its own keys)."""
        with self._lock:
            data = read_raw(self.path)
            data.update(changes)
            return write_raw(data, self.path)
