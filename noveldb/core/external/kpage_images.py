"""Bounded image lookahead for the kpage chapter-list download stage."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from html import unescape
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock

import requests


class KpageImages:
    def __init__(self, job, book_url, workers):
        self._stack = ExitStack()
        self._futures = {}
        self._job = job
        self._sessions = Queue()
        self._slots = BoundedSemaphore(workers * 32)
        self._size_lock = Lock()
        self._bytes = 0
        try:
            self._folder = Path(self._stack.enter_context(TemporaryDirectory(prefix="nd-kpage-images-")))
            with requests.Session() as template:
                job._copy_browser_cookies_to_session(template)
                for _ in range(workers):
                    session = self._stack.enter_context(requests.Session())
                    session.headers["Referer"] = book_url
                    session.cookies.update(template.cookies)
                    self._sessions.put(session)
            self._pool = self._stack.enter_context(ThreadPoolExecutor(max_workers=workers))
        except Exception:
            self._stack.close()
            raise

    def offer(self, chapter):
        for image in chapter.get("images") or []:
            if self._bytes >= 128 * 1024 * 1024:
                break
            url = unescape(image.get("url") or "").strip()
            if not url or url in self._futures or image.get("data") or image.get("candidates"):
                continue
            if not self._slots.acquire(blocking=False):
                break
            cookies = image.get("_cookies") or chapter.get("_imageCookies")
            target = self._folder / str(len(self._futures))
            future = self._pool.submit(self._fetch, url, cookies, target)
            future.add_done_callback(lambda _: self._slots.release())
            self._futures[url] = future

    def _fetch(self, url, cookies, target):
        if self._job._download_cancelled:
            return None
        session = self._sessions.get()
        try:
            data = self._job._download_image_python(
                url, session=session, request_cookies=cookies, log_success=False, max_retries=1, use_prefetch=False)
            if data:
                with self._size_lock:
                    if self._bytes + len(data) > 128 * 1024 * 1024:
                        return None
                    self._bytes += len(data)
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
            path = future.result()
            if path is not None:
                try:
                    return path.read_bytes()
                except OSError:
                    pass
        return None

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._stack.close()
