"""The Queue's Upload and Show files actions, multi-select, the upload dialog and the ZIP settings, offscreen.

Uploads go to a fake host; nothing leaves the machine.
"""

import os
from dataclasses import replace

import pytest
from upload_fakes import FakeHost, install

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from noveldb.core.queue.model import DONE, FAILED, Job  # noqa: E402

TITLES = {"101": "첫 번째 책", "102": "두 번째 책", "103": "Third Book", "104": "넷째", "105": "다섯째"}


@pytest.fixture
def context(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from noveldb.qt.context import AppContext

    ctx = AppContext(start_workers=False)
    (tmp_path / "out").mkdir()

    def fake_executor(job, token):
        if job.input == "999":
            return {"ok": False, "error": "no luck"}
        out = tmp_path / "out" / f"{TITLES.get(job.input, job.input)}.epub"
        out.write_bytes(b"book " + job.input.encode() * 100)
        return {"ok": True, "title": TITLES.get(job.input, job.input), "outputs": [str(out)]}

    ctx.runner._executor = fake_executor
    ctx.update_settings(lambda s: replace(
        s, output=replace(s.output, ask_each_time=False, output_dir=str(tmp_path / "out")),
        upload=replace(s.upload, hosts=("fake",), agreement_accepted=True, copy_link=False)))
    yield ctx
    ctx._save_timer.stop()
    ctx.runner.cancel_all()
    ctx.runner.wait_idle(5)


@pytest.fixture
def host(monkeypatch):
    return install(monkeypatch, FakeHost())


@pytest.fixture
def queue(context, qtbot):
    from noveldb.qt.pages.queue import QueuePage

    page = QueuePage(context)
    qtbot.addWidget(page)
    return page


@pytest.fixture
def dialogs(monkeypatch, qtbot):
    """Run each UploadDialog to the end instead of blocking in `exec()`; the finished dialogs are collected."""
    from noveldb.qt import uploads

    shown = []

    def finish(self):
        shown.append(self)
        qtbot.addWidget(self)
        qtbot.waitUntil(lambda: self.cancel.text() == "Close", timeout=15000)
        return 0

    monkeypatch.setattr(uploads.UploadDialog, "exec", finish)
    return shown


def finish_jobs(context, queue, *inputs):
    context.runner.submit([Job(i, source="npia", engine="browser") for i in inputs])
    assert context.runner.wait_idle(5)
    queue.reload()


def select_rows(queue, *rows):
    from PySide6.QtCore import QItemSelectionModel

    model = queue.table.selectionModel()
    model.clearSelection()
    for row in rows:
        model.select(queue.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)


def test_upload_is_available_for_finished_jobs_only(context, queue):
    finish_jobs(context, queue, "101", "999")
    assert [job.state for job in context.runner.jobs] == [DONE, FAILED]
    select_rows(queue, 0)
    assert queue.upload.isEnabled() and queue.reveal.isEnabled()
    select_rows(queue, 1)
    assert not queue.upload.isEnabled()
    select_rows(queue)
    assert not queue.upload.isEnabled()
    assert queue.upload.text() == "Upload" and queue.reveal.text() == "Show files"


def test_context_menu_offers_upload_and_show_files(context, queue, monkeypatch):
    from types import SimpleNamespace

    from noveldb.qt.pages import queue as queue_module

    entries = {}

    class FakeMenu:  # a real QMenu would block in exec()
        def __init__(self, parent=None):
            self.actions = []

        def addSeparator(self):
            pass

        def addAction(self, text):
            action = SimpleNamespace(text=text, enabled=True, triggered=SimpleNamespace(connect=lambda slot: None))
            action.setEnabled = lambda on: setattr(action, "enabled", on)
            self.actions.append(action)
            return action

        def exec(self, pos):
            entries.update({a.text: a.enabled for a in self.actions})

    monkeypatch.setattr(queue_module, "QMenu", FakeMenu)
    finish_jobs(context, queue, "101", "102")
    select_rows(queue, 0, 1)
    queue._menu(queue.table.viewport().rect().center())
    assert entries["Upload"] and entries["Show files"] and entries["Select finished"]
    select_rows(queue)
    queue._menu(queue.table.viewport().rect().center())
    assert not entries["Upload"] and not entries["Show files"]


def test_several_selected_jobs_upload_as_one_zip(context, queue, host, dialogs):
    finish_jobs(context, queue, "101", "102", "103")
    select_rows(queue, 2, 0, 1)  # selected out of order; the ZIP follows the queue's order
    assert queue.detail.text() == "3 selected, 3 file(s) ready to upload"
    queue.upload.click()

    assert len(host.sent) == 1
    sent = host.sent[0]
    assert sent.name == "NovelDB_3_EPUBs.zip"
    assert sent.entry_names == ["첫 번째 책.epub", "두 번째 책.epub", "Third Book.epub"]
    dialog = dialogs[0]
    assert dialog.heading.text() == "Uploaded 3 files in 1 link"
    text = dialog.results.toPlainText()
    assert "NovelDB_3_EPUBs.zip (3 books)" in text and sent.name in text
    assert "https://fake.test/1/NovelDB_3_EPUBs.zip" in text
    assert all(title in text for title in ("첫 번째 책", "두 번째 책", "Third Book"))  # the titles inside
    assert [o.link for o in dialog.outcomes] == dialog.links == ["https://fake.test/1/NovelDB_3_EPUBs.zip"]
    assert len(context.uploads.load()) == 1 and context.uploads.load()[0].entries == 3


def test_one_job_uploads_as_the_file_not_a_zip(context, queue, host, dialogs):
    finish_jobs(context, queue, "101")
    select_rows(queue, 0)
    queue.upload.click()
    assert [sent.name for sent in host.sent] == ["npia_101.epub"]  # the Korean title becomes source and id
    assert not host.sent[0].is_zip
    assert dialogs[0].heading.text() == "Uploaded 1 file(s)"


def test_bundling_off_uploads_each_file_with_its_own_link(context, queue, host, dialogs):
    context.update_settings(lambda s: replace(s, upload=replace(s.upload, zip_bundle=False)))
    finish_jobs(context, queue, "101", "103")
    select_rows(queue, 0, 1)
    queue.upload.click()
    assert [sent.name for sent in host.sent] == ["npia_101.epub", "Third Book.epub"]
    assert dialogs[0].heading.text() == "Uploaded 2 file(s)"


def test_a_small_host_limit_gives_several_links(context, queue, host, dialogs):
    # The smallest setting is 50 MB, so a host with a tiny maximum stands in for a small "Largest ZIP".
    host.max_bytes = 4000
    finish_jobs(context, queue, "101", "102", "103", "104", "105")
    select_rows(queue, 0, 1, 2, 3, 4)
    queue.upload.click()
    assert [len(sent.entry_names) for sent in host.sent] == [3, 2]  # a ZIP holds three books at most
    assert all(len(sent.data) <= 4000 for sent in host.sent)
    assert dialogs[0].heading.text() == "Uploaded 5 files in 2 links"


def test_select_finished_picks_the_done_jobs(context, queue):
    finish_jobs(context, queue, "101", "999", "102")
    assert queue.select_finished.isEnabled()
    queue.select_finished.click()
    assert [job.input for job in queue._selected_jobs()] == ["101", "102"]
    assert queue.upload.isEnabled()


def test_select_finished_ignores_jobs_whose_files_are_gone(context, queue):
    finish_jobs(context, queue, "101", "102")
    os.remove(context.runner.jobs[0].outputs[0])
    queue.select_finished.click()
    assert [job.input for job in queue._selected_jobs()] == ["102"]


def test_select_finished_is_off_without_finished_jobs(context, queue):
    assert not queue.select_finished.isEnabled()


def test_the_queue_finished_banner_uploads_the_whole_batch_bundled(context, queue, host, dialogs, qtbot):
    context.runner.submit([Job(i, source="npia", engine="browser") for i in ("101", "102", "103")])
    assert context.runner.wait_idle(5)
    qtbot.waitUntil(lambda: queue.banner_text.text().startswith("Queue finished: 3 done"), timeout=3000)
    queue.upload_all.click()
    assert [sent.name for sent in host.sent] == ["NovelDB_3_EPUBs.zip"]
    assert dialogs[0].heading.text() == "Uploaded 3 files in 1 link"


def test_show_files_reveals_every_selected_file(context, queue, monkeypatch):
    from noveldb.core import platform_services

    shown = []
    monkeypatch.setattr(platform_services, "reveal_paths", lambda paths, *a: shown.append(list(paths)) or True)
    finish_jobs(context, queue, "101", "102", "103")
    select_rows(queue, 0, 2)
    queue.reveal.click()
    assert [[os.path.basename(p) for p in paths] for paths in shown] == [["첫 번째 책.epub", "Third Book.epub"]]


def test_the_agreement_is_still_asked_before_a_bundled_upload(context, queue, host, dialogs, monkeypatch):
    from noveldb.qt import uploads

    context.update_settings(lambda s: replace(s, upload=replace(s.upload, agreement_accepted=False)))
    asked = []
    monkeypatch.setattr(uploads, "ensure_agreement", lambda ctx, parent=None: asked.append(1) or False)
    finish_jobs(context, queue, "101", "102")
    select_rows(queue, 0, 1)
    queue.upload.click()
    assert asked == [1] and host.sent == [] and dialogs == []


# -- the dialog ------------------------------------------------------------------------------------------------


def test_the_dialog_says_when_a_zip_is_being_prepared(context, qtbot, tmp_path, host):
    from noveldb.core.upload import UploadItem
    from noveldb.qt.uploads import UploadDialog

    path = tmp_path / "a.epub"
    path.write_bytes(b"x")
    dialog = UploadDialog(context, [UploadItem(str(path))])
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.cancel.text() == "Close", timeout=10000)
    dialog._prepare(0, 3, "NovelDB_3_EPUBs.zip", 5_000_000, 10_000_000)
    assert dialog.heading.text() == "Preparing ZIP 1 of 3"
    assert "NovelDB_3_EPUBs.zip" in dialog.detail.text() and dialog.bar.value() == 500
    dialog._progress(1, 3, "Fake", 50, 100)
    assert dialog.heading.text() == "Uploading 2 of 3 to Fake" and dialog.bar.value() == 500


def test_copy_links_lists_each_link_with_what_it_holds(context, queue, host, dialogs, qapp):
    from PySide6.QtGui import QGuiApplication

    finish_jobs(context, queue, "101", "102", "103")
    select_rows(queue, 0, 1, 2)
    queue.upload.click()
    dialogs[0].copy.click()
    assert QGuiApplication.clipboard().text() == (
        "NovelDB_3_EPUBs.zip (3 books)\nhttps://fake.test/1/NovelDB_3_EPUBs.zip")


def test_copy_links_stays_bare_links_for_single_files(context, queue, host, dialogs, qapp):
    from PySide6.QtGui import QGuiApplication

    context.update_settings(lambda s: replace(s, upload=replace(s.upload, zip_bundle=False)))
    finish_jobs(context, queue, "101", "103")
    select_rows(queue, 0, 1)
    queue.upload.click()
    dialogs[0].copy.click()
    assert QGuiApplication.clipboard().text() == (
        "https://fake.test/1/npia_101.epub\nhttps://fake.test/2/Third Book.epub")


def test_a_rejected_zip_shows_what_it_held(context, queue, host, dialogs):
    host.reject = lambda name: name.endswith(".zip")
    finish_jobs(context, queue, "101", "102")
    select_rows(queue, 0, 1)
    queue.upload.click()
    dialog = dialogs[0]
    assert dialog.heading.text() == "Nothing was uploaded" and not dialog.copy.isEnabled()
    text = dialog.results.toPlainText()
    assert "NovelDB_2_EPUBs.zip (2 books)" in text and "Not uploaded:" in text and "첫 번째 책" in text


def test_the_temporary_zip_is_gone_after_the_upload(context, queue, host, dialogs):
    finish_jobs(context, queue, "101", "102")
    select_rows(queue, 0, 1)
    queue.upload.click()
    sent = host.sent[0]
    assert sent.path.endswith("NovelDB_2_EPUBs.zip") and not os.path.exists(sent.path)


# -- settings fields -------------------------------------------------------------------------------------------


def settings_widgets(context, qtbot):
    from PySide6.QtWidgets import QCheckBox, QSpinBox

    from noveldb.qt.pages.settings import upload_form

    form = upload_form(context)
    qtbot.addWidget(form)
    check = next(c for c in form.findChildren(QCheckBox) if c.text() == "Bundle several files into ZIP uploads")
    spin = form.findChildren(QSpinBox)[0]
    return form, check, spin


def test_settings_show_and_change_the_zip_options(context, qtbot):
    form, check, spin = settings_widgets(context, qtbot)
    assert check.isChecked() is True and spin.value() == 1024
    assert (spin.minimum(), spin.maximum(), spin.suffix()) == (50, 4096, " MB")
    spin.setValue(300)
    assert context.settings.upload.zip_max_mb == 300
    check.setChecked(False)
    assert context.settings.upload.zip_bundle is False and not spin.isEnabled()
    check.setChecked(True)
    assert context.settings.upload.zip_bundle is True and spin.isEnabled()
    spin.setValue(10)  # below the smallest ZIP: the box holds at 50
    assert spin.value() == 50 and context.settings.upload.zip_max_mb == 50


def test_settings_follow_changes_made_elsewhere(context, qtbot):
    form, check, spin = settings_widgets(context, qtbot)
    context.update_settings(lambda s: replace(s, upload=replace(s.upload, zip_bundle=False, zip_max_mb=2048)))
    assert not check.isChecked() and spin.value() == 2048 and not spin.isEnabled()


def test_the_zip_size_box_ignores_the_mouse_wheel(context, qtbot, qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    form, _check, spin = settings_widgets(context, qtbot)
    form.show()
    spin.setFocus()
    event = QWheelEvent(QPointF(5, 5), QPointF(spin.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    qapp.sendEvent(spin, event)
    assert spin.value() == 1024 and context.settings.upload.zip_max_mb == 1024


def test_zip_settings_are_saved_in_the_config(context):
    from noveldb.core.settings import desktop_config as dc

    context.update_settings(lambda s: replace(s, upload=replace(s.upload, zip_bundle=False, zip_max_mb=600)))
    exported = dc.export_config(context.settings)
    assert exported["upload_zip_bundle"] is False and exported["upload_zip_max_mb"] == 600
