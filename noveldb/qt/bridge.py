"""Thread-safe links between worker threads and the Qt interface.

Jobs report through `QtJobEvents`, which turns each call into a queued Qt signal (log lines are
batched through a `LogPump` that the window drains on a timer). `QtPrompts` lets a worker ask the
user something: it posts the question to the GUI thread and waits on a Future for the answer.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from noveldb.core.events import JobEvents, LogPump, Prompts


class QtEventHub(QObject):
    jobChanged = Signal(str)
    jobStatus = Signal(str, str)
    jobProgress = Signal(str, int, int, str)
    jobImages = Signal(str, int)
    jobBookInfo = Signal(str, dict)
    jobSaved = Signal(str, str)
    message = Signal(str)  # app-wide notices (toasts / status bar)

    def __init__(self) -> None:
        super().__init__()
        self.logs = LogPump()  # (job id, line) pairs; "" job id for app-wide lines

    def log(self, job_id: str, text: str) -> None:
        self.logs.push((job_id, text))


class QtJobEvents(JobEvents):
    def __init__(self, hub: QtEventHub, job_id: str) -> None:
        self.hub = hub
        self.job_id = job_id

    def log(self, text):
        self.hub.log(self.job_id, text)

    def status(self, text):
        self.hub.jobStatus.emit(self.job_id, text)

    def progress(self, value, total, info):
        self.hub.jobProgress.emit(self.job_id, int(value), int(total), info)

    def images(self, count):
        self.hub.jobImages.emit(self.job_id, int(count))

    def book_info(self, info):
        self.hub.jobBookInfo.emit(self.job_id, dict(info))

    def saved(self, path):
        self.hub.jobSaved.emit(self.job_id, path)


@dataclass
class PromptRequest:
    kind: str
    args: dict
    future: Future = field(default_factory=Future)


class QtPrompts(QObject, Prompts):
    """Questions from worker threads, answered by dialogs on the GUI thread."""

    requested = Signal(object)

    def __init__(self, parent_widget_getter=lambda: None) -> None:
        QObject.__init__(self)
        self._parent = parent_widget_getter
        self.requested.connect(self._answer, Qt.QueuedConnection)

    def _ask(self, kind: str, **args) -> Any:
        app = QApplication.instance()
        if app is not None and threading.current_thread() is threading.main_thread():
            raise RuntimeError("QtPrompts must be called from a worker thread")
        request = PromptRequest(kind, args)
        self.requested.emit(request)
        try:
            return request.future.result()
        except Exception:
            return None

    def ask_save_path(self, title, suggested, extension):
        return self._ask("save", title=title, suggested=suggested, extension=extension)

    def ask_folder(self, title, start=""):
        return self._ask("folder", title=title, start=start)

    def confirm(self, title, text, default=False):
        answer = self._ask("confirm", title=title, text=text)
        return default if answer is None else bool(answer)

    def warn(self, title, text):
        self._ask("warn", title=title, text=text)

    @Slot(object)
    def _answer(self, request: PromptRequest) -> None:
        parent = self._parent()
        a = request.args
        try:
            if request.kind == "save":
                ext = (a.get("extension") or "").lstrip(".")
                filters = f"{ext.upper()} files (*.{ext});;All files (*)" if ext else "All files (*)"
                path, _ = QFileDialog.getSaveFileName(parent, a.get("title") or "Save", a.get("suggested") or "", filters)
                request.future.set_result(path or None)
            elif request.kind == "folder":
                path = QFileDialog.getExistingDirectory(parent, a.get("title") or "Choose folder", a.get("start") or "")
                request.future.set_result(path or None)
            elif request.kind == "confirm":
                answer = QMessageBox.question(parent, a.get("title") or "", a.get("text") or "")
                request.future.set_result(answer == QMessageBox.Yes)
            else:
                QMessageBox.warning(parent, a.get("title") or "", a.get("text") or "")
                request.future.set_result(None)
        except Exception as exc:  # never leave a worker waiting
            if not request.future.done():
                request.future.set_exception(exc)

    def cancel_pending(self) -> None:
        """Nothing to do: dialogs are modal and answer synchronously on the GUI thread."""
