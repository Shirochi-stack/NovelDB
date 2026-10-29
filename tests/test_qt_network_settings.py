"""Settings > General > Network: the Request timeout and Page load wait spin boxes (offscreen Qt)."""

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from noveldb.core import net_timeouts  # noqa: E402
from noveldb.core.settings.store import ConfigStore, read_raw  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_timeouts():
    net_timeouts.reset()
    yield
    net_timeouts.reset()


@pytest.fixture
def context(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext

    ctx = AppContext(start_workers=False)
    yield ctx
    ctx._save_timer.stop()  # a pending save must not outlive the test's NOVELDB_HOME
    ctx.runner.cancel_all()
    ctx.runner.wait_idle(5)


@pytest.fixture
def general(context, qtbot):
    from noveldb.qt.main_window import MainWindow

    window = MainWindow(context)
    qtbot.addWidget(window)
    return window.settings_page.forms["General"]


def _network_spins(form):
    from PySide6.QtWidgets import QFormLayout, QSpinBox

    spins = {}
    for layout in form.findChildren(QFormLayout):
        for row in range(layout.rowCount()):
            label = layout.itemAt(row, QFormLayout.LabelRole)
            field = layout.itemAt(row, QFormLayout.FieldRole)
            if label and field and isinstance(field.widget(), QSpinBox):
                spins[label.widget().text()] = field.widget()
    return spins


def test_the_network_group_edits_both_settings_and_saves_them(context, general):
    from PySide6.QtWidgets import QLabel

    spins = _network_spins(general)
    request, page = spins["Request timeout"], spins["Page load wait"]
    for box in (request, page):
        assert (box.minimum(), box.maximum(), box.suffix(), box.value()) == (10, 600, " s", 60)
    texts = [label.text() for label in general.findChildren(QLabel)]
    assert "Network" in texts
    assert "Minimum wait for a server or a page. Sources that need longer keep their own." in texts

    request.setValue(120)
    page.setValue(240)
    assert context.settings.shared.request_timeout == 120
    assert context.settings.shared.page_load_timeout == 240
    assert (net_timeouts.request_setting(), net_timeouts.page_setting()) == (120, 240)
    context.save_now()  # the same ConfigStore path the 0.5 s timer takes
    raw = read_raw(context.store.path)
    assert raw["request_timeout"] == 120 and raw["page_load_timeout"] == 240
    assert ConfigStore(context.store.path).load().shared.page_load_timeout == 240


def test_the_form_follows_settings_loaded_elsewhere(context, general):
    context.update_settings(lambda s: replace(s, shared=replace(s.shared, request_timeout=33, page_load_timeout=44)))
    spins = _network_spins(general)
    assert (spins["Request timeout"].value(), spins["Page load wait"].value()) == (33, 44)


def test_the_network_spin_boxes_ignore_the_mouse_wheel(general, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from noveldb.qt.widgets import disable_wheel_on_inputs

    disable_wheel_on_inputs(qapp)
    box = _network_spins(general)["Request timeout"]
    box.setFocus()
    event = QWheelEvent(QPointF(5, 5), QPointF(box.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, 120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    qapp.sendEvent(box, event)
    assert box.value() == 60
