"""Each npia work's full-size cover: the original upload, kept beside the catalog.

The catalog rows hold the site's small card cover, so a page of cards stays a
few kilobytes each. The original upload is another file on the image host (up
to several megabytes, thousands of pixels a side) and cannot be derived from
the card cover's address, so it is recorded here for the NovelDB site's
Details view and its image download.

docs/data/npia_cover_originals_<NN>.json.gz, NN = id % 64:
    {"version": 1, "covers": {novel id: "/imagebox/..."}}
The path is on the image host the card covers use. The site reads only the
shard of the work whose Details are open.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

try:
    from . import source_names
    from .metadata_common import atomic_bytes
except ImportError:
    import source_names
    from metadata_common import atomic_bytes

SHARDS = 64
PREFIX = "npia_cover_originals_"
PLACEHOLDER_PARTS = ("readycover", "adult_cover_img")
IMAGE_PATH = re.compile(r"/imagebox/[A-Za-z0-9_./-]+\Z")


def shard_name(novel_id):
    return f"{PREFIX}{int(novel_id) % SHARDS:02d}.json.gz"


def image_path(value):
    """The image host path of a cover address in any form the API gives it, or ""."""
    text = str(value or "").strip()
    if not text or text in ("None", "null") or any(part in text for part in PLACEHOLDER_PARTS):
        return ""
    path = urlsplit("https:" + text if text.startswith("//") else text).path
    return path if IMAGE_PATH.fullmatch(path) and ".." not in path else ""


def image_key(path):
    """One stored image whatever resource of it is asked for (.wimg thumbnail, .file original)."""
    return re.sub(r"\.(?:wimg|file)\Z", "", path)


def original_path(item, card_cover):
    """The original upload's path when the API item names one that is not the card cover's image."""
    path = image_path(item.get("novel_img_all"))
    return path if path and image_key(path) != image_key(image_path(card_cover)) else ""


class Store:
    """The recorded originals of a data folder; only shards that changed are rewritten."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.shards = {}
        self.changed = set()

    def _shard(self, novel_id):
        name = shard_name(novel_id)
        if name not in self.shards:
            path, covers = self.data_dir / name, {}
            if path.exists():
                data = json.loads(gzip.decompress(path.read_bytes()))
                if data.get("version") != 1 or not isinstance(data.get("covers"), dict):
                    raise ValueError(f"Invalid cover originals shard {name}")
                covers = data["covers"]
            self.shards[name] = covers
        return name, self.shards[name]

    def get(self, novel_id):
        return self._shard(novel_id)[1].get(str(novel_id), "")

    def set(self, novel_id, path):
        """Record a work's original (a path on the image host), or forget it with ""."""
        try:
            name, covers = self._shard(novel_id)
        except (TypeError, ValueError):
            return False
        key = str(novel_id)
        if path and not IMAGE_PATH.fullmatch(path):
            return False
        if covers.get(key, "") == path:
            return False
        if path:
            covers[key] = path
        else:
            covers.pop(key, None)
        self.changed.add(name)
        return True

    def observe(self, item, card_cover):
        """Take an API item's original upload. An item without one leaves what is recorded."""
        novel_id = item.get("novel_no")
        path = original_path(item, card_cover)
        return bool(path) and novel_id is not None and self.set(novel_id, path)

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(self.changed):
            covers = dict(sorted(self.shards[name].items(), key=lambda entry: int(entry[0])))
            raw = source_names.dumps({"version": 1, "covers": covers}, ensure_ascii=False, separators=(",", ":"))
            atomic_bytes(self.data_dir / name, gzip.compress(raw.encode("utf-8"), compresslevel=9, mtime=0))
        written, self.changed = len(self.changed), set()
        return written
