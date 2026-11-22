"""Saved sign-ins: the account and password a browser profile signs in to a site with.

A site ends a sign-in sooner or later. With an account saved here, a download that finds its
profile signed out signs in again by itself instead of failing until someone opens Enter Browser.

The password never sits in a file as text. It is handed to the system's own protection: Windows
encrypts it for the signed-in Windows user (DPAPI), macOS keeps it in the Keychain and Linux in
the desktop keyring (`secret-tool`). Where none of these is there, nothing is saved. The file
beside the browser profiles holds the account name and the protected password only.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Dict, Optional, Tuple

from noveldb.core.external import profile_accounts

_FILE = "saved_logins.json"
_SERVICE = "NovelDB sign-in"
_lock = threading.Lock()


def _path(base: Optional[str] = None) -> str:
    return os.path.join(base or profile_accounts.folder(), _FILE)


def available() -> bool:
    """True when this system can keep a password protected."""
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        return shutil.which("security") is not None
    return shutil.which("secret-tool") is not None


def _run(arguments, text: Optional[str] = None) -> Optional[str]:
    try:
        done = subprocess.run(arguments, input=text, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _dpapi(data: bytes, protect: bool) -> Optional[bytes]:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    call = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        return None
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def _protect(key: str, password: str) -> Optional[str]:
    """What the file keeps for `password`, or None when it could not be protected."""
    if sys.platform == "win32":
        try:
            sealed = _dpapi(password.encode("utf-8"), True)
        except (OSError, AttributeError, ValueError):
            sealed = None
        return "dpapi:" + base64.b64encode(sealed).decode("ascii") if sealed else None
    if sys.platform == "darwin":
        stored = _run(["security", "add-generic-password", "-U", "-s", _SERVICE, "-a", key, "-w", password])
        return "keychain:" + key if stored is not None else None
    stored = _run(["secret-tool", "store", "--label", f"{_SERVICE} ({key})", "service", _SERVICE, "account", key],
                  password)
    return "keyring:" + key if stored is not None else None


def _reveal(token: str) -> str:
    kind, _, value = (token or "").partition(":")
    if kind == "dpapi" and sys.platform == "win32":
        try:
            opened = _dpapi(base64.b64decode(value), False)
        except (OSError, AttributeError, ValueError):
            opened = None
        return opened.decode("utf-8", "replace") if opened else ""
    if kind == "keychain":
        return (_run(["security", "find-generic-password", "-s", _SERVICE, "-a", value, "-w"]) or "").rstrip("\n")
    if kind == "keyring":
        return (_run(["secret-tool", "lookup", "service", _SERVICE, "account", value]) or "").rstrip("\n")
    return ""


def _forget(token: str) -> None:
    kind, _, value = (token or "").partition(":")
    if kind == "keychain":
        _run(["security", "delete-generic-password", "-s", _SERVICE, "-a", value])
    elif kind == "keyring":
        _run(["secret-tool", "clear", "service", _SERVICE, "account", value])


def _load(base: Optional[str] = None) -> dict:
    try:
        with open(_path(base), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict, base: Optional[str] = None) -> bool:
    try:
        os.makedirs(base or profile_accounts.folder(), exist_ok=True)
        with open(_path(base) + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(_path(base) + ".tmp", _path(base))
    except OSError:
        return False
    return True


def accounts(site: str, base: Optional[str] = None) -> Dict[int, str]:
    """{profile number: account name} of the sign-ins saved for `site`. No password is read."""
    found = {}
    for number, entry in (_load(base).get(site) or {}).items():
        try:
            if isinstance(entry, dict) and entry.get("user") and entry.get("secret"):
                found[int(number)] = str(entry["user"])
        except (TypeError, ValueError):
            continue
    return found


def save(site: str, profile: int, user: str, password: str, base: Optional[str] = None) -> bool:
    """Keep `user` and `password` for `profile` on `site`. False when they could not be protected."""
    user = (user or "").strip()
    if not (user and password and available()):
        return False
    with _lock:
        secret = _protect(f"{site}:{int(profile)}", password)
        if not secret:
            return False
        data = _load(base)
        data.setdefault(site, {})[str(int(profile))] = {"user": user, "secret": secret}
        return _write(data, base)


def remove(site: str, profile: int, base: Optional[str] = None) -> bool:
    """Forget the sign-in saved for `profile` on `site`. True when there was one."""
    with _lock:
        data = _load(base)
        entry = (data.get(site) or {}).pop(str(int(profile)), None)
        if entry is None:
            return False
        _forget(str(entry.get("secret") or "") if isinstance(entry, dict) else "")
        if not data[site]:
            data.pop(site)
        return _write(data, base)


def profile_deleted(profile: int, base: Optional[str] = None) -> None:
    """Profile `profile` was deleted and the ones after it moved up one number: its saved
    sign-in is forgotten and the later ones follow their profiles."""
    profile = int(profile)
    with _lock:
        data = _load(base)
        if not data:
            return
        for site in list(data):
            entries = data[site] if isinstance(data[site], dict) else {}
            kept = {}
            for key in sorted(entries, key=lambda text: int(text) if str(text).isdigit() else 0):
                entry = entries[key]
                token = str(entry.get("secret") or "") if isinstance(entry, dict) else ""
                number = int(key) if str(key).isdigit() else 0
                if number == profile:
                    _forget(token)
                    continue
                if number > profile:
                    if not token.startswith("dpapi:"):
                        # The keychain keeps the password under the profile's number: move it.
                        password = _reveal(token)
                        _forget(token)
                        token = (_protect(f"{site}:{number - 1}", password) if password else None) or ""
                        if not token:
                            continue
                        entry = dict(entry, secret=token)
                    key = str(number - 1)
                kept[key] = entry
            if kept:
                data[site] = kept
            else:
                data.pop(site)
        _write(data, base)


def get(site: str, profile: int, base: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """`(account, password)` saved for `profile` on `site`, or None."""
    entry = (_load(base).get(site) or {}).get(str(int(profile)))
    if not isinstance(entry, dict) or not entry.get("user"):
        return None
    password = _reveal(str(entry.get("secret") or ""))
    return (str(entry["user"]), password) if password else None
