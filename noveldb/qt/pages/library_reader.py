"""The Library's book page and offline reader, from the file itself.

The reader shows one chapter at a time from an EPUB or TXT file. It has a chapter list, previous
and next, text size, three themes, and remembers where each
book was left (`library-reading.json` in the app's data folder). It needs no web view, so every
build has it.
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import library, paths
from noveldb.core.engines.generic import clean_name
from noveldb.qt import icons
from noveldb.qt.scale import bind_title, on_scale
from noveldb.qt.widgets import hint

THEMES = {
    "Light": ("#fbfbf8", "#1f1f24"),
    "Sepia": ("#f4ecd8", "#5b4636"),
    "Dark": ("#141417", "#d8d8de"),
}
PLACES_FILE = "library-reading.json"


def _places_path() -> Path:
    return paths.data_dir() / PLACES_FILE


def _places() -> dict:
    try:
        return json.loads(_places_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_place(key: str, chapter: int, at: float) -> None:
    places = _places()
    places[key] = {"chapter": chapter, "at": round(at, 4)}
    try:
        _places_path().write_text(json.dumps(places), encoding="utf-8")
    except OSError:
        pass


class BookView(QWidget):
    """A Library book's page, laid out like the Catalog's details panel: the cover with its four
    buttons under it in two rows (Read | Open, Show in folder | Delete); the title, what the book is
    and its synopsis; then its chapters, with a box to find one."""

    back = Signal()
    readRequested = Signal(object, int)  # the book, the chapter to start at (-1: where it was left)
    openRequested = Signal(object)
    revealRequested = Signal(object)
    deleteRequested = Signal(object)

    COVER = QSize(220, 320)

    def __init__(self) -> None:
        super().__init__()
        self.book = None
        self.contents = None
        back = QPushButton("‹ Library")
        back.clicked.connect(self.back.emit)
        self.cover = QLabel()
        self.cover.setFixedSize(self.COVER)
        self.cover.setAlignment(Qt.AlignCenter)
        opener = QPushButton("Open")
        opener.setToolTip("Open the preferred format")
        opener.clicked.connect(lambda: self.openRequested.emit(self.book))
        reveal = QPushButton("Show in folder")
        reveal.clicked.connect(lambda: self.revealRequested.emit(self.book))
        self.read = QPushButton("Read")
        self.read.setProperty("primary", True)
        icons.set_icon(self.read, icons.MINING, 24)
        self.read.clicked.connect(lambda: self.readRequested.emit(self.book, -1))
        # Read and Open share a row: two columns as wide as the cover.
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.read, 1)
        actions.addWidget(opener, 1)
        opener.setMinimumHeight(self.read.sizeHint().height())  # one row of one height (Read has its icon)
        delete = QPushButton("🗑 Delete")
        delete.setToolTip("Move the book's files to the Recycle Bin")
        delete.clicked.connect(lambda: self.deleteRequested.emit(self.book))
        files = QHBoxLayout()
        files.setSpacing(8)
        files.addWidget(reveal, 1)
        files.addWidget(delete, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(self.cover)
        side.addLayout(actions)
        side.addLayout(files)
        side.addStretch(1)

        self.title = bind_title(QLabel(), 24, 700)
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.meta = hint("")
        self.synopsis = QLabel()
        self.synopsis.setWordWrap(True)
        self.synopsis.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.synopsis.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        synopsis = QScrollArea()
        synopsis.setWidget(self.synopsis)
        synopsis.setWidgetResizable(True)
        synopsis.setFrameShape(QFrame.NoFrame)
        synopsis.setMinimumHeight(120)
        info = QVBoxLayout()
        info.setSpacing(8)
        info.addWidget(self.title)
        info.addWidget(self.meta)
        info.addWidget(synopsis, 1)
        card = QFrame()
        card.setObjectName("bookCard")
        card.setStyleSheet("#bookCard { border: 1px solid rgba(128, 128, 128, 0.25); border-radius: 12px; }")
        top = QHBoxLayout(card)
        top.setContentsMargins(16, 16, 16, 16)
        top.setSpacing(20)
        top.addLayout(side)
        top.addLayout(info, 1)

        self.count = hint("")
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find a chapter")
        self.find.setClearButtonEnabled(True)
        self.find.setMaximumWidth(320)
        self.find.textChanged.connect(self._filter)
        heading = QHBoxLayout()
        heading.addWidget(self.count, 1)
        heading.addWidget(self.find)
        self.chapters = QListWidget()
        self.chapters.setAlternatingRowColors(True)
        self.chapters.setStyleSheet("QListWidget::item { padding: 6px 10px; }")
        # List views get the platform's smaller font on Windows: the app's, as everywhere else.
        on_scale(self.chapters, lambda: self.chapters.setFont(QApplication.font()))
        self.chapters.itemActivated.connect(self._activated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(back, 0, Qt.AlignLeft)
        layout.addWidget(card)
        layout.addLayout(heading)
        layout.addWidget(self.chapters, 1)

    def _activated(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.UserRole)
        if isinstance(index, int):
            self.readRequested.emit(self.book, index)

    def _filter(self, text: str) -> None:
        words = text.strip().lower()
        for row in range(self.chapters.count()):
            item = self.chapters.item(row)
            item.setHidden(bool(words) and words not in item.text().lower())

    @staticmethod
    def _size(size: int) -> str:
        if size <= 0:
            return ""
        return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, round(size / 1024))} KB"

    def show_book(self, book, cover: Optional[QImage]) -> None:
        self.book = book
        self.title.setText(book.title)
        picture = QPixmap.fromImage(cover) if cover is not None and not cover.isNull() else QPixmap(icons.path("halgakos-cover.png"))
        if picture.isNull():
            self.cover.clear()
        else:
            self.cover.setPixmap(picture.scaled(self.cover.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.chapters.clear()
        self.find.clear()
        file = book.readable()
        self.read.setEnabled(file is not None)
        self.contents = None
        if file is not None:
            try:
                self.contents = library.book_contents(file.path)
            except Exception:
                self.contents = None
        shown = 0
        for index, chapter in enumerate((self.contents or {}).get("chapters", [])):
            name = clean_name(chapter["name"]) or chapter["name"]
            if not shown and _FRONT_MATTER.match(name):
                continue  # the EPUB's cover or title page, not a chapter
            shown += 1
            item = QListWidgetItem(f"{shown}.  {name}")
            item.setData(Qt.UserRole, index)
            self.chapters.addItem(item)
        if self.contents is None:
            self.chapters.addItem("No readable EPUB or TXT file.")
        formats = " · ".join(dict.fromkeys(f.format for f in book.files))
        count = f"{shown} chapter{'s' if shown != 1 else ''}" if self.contents is not None else ""
        self.meta.setText(" · ".join(filter(None, [book.author, formats, self._size(book.size), count])))
        self.count.setText(f"Chapters ({shown}) · double-click one to read from it" if shown else "Chapters")
        self.synopsis.setText(_synopsis(library.description(book) or "") or "No synopsis available.")


# The EPUB's own pages before the first chapter.
_FRONT_MATTER = re.compile(r"^(cover|title( page)?|table of contents|contents|toc|nav|copyright)$", re.I)
_LEAD = re.compile(r"^\s*(summary|synopsis|description|introduction)\s*[:：]?\s*", re.I)
_TAIL = re.compile(r"\s*(show (more|less)|read more)\s*$", re.I)


def _synopsis(text: str) -> str:
    """The synopsis without the words a site's page put around it ("Summary …", "… Show More")."""
    return _TAIL.sub("", _LEAD.sub("", text.strip())).strip()


class _Page(QTextBrowser):
    """A chapter's HTML, its images read from the EPUB."""

    def __init__(self) -> None:
        super().__init__()
        self.epub: Optional[Path] = None
        self.folder = ""
        self.setOpenLinks(False)

    def loadResource(self, kind, url: QUrl):
        if kind == QTextDocument.ImageResource and self.epub is not None and not url.scheme():
            name = posixpath.normpath(posixpath.join(self.folder, url.toString().split("#")[0]))
            data = library.read_member(self.epub, name)
            if data:
                image = QImage()
                if image.loadFromData(data):
                    width = max(200, self.viewport().width() - 60)
                    return image.scaledToWidth(width, Qt.SmoothTransformation) if image.width() > width else image
        return super().loadResource(kind, url)


class LocalBookReader(QWidget):
    """Reads a Library EPUB: one chapter at a time, with a chapter list and the place remembered."""

    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.book = None
        self.file: Optional[Path] = None
        self.chapters = []
        self.index = 0
        self.size = 15
        self.theme = "Dark"
        back = QPushButton("‹ Book")
        back.clicked.connect(self._leave)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        smaller, larger = QPushButton("A−"), QPushButton("A+")
        smaller.clicked.connect(lambda: self._resize(-1))
        larger.clicked.connect(lambda: self._resize(1))
        self.themes = QComboBox()
        self.themes.addItems(list(THEMES))
        self.themes.setCurrentText(self.theme)
        self.themes.currentTextChanged.connect(self._set_theme)
        # The contents panel shows or hides (it starts shown; the choice is not kept).
        self.contents = QPushButton("☰ Contents")
        self.contents.setCheckable(True)
        self.contents.setChecked(True)
        self.contents.toggled.connect(lambda on: self.toc.setVisible(on))
        top = QHBoxLayout()
        top.addWidget(back)
        top.addWidget(self.contents)
        top.addWidget(self.title, 1)
        top.addWidget(smaller)
        top.addWidget(larger)
        top.addWidget(self.themes)

        # The contents: one line per chapter, cut short with "…" (the full name in its tooltip), in the
        # app's font and the reader's colours; no sideways scrolling.
        self.toc = QListWidget()
        self.toc.setMinimumWidth(180)
        self.toc.setMaximumWidth(420)
        self.toc.setFrameShape(QFrame.NoFrame)
        self.toc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.toc.setTextElideMode(Qt.ElideRight)
        self.toc.setWordWrap(False)
        self.toc.setUniformItemSizes(True)
        on_scale(self.toc, lambda: self.toc.setFont(QApplication.font()))
        self.toc.currentRowChanged.connect(lambda row: self.open_chapter(row) if row >= 0 and row != self.index else None)
        self.page = _Page()
        self.page.verticalScrollBar().valueChanged.connect(lambda _v: self._remember_soon())
        # The text view would take the arrow keys for itself: the reader turns the pages with them.
        self.page.installEventFilter(self)
        self.page.viewport().installEventFilter(self)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.toc)
        splitter.addWidget(self.page)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        # A thin line to drag, not the app's wide splitter grip.
        splitter.setHandleWidth(5)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(128, 128, 128, 0.22); margin: 0 2px; image: none; }")
        splitter.setSizes([280, 1000])

        self.previous = QPushButton("‹ Previous chapter")
        self.previous.clicked.connect(lambda: self.open_chapter(self.index - 1))
        self.next = QPushButton("Next chapter ›")
        self.next.clicked.connect(lambda: self.open_chapter(self.index + 1))
        self.where = hint("")
        bottom = QHBoxLayout()
        bottom.addWidget(self.previous)
        bottom.addStretch(1)
        bottom.addWidget(self.where)
        bottom.addStretch(1)
        bottom.addWidget(self.next)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        self._remember = QTimer(self, singleShot=True, interval=600)
        self._remember.timeout.connect(self._save)
        self._apply_look()

    def open_book(self, book, contents: dict, start: int = -1) -> None:
        self.book = book
        self.file = book.readable().path
        self.page.epub = self.file if self.file.suffix.lower() == ".epub" else None
        self._set_chapters(contents.get("chapters", []))
        place = _places().get(str(self.file), {})
        at = 0.0
        if start < 0:
            start, at = int(place.get("chapter", 0)), float(place.get("at", 0.0))
        self.open_chapter(min(max(0, start), max(0, len(self.chapters) - 1)), at)

    def _set_chapters(self, chapters) -> None:
        self.chapters = chapters
        self.toc.blockSignals(True)
        self.toc.clear()
        # Numbered as on the book page: the EPUB's cover and title pages before the first chapter carry no number.
        shown = 0
        for chapter in self.chapters:
            name = clean_name(chapter["name"]) or chapter["name"]
            if not shown and _FRONT_MATTER.match(name):
                item = QListWidgetItem(name.capitalize())
            else:
                shown += 1
                item = QListWidgetItem(f"{shown}.  {name}")
            item.setToolTip(name)
            self.toc.addItem(item)
        self.toc.blockSignals(False)

    def refresh_contents(self, contents) -> None:
        chapters = contents.get("chapters", [])
        if not chapters or not self.chapters:
            return
        current = self.chapters[self.index]
        index = next((i for i, chapter in enumerate(chapters) if chapter.get("href", chapter["name"])
                      == current.get("href", current["name"])), -1)
        self._set_chapters(chapters)
        if index < 0:
            bar = self.page.verticalScrollBar()
            self.open_chapter(min(self.index, len(chapters) - 1), bar.value() / max(1, bar.maximum()))
            return
        self.index = index
        self.toc.blockSignals(True)
        self.toc.setCurrentRow(index)
        self.toc.blockSignals(False)
        self.where.setText(f"Chapter {index + 1} of {len(chapters)}")
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(index + 1 < len(chapters))

    def open_chapter(self, index: int, at: float = 0.0) -> None:
        if not (0 <= index < len(self.chapters)):
            return
        self.index = index
        chapter = self.chapters[index]
        try:
            html, folder = library.chapter_source(self.file, chapter["href"]) if "href" in chapter else ("", "")
        except Exception as e:
            html, folder = f"<p>This chapter could not be read: {e}</p>", ""
        self.page.folder = folder
        if "text" in chapter:
            self.page.setPlainText(chapter["text"])
        else:
            self.page.setHtml(html)
        self.toc.blockSignals(True)
        self.toc.setCurrentRow(index)
        self.toc.blockSignals(False)
        self.title.setText(f"{self.book.title} · {chapter['name']}")
        self.where.setText(f"Chapter {index + 1} of {len(self.chapters)}")
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(index + 1 < len(self.chapters))
        bar = self.page.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(int(at * bar.maximum())))
        self._remember_soon()

    TURN_KEYS = (Qt.Key_Right, Qt.Key_Left, Qt.Key_PageDown, Qt.Key_PageUp, Qt.Key_Space)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() in self.TURN_KEYS:
            self.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        bar = self.page.verticalScrollBar()
        if event.key() in (Qt.Key_Right, Qt.Key_PageDown, Qt.Key_Space):
            if bar.value() >= bar.maximum():
                self.open_chapter(self.index + 1)
            else:
                bar.setValue(bar.value() + self.page.viewport().height() - 40)
        elif event.key() in (Qt.Key_Left, Qt.Key_PageUp):
            if bar.value() <= 0 and self.index > 0:
                self.open_chapter(self.index - 1, 1.0)
            else:
                bar.setValue(bar.value() - self.page.viewport().height() + 40)
        elif event.key() == Qt.Key_Escape:
            self._leave()
        else:
            super().keyPressEvent(event)

    def _resize(self, step: int) -> None:
        self.size = max(10, min(32, self.size + step))
        self._apply_look()

    def _set_theme(self, name: str) -> None:
        self.theme = name
        self._apply_look()

    def _apply_look(self) -> None:
        background, ink = THEMES.get(self.theme, THEMES["Dark"])
        self.page.setStyleSheet(f"QTextBrowser {{ background: {background}; color: {ink}; border: none; padding: 24px 48px; }}")
        # The contents in the page's colours: a quieter ink, the chapter being read picked out.
        self.toc.setStyleSheet(
            f"QListWidget {{ background: {background}; color: {ink}; border: none; padding: 6px; outline: none; }}"
            f"QListWidget::item {{ padding: 7px 10px; border-radius: 6px; margin: 1px 0; }}"
            f"QListWidget::item:hover {{ background: rgba(128, 128, 128, 0.16); }}"
            f"QListWidget::item:selected {{ background: rgba(225, 143, 152, 0.30); color: {ink}; font-weight: 600; }}")
        font = QFont("Georgia")
        font.setPointSize(self.size)
        self.page.document().setDefaultFont(font)
        self.page.document().setDefaultStyleSheet(f"p {{ line-height: 160%; margin-bottom: 10px; }} img {{ }}")

    def _remember_soon(self) -> None:
        self._remember.start()

    def _save(self) -> None:
        if self.file is None:
            return
        bar = self.page.verticalScrollBar()
        _save_place(str(self.file), self.index, bar.value() / bar.maximum() if bar.maximum() else 0.0)

    def _leave(self) -> None:
        self._save()
        self.back.emit()
