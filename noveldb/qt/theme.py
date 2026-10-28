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


def _check_image(color: str) -> str:
    """A checkmark SVG in `color`, written once to the cache (QSS images need a file)."""
    try:
        from noveldb.core import paths

        folder = paths.cache_dir() / "theme"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"check-{color.lstrip('#').lower()}.svg"
        if not target.exists():
            target.write_text(
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M3.5 8.5l3 3 6-7' "
                f"fill='none' stroke='{color}' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>",
                encoding="utf-8")
        return target.as_posix()
    except OSError:
        return ""


def stylesheet_for(scheme: dict) -> str:
    s = scheme
    check = _check_image(s["onPrimary"])
    check_rule = f"image: url({check});" if check else ""
    return f"""
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
