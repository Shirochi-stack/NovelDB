"""The single thread that owns the external scraper.

Playwright's sync API only works on the thread that started it, so every external job, the
Enter Browser window and the sfc sign-in run here, one at a time, sharing one `ExternalScraper`
(created lazily on this thread). Each submitted call receives the scraper; while it runs, the
scraper's log lines go to that call's log target.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from typing import Callable, Optional


class BrowserWorker:
    def __init__(self, scraper_factory: Optional[Callable] = None, log: Optional[Callable[[str], None]] = None):
        self._factory = scraper_factory
        self._default_log = log or (lambda text: None)
        self._log_target: Optional[Callable[[str], None]] = None
        self._scraper = None
        self._queue: "queue.Queue" = queue.Queue()
        self._busy = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="browser-worker", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def _log(self, text: str) -> None:
        (self._log_target or self._default_log)(text)

    def _scraper_here(self):
        assert threading.current_thread() is self._thread, "the scraper belongs to the browser worker thread"
        if self._scraper is None:
            factory = self._factory
            if factory is None:
                from external_scraper import ExternalScraper as factory
            self._scraper = factory(logger=self._log)
        return self._scraper

    def submit(self, call: Callable, log: Optional[Callable[[str], None]] = None) -> Future:
        """Run `call(scraper)` on the worker thread; the Future holds its result."""
        future: Future = Future()
        self._queue.put((call, log, future))
        return future

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            call, log, future = item
            if not future.set_running_or_notify_cancel():
                continue
            self._busy.set()
            self._log_target = log
            try:
                future.set_result(call(self._scraper_here()))
            except BaseException as exc:  # reported to the caller through the Future
                future.set_exception(exc)
            finally:
                self._log_target = None
                self._busy.clear()

    def shutdown(self, timeout: float = 3.0) -> None:
        """Close the scraper's browsers (on the worker thread) and stop the thread."""
        def cleanup(scraper):
            try:
                scraper.cleanup()
            except Exception:
                pass

        if self._scraper is not None:
            done = self.submit(cleanup)
            try:
                done.result(timeout=timeout)
            except Exception:
                pass
        self._queue.put(None)
        self._thread.join(timeout)
