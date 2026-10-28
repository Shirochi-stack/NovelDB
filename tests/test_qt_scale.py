"""The interface grows with the window (noveldb/qt/scale.py)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QListWidgetItem, QWidget  # noqa: E402

from noveldb.qt import scale  # noqa: E402
from noveldb.qt.scale import bind_rows, bind_size, bind_title, factor_for, scale_stylesheet, ui_scale  # noqa: E402


@pytest.fixture(autouse=True)
def reset(qapp):
    yield
    ui_scale.set_factor(1.0)


def test_the_factor_follows_the_tighter_side_between_one_and_the_cap():
    assert factor_for(1280, 860) == 1.0  # smaller than the base: never shrinks
    assert factor_for(*scale.BASE_SIZE) == 1.0
    assert factor_for(2560, 1400) == 1.45  # height is the tighter side
    assert factor_for(5120, 2880) == scale.MAX_FACTOR
    assert factor_for(2560, 1400, enabled=False) == 1.0


def test_stylesheet_pixels_scale_but_hairlines_stay():
    qss = "QPushButton { border: 1px solid red; padding: 6px 16px; border-radius: 16px; } #id12px { width: 0.5px; }"
    out = scale_stylesheet(qss, 1.5)
    assert "border: 1px solid" in out and "padding: 9px 24px" in out and "border-radius: 24px" in out
    assert "#id12px" in out and "0.5px" in out
    assert scale_stylesheet(qss, 1.0) == qss


def test_bound_widgets_and_the_app_font_follow_the_factor(qtbot):
    holder = QWidget()
    qtbot.addWidget(holder)
    title = bind_title(QLabel("Download", holder), 24)
    panel = bind_size(QWidget(holder), min_width=360, max_width=480)
    sidebar = QListWidget(holder)
    sidebar.addItem(QListWidgetItem("Download"))
    bind_rows(sidebar, 44)
    base_points = QApplication.font().pointSizeF()
    assert ui_scale.set_factor(1.5)
    assert panel.minimumWidth() == 540 and panel.maximumWidth() == 720
    assert "font-size: 36px" in title.styleSheet()
    assert sidebar.item(0).sizeHint().height() == 66
    assert QApplication.font().pointSizeF() == pytest.approx(base_points * 1.5)
    assert sidebar.font().pointSizeF() == pytest.approx(base_points * 1.5)  # not the platform's list font
    assert not ui_scale.set_factor(1.5)  # unchanged
    ui_scale.set_factor(1.0)
    assert panel.minimumWidth() == 360 and QApplication.font().pointSizeF() == pytest.approx(base_points)


def test_the_main_window_scales_when_it_grows(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    window = MainWindow(context)
    qtbot.addWidget(window)
    window.resize(2560, 1400)
    window._rescale()
    assert ui_scale.factor == 1.45
    assert window.sidebar.width() == round(176 * 1.45)
    from dataclasses import replace
    context.update_settings(lambda s: replace(s, ui=replace(s.ui, scale_with_window=False)))
    assert ui_scale.factor == 1.0
    context._save_timer.stop()


def test_a_jump_scales_at_once_and_a_drag_waits(qtbot, tmp_path, monkeypatch):
    # Maximising or snapping used to paint the old scale at the new size first (the page laid out
    # two or three times); a jump now scales in the same pass, before the first paint.
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    window = MainWindow(context)
    qtbot.addWidget(window)
    window.resize(1500, 900)
    window.show()
    qtbot.waitUntil(lambda: window.width() == 1500, timeout=3000)
    window._rescale()
    assert ui_scale.factor == 1.0

    window.resize(2560, 1400)  # a jump: no waiting for the timer
    qtbot.waitUntil(lambda: window.width() == 2560, timeout=3000)
    assert ui_scale.factor == 1.45
    assert not window._scale_timer.isActive()
    page = window.download
    left, log = page.split.sizes()
    assert log >= int((left + log) * 0.4) - 2  # the split is refit in the same pass

    window.resize(2500, 1400)  # a small step, as when dragging an edge: scaled once it settles
    qtbot.waitUntil(lambda: window.width() == 2500, timeout=3000)
    assert window._scale_timer.isActive()
    qtbot.waitUntil(lambda: not window._scale_timer.isActive(), timeout=3000)
    assert ui_scale.factor == factor_for(2500, 1400)
    context._save_timer.stop()


def test_a_jump_is_restyled_before_the_window_moves(qtbot, tmp_path, monkeypatch):
    # On Windows the restyle ran inside the resize, so the maximise / restore animation faded into
    # the old frame (a ghost of the old size) for a few hundred ms. WM_WINDOWPOSCHANGING now
    # restyles for the coming size first; the resize itself only lays out and paints.
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from PySide6.QtCore import QSize

    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    window = MainWindow(context)
    qtbot.addWidget(window)
    window.resize(1500, 900)
    window.show()
    qtbot.waitUntil(lambda: window.width() == 1500, timeout=3000)
    window._rescale()

    window._scale_before(QSize(1520, 910))  # a drag step: left to the resize
    assert ui_scale.factor == 1.0 and not window._relayout
    window._scale_before(QSize(2560, 1369))  # maximising
    assert ui_scale.factor == factor_for(2560, 1369)
    assert window._relayout and window._scale_timer.isActive()  # put right if the move is cancelled
    window.resize(2560, 1369)
    qtbot.waitUntil(lambda: window.width() == 2560, timeout=3000)
    assert not window._relayout and not window._scale_timer.isActive()
    left, log = window.download.split.sizes()
    assert log >= int((left + log) * 0.4) - 2  # laid out in the resize's own pass
    context._save_timer.stop()


def test_the_native_frame_follows_the_app_theme(qapp, monkeypatch):
    # The offscreen platform ignores colour schemes, so record what apply_theme asks Qt for.
    from PySide6.QtCore import Qt

    from noveldb.qt import theme

    calls = []

    class Hints:
        def setColorScheme(self, scheme):
            calls.append(scheme)

        def unsetColorScheme(self):
            calls.append("system")

        def colorScheme(self):
            return Qt.ColorScheme.Dark

    monkeypatch.setattr(theme.QGuiApplication, "styleHints", staticmethod(lambda: Hints()))
    for name in ("light", "dark", "amoled", "system"):
        theme.apply_theme(qapp, name)
    assert calls == [Qt.ColorScheme.Light, Qt.ColorScheme.Dark, Qt.ColorScheme.Dark, "system"]
    monkeypatch.undo()
    theme.apply_theme(qapp, "system")


def test_the_setting_round_trips_through_the_desktop_config():
    from noveldb.core.settings.desktop_config import export_config, import_config

    settings = import_config({"ui_scale_with_window": False})
    assert settings.ui.scale_with_window is False
    assert export_config(settings)["ui_scale_with_window"] is False
    assert import_config({}).ui.scale_with_window is True
