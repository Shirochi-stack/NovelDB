"""Settings › General › App updates, the update notice, and the download-then-restart flow."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox, QPushButton, QWidget  # noqa: E402

from noveldb.core import app_update  # noqa: E402

ASSET = app_update.Asset(name="NovelDB98-windows-x86_64_hotfix1.exe", variant="full", arch="x86_64", kind="exe",
                         build=2000, label="hotfix1", commit="c", size=9, sha256="0" * 64)
UPDATE = app_update.Update("v98", "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", ASSET)


@pytest.fixture
def context(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext

    ctx = AppContext(start_workers=False)
    yield ctx
    ctx._save_timer.stop()


@pytest.fixture
def general(context, qtbot):
    from noveldb.qt.pages.settings import general_form

    f = general_form(context)
    qtbot.addWidget(f)
    return f


def _texts(widget, kind):
    return [w.text() for w in widget.findChildren(kind)]


def test_a_development_build_says_so_and_cannot_check(general, context):
    assert context.settings.updates.check_automatically is True
    labels = " ".join(_texts(general, QLabel))
    assert "This copy: Development build." in labels
    assert "app updates are off" in labels
    check = next(b for b in general.findChildren(QPushButton) if b.text() == "Check for app updates now")
    assert not check.isEnabled()
    box = next(b for b in general.findChildren(QCheckBox) if b.text() == "Check for app updates automatically")
    box.click()
    assert context.settings.updates.check_automatically is False


def test_a_found_update_shows_update_now(general, context, monkeypatch):
    install = next(b for b in general.findChildren(QPushButton) if b.text() == "Update now")
    assert install.isHidden()
    result = app_update.CheckResult("update", "NovelDB v98 hotfix1 is available.", UPDATE)
    context.appUpdateChecked.emit(result, False)
    assert not install.isHidden()
    assert "NovelDB v98 hotfix1 is available." in _texts(general, QLabel)


def test_the_automatic_check_only_reports_updates(context, qtbot, monkeypatch):
    from noveldb.qt.app_update import UpdateOffer

    parent = QWidget()
    qtbot.addWidget(parent)
    offer = UpdateOffer(parent, context)
    offered = []
    monkeypatch.setattr(offer, "offer", offered.append)
    offer.checked(app_update.CheckResult("current", "up to date"), True)
    offer.checked(app_update.CheckResult("update", "x", UPDATE), False)  # the Settings button shows its own
    offer.checked(app_update.CheckResult("update", "x", UPDATE), True)
    offer.checked(app_update.CheckResult("update", "x", UPDATE), True)  # offered once per build
    assert offered == [UPDATE]


def test_update_downloads_verifies_and_queues_the_install(context, qtbot, monkeypatch, tmp_path):
    from noveldb.qt import app as qt_app
    from noveldb.qt.app_update import UpdateOffer

    parent = QWidget()
    qtbot.addWidget(parent)
    offer = UpdateOffer(parent, context)
    plan = app_update.InstallPlan(["helper"], "Replace NovelDB.")
    monkeypatch.setattr(app_update, "download", lambda update, progress, cancelled: tmp_path / "file")
    monkeypatch.setattr(app_update, "prepare_install", lambda update, path: plan)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    monkeypatch.setitem(qt_app._update, "plan", None)
    offer.install(UPDATE)
    qtbot.waitUntil(lambda: bool(asked), timeout=5000)
    assert qt_app._update["plan"] is plan  # installed when NovelDB quits
    assert "downloaded and verified" in asked[0]


def test_a_failed_check_of_the_download_installs_nothing(context, qtbot, monkeypatch):
    from noveldb.qt import app as qt_app
    from noveldb.qt.app_update import UpdateOffer

    parent = QWidget()
    qtbot.addWidget(parent)
    offer = UpdateOffer(parent, context)

    def bad(update, progress, cancelled):
        raise app_update.UpdateError("The download did not match its checksum; nothing was installed.")
    monkeypatch.setattr(app_update, "download", bad)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    monkeypatch.setitem(qt_app._update, "plan", None)
    offer.install(UPDATE)
    qtbot.waitUntil(lambda: bool(warnings), timeout=5000)
    assert "did not match its checksum" in warnings[0]
    assert qt_app._update["plan"] is None
