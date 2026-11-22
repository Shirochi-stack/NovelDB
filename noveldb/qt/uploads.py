"""Share-link uploads from the Queue page: the one-time agreement, progress and the links."""

from __future__ import annotations

import html
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
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
                self.items, settings.hosts, self.context.upload_context(stopped=self._stop.is_set, upload_settings=settings),
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
        self.results = QTextBrowser()  # links open in the browser when clicked
        self.results.setOpenExternalLinks(True)
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
        lines = [html.escape(outcome.label)]
        if outcome.link:
            link = html.escape(outcome.link, quote=True)
            lines.append(f'<a href="{link}">{link}</a>')
        else:
            lines.append(html.escape(f"Not uploaded: {outcome.error}"))
        if outcome.is_zip:
            lines.extend("&nbsp;&nbsp;&nbsp;&nbsp;" + html.escape(title) for title in outcome.titles)
        self.results.append("<br>".join(lines) + "<br>")

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


def format_of(path: str) -> str:
    """The output format a saved file is, as the choice shows it: EPUB, ZIP, Folder, TXT folder..."""
    if os.path.isdir(path):
        if path.lower().endswith(".folder"):
            return "Folder"
        kinds = {os.path.splitext(name)[1].lower() for name in os.listdir(path)}
        return "TXT folder" if kinds and kinds <= {".txt"} else "Folder"
    suffix = os.path.splitext(path)[1].lower()
    return {".htm": "HTML"}.get(suffix, suffix[1:].upper() or "File")


_last_formats: set = set()  # the formats left unticked the last time, for the next upload


class FormatDialog(QDialog):
    """Asks which of the saved output formats to upload."""

    def __init__(self, counts: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload and share")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("These downloads were saved in several formats. Upload which?"))
        self.boxes = {}
        for name, count in counts.items():
            box = QCheckBox(f"{name}  ({count} file{'s' if count != 1 else ''})")
            box.setChecked(name not in _last_formats)
            box.toggled.connect(self._changed)
            self.boxes[name] = box
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.upload = buttons.addButton("Upload", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if not self.chosen():
            for box in self.boxes.values():
                box.setChecked(True)
        self._changed()

    def chosen(self) -> set:
        return {name for name, box in self.boxes.items() if box.isChecked()}

    def _changed(self) -> None:
        self.upload.setEnabled(bool(self.chosen()))


def choose_formats(items: list, parent: Optional[QWidget] = None) -> list:
    """The items to upload: all of them when they are one format, else the formats the user ticks
    (nothing when the question is cancelled)."""
    counts: dict = {}
    for item in items:
        name = format_of(item.path)
        counts[name] = counts.get(name, 0) + 1
    if len(counts) < 2:
        return items
    dialog = FormatDialog(counts, parent)
    if dialog.exec() != QDialog.Accepted:
        return []
    chosen = dialog.chosen()
    _last_formats.clear()
    _last_formats.update(set(counts) - chosen)
    return [item for item in items if format_of(item.path) in chosen]


def upload_jobs(context: AppContext, jobs, parent: Optional[QWidget] = None) -> None:
    """Agreement (once), then upload every existing output of `jobs`, bundled as the settings say."""
    items = items_for_jobs(jobs)
    if not items:
        return
    if not ensure_agreement(context, parent):
        return
    items = choose_formats(items, parent)
    if not items:
        return
    dialog = UploadDialog(context, items, parent)
    dialog.exec()
    remember_links(context, jobs, dialog.outcomes)


def saved_entries(jobs, folder: str) -> list:
    """What the Download page's Upload button offers: `[(UploadItem, job or None, modified)]`, newest first.

    The files the queue's downloads saved (with their naming details), then everything else in
    the Save to folder that is a book or a chapter folder.
    """
    from noveldb.core import library

    def key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def modified(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    found: dict = {}
    for job in jobs:
        for path in job.outputs or []:
            if path and os.path.exists(path) and key(path) not in found:
                item = UploadItem(path, book_id_from_url(job.input), job.source or "", job.title or "")
                found[key(path)] = (item, job, modified(path))
    try:
        names = os.listdir(folder) if folder and os.path.isdir(folder) else []
    except OSError:
        names = []
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(".") or name.endswith((".part", ".tmp")) or key(path) in found:
            continue
        try:
            if library._format_of(Path(path)) is None:
                continue
        except OSError:
            continue
        found[key(path)] = (UploadItem(path), None, modified(path))
    return sorted(found.values(), key=lambda entry: entry[2], reverse=True)


class PickDialog(QDialog):
    """Asks which saved folders and files to upload."""

    def __init__(self, entries: list, parent: Optional[QWidget] = None) -> None:
        import time

        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        super().__init__(parent)
        self.setWindowTitle("Upload and share")
        self.setModal(True)
        bind_size(self, min_width=560)
        self.entries = entries
        self.list = QListWidget()
        self.list.setMinimumHeight(300)
        # The newest download is ticked: it is usually the one just made.
        newest = next((job for _item, job, _when in entries if job is not None), None)
        for item, job, when in entries:
            saved = time.strftime("%Y-%m-%d %H:%M", time.localtime(when)) if when else ""
            row = QListWidgetItem(f"{os.path.basename(item.path)}    ({format_of(item.path)}, {saved})")
            row.setToolTip(item.path)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if job is not None and job is newest else Qt.Unchecked)
            self.list.addItem(row)
        self.list.itemChanged.connect(self._changed)
        everything = QPushButton("Select all")
        everything.clicked.connect(lambda: self._tick(True))
        nothing = QPushButton("Select none")
        nothing.clicked.connect(lambda: self._tick(False))
        self.count = QLabel("")
        self.count.setProperty("hint", True)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.upload = buttons.addButton("Upload", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(everything)
        row.addWidget(nothing)
        row.addWidget(self.count, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Which folders and files should be uploaded?"))
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self._changed()

    def _tick(self, on: bool) -> None:
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def chosen(self) -> list:
        return [entry for index, entry in enumerate(self.entries)
                if self.list.item(index).checkState() == Qt.Checked]

    def _changed(self, *_args) -> None:
        count = len(self.chosen())
        self.count.setText(f"{count} of {len(self.entries)} selected")
        self.upload.setEnabled(count > 0)


def upload_saved(context: AppContext, parent: Optional[QWidget] = None) -> None:
    """The Download page's Upload button: ask which saved folders and files, then upload them."""
    from PySide6.QtWidgets import QMessageBox

    from noveldb.core import paths

    folder = context.settings.output.output_dir or str(paths.default_output_dir())
    entries = saved_entries(context.runner.jobs, folder)
    if not entries:
        QMessageBox.information(parent, "Upload and share",
                                f"There is nothing to upload yet: no finished download, and no book in\n{folder}")
        return
    picker = PickDialog(entries, parent)
    if picker.exec() != QDialog.Accepted:
        return
    chosen = picker.chosen()
    if not chosen or not ensure_agreement(context, parent):
        return
    dialog = UploadDialog(context, [item for item, _job, _when in chosen], parent)
    dialog.exec()
    jobs = []
    for _item, job, _when in chosen:
        if job is not None and job not in jobs:
            jobs.append(job)
    if jobs:
        remember_links(context, jobs, dialog.outcomes)


def remember_links(context: AppContext, jobs, outcomes) -> None:
    """Keep each upload's link on the jobs whose files it carries (Queue: Copy links)."""
    changed = False
    for outcome in outcomes:
        if not outcome.link:
            continue
        titles = set(outcome.titles or [])
        owners = [job for job in jobs
                  if (job.title and job.title in titles)
                  or any(os.path.basename(path) == outcome.name for path in job.outputs)
                  or any(os.path.splitext(os.path.basename(path))[0] in titles for path in job.outputs)]
        for job in owners or list(jobs):
            if outcome.link not in job.links:
                job.links.append(outcome.link)
                changed = True
    if changed:
        save = getattr(context.runner, "_save", None)
        if callable(save):
            save()
