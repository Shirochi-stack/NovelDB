"""The Catalog's generic search: books by name or tag from the top sites, with their metadata and covers.

The sites are the 100 most visited sites any engine knows (`generic_sites.json`, built by
scripts/build_generic_sites.py). Every source streams its cards to the page as they come in:
- Each site's own search and its genre and tag pages, found and read the generic way
  (`site_search.SiteSearch`): this covers every site in the list.
- lightnovel-crawler's crawlers that search (here) and LNReader's plugins (in the Catalog page itself,
  see noveldb/qt/catalog/generic/generic.js), for the sites they know.
- A web search (DuckDuckGo's HTML results, then Bing's RSS results) kept to sites an engine knows; an
  engine that refuses or answers with something else just adds nothing.
- A card without a cover gets its book page read (`generic.extract_metadata`); the page asks for the
  rest (author, description, tags) when a card is opened.

No Qt here: `GenericSearch.run` calls `emit(cards)` from worker threads and stops when `stop` is set.
"""

from __future__ import annotations

import html as html_module
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

from noveldb.core.engines.generic import CHAPTER_WORDS, decode, extract_metadata, site_of

SITES_FILE = Path(__file__).with_name("generic_sites.json")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
SITES_PER_QUERY = 8
MODES = ("name", "tag")


@lru_cache(maxsize=1)
def top_sites() -> List[dict]:
    try:
        return json.loads(SITES_FILE.read_text(encoding="utf-8"))["sites"]
    except (OSError, ValueError, KeyError):
        return []


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def default_fetch(url: str, method: str = "GET", data=None, headers: Optional[dict] = None,
                  binary: bool = False, referer: str = "", timeout: float = 15.0) -> Optional[dict]:
    """A plain request (the Catalog has no browser of its own): {status, url, content, text, headers}."""
    import requests

    sent = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,*;q=0.5", **(headers or {})}
    if referer:
        sent.setdefault("Referer", referer)
    try:
        response = requests.request(method, url, data=data, headers=sent, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    return {"status": response.status_code, "url": response.url, "content": response.content,
            "text": "" if binary else response.text,
            "headers": "\n".join(f"{k}: {v}" for k, v in response.headers.items())}


def looks_like_book(url: str, title: str = "") -> bool:
    """A book's page rather than a chapter, a list, a profile or the home page."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path or path.count("/") == 0:
        return False
    # Listing pages: the address starts with the listing's word (after a language prefix like /en/).
    segments = [s for s in path.split("/") if s]
    if segments and re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", segments[0], re.I):
        segments = segments[1:]
    if not segments:
        return False
    listing = r"(search|tags?|genres?|categor(y|ies)|authors?|users?|members?|forums?|login|register|latest|popular|"               r"ranking|rankings|top|list|library|blogs?|contests?|novel-list|manga-list|originals|canvas|page)"
    has_id = re.search(r"(^|&)(title_no|id|book_?id|novel_?id|bid|no)=", parts.query, re.I)
    if re.fullmatch(listing, segments[0], re.I) and not has_id and not re.search(
            r"^(novel|book|series|story|fiction|manga|comic|webtoon|title|works?)$", segments[0], re.I):
        return False
    if len(segments) == 1 and re.fullmatch(listing, segments[0], re.I):
        return False
    last = path.split("/")[-1]
    if re.search(r"(chapter|chap|ch|episode|ep)[-_.]?\d", last, re.I) or re.fullmatch(r"\d{1,5}(\.html?)?", last) and \
            re.search(r"/(novel|book|series|story|fiction|manga|comic|title)/[^/]+/\d+", path, re.I):
        return False
    if title and CHAPTER_WORDS.search(title) and re.search(r"\d", title) and not re.search(
            r"(novel|manga|webtoon|manhwa|series)", title, re.I):
        return False
    return True


def _clean_title(title: str, url: str) -> str:
    from noveldb.core.catalog.sites import clean_page_title

    title = html_module.unescape(re.sub(r"<[^>]+>", "", title or "")).strip()
    title = clean_page_title(title, url)
    title = re.sub(r"^(read\s+)", "", title, flags=re.I)
    title = re.sub(r"\s+(novel|light novel|manga|manhwa|webtoon)?\s*(online)?\s*(for\s+)?free.*$", "", title, flags=re.I)
    return title.strip(" -|–—")[:200]


# -- web search ------------------------------------------------------------------------------------

def bing_results(query: str, fetch: Callable) -> List[dict]:
    result = fetch(f"https://www.bing.com/search?format=rss&count=50&q={quote_plus(query)}")
    if not result or result["status"] != 200:
        return []
    out = []
    for item in re.findall(r"<item>(.*?)</item>", decode(result), re.S):
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        description = re.search(r"<description>(.*?)</description>", item, re.S)
        if link:
            out.append({"url": html_module.unescape(link.group(1).strip()),
                        "title": html_module.unescape(title.group(1).strip()) if title else "",
                        "snippet": html_module.unescape(description.group(1).strip()) if description else ""})
    return out


def duckduckgo_results(query: str, fetch: Callable) -> List[dict]:
    result = fetch("https://html.duckduckgo.com/html/", method="POST", data=f"q={quote_plus(query)}",
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    if not result or result["status"] != 200:
        return []
    page = decode(result)
    out = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href = html_module.unescape(match.group(1))
        if "uddg=" in href:
            href = unquote(parse_qs(urlsplit(href).query).get("uddg", [""])[0])
        if href.startswith("//"):
            href = "https:" + href
        snippet = ""
        tail = page[match.end(): match.end() + 2000]
        found = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', tail, re.S)
        if found:
            snippet = html_module.unescape(re.sub(r"<[^>]+>", "", found.group(1)))
        out.append({"url": href, "title": html_module.unescape(re.sub(r"<[^>]+>", "", match.group(2))), "snippet": snippet})
    return out


def web_search(query: str, fetch: Callable) -> List[dict]:
    """Both engines' results for one query (an engine that refuses or fails just adds nothing)."""
    results = []
    for engine in (duckduckgo_results, bing_results):
        try:
            results.extend(engine(query, fetch))
        except Exception:
            pass
    return results


def site_queries(text: str, mode: str, sites: List[dict]) -> List[str]:
    """Queries limited to groups of sites: `"text" (site:a OR site:b ...)`."""
    words = f'"{text}"' if mode == "name" else f'"{text}" (novel OR manga OR webtoon OR manhwa)'
    out = []
    for start in range(0, len(sites), SITES_PER_QUERY):
        group = sites[start:start + SITES_PER_QUERY]
        out.append(f"{words} (" + " OR ".join(f"site:{site['host']}" for site in group) + ")")
    return out


# -- the search ------------------------------------------------------------------------------------

class GenericSearch:
    def __init__(self, fetch: Callable = default_fetch, sites: Optional[List[dict]] = None,
                 known_site: Optional[Callable[[str], bool]] = None, workers: int = 12, site_search=None,
                 skip_lnreader: bool = True):
        from noveldb.core.catalog.site_search import SiteSearch

        self.fetch = fetch
        self.sites = sites if sites is not None else top_sites()
        self.known_site = known_site or (lambda url: False)
        self.workers = workers
        self.site_search = site_search or SiteSearch(fetch)
        self.skip_lnreader = skip_lnreader  # the page searches those sites with their LNReader plugin
        self._site_hosts = {host: site for site in self.sites for host in site.get("hosts", [site["host"]])}

    def site_for(self, url: str) -> Optional[dict]:
        host = _host(url)
        if host in self._site_hosts:
            return self._site_hosts[host]
        root = site_of(host)
        return next((site for site in self.sites if site["site"] == root), None)

    def card(self, url: str, title: str = "", snippet: str = "", source: str = "web", cover: str = "") -> dict:
        site = self.site_for(url)
        return {"url": url, "title": _clean_title(title, url) or url, "author": "", "cover": cover,
                "description": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", snippet or "")).strip()[:600], "tags": [],
                "site": (site or {}).get("site") or site_of(_host(url)), "comic": bool((site or {}).get("comic")),
                "source": source, "complete": False}

    def details(self, card: dict) -> dict:
        """The card with what the book's page says: title, author, cover, description, tags."""
        result = self.fetch(card["url"])
        if not result or result["status"] >= 400:
            return dict(card, complete=True)
        meta = extract_metadata(decode(result), result.get("url") or card["url"])
        merged = dict(card, complete=True)
        for key in ("title", "author", "cover", "description", "tags", "status"):
            value = meta.get(key)
            if value and (key != "description" or len(value) > len(card.get("description") or "")):
                merged[key] = value
        return merged

    def _lncrawl_entries(self) -> List[dict]:
        return [dict(entry, _site=site) for site in self.sites for entry in site.get("lncrawl", [])
                if site.get("search") == "lncrawl"]

    def run(self, text: str, mode: str, emit: Callable[[List[dict]], None], stop: threading.Event,
            done: Callable[[dict], None] = lambda _summary: None, deadline: float = 90.0) -> None:
        """Search everything for `text` for up to `deadline` seconds; `emit` gets new and updated cards
        as they arrive. Sites still answering then are left behind (their cards are dropped)."""
        outer = stop
        stop = threading.Event()
        watcher = threading.Thread(target=lambda: (outer.wait(deadline), stop.set()), daemon=True)
        watcher.start()
        text = (text or "").strip()
        mode = mode if mode in MODES else "name"
        seen: Dict[str, dict] = {}
        lock = threading.Lock()
        summary = {"web": 0, "lncrawl": 0, "details": 0, "failed": 0}

        def offer(cards: Iterable[dict]) -> List[dict]:
            fresh = []
            with lock:
                for card in cards:
                    key = card["url"].rstrip("/")
                    if key in seen or stop.is_set():
                        continue
                    seen[key] = card
                    fresh.append(card)
            if fresh:
                emit(fresh)
            return fresh

        pool = ThreadPoolExecutor(self.workers, thread_name_prefix="generic-search")
        futures = []
        detail_futures = []

        def enrich(cards: List[dict]):
            for card in cards:
                if not stop.is_set():
                    detail_futures.append(pool.submit(self._detail_and_emit, card, emit, stop, summary))

        def from_web(query: str, open_search: bool):
            if stop.is_set():
                return
            cards = []
            for hit in web_search(query, self.fetch):
                url = hit["url"]
                if not url.startswith(("http://", "https://")) or not looks_like_book(url, hit["title"]):
                    continue
                if self.site_for(url) is None and not (open_search and self.known_site(url)):
                    continue
                cards.append(self.card(url, hit["title"], hit["snippet"], "web"))
            summary["web"] += len(cards)
            enrich(offer(cards))

        def from_lncrawl(entry: dict):
            if stop.is_set():
                return
            try:
                from noveldb.core.engines.lncrawl_engine import LncrawlEngine

                engine = LncrawlEngine(fetch=self.fetch)
                hits = engine.search(entry, text)[:12]
            except Exception:
                summary["failed"] += 1
                return
            cards = [self.card(hit["url"], hit["name"], hit.get("info", ""), "lncrawl") for hit in hits]
            summary["lncrawl"] += len(cards)
            enrich(offer(cards))

        def from_site(site: dict):
            if stop.is_set():
                return
            home = f"https://{site['host']}/"
            try:
                hits = (self.site_search.search(home, text) if mode == "name"
                        else self.site_search.tag(home, text))
            except Exception:
                summary["failed"] += 1
                return
            cards = [self.card(hit["url"], hit["title"], hit.get("snippet", ""), "site", hit.get("cover", ""))
                     for hit in hits]
            summary["site"] = summary.get("site", 0) + len(cards)
            fresh = offer(cards)
            enrich([card for card in fresh if not card["cover"]])

        for site in self.sites:
            if not (self.skip_lnreader and site.get("lnreader")):
                futures.append(pool.submit(from_site, site))
        if mode == "name":
            for entry in self._lncrawl_entries():
                futures.append(pool.submit(from_lncrawl, entry))
        open_words = (f'"{text}" (novel OR manga OR webtoon)' if mode == "tag"
                      else f'"{text}" (novel OR manga OR webtoon OR manhwa)')
        futures.append(pool.submit(from_web, open_words, True))
        import time

        ends = time.monotonic() + deadline
        try:
            for future in as_completed(futures, timeout=max(1.0, ends - time.monotonic())):
                future.result()
            while detail_futures:
                pending = list(detail_futures)
                detail_futures.clear()
                for future in as_completed(pending, timeout=max(1.0, ends - time.monotonic())):
                    future.result()
        except TimeoutError:
            summary["late"] = sum(1 for future in futures if not future.done())
        timed_out = stop.is_set() and not outer.is_set()
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
        done(dict(summary, results=len(seen), stopped=outer.is_set(), timed_out=timed_out or bool(summary.get("late"))))

    def _detail_and_emit(self, card: dict, emit, stop: threading.Event, summary: dict) -> None:
        if stop.is_set():
            return
        try:
            updated = self.details(card)
        except Exception:
            updated = dict(card, complete=True)
        summary["details"] += 1
        if not stop.is_set():
            emit([updated])
