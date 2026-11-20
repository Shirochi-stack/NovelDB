"""Cache verified NovelUpdates titles and record them beside the catalog.

Collection is an explicit, resumable network operation. It walks the listing of
every series in a language, so a series is found whether or not its page names
a publisher. Matching and recording are offline. Native titles and author
aliases must both match; where a series names no author in native script, a
long title held once on each side is enough. A verified title never replaces
the displayed machine title: it is kept with its address for search and for
the Details view.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import parse_qs, urljoin, urlsplit

try:
    from . import source_names
    from .metadata_common import atomic_bytes, atomic_json, atomic_text, utc_now
except ImportError:
    import source_names
    from metadata_common import atomic_bytes, atomic_json, atomic_text, utc_now

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "metadata/state/nu_titles.json.gz"
ADDRESSES_NAME = "nu_title_addresses.json.gz"
RESTORE_NAME = "nu_title_restore.json.gz"
BASE = "https://www.novelupdates.com/"
PARSER_VERSION = 2
SOURCES = ("npia", "kpage", "sfc", "nweb", "jara", "mpia", "rbooks", "nseries")
NATIVE = re.compile(r"[\u3400-\u9fff\uac00-\ud7af]")
# Every series in a language is listed here, 25 to a page, each with its series number.
LISTINGS = {"Korean": "language/korean/", "Chinese": "language/chinese/"}
# The listings have no order by date added. A newly added series shows among the recently
# updated; the order by title is the one whose pages hold still while a sweep runs for days.
ORDERS = {"recent": "?sort=8&order=2&status=1", "all": "?sort=7&order=1&status=1"}
RESTART = {"recent": 20 * 3600, "all": 30 * 86400}
RECENT_PAGES = 120   # the most a sweep of the recently updated reads
QUIET_PAGES = 4      # ...and it ends after this many pages in a row with nothing new
REREAD = 180 * 86400
TURNED_AWAY = 8      # failures in a row that end a run
# A title this long (spaces aside) names one work: enough where a series names no native author.
DISTINCT_TITLE = 8


def normalized(value):
    """Ignore spacing and compatibility forms, retaining edition punctuation."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in value if not char.isspace() and char not in "\u200b\ufeff")


def author_keys(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    wrapped = re.fullmatch(r"(\[+)([^\[\]]+)(\]+)", text)
    if wrapped and len(wrapped[1]) == len(wrapped[3]):
        text = wrapped[2]
    aliases = re.fullmatch(r"([^\[\]]+)\[([^\[\]]+)\]", text)
    names = aliases.groups() if aliases else (text,)
    return {normalized(name).rstrip(".") if NATIVE.search(name) else normalized(name)
            for name in names if normalized(name)}


def observed(record):
    """When a cached series was read, in seconds."""
    return datetime.fromisoformat(record["checked_at"].replace("Z", "+00:00")).timestamp()


def language_of(source):
    return "Chinese" if source == "sfc" else "Korean"


def valid_title(value):
    return (isinstance(value, str) and bool(re.search(r"[A-Za-z]", value))
            and not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", value)
            and not any(token in value for token in ("|||", "\n", "\r")))


def nu_url(value, *, series=False):
    url = urlsplit(urljoin(BASE, value))
    if url.scheme != "https" or url.netloc != "www.novelupdates.com":
        raise ValueError("Unexpected NovelUpdates address")
    if series:
        post_id = parse_qs(url.query).get("p", [])
        if url.path == "/" and len(post_id) == 1 and re.fullmatch(r"[1-9][0-9]*", post_id[0]):
            return url._replace(query="p=" + post_id[0], fragment="").geturl()
        if not re.fullmatch(r"/series/[^/]+/", url.path) or url.path.split("/")[2] in (".", ".."):
            raise ValueError("Expected a canonical series address")
        url = url._replace(query="", fragment="")
    return url.geturl()


def read_cache(path=CACHE):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "series": {}, "publishers": {}}
    data = json.loads(gzip.decompress(path.read_bytes()))
    if (data.get("version") != 1 or not isinstance(data.get("series"), dict)
            or not isinstance(data.get("publishers"), dict)):
        raise ValueError("Invalid NovelUpdates title cache")
    return data


def write_cache(path, data):
    raw = source_names.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_bytes(path, gzip.compress(raw, mtime=0))


def import_snapshot(path, cache_path, origin_url, captured_at):
    """Import attributed NU metadata while retaining newer live observations."""
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    raw = Path(path).read_bytes()
    records = json.loads(raw)
    if not isinstance(records, list) or not origin_url.startswith("https://"):
        raise ValueError("Expected an attributed NU metadata snapshot")
    cache = read_cache(cache_path)
    known_ids = {str(record["nu_id"]): url for url, record in cache["series"].items() if record.get("nu_id")}
    digest = hashlib.sha256(raw).hexdigest()
    imported = 0
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or row["id"] <= 0:
            continue
        language = {"korean": "Korean", "chinese": "Chinese"}.get(row.get("original_language"))
        aliases = row.get("assoc_names")
        authors = row.get("authors")
        if (not language or not valid_title(row.get("name")) or not isinstance(aliases, list)
                or not isinstance(authors, list) or not all(isinstance(name, str) for name in aliases + authors)):
            continue
        url = known_ids.get(str(row["id"]), nu_url(f"?p={row['id']}", series=True))
        existing = cache["series"].get(url)
        if existing:
            observed = datetime.fromisoformat(existing["checked_at"].replace("Z", "+00:00"))
            if observed >= captured:
                existing["aliases"] = list(dict.fromkeys(existing.get("aliases", []) + aliases))
                existing["authors"] = list(dict.fromkeys(existing.get("authors", []) + authors))
                continue
        cache["series"][url] = {
            "url": url, "title": row["name"], "aliases": list(dict.fromkeys(aliases)),
            "authors": list(dict.fromkeys(authors)), "language": language,
            "checked_at": captured_at, "snapshot_url": origin_url, "snapshot_sha256": digest,
            "nu_id": str(row["id"]),
        }
        imported += 1
    cache.setdefault("snapshots", {})[digest] = {"url": origin_url, "captured_at": captured_at, "imported": imported}
    write_cache(cache_path, cache)
    return {"imported": imported, "snapshot_sha256": digest, "captured_at": captured_at}


def parse_series(html, expected_url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one(".seriestitlenu")
    aliases = soup.select_one("#editassociated")
    authors = soup.select("#showauthors a")
    language = soup.select_one("#showlang")
    canonical = soup.select_one('link[rel="canonical"]')
    identity = soup.select_one("#mypostid")
    novel_id = str(identity.get("value") or "") if identity else ""
    missing = [name for name, element in (("heading", heading), ("language", language), ("canonical", canonical))
               if not element]
    if missing:
        raise ValueError("Incomplete or challenged NovelUpdates series page. Missing " + ", ".join(missing))
    url = nu_url(canonical.get("href", ""), series=True)
    expected = nu_url(expected_url, series=True)
    expected_id = parse_qs(urlsplit(expected).query).get("p", [])
    if (expected_id and novel_id != expected_id[0]) or (not expected_id and url != expected):
        raise ValueError("NovelUpdates series identity changed")
    title = heading.get_text(" ", strip=True)
    if not title:
        raise ValueError("NovelUpdates heading is empty")
    native_aliases = []
    if aliases:
        for element in aliases.select("br"):
            element.replace_with("\n")
        for element in aliases.select("p, div, li"):
            element.insert_after("\n")
        native_aliases = list(dict.fromkeys(line.strip() for line in aliases.get_text().splitlines() if line.strip()))
    return {
        "url": url, "title": title,
        "aliases": native_aliases,
        "authors": list(dict.fromkeys(author.get_text(" ", strip=True) for author in authors)),
        "language": language.get_text(" ", strip=True), "checked_at": utc_now(),
        "parser_version": PARSER_VERSION,
        "nu_id": novel_id,
    }


class TitleIndex:
    def __init__(self, cache):
        self.aliases = defaultdict(list)
        self.urls = set(cache.get("series", {}))
        identities = {}
        for url, record in cache.get("series", {}).items():
            if record.get("url") != nu_url(url, series=True):
                raise ValueError("Invalid cached NovelUpdates series")
            if not valid_title(record.get("title")):
                continue
            identity = str(record.get("nu_id") or parse_qs(urlsplit(url).query).get("p", [url])[0])
            previous = identities.get(identity)
            if previous:
                newer = record if observed(record) > observed(previous) else previous
                record = {**newer, "aliases": list(dict.fromkeys(previous.get("aliases", []) + record.get("aliases", []))),
                          "authors": list(dict.fromkeys(previous.get("authors", []) + record.get("authors", [])))}
            identities[identity] = record
        for record in identities.values():
            for alias in set(normalized(alias) for alias in record.get("aliases", []) if NATIVE.search(alias)):
                self.aliases[alias].append(record)

    @classmethod
    def load(cls, path=CACHE):
        return cls(read_cache(path))

    def match(self, title, author, source, lone=False):
        """The one series with this native title and author.

        `lone`: the catalog names one author for this title. Then a series that names no
        author in native script (only a romanized one, or none: nothing to compare) is
        taken on a long title alone, when it is the only series with that title.
        """
        if not NATIVE.search(str(title or "")) or not author_keys(author):
            return None
        language = language_of(source)
        native = normalized(title)
        titled = [record for record in self.aliases.get(native, []) if record.get("language") == language]
        candidates = [record for record in titled
                      if author_keys(author).intersection(
                          key for alias in record.get("authors", []) for key in author_keys(alias))]
        if len(candidates) == 1:
            return candidates[0]
        if (lone and len(titled) == 1 and len(native) >= DISTINCT_TITLE
                and not any(NATIVE.search(name) for name in titled[0].get("authors", []))):
            return titled[0]
        return None

    def holders(self, rows, source, into=None):
        """{(language, native title): the authors these rows name for it}, for titles a series holds."""
        into = defaultdict(set) if into is None else into
        language = language_of(source)
        for row in rows:
            title = normalized(row[1])
            if title in self.aliases:
                into[language, title].add(frozenset(author_keys(row[2])))
        return into

    def match_rows(self, rows, source, holders=None):
        """(row, series) for each confirmed catalog row [id, title, author, ...].

        `holders` (catalog_holders) counts each title's authors over every source, so that a
        title two sources give to different authors is not taken on the title alone. Without
        it only these rows are counted.
        """
        holders = self.holders(rows, source) if holders is None else holders
        language = language_of(source)
        for row in rows:
            match = self.match(row[1], row[2], source,
                               lone=len(holders.get((language, normalized(row[1])), ())) == 1)
            if match:
                yield row, match


def read_restore(path):
    """The machine titles from before verified titles replaced them: {source: {id: {original, english}}}.

    `original` and `english` are corpus fields exactly as *_titles_en.txt held them.
    """
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(gzip.decompress(path.read_bytes()))
    if data.get("version") != 1 or not isinstance(data.get("sources"), dict):
        raise ValueError("Invalid machine title backup")
    return data["sources"]


def restore_state(state, restore=None):
    """Give each record back its machine title where a verified title had replaced it.

    A verified title is never the displayed title: it is kept beside the work
    (write_addresses) for search and Details. The machine title comes from the
    backup, else from the record's own history; a record with neither loses its
    title translation and is translated again. Returns (records changed, the ids
    left without a title).
    """
    try:
        from .metadata_pipeline import clean_field, decode_field
    except ImportError:
        from metadata_pipeline import clean_field, decode_field
    known = (restore or {}).get(state["source"], {})
    changed, pending = 0, set()
    for nid, record in state["records"].items():
        translations = record.get("translations") or {}
        old = translations.get("title")
        if not isinstance(old, dict) or old.get("origin") != "novelupdates":
            continue
        original = str(record.get("title") or "")
        english, translated_at = "", None
        saved = known.get(str(nid))
        if saved and saved.get("original") == clean_field(original):
            english = decode_field(saved.get("english") or "")
        if not english:
            for entry in reversed(record.get("translation_history", [])):
                if (entry.get("field") == "title" and entry.get("origin") != "novelupdates"
                        and entry.get("original") == original and str(entry.get("english") or "").strip()):
                    english, translated_at = entry["english"], entry.get("translated_at")
                    break
        record.setdefault("translation_history", []).append({**old, "field": "title", "reverted_at": utc_now()})
        if english:
            translations["title"] = {"original": original, "english": english,
                                     "translated_at": translated_at or utc_now()}
        else:
            translations.pop("title")
            pending.add(str(nid))
        changed += 1
    return changed, pending


class Collector:
    def __init__(self, delay=1.1, browser_fallback=False):
        from curl_cffi import requests
        self.session = requests.Session(impersonate="chrome")
        self.delay = max(1.0, delay)
        self.last_request = 0
        self.browser_fallback = browser_fallback
        self.browser = None

    def fetch(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        if self.browser is not None:
            return self.browser_fetch(url)
        response = self.session.get(nu_url(url), timeout=35)
        if response.status_code in (403, 503) and self.browser_fallback:
            from external_scraper import ExternalScraper
            self.browser = ExternalScraper(logger=lambda message: print(message, flush=True))
            return self.browser_fetch(url)
        response.raise_for_status()
        return response.text

    def browser_fetch(self, url):
        page = self.browser._site_page(url)
        result = page.evaluate("""async url => {
            const response = await fetch(url, {signal: AbortSignal.timeout(35000)});
            return {status: response.status, text: await response.text()};
        }""", url)
        if result["status"] == 200:
            try:
                if "/series/" in urlsplit(url).path or parse_qs(urlsplit(url).query).get("p"):
                    parse_series(result["text"], url)
                else:
                    parse_listing(result["text"], url)
                return result["text"]
            except ValueError:
                pass
        html = self.browser.render_html(url)
        if not html:
            raise RuntimeError("NovelUpdates browser request failed")
        return html

    def close(self):
        self.session.close()
        if self.browser:
            self.browser.cleanup()


def parse_listing(html, url):
    """A listing page: {series address: its series number, or ""} in page order, and the next page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for box in soup.select(".search_title"):
        mark = box.select_one('span[id^="sid"]')
        number = re.fullmatch(r"sid([1-9][0-9]*)", mark.get("id", "")) if mark else None
        for link in box.select("a[href]"):
            entries.setdefault(nu_url(link["href"], series=True), number[1] if number else "")
    if not entries:
        raise ValueError("Empty or challenged NovelUpdates listing page")
    next_link = soup.select_one('.digg_pagination a[rel="next"]')
    next_url = nu_url(urljoin(url, next_link["href"])) if next_link else None
    if next_url and urlsplit(next_url).path != urlsplit(url).path:
        raise ValueError("Unexpected listing pagination")
    return entries, next_url


def collect(sources, cache_path=CACHE, max_requests=100, fetch=None, browser_fallback=False, max_seconds=1800):
    """Checkpoint each successful request. Failed pages are read again later.

    Each language's listing is swept twice over: the recently updated, restarted daily and
    ended once its pages hold nothing new, and every series by title, restarted monthly. A
    sweep reads the series the cache has never held (the listing gives each one's number);
    the monthly one, from its second pass, also those last read long ago. The recently
    updated come first, so a series that is being translated now is not kept waiting.
    """
    cache = read_cache(cache_path)
    if cache.get("parser_version") != PARSER_VERSION:
        # Sweep again when alias extraction changes. Confirmed values stay available to
        # offline jobs throughout the refresh.
        cache["listings"] = {}
        cache["parser_version"] = PARSER_VERSION
    cache["publishers"] = {}  # Publisher indexes gave way to the listings: few series name a publisher.
    series = cache["series"]
    listings = cache.setdefault("listings", {})
    deferred = cache.setdefault("deferred", {})
    numbers = {}
    for url, record in series.items():
        number = str(record.get("nu_id") or "")
        if number and (number not in numbers or observed(record) > observed(series[numbers[number]])):
            numbers[number] = url

    started = time.time()

    def due(url, number="", reread=False):
        """Whether to read a series: never read (or by an older parser), or read long ago."""
        found = [record for record in (series.get(url), series.get(numbers.get(number)))
                 if record and record.get("parser_version", PARSER_VERSION) == PARSER_VERSION]
        return not found or (reread and started - max(map(observed, found)) >= REREAD)

    feeds = {}
    for order in ORDERS:
        for language in dict.fromkeys(language_of(source) for source in sources):
            progress = listings.setdefault(f"{language}/{order}", {"next": None, "pending": [], "complete": True})
            if progress["complete"] and started - progress.get("completed_at", 0) > RESTART[order]:
                progress.update(next=urljoin(BASE, LISTINGS[language] + ORDERS[order]), complete=False,
                                pages=0, quiet=0)
            feeds[f"{language}/{order}"] = order
    retries = [url for url, details in deferred.items() if details["retry_after"] <= started]
    client = Collector(browser_fallback=browser_fallback) if fetch is None else None
    fetch = fetch or client.fetch
    errors = []

    def failed(url, error):
        errors.append(f"{url}: {error}")
        print(f"NovelUpdates page remains pending: {url}: {error}", flush=True)

    def read(url):
        """Cache one series page. A page that fails is deferred, each time for longer."""
        try:
            record = parse_series(fetch(url), url)
        except Exception as error:
            count = deferred.get(url, {}).get("failures", 0) + 1
            deferred[url] = {"reason": str(error), "failures": count,
                             "retry_after": time.time() + min(30, 2 ** (count - 1)) * 86400}
            failed(url, error)
            return False
        series[url] = record
        deferred.pop(url, None)
        if record["nu_id"]:
            numbers[record["nu_id"]] = url
        return True

    def turn_page(name):
        """Queue what a listing page holds that is new, or due to be read again."""
        progress, order = listings[name], feeds[name]
        try:
            entries, next_url = parse_listing(fetch(progress["next"]), progress["next"])
        except Exception as error:
            failed(progress["next"], error)
            return False
        reread = order == "all" and bool(progress.get("completed_at"))
        wanted = [url for url, number in entries.items() if url not in deferred and due(url, number, reread)]
        progress["pages"] = progress.get("pages", 0) + 1
        progress["quiet"] = 0 if wanted else progress.get("quiet", 0) + 1
        if order == "recent" and (progress["quiet"] >= QUIET_PAGES or progress["pages"] >= RECENT_PAGES):
            next_url = None
        progress.update(pending=wanted, next=next_url)
        return True

    requested = 0
    # Failures since the last success. Some series pages never load, so a few in a row are
    # ordinary; this many mean the site is turning every request away.
    in_a_row = 0
    stalled = defaultdict(int)
    deadline = time.monotonic() + max_seconds
    try:
        while requested < max_requests and time.monotonic() < deadline and in_a_row < TURNED_AWAY:
            name = next((name for name in feeds if stalled[name] < 3 and not listings[name]["complete"]), None)
            made, failures = requested, len(errors)
            if retries:
                # A page that failed before may be gone for good: failing again says nothing of the site.
                requested += 1
                if read(retries.pop(0)):
                    in_a_row = 0
            elif name is None:
                break
            else:
                progress = listings[name]
                # Another sweep may have read a queued series since.
                while progress["pending"] and not due(progress["pending"][0], reread=True):
                    progress["pending"].pop(0)
                if progress["pending"]:
                    requested += 1
                    in_a_row = 0 if read(progress["pending"].pop(0)) else in_a_row + 1
                elif progress["next"]:
                    requested += 1
                    stalled[name] = 0 if turn_page(name) else stalled[name] + 1
                    in_a_row = 0 if not stalled[name] else in_a_row + 1
                if not progress["pending"] and not progress["next"]:
                    progress.update(complete=True, completed_at=time.time())
            write_cache(cache_path, cache)
            if requested > made and len(errors) == failures:
                print(f"NovelUpdates request {requested}: {len(series)} cached series", flush=True)
    finally:
        if client:
            client.close()
    if errors:
        raise RuntimeError(f"NovelUpdates collection incomplete. {len(errors)} pages failed: " + " | ".join(errors[:5]))
    return cache


def corpus_path(source, data_dir):
    return Path(data_dir) / ("titles_en.txt" if source == "npia" else f"{source}_titles_en.txt")


def raw_path(source, data_dir):
    return Path(data_dir) / ("novels.json" if source == "npia" else f"{source}_novels.json")


def catalog_holders(index, data_dir, sources=SOURCES):
    """TitleIndex.holders over every source's rows."""
    holders = defaultdict(set)
    for source in sources:
        path = raw_path(source, data_dir)
        if path.exists():
            index.holders(json.loads(path.read_text(encoding="utf-8")), source, holders)
    return holders


def repack(source, data_dir, replacements, verified):
    """Patch existing bundles, retaining raw rows, descriptions and ranking order.

    Only a work still showing its verified title (`verified`: id -> that title)
    is given the replacement; any other title a bundle shows is left alone.
    """
    try:
        from .build_catalog_preview import build as build_preview
    except ImportError:
        from build_catalog_preview import build as build_preview
    data_dir = Path(data_dir)
    manifest_path = data_dir / f"{source}_chunk_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = []
    top_name = f"{source}_top.json.gz"
    files = [*manifest["files"]]
    if (data_dir / top_name).exists():
        files.append(top_name)
    for name in files:
        path = data_dir / name
        if path.parent != data_dir or not path.exists():
            raise ValueError(f"Missing or unsafe title bundle: {name}")
        payload = json.loads(gzip.decompress(path.read_bytes()))
        rows = payload.get("novels", [])
        changed = False
        for row in rows:
            nid = str(row[0])
            match = replacements.get(nid)
            if (match and row[1] == match["original"] and row[2] == match["author"]
                    and payload.get("translations", {}).get(nid) == verified.get(nid) != match["english"]):
                payload.setdefault("translations", {})[nid] = match["english"]
                changed = True
        if changed:
            write_cache(path, payload)
        if name in manifest["files"]:
            hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    manifest["chunkHashes"] = hashes
    atomic_json(manifest_path, manifest)
    build_preview(source, data_dir)


def write_addresses(path, source, matches):
    """Keep each confirmed work's series address beside the published state.

    One gzip file for every source: {"sources": {source: {novel id: {"title", "url"}}}}.
    The title corpus and catalog bundles hold the English title alone, so this is
    where a work's NovelUpdates address stays after the run's report expires.
    """
    path = Path(path)
    data = {"version": 1, "sources": {}}
    if path.exists():
        stored = json.loads(gzip.decompress(path.read_bytes()))
        if stored.get("version") == 1 and isinstance(stored.get("sources"), dict):
            data = stored
    entries = {nid: {"title": match["english"], "url": match["url"]}
               for nid, match in sorted(matches.items())}
    if data["sources"].get(source) == entries and path.exists():
        return
    data["sources"][source] = entries
    data["sources"] = dict(sorted(data["sources"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_cache(path, data)


def record_catalog(source, data_dir, state_dir, cache_path=CACHE, rebuild=False, index=None, holders=None):
    """Record each confirmed work's verified title and address; never display it.

    The displayed English title is the machine translation. A corpus row that
    still shows a verified title (from when they replaced machine titles) gets
    its machine title back from the backup, or is emptied to be translated again.
    """
    try:
        from . import metadata_common, metadata_pipeline
    except ImportError:
        import metadata_common, metadata_pipeline
    # A run over every source passes these in: built once, they are the same for each.
    index = index or TitleIndex.load(cache_path)
    holders = catalog_holders(index, data_dir) if holders is None else holders
    restore = read_restore(Path(state_dir) / RESTORE_NAME)
    known = restore.get(source, {})
    rows = json.loads(raw_path(source, data_dir).read_text(encoding="utf-8"))
    matches = {str(row[0]): {"original": row[1], "author": row[2], "english": match["title"], "url": match["url"]}
               for row, match in index.match_rows(rows, source, holders)}
    path = corpus_path(source, data_dir)
    lines = []
    changes = {}
    restored = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.count("|||") < 2:
            lines.append(line + "\n")
            continue
        nid, rest = line.split("|||", 1)
        original, english = rest.rsplit("|||", 1)
        match = matches.get(nid)
        if (match and metadata_pipeline.decode_field(original) == match["original"]
                and metadata_pipeline.decode_field(english) == match["english"]):
            saved = known.get(nid)
            machine = saved.get("english", "") if saved and saved.get("original") == original else ""
            if machine != english:
                changes[nid] = {**match, "previous_english": english, "restored": machine}
                if machine:
                    restored[nid] = {**match, "english": metadata_pipeline.decode_field(machine)}
                english = machine
        lines.append(f"{nid}|||{original}|||{english}\n")
    if source in metadata_pipeline.SOURCES:
        state = metadata_common.load_state(source, state_dir)
        if restore_state(state, restore)[0]:
            metadata_common.save_state(state, state_dir)
    if changes:
        text = "".join(lines)
        atomic_text(path, text)
        if path.with_suffix(path.suffix + ".gz").exists():
            atomic_bytes(path.with_suffix(path.suffix + ".gz"), gzip.compress(text.encode("utf-8"), mtime=0))
    write_addresses(Path(state_dir) / ADDRESSES_NAME, source, matches)
    # Reconcile bundles on retries even when the corpus was already saved: every
    # confirmed work whose machine title is known shows it.
    if rebuild:
        for nid, match in matches.items():
            saved = known.get(nid)
            if (nid not in restored and saved and saved.get("english")
                    and saved.get("original") == metadata_pipeline.clean_field(match["original"])):
                restored[nid] = {**match, "english": metadata_pipeline.decode_field(saved["english"])}
        if restored:
            repack(source, data_dir, restored, {nid: match["english"] for nid, match in matches.items()})
    return {"source": source, "rows": len(rows), "matched": len(matches),
            "restored": sum(1 for change in changes.values() if change["restored"]),
            "pending": sum(1 for change in changes.values() if not change["restored"]), "changes": changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "record", "import-snapshot"))
    parser.add_argument("--source", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument("--browser-fallback", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "docs/data")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "metadata/state")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--snapshot-url")
    parser.add_argument("--snapshot-date")
    args = parser.parse_args()
    sources = SOURCES if args.source == "all" else (args.source,)
    if args.command == "collect":
        if args.max_requests <= 0 or args.max_seconds <= 0:
            parser.error("Collection limits must be positive")
        # Direct script invocation needs the repository root for the optional browser.
        if args.browser_fallback:
            import sys
            sys.path.insert(0, str(ROOT))
        collect(sources, args.cache, args.max_requests, browser_fallback=args.browser_fallback,
                max_seconds=args.max_seconds)
    elif args.command == "import-snapshot":
        if not all((args.snapshot, args.snapshot_url, args.snapshot_date)):
            parser.error("Snapshot path, attribution URL and date are required")
        report = import_snapshot(args.snapshot, args.cache, args.snapshot_url, args.snapshot_date)
        print(source_names.dumps(report))
    else:
        reports = []
        index = TitleIndex.load(args.cache)
        holders = catalog_holders(index, args.data_dir)
        for source in sources:
            report = record_catalog(source, args.data_dir, args.state_dir, args.cache, args.rebuild, index, holders)
            reports.append(report)
            print(f"{source}: {report['matched']} confirmed of {report['rows']}, {report['restored']} machine titles restored, "
                  f"{report['pending']} left to translate", flush=True)
            if args.report:
                atomic_json(args.report, reports)


if __name__ == "__main__":
    main()
