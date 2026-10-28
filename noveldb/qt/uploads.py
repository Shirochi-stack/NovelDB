"""Share-link uploads from the Queue page: the one-time agreement, progress and the links."""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.core.upload import UploadItem, UploadOutcome, links_text, upload_items
from noveldb.core.upload.transfer import book_id_from_url
from noveldb.qt.context import AppContext
from noveldb.qt.scale import bind_size

AGREEMENT_TEXT = (
    "Share links are public: anyone who has the link can download the file until the host "
    "deletes it.\n\n"
    "Only upload files you are allowed to share. Books you downloaded are usually for your own "
    "reading; sharing them can break the source's terms or copyright law. You are responsible "
    "for what you upload and who you send the link to."
)


class AgreementDialog(QDialog):
    """Asked once, the first time the user creates a share link."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Before you share")
        self.setModal(True)
        text = QLabel(AGREEMENT_TEXT)
        text.setWordWrap(True)
        bind_size(text, min_width=420)
        self.confirm = QCheckBox("I confirm I am authorised to share these files.")
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.accept_button = buttons.addButton("Continue", QDialogButtonBox.AcceptRole)
        self.accept_button.setEnabled(False)
        self.confirm.toggled.connect(self.accept_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.confirm)
        layout.addWidget(buttons)


def ensure_agreement(context: AppContext, parent: Optional[QWidget] = None) -> bool:
    if context.settings.upload.agreement_accepted:
        return True
    if AgreementDialog(parent).exec() != QDialog.Accepted:
        return False
    context.update_settings(lambda s: replace(s, upload=replace(s.upload, agreement_accepted=True)))
    context.save_now()
    return True


class UploadTask(QObject):
    """Uploads files on a worker thread: lone files, and ZIPs of several when bundling is on."""

    prepare = Signal(int, int, str, int, int)  # upload index, upload count, ZIP name, bytes written, bytes in total
    progress = Signal(int, int, str, int, int)  # upload index, upload count, host, sent, total
    outcome = Signal(object)  # an UploadOutcome
    finished = Signal()

    def __init__(self, context: AppContext, items: list[UploadItem]) -> None:
        super().__init__()
        self.context = context
        self.items = items
        self._stop = threading.Event()
        self._host = ""

    def start(self) -> None:
        threading.Thread(target=self._work, name="upload", daemon=True).start()

    def cancel(self) -> None:
        self._stop.set()

    def _work(self) -> None:
        settings = self.context.settings.upload

        def on_host(index, count, host):
            self._host = host
            self.progress.emit(index, count, host, 0, 0)

        try:
            upload_items(
                self.items, settings.hosts, self.context.upload_context(stopped=self._stop.is_set),
                bundle=settings.zip_bundle, max_mb=settings.zip_max_mb, history=self.context.uploads,
                on_prepare=self.prepare.emit, on_host=on_host,
                progress=lambda index, count, sent, total: self.progress.emit(index, count, self._host, sent, total),
                on_outcome=self.outcome.emit,
            )
        except Exception as exc:  # never leave the dialog waiting
            self.outcome.emit(UploadOutcome("Upload", error=str(exc) or type(exc).__name__))
        self.finished.emit()


def _mb(value: int) -> str:
    return f"{value / 1_048_576:.1f} MB"


class UploadDialog(QDialog):
    def __init__(self, context: AppContext, items: list[UploadItem], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("Upload and share")
        bind_size(self, min_width=480)
        self.outcomes: list[UploadOutcome] = []
        self.file_count = len(items)
        self.heading = QLabel(f"Uploading {len(items)} file(s)...")
        self.detail = QLabel("")
        self.detail.setProperty("hint", True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setPlaceholderText("Links appear here.")
        self.copy = QPushButton("Copy links")
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self._copy)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self._cancel)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.copy)
        row.addWidget(self.cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.bar)
        layout.addWidget(self.detail)
        layout.addWidget(self.results, 1)
        layout.addLayout(row)

        self.task = UploadTask(context, items)
        self.task.prepare.connect(self._prepare, Qt.QueuedConnection)
        self.task.progress.connect(self._progress, Qt.QueuedConnection)
        self.task.outcome.connect(self._outcome, Qt.QueuedConnection)
        self.task.finished.connect(self._finished, Qt.QueuedConnection)
        self.task.start()

    @property
    def links(self) -> list[str]:
        return [outcome.link for outcome in self.outcomes if outcome.link]

    def _prepare(self, index: int, count: int, name: str, done: int, total: int) -> None:
        self.heading.setText(f"Preparing ZIP {index + 1} of {count}")
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(done * 1000 / total) if total > 0 else 0)
        self.detail.setText(f"{name}  ({_mb(done)} of {_mb(total)})")

    def _progress(self, index: int, count: int, host: str, sent: int, total: int) -> None:
        self.heading.setText(f"Uploading {index + 1} of {count} to {host}")
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(sent * 1000 / total))
            self.detail.setText(f"{sent * 100 // total}%  ({_mb(sent)} of {_mb(total)})")
        else:
            self.bar.setRange(0, 0)
            self.detail.setText("Connecting...")

    def _outcome(self, outcome: UploadOutcome) -> None:
        self.outcomes.append(outcome)
        lines = [outcome.label]
        if outcome.link:
            lines.append(outcome.link)
        else:
            lines.append(f"Not uploaded: {outcome.error}")
        if outcome.is_zip:
            lines.extend(f"    {title}" for title in outcome.titles)
        self.results.appendPlainText("\n".join(lines) + "\n")

    def _finished(self) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        done = [outcome for outcome in self.outcomes if outcome.link]
        files = sum(outcome.entries for outcome in done)
        if not done:
            self.heading.setText("Nothing was uploaded")
        elif files == len(done):
            self.heading.setText(f"Uploaded {files} file(s)")
        else:
            self.heading.setText(f"Uploaded {files} files in {len(done)} link{'s' if len(done) != 1 else ''}")
        self.detail.setText("")
        self.copy.setEnabled(bool(done))
        self.cancel.setText("Close")
        if done and self.context.settings.upload.copy_link:
            self._copy()
            self.detail.setText("Links copied to the clipboard.")

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(links_text(self.outcomes))

    def _cancel(self) -> None:
        if self.cancel.text() == "Close":
            self.accept()
        else:
            self.task.cancel()
            self.cancel.setEnabled(False)
            self.detail.setText("Cancelling...")

    def reject(self) -> None:
        self.task.cancel()
        super().reject()


def items_for_jobs(jobs) -> list[UploadItem]:
    """The files to upload for `jobs` (every output that still exists), each with its naming details."""
    items: list[UploadItem] = []
    seen: set[str] = set()
    for job in jobs:
        for path in job.outputs:
            if path and os.path.exists(path) and path not in seen:
                seen.add(path)
                items.append(UploadItem(path, book_id_from_url(job.input), job.source or "", job.title or ""))
    return items


def upload_jobs(context: AppContext, jobs, parent: Optional[QWidget] = None) -> None:
    """Agreement (once), then upload every existing output of `jobs`, bundled as the settings say."""
    items = items_for_jobs(jobs)
    if not items:
        return
    if not ensure_agreement(context, parent):
        return
    UploadDialog(context, items, parent).exec()
