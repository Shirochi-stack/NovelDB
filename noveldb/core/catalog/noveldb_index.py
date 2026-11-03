"""NovelDB's own books in the Catalog's generic search, read from its search index.

The index (built daily by scripts/noveldb_index/build.cjs from the NovelDB site's data, published as
assets of the `engines` release, next to the engines' bundles) holds every book of the site, 1.1 million, with its metadata:
title and English title, author, cover, tags, views, likes, chapters, status and its page. A search
reads the index's small table of contents once a day, then only the pieces it needs with HTTP Range
requests: one word bucket per query word, then the card blocks of the books found. A book's synopsis
comes from the site's own synopsis shard for it, when its card is opened. Nothing reads the site's
whole catalog.

The tokens are the builder's (`tokens` there); keep the two (and scripts/generic/catalog-app.js) in step.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

INDEX_URL = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
PAGES_URL = "https://shirochi-stack.github.io/NovelDB/data/"
MANIFEST = "noveldb-search.json"
MANIFEST_HOURS = 24
PIECES = 256  # word buckets and card blocks kept in memory

_STOP = {"the", "a", "an", "of", "to", "in", "and", "is", "on", "at", "for", "with"}
_CJK = re.compile("[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]")
_WORD = re.compile(r"[^\W_]+")


def normal(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def tokens(text: str) -> List[str]:
    """A title's (or query's) words, and two-character pieces of Chinese, Japanese and Korean words."""
    out: Dict[str, None] = {}
    for word in _WORD.findall(normal(text)):
        if _CJK.search(word):
            if len(word) <= 2:
                out[word] = None
            for i in range(len(word) - 1):
                out[word[i:i + 2]] = None
        elif len(word) >= 2 and word not in _STOP:
            out[word] = None
    return list(out)


def tag_token(tag: str) -> str:
    return "#" + re.sub(r"[\s_\-]+", " ", normal(tag)).strip()


def bucket_of(token: str, buckets: int) -> int:
    value = 0x811C9DC5
    for byte in token.encode("utf-8"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value % buckets


def description_shard(book_id: str, count: int) -> int:
    """The site's synopsis shard of a book (docs/app.js descriptionShardIndex)."""
    text = str(book_id or "")
    if re.fullmatch(r"-?\d+", text):
        return abs(int(text)) % count
    value = 0
    for char in text:
        value = (value * 31 + ord(char)) & 0xFFFFFFFF
    return value % count


def _site(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class NovelDBIndex:
    """Searches the index. `fetch` is the generic search's: fetch(url, headers=, binary=) -> {status, content} or None."""

    def __init__(self, fetch: Callable, cache_dir: Optional[Path] = None, base: str = INDEX_URL):
        self.fetch = fetch
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.base = base
        self._manifest: Optional[dict] = None
        self._pieces: "OrderedDict[tuple, object]" = OrderedDict()
        self._lock = threading.Lock()

    # -- reading the index -----------------------------------------------------------------------

    def manifest(self) -> Optional[dict]:
        """The table of contents: kept for a day (in the cache folder too)."""
        with self._lock:
            if self._manifest and time.time() - self._manifest.get("_read", 0) < MANIFEST_HOURS * 3600:
                return self._manifest
        cached = self.cache_dir / MANIFEST if self.cache_dir else None
        if cached is not None and cached.is_file() and time.time() - cached.stat().st_mtime < MANIFEST_HOURS * 3600:
            try:
                found = json.loads(cached.read_text(encoding="utf-8"))
                return self._keep(found)
            except (OSError, ValueError):
                pass
        result = self.fetch(self.base + MANIFEST, binary=True)
        if not result or result.get("status", 0) >= 400 or not result.get("content"):
            return self._manifest
        try:
            found = json.loads(result["content"].decode("utf-8"))
        except ValueError:
            return self._manifest
        if found.get("format") != 1:
            return None  # an index for a newer app
        if cached is not None:
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(found), encoding="utf-8")
            except OSError:
                pass
        return self._keep(found)

    def _keep(self, found: dict) -> dict:
        found["_read"] = time.time()
        with self._lock:
            self._manifest = found
            self._pieces.clear()
        return found

    def _piece(self, kind: str, index: int):
        manifest = self.manifest()
        if manifest is None:
            return None
        key = (manifest.get("built"), kind, index)
        with self._lock:
            if key in self._pieces:
                self._pieces.move_to_end(key)
                return self._pieces[key]
        offset, length = manifest["bucketAt" if kind == "bucket" else "blockAt"][index]
        result = self.fetch(self.base + manifest["bin"], binary=True,
                            headers={"Range": f"bytes={offset}-{offset + length - 1}"})
        data = (result or {}).get("content") or b""
        # A server that ignores the range sends the whole file: not something to read here.
        if not result or result.get("status") not in (200, 206) or len(data) != length:
            return None
        try:
            value = json.loads(gzip.decompress(data).decode("utf-8"))
        except (OSError, ValueError):
            return None
        with self._lock:
            self._pieces[key] = value
            while len(self._pieces) > PIECES:
                self._pieces.popitem(last=False)
        return value

    def _postings(self, token: str) -> List[int]:
        manifest = self.manifest()
        if manifest is None:
            return []
        bucket = self._piece("bucket", bucket_of(token, manifest["buckets"])) or {}
        return list(bucket.get(token) or [])

    # -- searching -------------------------------------------------------------------------------

    def search(self, text: str, mode: str = "name", limit: int = 40) -> List[dict]:
        """Cards (the generic search's shape) for a title or tag search; the most read first."""
        wanted = [tag_token(text)] if mode == "tag" else tokens(text)
        if not wanted or self.manifest() is None:
            return []
        lists = [set(self._postings(token)) for token in wanted]
        if not all(lists):
            return []
        found = set.intersection(*lists)
        numbers = sorted(found)[:limit]
        return [card for card in (self._card(number) for number in numbers) if card]

    def _card(self, number: int) -> Optional[dict]:
        manifest = self.manifest()
        block = self._piece("block", number // manifest["block"])
        if not block:
            return None
        row = block[number % manifest["block"]]
        source, book_id, title, title_en, author, cover, tags, views, likes, chapters, complete, url, updated = row
        return {
            "url": url, "title": title_en or title, "originalTitle": title if title_en else "", "author": author or "",
            "cover": cover or "", "description": "", "tags": list(tags or []), "site": _site(url), "comic": False,
            "source": "noveldb", "noveldb": {"source": source, "id": book_id}, "complete": False,
            "views": _count(views), "likes": _count(likes), "chapters": _count(chapters),
            "status": {1: "Completed", 0: "Ongoing"}.get(complete, ""), "updated": str(updated or "")[:10],
        }

    def description(self, card: dict) -> str:
        """The book's synopsis from the site's synopsis shard for it (one small file), or ""."""
        ref = card.get("noveldb") or {}
        manifest = self.manifest() or {}
        info = (manifest.get("sources") or {}).get(ref.get("source"), {}).get("descriptions") or {}
        if not info.get("prefix") or not ref.get("id"):
            return ""
        shard = description_shard(ref["id"], int(info.get("count") or 128))
        result = self.fetch(f"{PAGES_URL}{info['prefix']}{shard:03d}.json.gz", binary=True)
        if not result or result.get("status", 0) >= 400 or not result.get("content"):
            return ""
        data = result["content"]
        try:
            text = (gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data).decode("utf-8")
            return str(json.loads(text).get(str(ref["id"])) or "").replace("\\n", "\n").strip()
        except (OSError, ValueError):
            return ""


def _count(value) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return ""
    return f"{number:,}" if number > 0 else ""
