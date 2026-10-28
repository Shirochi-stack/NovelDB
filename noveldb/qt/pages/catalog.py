"""Catalog page. Builds without QtWebEngine (TurboLite) open the site in the system browser."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from noveldb.qt.context import AppContext
from noveldb.qt.widgets import hint


class BrowserCatalogPage(QWidget):
    """Fallback: open the catalog in the system browser; links dropped on the window go to the cart."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        title = QLabel("Catalog")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.site = hint("")
        open_site = QPushButton("Open catalog in browser")
        open_site.setProperty("primary", True)
        open_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.context.settings.catalog.url)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(title)
        layout.addWidget(self.site)
        layout.addWidget(hint(
            "This build has no built-in web view. Browse the catalog in your browser and drag book "
            "links onto the Download box, or paste them there."))
        layout.addWidget(open_site)
        layout.addStretch(1)
        context.settingsChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        self.site.setText(self.context.settings.catalog.url)


def catalog_page(context: AppContext) -> QWidget:
    """The embedded catalog when QtWebEngine is available (Full, Lite), else the browser fallback."""
    from noveldb import features

    if features.has_webengine():
        try:
            from noveldb.qt.catalog.web import CatalogPage

            return CatalogPage(context)
        except ImportError:
            pass
    return BrowserCatalogPage(context)
