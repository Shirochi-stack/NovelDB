"""The Library's book page and EPUB reader: everything inside the Library tab, from the file itself.

The reader shows one chapter at a time from the EPUB (never the site), with its images read from the
book; it has a chapter list, previous and next, text size, three themes, and remembers where each
book was left (`library-reading.json` in the app's data folder). It needs no web view, so every
build has it.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import library, paths
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
    """A Library book's page: its cover, title, author, synopsis, files and chapters."""

    back = Signal()
    readRequested = Signal(object, int)  # the book, the chapter to start at (-1: where it was left)
    openRequested = Signal(object)
    revealRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.book = None
        self.contents = None
        back = QPushButton("‹ Library")
        back.clicked.connect(self.back.emit)
        self.cover = QLabel()
        self.cover.setFixedSize(QSize(210, 300))
        self.cover.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setWordWrap(True)
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        self.title.setFont(font)
        self.meta = hint("")
        self.synopsis = QLabel()
        self.synopsis.setWordWrap(True)
        self.synopsis.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.synopsis.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.read = QPushButton("Read")
        self.read.setProperty("primary", True)
        self.read.clicked.connect(lambda: self.readRequested.emit(self.book, -1))
        opener = QPushButton("Open")
        opener.setToolTip("Open it with the app your system uses for it")
        opener.clicked.connect(lambda: self.openRequested.emit(self.book))
        reveal = QPushButton("Show in folder")
        reveal.clicked.connect(lambda: self.revealRequested.emit(self.book))
        delete = QPushButton("Delete")
        delete.clicked.connect(lambda: self.deleteRequested.emit(self.book))
        buttons = QHBoxLayout()
        for button in (self.read, opener, reveal, delete):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.chapters = QListWidget()
        self.chapters.itemActivated.connect(lambda item: self.readRequested.emit(self.book, self.chapters.row(item)))

        info = QVBoxLayout()
        info.addWidget(self.title)
        info.addWidget(self.meta)
        info.addLayout(buttons)
        info.addWidget(self.synopsis, 1)
        top = QHBoxLayout()
        top.addWidget(self.cover, 0, Qt.AlignTop)
        top.addLayout(info, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(back, 0, Qt.AlignLeft)
        layout.addLayout(top)
        layout.addWidget(hint("Chapters (double-click one to read from it)"))
        layout.addWidget(self.chapters, 1)

    def show_book(self, book, cover: Optional[QImage]) -> None:
        self.book = book
        self.title.setText(book.title)
        formats = " · ".join(dict.fromkeys(f.format for f in book.files))
        size = sum(f.size for f in book.files) / 1048576
        self.meta.setText(" · ".join(filter(None, [book.author, formats, f"{size:.1f} MB"])))
        self.synopsis.setText(library.description(book) or "")
        if cover is not None and not cover.isNull():
            self.cover.setPixmap(QPixmap.fromImage(cover).scaled(self.cover.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.cover.clear()
        self.chapters.clear()
        epub = next((f for f in book.files if f.format == "EPUB"), None)
        self.read.setEnabled(epub is not None)
        self.contents = None
        if epub is not None:
            try:
                self.contents = library.epub_contents(epub.path)
            except Exception:
                self.contents = None
        for index, chapter in enumerate((self.contents or {}).get("chapters", []), 1):
            self.chapters.addItem(QListWidgetItem(f"{index}. {chapter['name']}"))
        if self.contents is None:
            self.chapters.addItem("No EPUB: this book opens with your system's app.")


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


class EpubReader(QWidget):
    """Reads a Library EPUB: one chapter at a time, with a chapter list and the place remembered."""

    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.book = None
        self.epub: Optional[Path] = None
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
        top = QHBoxLayout()
        top.addWidget(back)
        top.addWidget(self.title, 1)
        top.addWidget(smaller)
        top.addWidget(larger)
        top.addWidget(self.themes)

        self.toc = QListWidget()
        self.toc.setMaximumWidth(320)
        self.toc.currentRowChanged.connect(lambda row: self.open_chapter(row) if row >= 0 and row != self.index else None)
        self.page = _Page()
        self.page.verticalScrollBar().valueChanged.connect(lambda _v: self._remember_soon())
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.toc)
        splitter.addWidget(self.page)
        splitter.setStretchFactor(1, 1)

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
        self.epub = next(f.path for f in book.files if f.format == "EPUB")
        self.chapters = contents.get("chapters", [])
        self.page.epub = self.epub
        self.toc.blockSignals(True)
        self.toc.clear()
        for index, chapter in enumerate(self.chapters, 1):
            self.toc.addItem(f"{index}. {chapter['name']}")
        self.toc.blockSignals(False)
        place = _places().get(str(self.epub), {})
        at = 0.0
        if start < 0:
            start, at = int(place.get("chapter", 0)), float(place.get("at", 0.0))
        self.open_chapter(min(max(0, start), max(0, len(self.chapters) - 1)), at)

    def open_chapter(self, index: int, at: float = 0.0) -> None:
        if not (0 <= index < len(self.chapters)):
            return
        self.index = index
        chapter = self.chapters[index]
        try:
            html, folder = library.chapter_source(self.epub, chapter["href"])
        except Exception as e:
            html, folder = f"<p>This chapter could not be read: {e}</p>", ""
        self.page.folder = folder
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
        font = QFont("Georgia")
        font.setPointSize(self.size)
        self.page.document().setDefaultFont(font)
        self.page.document().setDefaultStyleSheet(f"p {{ line-height: 160%; margin-bottom: 10px; }} img {{ }}")

    def _remember_soon(self) -> None:
        self._remember.start()

    def _save(self) -> None:
        if self.epub is None:
            return
        bar = self.page.verticalScrollBar()
        _save_place(str(self.epub), self.index, bar.value() / bar.maximum() if bar.maximum() else 0.0)

    def _leave(self) -> None:
        self._save()
        self.back.emit()
