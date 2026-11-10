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

from PySide6.QtCore import QEvent, QFile, QFileSystemWatcher, QPersistentModelIndex, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPalette, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyledItemDelegate,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
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
from noveldb.qt.pages.library_reader import BookView, LocalBookReader
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


class _BookCard(QStyledItemDelegate):
    """A book on the shelf as a card: the cover with rounded corners, its title and formats below. Hovered, it
    lifts with a shadow; pressed, it sinks a little; picked, the cover gets an accent ring (the list's own
    highlight tinted the whole cell, cover and all)."""

    RADIUS = 10

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self.view = view
        self.pressed = QPersistentModelIndex()
        view.setMouseTracking(True)
        view.viewport().setAttribute(Qt.WA_Hover, True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.pressed = QPersistentModelIndex(self.view.indexAt(event.position().toPoint()))
            self.view.viewport().update()
        elif kind in (QEvent.MouseButtonRelease, QEvent.Leave) and self.pressed.isValid():
            self.pressed = QPersistentModelIndex()
            self.view.viewport().update()
        return False

    def sizeHint(self, option, index) -> QSize:
        return self.view.gridSize()

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        palette = option.palette
        accent = palette.color(QPalette.Highlight)
        hovered = bool(option.state & QStyle.State_MouseOver)
        picked = bool(option.state & QStyle.State_Selected)
        pressed = self.pressed.isValid() and self.pressed == QPersistentModelIndex(index)
        cell = QRectF(option.rect).adjusted(4, 4, -4, -4)

        # The card behind cover and text: only when hovered or picked (the shelf stays calm otherwise).
        if hovered or picked:
            tint = QColor(accent)
            tint.setAlpha(34 if picked else 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(tint)
            painter.drawRoundedRect(cell, self.RADIUS + 4, self.RADIUS + 4)

        cover = QRectF(0, 0, COVER.width(), COVER.height())
        cover.moveCenter(cell.center())
        cover.moveTop(cell.top() + 8)
        if pressed:
            cover = cover.adjusted(4, 5, -4, -5)  # sinks under the finger
        elif hovered:
            cover.translate(0, -3)  # lifts
        # A soft shadow under the cover, deeper when lifted.
        depth = 0 if pressed else (7 if hovered else 3)
        for step in range(depth, 0, -1):
            shade = QColor(0, 0, 0, 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(shade)
            painter.drawRoundedRect(cover.adjusted(-step / 2, step / 2, step / 2, step), self.RADIUS + step / 2, self.RADIUS + step / 2)
        clip = QPainterPath()
        clip.addRoundedRect(cover, self.RADIUS, self.RADIUS)
        painter.setClipPath(clip)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(COVER)
            if not pixmap.isNull():
                # Filled, cut to the cover's shape (a narrower picture is not letterboxed).
                scaled = pixmap.scaled(cover.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                x = cover.left() + (cover.width() - scaled.width()) / 2
                y = cover.top() + (cover.height() - scaled.height()) / 2
                painter.drawPixmap(int(x), int(y), scaled)
        if pressed:
            painter.fillRect(cover, QColor(0, 0, 0, 40))
        painter.setClipping(False)
        badge = index.data(Qt.UserRole + 1)
        if badge:
            badge_rect = QRectF(cover.left() + 4, cover.top() + 4, cover.width() - 8, 24)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(badge_rect, 6, 6)
            painter.setPen(palette.color(QPalette.HighlightedText))
            painter.drawText(badge_rect, Qt.AlignCenter, badge)
        if picked:
            ring = QColor(accent)
            painter.setPen(ring)
            painter.setBrush(Qt.NoBrush)
            pen = painter.pen()
            pen.setWidthF(2.5)
            painter.setPen(pen)
            painter.drawRoundedRect(cover.adjusted(-2, -2, 2, 2), self.RADIUS + 2, self.RADIUS + 2)

        # Title (two lines at most) and its formats in a quieter ink.
        lines = str(index.data(Qt.DisplayRole) or "").split("\n")
        title, formats = lines[0], " ".join(lines[1:])
        text = QRectF(cell.left() + 6, cell.top() + 8 + COVER.height() + 8, cell.width() - 12, cell.bottom() - cell.top())
        font = option.font
        font.setWeight(font.Weight.DemiBold if picked else font.Weight.Medium)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        ink = palette.color(QPalette.Text)
        painter.setPen(ink)
        # Broken at a space where one fits, else between any two characters (Japanese and Chinese have none).
        rest, first = title.strip(), ""
        if metrics.horizontalAdvance(rest) > text.width():
            cut = len(rest)
            while cut > 1 and metrics.horizontalAdvance(rest[:cut]) > text.width():
                cut -= 1
            space = rest.rfind(" ", 0, cut + 1)
            if space > cut // 2:
                cut = space
            first, rest = rest[:cut].rstrip(), rest[cut:].lstrip()
        shown = [part for part in (first, metrics.elidedText(rest, Qt.ElideRight, int(text.width()))) if part]
        y = text.top()
        for part in shown:
            painter.drawText(QRectF(text.left(), y, text.width(), metrics.height()), Qt.AlignHCenter | Qt.AlignTop, part)
            y += metrics.height()
        if formats:
            small = option.font
            small.setPointSizeF(max(7.0, small.pointSizeF() - 1.5))
            painter.setFont(small)
            muted = QColor(ink)
            muted.setAlpha(150)
            painter.setPen(muted)
            painter.drawText(QRectF(text.left(), y + 1, text.width(), QFontMetrics(small).height()), Qt.AlignHCenter | Qt.AlignTop,
                             QFontMetrics(small).elidedText(formats, Qt.ElideRight, int(text.width())))
        painter.restore()


class Loading(QWidget):
    """Halgakos turning, with a progress bar, while the Library folder is read."""

    def __init__(self) -> None:
        super().__init__()
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setFixedSize(QSize(200, 200))
        self.bar = QProgressBar()
        # Tall and wide enough for its "30 of 669" text, and the note below on one line.
        self.bar.setFixedWidth(360)
        self.bar.setMinimumHeight(24)
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.setTextVisible(True)
        self.text = hint("Reading the Library folder…")
        self.text.setWordWrap(False)
        self.text.setMinimumWidth(360)
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
    _scan_found = Signal(object, float)  # the books read so far, while the folder is still being read
    _scan_progress = Signal(int, int, float)
    _covered = Signal(str, object, int)  # book name, cover QImage, page generation
    openInCatalog = Signal(dict)  # {title, url, author}: the book found again in the Catalog

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.books: List[library.LibraryBook] = []
        self.shown: List[library.LibraryBook] = []
        self.page = 0
        self._generation = 0
        self._scan_started = 0.0
        self._reading = None  # (done, total) while the folder is read with books already shown
        self._places = None  # book name: its place, kept while books stream in (no reshuffling)
        # (EPUB path, title, chapter or -1) -> the book in the Catalog's reader; set by the main window.
        self.read_in_catalog = None
        self._items = {}  # book name: its item on the page shown
        self._covers = {}  # (book name, modified): its decoded cover, kept while the page refills
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
        library_settings = QPushButton("Library settings")
        library_settings.clicked.connect(self._library_settings)
        bar.addWidget(library_settings)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setIconSize(COVER)
        self.grid.setGridSize(QSize(COVER.width() + 34, COVER.height() + 74))
        self.grid.setWordWrap(True)
        self.grid.setSpacing(8)
        self.grid.setUniformItemSizes(True)
        self.grid.setItemDelegate(_BookCard(self.grid))
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
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
        for label, enabled in (("Enable auto-update", True), ("Disable auto-update", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=enabled: self._set_auto_update(value))
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
        self.reader = LocalBookReader()
        self.read_loading = Loading()
        self.read_loading.text.setText("Updating book…")
        close_loading = QPushButton("Close")
        close_loading.clicked.connect(self._close_pending_read)
        self.read_loading.layout().addWidget(close_loading, 0, Qt.AlignHCenter)
        self._pending_read = None
        self.views = QStackedWidget()
        for view in (shelf, self.book_view, self.reader, self.read_loading):
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
        self._scan_found.connect(self._take_some)
        self._scan_progress.connect(self._progress)
        self._covered.connect(self._set_cover)
        context.hub.jobSaved.connect(lambda *_a: self._later.start() if self._ever_shown else None)
        context.settingsChanged.connect(self._folder_changed)
        context.libraryChanged.connect(self._update_badges)
        context.hub.jobChanged.connect(lambda *_: self._update_badges())
        context.hub.jobChanged.connect(self._resume_read)
        context.hub.jobSaved.connect(self._refresh_open_book)
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

    def hideEvent(self, event) -> None:
        if self._pending_read is not None:
            self._close_pending_read()
        super().hideEvent(event)

    def refresh(self) -> None:
        folder = library_folder(self.context)
        started = time.monotonic()
        self._scan_started = started
        if self.books:
            self._places = None  # a later reading shows the books in their order once it is done
        else:
            self.body.setCurrentWidget(self.loading)
            self.loading.start()
            self.empty.setVisible(False)

        streaming = not self.books  # a refresh keeps the books shown until it is done

        def run():
            books = library.scan(folder, lambda done, total: self._scan_progress.emit(done, total, started),
                                 (lambda some: self._scan_found.emit(some, started)) if streaming else None)
            self._scanned.emit(books, started)

        threading.Thread(target=run, name="library-scan", daemon=True).start()

    def _progress(self, done: int, total: int, started: float) -> None:
        if started != self._scan_started:
            return
        self.loading.progress(done, total)
        if self._reading is not None and done < total:
            self._reading = (done, total)
            self._label()

    def _take_some(self, books, started: float) -> None:
        """The books read so far, shown while the rest of the folder is read."""
        if started != self._scan_started:
            return
        if self._reading is None:
            self._reading = (0, 0)
            self._places = {}
            self.loading.stop()
            self.body.setCurrentWidget(self.grid)
        self.books = books
        self.context.register_library_books(books)
        self._show()

    def _take(self, books, started: float) -> None:
        if started != self._scan_started:
            return  # a newer scan is on its way
        self._reading = None
        self.books = books
        self.context.register_library_books(books)
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
        if self._places is not None:
            if reset:
                self._places = None  # the reader picked another order or search: it applies at once
            else:
                # Books keep the places they were shown in; the ones read since come after them.
                for book in books:
                    self._places.setdefault(book.name, len(self._places))
                books.sort(key=lambda b: self._places[b.name])
        self.shown = books
        pages = max(1, (len(books) + PER_PAGE - 1) // PER_PAGE)
        self.page = min(self.page, pages - 1)
        start = self.page * PER_PAGE
        if not reset and [b.name for b in books[start:start + PER_PAGE]] == list(self._items):
            self._label()  # the page shows the same books: only the counts change
        else:
            self._fill()
        if self._reading is not None and not books:
            self.empty.setText("Reading the Library folder…")
        elif not self.books:
            self.empty.setText("No books yet. Downloads are saved here, and show up as soon as they finish.")
        elif not books:
            self.empty.setText("No book matches.")
        self.empty.setVisible(not books)

    def _fill(self) -> None:
        selected = {item.data(Qt.UserRole).name for item in self.grid.selectedItems()}
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
            item.setData(Qt.UserRole + 1, self._badge(book))
            item.setToolTip("\n".join(filter(None, [book.title, book.author, formats, _size_text(book.size),
                                                    time.strftime("%Y-%m-%d %H:%M", time.localtime(book.modified))])))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(item)
            self._items[book.name] = item
            if book.name in selected:
                item.setSelected(True)
        self.grid.setUpdatesEnabled(True)
        self._label()
        self._selection_changed()
        self._load_covers(page_books, self._generation)

    def _label(self) -> None:
        pages = max(1, (len(self.shown) + PER_PAGE - 1) // PER_PAGE)
        text = f"Page {self.page + 1} of {pages}  ·  {len(self.shown)} books"
        if self._reading is not None and self._reading[1]:
            text += f"  ·  reading {self._reading[0]} of {self._reading[1]}…"
        self.page_label.setText(text)
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page < pages - 1)
        for widget in (self.previous, self.next, self.page_label):
            widget.setVisible(pages > 1 or self._reading is not None)

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
                key = (book.name, book.modified)
                image = self._covers.get(key)
                if image is None:
                    if len(self._covers) > 200:
                        self._covers.clear()  # a few pages' worth, no more
                    image = self._covers[key] = _cover_image(library.cover(book))
                self._covered.emit(book.name, image, generation)

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

    @staticmethod
    def _epub(book):
        return next((f.path for f in book.files if f.format == "EPUB"), None)

    def _badge(self, book):
        path = self._epub(book)
        if path is None:
            return ""
        job = next((j for j in self.context.runner.jobs if j.active and j.library_target == str(path.resolve())), None)
        if job:
            return "Updating…" if job.state == "running" else "Update queued"
        return "Auto-update" if self.context.library_updates.allowed(path) else ""

    def _update_badges(self):
        for item in self._items.values():
            item.setData(Qt.UserRole + 1, self._badge(item.data(Qt.UserRole)))

    def _set_auto_update(self, enabled):
        books = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
        self.context.set_library_auto_update([p for p in map(self._epub, books) if p], enabled)

    def _library_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Library settings")
        layout = QVBoxLayout(dialog)
        checks = []
        for key, label in (("enabled", "Allow Library auto-updates"),
                           ("background", "Background auto-update every 15 minutes while the app is open"),
                           ("uploadDrive", "Upload refreshed EPUBs to Google Drive")):
            check = QCheckBox(label)
            check.setChecked(self.context.library_updates.data[key])
            layout.addWidget(check)
            checks.append((key, check))
        layout.addWidget(hint("Opening a book checks for updates when its auto-update is enabled.\n"
                              "Enabled books refresh the full novel.\n"
                              "Full downloads default on. Ranged downloads default off.\n"
                              "Books without saved range history start off until enabled.\n"
                              "Google Drive uploads are shared with anyone who has the link."))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            changes = {key: check.isChecked() for key, check in checks}
            if changes["uploadDrive"] and not self.context.settings.upload.agreement_accepted:
                from dataclasses import replace
                self.context.update_settings(lambda s: replace(s, upload=replace(s.upload, agreement_accepted=True)))
            self.context.configure_library(**changes)

    def _menu(self, point) -> None:
        item = self.grid.itemAt(point)
        if item is None:
            return
        book = item.data(Qt.UserRole)
        menu = QMenu(self)
        action = menu.addAction("Auto-update this book")
        action.setCheckable(True)
        path = self._epub(book)
        action.setEnabled(path is not None and bool(self.context.library_updates.entry(path).get("url")))
        action.setChecked(bool(path and self.context.library_updates.preferred(path)))
        action.triggered.connect(lambda enabled: self.context.set_library_auto_update([path], enabled))
        if book.readable() is not None:
            menu.addAction("Read in the app", lambda: self._read(book))
        for file in book.files:
            menu.addAction(f"Open {file.format}  ({_size_text(file.size)})",
                           lambda f=file: QDesktopServices.openUrl(QUrl.fromLocalFile(str(f.path))))
        # The book in the Catalog, to download what an unfinished or outdated copy is missing.
        menu.addAction("Open in Catalog", lambda: self.openInCatalog.emit(
            {"title": book.title, "url": book.url, "author": book.author}))
        menu.addSeparator()
        menu.addAction("Show in folder", lambda: self._reveal(book))
        menu.addAction("Delete…", lambda: self._delete(book))
        menu.exec(self.grid.viewport().mapToGlobal(point))

    def _show_book(self, book) -> None:
        """A book's page, in the Library tab."""
        if book is None:
            return
        if self._epub(book):
            self.context.request_library_update(self._epub(book))
        self.book_view.show_book(book, _cover_image(library.cover(book)) if book.cover is None else None)
        self.views.setCurrentWidget(self.book_view)

    def _read(self, book) -> None:
        """Read the book's preferred readable file."""
        if book is None:
            return
        if self._epub(book):
            self.context.request_library_update(self._epub(book))
        if book.readable() is None:
            self._open(book)
            return
        if self.book_view.book is not book:
            self.book_view.show_book(book, _cover_image(library.cover(book)))
        self._read_at(book, -1)

    def _read_at(self, book, chapter: int) -> None:
        # The Catalog's reader where there is one (its layouts, Google Translate and the app's theme); the
        # Library's own plain reader in a build without the web view.
        file = book.readable()
        if file is None:
            return
        if any(j.active and j.library_target == str(file.path.resolve()) for j in self.context.runner.jobs):
            self._pending_read = (book, chapter)
            self.read_loading.start()
            self.views.setCurrentWidget(self.read_loading)
            return
        if self.read_in_catalog is not None and library_folder(self.context).resolve() in file.path.resolve().parents:
            self.read_in_catalog(file.path, book.title, chapter)
            return
        contents = self.book_view.contents if self.book_view.book is book else None
        if not contents or not contents.get("chapters"):
            QMessageBox.warning(self, "Open book", "This file has no readable chapters.")
            return
        self.reader.open_book(book, contents, chapter)
        self.views.setCurrentWidget(self.reader)
        self.reader.setFocus()

    def _close_pending_read(self) -> None:
        self._pending_read = None
        self.read_loading.stop()
        self.views.setCurrentWidget(self.book_view)

    def _resume_read(self, *_args) -> None:
        if self._pending_read is None:
            return
        book, chapter = self._pending_read
        file = book.readable()
        if file is not None and any(j.active and j.library_target == str(file.path.resolve()) for j in self.context.runner.jobs):
            return
        self._pending_read = None
        self.read_loading.stop()
        self.book_view.show_book(book, _cover_image(library.cover(book)))
        self._read_at(book, chapter)

    def _refresh_open_book(self, _job_id, path) -> None:
        book = self.book_view.book
        if book is not None and self._epub(book) == Path(path):
            self.book_view.show_book(book, _cover_image(library.cover(book)))

    def _open(self, book) -> None:
        if book is not None and book.readable() is not None:
            self._read(book)
            return
        file = book.preferred() if book is not None else None
        if file is not None:
            platform_services.open_path(file.path)

    def read_output(self, path: Path) -> None:
        """Open a completed Queue output, including files in a job's custom save folder."""
        path = Path(path)
        try:
            stat = path.stat()
        except OSError:
            return
        book = library.LibraryBook(path.stem, path.stem, files=[
            library.LibraryFile(path, path.suffix[1:].upper(), stat.st_size, stat.st_mtime),
        ])
        self._read(book)

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
