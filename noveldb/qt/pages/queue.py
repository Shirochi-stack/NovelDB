"""Queue page: every download, with Stop / Retry / Remove / Open / Upload."""

from __future__ import annotations

import os
from dataclasses import replace

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import platform_services
from noveldb.core.queue.model import ACTIVE, CANCELLED, DONE, FAILED, FINISHED, INTERRUPTED, QUEUED, RUNNING
from noveldb.qt.context import AppContext
from noveldb.qt.uploads import upload_jobs
from noveldb.qt.widgets import hint, spin

STATE_LABELS = {
    QUEUED: "Waiting", RUNNING: "Downloading", DONE: "Done", FAILED: "Failed",
    CANCELLED: "Stopped", INTERRUPTED: "Interrupted",
}
COLUMNS = ("Title", "Source", "State", "Progress", "Details")


class QueuePage(QWidget):
    activeCountChanged = Signal(int)
    finishedBatch = Signal(int, int)  # done, failed

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.runner = context.runner
        self._batch: set[str] = set()  # jobs since the queue was last idle

        title = QLabel("Queue")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.pause = QPushButton()
        self.pause.clicked.connect(self._toggle_pause)
        stop_all = QPushButton("Stop all")
        stop_all.clicked.connect(self.runner.cancel_all)
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self._clear_finished)
        self.at_once = spin(context.settings.queue.max_concurrent_jobs, 1, 3, self._set_at_once)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("Downloads at once"))
        header.addWidget(self.at_once)
        header.addWidget(self.pause)
        header.addWidget(stop_all)
        header.addWidget(clear)

        # Shown when the whole queue has finished: share or upload the batch at once.
        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.StyledPanel)
        self.banner_text = QLabel("")
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self._open_batch_folder)
        upload_all = QPushButton("Upload all")
        upload_all.clicked.connect(lambda: upload_jobs(self.context, self._batch_done(), self))
        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(self.banner.hide)
        banner_row = QHBoxLayout(self.banner)
        banner_row.addWidget(self.banner_text, 1)
        banner_row.addWidget(open_folder)
        banner_row.addWidget(upload_all)
        banner_row.addWidget(dismiss)
        self.banner.hide()
        self._last_batch: list[str] = []

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (1, 2):
            head.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(3, 140)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _index: self._open())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)

        self.stop = QPushButton("Stop")
        self.stop.clicked.connect(lambda: self._each(self.runner.cancel))
        self.retry = QPushButton("Retry")
        self.retry.clicked.connect(lambda: self._each(self.runner.retry))
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        self.open = QPushButton("Open")
        self.open.clicked.connect(self._open)
        self.reveal = QPushButton("Show in folder")
        self.reveal.clicked.connect(self._reveal)
        self.upload = QPushButton("Upload")
        self.upload.clicked.connect(lambda: upload_jobs(self.context, self._selected_jobs(), self))
        actions = QHBoxLayout()
        for button in (self.stop, self.retry, self.remove):
            actions.addWidget(button)
        actions.addStretch(1)
        for button in (self.open, self.reveal, self.upload):
            actions.addWidget(button)
        self.detail = hint("")
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.banner)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(self.detail)

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(120)
        self._reload_timer.timeout.connect(self.reload)
        context.hub.jobChanged.connect(self._job_changed)
        context.settingsChanged.connect(self._settings_changed)
        self.reload()

    # -- table ---------------------------------------------------------------------------

    def reload(self) -> None:
        jobs = self.runner.jobs
        selected = {job.id for job in self._selected_jobs()}
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = (job.display_title, job.source or "", STATE_LABELS.get(job.state, job.state), None,
                      job.error or job.status or job.info)
            for column, value in enumerate(values):
                if column == 3:
                    self._progress_cell(row, job)
                    continue
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(str(value))
                item.setData(Qt.UserRole, job.id)
                if column == 0:
                    item.setToolTip(job.input)
        model = self.table.selectionModel()
        model.blockSignals(True)
        model.clearSelection()
        for row, job in enumerate(jobs):
            if job.id in selected:
                model.select(self.table.model().index(row, 0),
                             QItemSelectionModel.Select | QItemSelectionModel.Rows)
        model.blockSignals(False)
        self.table.viewport().update()
        self.table.setUpdatesEnabled(True)
        self._track_batch(jobs)
        self._update_pause()
        self._selection_changed()
        self.activeCountChanged.emit(sum(1 for job in jobs if job.state in ACTIVE))

    def _progress_cell(self, row: int, job) -> None:
        holder = self.table.cellWidget(row, 3)
        bar = holder.findChild(QProgressBar) if holder is not None else None
        if bar is None:
            holder = QWidget()
            box = QVBoxLayout(holder)
            box.setContentsMargins(8, 0, 8, 0)
            bar = QProgressBar()
            bar.setTextVisible(False)
            box.addWidget(bar, 0, Qt.AlignVCenter)
            self.table.setCellWidget(row, 3, holder)
        if job.total > 0:
            bar.setRange(0, job.total)
            bar.setValue(min(job.value, job.total))
        elif job.state == RUNNING:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 1)
            bar.setValue(1 if job.state == DONE else 0)
        bar.setToolTip(f"{job.value} / {job.total}" if job.total else "")

    def _selected_jobs(self):
        ids = []
        for index in self.table.selectionModel().selectedRows() if self.table.selectionModel() else []:
            item = self.table.item(index.row(), 0)
            if item is not None:
                ids.append(item.data(Qt.UserRole))
        return [job for job in (self.runner.job(job_id) for job_id in ids) if job is not None]

    def _selection_changed(self) -> None:
        jobs = self._selected_jobs()
        self.stop.setEnabled(any(job.state in ACTIVE for job in jobs))
        self.retry.setEnabled(any(job.state in FINISHED for job in jobs))
        self.remove.setEnabled(any(job.state != RUNNING for job in jobs))
        has_outputs = any(self._existing_outputs(job) for job in jobs)
        self.open.setEnabled(has_outputs)
        self.reveal.setEnabled(has_outputs or any(job.output_dir for job in jobs))
        self.upload.setEnabled(has_outputs)
        if len(jobs) == 1:
            job = jobs[0]
            lines = [job.input]
            if job.outputs:
                lines.append("Saved: " + ", ".join(os.path.basename(path) for path in job.outputs))
            if job.error:
                lines.append(job.error)
            if job.warnings:
                lines.append("Warnings: " + "; ".join(str(w) for w in job.warnings[:3]))
            self.detail.setText("\n".join(lines))
        else:
            self.detail.setText(f"{len(jobs)} selected" if jobs else "")

    @staticmethod
    def _existing_outputs(job) -> list:
        return [path for path in job.outputs if path and os.path.exists(path)]

    # -- actions ---------------------------------------------------------------------------

    def _each(self, action) -> None:
        for job in self._selected_jobs():
            action(job.id)
        self.reload()

    def _remove(self) -> None:
        self._each(self.runner.remove)

    def _clear_finished(self) -> None:
        self.runner.clear_finished()
        self.reload()

    def _open(self) -> None:
        for job in self._selected_jobs()[:5]:
            outputs = self._existing_outputs(job)
            if outputs:
                platform_services.open_path(outputs[0])

    def _reveal(self) -> None:
        for job in self._selected_jobs()[:5]:
            outputs = self._existing_outputs(job)
            if outputs:
                platform_services.reveal_path(outputs[0])
            elif job.output_dir and os.path.isdir(job.output_dir):
                platform_services.open_path(job.output_dir)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        for button in (self.stop, self.retry, self.remove, None, self.open, self.reveal, self.upload):
            if button is None:
                menu.addSeparator()
                continue
            action = menu.addAction(button.text())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(button.click)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _toggle_pause(self) -> None:
        if self.runner.paused:
            self.runner.resume()
        else:
            self.runner.pause()
        self._update_pause()

    def _update_pause(self) -> None:
        waiting = any(job.state == QUEUED for job in self.runner.jobs)
        self.pause.setText("Start" if self.runner.paused else "Pause")
        self.pause.setEnabled(self.runner.paused or waiting)

    def _set_at_once(self, value: int) -> None:
        self.context.update_settings(lambda s: replace(s, queue=replace(s.queue, max_concurrent_jobs=value)))

    def _settings_changed(self) -> None:
        if self.at_once.value() != self.context.settings.queue.max_concurrent_jobs:
            self.at_once.blockSignals(True)
            self.at_once.setValue(self.context.settings.queue.max_concurrent_jobs)
            self.at_once.blockSignals(False)

    # -- whole-queue completion -----------------------------------------------------------------

    def _job_changed(self, job_id: str) -> None:
        # Every job that changes before the queue goes idle belongs to this batch, even one that
        # finished before the next reload.
        self._batch.add(job_id)
        self._reload_timer.start()

    def _track_batch(self, jobs) -> None:
        if not self._batch or any(job.state in ACTIVE for job in jobs):
            return
        finished = [job for job in jobs if job.id in self._batch and job.state in FINISHED]
        self._batch = set()
        done = [job for job in finished if job.state == DONE]
        failed = sum(1 for job in finished if job.state == FAILED)
        if not finished:
            return
        self._last_batch = [job.id for job in done]
        parts = [f"{len(done)} done"]
        if failed:
            parts.append(f"{failed} failed")
        self.banner_text.setText("Queue finished: " + ", ".join(parts) + ".")
        self.banner.setVisible(bool(done) or bool(failed))
        self.finishedBatch.emit(len(done), failed)

    def _batch_done(self):
        return [job for job in (self.runner.job(job_id) for job_id in self._last_batch) if job is not None]

    def _open_batch_folder(self) -> None:
        for job in self._batch_done():
            outputs = self._existing_outputs(job)
            if outputs:
                platform_services.open_path(os.path.dirname(outputs[0]))
                return
            if job.output_dir and os.path.isdir(job.output_dir):
                platform_services.open_path(job.output_dir)
                return
