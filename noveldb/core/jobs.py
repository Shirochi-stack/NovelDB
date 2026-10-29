"""Runs queued jobs: npia lines on the native pipeline, everything else on the browser worker."""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, Optional

from noveldb.core import net_timeouts
from noveldb.core.events import CancelToken, JobEvents, Prompts
from noveldb.core.queue.model import Job
from noveldb.core.settings.model import AppSettings
from noveldb.core.sources import NPIA_NATIVE


class JobDispatcher:
    """The `QueueRunner` executor.

    `settings` returns the current settings (snapshotted per job), `events_for(job)` the events
    sink for one job, and `browser` is the shared `BrowserWorker`.
    """

    def __init__(
        self,
        settings: Callable[[], AppSettings],
        auth,
        browser,
        events_for: Callable[[Job], JobEvents],
        prompts: Optional[Prompts] = None,
        npia_job_factory=None,
        external_job_factory=None,
    ) -> None:
        self._settings = settings
        self._auth = auth
        self._browser = browser
        self._events_for = events_for
        self._prompts = prompts or Prompts()
        if npia_job_factory is None:
            from noveldb.core.npia.job import NpiaJob as npia_job_factory
        if external_job_factory is None:
            from noveldb.core.external.job import ExternalJob as external_job_factory
        self._npia_job = npia_job_factory
        self._external_job = external_job_factory

    def __call__(self, job: Job, token: CancelToken) -> dict:
        settings = self._settings()
        net_timeouts.configure_from(settings)  # the minimum waits this job runs with
        events = self._events_for(job)
        output_dir = job.output_dir or None
        if job.engine == NPIA_NATIVE:
            return self._npia_job(job.input, settings, self._auth, events, self._prompts, token, output_dir).run()
        external = self._external_job(job.input, settings, job.source, events, token, output_dir)
        future = self._browser.submit(external.run, log=external._log)
        while True:
            try:
                return future.result(timeout=0.25)
            except FutureTimeout:
                if token.cancelled:
                    external.stop()
