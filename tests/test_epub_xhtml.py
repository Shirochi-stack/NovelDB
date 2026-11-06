"""EPUB pages are well-formed XHTML whatever HTML a site sent (strict readers such as Thorium parse them as XML)."""
import xml.etree.ElementTree as ET
import zipfile

from epub_generator import EpubGenerator, xhtml_body


def test_a_sites_html_becomes_well_formed_xhtml(tmp_path):
    path = tmp_path / "book.epub"
    epub = EpubGenerator({"title": "Book", "author": "Author"}, str(path), "")
    epub.add_chapter("One", '<p>Before</p><hr><p>A&nbsp;B <br> <img src="../Images/a.webp" alt=x></p><p>open')
    epub.add_extra_page("info.xhtml", "<p>Info<hr></p>")
    epub.generate()
    with zipfile.ZipFile(path) as book:
        pages = [name for name in book.namelist() if name.startswith("OEBPS/Text/")]
        assert len(pages) == 3
        for name in pages:
            ET.fromstring(book.read(name))  # raises on anything that is not XML
        chapter = book.read("OEBPS/Text/chapter0001.xhtml").decode("utf-8")
    assert "<hr/>" in chapter and "A B" in chapter and "../Images/a.webp" in chapter and "open" in chapter


def test_well_formed_content_is_kept_as_it_is():
    body = '<p class="x">Text &amp; more<br/></p><hr/>'
    assert xhtml_body(body) == body
