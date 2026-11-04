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
ID_TEMPLATES_FILE = Path(__file__).with_name("id_templates.json")
# A novel ID: a number, or a code like "n1234ab".
ID_QUERY = re.compile(r"^\s*(\d{3,12}|n\d{4}[a-z]{1,3})\s*$", re.I)
# A page that says the book doesn't exist (an ID no book has).
NOT_FOUND = re.compile(r"(\b404\b|not found|page not found|does not exist|doesn't exist|no longer available|has been removed|"
                       r"존재하지 않|삭제된|없는 (작품|페이지|소설)|ページが見つかりません|存在しません|不存在|已下架|找不到)", re.I)
_learned: Dict[str, str] = {}  # site -> a book page address with {id}, learned from the books searches find
_learned_lock = threading.Lock()
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
SITES_PER_QUERY = 8
MODES = ("name", "tag", "author")  # "author": NovelDB's books by that author
# A site's "nothing matched your search" page, which some search pages give a book-like card or title.
NO_RESULTS = re.compile(
    r"(no (items?|results?|books?|novels?|titles?|matches|products?)( were)? (found|matching|match)|nothing (was )?found|"
    r"did not match any|didn't match any|(found|returned) (0|no) (results?|items?|matches)|\b0 results\b|no results for|"
    r"見つかりませんでした|該当する.{0,6}(ありません|見つかりません)|検索結果は?0件|"
    r"검색\s*결과가\s*없|결과가\s*없습니다|일치하는.{0,10}없|"
    r"没有找到|未找到|沒有找到|找不到相关|暂无(结果|数据|相关)|無結果|无结果|查無|"
    r"no se encontraron|sin resultados|aucun résultat|ничего не найдено|не найдено|keine (ergebnisse|treffer)|nenhum resultado)",
    re.I)


def is_no_results(*texts: str) -> bool:
    """A site's empty search page: its title or text says nothing matched."""
    return any(NO_RESULTS.search(text or "") for text in texts)
# The dates and counts a book page may show (generic.extract_metadata), passed on to the page as they are.
FIGURES = ("published", "updated", "rating", "ratings", "likes", "views", "chapters")


@lru_cache(maxsize=1)
def top_sites() -> List[dict]:
    try:
        return json.loads(SITES_FILE.read_text(encoding="utf-8"))["sites"]
    except (OSError, ValueError, KeyError):
        return []


@lru_cache(maxsize=1)
def id_templates() -> List[dict]:
    try:
        return json.loads(ID_TEMPLATES_FILE.read_text(encoding="utf-8"))["templates"]
    except (OSError, ValueError, KeyError):
        return []


def learn_template(url: str) -> None:
    """Remember how a site addresses its books, when the address carries one number of 3+ digits
    (".../novel/178019", "?no=12345"): the ID search tries that address with other IDs."""
    if not looks_like_book(url):
        return
    parts = urlsplit(url)
    numbers = re.findall(r"(?<![\d])\d{3,12}(?![\d])", parts.path + "?" + parts.query)
    if len(numbers) != 1:
        return
    site = site_of(_host(url))
    template = re.sub(r"(?<![\d])" + numbers[0] + r"(?![\d])", "{id}", f"{parts.scheme}://{parts.netloc}{parts.path}"
                      + (f"?{parts.query}" if parts.query else ""), count=1)
    with _learned_lock:
        if site and site not in _learned and len(_learned) < 400:
            _learned[site] = template


def load_learned(path: Path) -> None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    with _learned_lock:
        for site, template in (data or {}).items():
            if isinstance(template, str) and "{id}" in template and template.startswith(("http://", "https://")):
                _learned.setdefault(site, template)


def save_learned(path: Path) -> None:
    with _learned_lock:
        data = dict(_learned)
    try:
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    except OSError:
        pass


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def default_fetch(url: str, method: str = "GET", data=None, headers: Optional[dict] = None,
                  binary: bool = False, referer: str = "", timeout: float = 15.0, quick: bool = False) -> Optional[dict]:
    """A plain request: {status, url, content, text, headers}. `quick`: a short timeout, for guesses."""
    if quick:
        timeout = min(timeout, 8.0)
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
    listing = r"(search|tags?|genres?|categor(y|ies)|authors?|users?|members?|forums?|login|register|latest|popular|"               r"ranking|rankings|top|list|library|blogs?|contests?|novel-list|manga-list|originals|canvas|page|contact|contact-us|about|about-us|privacy|privacy-policy|terms|terms-of-service|dmca|faq|feedback|support|donate)"
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
                 known_site: Optional[Callable[[str], bool]] = None, workers: int = 32, site_search=None,
                 skip_lnreader: bool = True, noveldb=None):
        from noveldb.core.catalog.site_search import SiteSearch

        self.fetch = fetch
        self.sites = sites if sites is not None else top_sites()
        self.known_site = known_site or (lambda url: False)
        self.workers = workers
        self.site_search = site_search or SiteSearch(fetch)
        self.skip_lnreader = skip_lnreader  # the page searches those sites with their LNReader plugin
        self.noveldb = noveldb  # NovelDB's own books (noveldb_index.NovelDBIndex), when given
        self._site_hosts = {host: site for site in self.sites for host in site.get("hosts", [site["host"]])}

    def site_for(self, url: str) -> Optional[dict]:
        host = _host(url)
        if host in self._site_hosts:
            return self._site_hosts[host]
        root = site_of(host)
        return next((site for site in self.sites if site["site"] == root), None)

    def card(self, url: str, title: str = "", snippet: str = "", source: str = "web", cover: str = "") -> dict:
        learn_template(url)
        site = self.site_for(url)
        return {"url": url, "title": _clean_title(title, url) or url, "author": "", "cover": cover,
                "description": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", snippet or "")).strip()[:600], "tags": [],
                "site": (site or {}).get("site") or site_of(_host(url)), "comic": bool((site or {}).get("comic")),
                "source": source, "complete": False}

    def details(self, card: dict) -> dict:
        """The card with what the book's page says: title, author, cover, description, tags. A NovelDB
        card has all of that but its synopsis, which comes from NovelDB's synopsis file for it."""
        if card.get("source") == "noveldb" and self.noveldb is not None:
            return dict(card, complete=True, description=self.noveldb.description(card) or card.get("description", ""))
        result = self.fetch(card["url"])
        if not result or result["status"] >= 400:
            return dict(card, complete=True)
        meta = extract_metadata(decode(result), result.get("url") or card["url"])
        if is_no_results(meta.get("title", ""), (meta.get("description") or "")[:400]):
            return dict(card, complete=True, empty=True)  # the page tells the card to go
        merged = dict(card, complete=True)
        for key in ("title", "author", "cover", "description", "tags", "status") + FIGURES:
            value = meta.get(key)
            if value and (key != "description" or len(value) > len(card.get("description") or "")):
                merged[key] = value
        return merged

    def _lncrawl_entries(self) -> List[dict]:
        """Every lightnovel-crawler source that searches (the top sites' and all the others)."""
        try:
            from noveldb.core.engines.lncrawl_engine import searchable_sites

            entries = searchable_sites()
        except Exception:
            entries = []
        out, seen = [], set()
        for entry in entries:
            host = _host(entry["base_urls"][0] if entry.get("base_urls") else "")
            if host and host not in seen and not (self.skip_lnreader and self._lnreader_host(host)):
                seen.add(host)
                out.append(dict(entry, _site=self.site_for("https://" + host + "/") or {"site": site_of(host), "host": host}))
        return out[:120]

    def _lnreader_host(self, host: str) -> bool:
        site = self.site_for("https://" + host + "/")
        return bool(site and site.get("lnreader"))

    def run(self, text: str, mode: str, emit: Callable[[List[dict]], None], stop: threading.Event,
            done: Callable[[dict], None] = lambda _summary: None, deadline: float = 45.0,
            progress: Callable[[dict], None] = lambda _item: None) -> None:
        """Search everything for `text` for up to `deadline` seconds; `emit` gets new and updated cards
        as they arrive. Sites still answering then are left behind (their cards are dropped).
        `progress` hears {"total": n} first, then {"site", "ms", "found", "state"} as each source answers."""
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
                    if key in seen or stop.is_set() or is_no_results(card["title"], card.get("description", "")[:400]):
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

        import time

        def report(site: str, started: float, found: int, state: str = "ok"):
            progress({"site": site, "ms": int((time.monotonic() - started) * 1000), "found": found, "state": state})

        def from_web(query: str, open_search: bool):
            if stop.is_set():
                return
            started = time.monotonic()
            cards = []
            for hit in web_search(query, self.fetch):
                url = hit["url"]
                if not url.startswith(("http://", "https://")) or not looks_like_book(url, hit["title"]):
                    continue
                if self.site_for(url) is None and not (open_search and self.known_site(url)):
                    continue
                cards.append(self.card(url, hit["title"], hit["snippet"], "web"))
            summary["web"] += len(cards)
            fresh = offer(cards)
            for card in fresh:
                card["ms"] = int((time.monotonic() - started) * 1000)
            report("web search", started, len(fresh))
            enrich(fresh)

        def from_lncrawl(entry: dict):
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                from noveldb.core.engines.lncrawl_engine import LncrawlEngine

                engine = LncrawlEngine(fetch=self.fetch)
                hits = engine.search(entry, text)[:12]
            except Exception:
                summary["failed"] += 1
                report(entry["_site"]["site"], started, 0, "failed")
                return
            ms = int((time.monotonic() - started) * 1000)
            cards = [dict(self.card(hit["url"], hit["name"], hit.get("info", ""), "lncrawl"), ms=ms) for hit in hits]
            summary["lncrawl"] += len(cards)
            fresh = offer(cards)
            report(entry["_site"]["site"], started, len(fresh))
            enrich(fresh)

        def from_site(site: dict):
            if stop.is_set():
                return
            started = time.monotonic()
            home = f"https://{site['host']}/"
            try:
                hits = (self.site_search.search(home, text) if mode == "name"
                        else self.site_search.tag(home, text))
            except Exception:
                summary["failed"] += 1
                report(site["site"], started, 0, "failed")
                return
            ms = int((time.monotonic() - started) * 1000)
            cards = [dict(self.card(hit["url"], hit["title"], hit.get("snippet", ""), "site", hit.get("cover", "")), ms=ms,
                          verified=bool(hit.get("verified")))
                     for hit in hits]
            summary["site"] = summary.get("site", 0) + len(cards)
            fresh = offer(cards)
            plan = getattr(self.site_search, "plan", None)
            refused = plan is not None and not plan(home).get("reachable", True)
            report(site["site"], started, len(fresh), "refused" if refused else "ok")
            enrich([card for card in fresh if not card["cover"]])

        def from_noveldb():
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                # How many books match, as soon as that is known: the page shows it while the cards load.
                cards = self.noveldb.search(text, mode, on_total=lambda total: progress({"noveldbTotal": total}))
            except Exception:
                summary["failed"] += 1
                report("NovelDB", started, 0, "failed")
                return
            ms = int((time.monotonic() - started) * 1000)
            fresh = offer([dict(card, ms=ms, verified=mode != "name") for card in cards])
            summary["noveldb"] = len(fresh)
            report("NovelDB", started, len(fresh))

        if self.noveldb is not None:
            futures.append(pool.submit(from_noveldb))
        if mode == "author":  # NovelDB knows books by author; the sites' searches are by title
            progress({"total": len(futures)})
            for future in futures:
                future.result()
            pool.shutdown(wait=False)
            done(dict(summary, results=len(seen), stopped=outer.is_set(), timed_out=False))
            return
        for site in self.sites:
            if not (self.skip_lnreader and site.get("lnreader")):
                futures.append(pool.submit(from_site, site))
        if mode == "name":
            for entry in self._lncrawl_entries():
                futures.append(pool.submit(from_lncrawl, entry))
        open_words = (f'"{text}" (novel OR manga OR webtoon)' if mode == "tag"
                      else f'"{text}" (novel OR manga OR webtoon OR manhwa)')
        futures.append(pool.submit(from_web, open_words, True))
        progress({"total": len(futures)})

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

    def more(self, text: str, mode: str, page: int, emit: Callable[[List[dict]], None], stop: threading.Event,
             done: Callable[[dict], None] = lambda _s: None, deadline: float = 30.0,
             ndb_page: Optional[int] = None, sites: bool = True) -> None:
        """The next results of a search, as it is scrolled: NovelDB's page `ndb_page` (default `page`; 1 is
        the second, 0 none), and with `sites` the next page of each site whose results page had one. `done`
        hears {"more": whether any source has more after these, "ndbMore": whether NovelDB has}."""
        ndb_page = page if ndb_page is None else ndb_page
        text = (text or "").strip()
        mode = mode if mode in MODES else "name"
        kind = "tag" if mode == "tag" else "search"
        lock = threading.Lock()
        seen: set = set()
        state = {"more": False, "ndbMore": False, "results": 0}

        def offer(cards: List[dict]) -> None:
            fresh = []
            with lock:
                for card in cards:
                    key = card["url"].rstrip("/")
                    if key not in seen and not stop.is_set():
                        seen.add(key)
                        fresh.append(card)
                state["results"] += len(fresh)
            if fresh:
                emit(fresh)

        def from_noveldb():
            cards, more = self.noveldb.search_page(text, mode, ndb_page)
            offer([dict(card, verified=mode != "name") for card in cards])
            if more:
                state["more"] = state["ndbMore"] = True

        def from_site(home: str):
            if stop.is_set():
                return
            hits = self.site_search.more(home, kind, text)
            offer([dict(self.card(hit["url"], hit["title"], hit.get("snippet", ""), "site", hit.get("cover", "")),
                        verified=bool(hit.get("verified"))) for hit in hits])
            if self.site_search.has_more(home, kind, text):
                state["more"] = True

        jobs = [from_noveldb] if self.noveldb is not None and ndb_page >= 1 else []
        for site in self.sites if sites else []:
            home = f"https://{site['host']}/"
            if self.site_search.has_more(home, kind, text):
                jobs.append(lambda home=home: from_site(home))
        self._run_all(jobs, deadline)
        done(dict(state, stopped=stop.is_set()))

    def popular_more(self, sites: List[dict], emit: Callable[[List[dict]], None], stop: threading.Event,
                     done: Callable[[dict], None] = lambda _s: None, deadline: float = 30.0) -> None:
        """The next page of each of `sites`' popular lists read before (for the Top lists, as they are
        scrolled); cards carry "siteRank" and "position" like `popular`'s."""
        state = {"more": False, "results": 0}
        lock = threading.Lock()

        def one(index: int, site: dict):
            home = f"https://{site['host']}/"
            if stop.is_set() or not self.site_search.has_more(home, "popular", ""):
                return
            hits = self.site_search.more(home, "popular", "")
            cards = [dict(self.card(hit["url"], hit["title"], hit.get("snippet", ""), "site", hit.get("cover", "")),
                          siteRank=index, position=position, listLength=len(hits))
                     for position, hit in enumerate(hits) if looks_like_book(hit["url"], hit["title"])]
            with lock:
                state["results"] += len(cards)
                if self.site_search.has_more(home, "popular", ""):
                    state["more"] = True
            if cards and not stop.is_set():
                emit(cards)

        self._run_all([lambda i=i, site=site: one(i, site) for i, site in enumerate(sites)], deadline)
        done(state)

    def _run_all(self, jobs: list, deadline: float) -> None:
        if not jobs:
            return
        pool = ThreadPoolExecutor(min(self.workers, 16), thread_name_prefix="generic-more")
        futures = [pool.submit(job) for job in jobs]
        try:
            for future in as_completed(futures, timeout=deadline):
                try:
                    future.result()
                except Exception:
                    pass
        except TimeoutError:
            pass
        pool.shutdown(wait=False, cancel_futures=True)

    def by_id(self, text: str, emit: Callable[[List[dict]], None], stop: threading.Event,
              done: Callable[[dict], None] = lambda _s: None, progress: Callable[[dict], None] = lambda _i: None,
              deadline: float = 40.0) -> None:
        """The book with this ID on every site that addresses its books by ID (the shipped templates,
        then the ones learned from searches): each page is read, and kept when it is a real book."""
        import time

        code = text.strip()
        numeric = code.isdigit()
        tries, seen_sites = [], set()
        for entry in id_templates():
            if (entry.get("id") == "number") == numeric:
                tries.append((entry["site"], entry["template"]))
                seen_sites.add(site_of(_host(entry["template"].replace("{id}", "1"))))
        with _learned_lock:
            learned = dict(_learned)
        if numeric:
            tries.extend((site, template) for site, template in learned.items() if site not in seen_sites)
        progress({"total": len(tries)})
        found = {"n": 0}
        pool = ThreadPoolExecutor(min(self.workers, 16), thread_name_prefix="generic-id")

        def one(site: str, template: str):
            if stop.is_set():
                return
            started = time.monotonic()
            url = template.replace("{id}", code)
            card = self.card(url, "", "", "id")
            card["site"] = site_of(_host(url))
            book = None
            try:
                result = self.fetch(url)
                final = (result or {}).get("url") or url
                if result and result["status"] < 400 and code.lower() in final.lower():
                    meta = extract_metadata(decode(result), final)
                    title = (meta.get("title") or "").strip()
                    if title and not NOT_FOUND.search(title) and not is_no_results(title, meta.get("description", "")[:300])                             and not self._same_for_any_id(template, numeric, title):
                        book = dict(card, url=final, complete=True, ms=int((time.monotonic() - started) * 1000),
                                    **{k: meta.get(k) for k in ("title", "author", "cover", "description", "tags", "status")
                                       + FIGURES if meta.get(k)})
            except Exception:
                book = None
            ms = int((time.monotonic() - started) * 1000)
            progress({"site": card["site"], "ms": ms, "found": 1 if book else 0, "state": "ok" if book else "failed"})
            if book and not stop.is_set():
                found["n"] += 1
                emit([book])

        futures = [pool.submit(one, site, template) for site, template in tries]
        try:
            for future in as_completed(futures, timeout=deadline):
                future.result()
        except TimeoutError:
            pass
        pool.shutdown(wait=False, cancel_futures=True)
        done({"results": found["n"]})

    def _same_for_any_id(self, template: str, numeric: bool, title: str) -> bool:
        """The site shows this title for an ID no book has too (a page its scripts fill in, or its home
        page): the title says nothing about the book."""
        control = template.replace("{id}", "987654321" if numeric else "n0000zz")
        try:
            result = self.fetch(control, quick=True)
        except Exception:
            return False
        if not result or result["status"] >= 400:
            return False
        other = (extract_metadata(decode(result), result.get("url") or control).get("title") or "").strip()
        return bool(other) and other == title

    def popular(self, sites: List[dict], emit: Callable[[List[dict]], None], stop: threading.Event,
                done: Callable[[dict], None] = lambda _s: None, progress: Callable[[dict], None] = lambda _i: None,
                deadline: float = 40.0) -> None:
        """The popular lists of `sites` (busiest first): each card carries its site's rank ("siteRank") and
        its place in the list ("position") for the page to score. Cards without a cover get their page read."""
        import time

        pool = ThreadPoolExecutor(min(self.workers, 16), thread_name_prefix="generic-top")
        progress({"total": len(sites)})
        seen = set()
        lock = threading.Lock()

        def one(index: int, site: dict):
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                hits = self.site_search.popular(f"https://{site['host']}/")
            except Exception:
                hits = []
            ms = int((time.monotonic() - started) * 1000)
            cards = []
            with lock:
                for position, hit in enumerate(hits):
                    url = hit["url"].rstrip("/")
                    if url in seen or is_no_results(hit["title"]) or not looks_like_book(hit["url"], hit["title"]):
                        continue
                    seen.add(url)
                    card = self.card(hit["url"], hit["title"], hit.get("snippet", ""), "site", hit.get("cover", ""))
                    cards.append(dict(card, ms=ms, siteRank=index, position=position, listLength=len(hits)))
            progress({"site": site["site"], "ms": ms, "found": len(cards), "state": "ok" if cards else "failed"})
            if cards and not stop.is_set():
                emit(cards)

        futures = [pool.submit(one, index, site) for index, site in enumerate(sites)]
        try:
            for future in as_completed(futures, timeout=deadline):
                future.result()
        except TimeoutError:
            pass
        pool.shutdown(wait=False, cancel_futures=True)
        done({"results": len(seen)})

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
