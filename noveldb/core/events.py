"""How download jobs talk to whatever interface runs them, without importing one.

Jobs run on worker threads. They report through `JobEvents` (log lines, status, progress, book
info, saved files) and ask questions through `Prompts` (save path, confirmation). The Qt
interface implements both with signals; tests and the command line use the plain versions here.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional


class CancelToken:
    """A stop request shared between the interface and a running job."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`, returning True early if the job was cancelled."""
        return self._event.wait(max(0.0, seconds))


def format_eta(value: int, total: int, started: Optional[float], now: Optional[float] = None) -> str:
    """'12/45 (26%) — ETA: 1m 30s', the label both old windows showed."""
    if total <= 0:
        return ""
    pct = int(value / total * 100)
    info = f"{value}/{total} ({pct}%)"
    if started is not None and value > 0:
        elapsed = (now if now is not None else time.time()) - started
        rate = value / max(elapsed, 0.001)
        remaining = total - value
        if rate > 0 and remaining > 0:
            minutes, seconds = divmod(int(remaining / rate), 60)
            info += f" — ETA: {minutes}m {seconds}s" if minutes > 0 else f" — ETA: {seconds}s"
        elif remaining == 0:
            info += " — Done"
    return info


class ProgressTracker:
    """Thread-safe progress counters (the old windows added to them from several threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.value = 0
        self.total = 0
        self.started: Optional[float] = None

    def reset(self, total: int = 0) -> None:
        with self._lock:
            self.value = 0
            self.total = max(0, int(total))
            self.started = time.time() if total else None

    def set(self, value: Optional[int] = None, total: Optional[int] = None) -> tuple[int, int]:
        with self._lock:
            if total is not None:
                self.total = max(0, int(total))
                if self.started is None and self.total:
                    self.started = time.time()
            if value is not None:
                self.value = max(0, int(value))
            return self.value, self.total

    def advance(self, step: int = 1) -> tuple[int, int]:
        with self._lock:
            self.value += step
            return self.value, self.total

    @property
    def percent(self) -> int:
        with self._lock:
            return int(self.value / self.total * 100) if self.total else 0

    def info(self) -> str:
        with self._lock:
            return format_eta(self.value, self.total, self.started)


class JobEvents:
    """What a job reports. Every method may be called from any thread; the default does nothing."""

    def log(self, text: str) -> None: ...
    def status(self, text: str) -> None: ...
    def progress(self, value: int, total: int, info: str) -> None: ...
    def images(self, count: int) -> None: ...
    def book_info(self, info: dict) -> None: ...
    def saved(self, path: str) -> None: ...
    def finished(self, result: dict) -> None: ...


class CallbackEvents(JobEvents):
    """`JobEvents` backed by optional callables (tests, the command line)."""

    def __init__(self, **callbacks: Callable) -> None:
        self._callbacks = callbacks

    def _call(self, name: str, *args) -> None:
        callback = self._callbacks.get(name)
        if callback is not None:
            callback(*args)

    def log(self, text):
        self._call("log", text)

    def status(self, text):
        self._call("status", text)

    def progress(self, value, total, info):
        self._call("progress", value, total, info)

    def images(self, count):
        self._call("images", count)

    def book_info(self, info):
        self._call("book_info", info)

    def saved(self, path):
        self._call("saved", path)

    def finished(self, result):
        self._call("finished", result)


class Prompts:
    """Questions a job may ask. The headless answers keep a job moving without a person."""

    def ask_save_path(self, title: str, suggested: str, extension: str) -> Optional[str]:
        """A path to save to, or None to cancel. Headless: the suggested path."""
        return suggested

    def ask_folder(self, title: str, start: str = "") -> Optional[str]:
        return start or None

    def confirm(self, title: str, text: str, default: bool = False) -> bool:
        return default

    def warn(self, title: str, text: str) -> None:
        return None


SAVED_PREFIX = "✅ Saved: "


class LogPump:
    """Log lines from worker threads, drained in batches by the interface.

    Mirrors the old `_poll_queue` contract: at most `max_items` lines or `budget` seconds per
    drain, so a flood of log lines never freezes the window.
    """

    def __init__(self) -> None:
        self._lines: deque = deque()
        self._lock = threading.Lock()

    def push(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def drain(self, max_items: int = 100, budget: float = 0.01) -> list:
        out: list = []
        deadline = time.perf_counter() + budget
        with self._lock:
            while self._lines and len(out) < max_items:
                out.append(self._lines.popleft())
                if time.perf_counter() >= deadline:
                    break
        return out

    @property
    def backlog(self) -> bool:
        with self._lock:
            return bool(self._lines)


class MsgQueueAdapter:
    """Stands in for the external dialog's `_msg_queue`: `put((kind, data))` becomes `JobEvents`."""

    def __init__(self, events: JobEvents) -> None:
        self.events = events
        self.messages: list = []

    def put(self, item) -> None:
        kind, data = item if isinstance(item, tuple) and len(item) == 2 else ("log", item)
        self.messages.append((kind, data))
        if kind == "log":
            text = str(data)
            self.events.log(text)
            if text.startswith(SAVED_PREFIX):
                self.events.saved(text[len(SAVED_PREFIX):].strip())
        elif kind == "progress":
            value, total = data
            self.events.progress(int(value), int(total), format_eta(int(value), int(total), None))
        elif kind == "book_parsed":
            self.events.book_info(dict(data or {}))
        elif kind == "error":
            self.events.log(f"ERROR: {data}")
        elif kind == "finished":
            self.events.finished({"kind": "finished", "data": data})

    def put_nowait(self, item) -> None:
        self.put(item)
