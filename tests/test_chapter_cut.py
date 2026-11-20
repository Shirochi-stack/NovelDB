""""Upload chapters" takes its chapters from the saved files, and the Library shows one card per book."""
import os
import re
import zipfile
from types import SimpleNamespace

import pytest

from noveldb.core import chapter_cut, chapter_range, library

OPF = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book</dc:title><dc:creator>Ann</dc:creator>
<meta name="cover" content="cover-image"/></metadata>
<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="cover-image" href="Images/cover.jpg" media-type="image/jpeg"/>
<item id="info" href="Text/info.xhtml" media-type="application/xhtml+xml"/>
%s</manifest><spine toc="ncx"><itemref idref="info"/>%s</spine></package>"""


def _book(folder, name, chapters):
    """An EPUB, a chapter ZIP and a chapter folder the way a download saves them."""
    items = refs = points = ""
    epub = folder / f"{name}.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml",
                         '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/Images/cover.jpg", b"cover")
        archive.writestr("OEBPS/Text/info.xhtml", '<html><body><img src="../Images/cover.jpg"/></body></html>')
        for n in chapters:
            archive.writestr(f"OEBPS/Images/ch{n:04d}_0001.webp", b"page %d" % n)
            archive.writestr(f"OEBPS/Text/chapter{n:04d}.xhtml",
                             f'<html><body><h1>Chapter {n}</h1><img src="../Images/ch{n:04d}_0001.webp"/></body></html>')
            items += (f'<item id="chapter{n:04d}" href="Text/chapter{n:04d}.xhtml" media-type="application/xhtml+xml"/>'
                      f'<item id="img{n}" href="Images/ch{n:04d}_0001.webp" media-type="image/webp"/>')
            refs += f'<itemref idref="chapter{n:04d}"/>'
            points += (f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>Chapter {n}</text></navLabel>'
                       f'<content src="Text/chapter{n:04d}.xhtml"/></navPoint>')
        archive.writestr("OEBPS/content.opf", OPF % (items, refs))
        archive.writestr("OEBPS/toc.ncx", f"<ncx><navMap>{points}</navMap></ncx>")
    pages = folder / f"{name}.folder"
    with zipfile.ZipFile(folder / f"{name}.zip", "w") as archive:
        for n in chapters:
            (pages / f"{n:04d} Chapter {n}").mkdir(parents=True)
            (pages / f"{n:04d} Chapter {n}" / "001.webp").write_bytes(b"page %d" % n)
            archive.writestr(f"{n:04d} Chapter {n}/001.webp", b"page %d" % n)
    return [str(epub), str(pages), str(folder / f"{name}.zip")]


def test_chapters_are_cut_out_of_the_saved_files_without_any_download(tmp_path):
    outputs = _book(tmp_path, "Book (1,3-5)", [1, 3, 4, 5])
    made, failed = chapter_cut.cut_outputs(outputs, 3, 4, "")
    assert failed == []
    assert sorted(os.path.basename(path) for path in made) == ["Book (3-4).epub", "Book (3-4).folder", "Book (3-4).zip"]
    with zipfile.ZipFile(tmp_path / "Book (3-4).epub") as archive:
        names = archive.namelist()
        assert names[0] == "mimetype" and archive.testzip() is None
        assert [n for n in names if "chapter" in n] == ["OEBPS/Text/chapter0003.xhtml", "OEBPS/Text/chapter0004.xhtml"]
        assert sorted(n for n in names if "Images" in n) == [
            "OEBPS/Images/ch0003_0001.webp", "OEBPS/Images/ch0004_0001.webp", "OEBPS/Images/cover.jpg"]
        opf = archive.read("OEBPS/content.opf").decode()
        assert all("OEBPS/" + href in names for href in re.findall(r'href="([^"]+)"', opf))
        assert re.findall(r'idref="([^"]+)"', opf) == ["info", "chapter0003", "chapter0004"]
        assert re.findall(r'src="([^"]+)"', archive.read("OEBPS/toc.ncx").decode()) == [
            "Text/chapter0003.xhtml", "Text/chapter0004.xhtml"]
    assert sorted(os.listdir(tmp_path / "Book (3-4).folder")) == ["0003 Chapter 3", "0004 Chapter 4"]
    with zipfile.ZipFile(tmp_path / "Book (3-4).zip") as archive:
        assert archive.namelist() == ["0003 Chapter 3/001.webp", "0004 Chapter 4/001.webp"]
    # A comma list, from a whole-book file.
    whole = _book(tmp_path, "Other", [1, 2, 3, 4])
    made, _failed = chapter_cut.cut_outputs(whole[:1], 1, 4, "1,4")
    assert [os.path.basename(path) for path in made] == ["Other (1,4).epub"]
    with zipfile.ZipFile(made[0]) as archive:
        assert [n[-9:-6] for n in archive.namelist() if "chapter" in n] == ["001", "004"]


def test_chapters_the_files_do_not_hold_and_undivided_files_are_reported(tmp_path):
    outputs = _book(tmp_path, "Book (1-2)", [1, 2])
    assert chapter_cut.cut_outputs(outputs, 7, 7, "") == ([], ["Book (1-2).epub", "Book (1-2).folder", "Book (1-2).zip"])
    assert not [name for name in os.listdir(tmp_path) if "(7)" in name]  # nothing half-written is left
    pdf = tmp_path / "Book.pdf"
    pdf.write_bytes(b"%PDF")
    assert chapter_cut.cut_outputs([str(pdf)], 1, 1, "") == ([], ["Book.pdf"])
    # The files already are that range: they are the answer as they are.
    assert chapter_cut.cut_outputs(outputs[:1], 1, 2, "") == ([outputs[0]], [])


def test_file_names_give_their_range_back():
    assert chapter_range.split_name("Book (4-7)") == ("Book", "4-7")
    assert chapter_range.split_name("Book (1,3-5)") == ("Book", "1,3-5")
    assert chapter_range.split_name("Book (4-end)") == ("Book", "4-end")
    assert chapter_range.split_name("Book (Remastered)") == ("Book (Remastered)", "")
    assert chapter_range.split_name("(4)") == ("(4)", "")


def test_the_library_shows_one_card_for_a_book_and_its_ranged_downloads(tmp_path):
    _book(tmp_path, "Book (1,3-5)", [1, 3, 4, 5])
    _book(tmp_path, "Book (4)", [4])
    whole = _book(tmp_path, "Book", [1, 2, 3, 4, 5])
    _book(tmp_path, "Another", [1])
    books = {book.name: book for book in library.scan(tmp_path)}
    assert sorted(books) == ["Another", "Book"]
    book = books["Book"]
    assert len(book.files) == 9 and {f.format for f in book.files} == {"EPUB", "ZIP", "Folder"}
    assert (book.title, book.author) == ("Book", "Ann")
    # Reading and the cover use the whole book, not one of its parts.
    assert str(book.readable().path) == whole[0]

def test_upload_chapters_uploads_a_cut_of_the_saved_files_and_queues_no_download(tmp_path, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from noveldb.core.queue.model import DONE, Job
    from noveldb.qt import uploads
    from noveldb.qt.context import AppContext
    from noveldb.qt.main_window import MainWindow
    from noveldb.qt.pages import queue as queue_page

    context = AppContext(start_workers=False)
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.queue
    books = tmp_path / "books"
    books.mkdir()
    job = Job("https://example.com/b", source="kpage", state=DONE, title="Book", chapters=[1, 5, "1,3-5"],
              outputs=_book(books, "Book (1,3-5)", [1, 3, 4, 5]))
    context.runner._jobs.append(job)
    page.reload()
    page.table.selectRow(0)
    uploaded, told = [], []
    monkeypatch.setattr(queue_page, "upload_jobs", lambda context, jobs, parent=None: uploaded.append(list(jobs)))
    monkeypatch.setattr(uploads, "ensure_agreement", lambda context, parent=None: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: told.append(args[2]))

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("4", True))
    page._upload_chapters()
    assert not [j for j in context.runner.jobs if j.active]  # nothing was queued to download
    part = uploaded[0][0]
    assert part is not job and part.state == DONE and part.chapters == [4, 4, ""]
    assert sorted(os.path.basename(path) for path in part.outputs) == ["Book (4).epub", "Book (4).folder", "Book (4).zip"]
    assert part in context.runner.jobs

    # Chapters the files do not hold: said so, nothing uploaded, nothing downloaded.
    page.table.selectRow(page.table.rowCount() - 1)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("9", True))
    page._upload_chapters()
    assert len(uploaded) == 1 and "not in the saved files" in told[0]
    assert not [j for j in context.runner.jobs if j.active]

    # No range: the saved files go up as they are.
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("", True))
    page._upload_chapters()
    assert uploaded[1] == [job]
    context._save_timer.stop()


def test_several_formats_ask_which_to_upload_and_links_are_clickable(tmp_path, monkeypatch, qtbot):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QDialog

    from noveldb.core.upload import UploadItem, UploadOutcome
    from noveldb.qt import uploads

    outputs = _book(tmp_path, "Book", [1, 2])
    items = [UploadItem(path) for path in outputs]
    assert [uploads.format_of(path) for path in outputs] == ["EPUB", "Folder", "ZIP"]
    # One format: nothing to ask.
    monkeypatch.setattr(uploads.FormatDialog, "exec", lambda self: pytest.fail("asked about one format"))
    assert uploads.choose_formats(items[:1]) == items[:1]

    def untick_zip(dialog):
        qtbot.addWidget(dialog)
        assert list(dialog.boxes) == ["EPUB", "Folder", "ZIP"] and dialog.upload.isEnabled()
        for box in dialog.boxes.values():
            box.setChecked(False)
        assert not dialog.upload.isEnabled()  # nothing ticked uploads nothing
        dialog.boxes["EPUB"].setChecked(True)
        dialog.boxes["Folder"].setChecked(True)
        return QDialog.Accepted

    monkeypatch.setattr(uploads.FormatDialog, "exec", untick_zip)
    assert uploads.choose_formats(items) == items[:2]
    # The choice is remembered for the next upload, and Cancel uploads nothing.
    monkeypatch.setattr(uploads.FormatDialog, "exec", lambda dialog: QDialog.Accepted)
    assert uploads.choose_formats(items) == items[:2]
    monkeypatch.setattr(uploads.FormatDialog, "exec", lambda dialog: QDialog.Rejected)
    assert uploads.choose_formats(items) == []
    uploads._last_formats.clear()

    monkeypatch.setattr(uploads.UploadTask, "start", lambda self: None)
    from noveldb.qt.context import AppContext

    context = AppContext(start_workers=False)
    dialog = uploads.UploadDialog(context, items[:1])
    qtbot.addWidget(dialog)
    dialog._outcome(UploadOutcome("Book <1>.epub", result=SimpleNamespace(link="https://example.com/f?a=1&b=2")))
    assert dialog.results.openExternalLinks()
    assert 'href="https://example.com/f?a=1&amp;b=2"' in dialog.results.toHtml()
    assert "Book <1>.epub" in dialog.results.toPlainText() and "https://example.com/f?a=1&b=2" in dialog.results.toPlainText()
    context._save_timer.stop()


def test_the_library_says_which_chapters_each_file_of_a_book_is(tmp_path):
    _book(tmp_path, "Book (4-7)", [4, 5, 6, 7])
    _book(tmp_path, "Book (2)", [2])
    _book(tmp_path, "Book", [1, 2])
    book = library.scan(tmp_path)[0]
    assert [library.file_range(f.path) for f in book.files] == ["", "", "", "2", "2", "2", "4-7", "4-7", "4-7"]
