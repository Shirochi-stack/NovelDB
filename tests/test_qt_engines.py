"""Settings > Site engines: reorder, switch off, and the WebToEpub update controls."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QLabel, QListWidget, QPushButton  # noqa: E402

from noveldb.core import engine_updates  # noqa: E402


@pytest.fixture
def context(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext

    ctx = AppContext(start_workers=False)
    yield ctx
    ctx._save_timer.stop()


@pytest.fixture
def form(context, qtbot):
    from noveldb.qt.pages.settings import engines_form

    f = engines_form(context)
    qtbot.addWidget(f)
    return f


def _names(engines):
    return [engines.item(i).data(Qt.UserRole) for i in range(engines.count())]


def _button(form, text):
    return next(b for b in form.findChildren(QPushButton) if b.text() == text)


def test_reorder_and_switch_off(form, context):
    engines = form.findChild(QListWidget)
    assert _names(engines) == ["native", "novel_downloader", "webtoepub"]
    engines.setCurrentRow(2)
    _button(form, "Move up").click()
    _button(form, "Move up").click()
    assert context.settings.engines.order == ("webtoepub", "native", "novel_downloader")
    assert _names(engines) == ["webtoepub", "native", "novel_downloader"]
    assert engines.item(0).text().startswith("1. WebToEpub parsers")

    engines.item(2).setCheckState(Qt.Unchecked)
    assert context.settings.engines.disabled == ("novel_downloader",)
    assert context.settings.engines.enabled() == ("webtoepub", "native")

    _button(form, "Default order").click()
    assert context.settings.engines.enabled() == ("native", "novel_downloader", "webtoepub")


def test_the_last_engine_cannot_be_switched_off(form, context):
    engines = form.findChild(QListWidget)
    engines.item(0).setCheckState(Qt.Unchecked)
    engines.item(1).setCheckState(Qt.Unchecked)
    engines.item(2).setCheckState(Qt.Unchecked)
    assert context.settings.engines.enabled() == ("webtoepub",)
    assert engines.item(2).checkState() == Qt.Checked


def test_turning_on_updates_checks_once(form, context, monkeypatch):
    calls = []
    monkeypatch.setattr(context, "check_engine_updates", lambda automatic=False: calls.append(automatic))
    boxes = {b.text(): b for b in form.findChildren(QCheckBox) if "automatically" in b.text()}
    nd = boxes["Update the novel-downloader rules automatically"]
    w2e = boxes["Update the WebToEpub parsers automatically"]
    nd.click()
    assert context.settings.engines.nd_auto_update is True
    assert context.settings.engines.webtoepub_auto_update is False
    assert calls == [True]
    w2e.click()
    assert calls == [True, True]
    nd.click()  # turning one off checks nothing
    assert calls == [True, True]


def test_check_now_shows_each_engine(form, context, monkeypatch):
    def fake(automatic=False):
        for engine in engine_updates.ENGINES:
            context.engineUpdateChecked.emit(
                engine_updates.UpdateResult(engine, "current", f"The {engine.title} are up to date."))
    monkeypatch.setattr(context, "check_engine_updates", fake)
    button = _button(form, "Check for updates now")
    button.click()
    shown = "\n".join(label.text() for label in form.findChildren(QLabel) if label.isVisibleTo(form))
    assert "The novel-downloader rules are up to date." in shown
    assert "The WebToEpub parsers are up to date." in shown
    assert "novel-downloader rules: " in shown and "WebToEpub parsers: " in shown
    assert button.text() == "✓ Up to date"
