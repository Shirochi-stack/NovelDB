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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

INDEX_URL = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
PAGES_URL = "https://shirochi-stack.github.io/NovelDB/data/"
MANIFEST = "noveldb-search.json"
MANIFEST_HOURS = 24
HEDGE_SECONDS = 2.0  # a piece's request is sent again after this long without an answer
# The pieces' requests share a few kept-open connections: each on a new connection, a burst of them lost
# connection attempts and waited 3, 7, 15 s for the retries (a search took 80 s); kept open, 0.1 s each.
RANGE_CONNECTIONS = 8
_HEDGES = ThreadPoolExecutor(RANGE_CONNECTIONS, thread_name_prefix="noveldb-range")
_session = None
_session_lock = threading.Lock()


def _plain_fetch():
    """The plain HTTP fetch the app hands the index (its requests go over the kept-open connections instead)."""
    from noveldb.core.catalog.generic_search import default_fetch

    return default_fetch


def _range_session():
    global _session
    with _session_lock:
        if _session is None:
            import requests
            from requests.adapters import HTTPAdapter

            _session = requests.Session()
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=RANGE_CONNECTIONS)
            _session.mount("https://", adapter)
            _session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
        return _session
SIGNED_SECONDS = 240  # how long a release file's signed address is used (GitHub's last five minutes)
PIECES = 512  # word buckets, list pages and card blocks kept in memory
FORMAT = 2
LEAD = "npia"  # its books come first in each round of results
MULTI_PAGES = 40  # list pages of each word read to match a search of several words (per source)
INTERSECT_LISTS = 4  # the shortest lists of a search's words intersected (per source)
UNION_PAGES = 1  # list pages of each alias of a tag merged (per source): the first, already in its bucket

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
        self._signed: Optional[tuple] = None  # (release address, its signed address, valid until)

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
        result = self._range(self.base + manifest["bin"], offset, length)
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

    def _range(self, url: str, offset: int, length: int) -> Optional[dict]:
        """Bytes `offset`..+`length` of the release file. The release address sends each request on to a
        signed one (a second round trip every time): that address is used directly while it is valid."""
        headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
        with self._lock:
            signed = self._signed if self._signed and self._signed[0] == url and time.time() < self._signed[2] else None
        if signed:
            result = self._hedged(signed[1], headers)
            if result and result.get("status") == 206 and len(result.get("content") or b"") == length:
                return result
            with self._lock:
                self._signed = None  # expired or refused: through the release address again
        result = self._hedged(url, headers)
        final = (result or {}).get("url") or ""
        if result and result.get("status") == 206 and final and final != url:
            with self._lock:
                self._signed = (url, final, time.time() + SIGNED_SECONDS)
        return result

    def _get(self, url: str, headers: dict) -> Optional[dict]:
        """A piece of the release file over the kept-open connections (the given fetch for anything else,
        or when that fails), in the fetch's result shape."""
        if self.fetch is not _plain_fetch():
            return self.fetch(url, binary=True, headers=headers)
        try:
            response = _range_session().get(url, headers=headers, timeout=20, allow_redirects=True)
        except Exception:
            return self.fetch(url, binary=True, headers=headers)
        return {"status": response.status_code, "url": response.url, "content": response.content,
                "text": "", "headers": "\n".join(f"{k}: {v}" for k, v in response.headers.items())}

    def _hedged(self, url: str, headers: dict) -> Optional[dict]:
        """One request, and a second alike if the first hasn't answered in HEDGE_SECONDS: now and then one
        stalls for seconds (the search waited 7 s for one piece); whichever answers first is taken."""
        first = _HEDGES.submit(self._get, url, headers)
        try:
            return first.result(timeout=HEDGE_SECONDS)
        except FutureTimeout:
            pass
        except Exception:
            return None
        second = _HEDGES.submit(self._get, url, headers)
        for future in as_completed([first, second]):
            try:
                result = future.result()
            except Exception:
                continue
            if result is not None:
                return result
        return None

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
        # The tag's lists, and then their pages, are read at once (one after another took a minute).
        with ThreadPoolExecutor(min(16, len(wanted)), thread_name_prefix="noveldb-tags") as pool:
            found = [lists for lists in pool.map(self._lists, wanted) if lists]
        merging = {}
        for source in set().union(*found) if found else ():
            lists, every = [], [lists[source] for lists in found if source in lists]
            for lst in every:  # a tag and its translation list the same books: read them once
                if not any(lst.total == other.total and lst.head == other.head for other in lists):
                    lists.append(lst)
            merging[source] = lists
        jobs = [(source, at, lst) for source, lists in merging.items() if len(lists) > 1 for at, lst in enumerate(lists)]
        pages = {}
        if jobs:
            with ThreadPoolExecutor(min(16, len(jobs)), thread_name_prefix="noveldb-tag-pages") as pool:
                pages = dict(zip([(source, at) for source, at, _lst in jobs],
                                 pool.map(lambda job: job[2].numbers(0, UNION_PAGES * job[2].page), jobs)))
        out = {}
        for source, lists in merging.items():
            if len(lists) == 1:
                out[source] = lists[0]
                continue
            read = [pages[(source, at)] for at in range(len(lists))]
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
        # Each word's lists, and then their first pages, are read at once (one after another, a title of
        # five words took seconds: each a Range request).
        if len(wanted) == 1:
            return self._lists(wanted[0])
        with ThreadPoolExecutor(min(16, len(wanted)), thread_name_prefix="noveldb-words") as pool:
            per_token = list(pool.map(self._lists, wanted))
        if not per_token or not all(per_token):
            return {}
        sources = list(set.intersection(*(set(lists) for lists in per_token)))
        # Each source's shortest lists only (a long title's common pieces, such as "生活" in 6,000 titles,
        # are not read page by page): every book with the whole title is in them too.
        shortest = {source: sorted(range(len(per_token)), key=lambda at: per_token[at][source].total)[:INTERSECT_LISTS]
                    for source in sources}
        jobs = [(source, at, per_token[at][source]) for source in sources for at in shortest[source]]
        with ThreadPoolExecutor(min(16, max(1, len(jobs))), thread_name_prefix="noveldb-pages") as pool:
            pages = dict(zip([(source, at) for source, at, _lst in jobs],
                             pool.map(lambda job: job[2].numbers(0, MULTI_PAGES * job[2].page), jobs)))
        out = {}
        for source in sources:
            lists = [per_token[at][source] for at in shortest[source]]
            read = [pages[(source, at)] for at in shortest[source]]
            # A list read only in part says nothing of the books after its last one read.
            cutoff = min((nums[-1] for nums, lst in zip(read, lists) if nums and len(nums) < lst.total), default=None)
            found = set(read[0]).intersection(*read[1:])
            numbers = sorted(n for n in found if cutoff is None or n <= cutoff)
            if numbers:
                out[source] = _List.of(numbers)
        return out

    # -- searching -------------------------------------------------------------------------------

    def search(self, text: str, mode: str = "name", limit: int = 40,
               on_total: Optional[Callable[[int], None]] = None) -> List[dict]:
        """The first page of cards (the generic search's shape) for a title or tag search."""
        return self.search_page(text, mode, 0, limit, on_total=on_total)[0]

    def search_page(self, text: str, mode: str = "name", page: int = 0, limit: int = 40,
                    sources: Optional[List[str]] = None, on_total: Optional[Callable[[int], None]] = None) -> tuple:
        """(cards, more): page `page` of the results, `limit` cards each. Each round takes the next
        most read book of every source, the lead source's first; `more` says whether pages follow.
        `on_total(n)`: how many books match, before the cards are read. Mode "source": `text` names
        sources ("npia,sfc"), and the results are their books, the most read first (their Top lists)."""
        if mode == "source":
            names = [name for name in text.split(",") if name]
            if not names or self.manifest() is None:
                return [], False
            streams = {name: lst for name in names for source, lst in self._lists("!" + name).items() if source == name}
        else:
            wanted = ([tag_token(text)] if mode == "tag" else ["@" + re.sub(r"\s+", " ", normal(text)).strip()]
                      if mode == "author" else tokens(text))
            if not wanted or self.manifest() is None:
                return [], False
            streams = self._tag_lists(text) if mode == "tag" else self._streams(wanted)
        if sources:
            streams = {name: stream for name, stream in streams.items() if name in sources}
        if on_total is not None:
            on_total(sum(stream.total for stream in streams.values()))
        order = sorted(streams, key=lambda name: (name != LEAD, streams[name].first))
        picks, more = _round_robin([(name, streams[name].total) for name in order], page * limit, limit)
        numbers = [number for number in (streams[name].number(at) for name, at in picks) if number is not None]
        self._prefetch_blocks(numbers)
        return [card for card in (self._card(number) for number in numbers) if card], more

    def _prefetch_blocks(self, numbers: List[int]) -> None:
        """The card blocks these books are in, read at once: one after another, a page's ~40 blocks took
        10 s (each a Range request to the release file); together they take about as long as one."""
        manifest = self.manifest()
        blocks = sorted({number // manifest["block"] for number in numbers}) if manifest else []
        if len(blocks) < 2:
            return
        with ThreadPoolExecutor(min(16, len(blocks)), thread_name_prefix="noveldb-blocks") as pool:
            list(pool.map(lambda index: self._piece("block", index), blocks))

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

    def tag_names(self) -> List[str]:
        """Every tag name NovelDB's books carry (on a few books or more), the most used first."""
        manifest = self.manifest() or {}
        if manifest and not manifest.get("tagsAt"):
            # A table of contents kept from before the index listed its tags: read it again once.
            self._forget()
            manifest = self.manifest() or {}
        at = manifest.get("tagsAt")
        listed = self._piece("tags", 0, at=at) if at else None
        return [str(entry[0]) for entry in listed or [] if isinstance(entry, list) and entry]

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
        wanted = range(start // self.page, (end - 1) // self.page + 1)
        if len(wanted) > 2:
            # The pages read at once (each a Range request): a word in many titles has dozens.
            with ThreadPoolExecutor(min(8, len(wanted)), thread_name_prefix="noveldb-list") as pool:
                pages = list(pool.map(self._page, wanted))
        else:
            pages = [self._page(number) for number in wanted]
        for page in pages:
            out.extend(page)
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
