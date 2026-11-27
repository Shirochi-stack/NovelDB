"""LINE Webtoons through its mobile app API: the episode list, then each episode's images.

The desktop webtoons.com pages only show a window of recent episodes, so the built-in generic
engine cannot reach older ones, and fast-pass / paid episodes never appear there at all. The app
API lists every episode of a title and hands back each episode's panel images directly, so this
native engine reads a whole series -- including old episodes and the fast-pass episodes the account
has unlocked when a webtoons session cookie is saved (Tools > Enter App (webtoons)).

The panel images are plain CDN files that do not expire, so (unlike bilicomic) they are not
re-signed: a chapter's pages are fetched here, as the chapter is read, into temporary files the
saver reads (``file:`` data). The Catalog reader (``reading``) takes the addresses as they are.

An episode the account has not unlocked is reported as locked. Nothing is bought here.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import weakref
from html import escape

# A chapter whose images are still coming in says so every few seconds.
PROGRESS_SECONDS = 3.0
# Seconds to connect, then between two pieces of a page.
PAGE_TIMEOUT = (5, 60)

# The app API host, signing key, user agents and the session cookie name, kept out of source as
# plaintext per the repository's naming policy (decoded at runtime; not a security measure). This
# is the same wire contract the desktop app's webtoons login uses.
_BLOB = ("eyJhcGkiOiJodHRwczovL2dsb2JhbC5hcGlzLm5hdmVyLmNvbS9saW5lV2VidG9vbi93ZWJ0b29uLyIs"
         "InRpbWUiOiJodHRwczovL2dsb2JhbC5hcGlzLm5hdmVyLmNvbS9jdXJyZW50VGltZSIsImtleSI6Imd"
         "VdFB6SkZaY2g0WnlBR3ZpaXlIOTRQOTlsUTNwRmRSVHdwSldEbFNHRmZ3Z3ByNnNlczVBTE94V0hPSV"
         "Q3UjEiLCJ1YSI6Im5BcHBzIChBbmRyb2lkIDEzOyBsaW5ld2VidG9vbjsgMi4xMi4yKSIsImxvZ2lud"
         "WEiOiJBbmRyb2lkLzkgTW9kZWwvU00tTjk3NUYgY29tLm5hdmVyLmxpbmV3ZWJ0b29uLzIuMTIuNSgy"
         "MTIwNTAwLHVpZDoxMDA1OSkgTmVvSWRTaWduSW5Nb2QvMC4xLjEyIiwic2VzIjoiTkVPX1NFUyJ9")
_CFG = None

# The CDN that serves the panel paths without a Referer; image urls in the API are host-relative.
_IMG_HOST = "https://swebtoon-phinf.pstatic.net"
_COMMON = "serviceZone=GLOBAL&language=en&platform=APP_ANDROID&locale=en"
_HOSTS = ("webtoons.com", "www.webtoons.com", "m.webtoons.com")


def _cfg():
    global _CFG
    if _CFG is None:
        _CFG = json.loads(base64.b64decode(_BLOB).decode("utf-8"))
    return _CFG


def route(url):
    """(titleNo, episodeNo or None) for a webtoons.com list/viewer URL, else None."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _HOSTS:
        return None
    query = urllib.parse.parse_qs(parts.query)

    def number(name):
        try:
            return int(query.get(name, [""])[0])
        except (TypeError, ValueError):
            return None

    title_no = number("title_no")
    if not title_no or title_no <= 0:
        return None
    return title_no, number("episode_no")


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
    return ""


class Locked(RuntimeError):
    """An episode the account in use has not unlocked (the downloader reports it as locked)."""

    locked = True


def _author(info):
    names = [str(info.get(key) or "").strip()
             for key in ("writingAuthorName", "pictureAuthorName")]
    seen = [name for name in dict.fromkeys(names) if name]
    return ", ".join(seen)


class WebtoonsEngine:
    label = "Webtoons app API"
    knows = staticmethod(knows)

    def __init__(self, fetch, log=lambda _: None, stopped=lambda: False, reading=lambda: False,
                 cookie=lambda: "", workers=8):
        self.fetch = fetch
        self.log = log
        self.stopped = stopped
        self.reading = reading  # the Catalog reader shows pages at once: it takes their addresses
        self._cookie = cookie
        self.workers = workers
        self._lock = threading.Lock()
        self._title = None
        self._folder = None
        self._pages = None

    # -- the API ---------------------------------------------------------------------------------

    @staticmethod
    def _text(result):
        if not result:
            return ""
        return result.get("text") or (result.get("content") or b"").decode("utf-8", "replace")

    def _server_time(self):
        """The app API's own clock (raw bytes); both msgpad and the signature use it verbatim."""
        cfg = _cfg()
        result = self.fetch(cfg["time"], headers={"User-Agent": cfg["ua"]}, binary=True)
        return (result or {}).get("content") or b""

    def _sign(self, url, time_bytes):
        """Append the app API's msgpad/md signature (HMAC-SHA1 of the url's first 255 bytes + time)."""
        cfg = _cfg()
        digest = hmac.new(cfg["key"].encode("utf-8"),
                          url.encode("utf-8")[:255] + time_bytes, hashlib.sha1).digest()
        md = base64.b64encode(digest).decode("ascii")
        sep = "&" if "?" in url else "?"
        return (f"{url}{sep}msgpad={urllib.parse.quote(time_bytes.decode('ascii'))}"
                f"&md={urllib.parse.quote(md)}")

    def _api(self, path, allow_empty=False):
        """GET a signed app-API endpoint and return its ``message.result`` object. A locked episode
        answers with no result and no error; `allow_empty` returns None for it instead of raising."""
        cfg = _cfg()
        url = cfg["api"] + path
        time_bytes = self._server_time()
        if not time_bytes:
            raise RuntimeError("the app API clock did not answer")
        result = self.fetch(self._sign(url, time_bytes),
                            headers={"User-Agent": cfg["ua"]}, cookie=self._cookie() or "")
        if not result:
            raise RuntimeError("the app API did not answer")
        try:
            reply = json.loads(self._text(result))
        except ValueError:
            raise RuntimeError("the app API answered without JSON (the signing may have changed)")
        message = reply.get("message") if isinstance(reply, dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("the app API answered with no message")
        data = message.get("result")
        if data is None:
            if allow_empty:
                return None
            error = message.get("error") or {}
            raise RuntimeError(error.get("errorMessage") or "the app API returned no result")
        return data

    def _episode_rows(self, title_no):
        """The free (SERVICE) episodes keyed by episodeNo, and the title's TOTAL episode count.

        The app's episode list only returns the free episodes; fast-pass/paid episodes are left out
        of it but counted in ``totalCountInTitle``, so the total is what the download must span.
        """
        episodes, service_total, total, start = {}, 0, 0, 1
        for _ in range(200):  # safety bound on pages
            if self.stopped():
                break
            data = self._api(
                f"episodeList.json?v=5&titleNo={title_no}&startIndex={start}&pageSize=100&{_COMMON}")
            block = data.get("episodeList") or {}
            batch = block.get("episode") or []
            service_total = block.get("totalServiceEpisodeCount") or service_total
            total = block.get("totalCountInTitle") or total
            if not batch:
                break
            for ep in batch:
                number = ep.get("episodeNo") if isinstance(ep, dict) else None
                if isinstance(number, int) and str(ep.get("serviceStatus")) == "SERVICE":
                    episodes[number] = ep
            start += len(batch)
            if (service_total and len(episodes) >= service_total) or len(batch) < 100:
                break
        return episodes, int(total or 0)

    # -- the book --------------------------------------------------------------------------------

    def parse_book(self, url):
        routed = route(url)
        if not routed:
            return None
        title_no = routed[0]
        try:
            info = self._api(f"titleInfo.json?titleNo={title_no}&{_COMMON}").get("titleInfo") or {}
        except Exception:
            info = {}
        free_rows, total = self._episode_rows(title_no)
        highest = max(free_rows) if free_rows else 0
        total = max(total, highest, int(info.get("totalEpisodeCount") or 0))
        if total <= 0:
            return None
        book_url = f"https://www.webtoons.com/en/list?title_no={title_no}"
        chapters, fastpass = [], 0
        for episode_no in range(1, total + 1):
            ep = free_rows.get(episode_no)
            if ep is not None:
                name = str(ep.get("episodeTitle") or "").strip() or f"Episode {episode_no}"
                paid = False  # in the free service list
            else:
                # Beyond the free list: a fast-pass/paid episode. Still listed and attempted, so the
                # ones the saved session has unlocked download; the rest are reported locked.
                name = f"Episode {episode_no}"
                paid = True
                fastpass += 1
            chapters.append({
                "url": f"https://www.webtoons.com/en/viewer?title_no={title_no}&episode_no={episode_no}",
                "name": name, "number": episode_no, "bookUrl": book_url,
                "isVIP": paid, "isPaid": paid, "isAccessible": True,
            })
        with self._lock:
            if self._title != title_no:
                self._clear()
                self._title = title_no
        tags = []
        genre = info.get("genreInfo") or {}
        if isinstance(genre, dict) and genre.get("name"):
            tags.append(str(genre["name"]))
        warnings = []
        if fastpass:
            warnings.append(
                f"{fastpass} of {len(chapters)} episodes are fast-pass/paid. "
                'Turn off "Skip paid" to request them with your saved app session.')
        cover = info.get("thumbnailImageUrl") or info.get("thumbnail") or ""
        return {
            "bookUrl": book_url,
            "bookname": str(info.get("titleName") or info.get("title") or f"Webtoon {title_no}"),
            "author": _author(info),
            "introduction": str(info.get("synopsis") or info.get("description") or ""),
            "coverUrl": (_IMG_HOST + cover) if isinstance(cover, str) and cover.startswith("/") else "",
            "tags": tags,
            "chapters": chapters, "chapterCount": len(chapters), "engine": "webtoons",
            "ruleName": self.label, "warnings": warnings,
        }

    # -- a chapter -------------------------------------------------------------------------------

    def _image_addresses(self, title_no, episode_no):
        data = self._api(
            f"episodeInfo.json?v=5&titleNo={title_no}&episodeNo={episode_no}&{_COMMON}",
            allow_empty=True)
        info = (data or {}).get("episodeInfo") or {}
        images = info.get("imageInfo") or []
        addresses = []
        for item in sorted((i for i in images if isinstance(i, dict)),
                           key=lambda i: i.get("sortOrder") or 0):
            path = item.get("url")
            if isinstance(path, str) and path.startswith("/"):
                addresses.append(_IMG_HOST + path)
        return addresses, info

    def parse_chapter(self, chapter, book):
        routed = route(chapter.get("url") or "")
        if not routed or not routed[1]:
            return None
        title_no, episode_no = routed
        if chapter.get("isAccessible") is False:
            raise Locked("not unlocked by the account in use")
        addresses, info = self._image_addresses(title_no, episode_no)
        if not addresses:
            # No images: a paid/fast-pass episode the account has not unlocked, or an empty episode.
            if chapter.get("isPaid") or info.get("productInfo") or info.get("product"):
                raise Locked("the API returned no pages for this paid episode with the current session")
            raise RuntimeError("the episode returned no images")
        result = {
            "chapterName": chapter.get("name") or "", "contentText": "", "comic": True,
            "imageCount": len(addresses), "imageReferer": "https://www.webtoons.com/", "images": [],
            "contentHtml": "".join(f'<p><img src="{escape(link)}" alt=""/></p>' for link in addresses),
        }
        if not self.reading():
            try:
                began = time.time()
                result["images"] = self._save_pages(
                    episode_no, addresses, result["chapterName"] or str(episode_no))
                size = sum(os.path.getsize(page["data"][len("file:"):]) for page in result["images"])
                took = max(time.time() - began, 0.001)
                self.log(f"  [Webtoons] {result['chapterName'] or episode_no}: "
                         f"{len(result['images'])} pages downloaded "
                         f"({size / 1048576:.1f} MB, {size / 1048576 / took:.1f} MB/s)")
            except Exception:
                if self.stopped():
                    return None  # Stop was pressed: not a failure to report
                raise
        return result

    # -- the pages -------------------------------------------------------------------------------

    def _session(self):
        with self._lock:
            if self._pages is None:
                import requests
                from requests.adapters import HTTPAdapter

                session = requests.Session()
                session.headers.update({
                    "User-Agent": _cfg()["ua"], "Accept": "*/*",
                    "Referer": "https://www.webtoons.com/"})
                adapter = HTTPAdapter(pool_connections=2, pool_maxsize=max(8, self.workers * 2),
                                      pool_block=True)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                self._pages = session
            return self._pages

    def _http_page(self, url):
        """One page's bytes, or None (the image CDN needs no sign-in)."""
        for attempt in (1, 2, 3):
            if self.stopped():
                return None
            try:
                response = self._session().get(url, timeout=PAGE_TIMEOUT)
                if response.status_code == 200 and response.content:
                    return response.content
                if response.status_code in (401, 403, 404):
                    return None
            except Exception:
                pass
            if attempt < 3:
                time.sleep(attempt)
        return None

    def _pages_folder(self):
        with self._lock:
            if not self._folder or not os.path.isdir(self._folder):
                self._folder = tempfile.mkdtemp(prefix="nd-webtoons-pages-")
                weakref.finalize(self, shutil.rmtree, self._folder, True)
            return self._folder

    def _save_pages(self, episode, addresses, name=""):
        from concurrent.futures import ThreadPoolExecutor

        folder = self._pages_folder()
        began = time.time()
        progress = {"pages": 0, "bytes": 0, "said": began}
        tick = threading.Lock()

        def arrived(size):
            with tick:
                progress["pages"] += 1
                progress["bytes"] += size
                now = time.time()
                if progress["pages"] >= len(addresses) or now - progress["said"] < PROGRESS_SECONDS:
                    return
                progress["said"] = now
                pages, megabytes = progress["pages"], progress["bytes"] / 1048576
            self.log(f"    [Webtoons] {name}: {pages}/{len(addresses)} pages ({megabytes:.1f} MB)")

        def one(item):
            number, url = item
            if self.stopped():
                raise RuntimeError("stopped")
            data = self._http_page(url)
            if not data:
                raise RuntimeError(f"page {number} of {len(addresses)} could not be downloaded")
            kind = image_kind(data) or "jpg"
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
                except Exception as error:
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
            pages, self._pages = self._pages, None
        if pages is not None:
            try:
                pages.close()
            except Exception:
                pass
