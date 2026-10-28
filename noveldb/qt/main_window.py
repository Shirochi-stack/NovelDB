"""The main window: sidebar pages (Download, Catalog, Queue, Settings) over a log panel."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QByteArray, QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
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
from noveldb.qt.scale import bind_rows, bind_size, factor_for, on_scale, ui_scale
from noveldb.qt.theme import apply_theme

PAGES = ("Download", "Catalog", "Queue", "Settings")
# Saved geometry is prefixed with a version: sizes saved before display scaling followed the OS
# setting were a third too small, so they are not restored.
GEOMETRY_VERSION = "2:"
# A resize bigger than this share of a side in one step is a jump (snapping, a program), not a drag.
JUMP = 0.15
# A normal (not maximised) window this close to the screen's size fills it: restoring to it would
# look like nothing happened, so it gets the default window size instead.
FILLS_SCREEN = 0.95


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        context.prompts._parent = lambda: self
        suffix = {"lite": " Lite", "turbolite": " TurboLite"}.get(features.variant(), "")
        self.setWindowTitle(f"NovelDB {VERSION}{suffix}")
        self.setMinimumSize(900, 620)
        self._start_maximized = False
        self._maximized_before_full_screen = False

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        bind_size(self.sidebar, fixed_width=176)
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        for name in PAGES:
            item = QListWidgetItem(name)
            # Explicit height: QListView lays rows out from the unstyled size, so the stylesheet's
            # padding made rows overlap.
            item.setSizeHint(QSize(0, 44))
            self.sidebar.addItem(item)
        bind_rows(self.sidebar, 44)

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
        self.splitter.setHandleWidth(12)  # a bar that is easy to grab (themed with a grip)
        self.splitter.splitterMoved.connect(self._log_resized)
        self.sidebar.currentRowChanged.connect(self._apply_log_share)

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
        self.catalog.openCart.connect(self.show_cart)
        self.catalog.downloadCart.connect(self.download_cart)
        # The log stays folded away until a download writes to it (or the user opens it).
        self.log_toggle = QPushButton("Show log")
        self.log_toggle.setFlat(True)
        self.log_toggle.clicked.connect(lambda: self.set_log_visible(not self._log_open))
        self.statusBar().addPermanentWidget(self.log_toggle)
        self.log_panel.activity.connect(lambda: self.set_log_visible(True))
        self.set_log_visible(False)
        self.sidebar.currentRowChanged.connect(self._place_log)
        self._place_log()
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
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self.toggle_full_screen)
        self.statusBar().setSizeGripEnabled(True)
        # The interface grows with the window (noveldb/qt/scale.py): at once when the window is
        # maximised, restored or made full screen, otherwise once dragging an edge settles.
        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(150)
        self._scale_timer.timeout.connect(self._rescale)
        self._scaled_state = self._size_state()
        on_scale(self, lambda: self._apply_theme(force=True), now=False)
        self._restore_geometry()

    # -- navigation ----------------------------------------------------------------------

    def show_page(self, index: int) -> None:
        self.sidebar.setCurrentRow(index)

    def show_cart(self) -> None:
        dialog = CartDialog(self.context, self)
        dialog.useLinks.connect(self.download.add_lines)
        dialog.downloadLinks.connect(self.download.queue_lines)
        dialog.exec()

    def download_cart(self) -> None:
        self.download.queue_lines(self.context.cart.urls)

    def _place_log(self, *_args) -> None:
        """The Download page shows the log as its right-hand column; other pages fold it at the bottom."""
        docked = self._page_name() == "Download"
        if docked:
            if self.log_panel.parentWidget() is not self.download.log_dock:
                self.download.log_dock.layout().addWidget(self.log_panel)
            self.log_panel.show()
            self.log_toggle.hide()
            return
        if self.log_panel.parentWidget() is not self.splitter:
            self.splitter.addWidget(self.log_panel)
            self.splitter.setStretchFactor(1, 1)
        self.log_panel.setVisible(bool(getattr(self, "_log_open", False)))
        self.log_toggle.show()
        self._apply_log_share()

    def set_log_visible(self, visible: bool) -> None:
        """The bottom log on pages other than Download (where the log is always a column)."""
        # isVisible() is False for every widget before the window is shown, so track it here.
        if visible == getattr(self, "_log_open", None):
            return
        self._log_open = visible
        self.log_toggle.setText("Hide log" if visible else "Show log")
        if self._page_name() != "Download":
            self.log_panel.setVisible(visible)
            if visible:
                self._apply_log_share()

    # Share of the height the bottom log takes (the Catalog wants room). A size the user drags to
    # is kept per page. The Download page shows the log as a column instead.
    LOG_SHARE: dict = {}
    DEFAULT_LOG_SHARE = 0.28

    def _page_name(self) -> str:
        row = self.sidebar.currentRow()
        return PAGES[row] if 0 <= row < len(PAGES) else ""

    def _apply_log_share(self, *_args) -> None:
        if not getattr(self, "_log_open", False) or self._page_name() == "Download":
            return
        shares = getattr(self, "_log_shares", {})
        share = shares.get(self._page_name(), self.LOG_SHARE.get(self._page_name(), self.DEFAULT_LOG_SHARE))
        total = max(self.splitter.height(), 400)
        self._resizing = True
        self.splitter.setSizes([int(total * (1 - share)), int(total * share)])
        self._resizing = False

    def _log_resized(self, *_args) -> None:
        if getattr(self, "_resizing", False) or not getattr(self, "_log_open", False):
            return
        top, bottom = self.splitter.sizes()
        if top + bottom > 0:
            self._log_shares = {**getattr(self, "_log_shares", {}), self._page_name(): bottom / (top + bottom)}

    def _queued(self, count: int) -> None:
        if count:
            self.set_log_visible(True)
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
        self.maybe_shut_down(done, failed)

    def maybe_shut_down(self, done: int, failed: int, shut_down=None) -> None:
        """"Shut down computer after successful download": every job in the run finished."""
        from noveldb.core import platform_services
        from noveldb.qt.shutdown import ShutdownCountdown

        if not (self.context.settings.output.shutdown_after_success and done > 0 and failed == 0):
            return
        if shut_down is None and not platform_services.shutdown_supported():
            return
        self.shutdown_dialog = ShutdownCountdown(self, shut_down=shut_down, before=self.context.save_now)
        self.shutdown_dialog.show()

    # -- appearance ------------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if not event.spontaneous():
            self._rescale()  # the first paint already has the window's scale

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Maximising, restoring and snapping jump straight to the new size. Waiting for the timer
        # there painted the old scale at the new size first (a small form in a big window, or a
        # clipped one). Dragging an edge moves a little at a time and waits until it settles.
        old, new = event.oldSize(), event.size()
        jumped = not old.isValid() or abs(new.width() - old.width()) > old.width() * JUMP \
            or abs(new.height() - old.height()) > old.height() * JUMP
        if jumped or self._size_state() != self._scaled_state:
            self._rescale()
        else:
            self._scale_timer.start()

    def _size_state(self) -> Qt.WindowState:
        return self.windowState() & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen)

    def _rescale(self) -> None:
        self._scale_timer.stop()
        self._scaled_state = self._size_state()
        factor = factor_for(self.width(), self.height(), self.context.settings.ui.scale_with_window)
        if ui_scale.set_factor(factor):
            # Windows paints right after a resize, before queued layout passes run: lay out with
            # the new sizes now, so the first paint is the final layout.
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)

    def _settings_changed(self) -> None:
        self._rescale()
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
                    self._tidy_restored_geometry()
                    return
            except Exception:
                pass
        self._default_geometry()

    def _tidy_restored_geometry(self) -> None:
        """Start maximised rather than full screen, and never with a screen-sized window to restore to."""
        state = self.windowState()
        if state & Qt.WindowState.WindowFullScreen:
            self.setWindowState(Qt.WindowState.WindowMaximized)
        if self._fills_screen(self.normalGeometry()):
            self.setWindowState(Qt.WindowState.WindowNoState)
            self.setGeometry(self._normal_rect())
            self._start_maximized = True

    def _normal_rect(self) -> QRect:
        """The default window: most of the screen, centred, clearly smaller than maximised."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, 1280, 860)
        area = screen.availableGeometry()
        width = int(min(area.width() * 0.92, max(1280, area.width() * 0.75)))
        height = int(min(area.height() * 0.92, max(860, area.height() * 0.85)))
        return QRect(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2, width, height)

    def _fills_screen(self, rect: QRect) -> bool:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None or not rect.isValid():
            return False
        area = screen.availableGeometry()
        return rect.width() >= area.width() * FILLS_SCREEN and rect.height() >= area.height() * FILLS_SCREEN

    def _default_geometry(self) -> None:
        """Most of the screen: big enough for the Catalog, maximized on small or high-scale screens."""
        self.setGeometry(self._normal_rect())
        self._start_maximized = True  # first start: use the whole screen; later starts restore the window

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        big = Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen
        if event.oldState() & big and not self.windowState() & (big | Qt.WindowState.WindowMinimized):
            # Restored to a window as big as the screen (saved that way, or dragged out to it): the
            # restore button would seem to do nothing, so use the default window size.
            QTimer.singleShot(0, self, self._shrink_if_it_fills_the_screen)

    def _shrink_if_it_fills_the_screen(self) -> None:
        if not self.windowState() & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen) \
                and self._fills_screen(self.geometry()):
            self.setGeometry(self._normal_rect())

    def toggle_full_screen(self) -> None:
        """F11: full screen, and back to maximised or the window it was."""
        if self.isFullScreen():
            if self._maximized_before_full_screen:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._maximized_before_full_screen = self.isMaximized()
            self.showFullScreen()

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
