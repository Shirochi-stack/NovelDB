"""The sites the login browser has cookies for (Settings > Saved sessions), so a user can check a login
is saved. Only cookie names, hosts and expiry dates are read, never their values.

The browser profiles are the downloader's (Enter Browser) and the NewToki one; each is a Chromium
profile whose cookie database is SQLite (any platform). The database may be locked while the browser
runs, so a copy is read.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Cookie names that usually mean a login (a session id, an auth token).
LOGIN_NAMES = re.compile(r"(sess|sid\b|auth|token|login|logged|remember|jwt|user_?id|member|account|_t\b|ticket)", re.I)
# Chromium counts time from 1601-01-01 in microseconds.
_CHROME_EPOCH = 11644473600


@dataclass
class SavedSite:
    site: str
    cookies: int = 0
    login: bool = False
    expires: float = 0.0           # the latest expiry (Unix time), 0 for session-only cookies
    profiles: List[str] = field(default_factory=list)


def _site_of(host: str) -> str:
    from noveldb.core.engines.generic import site_of

    return site_of(host.lstrip(".").lower())


def profile_folders() -> Dict[str, Path]:
    """The login browser profiles that exist: {label: folder}."""
    from external_scraper import ExternalScraper

    out = {}
    for label, getter in (("Enter Browser", "_get_user_data_dir"), ("NewToki browser", "_get_ntk_user_data_dir")):
        try:
            folder = Path(getattr(ExternalScraper, getter)())
        except Exception:
            continue
        if folder.is_dir():
            out[label] = folder
    return out


def _cookie_files(profile: Path) -> List[Path]:
    found = []
    for name in ("Default", "Profile 1"):
        for relative in ("Network/Cookies", "Cookies"):
            path = profile / name / relative
            if path.is_file():
                found.append(path)
    return found


def _read(database: Path) -> List[tuple]:
    """(host, name, expires) of every cookie, from a copy of the database (the browser may hold a lock)."""
    with tempfile.TemporaryDirectory(prefix="noveldb-cookies-") as temp:
        copy = Path(temp) / "Cookies"
        try:
            shutil.copyfile(database, copy)
        except OSError:
            copy = database  # locked: read it in place, read-only
        uri = f"file:{copy.as_posix()}?mode=ro" + ("&immutable=1" if copy == database else "")
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            return connection.execute("SELECT host_key, name, expires_utc FROM cookies").fetchall()
        finally:
            connection.close()


def saved_sites(storage_state: Optional[Path] = None) -> List[SavedSite]:
    """Every site with cookies in the login browsers, sites with a login cookie first."""
    import json
    import time

    sites: Dict[str, SavedSite] = {}

    def add(host: str, name: str, expires: float, label: str) -> None:
        site = _site_of(host)
        if not site:
            return
        entry = sites.setdefault(site, SavedSite(site))
        entry.cookies += 1
        entry.login = entry.login or bool(LOGIN_NAMES.search(name or ""))
        entry.expires = max(entry.expires, expires)
        if label not in entry.profiles:
            entry.profiles.append(label)

    for label, folder in profile_folders().items():
        for database in _cookie_files(folder):
            try:
                rows = _read(database)
            except Exception:
                continue
            for host, name, expires in rows:
                seconds = expires / 1_000_000 - _CHROME_EPOCH if expires else 0.0
                add(host, name, seconds, label)
        state = storage_state or folder / "nd_storage_state.json"
        try:
            for cookie in json.loads(Path(state).read_text(encoding="utf-8")).get("cookies", []):
                add(cookie.get("domain", ""), cookie.get("name", ""), float(cookie.get("expires") or 0), label)
        except (OSError, ValueError):
            pass
    now = time.time()
    for entry in sites.values():
        if entry.expires and entry.expires < now:
            entry.login = False  # only expired logins left
    return sorted(sites.values(), key=lambda s: (not s.login, s.site))
