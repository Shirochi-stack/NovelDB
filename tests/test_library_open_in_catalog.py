"""A Library book found again in the Catalog: EPUBs say where they came from, and the Library reads it."""
from noveldb.core import library
from epub_generator import EpubGenerator


def test_epubs_record_their_source_and_the_library_reads_it(tmp_path):
    url = "https://example.test/novel/42"
    epub = EpubGenerator({"title": "A Book", "author": "Someone", "source_url": url}, str(tmp_path / "A Book.epub"), "")
    epub.add_chapter("One", "<p>text</p>")
    epub.generate()
    books = library.scan(tmp_path)
    assert [(b.title, b.author, b.url) for b in books] == [("A Book", "Someone", url)]


def test_an_epub_without_a_source_has_none(tmp_path):
    epub = EpubGenerator({"title": "Old", "author": "", "source_url": "not a link"}, str(tmp_path / "Old.epub"), "")
    epub.add_chapter("One", "<p>text</p>")
    epub.generate()
    assert library.scan(tmp_path)[0].url == ""
