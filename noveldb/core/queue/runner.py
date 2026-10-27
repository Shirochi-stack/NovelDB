"""Starts queued downloads, a few at a time.

`max_concurrent` (1-3) caps how many run together, and each engine has its own cap: one native
npia download (the old window never ran two, and the server rate-limits) and one browser job
(the external scraper owns a single browser worker). Jobs restored after a restart come back
paused, and ones that were running come back as Interrupted with Retry available.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from noveldb.core.events import CancelToken
from noveldb.core.queue.model import (
    CANCELLED,
    DONE,
    FAILED,
    FINISHED,
    INTERRUPTED,
    QUEUED,
    RUNNING,
    Job,
)

Executor = Callable[[Job, CancelToken], Optional[dict]]

DEFAULT_ENGINE_LIMITS = {"npia_native": 1, "browser": 1}


class JobCancelled(Exception):
    """Raised by an executor that stopped because its job was cancelled."""


class QueueRunner:
    def __init__(
        self,
        executor: Executor,
        persist_path: Optional[os.PathLike | str] = None,
        max_concurrent: int = 1,
        engine_limits: Optional[dict] = None,
        on_change: Optional[Callable[[Job], None]] = None,
    ) -> None:
        self._executor = executor
        self._path = Path(persist_path) if persist_path else None
        self._max = self._clamp(max_concurrent)
        self._limits = dict(engine_limits or DEFAULT_ENGINE_LIMITS)
        self._on_change = on_change
        self._lock = threading.RLock()
        self._jobs: list[Job] = []
        self._tokens: dict[str, CancelToken] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._idle = threading.Condition(self._lock)
        self.paused = False
        self._restore()

    # -- state ---------------------------------------------------------------

    @staticmethod
    def _clamp(value: int) -> int:
        return max(1, min(3, int(value)))

    @property
    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return next((job for job in self._jobs if job.id == job_id), None)

    def running(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs if job.state == RUNNING]

    def set_max_concurrent(self, value: int) -> None:
        with self._lock:
            self._max = self._clamp(value)
        self._schedule()

    # -- commands ------------------------------------------------------------

    def submit(self, jobs: Iterable[Job]) -> list[Job]:
        added = list(jobs)
        with self._lock:
            self._jobs.extend(added)
        for job in added:
            self._changed(job)
        self._save()
        self._schedule()
        return added

    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = self.job(job_id)
            if job is None:
                return
            if job.state == QUEUED:
                job.state = CANCELLED
                job.status = "Cancelled"
                job.finished_at = time.time()
            elif job.state == RUNNING:
                job.status = "Stopping..."
                token = self._tokens.get(job_id)
                if token:
                    token.cancel()
        self._changed(job)
        self._save()

    def cancel_all(self) -> None:
        for job in self.jobs:
            if job.active:
                self.cancel(job.id)

    def retry(self, job_id: str) -> None:
        with self._lock:
            job = self.job(job_id)
            if job is None or job.state not in FINISHED:
                return
            job.state, job.value, job.total = QUEUED, 0, 0
            job.info = job.error = job.status = ""
            job.finished_at = None
        self._changed(job)
        self._save()
        self._schedule()

    def remove(self, job_id: str) -> None:
        with self._lock:
            job = self.job(job_id)
            if job is None or job.state == RUNNING:
                return
            self._jobs.remove(job)
        self._save()

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = [job for job in self._jobs if job.state not in FINISHED]
        self._save()

    def pause(self) -> None:
        with self._lock:
            self.paused = True

    def resume(self) -> None:
        with self._lock:
            self.paused = False
        self._schedule()

    def update(self, job: Job, **changes) -> None:
        """Called by executors to report progress (any thread)."""
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
        self._changed(job)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Block until nothing is running or queued (tests and shutdown)."""
        deadline = time.time() + timeout
        with self._idle:
            while any(job.state == RUNNING for job in self._jobs) or (
                not self.paused and any(job.state == QUEUED for job in self._jobs)
            ):
                left = deadline - time.time()
                if left <= 0:
                    return False
                self._idle.wait(left)
        return True

    # -- scheduling ------------------------------------------------------------

    def _schedule(self) -> None:
        to_start = []
        with self._lock:
            if self.paused:
                return
            running = [job for job in self._jobs if job.state == RUNNING]
            for job in self._jobs:
                if len(running) + len(to_start) >= self._max:
                    break
                if job.state != QUEUED:
                    continue
                busy = sum(1 for other in running + to_start if other.engine == job.engine)
                if busy >= self._limits.get(job.engine, 1):
                    continue
                job.state = RUNNING
                job.status = "Starting..."
                to_start.append(job)
                self._tokens[job.id] = CancelToken()
        for job in to_start:
            thread = threading.Thread(target=self._run, args=(job,), name=f"job-{job.id}", daemon=True)
            self._threads[job.id] = thread
            self._changed(job)
            thread.start()
        if to_start:
            self._save()

    def _run(self, job: Job) -> None:
        token = self._tokens[job.id]
        try:
            result = self._executor(job, token) or {}
            if token.cancelled or result.get("cancelled"):
                state, status = CANCELLED, "Stopped"
            elif result.get("ok", True):
                state, status = DONE, result.get("status") or "Done"
            else:
                state, status = FAILED, result.get("error") or "Failed"
            with self._lock:
                job.outputs = list(result.get("outputs") or job.outputs)
                job.warnings = list(result.get("warnings") or job.warnings)
                if result.get("title"):
                    job.title = result["title"]
                if state == FAILED:
                    job.error = status
        except JobCancelled:
            state, status = CANCELLED, "Stopped"
        except Exception as exc:  # the job failed; keep the queue going
            state, status = FAILED, f"{type(exc).__name__}: {exc}"
            with self._lock:
                job.error = status
        with self._idle:
            job.state, job.status = state, status
            job.finished_at = time.time()
            self._tokens.pop(job.id, None)
            self._threads.pop(job.id, None)
            self._idle.notify_all()
        self._changed(job)
        self._save()
        self._schedule()
        with self._idle:
            self._idle.notify_all()

    # -- persistence -----------------------------------------------------------

    def _changed(self, job: Job) -> None:
        if self._on_change is not None:
            try:
                self._on_change(job)
            except Exception:
                pass

    def _save(self) -> None:
        if self._path is None:
            return
        with self._lock:
            data = [job.to_dict() for job in self._jobs]
        try:
            from noveldb.core.settings.store import write_raw

            write_raw({"jobs": data}, self._path)
        except Exception:
            pass

    def _restore(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        jobs = [Job.from_dict(item) for item in data.get("jobs", []) if isinstance(item, dict)]
        self._jobs = jobs
        if any(job.state == QUEUED for job in jobs):
            self.paused = True

    @property
    def interrupted(self) -> list[Job]:
        return [job for job in self.jobs if job.state == INTERRUPTED]

    @property
    def done(self) -> list[Job]:
        return [job for job in self.jobs if job.state == DONE]

    @property
    def failed(self) -> list[Job]:
        return [job for job in self.jobs if job.state == FAILED]
