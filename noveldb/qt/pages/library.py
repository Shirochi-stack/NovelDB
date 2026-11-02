"""Library page: the books in the save folder, with their covers, so nobody needs a file manager.

The folder is the one downloads go to (Settings, or the default "NovelDB Library"). Nothing is read
until the page is first shown; then it reads the folder again when a download is saved and when the
folder changes on disk. Scanning runs on a worker thread (`noveldb.core.library`), books show a page
at a time, and only the covers on the page shown are read and decoded, on a worker thread too.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List

from PySide6.QtCore import QFile, QFileSystemWatcher, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPainter, QPixmap, QTransform
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
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import library, paths, platform_services
from noveldb.qt import icons
from noveldb.qt.context import AppContext
from noveldb.qt.pages.library_reader import BookView, EpubReader
from noveldb.qt.scale import bind_title
from noveldb.qt.widgets import hint

COVER = QSize(150, 210)
PLACEHOLDER = "halgakos-cover.png"
PER_PAGE = 60


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


def _cover_image(data) -> QImage:
    """A cover scaled and cropped to the card (QImage: safe to make on a worker thread)."""
    image = QImage()
    if not (data and image.loadFromData(data)):
        image = QImage(icons.path(PLACEHOLDER))
    canvas = QImage(COVER, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    if not image.isNull():
        scaled = image.scaled(COVER, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter = QPainter(canvas)
        painter.drawImage((COVER.width() - scaled.width()) // 2, (COVER.height() - scaled.height()) // 2, scaled)
        painter.end()
    return canvas


class Loading(QWidget):
    """Halgakos turning, with a progress bar, while the Library folder is read."""

    def __init__(self) -> None:
        super().__init__()
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setFixedSize(QSize(200, 200))
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(360)
        self.bar.setTextVisible(True)
        self.text = hint("Reading the Library folder…")
        self.text.setAlignment(Qt.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.picture, 0, Qt.AlignHCenter)
        layout.addWidget(self.bar, 0, Qt.AlignHCenter)
        layout.addWidget(self.text, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        image = QPixmap(icons.path(PLACEHOLDER))
        self.image = image.scaled(QSize(140, 140), Qt.KeepAspectRatio, Qt.SmoothTransformation) if not image.isNull() else image
        self.angle = 0
        self.timer = QTimer(self, interval=40)
        self.timer.timeout.connect(self._turn)

    def start(self) -> None:
        self.bar.setRange(0, 0)  # busy until the first count arrives
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def progress(self, done: int, total: int) -> None:
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.bar.setFormat(f"{done} of {total}")

    def _turn(self) -> None:
        if self.image.isNull():
            return
        self.angle = (self.angle + 6) % 360
        turned = self.image.transformed(QTransform().rotate(self.angle), Qt.SmoothTransformation)
        self.picture.setPixmap(turned)


class LibraryPage(QWidget):
    _scanned = Signal(object, float)
    _scan_progress = Signal(int, int, float)
    _covered = Signal(str, object, int)  # book name, cover QImage, page generation

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.books: List[library.LibraryBook] = []
        self.shown: List[library.LibraryBook] = []
        self.page = 0
        self._generation = 0
        self._scan_started = 0.0
        self._ever_shown = False
        title = bind_title(QLabel("Library"), 24)
        self.where = hint("")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a book by title or author")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._show(reset=True))
        self.sort = QComboBox()
        self.sort.addItems(["Newest first", "Title", "Largest first"])
        self.sort.currentIndexChanged.connect(lambda _i: self._show(reset=True))
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
        self.grid.itemActivated.connect(lambda item: self._show_book(item.data(Qt.UserRole)))
        self.empty = hint("")
        self.empty.setAlignment(Qt.AlignCenter)

        pages = QHBoxLayout()
        self.previous = QPushButton("‹ Previous")
        self.previous.clicked.connect(lambda: self._turn(-1))
        self.next = QPushButton("Next ›")
        self.next.clicked.connect(lambda: self._turn(1))
        self.page_label = QLabel("")
        pages.addStretch(1)
        pages.addWidget(self.previous)
        pages.addWidget(self.page_label)
        pages.addWidget(self.next)
        pages.addStretch(1)

        actions = QHBoxLayout()
        self.read_button = QPushButton("Read")
        self.read_button.setProperty("primary", True)
        self.read_button.setToolTip("Read the EPUB in the app's reader")
        self.read_button.clicked.connect(lambda: self._read(self._selected()))
        self.open_button = QPushButton("Open")
        self.open_button.setToolTip("Open it with the app your system uses for it")
        self.open_button.clicked.connect(lambda: self._open(self._selected()))
        self.show_button = QPushButton("Show in folder")
        self.show_button.clicked.connect(lambda: self._reveal(self._selected()))
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(lambda: self._delete(self._selected()))
        self.detail = hint("")
        actions.addWidget(self.detail, 1)
        for button in (self.read_button, self.open_button, self.show_button, self.delete_button):
            actions.addWidget(button)
        self.grid.currentItemChanged.connect(lambda *_a: self._selection_changed())

        self.loading = Loading()
        self.body = QStackedWidget()  # the grid, or Halgakos while the folder is read
        self.body.addWidget(self.grid)
        self.body.addWidget(self.loading)
        shelf = QWidget()
        layout = QVBoxLayout(shelf)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(title)
        layout.addWidget(self.where)
        layout.addLayout(bar)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.empty)
        layout.addLayout(pages)
        layout.addLayout(actions)

        # The Library's own views: the shelf, a book's page, and the reader (all from the files themselves).
        self.book_view = BookView()
        self.reader = EpubReader()
        self.views = QStackedWidget()
        for view in (shelf, self.book_view, self.reader):
            self.views.addWidget(view)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.views)
        self.book_view.back.connect(lambda: self.views.setCurrentIndex(0))
        self.book_view.readRequested.connect(self._read_at)
        self.book_view.openRequested.connect(self._open)
        self.book_view.revealRequested.connect(self._reveal)
        self.book_view.deleteRequested.connect(self._delete)
        self.reader.back.connect(lambda: self.views.setCurrentWidget(self.book_view))

        self._placeholder = QIcon(QPixmap.fromImage(_cover_image(None)))
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._later.start())
        self._later = QTimer(self, singleShot=True, interval=800)
        self._later.timeout.connect(self.refresh)
        self._scanned.connect(self._take)
        self._scan_progress.connect(lambda done, total, started: self.loading.progress(done, total)
                                    if started == self._scan_started else None)
        self._covered.connect(self._set_cover)
        context.hub.jobSaved.connect(lambda *_a: self._later.start() if self._ever_shown else None)
        context.settingsChanged.connect(self._folder_changed)
        self._folder = None
        self._selection_changed()

    # -- reading the folder -----------------------------------------------------------------------

    def _folder_changed(self) -> None:
        folder = library_folder(self.context)
        if folder == self._folder or not self._ever_shown:
            return
        self._watch(folder)
        self.refresh()

    def _watch(self, folder: Path) -> None:
        self._folder = folder
        self.where.setText(f"Books saved in {folder}")
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        if folder.is_dir():
            self.watcher.addPath(str(folder))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._ever_shown:
            # Nothing is read before the page is first opened (the app starts without the scan).
            self._ever_shown = True
            self._watch(library_folder(self.context))
        self.refresh()

    def refresh(self) -> None:
        folder = library_folder(self.context)
        started = time.monotonic()
        self._scan_started = started
        if not self.books:
            self.body.setCurrentWidget(self.loading)
            self.loading.start()
            self.empty.setVisible(False)

        def run():
            books = library.scan(folder, lambda done, total: self._scan_progress.emit(done, total, started))
            self._scanned.emit(books, started)

        threading.Thread(target=run, name="library-scan", daemon=True).start()

    def _take(self, books, started: float) -> None:
        if started != self._scan_started:
            return  # a newer scan is on its way
        self.books = books
        self.loading.stop()
        self.body.setCurrentWidget(self.grid)
        self._show()

    # -- showing it, a page at a time --------------------------------------------------------------

    def _show(self, reset: bool = False) -> None:
        if reset:
            self.page = 0
        text = self.search.text()
        books = [book for book in self.books if library.matches(book, text)]
        order = self.sort.currentIndex()
        if order == 1:
            books.sort(key=lambda b: b.title.lower())
        elif order == 2:
            books.sort(key=lambda b: b.size, reverse=True)
        self.shown = books
        pages = max(1, (len(books) + PER_PAGE - 1) // PER_PAGE)
        self.page = min(self.page, pages - 1)
        self._fill()
        if not self.books:
            self.empty.setText("No books yet. Downloads are saved here, and show up as soon as they finish.")
        elif not books:
            self.empty.setText("No book matches.")
        self.empty.setVisible(not books)

    def _fill(self) -> None:
        selected = self._selected()
        start = self.page * PER_PAGE
        page_books = self.shown[start:start + PER_PAGE]
        self._generation += 1
        self.grid.setUpdatesEnabled(False)
        self.grid.clear()
        self._items = {}
        for book in page_books:
            formats = " · ".join(dict.fromkeys(f.format for f in book.files))
            item = QListWidgetItem(self._placeholder, f"{book.title}\n{formats}")
            item.setData(Qt.UserRole, book)
            item.setToolTip("\n".join(filter(None, [book.title, book.author, formats, _size_text(book.size),
                                                    time.strftime("%Y-%m-%d %H:%M", time.localtime(book.modified))])))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(item)
            self._items[book.name] = item
            if selected is not None and book.name == selected.name:
                self.grid.setCurrentItem(item)
        self.grid.setUpdatesEnabled(True)
        pages = max(1, (len(self.shown) + PER_PAGE - 1) // PER_PAGE)
        self.page_label.setText(f"Page {self.page + 1} of {pages}  ·  {len(self.shown)} books")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page < pages - 1)
        for widget in (self.previous, self.next, self.page_label):
            widget.setVisible(pages > 1)
        self._selection_changed()
        self._load_covers(page_books, self._generation)

    def _turn(self, step: int) -> None:
        self.page += step
        self._fill()
        self.grid.scrollToTop()

    def _load_covers(self, books, generation: int) -> None:
        """The page's covers, read and decoded on a worker thread, set as each is ready."""
        def run():
            for book in books:
                if generation != self._generation:
                    return  # another page is shown now
                if not any(f.format == "EPUB" for f in book.files):
                    continue
                self._covered.emit(book.name, _cover_image(library.cover(book)), generation)

        threading.Thread(target=run, name="library-covers", daemon=True).start()

    def _set_cover(self, name: str, image, generation: int) -> None:
        item = self._items.get(name) if generation == self._generation else None
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    def _selected(self):
        item = self.grid.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _selection_changed(self) -> None:
        book = self._selected()
        for button in (self.open_button, self.show_button, self.delete_button):
            button.setEnabled(book is not None)
        self.read_button.setEnabled(book is not None and any(f.format == "EPUB" for f in book.files))
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
        if any(f.format == "EPUB" for f in book.files):
            menu.addAction("Read in the app", lambda: self._read(book))
        for file in book.files:
            menu.addAction(f"Open {file.format}  ({_size_text(file.size)})",
                           lambda f=file: QDesktopServices.openUrl(QUrl.fromLocalFile(str(f.path))))
        menu.addSeparator()
        menu.addAction("Show in folder", lambda: self._reveal(book))
        menu.addAction("Delete…", lambda: self._delete(book))
        menu.exec(self.grid.viewport().mapToGlobal(point))

    def _show_book(self, book) -> None:
        """A book's page, in the Library tab."""
        if book is None:
            return
        self.book_view.show_book(book, _cover_image(library.cover(book)) if book.cover is None else None)
        self.views.setCurrentWidget(self.book_view)

    def _read(self, book) -> None:
        """The EPUB in the Library's reader (from the file: nothing is scraped again); a book without
        one opens with the system app."""
        if book is None:
            return
        if not any(f.format == "EPUB" for f in book.files):
            self._open(book)
            return
        if self.book_view.book is not book:
            self.book_view.show_book(book, _cover_image(library.cover(book)))
        self._read_at(book, -1)

    def _read_at(self, book, chapter: int) -> None:
        contents = self.book_view.contents if self.book_view.book is book else None
        if not contents or not contents.get("chapters"):
            self._open(book)
            return
        self.reader.open_book(book, contents, chapter)
        self.views.setCurrentWidget(self.reader)
        self.reader.setFocus()

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
        self.views.setCurrentIndex(0)
        failed = [f.path.name for f in book.files if not _to_trash(f.path)]
        if failed:
            QMessageBox.warning(self, "Delete book", "These could not be moved to the Recycle Bin:\n" + "\n".join(failed))
        self.refresh()
