"""Which account each Enter Browser profile is signed in with, as last seen by a download.

A profile is only a number ("Profile #3"); the account inside it is what tells profiles apart.
A kpage download asks the site who is signed in and notes it here, so the Download page can show
"Profile #3 (name@example.com)". Kept in the app's data folder beside the browser profiles; it
holds no password or cookie, only the name the site shows for the account.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Dict, Optional

_APP_DATA_NAME = "NpiaDownloader"
_FILE = "profile_accounts.json"
_lock = threading.Lock()


def folder() -> str:
    """The app's per-user data folder (the one `external_scraper` keeps browser profiles in)."""
    override = os.environ.get("NPIA_BROWSER_DATA_DIR")
    if override:
        return override
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), _APP_DATA_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", _APP_DATA_NAME)
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), _APP_DATA_NAME)


def _path(base: Optional[str] = None) -> str:
    return os.path.join(base or folder(), _FILE)


def load(base: Optional[str] = None) -> Dict[int, str]:
    """{profile number: account name}; empty when nothing was seen yet."""
    try:
        with open(_path(base), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    found = {}
    for key, value in (data.items() if isinstance(data, dict) else ()):
        try:
            if value:
                found[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return found


def remember(number: int, account: str, base: Optional[str] = None) -> bool:
    """Note `account` for profile `number` ("" when it is signed out). True when that changed it."""
    with _lock:
        known = load(base)
        number = int(number)
        account = (account or "").strip()
        if known.get(number, "") == account:
            return False
        if account:
            known[number] = account
        else:
            known.pop(number, None)
        try:
            os.makedirs(base or folder(), exist_ok=True)
            with open(_path(base), "w", encoding="utf-8") as handle:
                json.dump({str(key): value for key, value in sorted(known.items())}, handle, ensure_ascii=False)
        except OSError:
            return False
        return True


def label(number: int, accounts: Optional[Dict[int, str]] = None) -> str:
    """"Profile #3 (name@example.com)", or "Profile #3" when its account is not known."""
    account = (accounts if accounts is not None else load()).get(int(number), "")
    return f"Profile #{int(number)} ({account})" if account else f"Profile #{int(number)}"
