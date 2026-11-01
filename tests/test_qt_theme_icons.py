"""The stylesheet's small icons (checkbox tick, drop-down arrows) are PNG files Qt reads itself."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402


def test_the_tick_is_a_png_with_ink_in_it(qapp, tmp_path, monkeypatch):
    # A built app that could not load Qt's SVG plugin showed checked boxes with no tick.
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path))
    from noveldb.qt import theme

    path = theme._icon_file("check", "#603338")
    assert path.endswith(".png")
    image = QImage(path)
    assert not image.isNull()
    inked = sum(
        1 for x in range(0, image.width(), 4) for y in range(0, image.height(), 4)
        if image.pixelColor(x, y).alpha() > 0
    )
    assert inked > 10


def test_every_stylesheet_icon_is_a_png(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path))
    from noveldb.qt import theme

    qss = theme.stylesheet_for(theme.scheme_for("dark", "halgakos_rose"))
    assert "image: url(" in qss and ".svg)" not in qss
