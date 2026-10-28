"""Small widgets shared by the pages: wrapping flow layout, toggle chips, labelled field groups."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QWidget,
)


class FlowLayout(QLayout):
    """Lays children out left to right and wraps onto new rows (Qt's flow layout example)."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 8, row_spacing: int = 8) -> None:
        super().__init__(parent)
        self._items = []
        self._h = spacing
        self._v = row_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins: QMargins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisibleTo(item.widget().parentWidget()):
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


def flow(*widgets: QWidget, spacing: int = 8) -> QWidget:
    holder = QWidget()
    layout = FlowLayout(holder, spacing=spacing)
    for widget in widgets:
        layout.addWidget(widget)
    holder.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    return holder


class Chip(QPushButton):
    """A checkable filter chip; `on_change(checked)` fires on user clicks only."""

    def __init__(self, text: str, checked: bool = False, on_change: Optional[Callable[[bool], None]] = None) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setProperty("chip", True)
        self.setCursor(Qt.PointingHandCursor)
        if on_change is not None:
            self.clicked.connect(lambda value: on_change(bool(value)))

    def set_quietly(self, checked: bool) -> None:
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)


def section(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("section", True)
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("hint", True)
    label.setWordWrap(True)
    return label


def field_group(label: str, *widgets: QWidget) -> QWidget:
    """A label kept right next to its control(s)."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(QLabel(label))
    for widget in widgets:
        row.addWidget(widget)
    return holder


def spin(value: int, low: int, high: int, on_change: Callable[[int], None], special: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    if special:
        box.setSpecialValueText(special)
    box.setValue(value)
    box.setKeyboardTracking(False)
    box.valueChanged.connect(on_change)
    return box


def dspin(value: float, low: float, high: float, on_change: Callable[[float], None], step: float = 0.5) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(1)
    box.setSingleStep(step)
    box.setValue(value)
    box.setKeyboardTracking(False)
    box.valueChanged.connect(on_change)
    return box


def set_quietly(widget, value) -> None:
    widget.blockSignals(True)
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.setValue(value)
    elif hasattr(widget, "setChecked"):
        widget.setChecked(bool(value))
    widget.blockSignals(False)
