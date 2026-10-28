"""The PySide6 window, offscreen: pages build, Download queues jobs, Queue shows them, uploads ask once."""

import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from noveldb.core.catalog.cart import CartItem  # noqa: E402
from noveldb.core.queue.model import DONE  # noqa: E402
from noveldb.core.settings.model import NPIA  # noqa: E402


@pytest.fixture
def context(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext

    ctx = AppContext(start_workers=False)
    ran = []

    def fake_executor(job, token):
        ran.append(job.input)
        out = tmp_path / f"{job.id}.epub"
        out.write_bytes(b"book")
        return {"ok": True, "title": f"Book {job.input}", "outputs": [str(out)]}

    ctx.runner._executor = fake_executor
    ctx.ran = ran
    ctx.update_settings(lambda s: replace(s, output=replace(s.output, ask_each_time=False,
                                                                output_dir=str(tmp_path / "out"))))
    yield ctx
    ctx._save_timer.stop()  # a pending save must not outlive the test's NOVELDB_HOME
    ctx.runner.cancel_all()
    ctx.runner.wait_idle(5)


@pytest.fixture
def window(context, qtbot):
    from noveldb.qt.main_window import MainWindow

    win = MainWindow(context)
    qtbot.addWidget(win)
    return win


def test_pages_build(window):
    names = [window.sidebar.item(i).text() for i in range(window.sidebar.count())]
    assert names == ["Download", "Catalog", "Queue", "Settings"]
    window.show_page(3)
    assert window.stack.currentWidget() is window.settings_page


def test_download_queues_detected_lines_and_queue_shows_them(window, context, qtbot):
    page = window.download
    page.input.setPlainText("12345\n# comment\nnot a link\n67890")
    page._detect()
    assert page.download.isEnabled()
    assert page.download.text() == "Download 2"
    assert page._source == NPIA
    assert page.workers_label.text() == "Compression Workers"
    page.download.click()
    assert context.runner.wait_idle(5)
    assert sorted(context.ran) == ["12345", "67890"]
    assert page.input.toPlainText() == "not a link"  # unrecognised lines stay
    jobs = context.runner.jobs
    assert all(job.state == DONE for job in jobs)
    assert all(job.output_dir == context.settings.output.output_dir for job in jobs)
    window.queue.reload()
    assert window.queue.table.rowCount() == 2
    assert {window.queue.table.item(r, 0).text() for r in range(2)} == {"Book 12345", "Book 67890"}
    qtbot.waitUntil(lambda: window.queue.banner.isVisible() or not window.isVisible(), timeout=2000)
    assert window.queue.banner_text.text().startswith("Queue finished: 2 done")


def test_npia_edits_go_to_its_override(window, context):
    page = window.download
    page.input.setPlainText("12345")
    page._detect()
    page.threads.setValue(3)
    assert context.settings.override(NPIA).threads == 3
    assert context.settings.shared.threads != 3 or context.settings.shared.threads == 4


def test_cart_lines_leave_the_cart_once_queued(window, context):
    context.cart.add(CartItem("12345", "A"))
    context.cart.add(CartItem("https://example.com/other"))
    assert "(2)" in window.download.cart_button.text()
    window.download.add_lines(["12345"])
    window.download.download.click()
    assert context.runner.wait_idle(5)
    assert context.cart.urls == ["https://example.com/other"]
    assert "(1)" in window.download.cart_button.text()


def test_share_agreement_is_asked_once(context, monkeypatch):
    from noveldb.qt import uploads

    asked = []

    class FakeDialog:
        def __init__(self, parent=None):
            asked.append(1)

        def exec(self):
            return uploads.QDialog.Accepted

    monkeypatch.setattr(uploads, "AgreementDialog", FakeDialog)
    assert uploads.ensure_agreement(context)
    assert context.settings.upload.agreement_accepted
    assert uploads.ensure_agreement(context)
    assert asked == [1]


def test_agreement_needs_the_checkbox(qtbot):
    from noveldb.qt.uploads import AgreementDialog

    dialog = AgreementDialog()
    qtbot.addWidget(dialog)
    assert not dialog.accept_button.isEnabled()
    dialog.confirm.setChecked(True)
    assert dialog.accept_button.isEnabled()


def test_smoke_entry_point(tmp_path):
    """`noveldb_app.py --smoke` in a fresh interpreter (other tests load tkinter into this one)."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, NOVELDB_HOME=str(tmp_path / "smoke"), NOVELDB_SMOKE_DELAY_MS="50",
               QT_QPA_PLATFORM="offscreen")
    report = tmp_path / "report.json"
    done = subprocess.run([sys.executable, str(root / "noveldb_app.py"), "--smoke", "--smoke-report", str(report)],
                          cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] and data["window"]
    assert data["pages"][0] == "Download"
    assert data["forbidden_loaded"] == []


def test_mouse_wheel_never_changes_spin_boxes_or_drop_downs(qtbot, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget

    from noveldb.qt.widgets import disable_wheel_on_inputs

    disable_wheel_on_inputs(qapp)
    area = QScrollArea()
    body = QWidget()
    layout = QVBoxLayout(body)
    spin, combo = QSpinBox(), QComboBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    combo.addItems(["a", "b", "c"])
    layout.addWidget(spin)
    layout.addWidget(combo)
    body.setMinimumHeight(3000)
    area.setWidget(body)
    area.resize(200, 200)
    qtbot.addWidget(area)
    area.show()
    for widget in (spin, combo):
        widget.setFocus()
        event = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        qapp.sendEvent(widget, event)
    assert spin.value() == 50
    assert combo.currentIndex() == 0
    assert area.verticalScrollBar().value() > 0  # the page scrolled instead


def test_save_button_animates_then_settles(qtbot):
    from PySide6.QtWidgets import QPushButton

    from noveldb.qt.widgets import ButtonFeedback

    button = QPushButton("Save")
    qtbot.addWidget(button)
    feedback = ButtonFeedback(button)
    feedback.busy("Saving")
    assert not button.isEnabled() and button.text().startswith("Saving")
    first = button.text()
    qtbot.waitUntil(lambda: button.text() != first, timeout=2000)  # the dots move
    feedback.done(True)
    assert button.isEnabled() and button.text().endswith("Saved") and button.property("feedback") == "ok"
    feedback._restore.setInterval(10)
    feedback._restore.start()
    qtbot.waitUntil(lambda: button.text() == "Save", timeout=2000)


def test_separate_txt_lives_in_settings_text_and_pdf(window, context):
    from PySide6.QtWidgets import QCheckBox

    assert not hasattr(window.download, "separate_txt")
    form = window.settings_page.forms["Text and PDF"]
    box = next(b for b in form.findChildren(QCheckBox) if b.text().startswith("Separate TXT files"))
    box.click()
    assert context.settings.shared.separate_txt is True


def test_log_is_a_column_on_download_and_a_bottom_panel_elsewhere(window, qtbot):
    window.resize(1400, 900)
    window.show()
    qtbot.waitExposed(window)
    window.show_page(0)
    assert window.log_panel.parentWidget() is window.download.log_dock
    assert window.log_panel.isVisible() and not window.log_toggle.isVisible()
    window.show_page(2)  # Queue: folded at the bottom until opened
    assert window.log_panel.parentWidget() is window.splitter
    assert not window.log_panel.isVisible() and window.log_toggle.isVisible()
    window.set_log_visible(True)
    top, bottom = window.splitter.sizes()
    assert 0.2 < bottom / (top + bottom) < 0.35
    assert window.splitter.handleWidth() >= 10
    window.show_page(0)
    assert window.log_panel.parentWidget() is window.download.log_dock and window.log_panel.isVisible()


def test_log_paths_become_links(window, tmp_path):
    from PySide6.QtCore import QUrl

    book = tmp_path / "[1] book name.epub"
    book.write_text("x", encoding="utf-8")
    panel = window.log_panel
    panel._append([f"Output file: {book} (2.96 MB)", "Download Complete!"])
    block = panel.log.document().firstBlock()
    hrefs = set()
    it = block.begin()
    while not it.atEnd():
        fragment = it.fragment()
        if fragment.charFormat().isAnchor():
            hrefs.add(fragment.charFormat().anchorHref())
            assert fragment.text() == str(book)
        it += 1
    assert hrefs == {QUrl.fromLocalFile(str(book)).toString()}
    second = block.next()
    assert not any(second.begin().fragment().charFormat().isAnchor() for _ in [0])


def test_shutdown_after_a_successful_queue_counts_down_and_can_be_cancelled(window, context, qtbot):
    from dataclasses import replace

    calls = []
    window.maybe_shut_down(2, 0, shut_down=lambda: calls.append("off"))
    assert not hasattr(window, "shutdown_dialog")  # the setting is off
    context.update_settings(lambda s: replace(s, output=replace(s.output, shutdown_after_success=True)))
    assert window.download.shutdown.isChecked()
    window.maybe_shut_down(2, 1, shut_down=lambda: calls.append("off"))
    assert not hasattr(window, "shutdown_dialog")  # a failure: no shutdown
    window.maybe_shut_down(2, 0, shut_down=lambda: calls.append("off"))
    dialog = window.shutdown_dialog
    dialog.reject()  # Cancel
    dialog._tick()
    assert calls == []
    window.maybe_shut_down(1, 0, shut_down=lambda: calls.append("off"))
    window.shutdown_dialog.remaining = 1
    window.shutdown_dialog._tick()
    assert calls == ["off"]


def test_options_keep_a_compact_width_and_the_log_gets_the_rest(window, qtbot):
    window.resize(2560, 1300)
    window.show()
    qtbot.waitExposed(window)
    window.show_page(0)
    page = window.download
    page._user_split = False
    page._fit_split()
    left, right = page.split.sizes()
    assert left <= page._columns.maximumWidth() + 24 and right > left
