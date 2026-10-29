"""One queued external-source download, run by `ExternalPipelineMixin` without a window.

`run(scraper)` must execute on the `BrowserWorker` thread. Options come from a settings
snapshot; the pipeline's `_msg_queue` messages become `JobEvents` through `MsgQueueAdapter`.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from noveldb.core.events import CancelToken, JobEvents, MsgQueueAdapter
from noveldb.core.external.pipeline import ExternalPipelineMixin
from noveldb.core.settings.legacy_vars import external_values
from noveldb.core.settings.model import AppSettings


# Sources whose scraper paths need no started Playwright context (as in _do_fetch_and_download).
_NATIVE_PREDICATES = (
    "is_ntk_novel", "is_qdn", "is_yeduji", "is_1qxs", "is_69shuba", "is_floo", "is_fenrir",
    "is_maru", "is_xiyuwx", "is_global_npia", "is_rbooks", "is_kobo", "is_mpia", "is_jara",
    "is_nweb_series", "is_nweb_novel", "is_npia",
)


def needs_browser_start(scraper, url: str) -> bool:
    """True when `url` uses the generic browser path and the shared scraper has not started one."""
    if getattr(scraper, "_context", None) is not None:
        return False
    for name in _NATIVE_PREDICATES:
        check = getattr(scraper, name, None)
        if check is not None and check(url):
            return False
    return True


class ExternalJob(ExternalPipelineMixin):
    def __init__(
        self,
        url: str,
        settings: AppSettings,
        source: Optional[str] = None,
        events: Optional[JobEvents] = None,
        cancel: Optional[CancelToken] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        for name, value in external_values(settings, url, source, output_dir).items():
            setattr(self, name, value)
        self.events = events or JobEvents()
        self.cancel_token = cancel or CancelToken()
        self._msg_queue = MsgQueueAdapter(self.events)
        self._work_queue = None
        self._scraper = None
        self._book_data = None
        self._chapter_results = []
        self._downloading = True
        self._download_cancelled = False
        self._active_output_formats = self._selected_output_formats()
        self._active_separate_txt = bool(self._var_separate_txt.get())
        self._active_generate_on_stop = bool(self._var_generate_on_stop.get())
        self._start_time = time.time()

    def stop(self) -> None:
        self._downloading = False
        self._download_cancelled = True
        if self._scraper is not None:
            self._scraper._stop_requested = True

    def run(self, scraper) -> dict:
        """Fetch and download the book with `scraper` (call on the browser worker thread)."""
        self._scraper = scraper
        refresh = getattr(scraper, "refresh_net_timeouts", None)
        if callable(refresh):
            refresh()  # a shared browser context keeps the Page load wait it started with
        done = threading.Event()
        watcher = threading.Thread(target=self._watch_cancel, args=(done,), daemon=True)
        watcher.start()
        try:
            # The dialog started its browser when it created the scraper; the shared one is
            # created lazily by the browser worker, so the first generic-site job starts it here.
            url = self._url_var.get()
            if needs_browser_start(scraper, url):
                scraper.prepare_download_browser()
                scraper.start()
            interval, interval_max = self._get_interval_range()
            threads = max(1, int(self._var_ext_threads.get()))
            self._do_fetch_and_download(
                self._url_var.get(), interval, threads, bool(self._var_skip_paid.get()),
                self._get_retry_passes(), interval_max,
            )
        finally:
            done.set()
        return self.queue_result()

    def _watch_cancel(self, done: threading.Event) -> None:
        while not done.is_set():
            if self.cancel_token.wait(0.25):
                self.stop()
                return

    def queue_result(self) -> dict:
        messages = self._msg_queue.messages
        outputs, errors, title = [], [], ""
        prefix = "✅ Saved: "
        for kind, data in messages:
            if kind == "log" and str(data).startswith(prefix):
                outputs.append(str(data)[len(prefix):].strip())
            elif kind == "error":
                errors.append(str(data))
            elif kind == "book_parsed" and isinstance(data, dict):
                title = data.get("bookname") or title
        cancelled = self._download_cancelled
        ok = bool(outputs) and not cancelled
        return {
            "ok": ok,
            "cancelled": cancelled and not outputs,
            "title": title,
            "outputs": outputs,
            "warnings": [] if not errors or not ok else errors[-3:],
            "error": "" if ok else (errors[-1] if errors else ("Stopped" if cancelled else "No files were written")),
            "status": "Done" if ok else "",
        }
