"""The link cart: books picked on the Catalog page, waiting to be downloaded."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from noveldb.qt.context import AppContext


class CartDialog(QDialog):
    """Pick links to move into the Download box (or remove them)."""

    useLinks = Signal(list)
    downloadLinks = Signal(list)

    def __init__(self, context: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("\U0001F6D2 Link cart")
        self.resize(560, 420)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.itemChanged.connect(self._update_buttons)
        self.empty = QLabel("The cart is empty. Use the \U0001F6D2 button on Catalog cards to add books.")
        self.empty.setWordWrap(True)
        self.empty.setProperty("hint", True)
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._check_all(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._check_all(False))
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        self.use = QPushButton("Add to Download")
        self.use.clicked.connect(self._use)
        self.download = QPushButton("Download now")
        self.download.setProperty("primary", True)
        self.download.clicked.connect(self._download)
        row = QHBoxLayout()
        row.addWidget(select_all)
        row.addWidget(select_none)
        row.addStretch(1)
        row.addWidget(self.remove)
        row.addWidget(self.use)
        row.addWidget(self.download)
        layout = QVBoxLayout(self)
        layout.addWidget(self.empty)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        context.cartChanged.connect(self._reload)
        self._reload()

    def _reload(self) -> None:
        checked = {item.data(Qt.UserRole) for item in self._items() if item.checkState() == Qt.Checked}
        first = self.list.count() == 0
        self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for entry in self.context.cart.items:
            text = f"{entry.title}  ·  {entry.url}" if entry.title else entry.url
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry.url)
            item.setToolTip(entry.url)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if first or entry.url in checked else Qt.Unchecked)
            self.list.addItem(item)
        self.list.setUpdatesEnabled(True)
        self.list.blockSignals(False)
        self.empty.setVisible(self.list.count() == 0)
        self._update_buttons()

    def _items(self):
        return [self.list.item(i) for i in range(self.list.count())]

    def _checked(self) -> list[str]:
        return [item.data(Qt.UserRole) for item in self._items() if item.checkState() == Qt.Checked]

    def _check_all(self, on: bool) -> None:
        for item in self._items():
            item.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _update_buttons(self, *_args) -> None:
        count = len(self._checked())
        self.remove.setEnabled(count > 0)
        self.use.setEnabled(count > 0)
        self.download.setEnabled(count > 0)
        self.download.setText(f"Download {count} now" if count > 1 else "Download now")

    def _remove(self) -> None:
        self.context.cart.remove_all(self._checked())

    def _download(self) -> None:
        links = self._checked()
        if links:
            self.downloadLinks.emit(links)
            self.accept()

    def _use(self) -> None:
        links = self._checked()
        if links:
            self.useLinks.emit(links)
            self.accept()
