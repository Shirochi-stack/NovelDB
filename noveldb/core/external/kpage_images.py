"""Chapter images downloaded while the chapter lists are still being read."""
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from html import unescape
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Lock, local

import requests

# The export step's own image requests carry this (its sessions are made after the chapters).
_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/120.0.0.0 Safari/537.36")


class ProfileSessions:
    """Image requests signed in as one browser profile.

    A ticket opens a chapter for the account that used it, and the site serves the chapter's
    images to that account only. So the images of a chapter another profile unlocked are asked
    for with that profile's cookies, whichever profile the rest of the download uses. Made
    while that profile's browser is the one in use.
    """

    def __init__(self, job, book_url):
        with requests.Session() as template:
            job._copy_browser_cookies_to_session(template, quiet=True)
            self._cookies = template.cookies.copy()
        self._referer = book_url or ""
        self._local = local()
        self._lock = Lock()
        self._sessions = []

    def __bool__(self):
        return len(self._cookies) > 0

    def session(self):
        """This thread's session (a requests session is not shared between threads here)."""
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = requests.Session()
            session.headers["Referer"] = self._referer
            session.cookies.update(self._cookies)
            with self._lock:
                self._sessions.append(session)
        return session

    def close(self):
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()


class _Chapter:
    """One offered chapter's images: counted as they finish, for its line in the log."""

    def __init__(self, name, total):
        self.name = name
        self.left = total
        self.total = total
        self.arrived = 0
        self.size = 0
        self.began = time.time()


class ImageLookahead:
    """Downloads a chapter's images as soon as the chapter is read.

    The export step used to ask for every image only after the last chapter was read: a long book
    waited through all its chapter lists before its first image. Here each chapter's images start at
    once, on `workers` requests of their own, and wait in a temporary folder; the export step takes
    them from there (`read`, through `_download_image_python`) and asks the site itself only for what
    was not started or failed here.

    Images are kept on disk, not in memory, so a whole book fits. With less than `reserve` bytes free
    where they are kept, the rest wait for the export step (which stores them compressed).
    """

    def __init__(self, job, book_url, workers, agent=None, reserve=1024 * 1024 * 1024):
        self._stack = ExitStack()
        self._futures = {}
        self._job = job
        self._reserve = reserve
        self._sessions = Queue()
        self._lock = Lock()
        self._offers = Lock()
        self._full = False
        self.started = 0
        try:
            self._folder = Path(self._stack.enter_context(TemporaryDirectory(prefix="nd-chapter-images-")))
            # Made here, on the download's own thread: the browser's cookies are read from it.
            with requests.Session() as template:
                job._copy_browser_cookies_to_session(template)
                for _ in range(workers):
                    session = self._stack.enter_context(requests.Session())
                    session.headers["Referer"] = book_url
                    if agent:
                        session.headers["User-Agent"] = agent
                    session.cookies.update(template.cookies)
                    self._sessions.put(session)
            self._pool = self._stack.enter_context(ThreadPoolExecutor(max_workers=workers))
        except Exception:
            self._stack.close()
            raise

    def _room(self):
        if self._full:
            return False
        try:
            free = shutil.disk_usage(self._folder).free
        except OSError:
            return True
        if free < self._reserve:
            self._full = True
            self._job._log("  Little disk space is left for images: the rest download during export.")
        return not self._full

    def offer(self, chapter):
        """A chapter that was just read: the images nobody holds yet start downloading."""
        # The job's reading of a chapter's images: a comic's pages may be in its text only.
        images = getattr(self._job, "_chapter_images", lambda result: result.get("images") or [])(chapter)
        wanted = {}
        for image in images:
            url = unescape(image.get("url") or "").strip()
            if not url or url in self._futures or url in wanted or image.get("data") or image.get("candidates"):
                continue
            wanted[url] = image.get("_cookies") or chapter.get("_imageCookies")
        if not wanted or not self._room():
            return
        group = _Chapter(chapter.get("chapterName") or "", len(wanted))
        for url, cookies in wanted.items():
            with self._offers:  # chapters are read on several threads
                if url in self._futures:  # the same address again (in this chapter, or another one's)
                    future = None
                else:
                    target = self._folder / str(len(self._futures))
                    future = self._futures[url] = self._pool.submit(self._fetch, url, cookies, target)
                    self.started += 1
            if future is None:
                self._finished(group, None)
            else:
                future.add_done_callback(lambda done, group=group: self._finished(group, done))

    def _finished(self, group, future):
        """Counts one image of a chapter; the chapter's line is written when its last one ends."""
        size = 0
        if future is not None and not future.cancelled() and future.exception() is None:
            path = future.result()
            if path is not None:
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
        with self._lock:
            group.left -= 1
            if size:
                group.arrived += 1
                group.size += size
            last = group.left == 0
        if not last or not group.arrived or self._job._download_cancelled:
            return
        megabytes = group.size / 1048576
        took = max(time.time() - group.began, 0.001)
        missing = group.total - group.arrived
        self._job._log(
            f"  Images of {group.name or 'a chapter'}: {group.arrived} downloaded "
            f"({megabytes:.1f} MB, {megabytes / took:.1f} MB/s)"
            + (f"; {missing} left for the export" if missing else ""))

    def _fetch(self, url, cookies, target):
        if self._job._download_cancelled:
            return None
        session = self._sessions.get()
        try:
            data = self._job._download_image_python(
                url, session=session, request_cookies=cookies, log_success=False, max_retries=1, use_prefetch=False)
            if data:
                target.write_bytes(data)
                return target
        except OSError:
            pass
        finally:
            self._sessions.put(session)
        return None

    def read(self, url):
        future = self._futures.get(url)
        if future is not None:
            try:
                path = future.result()
            except Exception:
                return None
            if path is not None:
                try:
                    return path.read_bytes()
                except OSError:
                    pass
        return None

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._stack.close()


# The lookahead began with kpage alone.
KpageImages = ImageLookahead
