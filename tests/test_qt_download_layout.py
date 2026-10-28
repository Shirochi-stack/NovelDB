"""The Download page's layout: the log's default share and where Enter App (sfc) lives."""
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


def test_the_log_gets_about_forty_percent_of_the_page_by_default(window, qtbot):
    from noveldb.qt.pages.download import LOG_SHARE

    window.resize(1920, 1040)
    window.show()
    page = window.download
    qtbot.waitUntil(lambda: page.split.width() > 1000, timeout=3000)
    page._fit_split()
    left, log = page.split.sizes()
    total = left + log
    assert log >= int(total * LOG_SHARE) - 2
    assert log >= page.log_dock.minimumWidth()
