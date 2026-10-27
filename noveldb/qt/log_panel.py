"""The bottom panel: the running job's status and progress, and the shared log."""

from __future__ import annotations

from collections import deque
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import platform_services
from noveldb.core.events import SAVED_PREFIX
from noveldb.core.queue.model import RUNNING
from noveldb.qt.context import AppContext

MAX_LINES = 20_000


class LogView(QPlainTextEdit):
    """Read-only log; double-click a "Saved:" line to show the file."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("log")
        self.setReadOnly(True)
        self.setMaximumBlockCount(MAX_LINES)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def _saved_path(self, pos) -> Optional[str]:
        line = self.cursorForPosition(pos).block().text()
        return line[len(SAVED_PREFIX):].strip() if line.startswith(SAVED_PREFIX) else None

    def mouseDoubleClickEvent(self, event):
        path = self._saved_path(event.position().toPoint())
        if path:
            platform_services.reveal_path(path)
            return
        super().mouseDoubleClickEvent(event)

    def _menu(self, pos):
        menu = self.createStandardContextMenu()
        path = self._saved_path(pos)
        if path:
            menu.insertSeparator(menu.actions()[0] if menu.actions() else None)
            open_file = QAction("Open file", menu)
            open_file.triggered.connect(lambda: platform_services.open_path(path))
            reveal = QAction("Show in folder", menu)
            reveal.triggered.connect(lambda: platform_services.reveal_path(path))
            first = menu.actions()[0] if menu.actions() else None
            menu.insertAction(first, open_file)
            menu.insertAction(first, reveal)
        clear = QAction("Clear log", menu)
        clear.triggered.connect(self.clear)
        menu.addSeparator()
        menu.addAction(clear)
        menu.exec(self.mapToGlobal(pos))


class LogPanel(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._lines: deque = deque(maxlen=MAX_LINES)  # (job id, text)
        self._current: Optional[str] = None  # job shown in the status strip

        self.title = QLabel("Ready")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel("")
        self.status.setProperty("hint", True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.info = QLabel("")
        self.info.setProperty("hint", True)
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self._stop_current)
        self.filter = QComboBox()
        self.filter.addItem("All jobs", "")
        self.filter.currentIndexChanged.connect(self._refilter)
        self.log = LogView()

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.title, 1)
        top.addWidget(self.info)
        top.addWidget(self.filter)
        top.addWidget(self.stop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 10)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log, 1)

        hub = context.hub
        hub.jobChanged.connect(self._job_changed)
        hub.jobProgress.connect(self._job_progress)
        hub.jobStatus.connect(self._job_status)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    # -- log -------------------------------------------------------------------------

    def _drain(self) -> None:
        items = self.context.hub.logs.drain(max_items=400, budget=0.02)
        if not items:
            return
        wanted = self.filter.currentData()
        shown = []
        for job_id, text in items:
            self._lines.append((job_id, text))
            if not wanted or job_id == wanted or not job_id:
                shown.append(text)
        if shown:
            self._append(shown)

    def _append(self, lines) -> None:
        bar = self.log.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        cursor = QTextCursor(self.log.document())
        cursor.movePosition(QTextCursor.End)
        for text in lines:
            if not self.log.document().isEmpty():
                cursor.insertBlock()
            cursor.insertText(str(text).rstrip("\n"))
        if at_end:
            bar.setValue(bar.maximum())

    def _refilter(self) -> None:
        wanted = self.filter.currentData()
        self.log.clear()
        self._append([text for job_id, text in self._lines if not wanted or job_id == wanted or not job_id])

    def _ensure_filter_entry(self, job) -> None:
        index = self.filter.findData(job.id)
        label = (job.display_title or job.id)[:60]
        if index < 0:
            self.filter.addItem(label, job.id)
        elif self.filter.itemText(index) != label:
            self.filter.setItemText(index, label)

    # -- status strip ------------------------------------------------------------------

    def _job_changed(self, job_id: str) -> None:
        job = self.context.runner.job(job_id)
        if job is None:
            return
        self._ensure_filter_entry(job)
        current = self.context.runner.job(self._current) if self._current else None
        if job.state == RUNNING and (current is None or current.state != RUNNING):
            self._current = job.id
        if job.id != self._current:
            if current is None or current.state != RUNNING:
                self._pick_next()
            return
        self._show(job)

    def _pick_next(self) -> None:
        running = self.context.runner.running()
        if running:
            self._current = running[0].id
            self._show(running[0])

    def _show(self, job) -> None:
        self.title.setText(job.display_title)
        self.status.setText(job.status or job.error or job.state.capitalize())
        self.stop.setEnabled(job.state == RUNNING)
        if job.total > 0:
            self.progress.setRange(0, job.total)
            self.progress.setValue(min(job.value, job.total))
        elif job.state == RUNNING:
            self.progress.setRange(0, 0)  # busy
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if job.state == "done" else 0)
        self.info.setText(job.info if job.state == RUNNING else "")

    def _job_progress(self, job_id: str, value: int, total: int, info: str) -> None:
        if job_id != self._current:
            return
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(value, total))
        self.info.setText(info)

    def _job_status(self, job_id: str, text: str) -> None:
        if job_id == self._current:
            self.status.setText(text)

    def _stop_current(self) -> None:
        if self._current:
            self.context.runner.cancel(self._current)
