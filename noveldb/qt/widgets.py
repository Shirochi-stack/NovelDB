"""Small widgets shared by the pages: wrapping flow layout, toggle chips, labelled field groups."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (
    QCoreApplication,
    QMargins,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QComboBox,
    QDoubleSpinBox,
    QGraphicsOpacityEffect,
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
        # Items wait for their row's height so each is centred in it: a taller neighbour (a menu
        # button, say) then leaves no extra gap below the shorter ones.
        line = []

        def place() -> None:
            if not test:
                for item, left, hint in line:
                    item.setGeometry(QRect(QPoint(left, y + (line_height - hint.height()) // 2), hint))
            line.clear()

        for item in self._items:
            if item.widget() is not None and not item.widget().isVisibleTo(item.widget().parentWidget()):
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > area.right() + 1 and line_height > 0:
                place()
                x = area.x()
                y += line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            line.append((item, x, hint))
            x = next_x
            line_height = max(line_height, hint.height())
        place()
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
    box = SpinBox()
    box.setRange(low, high)
    if special:
        box.set_blank_placeholder(special)
    box.setValue(value)
    box.setKeyboardTracking(False)
    box.valueChanged.connect(on_change)
    return box


def dspin(value: float, low: float, high: float, on_change: Callable[[float], None], step: float = 0.5) -> QDoubleSpinBox:
    box = DoubleSpinBox()
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


def scroll_page_instead(widget: QWidget, event) -> None:
    """Hands a wheel event to the scroll area around `widget`: scrolling a form must never change a
    value just because the pointer passed over a spin box or a drop-down.

    The controls below do this themselves. An application-wide event filter did it before, but it
    sent every event of every object through Python, and on Linux PySide crashed wrapping Qt
    WebEngine's internal objects for it when the Catalog opened."""
    area = widget.parentWidget()
    while area is not None and not isinstance(area, QAbstractScrollArea):
        area = area.parentWidget()
    if area is not None:
        QCoreApplication.sendEvent(area.verticalScrollBar(), event)
    event.accept()


class SpinBox(QSpinBox):
    """A spin box the mouse wheel never changes (see `scroll_page_instead`)."""

    _blank_min = False

    def wheelEvent(self, event):
        scroll_page_instead(self, event)

    def set_blank_placeholder(self, text):
        """Leave the field blank at its minimum and show `text` as a greyed placeholder, instead of a
        special value the user has to delete before typing (what setSpecialValueText does)."""
        self._blank_min = True
        self.lineEdit().setPlaceholderText(text)

    def textFromValue(self, value):
        if self._blank_min and value == self.minimum():
            return ""
        return super().textFromValue(value)

    def validate(self, text, pos):
        # Accept an empty field so the user can clear it; without this the box reverts to the last value.
        if self._blank_min and not text.strip():
            from PySide6.QtGui import QValidator
            return (QValidator.Acceptable, text, pos)
        return super().validate(text, pos)

    def valueFromText(self, text):
        # An empty field means the minimum (the blank/placeholder state), not the previous value.
        if self._blank_min and not text.strip():
            return self.minimum()
        return super().valueFromText(text)


class DoubleSpinBox(QDoubleSpinBox):
    """A decimal spin box the mouse wheel never changes (see `scroll_page_instead`)."""

    def wheelEvent(self, event):
        scroll_page_instead(self, event)


class ComboBox(QComboBox):
    """A drop-down the mouse wheel never changes (see `scroll_page_instead`)."""

    def wheelEvent(self, event):
        scroll_page_instead(self, event)


class ButtonFeedback(QObject):
    """Animates a button through a save: "Saving" with moving dots, then a short check or cross."""

    def __init__(self, button: QPushButton) -> None:
        super().__init__(button)
        self.button = button
        self.label = button.text()
        self._busy_text = ""
        self._dots = 0
        self._spin = QTimer(self)
        self._spin.setInterval(350)
        self._spin.timeout.connect(self._tick)
        self._restore = QTimer(self)
        self._restore.setSingleShot(True)
        self._restore.setInterval(1800)
        self._restore.timeout.connect(self._reset)

    def busy(self, text: str = "Saving") -> None:
        self._restore.stop()
        self._busy_text = text
        self._dots = 0
        self.button.setEnabled(False)
        self._tick()
        self._spin.start()

    def done(self, ok: bool = True, text: str = "") -> None:
        self._spin.stop()
        self.button.setEnabled(True)
        self.button.setText(("✓ " if ok else "✗ ") + (text or ("Saved" if ok else "Not saved")))
        self.button.setProperty("feedback", "ok" if ok else "error")
        self._repolish()
        self._restore.start()

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.button.setText(self._busy_text + "." * self._dots + " " * (3 - self._dots))

    def _reset(self) -> None:
        self.button.setText(self.label)
        self.button.setProperty("feedback", "")
        self._repolish()

    def _repolish(self) -> None:
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)


class Toast(QLabel):
    """A short message floating over the bottom of its parent, fading out by itself."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(450)
        self._fade.finished.connect(self._faded)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)
        self.hide()

    def show_message(self, text: str, seconds: float = 2.2) -> None:
        self.setText(text)
        parent = self.parentWidget()
        width = min(max(320, parent.width() // 3), parent.width() - 40)
        self.setFixedWidth(width)
        self.adjustSize()
        self.move((parent.width() - width) // 2, parent.height() - self.height() - 32)
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.show()
        self.raise_()
        self._hold.start(int(seconds * 1000))

    def _fade_out(self) -> None:
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _faded(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.hide()
