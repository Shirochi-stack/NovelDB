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

from noveldb.core.upload import UploadCancelled, upload
from noveldb.core.upload.transfer import book_id_from_url, upload_name
from noveldb.qt.context import AppContext

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
        text.setMinimumWidth(420)
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
    """Uploads files one after another on a worker thread."""

    progress = Signal(int, int, str, int, int)  # file index, file count, host, sent, total
    fileDone = Signal(str, str, str)  # file name, link ("" on failure), error
    finished = Signal()

    def __init__(self, context: AppContext, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self.context = context
        self.items = items  # (path, book id)
        self._stop = threading.Event()
        self._host = ""

    def start(self) -> None:
        threading.Thread(target=self._work, name="upload", daemon=True).start()

    def cancel(self) -> None:
        self._stop.set()

    def _work(self) -> None:
        count = len(self.items)
        hosts = self.context.settings.upload.hosts
        upload_context = self.context.upload_context(stopped=self._stop.is_set)
        for index, (path, book_id) in enumerate(self.items):
            if self._stop.is_set():
                break
            name = upload_name(path)

            def on_host(host, index=index):
                self._host = host
                self.progress.emit(index, count, host, 0, 0)

            def on_progress(sent, total, index=index):
                self.progress.emit(index, count, self._host, sent, total)

            try:
                result = upload(path, hosts, upload_context, on_progress, on_host, self.context.uploads, book_id)
                self.fileDone.emit(name, result.link, "")
            except UploadCancelled:
                self.fileDone.emit(name, "", "Cancelled")
                break
            except Exception as exc:
                self.fileDone.emit(name, "", str(exc) or type(exc).__name__)
        self.finished.emit()


def _mb(value: int) -> str:
    return f"{value / 1_048_576:.1f} MB"


class UploadDialog(QDialog):
    def __init__(self, context: AppContext, items: list[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("Upload and share")
        self.setMinimumWidth(480)
        self.links: list[str] = []
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
        self.task.progress.connect(self._progress, Qt.QueuedConnection)
        self.task.fileDone.connect(self._file_done, Qt.QueuedConnection)
        self.task.finished.connect(self._finished, Qt.QueuedConnection)
        self.task.start()

    def _progress(self, index: int, count: int, host: str, sent: int, total: int) -> None:
        self.heading.setText(f"Uploading {index + 1} of {count} to {host}")
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(sent * 1000 / total))
            self.detail.setText(f"{sent * 100 // total}%  ({_mb(sent)} of {_mb(total)})")
        else:
            self.bar.setRange(0, 0)
            self.detail.setText("Connecting...")

    def _file_done(self, name: str, link: str, error: str) -> None:
        if link:
            self.links.append(link)
            self.results.appendPlainText(f"{name}\n{link}\n")
        else:
            self.results.appendPlainText(f"{name}\nNot uploaded: {error}\n")

    def _finished(self) -> None:
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        ok = len(self.links)
        self.heading.setText(f"Uploaded {ok} file(s)" if ok else "Nothing was uploaded")
        self.detail.setText("")
        self.copy.setEnabled(ok > 0)
        self.cancel.setText("Close")
        if ok and self.context.settings.upload.copy_link:
            self._copy()
            self.detail.setText("Links copied to the clipboard.")

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self.links))

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


def upload_jobs(context: AppContext, jobs, parent: Optional[QWidget] = None) -> None:
    """Agreement (once), then upload every existing output of `jobs`."""
    items = []
    for job in jobs:
        for path in job.outputs:
            if path and os.path.exists(path):
                items.append((path, book_id_from_url(job.input)))
    if not items:
        return
    if not ensure_agreement(context, parent):
        return
    UploadDialog(context, items, parent).exec()
