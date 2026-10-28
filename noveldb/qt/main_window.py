"""The main window: sidebar pages (Download, Catalog, Queue, Settings) over a log panel."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QByteArray, QSize, Qt
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
# Saved geometry is prefixed with a version: sizes saved before display scaling followed the OS
# setting were a third too small, so they are not restored.
GEOMETRY_VERSION = "2:"


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        context.prompts._parent = lambda: self
        suffix = {"lite": " Lite", "turbolite": " TurboLite"}.get(features.variant(), "")
        self.setWindowTitle(f"NovelDB {VERSION}{suffix}")
        self.setMinimumSize(900, 620)
        self._start_maximized = False

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(176)
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        for name in PAGES:
            item = QListWidgetItem(name)
            # Explicit height: QListView lays rows out from the unstyled size, so the stylesheet's
            # padding made rows overlap.
            item.setSizeHint(QSize(0, 44))
            self.sidebar.addItem(item)

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

        from noveldb.qt.tools import Tools

        self.tools = Tools(context, self)
        self.download.toolRequested.connect(self.tools.run)
        context.toolRequested.connect(self.tools.run)
        self.download.openCart.connect(self.show_cart)
        if hasattr(self.catalog, "openCart"):
            self.catalog.openCart.connect(self.show_cart)
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
        if saved.startswith(GEOMETRY_VERSION):
            try:
                if self.restoreGeometry(QByteArray.fromBase64(saved[len(GEOMETRY_VERSION):].encode("ascii"))):
                    return
            except Exception:
                pass
        self._default_geometry()

    def _default_geometry(self) -> None:
        """Most of the screen: big enough for the Catalog, maximized on small or high-scale screens."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 860)
            return
        area = screen.availableGeometry()
        width = int(min(area.width() * 0.92, max(1280, area.width() * 0.75)))
        height = int(min(area.height() * 0.92, max(860, area.height() * 0.85)))
        self.resize(width, height)
        self.move(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)
        self._start_maximized = width >= area.width() * 0.9 or height >= area.height() * 0.9

    def show_initial(self) -> None:
        if self._start_maximized:
            self.showMaximized()
        else:
            self.show()

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
        if hasattr(self.catalog, "release"):
            self.catalog.release()
        geometry = GEOMETRY_VERSION + bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.context.update_settings(lambda s: replace(s, ui=replace(s.ui, window_geometry=geometry)))
        super().closeEvent(event)
