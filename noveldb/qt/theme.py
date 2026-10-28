"""Appearance: Fusion style with Material colours (System / Light / Dark / AMOLED, three accents)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from noveldb.qt.theme_palettes import SCHEMES


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # pragma: no cover - Qt < 6.5
        return QGuiApplication.palette().window().color().lightness() < 128


def scheme_for(theme: str, accent: str) -> dict:
    dark = theme in ("dark", "amoled") or (theme == "system" and system_is_dark())
    scheme = dict(SCHEMES.get((accent, "dark" if dark else "light")) or SCHEMES[("halgakos_rose", "dark" if dark else "light")])
    if theme == "amoled":
        scheme.update(background="#000000", surface="#000000", surfaceContainerLowest="#000000",
                      surfaceContainerLow="#0A0A0A", surfaceContainer="#0A0A0A")
    # Material baseline error containers (the ported palettes only carry `error`).
    scheme.setdefault("errorContainer", "#93000A" if dark else "#FFDAD6")
    scheme.setdefault("onErrorContainer", "#FFDAD6" if dark else "#410002")
    scheme["_dark"] = dark
    return scheme


def palette_for(scheme: dict) -> QPalette:
    c = {key: QColor(value) for key, value in scheme.items() if isinstance(value, str)}
    p = QPalette()
    p.setColor(QPalette.Window, c["surface"])
    p.setColor(QPalette.WindowText, c["onSurface"])
    p.setColor(QPalette.Base, c["surfaceContainerLowest"] if not scheme["_dark"] else c["surfaceContainerLow"])
    p.setColor(QPalette.AlternateBase, c["surfaceContainer"])
    p.setColor(QPalette.ToolTipBase, c["inverseSurface"])
    p.setColor(QPalette.ToolTipText, c["inverseOnSurface"])
    p.setColor(QPalette.Text, c["onSurface"])
    p.setColor(QPalette.PlaceholderText, c["onSurfaceVariant"])
    p.setColor(QPalette.Button, c["surfaceContainerHigh"])
    p.setColor(QPalette.ButtonText, c["onSurface"])
    p.setColor(QPalette.BrightText, c["error"])
    p.setColor(QPalette.Highlight, c["primary"])
    p.setColor(QPalette.HighlightedText, c["onPrimary"])
    p.setColor(QPalette.Link, c["primary"])
    p.setColor(QPalette.LinkVisited, c["tertiary"])
    p.setColor(QPalette.Mid, c["outlineVariant"])
    p.setColor(QPalette.Dark, c["outline"])
    disabled = QColor(c["onSurface"])
    disabled.setAlphaF(0.38)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


_ICONS = {
    "check": "<path d='M3.5 8.5l3 3 6-7' fill='none' stroke='{c}' stroke-width='2' stroke-linecap='round' "
             "stroke-linejoin='round'/>",
    "down": "<path d='M4 6l4 4 4-4' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round' "
            "stroke-linejoin='round'/>",
    "up": "<path d='M4 10l4-4 4 4' fill='none' stroke='{c}' stroke-width='1.8' stroke-linecap='round' "
          "stroke-linejoin='round'/>",
    "grip": "<rect x='3' y='6' width='10' height='1.4' rx='.7' fill='{c}'/><rect x='3' y='8.6' width='10' "
            "height='1.4' rx='.7' fill='{c}'/>",
}


def _icon_file(name: str, color: str) -> str:
    """A small SVG in `color`, written once to the cache (QSS images need a file); "" if it can't be."""
    try:
        from noveldb.core import paths

        folder = paths.cache_dir() / "theme"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{name}-{color.lstrip('#').lower()}.svg"
        if not target.exists():
            target.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
                              + _ICONS[name].format(c=color) + "</svg>", encoding="utf-8")
        return target.as_posix()
    except OSError:
        return ""


def _image(name: str, color: str) -> str:
    path = _icon_file(name, color)
    return f"image: url({path});" if path else ""


def stylesheet_for(scheme: dict) -> str:
    s = scheme
    check_rule = _image("check", s["onPrimary"])
    down, up = _image("down", s["onSurfaceVariant"]), _image("up", s["onSurfaceVariant"])
    down_off = _image("down", s["outlineVariant"])
    grip = _image("grip", s["onSurfaceVariant"])
    field_bg = s["surfaceContainerLowest"]
    return f"""
    /* Text fields, spin boxes and drop-downs: outlined, rose focus ring (Material 3 outlined fields). */
    QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
        background: {field_bg}; color: {s['onSurface']}; border: 1px solid {s['outline']}; border-radius: 8px;
        padding: 5px 9px; selection-background-color: {s['primary']}; selection-color: {s['onPrimary']}; }}
    QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{
        border-color: {s['onSurfaceVariant']}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{
        border: 2px solid {s['primary']}; padding: 4px 8px; }}
    QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{
        color: {s['outline']}; border-color: {s['outlineVariant']}; background: transparent; }}
    QComboBox {{ padding-right: 28px; }}
    QComboBox:focus {{ padding-right: 27px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }}
    QComboBox::down-arrow {{ {down} width: 14px; height: 14px; }}
    QComboBox::down-arrow:disabled {{ {down_off} }}
    QComboBox QAbstractItemView {{ background: {s['surfaceContainer']}; color: {s['onSurface']};
        border: 1px solid {s['outlineVariant']}; border-radius: 8px; padding: 4px; outline: 0;
        selection-background-color: {s['secondaryContainer']}; selection-color: {s['onSecondaryContainer']}; }}
    QAbstractSpinBox {{ padding-right: 24px; }}
    QAbstractSpinBox:focus {{ padding-right: 23px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 22px;
        border: none; background: transparent; }}
    QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin: 3px 3px 0 0; }}
    QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 3px 3px 0; }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
        background: {s['surfaceContainerHigh']}; border-radius: 4px; }}
    QAbstractSpinBox::up-arrow {{ {up} width: 12px; height: 12px; }}
    QAbstractSpinBox::down-arrow {{ {down} width: 12px; height: 12px; }}
    QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: none; }}
    QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}
    /* Splitter handles: a visible grip, highlighted while hovered or dragged. */
    QSplitter::handle:vertical {{ background: {s['surfaceContainerLow']}; {grip} }}
    QSplitter::handle:vertical:hover, QSplitter::handle:vertical:pressed {{ background: {s['secondaryContainer']}; }}
    /* Plain buttons: tonal pills; chips, primary and feedback buttons below override them. */
    QPushButton {{ background: {s['surfaceContainerHigh']}; color: {s['onSurface']};
        border: 1px solid {s['outlineVariant']}; border-radius: 16px; padding: 6px 16px; }}
    QPushButton:hover {{ background: {s['surfaceContainerHighest']}; border-color: {s['outline']}; }}
    QPushButton:pressed {{ background: {s['secondaryContainer']}; color: {s['onSecondaryContainer']}; }}
    QPushButton:disabled {{ color: {s['outline']}; background: transparent; border-color: {s['outlineVariant']}; }}
    QPushButton:flat {{ background: transparent; border: none; color: {s['primary']}; padding: 4px 10px; }}
    QToolButton {{ background: transparent; color: {s['onSurface']}; border: 1px solid transparent;
        border-radius: 8px; padding: 4px 10px; }}
    QToolButton:hover {{ background: {s['surfaceContainerHigh']}; }}
    QToolButton[menu="true"] {{ background: {s['surfaceContainerHigh']}; border: 1px solid {s['outlineVariant']};
        border-radius: 16px; padding: 6px 28px 6px 16px; }}
    QToolButton::menu-indicator {{ {down} subcontrol-origin: padding; subcontrol-position: center right;
        right: 8px; width: 12px; height: 12px; }}
    QMenu {{ background: {s['surfaceContainer']}; color: {s['onSurface']}; border: 1px solid {s['outlineVariant']};
        border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {s['secondaryContainer']}; color: {s['onSecondaryContainer']}; }}
    QMenu::item:disabled {{ color: {s['outline']}; }}
    QMenu::separator {{ height: 1px; background: {s['outlineVariant']}; margin: 4px 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid {s['onSurfaceVariant']}; border-radius: 3px;
                            background: transparent; }}
    QCheckBox::indicator:checked {{ background: {s['primary']}; border-color: {s['primary']}; {check_rule} }}
    QCheckBox::indicator:disabled {{ border-color: {s['outlineVariant']}; }}
    QCheckBox::indicator:checked:disabled {{ background: {s['outlineVariant']}; border-color: {s['outlineVariant']}; }}
    QListWidget#sidebar {{ background: {s['surfaceContainerLow']}; border: none; padding: 8px 4px; outline: 0; }}
    QListWidget#sidebar::item {{ padding: 10px 14px; margin: 2px 4px; border-radius: 16px; color: {s['onSurfaceVariant']}; }}
    QListWidget#sidebar::item:selected {{ background: {s['secondaryContainer']}; color: {s['onSecondaryContainer']}; }}
    QListWidget#sidebar::item:hover:!selected {{ background: {s['surfaceContainerHigh']}; }}
    QPushButton[chip="true"] {{ padding: 4px 12px; border: 1px solid {s['outline']}; border-radius: 8px;
                                 background: transparent; color: {s['onSurfaceVariant']}; min-height: 22px; }}
    QPushButton[chip="true"]:checked {{ background: {s['secondaryContainer']}; color: {s['onSecondaryContainer']};
                                         border-color: {s['secondaryContainer']}; }}
    QPushButton[primary="true"] {{ background: {s['primary']}; color: {s['onPrimary']}; border: none;
                                    border-radius: 18px; padding: 8px 22px; font-weight: 600; }}
    QPushButton[primary="true"]:disabled {{ background: {s['surfaceContainerHighest']}; color: {s['onSurfaceVariant']}; }}
    QLabel#toast {{ background: {s['inverseSurface']}; color: {s['inverseOnSurface']}; border-radius: 10px;
                    padding: 10px 18px; }}
    QWidget#panel {{ background: {s['surfaceContainerLow']}; border: 1px solid {s['outlineVariant']};
                      border-radius: 12px; }}
    QPushButton[feedback="ok"] {{ background: {s['primaryContainer']}; color: {s['onPrimaryContainer']}; }}
    QPushButton[feedback="error"] {{ background: {s['errorContainer']}; color: {s['onErrorContainer']}; }}
    QLabel[section="true"] {{ color: {s['primary']}; font-weight: 600; padding-top: 8px; }}
    QLabel[hint="true"] {{ color: {s['onSurfaceVariant']}; }}
    QPlainTextEdit#log {{ background: {s['surfaceContainerLowest']}; border: 1px solid {s['outlineVariant']}; border-radius: 8px; }}
    QProgressBar {{ border: none; background: {s['surfaceContainerHighest']}; border-radius: 3px; max-height: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {s['primary']}; border-radius: 3px; }}
    """


def apply_theme(app: QApplication, theme: str = "system", accent: str = "halgakos_rose") -> dict:
    app.setStyle("Fusion")
    scheme = scheme_for(theme, accent)
    app.setPalette(palette_for(scheme))
    app.setStyleSheet(stylesheet_for(scheme))
    return scheme
