"""Install the optional, versioned catalogue independently of application packages."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
from urllib.request import urlopen
from zipfile import ZipFile

from noveldb.core import paths

BASE = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
MANIFEST = "noveldb-local.json"
_lock = threading.Lock()
_state = {"busy": False, "progress": 0, "error": ""}


def root():
    return paths.data_dir() / "catalogue-data"


def installed():
    try:
        return json.loads((root() / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def status():
    saved = installed()
    with _lock:
        return {**_state, "version": saved.get("revision", ""), "built": saved.get("built", ""),
                "books": saved.get("books", 0)}


def folder(version=None):
    revision = version or installed().get("revision", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", revision):
        raise FileNotFoundError("Download catalogue data in catalogue settings first")
    return root() / revision


def _download():
    try:
        with urlopen(BASE + MANIFEST, timeout=60) as response:
            manifest = json.load(response)
        revision = manifest["revision"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", revision) or len(manifest["files"]) != 2:
            raise ValueError("Invalid catalogue manifest")
        previous = installed().get("revision")
        if previous == revision:
            return
        target = folder(revision)
        target.mkdir(parents=True, exist_ok=True)
        for part, entry in enumerate(manifest["files"]):
            name = entry["name"]
            if not re.fullmatch(r"noveldb-local-[A-Za-z0-9_-]+-[01]\.zip", name):
                raise ValueError("Invalid catalogue file")
            pending = target / f"part-{part}.tmp"
            digest = hashlib.sha256()
            size = 0
            with urlopen(BASE + name, timeout=60) as response, pending.open("wb") as output:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(block)
                    digest.update(block)
                    size += len(block)
                    with _lock:
                        _state["progress"] = (part + min(1, size / max(1, entry["size"]))) / 2
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ValueError("Catalogue download failed its checksum")
            with ZipFile(pending) as archive:
                if archive.testzip() is not None:
                    raise ValueError("Catalogue archive is damaged")
            pending.replace(target / f"instant-catalog-{part}.zip")
        pending = root() / "installed.tmp"
        pending.write_text(json.dumps(manifest), encoding="utf-8")
        pending.replace(root() / "installed.json")
        from .instant import archive
        archive.cache_clear()
        for old in root().iterdir():
            if old.is_dir() and old.name not in (revision, previous) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", old.name):
                shutil.rmtree(old, ignore_errors=True)
    except Exception as error:
        with _lock:
            _state["error"] = str(error)
    finally:
        with _lock:
            _state["busy"] = False


def handle(action):
    if action == "download":
        with _lock:
            if not _state["busy"]:
                _state.update(busy=True, progress=0, error="")
                threading.Thread(target=_download, daemon=True, name="catalogue-data").start()
    elif action == "remove":
        with _lock:
            if not _state["busy"]:
                from .instant import archive
                archive.cache_clear()
                shutil.rmtree(root(), ignore_errors=True)
                _state.update(progress=0, error="")
    return status()
