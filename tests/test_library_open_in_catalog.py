"""A Library book found again in the Catalog: EPUBs say where they came from, and the Library reads it."""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from noveldb.core import library
from noveldb.core.catalog.live_reader import LiveReader
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


def test_open_prefers_the_epub_even_if_txt_is_listed_first(tmp_path):
    text = tmp_path / "A Book.txt"
    text.write_text("Some text", encoding="utf-8")
    epub = tmp_path / "A Book.EPUB"
    epub.touch()
    assert library.reader_file([text, epub]) == epub
    assert library.scan(tmp_path)[0].readable().path == epub
    epub.unlink()
    assert library.reader_file([epub, text]) == text


LISTED = "Book\n\nTable of Contents\n\nFirst chapter\nSecond chapter\n\nFirst chapter\n\nLine <one>\n\n  Indented.\n\nSecond chapter\n\nLast line."
SPACED = "First chapter\n\nLine <one>\n\n  Indented.\n\n\nSecond chapter\n\nLast line.\n"
RULED = "Book\nAuthor: A Writer\n========\n\n────────\nFirst chapter\n────────\n\nLine <one>\n\n  Indented.\n\n────────\nSecond chapter\n────────\n\nLast line."


@pytest.mark.parametrize("content,encoding", [(SPACED, "utf-8"), (RULED, "utf-8-sig"), (LISTED, "utf-16")])
def test_desktop_catalog_reads_txt_chapters_and_keeps_spacing(tmp_path, content, encoding):
    text = tmp_path / "Book.txt"
    text.write_bytes(content.replace("\n", "\r\n").encode(encoding))
    reader = LiveReader(lambda *_: pytest.fail("local reading called the network"))
    book = reader.book(text.as_uri())
    assert [c["name"] for c in book["chapters"]] == ["First chapter", "Second chapter"]
    assert "Line &lt;one&gt;\n\n  Indented." in reader.chapter(text.as_uri(), 0)["html"]
    assert "Last line." in reader.chapter(text.as_uri(), 1)["html"]


def test_a_plain_txt_is_read_as_one_section(tmp_path):
    text = tmp_path / "Plain.txt"
    text.write_text("One line.\n\nAnother paragraph.", encoding="utf-8")
    assert library.txt_contents(text)["chapters"] == [
        {"name": "Plain", "text": "One line.\n\nAnother paragraph."},
    ]


@pytest.mark.parametrize("assets,content", [
    ("android/app/src/main/assets/catalog", SPACED),
    ("ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog", LISTED),
])
def test_mobile_reader_opens_txt_and_navigates_its_chapters(assets, content):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).resolve().parents[1] / assets
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    with playwright.sync_playwright() as api:
        options = {} if Path(api.chromium.executable_path).is_file() else {"channel": "chrome"}
        try:
            browser = api.chromium.launch(**options)
        except playwright.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        with browser:
            page = browser.new_page(viewport={"width": 390, "height": 800})

            def serve(route):
                name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
                if (assets / name).is_file():
                    route.fulfill(path=assets / name)
                else:
                    route.fulfill(body="")

            page.route("**/*", serve)
            page.add_init_script(
                "window.NovelDBTransport = async request => request.op === 'file' "
                "&& request.url === 'file:///Library/Book.txt' ? "
                f"{{status: 200, responseBase64: {json.dumps(encoded)}}} : {{status: 404}};"
            )
            page.goto("https://catalog.test/index.html")
            page.evaluate("window.NovelDBGeneric.readLocal({title: 'Book', url: 'file:///Library/Book.txt'})")
            playwright.expect(page.locator("#flow")).to_contain_text("Line <one>")
            page.locator("#rToc").click()
            page.locator("#tocList").get_by_text("Second chapter", exact=False).click()
            playwright.expect(page.locator("#flow")).to_contain_text("Last line.")
