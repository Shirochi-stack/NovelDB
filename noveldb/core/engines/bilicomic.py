"""Bilibili Manga through its app API: the chapter list, then each chapter's pages.

The site signs every page address for about fifteen minutes, far less than a book takes to
download, and the downloader fetches images only when it builds the book. So a chapter's pages
are fetched here, as the chapter is read, into temporary files the saver reads (`file:` data).
The Catalog reader shows a chapter at once and takes the addresses as they are (`reading`).

The mobile apps run this module too (scripts/python_host), always with `reading`: their own
adapters fetch a chapter's pages from the addresses as soon as it is read.

A chapter the account in use has not bought is reported as locked. Nothing is bought here.
"""

import base64
from html import escape
import json
import os
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import weakref

# A chapter whose images are still coming in says so at every quarter, and this often when a
# quarter takes longer.
PROGRESS_SECONDS = 3.0
# A page request still running after this long gets a second one beside it.
HEDGE_SECONDS = 2.0
# Seconds to connect, then between two pieces of a page. An address of the image host that stays
# silent is given up after two and a half seconds (the next one is tried), not fifteen.
PAGE_TIMEOUT = (2.5, 60)

HOST = "manga.bilibili.com"
BASE = f"https://{HOST}"
FINGERPRINT = "https://api.bilibili.com/x/frontend/finger/spi"
_ROUTE = re.compile(r"^/(?:m/)?(?:detail/)?mc(\d{1,12})(?:/(\d{1,12}))?/?$", re.I)

# The comic API answers the Android app's identity; the web identity is refused (code 99).
APP_AGENT = ("Mozilla/5.0 (Linux; Android 12; SM-G9730) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/126.0.0.0 Safari/537.36")
WEB_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/131.0.0.0 Safari/537.36")
APP_QUERY = {"appkey": "cc8617fd6961e070", "mobi_app": "android_comic", "version": "4.13.0",
             "build": "36413002", "channel": "bilicomic", "platform": "android", "device": "android"}
_HEAD = 20496  # the encrypted start of a protected page; the rest is stored as it is


def route(url):
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() != HOST:
        return None
    return _ROUTE.fullmatch(parts.path)


def knows(url):
    return route(url) is not None


def image_kind(data):
    """The file extension for image bytes, or "" when they are not an image."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    return ""


def decrypt_page(data, url):
    """A page as the site stored it: protected pages ("cpx" in the address) are decoded.

    Layout: 0x01, the length of the image (4 bytes, big-endian), the image with its first 20496
    bytes AES-CBC encrypted, then the key. The IV is bytes 60-76 of the decoded "cpx" value.
    """
    values = parse_qs(urlsplit(url).query).get("cpx")
    if not values or image_kind(data):
        return data
    if len(data) < 5 or data[0] != 1:
        raise RuntimeError("a protected page has an unknown layout")
    end = 5 + int.from_bytes(data[1:5], "big")
    if end <= 5 or end > len(data):
        raise RuntimeError("a protected page has an invalid length")
    try:
        iv = base64.b64decode(values[0] + "=" * (-len(values[0]) % 4))[60:76]
    except ValueError as error:
        raise RuntimeError(f"a protected page's key could not be read ({error})") from None
    key, content = data[end:], data[5:end]
    head = content[:_HEAD]
    if len(iv) != 16 or len(key) not in (16, 24, 32) or not head or len(head) % 16:
        raise RuntimeError("a protected page has an invalid key")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(head) + decryptor.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    restored = plain + content[_HEAD:]
    if not image_kind(restored):
        raise RuntimeError("a protected page did not decode to an image")
    return restored


class ApiError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"{message or 'no message'} (code {code})")
        self.code = str(code)
        self.locked = self.code == "1" and "buy" in str(message).lower()


class Locked(RuntimeError):
    """A chapter the account in use has not bought (the downloader reports it as locked)."""

    locked = True


def _order(row):
    try:
        return float(row.get("ord") or 0), int(row.get("id") or 0)
    except (TypeError, ValueError):
        return 0.0, 0


def _chapter_name(row):
    short, title = str(row.get("short_title") or "").strip(), str(row.get("title") or "").strip()
    if short and title and short != title:
        return f"{short} {title}"
    return title or short or f"Chapter {row.get('ord') or '?'}"


def _https(value):
    try:
        parts = urlsplit(value if isinstance(value, str) else "")
    except ValueError:
        return ""
    return value if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password else ""


class BiliComicEngine:
    label = "Bilibili Manga reader"
    knows = staticmethod(knows)

    def __init__(self, fetch, log=lambda _: None, stopped=lambda: False, reading=lambda: False,
                 download=None, workers=8):
        self.fetch = fetch
        self.log = log
        self.stopped = stopped
        self.reading = reading  # the Catalog reader shows pages at once: it takes their addresses
        self.workers = workers
        self._download = download or self._http_page
        self._lock = threading.Lock()
        self._fingerprint = None
        self._folder = None
        self._comic = None
        self._local = threading.local()
        self._race = None
        self._pages = None

    # -- the API ---------------------------------------------------------------------------------

    def _cookie(self):
        """The device cookies the API wants with every request (it answers code 99 without them)."""
        with self._lock:
            if self._fingerprint is None:
                self._fingerprint = ""
                result = self.fetch(FINGERPRINT, headers={"User-Agent": APP_AGENT}, referer=BASE + "/")
                try:
                    data = json.loads(self._text(result)).get("data") or {}
                except (ValueError, AttributeError):
                    data = {}
                parts = [f"{name}={data[key]}" for key, name in (("b_3", "buvid3"), ("b_4", "buvid4"))
                         if isinstance(data.get(key), str) and re.fullmatch(r"[\w.+/=-]{8,128}", data[key])]
                self._fingerprint = "; ".join(parts)
            return self._fingerprint

    @staticmethod
    def _text(result):
        if not result:
            return ""
        return result.get("text") or (result.get("content") or b"").decode("utf-8", "replace")

    def _api(self, method, payload):
        reply, status = None, 0
        for attempt in (1, 2):
            query = urlencode({"ts": int(time.time()), **APP_QUERY})
            result = self.fetch(f"{BASE}/twirp/comic.v1.Comic/{method}?{query}", method="POST",
                                data=json.dumps(payload),
                                headers={"User-Agent": APP_AGENT, "Accept": "application/json, text/plain, */*",
                                         "Content-Type": "application/json"},
                                cookie=self._cookie())
            if not result:
                raise RuntimeError("the site did not answer")
            status = result.get("status") or 0
            try:
                reply = json.loads(self._text(result))
            except ValueError:
                reply = None
            if not isinstance(reply, dict):
                raise RuntimeError(f"the site answered {status} without data")
            if str(reply.get("code")) == "99" and attempt == 1:
                with self._lock:
                    self._fingerprint = None  # a refused device cookie: a new one, once
                continue
            break
        code = str(reply.get("code"))
        if code != "0":
            raise ApiError(code, reply.get("msg") or reply.get("message") or "")
        if reply.get("data") is None:
            raise RuntimeError(f"{method} returned no data")
        return reply["data"]

    # -- the book --------------------------------------------------------------------------------

    def parse_book(self, url):
        match = route(url)
        if not match:
            return None
        comic = int(match[1])
        book_url = f"{BASE}/detail/mc{comic}"
        detail = self._api("ComicDetail", {"comicId": comic})
        if not isinstance(detail, dict) or not detail.get("title"):
            return None
        rows = sorted((row for row in detail.get("ep_list") or []
                       if isinstance(row, dict) and str(row.get("id") or "").isdigit()), key=_order)
        chapters, locked = [], 0
        for index, row in enumerate(rows, 1):
            # Locked for the account in use: paid and not bought (a bought chapter is like a free one).
            # The downloaders skip these with "Skip paid", and the phone apps report them as refused.
            closed = bool(row.get("is_locked")) and not row.get("is_in_free")
            locked += closed
            chapters.append({"url": f"{BASE}/mc{comic}/{int(row['id'])}", "name": _chapter_name(row),
                             "number": index, "bookUrl": book_url, "isVIP": closed, "isPaid": closed,
                             "isAccessible": not closed})
        if not chapters:
            return None
        with self._lock:
            if self._comic != comic:
                self._clear()  # another book's pages
                self._comic = comic
        authors = [name.strip() for name in detail.get("author_name") or [] if isinstance(name, str) and name.strip()]
        tags = [name for name in detail.get("styles") or [] if isinstance(name, str)]
        tags += [tag["name"] for tag in detail.get("tags") or [] if isinstance(tag, dict) and isinstance(tag.get("name"), str)]
        warnings = []
        if locked:
            warnings.append(f"{locked} of {len(chapters)} chapters are locked for the account in use (not bought, "
                            "or not signed in). Sign in to the site with Enter Browser to read chapters you bought.")
        return {"bookUrl": book_url, "bookname": str(detail["title"]), "author": ", ".join(authors),
                "introduction": detail.get("evaluate") or detail.get("classic_lines") or "",
                "coverUrl": _https(detail.get("vertical_cover")) or _https(detail.get("square_cover")),
                "tags": list(dict.fromkeys(tag.strip() for tag in tags if tag.strip())),
                "chapters": chapters, "chapterCount": len(chapters), "engine": "bilicomic",
                "ruleName": self.label, "warnings": warnings}

    # -- a chapter -------------------------------------------------------------------------------

    def _page_addresses(self, episode):
        try:
            index = self._api("GetImageIndex", {"epId": episode})
        except ApiError as error:
            if error.locked:
                raise Locked("not bought by the account in use") from None
            raise
        paths = [row["path"] for row in (index.get("images") if isinstance(index, dict) else None) or []
                 if isinstance(row, dict) and isinstance(row.get("path"), str) and row["path"]]
        if not 0 < len(paths) <= 2000:
            raise RuntimeError("the site returned no pages for the chapter")
        # "urls" is a JSON list inside a string.
        tokens = self._api("ImageToken", {"urls": json.dumps(paths, ensure_ascii=False, separators=(",", ":"))})
        if not isinstance(tokens, list) or len(tokens) != len(paths):
            raise RuntimeError("the site returned an incomplete list of page addresses")
        addresses = []
        for item in tokens:
            item = item if isinstance(item, dict) else {}
            address = item.get("complete_url")
            if not address and item.get("url") and item.get("token") is not None:
                address = f"{item['url']}?token={item['token']}"
            addresses.append(_https(address))
        if not all(addresses):
            raise RuntimeError("the site returned an invalid page address")
        return addresses

    def parse_chapter(self, chapter, book):
        match = route(chapter.get("url") or "")
        if not match or not match[2]:
            return None
        comic, episode = int(match[1]), int(match[2])
        address = f"{BASE}/mc{comic}/{episode}"
        if chapter.get("isAccessible") is False:
            # The chapter list of this download already says so: no request that can only be refused.
            raise Locked("not bought by the account in use")
        addresses = self._page_addresses(episode)
        result = {"chapterName": chapter.get("name") or "", "contentText": "", "comic": True,
                  "imageCount": len(addresses), "imageReferer": address, "images": [],
                  "contentHtml": "".join(f'<p><img src="{escape(link)}" alt=""/></p>' for link in addresses)}
        if not self.reading():
            try:
                began = time.time()
                result["images"] = self._save_pages(episode, addresses, result["chapterName"] or str(episode))
                # The pages come in while the other chapters are still read: say so, or the log shows
                # chapter lines only and it looks as if no image is asked for until the last chapter.
                size = sum(os.path.getsize(page["data"][len("file:"):]) for page in result["images"])
                took = max(time.time() - began, 0.001)
                self.log(f"  [Bilibili Manga] {result['chapterName'] or episode}: {len(result['images'])} pages "
                         f"downloaded ({size / 1048576:.1f} MB, {size / 1048576 / took:.1f} MB/s)")
            except Exception:
                if self.stopped():
                    return None  # Stop was pressed: not a failure to report
                raise
        return result

    # -- the pages -------------------------------------------------------------------------------

    def _session(self):
        """The one session every page request shares: a few connections, kept open for the whole book.

        A new connection to the image host often loses its first packets (the same address answers
        in 0.02 s one moment and after 3 s, or not within 6 s, the next). With a session per thread,
        and new threads for every chapter, each chapter opened its own connections and kept running
        into that: its last pages then came many seconds after the rest. Kept connections are opened
        once; `pool_block` makes a thread wait for one instead of opening another.
        """
        with self._lock:
            if self._pages is None:
                import requests
                from requests.adapters import HTTPAdapter

                session = requests.Session()
                session.headers.update({"User-Agent": WEB_AGENT, "Accept": "*/*", "Referer": BASE + "/"})
                adapter = HTTPAdapter(pool_connections=2, pool_maxsize=max(8, self.workers * 2), pool_block=True)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                self._pages = session
            return self._pages

    def _http_page(self, url):
        """One page's bytes, or None (the image host needs no sign-in).

        A request can sit on a connection that is slow to open (see `_session`) while the same page
        comes at once over one that is already open: a chapter of a hundred pages then waited many
        seconds for its last two or three. A request that is still running after `HEDGE_SECONDS` gets
        a second one beside it, and the first answer is taken.
        """
        from concurrent.futures import FIRST_COMPLETED, wait

        pool = self._racers()
        running = {pool.submit(self._request_page, url, False)}
        done, _ = wait(running, timeout=HEDGE_SECONDS)
        if not done and not self.stopped():
            running.add(pool.submit(self._request_page, url, True))
        while running:
            done, running = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
            for finished in done:
                data = finished.result()
                if data:
                    return data
            if self.stopped():
                return None
        return None

    def _racers(self):
        """The threads the page requests run on (a slow one stays here until it ends, out of the way)."""
        from concurrent.futures import ThreadPoolExecutor

        with self._lock:
            if self._race is None:
                self._race = ThreadPoolExecutor(max_workers=max(8, self.workers * 4), thread_name_prefix="nd-bili-page")
            return self._race

    def _request_page(self, url, again):
        """Three tries at one page, over the shared connections. `again`: the second request for a
        page whose first is still running; it takes whichever kept connection is free next, which is
        one that is already open while the first may be waiting for one that is still being opened."""
        for attempt in (1, 2, 3):
            if self.stopped():
                return None
            try:
                response = self._session().get(url, timeout=PAGE_TIMEOUT)
                if response.status_code == 200 and response.content:
                    return response.content
                if response.status_code in (401, 403, 404):
                    return None  # a refused address stays refused
            except Exception:
                pass
            if attempt < 3:
                time.sleep(attempt)
        return None

    def _pages_folder(self):
        with self._lock:
            if not self._folder or not os.path.isdir(self._folder):
                self._folder = tempfile.mkdtemp(prefix="nd-bilicomic-pages-")
                weakref.finalize(self, shutil.rmtree, self._folder, True)
            return self._folder

    def _save_pages(self, episode, addresses, name=""):
        from concurrent.futures import ThreadPoolExecutor  # here: the phones' Python has no threads

        folder = self._pages_folder()
        # Progress while a chapter's pages come in: a line at every quarter of them, and every three
        # seconds when a quarter takes longer, so the log is never silent until the last page.
        began = time.time()
        progress = {"pages": 0, "bytes": 0, "said": began, "quarter": 0}
        tick = threading.Lock()

        def arrived(size):
            with tick:
                progress["pages"] += 1
                progress["bytes"] += size
                now = time.time()
                if progress["pages"] >= len(addresses):
                    return  # the chapter's own line follows
                quarter = progress["pages"] * 4 // len(addresses)
                if quarter <= progress["quarter"] and now - progress["said"] < PROGRESS_SECONDS:
                    return
                progress["said"] = now
                progress["quarter"] = max(quarter, progress["quarter"])
                pages, megabytes = progress["pages"], progress["bytes"] / 1048576
            self.log(f"    [Bilibili Manga] {name}: {pages}/{len(addresses)} pages "
                     f"({megabytes:.1f} MB, {megabytes / max(now - began, 0.001):.1f} MB/s)")

        def one(item):
            number, url = item
            if self.stopped():
                raise RuntimeError("stopped")
            data = self._download(url)
            if not data:
                raise RuntimeError(f"page {number} of {len(addresses)} could not be downloaded")
            data = decrypt_page(data, url)
            kind = image_kind(data)
            if not kind:
                raise RuntimeError(f"page {number} of {len(addresses)} is not an image")
            path = os.path.join(folder, f"{episode}_{number:04d}.{kind}")
            with open(path, "wb") as handle:
                handle.write(data)
            arrived(len(data))
            return {"url": url, "name": f"page_{number:04d}.{kind}", "data": "file:" + path}

        with ThreadPoolExecutor(max_workers=max(1, min(self.workers, len(addresses)))) as pool:
            futures = [pool.submit(one, item) for item in enumerate(addresses, 1)]
            pages, failure = [], None
            for future in futures:
                try:
                    pages.append(future.result())
                except Exception as error:  # the first failure is the chapter's; the rest still finish
                    failure = failure or error
        if failure is not None:
            for page in pages:
                try:
                    os.remove(page["data"][len("file:"):])
                except OSError:
                    pass
            raise failure
        return pages

    def _clear(self):
        folder, self._folder = self._folder, None
        if folder:
            shutil.rmtree(folder, ignore_errors=True)

    def close(self):
        with self._lock:
            self._clear()
            race, self._race = self._race, None
            pages, self._pages = self._pages, None
        if race is not None:
            race.shutdown(wait=False, cancel_futures=True)
        if pages is not None:
            try:
                pages.close()
            except Exception:
                pass
