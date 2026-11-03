"""NovelDB's own books in the Catalog's generic search, read from its search index.

The index (built daily by scripts/noveldb_index/build.cjs from the NovelDB site's data, published as
assets of the `engines` release, next to the engines' bundles) holds every book of the site, 1.1 million, with its metadata:
title and English title, author, cover, tags, views, likes, chapters, status and its page. A search
reads the index's small table of contents once a day, then only the pieces it needs with HTTP Range
requests: one word bucket per query word, then the card blocks of the books found. Each word's books
are listed per source, every one of them, in pages: a search shows the first page of results (the
lead source, npia, first, then each source in turn) and reads further pages as the results are
scrolled (`search_page`). A book's synopsis
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
PIECES = 512  # word buckets, list pages and card blocks kept in memory
FORMAT = 2
LEAD = "npia"  # its books come first in each round of results
MULTI_PAGES = 40  # list pages of each word read to match a search of several words (per source)
UNION_PAGES = 10  # list pages of each alias of a tag read to merge them (per source)

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


_BRACKETED = re.compile(r"[(\[{（【][^)\]}）】]*[)\]}）】]")


def tag_concept(tag: str) -> str:
    """A tag's alias key (the builder's tagConcept): its letters and digits without a bracketed note
    and an ending -ing, -er or -s. "Gender Bender" and "Genderbend (TS)" share "=genderbend"."""
    def letters(value: str) -> str:
        return "".join(ch for ch in value if unicodedata.category(ch)[0] in "LN")

    text = normal(tag)
    key = letters(_BRACKETED.sub(" ", text)) or letters(text)
    if len(key) > 5 and not _CJK.search(key):
        key = re.sub(r"(ing|er|s)$", "", key)
    return "=" + key if key else ""


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
                if found.get("format") == FORMAT:
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
        if found.get("format") != FORMAT:
            return None  # an index for another version of the app
        if cached is not None:
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(found), encoding="utf-8")
            except OSError:
                pass
        return self._keep(found)

    def _forget(self) -> None:
        with self._lock:
            self._manifest = None
            self._pieces.clear()
        if self.cache_dir is not None:
            try:
                (self.cache_dir / MANIFEST).unlink()
            except OSError:
                pass

    def _keep(self, found: dict) -> dict:
        found["_read"] = time.time()
        with self._lock:
            self._manifest = found
            self._pieces.clear()
        return found

    def _piece(self, kind: str, index: int, retry: bool = True, at: Optional[list] = None):
        """A word bucket or card block by its number, or a list page (`at`: its [offset, length])."""
        manifest = self.manifest()
        if manifest is None:
            return None
        key = (manifest.get("built"), kind, index)
        with self._lock:
            if key in self._pieces:
                self._pieces.move_to_end(key)
                return self._pieces[key]
        offset, length = at or manifest["bucketAt" if kind == "bucket" else "blockAt"][index]
        result = self.fetch(self.base + manifest["bin"], binary=True,
                            headers={"Range": f"bytes={offset}-{offset + length - 1}"})
        data = (result or {}).get("content") or b""
        if result and result.get("status") == 404 and retry:
            # A table of contents kept from an earlier build points at data a newer build replaced.
            self._forget()
            return None if at else self._piece(kind, index, retry=False)
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

    def _lists(self, token: str) -> Dict[str, "_List"]:
        """A token's book list in each source that has it."""
        manifest = self.manifest()
        if manifest is None:
            return {}
        bucket = self._piece("bucket", bucket_of(token, manifest["buckets"])) or {}
        entry = bucket.get(token) or {}
        if not isinstance(entry, dict):
            return {}
        return {source: _List(self, _undelta(first), entry.get("+" + source), manifest.get("page") or 500)
                for source, first in entry.items() if not source.startswith("+") and first}

    def _tag_lists(self, tag: str) -> Dict[str, "_List"]:
        """A tag's books in each source: its own token's and those of the tags sharing its alias key
        ("Genderbend (TS)" for "Gender Bender"), merged."""
        manifest = self.manifest()
        if manifest is None:
            return {}
        wanted = [tag_token(tag)]
        key = tag_concept(tag)
        if key:
            aliases = (self._piece("bucket", bucket_of(key, manifest["buckets"])) or {}).get(key)
            if isinstance(aliases, list):
                wanted += [token for token in aliases if isinstance(token, str) and token not in wanted]
        found = [lists for lists in (self._lists(token) for token in wanted) if lists]
        out = {}
        for source in set().union(*found) if found else ():
            lists, every = [], [lists[source] for lists in found if source in lists]
            for lst in every:  # a tag and its translation list the same books: read them once
                if not any(lst.total == other.total and lst.head == other.head for other in lists):
                    lists.append(lst)
            if len(lists) == 1:
                out[source] = lists[0]
                continue
            read = [lst.numbers(0, UNION_PAGES * lst.page) for lst in lists]
            # A list read only in part says nothing of the books after its last one read: the merge stops
            # there, and the longest list's own books follow.
            cutoff = min((nums[-1] for nums, lst in zip(read, lists) if nums and len(nums) < lst.total), default=None)
            numbers = sorted({n for nums in read for n in nums if cutoff is None or n <= cutoff})
            if cutoff is None:
                out[source] = _List.of(numbers)
            else:
                longest = max(range(len(lists)), key=lambda i: lists[i].total)
                out[source] = _Chain(numbers, lists[longest], sum(1 for n in read[longest] if n <= cutoff))
        return out

    def _streams(self, wanted: List[str]) -> Dict[str, "_List"]:
        """Each source's books with every token: a token's own list, or for several the books in all
        of them (among the first MULTI_PAGES pages of each)."""
        per_token = [self._lists(token) for token in wanted]
        if not per_token or not all(per_token):
            return {}
        if len(per_token) == 1:
            return per_token[0]
        out = {}
        for source in set.intersection(*(set(lists) for lists in per_token)):
            lists = [lists[source] for lists in per_token]
            read = [lst.numbers(0, MULTI_PAGES * lst.page) for lst in lists]
            # A list read only in part says nothing of the books after its last one read.
            cutoff = min((nums[-1] for nums, lst in zip(read, lists) if nums and len(nums) < lst.total), default=None)
            found = set(read[0]).intersection(*read[1:])
            numbers = sorted(n for n in found if cutoff is None or n <= cutoff)
            if numbers:
                out[source] = _List.of(numbers)
        return out

    # -- searching -------------------------------------------------------------------------------

    def search(self, text: str, mode: str = "name", limit: int = 40) -> List[dict]:
        """The first page of cards (the generic search's shape) for a title or tag search."""
        return self.search_page(text, mode, 0, limit)[0]

    def search_page(self, text: str, mode: str = "name", page: int = 0, limit: int = 40,
                    sources: Optional[List[str]] = None) -> tuple:
        """(cards, more): page `page` of the results, `limit` cards each. Each round takes the next
        most read book of every source, the lead source's first; `more` says whether pages follow."""
        wanted = [tag_token(text)] if mode == "tag" else tokens(text)
        if not wanted or self.manifest() is None:
            return [], False
        streams = self._tag_lists(text) if mode == "tag" else self._streams(wanted)
        if sources:
            streams = {name: stream for name, stream in streams.items() if name in sources}
        order = sorted(streams, key=lambda name: (name != LEAD, streams[name].first))
        picks, more = _round_robin([(name, streams[name].total) for name in order], page * limit, limit)
        numbers = [number for number in (streams[name].number(at) for name, at in picks) if number is not None]
        return [card for card in (self._card(number) for number in numbers) if card], more

    def _card(self, number: int) -> Optional[dict]:
        manifest = self.manifest()
        block = self._piece("block", number // manifest["block"])
        if not block:
            return None
        row = block[number % manifest["block"]]
        source, book_id, title, title_en, author, cover, tags, views, likes, chapters, complete, url, updated = row[:13]
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


def _undelta(values) -> List[int]:
    """A list written as differences from the number before (the first as it is)."""
    out, total = [], 0
    for value in values or []:
        total += int(value)
        out.append(total)
    return out


def _round_robin(totals: List[tuple], offset: int, count: int) -> tuple:
    """([(name, position), ...], more): places `offset` to `offset + count` of the merged order that
    takes position 0 of each list in turn, then position 1, and so on (a list that runs out drops out)."""
    out, place, round_ = [], 0, 0
    longest = max((total for _name, total in totals), default=0)
    while round_ < longest and len(out) < count:
        active = [name for name, total in totals if total > round_]
        if place + len(active) <= offset:
            place += len(active)
        else:
            for name in active:
                if place >= offset and len(out) < count:
                    out.append((name, round_))
                place += 1
        round_ += 1
    return out, offset + len(out) < sum(total for _name, total in totals)


class _List:
    """One source's books for a token: the first page from the bucket, the rest read as needed."""

    def __init__(self, index: Optional[NovelDBIndex], first: List[int], more: Optional[list], page: int):
        self.index = index
        self.head = first
        self.page = page
        self.total = int(more[0]) if more else len(first)
        self.first_page = int(more[1]) if more else 0
        self.pages_at = list(more[2]) if more else []

    @classmethod
    def of(cls, numbers: List[int]) -> "_List":
        return cls(None, numbers, None, max(1, len(numbers)))

    @property
    def first(self) -> int:
        return self.head[0] if self.head else 1 << 40

    def _page(self, number: int) -> List[int]:
        if number == 0:
            return self.head
        if number > len(self.pages_at) or self.index is None:
            return []
        return _undelta(self.index._piece("page", self.first_page + number - 1, at=self.pages_at[number - 1]) or [])

    def number(self, at: int) -> Optional[int]:
        page = self._page(at // self.page)
        return page[at % self.page] if at % self.page < len(page) else None

    def numbers(self, start: int, end: int) -> List[int]:
        """Positions `start` to `end` of the list (reading the pages they are on)."""
        end = min(end, self.total)
        if end <= start:
            return []
        out: List[int] = []
        for number in range(start // self.page, (end - 1) // self.page + 1):
            out.extend(self._page(number))
        skip = start - (start // self.page) * self.page
        return out[skip:skip + end - start]


class _Chain:
    """Merged lists of a tag: their books merged up to where every list was read (`head`), then the
    longest list's own books after its first `skip`."""

    def __init__(self, head: List[int], rest: _List, skip: int):
        self.head, self.rest, self.skip = head, rest, skip
        self.page = rest.page
        self.total = len(head) + max(0, rest.total - skip)

    @property
    def first(self) -> int:
        return self.head[0] if self.head else self.rest.first

    def number(self, at: int) -> Optional[int]:
        if at < len(self.head):
            return self.head[at]
        return self.rest.number(self.skip + at - len(self.head)) if at < self.total else None

    def numbers(self, start: int, end: int) -> List[int]:
        end = min(end, self.total)
        out = self.head[start:min(end, len(self.head))]
        if end > len(self.head):
            out += self.rest.numbers(self.skip + max(0, start - len(self.head)), self.skip + end - len(self.head))
        return out


def _count(value) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return ""
    return f"{number:,}" if number > 0 else ""
