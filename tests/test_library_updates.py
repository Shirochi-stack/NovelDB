"""Library preference defaults, queue admission and safe refresh through the app executor."""
import threading
from pathlib import Path
from zipfile import ZipFile

import pytest

from noveldb.core import library
from noveldb.core.events import CancelToken
from noveldb.core.library_updates import LibraryUpdates, replace_epub, source_key
from noveldb.core.queue.model import Job
from noveldb.core.queue.runner import QueueRunner


def epub(path, count):
    with ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        items = ''.join(f'<item id="c{i}" href="{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(count))
        spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(count))
        archive.writestr("book.opf", '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Book</dc:title><dc:source>https://example.test/book/1</dc:source></metadata><manifest>' + items + '</manifest><spine>' + spine + '</spine></package>')
        for index in range(count):
            archive.writestr(f"{index}.xhtml", f"<html><head><title>Chapter {index + 1}</title></head><body>Chapter text</body></html>")
    return path


def test_range_defaults_bulk_override_opt_out_and_restart(tmp_path):
    state = LibraryUpdates(tmp_path / "preferences.json")
    full, ranged = tmp_path / "Full.epub", tmp_path / "Range.epub"
    state.record(full, "https://example.test/1", False)
    state.record(ranged, "https://example.test/2", True)
    assert state.allowed(full) and not state.allowed(ranged)
    state.set_books([full, ranged], True)
    state.configure(enabled=False)
    assert not state.allowed(full) and not state.allowed(ranged)
    state = LibraryUpdates(state.path)
    assert state.preferred(full) and state.preferred(ranged)
    state.configure(enabled=True)
    assert state.allowed(full) and state.allowed(ranged)
    state.set_books([full, ranged], False)
    state.record(full, "https://example.test/1", False)
    assert not state.allowed(full)


def test_old_books_without_range_history_require_opt_in(tmp_path):
    epub(tmp_path / "Book.epub", 2)
    state = LibraryUpdates(tmp_path / "preferences.json")
    state.discover(library.scan(tmp_path), [])
    assert not state.allowed(tmp_path / "Book.epub")
    assert state.entry(tmp_path / "Book.epub")["url"]


def test_shorter_refresh_leaves_original_untouched(tmp_path):
    target = epub(tmp_path / "book.epub", 3)
    original = target.read_bytes()
    candidate = epub(tmp_path / "new.epub", 2)
    with pytest.raises(ValueError, match="fewer chapters"):
        replace_epub(candidate, target)
    assert target.read_bytes() == original
    epub(candidate, 4)
    replace_epub(candidate, target)
    assert len(library.epub_contents(target)["chapters"]) == 4


def test_same_source_jobs_wait_while_another_source_can_run():
    release = threading.Event()
    first = threading.Event()
    other = threading.Event()
    def execute(job, token):
        (other if "other.test" in job.input else first).set()
        release.wait(5)
        return {"ok": True}
    runner = QueueRunner(execute, max_concurrent=3, engine_limits={"a": 3, "b": 3})
    jobs = [Job("https://www.example.test/1", engine="a", library_target="one.epub"), Job("https://m.example.test/2", engine="b", library_target="two.epub"), Job("https://other.test/3", engine="b")]
    try:
        runner.submit(jobs)
        assert first.wait(2) and other.wait(2)
        assert jobs[1].state == "queued"
        assert source_key("https://011.example.test/1") == source_key("https://012.example.test/2")
        assert source_key("123", "npia") == source_key("https://example.test/book/2", "npia")
    finally:
        release.set()
        runner.wait_idle(5)


@pytest.mark.parametrize("warnings", [[], ["Chapter failed"]])
def test_app_refresh_stages_output_and_preserves_the_saved_name(tmp_path, monkeypatch, qtbot, warnings):
    from noveldb.qt.context import AppContext
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "state"))
    context = AppContext(start_workers=False)
    target = epub(tmp_path / "Saved name.epub", 2)
    original = target.read_bytes()
    context.library_updates.record(target, "https://example.test/1", False)
    def download(job, token, output_dir=None):
        assert Path(output_dir) != target.parent
        output = epub(Path(output_dir) / "Changed source title.epub", 3)
        return {"ok": True, "outputs": [str(output)], "warnings": warnings}
    context._download = download
    try:
        job = Job("https://example.test/1", library_target=str(target))
        if warnings:
            with pytest.raises(ValueError, match="complete EPUB"):
                context._execute_job(job, CancelToken())
            assert target.read_bytes() == original
        else:
            result = context._execute_job(job, CancelToken())
            assert result["outputs"] == [str(target)]
            assert len(library.epub_contents(target)["chapters"]) == 3
    finally:
        context.shutdown()


def test_desktop_bulk_controls_badges_and_card_open(tmp_path, monkeypatch, qtbot):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QPushButton, QCheckBox, QDialog, QDialogButtonBox
    from noveldb.qt.context import AppContext
    from noveldb.qt.pages.library import LibraryPage
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "state"))
    context = AppContext(start_workers=False)
    first = epub(tmp_path / "Full.epub", 2)
    second = epub(tmp_path / "Range.epub", 2)
    context.library_updates.record(first, "https://example.test/1", False)
    context.library_updates.record(second, "https://example.test/2", True)
    page = LibraryPage(context)
    qtbot.addWidget(page)
    try:
        page._take(library.scan(tmp_path), page._scan_started)
        assert page.grid.count() == 2
        for index in range(2):
            page.grid.item(index).setSelected(True)
        next(button for button in page.findChildren(QPushButton) if button.text() == "Enable auto-update").click()
        assert context.library_updates.allowed(first) and context.library_updates.allowed(second)
        page._fill()
        assert len(page.grid.selectedItems()) == 2
        assert all(page.grid.item(i).data(Qt.UserRole + 1) == "Auto-update" for i in range(2))
        def disable_globally():
            dialog = page.findChild(QDialog)
            next(check for check in dialog.findChildren(QCheckBox) if check.text() == "Allow Library auto-updates").setChecked(False)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        QTimer.singleShot(0, disable_globally)
        page._library_settings()
        assert not context.library_updates.data["enabled"]
        assert all(not page.grid.item(i).data(Qt.UserRole + 1) for i in range(2))
        requested = []
        monkeypatch.setattr(context, "request_library_update", requested.append)
        page._show_book(page.books[0])
        assert len(requested) == 1
    finally:
        context.shutdown()


def test_open_coalesces_requests_and_global_opt_out_cancels_pending_updates(tmp_path, monkeypatch, qtbot):
    from noveldb.qt.context import AppContext
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "state"))
    context = AppContext(start_workers=False)
    context.runner.pause()
    target = epub(tmp_path / "Book.epub", 2)
    context.library_updates.record(target, "https://example.test/book/1", False)
    try:
        context.request_library_update(target)
        context.request_library_update(target)
        assert len(context.runner.jobs) == 1
        assert context.runner.jobs[0].state == "queued"
        second = epub(tmp_path / "Another saved copy.epub", 2)
        context.library_updates.record(second, "https://example.test/book/1", False)
        context.request_library_updates([target, second, second])
        assert len(context.runner.jobs) == 2
        context.configure_library(enabled=False)
        assert context.runner.jobs[0].state == "cancelled"
        context.request_library_update(target)
        assert len(context.runner.jobs) == 2
        assert context.library_updates.preferred(target)
    finally:
        context.shutdown()


def test_library_dispatch_overrides_the_current_download_range_and_formats(tmp_path):
    from dataclasses import replace
    from noveldb.core.events import CallbackEvents
    from noveldb.core.jobs import JobDispatcher
    from noveldb.core.settings.model import AppSettings
    seen = []
    class Download:
        def __init__(self, address, settings, *args):
            seen.append(settings)
        def run(self):
            return {"ok": True}
    settings = AppSettings()
    settings = replace(settings, shared=replace(settings.shared, range_from=8, range_to=12, formats=("txt",), generate_on_stop=True))
    dispatcher = JobDispatcher(lambda: settings, None, None, lambda job: CallbackEvents(), npia_job_factory=Download)
    job = Job("123", source="npia", engine="npia_native", library_target=str(tmp_path / "book.epub"))
    dispatcher(job, CancelToken(), output_dir=str(tmp_path))
    assert seen[0].shared.range_from is None and seen[0].shared.range_to is None
    assert seen[0].shared.formats == ("epub",) and not seen[0].shared.generate_on_stop
