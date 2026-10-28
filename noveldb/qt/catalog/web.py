"""Catalog page with QtWebEngine: the catalog site plus the shared bridge script.

The bridge (catalog-bridge.js, the same file the mobile apps inject) runs in an isolated script
world, so the site's own scripts cannot reach the app channel. It reports cart buttons and
outside links over a QWebChannel; the app answers with `NovelDBApp.setCart` / `setMode`.
Clicked links to other sites leave the page: on NovelDB they open in the system browser, on
other catalog sites they go to the link cart (when that setting is on).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.core import paths
from noveldb.core.catalog.cart import CartItem
from noveldb.core.catalog.urls import is_noveldb_site, valid_catalog_url
from noveldb.core.settings.model import DEFAULT_CATALOG_URL
from noveldb.core.sources import detect_source
from noveldb.qt.context import AppContext
from noveldb.qt.widgets import ButtonFeedback, Toast, hint

log = logging.getLogger(__name__)
WORLD = QWebEngineScript.ApplicationWorld
BRIDGE_JS = Path(__file__).with_name("catalog-bridge.js")
MESSAGE_TYPES = ("ready", "cartAdd", "cartRemove", "open")

# Queues bridge messages until the channel is connected, then hands them to the app.
SHIM_JS = """
(function () {
    var queue = [], bridge = null;
    window.NovelDBQt = {postMessage: function (text) { if (bridge) bridge.post(String(text)); else queue.push(String(text)); }};
    new QWebChannel(qt.webChannelTransport, function (channel) {
        bridge = channel.objects.bridge;
        queue.splice(0).forEach(function (text) { bridge.post(text); });
    });
})();
"""


def _qwebchannel_js() -> str:
    handle = QFile(":/qtwebchannel/qwebchannel.js")
    if not handle.open(QIODevice.ReadOnly):
        raise RuntimeError("qwebchannel.js is missing from QtWebChannel")
    try:
        return bytes(handle.readAll()).decode("utf-8")
    finally:
        handle.close()


def bridge_script() -> QWebEngineScript:
    """One script (channel library, shim, bridge) so they always run in this order."""
    script = QWebEngineScript()
    script.setName("noveldb-bridge")
    # DocumentReady: at DocumentCreation QtWebEngine has no <html> element yet for the bridge's styles.
    script.setInjectionPoint(QWebEngineScript.DocumentReady)
    script.setWorldId(WORLD)
    script.setRunsOnSubFrames(False)
    script.setSourceCode(_qwebchannel_js() + "\n" + SHIM_JS + "\n" + BRIDGE_JS.read_text(encoding="utf-8"))
    return script


def parse_message(text: str) -> Optional[dict]:
    """A bridge message, or None when it is malformed (the site shares the DOM, so check everything)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("type") not in MESSAGE_TYPES:
        return None
    if data["type"] != "ready":
        url = data.get("url")
        if not isinstance(url, str) or valid_catalog_url(url) is None or len(url) > 4000:
            return None
    for key in ("title", "source"):
        if key in data and not isinstance(data[key], str):
            data[key] = ""
    return data


class Bridge(QObject):
    message = Signal(dict)

    @Slot(str)
    def post(self, text: str) -> None:
        data = parse_message(text)
        if data is not None:
            self.message.emit(data)


class CatalogWebPage(QWebEnginePage):
    """Sends clicked links to other sites (and new windows) to `leave`."""

    leave = Signal(str, str)  # url, title

    def __init__(self, profile: QWebEngineProfile, parent: QObject) -> None:
        super().__init__(profile, parent)
        self.home_host = ""
        self._popups: list = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.ErrorMessageLevel and (source.startswith("userscript:") or not source):
            log.warning("catalog script error %s:%s %s", source, line, message)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if (is_main_frame and nav_type == QWebEnginePage.NavigationTypeLinkClicked
                and url.scheme() in ("http", "https") and url.host().lower() != self.home_host):
            self.leave.emit(url.toString(), "")
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, _window_type):
        # target=_blank and window.open: catch the address, then drop the throwaway page.
        popup = QWebEnginePage(self.profile(), self)
        self._popups.append(popup)

        def first_url(url, popup=popup):
            if not (url.isValid() and url.scheme() in ("http", "https")):
                return  # about:blank first, then the real address
            self.leave.emit(url.toString(), "")
            popup.urlChanged.disconnect()
            self._popups.remove(popup)
            QTimer.singleShot(0, popup.deleteLater)

        popup.urlChanged.connect(first_url)
        return popup


class CatalogView(QWebEngineView):
    addToCart = Signal(str, str)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        request = self.lastContextMenuRequest()
        link = request.linkUrl() if request is not None else QUrl()
        if link.isValid() and link.scheme() in ("http", "https"):
            action = QAction("\U0001F517 Add link to cart", menu)
            action.triggered.connect(lambda: self.addToCart.emit(link.toString(), request.linkText()))
            first = menu.actions()[0] if menu.actions() else None
            menu.insertAction(first, action)
            if first is not None:
                menu.insertSeparator(first)
        menu.popup(event.globalPos())


class CatalogPage(QWidget):
    openCart = Signal()
    downloadCart = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.profile: Optional[QWebEngineProfile] = None
        self.page: Optional[CatalogWebPage] = None
        self.view: Optional[CatalogView] = None
        self._loaded_url = ""

        def tool(text, tip, slot):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.clicked.connect(slot)
            return button

        self.back = tool("←", "Back", lambda: self.view and self.view.back())
        self.forward = tool("→", "Forward", lambda: self.view and self.view.forward())
        self.reload = tool("↻", "Reload", lambda: self.view and self.view.reload())
        self.home = tool("⌂", "Catalog home", self.go_home)
        self.address = QLabel("")
        self.address.setProperty("hint", True)
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.zoom_out = tool("−", "Zoom out (Ctrl+-)", lambda: self.zoom_by(-0.1))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setProperty("hint", True)
        self.zoom_in = tool("+", "Zoom in (Ctrl++)", lambda: self.zoom_by(0.1))
        self.external = tool("↗", "Open this page in your browser", self._open_external)
        for keys, step in (("Ctrl++", 0.1), ("Ctrl+=", 0.1), ("Ctrl+-", -0.1)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self.zoom_by(s))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.set_zoom(1.0))
        self.cart_button = QPushButton()
        self.cart_button.clicked.connect(self.openCart.emit)
        self.run = QPushButton("Download")
        self.run.setProperty("primary", True)
        self.run.setToolTip("Download every book in the link cart")
        self.run.clicked.connect(self.downloadCart.emit)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.setSpacing(2)
        for widget in (self.back, self.forward, self.reload, self.home):
            bar.addWidget(widget)
        bar.addWidget(self.address, 1)
        bar.addWidget(self.zoom_out)
        bar.addWidget(self.zoom_label)
        bar.addWidget(self.zoom_in)
        bar.addWidget(self.external)
        bar.addWidget(self.cart_button)
        bar.addWidget(self.run)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(3)
        self.progress.hide()

        # Error overlay (blank pages were the mobile apps' worst catalog bug; say what happened).
        self.error = QWidget()
        self.error_text = QLabel("")
        self.error_text.setWordWrap(True)
        self.error_text.setAlignment(Qt.AlignCenter)
        retry = QPushButton("Retry")
        retry.setProperty("primary", True)
        retry.clicked.connect(self._retry)
        use_default = QPushButton("Use NovelDB")
        use_default.clicked.connect(self._use_default)
        error_buttons = QHBoxLayout()
        error_buttons.addStretch(1)
        error_buttons.addWidget(retry)
        error_buttons.addWidget(use_default)
        error_buttons.addStretch(1)
        error_layout = QVBoxLayout(self.error)
        error_layout.addStretch(1)
        error_layout.addWidget(self.error_text)
        error_layout.addLayout(error_buttons)
        error_layout.addStretch(2)

        self.stack = QStackedLayout()
        self.placeholder = hint("Loading the catalog...")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.error)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.progress)
        layout.addLayout(self.stack, 1)

        self.toast = Toast(self)
        self.cart_feedback = ButtonFeedback(self.cart_button)
        context.cartChanged.connect(self._cart_changed)
        context.settingsChanged.connect(self._settings_changed)
        self._cart_changed()
        self._show_zoom()

    # -- lifecycle ---------------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if self.view is None:
            QTimer.singleShot(0, self._create_view)

    def _create_view(self) -> None:
        if self.view is not None:
            return
        storage = paths.data_dir() / "webengine" / "catalog"
        self.profile = QWebEngineProfile("noveldb-catalog", self)
        self.profile.setPersistentStoragePath(str(storage))
        self.profile.setCachePath(str(paths.cache_dir() / "webengine" / "catalog"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.AllowPersistentCookies)
        self.profile.scripts().insert(bridge_script())
        self.page = CatalogWebPage(self.profile, self)
        self.page.settings().setAttribute(QWebEngineSettings.FocusOnNavigationEnabled, False)
        self.channel = QWebChannel(self.page)
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel, WORLD)
        self.bridge.message.connect(self._message)
        self.page.leave.connect(self.leave)
        self.view = CatalogView(self)
        self.view.setPage(self.page)
        self.view.addToCart.connect(lambda url, title: self._add_to_cart(url, title, announce=True))
        self.view.loadStarted.connect(self._load_started)
        self.view.loadProgress.connect(self.progress.setValue)
        self.view.loadFinished.connect(self._load_finished)
        self.view.urlChanged.connect(self._url_changed)
        self.view.setZoomFactor(self.context.settings.catalog.zoom)
        self.stack.addWidget(self.view)
        self.stack.setCurrentWidget(self.view)
        self.go_home()

    def release(self) -> None:
        """Delete the page before its profile (Qt warns, and may crash, otherwise)."""
        if self.view is not None:
            self.view.setPage(None)
        if self.page is not None:
            self.page.deleteLater()
            self.page = None

    # -- zoom ------------------------------------------------------------------------------------

    def zoom_by(self, step: float) -> None:
        self.set_zoom(self.context.settings.catalog.zoom + step)

    def set_zoom(self, value: float) -> None:
        from dataclasses import replace

        value = round(min(3.0, max(0.5, value)), 2)
        self.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, zoom=value)))

    def _show_zoom(self) -> None:
        zoom = self.context.settings.catalog.zoom
        self.zoom_label.setText(f"{round(zoom * 100)}%")
        if self.view is not None and abs(self.view.zoomFactor() - zoom) > 0.001:
            self.view.setZoomFactor(zoom)

    # -- navigation ----------------------------------------------------------------------------

    @property
    def site(self) -> str:
        return valid_catalog_url(self.context.settings.catalog.url) or DEFAULT_CATALOG_URL

    def go_home(self) -> None:
        if self.view is None:
            return
        site = self.site
        self.page.home_host = (urlsplit(site).hostname or "").lower()
        self._loaded_url = site
        self.view.load(QUrl(site))

    def _retry(self) -> None:
        if self.view is None:
            return
        self.stack.setCurrentWidget(self.view)
        if self.view.url().isEmpty():
            self.go_home()
        else:
            self.view.reload()

    def _use_default(self) -> None:
        from dataclasses import replace

        self.context.update_settings(lambda s: replace(s, catalog=replace(s.catalog, url=DEFAULT_CATALOG_URL)))

    def _open_external(self) -> None:
        url = self.view.url() if self.view is not None else QUrl(self.site)
        QDesktopServices.openUrl(url if url.isValid() and not url.isEmpty() else QUrl(self.site))

    def _load_started(self) -> None:
        self.progress.setValue(0)
        self.progress.show()

    def _load_finished(self, ok: bool) -> None:
        self.progress.hide()
        self.back.setEnabled(self.view.history().canGoBack())
        self.forward.setEnabled(self.view.history().canGoForward())
        if ok:
            self.stack.setCurrentWidget(self.view)
        else:
            self.error_text.setText(f"The catalog did not load.\n{self.view.url().toString() or self.site}\n\n"
                                    "Check your connection or the catalog address in Settings.")
            self.stack.setCurrentWidget(self.error)

    def _url_changed(self, url: QUrl) -> None:
        self.address.setText(url.toString())
        if self.view is not None:
            self.back.setEnabled(self.view.history().canGoBack())
            self.forward.setEnabled(self.view.history().canGoForward())

    # -- app <-> page --------------------------------------------------------------------------

    def _cart_links(self) -> bool:
        return self.context.settings.catalog.cart_links and not is_noveldb_site(self.site)

    def _push_state(self) -> None:
        if self.page is None:
            return
        urls = json.dumps(self.context.cart.urls)
        mode = json.dumps({"cartLinks": self._cart_links(), "fill": self.context.settings.catalog.fill_width})
        self.page.runJavaScript(
            f"window.NovelDBApp && (NovelDBApp.setCart({urls}), NovelDBApp.setMode({mode}));", WORLD)

    def _message(self, data: dict) -> None:
        kind = data["type"]
        if kind == "ready":
            self._push_state()
        elif kind == "cartAdd":
            self._add_to_cart(data["url"], data.get("title", ""), data.get("source", ""), announce=True)
        elif kind == "cartRemove":
            self.context.cart.remove(data["url"])
            self.toast.show_message("Removed from the link cart: " + (data.get("title") or data["url"]))
        elif kind == "open":
            self.leave(data["url"], data.get("title", ""))

    def leave(self, url: str, title: str = "") -> None:
        """A link out of the catalog: the link cart on other sites (setting on), else the browser."""
        if self._cart_links():
            self._add_to_cart(url, title, announce=True)
        else:
            QDesktopServices.openUrl(QUrl(url))

    def _add_to_cart(self, url: str, title: str = "", source: str = "", announce: bool = False) -> None:
        added = self.context.cart.add(CartItem(url, title, source or detect_source(url) or ""))
        if announce:
            text = ("Added to the link cart: " if added else "Already in the link cart: ") + (title or url)
            self.context.hub.message.emit(text)
            self.toast.show_message("\U0001F517 " + text)
            if added:
                self.cart_feedback.label = self.cart_button.text()
                self.cart_feedback.done(True, f"Added ({len(self.context.cart)})")

    def _cart_changed(self) -> None:
        count = len(self.context.cart)
        if hasattr(self, "cart_feedback"):
            self.cart_feedback.label = f"\U0001F517 Cart ({count})"
        if not (hasattr(self, "cart_feedback") and self.cart_feedback._restore.isActive()):
            self.cart_button.setText(f"\U0001F517 Cart ({count})")
        self.run.setText(f"Download {count}" if count > 1 else "Download")
        self.run.setEnabled(count > 0)
        self._push_state()

    def _settings_changed(self) -> None:
        self._show_zoom()
        if self.view is not None and self.site != self._loaded_url:
            self.go_home()
        else:
            self._push_state()
