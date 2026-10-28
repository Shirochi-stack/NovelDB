"""The interface grows with the window.

The full layout (sidebar, the Download page's two columns and the log) needs about 1700 x 960. When
the window is bigger than that (maximised on a large screen),
text, the stylesheet's padding and the few fixed widths grow together by the same factor, so the page
looks like the windowed one instead of a small form beside a huge log. Settings › General › Display
scaling turns it off. This multiplies whatever display scaling Qt already applies.
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QWidget

BASE_SIZE = (1700, 960)  # the window size the full layout needs at its natural size
MAX_FACTOR = 1.8
STEP = 0.05


def factor_for(width: int, height: int, enabled: bool = True) -> float:
    """The scale for a window of this size: 1 up to the base size, then growing with the tighter side."""
    if not enabled or width <= 0 or height <= 0:
        return 1.0
    factor = min(width / BASE_SIZE[0], height / BASE_SIZE[1])
    factor = max(1.0, min(MAX_FACTOR, factor))
    return round(round(factor / STEP) * STEP, 2)


_PX = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)px")


def scale_stylesheet(qss: str, factor: float) -> str:
    """Multiply the stylesheet's pixel sizes; hairlines (1px) stay hairlines."""
    if abs(factor - 1.0) < 1e-6:
        return qss

    def repl(match):
        value = float(match.group(1))
        if value <= 1:
            return match.group(0)
        return f"{max(1, round(value * factor))}px"
    return _PX.sub(repl, qss)


class UiScale(QObject):
    """The current factor, and a signal for the widgets that keep pixel sizes."""

    changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.factor = 1.0
        self._base_point_size: Optional[float] = None

    def px(self, value: float) -> int:
        return int(round(value * self.factor))

    def set_factor(self, factor: float) -> bool:
        """Apply a new factor; True when it changed."""
        if abs(factor - self.factor) < 1e-6:
            return False
        app = QApplication.instance()
        if app is not None:
            font = app.font()
            if self._base_point_size is None:
                self._base_point_size = font.pointSizeF()
            font.setPointSizeF(self._base_point_size * factor)
            app.setFont(font)
        self.factor = factor
        self.changed.emit(factor)
        return True


ui_scale = UiScale()


class _Binding(QObject):
    """Re-applies a widget's pixel sizes when the factor changes; goes away with the widget."""

    def __init__(self, widget: QWidget, apply, now: bool = True) -> None:
        super().__init__(widget)
        self._apply = apply
        ui_scale.changed.connect(self.refresh)
        if now:
            self.refresh(ui_scale.factor)

    def refresh(self, _factor: float = 1.0) -> None:
        self._apply()


def bind_size(widget: QWidget, *, min_width: int = 0, max_width: int = 0, fixed_width: int = 0,
              max_height: int = 0, fixed_height: int = 0) -> QWidget:
    """Give `widget` these sizes at the base window size, scaled with the window from then on."""
    px = ui_scale.px

    def apply():
        if fixed_width:
            widget.setFixedWidth(px(fixed_width))
        if min_width:
            widget.setMinimumWidth(px(min_width))
        if max_width:
            widget.setMaximumWidth(px(max_width))
        if fixed_height:
            widget.setFixedHeight(px(fixed_height))
        if max_height:
            widget.setMaximumHeight(px(max_height))
    _Binding(widget, apply)
    return widget


def on_scale(widget: QWidget, refresh, now: bool = True) -> None:
    """Call `refresh()` (now, unless `now` is False) and whenever the factor changes, while `widget` lives."""
    _Binding(widget, refresh, now)


def bind_title(label: QLabel, size: int, weight: int = 600) -> QLabel:
    """A heading whose pixel font size scales with the window."""
    _Binding(label, lambda: label.setStyleSheet(f"font-size: {ui_scale.px(size)}px; font-weight: {weight};"))
    return label


def bind_rows(view: QListWidget, height: int) -> QListWidget:
    """List rows with an explicit height (the sidebar lists), scaled with the window."""
    from PySide6.QtCore import QSize

    def apply():
        # List views get their own platform font on Windows (smaller than the app's); use the app's.
        view.setFont(QApplication.font())
        for row in range(view.count()):
            view.item(row).setSizeHint(QSize(0, ui_scale.px(height)))
    _Binding(view, apply)
    return view
