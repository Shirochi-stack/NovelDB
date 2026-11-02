"""Library page: the books in the save folder, with their covers, so nobody needs a file manager.

The folder is the one downloads go to (Settings, or the default "NovelDB Library"). The page reads
it again when it is shown, when a download is saved and when the folder changes on disk; the
scanning runs on a worker thread (`noveldb.core.library`).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List

from PySide6.QtCore import QFile, QFileSystemWatcher, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import library, paths, platform_services
from noveldb.qt import icons
from noveldb.qt.context import AppContext
from noveldb.qt.scale import bind_title
from noveldb.qt.widgets import hint

COVER = QSize(150, 210)
PLACEHOLDER = "halgakos-cover.png"


def _size_text(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _to_trash(path: Path) -> bool:
    result = QFile.moveToTrash(str(path))
    return bool(result[0] if isinstance(result, tuple) else result)


def library_folder(context: AppContext) -> Path:
    return Path(context.settings.output.output_dir or paths.default_output_dir())


class LibraryPage(QWidget):
    _scanned = Signal(object, float)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.books: List[library.LibraryBook] = []
        self._scan_started = 0.0
        title = bind_title(QLabel("Library"), 24)
        self.where = hint("")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a book by title or author")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._show)
        self.sort = QComboBox()
        self.sort.addItems(["Newest first", "Title", "Largest first"])
        self.sort.currentIndexChanged.connect(self._show)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(lambda: platform_services.open_path(library_folder(self.context)))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.sort)
        bar.addWidget(refresh)
        bar.addWidget(open_folder)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setIconSize(COVER)
        self.grid.setGridSize(QSize(COVER.width() + 34, COVER.height() + 74))
        self.grid.setWordWrap(True)
        self.grid.setSpacing(8)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.grid.itemActivated.connect(lambda item: self._open(item.data(Qt.UserRole)))
        self.empty = hint("")
        self.empty.setAlignment(Qt.AlignCenter)

        actions = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(lambda: self._open(self._selected()))
        self.show_button = QPushButton("Show in folder")
        self.show_button.clicked.connect(lambda: self._reveal(self._selected()))
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(lambda: self._delete(self._selected()))
        self.detail = hint("")
        actions.addWidget(self.detail, 1)
        for button in (self.open_button, self.show_button, self.delete_button):
            actions.addWidget(button)
        self.grid.currentItemChanged.connect(lambda *_a: self._selection_changed())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(title)
        layout.addWidget(self.where)
        layout.addLayout(bar)
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.empty)
        layout.addLayout(actions)

        self._placeholder = self._cover_icon(None)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._later.start())
        self._later = QTimer(self, singleShot=True, interval=800)
        self._later.timeout.connect(self.refresh)
        self._scanned.connect(self._take)
        context.hub.jobSaved.connect(lambda *_a: self._later.start())
        context.settingsChanged.connect(self._folder_changed)
        self._folder = None
        self._folder_changed()
        self._selection_changed()

    # -- reading the folder -----------------------------------------------------------------------

    def _folder_changed(self) -> None:
        folder = library_folder(self.context)
        if folder == self._folder:
            return
        self._folder = folder
        self.where.setText(f"Books saved in {folder}")
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        if folder.is_dir():
            self.watcher.addPath(str(folder))
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        folder = library_folder(self.context)
        started = time.monotonic()
        self._scan_started = started
        threading.Thread(target=lambda: self._scanned.emit(library.scan(folder), started),
                         name="library-scan", daemon=True).start()

    def _take(self, books, started: float) -> None:
        if started != self._scan_started:
            return  # a newer scan is on its way
        self.books = books
        self._show()

    # -- showing it -------------------------------------------------------------------------------

    def _cover_icon(self, data) -> QIcon:
        pixmap = QPixmap()
        if not (data and pixmap.loadFromData(data)):
            pixmap = QPixmap(icons.path(PLACEHOLDER))
        canvas = QPixmap(COVER)
        canvas.fill(Qt.transparent)
        if not pixmap.isNull():
            scaled = pixmap.scaled(COVER, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter = QPainter(canvas)
            painter.drawPixmap((COVER.width() - scaled.width()) // 2, (COVER.height() - scaled.height()) // 2, scaled)
            painter.end()
        return QIcon(canvas)

    def _show(self) -> None:
        selected = self._selected()
        text = self.search.text()
        books = [book for book in self.books if library.matches(book, text)]
        order = self.sort.currentIndex()
        if order == 1:
            books.sort(key=lambda b: b.title.lower())
        elif order == 2:
            books.sort(key=lambda b: b.size, reverse=True)
        self.grid.clear()
        for book in books:
            formats = " · ".join(dict.fromkeys(f.format for f in book.files))
            item = QListWidgetItem(self._cover_icon(book.cover) if book.cover else self._placeholder,
                                   f"{book.title}\n{formats}")
            item.setData(Qt.UserRole, book)
            item.setToolTip("\n".join(filter(None, [book.title, book.author, formats, _size_text(book.size),
                                                    time.strftime("%Y-%m-%d %H:%M", time.localtime(book.modified))])))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(item)
            if selected is not None and book.name == selected.name:
                self.grid.setCurrentItem(item)
        if not self.books:
            self.empty.setText("No books yet. Downloads are saved here, and show up as soon as they finish.")
        elif not books:
            self.empty.setText("No book matches.")
        self.empty.setVisible(not books)
        self._selection_changed()

    def _selected(self):
        item = self.grid.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _selection_changed(self) -> None:
        book = self._selected()
        for button in (self.open_button, self.show_button, self.delete_button):
            button.setEnabled(book is not None)
        if book is None:
            count = len(self.books)
            self.detail.setText(f"{count} book{'s' if count != 1 else ''}" if count else "")
            return
        self.detail.setText(" · ".join(filter(None, [book.author, _size_text(book.size),
                                                     time.strftime("%Y-%m-%d", time.localtime(book.modified))])))

    # -- actions ----------------------------------------------------------------------------------

    def _menu(self, point) -> None:
        item = self.grid.itemAt(point)
        if item is None:
            return
        book = item.data(Qt.UserRole)
        menu = QMenu(self)
        for file in book.files:
            menu.addAction(f"Open {file.format}  ({_size_text(file.size)})",
                           lambda f=file: QDesktopServices.openUrl(QUrl.fromLocalFile(str(f.path))))
        menu.addSeparator()
        menu.addAction("Show in folder", lambda: self._reveal(book))
        menu.addAction("Delete…", lambda: self._delete(book))
        menu.exec(self.grid.viewport().mapToGlobal(point))

    def _open(self, book) -> None:
        file = book.preferred() if book is not None else None
        if file is not None:
            platform_services.open_path(file.path)

    def _reveal(self, book) -> None:
        if book is not None:
            platform_services.reveal_paths([f.path for f in book.files])

    def _delete(self, book) -> None:
        if book is None:
            return
        names = "\n".join(f.path.name + ("/" if f.path.is_dir() else "") for f in book.files)
        answer = QMessageBox.question(self, "Delete book", f"Move {book.title} to the Recycle Bin?\n\n{names}")
        if answer != QMessageBox.Yes:
            return
        failed = [f.path.name for f in book.files if not _to_trash(f.path)]
        if failed:
            QMessageBox.warning(self, "Delete book", "These could not be moved to the Recycle Bin:\n" + "\n".join(failed))
        self.refresh()
