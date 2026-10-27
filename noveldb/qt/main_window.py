"""The main window: sidebar pages (Download, Catalog, Queue, Settings) over a log panel."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QWidget,
)

from app_version import VERSION
from noveldb import features
from noveldb.qt.cart_dialog import CartDialog
from noveldb.qt.context import AppContext
from noveldb.qt.log_panel import LogPanel
from noveldb.qt.pages.catalog import catalog_page
from noveldb.qt.pages.download import DownloadPage
from noveldb.qt.pages.queue import QueuePage
from noveldb.qt.pages.settings import SettingsPage
from noveldb.qt.theme import apply_theme

PAGES = ("Download", "Catalog", "Queue", "Settings")


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        context.prompts._parent = lambda: self
        suffix = {"lite": " Lite", "turbolite": " TurboLite"}.get(features.variant(), "")
        self.setWindowTitle(f"NovelDB {VERSION}{suffix}")
        self.setMinimumSize(760, 560)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(176)
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        for name in PAGES:
            self.sidebar.addItem(QListWidgetItem(name))

        self.download = DownloadPage(context)
        self.catalog = catalog_page(context)
        self.queue = QueuePage(context)
        self.settings_page = SettingsPage(context)
        self.stack = QStackedWidget()
        for page in (self.download, self.catalog, self.queue, self.settings_page):
            self.stack.addWidget(page)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.log_panel = LogPanel(context)
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self.log_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar)
        row.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        self.download.openCart.connect(self.show_cart)
        self.download.queued.connect(self._queued)
        self.queue.activeCountChanged.connect(self._active_count)
        self.queue.finishedBatch.connect(self._finished_batch)
        context.hub.message.connect(lambda text: self.statusBar().showMessage(text, 6000))
        context.settingsChanged.connect(self._settings_changed)
        self._appearance = (context.settings.ui.theme, context.settings.ui.accent)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _scheme: self._apply_theme(force=True))
        for index in range(len(PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self, activated=lambda i=index: self.show_page(i))
        self.statusBar().setSizeGripEnabled(True)
        self._restore_geometry()

    # -- navigation ----------------------------------------------------------------------

    def show_page(self, index: int) -> None:
        self.sidebar.setCurrentRow(index)

    def show_cart(self) -> None:
        dialog = CartDialog(self.context, self)
        dialog.useLinks.connect(self.download.add_lines)
        dialog.exec()

    def _queued(self, count: int) -> None:
        if count:
            self.statusBar().showMessage(f"Added {count} download(s) to the queue.", 5000)
            if self.context.runner.paused:
                self.context.runner.resume()

    def _active_count(self, count: int) -> None:
        item = self.sidebar.item(PAGES.index("Queue"))
        item.setText(f"Queue  ({count})" if count else "Queue")

    def _finished_batch(self, done: int, failed: int) -> None:
        text = f"Queue finished: {done} done" + (f", {failed} failed" if failed else "")
        self.statusBar().showMessage(text, 15000)
        QApplication.alert(self)

    # -- appearance ------------------------------------------------------------------------

    def _settings_changed(self) -> None:
        current = (self.context.settings.ui.theme, self.context.settings.ui.accent)
        if current != self._appearance:
            self._appearance = current
            self._apply_theme(force=True)

    def _apply_theme(self, force: bool = False) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, *self._appearance)

    # -- geometry and shutdown -----------------------------------------------------------------

    def _restore_geometry(self) -> None:
        saved = self.context.settings.ui.window_geometry
        if saved:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
                return
            except Exception:
                pass
        self.resize(1040, 760)

    def closeEvent(self, event):
        running = self.context.runner.running()
        if running:
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self, "Downloads running",
                f"{len(running)} download(s) are still running. Stop them and quit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.context.update_settings(lambda s: replace(s, ui=replace(s.ui, window_geometry=geometry)))
        super().closeEvent(event)
