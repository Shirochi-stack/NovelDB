"""The app's artwork icons (`assets/`): Halgakos in the link cart, and Halgakos with a download arrow.

The cart marks the link cart itself (its buttons, dialog and messages); the download arrow is what
the Catalog cards' buttons and Download labels show (the catalog bridge carries its own copy).
"""

from __future__ import annotations

from functools import lru_cache
from html import escape

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractButton

from noveldb.core import paths
from noveldb.qt.scale import on_scale, ui_scale

CART = "cart-icon.png"
DOWNLOAD = "download-icon.png"


def path(name: str) -> str:
    return (paths.bundle_dir() / "assets" / name).as_posix()


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    return QIcon(path(name))


def set_icon(button: QAbstractButton, name: str, size: int = 22) -> None:
    """`name` on `button`, `size` px at the base window size, growing with the window."""
    button.setIcon(icon(name))
    on_scale(button, lambda: button.setIconSize(QSize(ui_scale.px(size), ui_scale.px(size))))


def inline(name: str, size: int = 20) -> str:
    """The artwork as rich text, for labels and messages."""
    px = ui_scale.px(size)
    return f'<img src="{escape(path(name))}" width="{px}" height="{px}" style="vertical-align: middle">'
