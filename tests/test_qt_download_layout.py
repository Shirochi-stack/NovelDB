"""The Download page's layout: the log's fixed share and font, and where Enter App (sfc) lives."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QPushButton, QToolButton  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch, qtbot):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow

    context = AppContext(start_workers=False)
    main = MainWindow(context)
    qtbot.addWidget(main)
    yield main
    context._save_timer.stop()


def test_enter_app_is_in_the_tools_menu_not_on_the_page(window):
    page = window.download
    assert not [b for b in page.findChildren(QPushButton) if b.text().startswith("Enter App")]
    tools = next(b for b in page.findChildren(QToolButton) if b.text() == "Tools")
    labels = [action.text() for action in tools.menu().actions()]
    assert "Enter App (sfc)..." in labels
    assert labels.index("Enter App (sfc)...") + 1 == labels.index("Open the Android emulator (sfc)")
    requested = []
    page.toolRequested.disconnect()  # the window would open the real (modal) sign-in dialog
    page.toolRequested.connect(requested.append)
    next(a for a in tools.menu().actions() if a.text() == "Enter App (sfc)...").trigger()
    assert requested == ["sfc_app"]


def share(page) -> float:
    left, log = page.split.sizes()
    return log / (left + log)


def test_the_log_keeps_forty_percent_at_every_size(window, qtbot):
    from noveldb.qt.pages.download import LOG_SHARE

    window.resize(1920, 1040)
    window.show()
    page = window.download
    qtbot.waitUntil(lambda: page.split.width() > 1000, timeout=3000)
    page._fit_split()
    assert abs(share(page) - LOG_SHARE) < 0.01
    assert page.split.sizes()[1] >= page.log_dock.minimumWidth()
    # Not worked out from the options' width any more: the same share when maximised (a new scale)
    # and after a small drag of the window edge.
    for width, height in [(2560, 1400), (2500, 1400), (1700, 1000)]:
        window.resize(width, height)
        qtbot.waitUntil(lambda: window.width() == width, timeout=3000)
        qtbot.waitUntil(lambda: not window._scale_timer.isActive(), timeout=3000)
        assert abs(share(page) - LOG_SHARE) < 0.01, (width, page.split.sizes())


def test_a_dragged_split_keeps_its_share_when_the_window_changes(window, qtbot):
    window.resize(1920, 1040)
    window.show()
    page = window.download
    qtbot.waitUntil(lambda: page.split.width() > 1000, timeout=3000)
    page._fit_split()
    left, log = page.split.sizes()
    page.split.setSizes([left + log // 2, log - log // 2])
    page._split_moved()  # what dragging the handle reports
    dragged = share(page)
    window.resize(2560, 1400)
    qtbot.waitUntil(lambda: window.width() == 2560, timeout=3000)
    assert abs(share(page) - dragged) < 0.01


def test_the_log_text_is_ten_points_at_the_base_size(window):
    from PySide6.QtWidgets import QApplication

    from noveldb.qt.scale import ui_scale
    from noveldb.qt.theme import apply_theme

    apply_theme(QApplication.instance())  # as the app does at start
    view = window.log_panel.log
    try:
        # Back and forth: a restyle must not put the previous size back.
        for factor, size in [(1.0, 10.0), (1.45, 14.5), (1.0, 10.0), (1.5, 15.0)]:
            ui_scale.set_factor(factor)
            assert view.font().pointSizeF() == size, factor
    finally:
        ui_scale.set_factor(1.0)
