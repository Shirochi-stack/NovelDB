import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from noveldb.core.library_live import LibraryLive
from noveldb.qt.pages.library_reader import LocalBookReader


def test_plain_reader_live_contents_preserve_text_and_scroll(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = LibraryLive()
    reader = LocalBookReader(store)
    monkeypatch.setattr("noveldb.qt.pages.library_reader._places", lambda: {})
    monkeypatch.setattr("noveldb.qt.pages.library_reader._save_place", lambda *args: None)
    file = tmp_path / "book.epub"
    book = SimpleNamespace(title="Book", readable=lambda: SimpleNamespace(path=file))
    reader.resize(900, 600)
    reader.show()
    reader.open_book(book, {"chapters": [{"name": "One", "text": "Saved text\n" * 300}, {"name": "Two", "text": "Second"}]})
    app.processEvents()
    reader.page.verticalScrollBar().setValue(200)
    before = reader.page.toHtml()
    position = reader.page.verticalScrollBar().value()
    session = store.begin(file)
    session.book([{"title": name, "id": name} for name in ("Inserted", "One", "Two", "Three")])
    reader.poll_live()
    assert len(reader.chapters) == 4 and reader.index == 1
    assert reader.page.toHtml() == before and reader.page.verticalScrollBar().value() == position
    reader.open_chapter(3)
    assert reader.index == 1 and reader.page.toHtml() == before
    session.chapter(3, "<p>New chapter</p>")
    reader.poll_live()
    assert reader.page.toHtml() == before and reader.page.verticalScrollBar().value() == position
    reader.open_chapter(3)
    assert "New chapter" in reader.page.toPlainText()
    reader._remember.stop()
    reader._live_timer.stop()
    reader.close()
    store.close()
