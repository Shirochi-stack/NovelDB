"""The rule-engine bundles: the copies shipped with the app, and opt-in updates.

Two engines run site rules inside the browser page: novel-downloader (`rules-lib.js`, built by
scripts/update_nd_rules.py) and WebToEpub (`webtoepub/webtoepub-lib.js`, built by
scripts/update_webtoepub.py). Both ship with the app. When the user turns on updates for an
engine, newer builds are downloaded from this repository's `engines` release, which a daily
workflow refreshes from the official upstream repositories after checking that they load.

An update is used only when its API matches this app, its checksum matches its manifest and it is
newer than the shipped copy. If it then fails to load in a page, it is set aside and the shipped
copy is used again; that build is never downloaded again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from noveldb.core import paths
from noveldb.core.net_timeouts import request_timeout
from noveldb.core.userscript_patterns import UserScriptPatterns, is_home_page

RELEASE_URL = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
CHECK_EVERY = 24 * 3600  # seconds between automatic checks
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Engine:
    name: str              # the settings name (model.ENGINE_ND / ENGINE_W2E)
    title: str
    api: int               # the window.* contract this app's bridge speaks
    lib: str               # the script file
    folder: str            # where the shipped copy sits, relative to the app's bundle folder
    shipped_manifest: str  # "" when the version comes from the script's own header
    release_manifest: str  # the manifest asset in the release


NOVEL_DOWNLOADER = Engine("novel_downloader", "novel-downloader rules", 1, "rules-lib.js", "", "",
                          "novel-downloader-manifest.json")
WEBTOEPUB = Engine("webtoepub", "WebToEpub parsers", 1, "webtoepub-lib.js", "webtoepub", MANIFEST,
                   "webtoepub-manifest.json")
ENGINES = (NOVEL_DOWNLOADER, WEBTOEPUB)
BY_NAME = {engine.name: engine for engine in ENGINES}

_lock = threading.Lock()


def _version(value) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", str(value or "")))


@dataclass(frozen=True)
class Bundle:
    engine: Engine
    folder: Path
    manifest: dict
    updated: bool  # downloaded, not the copy shipped with the app

    @property
    def path(self) -> Path:
        return self.folder / self.engine.lib

    @property
    def key(self) -> tuple:
        """Newer bundles sort later: upstream version, then commit date, then build time."""
        m = self.manifest
        return _version(m.get("version")), str(m.get("date", "")), str(m.get("built", ""))

    @property
    def label(self) -> str:
        m = self.manifest
        parts = []
        if m.get("version") and self.engine is NOVEL_DOWNLOADER:
            parts.append(str(m["version"]))
        if m.get("date"):
            parts.append(str(m["date"])[:10])
        if m.get("commit"):
            parts.append(str(m["commit"])[:7])
        parts.append("downloaded update" if self.updated else "built in")
        return ", ".join(parts)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def knows(self, url: str) -> bool:
        """The engine lists the URL's site (page-based WebToEpub matches need the page itself)."""
        parts = urlsplit(url)
        try:
            index = _site_index(self.engine.name, str(self.path), self.path.stat().st_mtime)
        except OSError:
            return False
        hosts, patterns = index
        if self.engine is WEBTOEPUB:
            return _strip_www((parts.hostname or "").lower()) in hosts
        host = (parts.netloc or "").lower()  # novel-downloader matches location.host exactly
        return host in hosts or any(p.search(host) for p in patterns)

    def lists_page(self, url: str) -> bool:
        """The engine lists this page, not just its site.

        The novel-downloader rules name their pages in the script's header (`@match` and `@include`
        patterns that no `@exclude` refuses); WebToEpub lists only hosts, so any page of a listed site
        counts except the site's home page.
        """
        if self.engine is WEBTOEPUB:
            return self.knows(url) and not is_home_page(url)
        try:
            patterns = _page_patterns(str(self.path), self.path.stat().st_mtime)
        except OSError:
            return False
        return patterns.accepts(url)


@lru_cache(maxsize=8)
def _page_patterns(path: str, _mtime: float) -> UserScriptPatterns:
    return UserScriptPatterns.parse(Path(path).read_text(encoding="utf-8"))


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


@lru_cache(maxsize=8)
def _site_index(engine: str, path: str, _mtime: float) -> tuple[frozenset, tuple]:
    text = Path(path).read_text(encoding="utf-8")
    if engine == WEBTOEPUB.name:
        hosts = re.findall(r'parserFactory\.register\("([^"]+)"', text)
        return frozenset(_strip_www(h.lower()) for h in hosts), ()
    # novel-downloader's getRule() is one switch over location.host, then a few host patterns.
    end = text.find('throw new Error("Not Found Rule!")')
    start = text.rfind("switch(", 0, end)
    if end < 0 or start < 0:
        return frozenset(), ()
    region = text[start:end]
    hosts = set()
    for raw in re.findall(r'case"([^"]+)":', region):
        try:
            hosts.add(json.loads(f'"{raw}"').lower())
        except ValueError:
            hosts.add(raw.lower())
    patterns = []
    for raw in re.findall(r"if\((/.+?/)\.test\(e\)", region):
        try:
            patterns.append(re.compile(raw[1:-1]))
        except re.error:
            pass
    return frozenset(hosts), tuple(patterns)


def shipped_dir(engine: Engine) -> Path:
    return paths.bundle_dir() / engine.folder if engine.folder else paths.bundle_dir()


def updates_dir(engine: Engine) -> Path:
    return paths.data_dir() / "engines" / engine.name


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _header_version(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            head = stream.read(4096)
    except OSError:
        return ""
    match = re.search(r"@version\s+(\S+)", head)
    return match.group(1) if match else ""


def shipped(engine: Engine) -> Optional[Bundle]:
    folder = shipped_dir(engine)
    if not (folder / engine.lib).is_file():
        return None
    if engine.shipped_manifest:
        manifest = _read_json(folder / engine.shipped_manifest)
        if not manifest or manifest.get("api") != engine.api:
            return None
    else:
        manifest = {"api": engine.api, "version": _header_version(folder / engine.lib)}
    return Bundle(engine, folder, manifest, False)


def downloaded(engine: Engine) -> Optional[Bundle]:
    folder = updates_dir(engine)
    manifest = _read_json(folder / MANIFEST)
    if not manifest or manifest.get("api") != engine.api or not (folder / engine.lib).is_file():
        return None
    try:
        if hashlib.sha256((folder / engine.lib).read_bytes()).hexdigest() != manifest.get("sha256"):
            return None
    except OSError:
        return None
    return Bundle(engine, folder, manifest, True)


def current(engine: Engine) -> Optional[Bundle]:
    """The bundle to inject: a valid download when it is newer than the shipped copy."""
    base = shipped(engine)
    update = downloaded(engine)
    if update is not None and (base is None or update.key > base.key):
        return update
    return base


def _release_key(manifest: dict) -> str:
    return f"{manifest.get('commit', '')}-{manifest.get('shim', '')}"


def _state(engine: Engine) -> dict:
    return _read_json(updates_dir(engine) / "state.json") or {}


def _save_state(engine: Engine, state: dict) -> None:
    folder = updates_dir(engine)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, folder / "state.json")


def reject(bundle: Bundle, reason: str = "") -> None:
    """Set a downloaded bundle aside (it failed to load) so it is not used or fetched again."""
    if not bundle.updated:
        return
    with _lock:
        state = _state(bundle.engine)
        state["rejected"] = sorted(set(state.get("rejected", [])) | {_release_key(bundle.manifest)})
        state["rejected_reason"] = reason[:300]
        _save_state(bundle.engine, state)
        for name in (bundle.engine.lib, MANIFEST):
            try:
                (bundle.folder / name).unlink()
            except OSError:
                pass


def last_checked(engine: Engine) -> float:
    try:
        return float(_state(engine).get("checked", 0))
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class UpdateResult:
    engine: Engine
    status: str  # "updated", "current", "failed"
    message: str
    bundle: Optional[Bundle] = None


def _download(url: str, timeout: float) -> bytes:
    import requests

    response = requests.get(url, timeout=request_timeout(timeout), headers={"User-Agent": "NovelDB"})
    response.raise_for_status()
    return response.content


def check_for_update(engine: Engine, fetch: Callable[[str, float], bytes] = _download,
                     timeout: float = 30.0) -> UpdateResult:
    """Download the release's build of `engine` when it is newer than the one in use."""
    title = engine.title
    with _lock:
        state = _state(engine)
        state["checked"] = time.time()

        def done(status: str, message: str, bundle: Optional[Bundle] = None) -> UpdateResult:
            _save_state(engine, state)
            return UpdateResult(engine, status, message, bundle)

        try:
            remote = json.loads(fetch(RELEASE_URL + engine.release_manifest, timeout))
            if not isinstance(remote, dict):
                raise ValueError("the manifest is not an object")
        except Exception as e:  # network, HTTP or JSON errors
            return done("failed", f"Could not check for {title} updates: {e}")
        in_use = current(engine)
        if remote.get("engine", engine.name) != engine.name:
            return done("failed", f"The {title} update manifest is for another engine.")
        if remote.get("api") != engine.api:
            return done("current", f"The newest {title} need a newer NovelDB.", in_use)
        if _release_key(remote) in state.get("rejected", []):
            return done("current", f"The newest {title} failed to load before; keeping these.", in_use)
        if in_use is not None and in_use.manifest.get("commit") and _release_key(remote) == _release_key(in_use.manifest):
            # The same upstream commit and build patch: a rebuild of what is in use.
            return done("current", f"The {title} are up to date ({in_use.label}).", in_use)
        remote_key = (_version(remote.get("version")), str(remote.get("date", "")), str(remote.get("built", "")))
        if in_use is not None and remote_key <= in_use.key:
            return done("current", f"The {title} are up to date ({in_use.label}).", in_use)
        name = str(remote.get("file") or "")
        if not name or "/" in name or "\\" in name or ".." in name:
            return done("failed", f"The {title} update manifest names no valid file.")
        try:
            data = fetch(RELEASE_URL + name, max(timeout, 60.0))
        except Exception as e:
            return done("failed", f"Could not download the {title} update: {e}")
        if hashlib.sha256(data).hexdigest() != remote.get("sha256") or len(data) != remote.get("size"):
            return done("failed", f"The downloaded {title} update did not match its checksum.")
        folder = updates_dir(engine)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (engine.lib + ".tmp")).write_bytes(data)
        os.replace(folder / (engine.lib + ".tmp"), folder / engine.lib)
        manifest = dict(remote, file=engine.lib)
        (folder / (MANIFEST + ".tmp")).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(folder / (MANIFEST + ".tmp"), folder / MANIFEST)
        bundle = Bundle(engine, folder, manifest, True)
        return done("updated", f"Updated the {title} to {bundle.label}.", bundle)


def check_if_due(engine: Engine, log: Callable[[str], None] = lambda _m: None,
                 every: float = CHECK_EVERY) -> Optional[UpdateResult]:
    """The automatic check: at most once per `every` seconds. Logs only news and failures."""
    if time.time() - last_checked(engine) < every:
        return None
    result = check_for_update(engine)
    if result.status != "current":
        log(result.message)
    return result
