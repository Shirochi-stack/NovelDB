"""One queued npia download, run by `NpiaPipelineMixin` without a window.

Each job has its own `DownloaderCore` and option snapshot and shares the app's signed-in
`NpiaAuth`. Interface work goes through `JobEvents` (log, status, progress) and `Prompts` (save
path, confirmation), so the Qt app, tests and the command line all run the same pipeline.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from downloader_core import DownloaderCore
from noveldb.core.events import CancelToken, JobEvents, Prompts, format_eta
from noveldb.core.npia.pipeline import NpiaPipelineMixin
from noveldb.core.settings.legacy_vars import npia_values
from noveldb.core.settings.model import AppSettings

_logger = logging.getLogger("ND")


def _font_mapper(path: str):
    if not path or not os.path.isfile(path):
        return None
    try:
        from font_mapper import FontMapper

        return FontMapper(path)
    except Exception:
        return None


class NpiaJob(NpiaPipelineMixin):
    def __init__(
        self,
        novel_input: str,
        settings: AppSettings,
        auth,
        events: Optional[JobEvents] = None,
        prompts: Optional[Prompts] = None,
        cancel: Optional[CancelToken] = None,
        output_dir: Optional[str] = None,
        downloader_factory=DownloaderCore,
    ) -> None:
        for name, value in npia_values(settings, novel_input, output_dir).items():
            setattr(self, name, value)
        self.events = events or JobEvents()
        self.prompts = prompts or Prompts()
        self.cancel_token = cancel or CancelToken()
        self.auth = auth
        self.downloader = downloader_factory(auth, self.log_message)
        self.font_mapper = _font_mapper(settings.npia.mapping_path)
        self.image_lock = threading.Lock()
        self.image_no = 1
        self.progress_value = 0
        self.progress_total = 0
        self._progress_start_time = None
        self._img_downloaded_count = 0
        self._stop_requested = False
        self._last_download_result = None

    # -- hooks ---------------------------------------------------------------------

    def log_message(self, message):
        _logger.info(message)
        self.events.log(str(message))

    def _ui_status(self, text):
        self.events.status(text)

    def _ui_images(self, count):
        self.events.images(count)

    def _ui_warn(self, title, message, **kwargs):
        self.log_message(f"{title}: {message}")
        self.prompts.warn(title, message)

    def _ui_confirm(self, title, message, **kwargs):
        return self.prompts.confirm(title, message)

    def _ui_ask_save_path(self, **kwargs):
        folder = kwargs.get("initialdir") or ""
        name = kwargs.get("initialfile") or ""
        suggested = os.path.join(folder, name) if folder else name
        return self.prompts.ask_save_path(kwargs.get("title") or "Save", suggested, kwargs.get("defaultextension") or "") or ""

    def _update_progress(self, value=None, total=None, status_text=None):
        if total is not None:
            self.progress_total = total
            if self._progress_start_time is None and total:
                self._progress_start_time = time.time()
        if value is not None:
            self.progress_value = value
        if status_text is not None:
            self.events.status(status_text)
        self.events.progress(
            self.progress_value,
            self.progress_total,
            format_eta(self.progress_value, self.progress_total, self._progress_start_time),
        )

    # -- running -------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_requested = True
        self.downloader.stop_signal = True

    def run(self) -> dict:
        """Download the novel; returns the queue result (ok, title, outputs, warnings, error)."""
        done = threading.Event()
        watcher = threading.Thread(target=self._watch_cancel, args=(done,), daemon=True)
        watcher.start()
        try:
            result = self._download_worker() or {}
        finally:
            done.set()
        return self.queue_result(result, stopped=self._stop_requested)

    def _watch_cancel(self, done: threading.Event) -> None:
        """Turn a queue cancel into the pipeline's stop flags while the download runs."""
        while not done.is_set():
            if self.cancel_token.wait(0.25):
                self.stop()
                return

    @staticmethod
    def queue_result(result: dict, stopped: bool = False) -> dict:
        status = result.get("status", "failed")
        outputs = [p for p in (result.get("output_paths") or {}).values() if p]
        if not outputs and result.get("output_path"):
            outputs = [result["output_path"]]
        warnings = []
        if result.get("failed_chapters"):
            warnings.append(f"{len(result['failed_chapters'])} chapter(s) failed")
        if result.get("blocked_chapters"):
            warnings.append(f"{len(result['blocked_chapters'])} chapter(s) blocked")
        if result.get("image_failures"):
            warnings.append(f"{result['image_failures']} image(s) failed")
        ok = status not in ("failed", "cancelled", "stopped") and not stopped
        return {
            "ok": ok,
            "cancelled": stopped,
            "title": result.get("title") or "",
            "outputs": outputs,
            "warnings": warnings,
            "error": result.get("error") or ("" if ok else status),
            "status": "Done with warnings" if (ok and warnings) else ("Done" if ok else ""),
            "raw": result,
        }
