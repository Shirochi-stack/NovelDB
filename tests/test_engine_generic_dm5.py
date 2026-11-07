"""The generic engine on DM5-platform manga sites (fanfox, mangahere), from saved fanfox pages: the book page's
two chapter tabs, the chapter's packed key, its pages from chapterfun.ashx, and the phone engine doing the same."""
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from noveldb.core.engines.generic import (
    DM5_AD_SIZE,
    GenericEngine,
    dm5_chapter,
    dm5_chapters,
    dm5_desktop,
    dm5_images,
    image_size,
    soup_of,
    unpack_js,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "dm5"
BOOK_URL = "https://fanfox.net/manga/one_piece/"
CHAPTER_URL = "https://fanfox.net/manga/one_piece/v01/c001/1.html"
PAGES = "https://zjcdn.mangafox.me/store/manga/106/01-001.0/compressed/"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def answer(url: str, body, status: int = 200) -> dict:
    content = body if isinstance(body, bytes) else body.encode("utf-8")
    return {"status": status, "url": url, "content": content, "text": "" if isinstance(body, bytes) else body,
            "headers": "Content-Type: text/html; charset=utf-8"}


def test_the_chapter_page_gives_its_id_page_count_and_packed_key():
    html = fixture("fanfox_chapter.html")
    assert any("guidkey" in code for code in unpack_js(html))
    assert dm5_chapter(html) == {"cid": "398501", "count": 62, "key": "81d8f8d240bf6852", "images": []}
    assert dm5_chapter("<html><body><p>A chapter of text.</p></body></html>") is None


def test_a_chapterfun_answer_unpacks_to_two_page_addresses():
    assert dm5_images(fixture("fanfox_chapterfun_1.js"), CHAPTER_URL) == [
        PAGES + "uone_piece_v001-000.jpg?token=79022ea1d03a08b5da603d849bfac1790caded60&ttl=1794067200",
        PAGES + "uone_piece_v001-001.jpg?token=0e7d6d0a6f87b4a3ed3a6fccad27e8385301d8cf&ttl=1794067200",
    ]
    assert [u.split("?")[0].rsplit("/", 1)[1] for u in dm5_images(fixture("fanfox_chapterfun_3.js"), CHAPTER_URL)] == \
        ["uone_piece_v001-002.jpg", "uone_piece_v001-003.jpg"]


def test_both_chapter_tabs_are_read_oldest_first():
    chapters = dm5_chapters(soup_of(fixture("fanfox_book.html")), BOOK_URL)
    numbers = [c["url"].split("/c")[-1].split("/")[0] for c in chapters]
    assert numbers == ["001", "002", "003", "1024.5", "1046.5", "1196", "1197", "1198"]
    assert chapters[0] == {"url": CHAPTER_URL, "name": "Vol.01 Ch.001 - Romance Dawn"}
    assert dm5_chapters(soup_of("<html><body><a href='/c1/'>x</a></body></html>"), BOOK_URL) == []


def test_mobile_addresses_become_the_desktop_ones():
    assert dm5_desktop("https://m.fanfox.net/roll_manga/one_piece/v01/c001/1.html") == CHAPTER_URL
    assert dm5_desktop("http://m.fanfox.net/manga/one_piece/") == BOOK_URL
    assert dm5_desktop("https://m.mangahere.cc/manga/one_piece/") == "https://www.mangahere.cc/manga/one_piece/"
    assert dm5_desktop("https://example.com/m/x") == "https://example.com/m/x"


def test_image_sizes_come_from_their_headers():
    assert image_size(png(1000, 563)) == DM5_AD_SIZE
    assert image_size(jpeg(728, 1140)) == (728, 1140)
    assert image_size(b"<html>403</html>") is None


def test_the_engine_reads_the_book_page():
    def fetch(url, **_kwargs):
        return answer(url, fixture("fanfox_book.html"))

    book = GenericEngine(fetch=fetch).parse_book("https://m.fanfox.net/manga/one_piece/")
    assert book["bookUrl"] == BOOK_URL
    assert book["bookname"] == "One Piece"
    assert book["author"] == "ODA Eiichiro"
    assert "Action" in book["tags"]
    assert book["introduction"].startswith("Gol D. Roger") and not book["introduction"].endswith("...")
    assert len(book["chapters"]) == 8 and book["chapters"][-1]["name"] == "Vol.TBE Ch.1198"


def test_the_engine_reads_a_chapter_from_chapterfun_and_leaves_out_the_closing_advert():
    page = fixture("fanfox_chapter.html").replace("imagecount=62", "imagecount=5")
    answers = {1: fixture("fanfox_chapterfun_1.js"), 3: fixture("fanfox_chapterfun_3.js"),
               5: 'var pix="//zjcdn.mangafox.me/store/manga/106/01-001.0/compressed";'
                  'var pvalue=["/p4.jpg","/p5.jpg"];for(var i=0;i<pvalue.length;i++){pvalue[i]=pix+pvalue[i]}'}
    asked = []

    def fetch(url, referer="", headers=None, binary=False, **_kwargs):
        asked.append((url, referer, dict(headers or {})))
        if "chapterfun.ashx" in url:
            return answer(url, answers[int(url.split("page=")[1].split("&")[0])])
        if url.endswith("/p4.jpg"):
            return answer(url, png(*DM5_AD_SIZE))  # the advert that ends every chapter
        return answer(url, page)

    shown = []
    engine = GenericEngine(fetch=fetch)
    engine.on_images = lambda images, total: shown.append((len(images), total))
    result = engine.parse_chapter({"url": "https://m.fanfox.net/roll_manga/one_piece/v01/c001/1.html", "name": "Ch.001"},
                                  {"bookUrl": BOOK_URL})
    images = [part.split('"')[0] for part in result["contentHtml"].split('<img src="')[1:]]
    assert [u.split("?")[0].rsplit("/", 1)[1] for u in images] == \
        ["uone_piece_v001-000.jpg", "uone_piece_v001-001.jpg", "uone_piece_v001-002.jpg", "uone_piece_v001-003.jpg"]
    calls = [call for call in asked if "chapterfun.ashx" in call[0]]
    assert [c[0].split("?")[1] for c in calls] == [f"cid=398501&page={n}&key=81d8f8d240bf6852" for n in (1, 3, 5)]
    assert all(c[1] == CHAPTER_URL and c[2].get("X-Requested-With") == "XMLHttpRequest" for c in calls)
    assert asked[0][0] == CHAPTER_URL  # the desktop page, not the mobile one ("licensed and not available")
    assert shown[-1] == (4, 4)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_phone_engine_reads_the_saved_pages_the_same_way():
    script = """
global.window = {fetch: () => Promise.reject(new Error('offline'))};
const fs = require('fs');
eval(fs.readFileSync('scripts/generic/generic-lib.js', 'utf8'));
const g = window.__GEN_lib, dir = 'tests/fixtures/dm5/', base = '%s';
const ad = Buffer.from('89504e470d0a1a0a0000000d49484452000003e80000023308020000', 'hex');
console.log(JSON.stringify({
  error: window.__GEN_LOAD_ERROR || null,
  chapter: g.dm5Chapter(fs.readFileSync(dir + 'fanfox_chapter.html', 'utf8')),
  first: g.dm5Images(fs.readFileSync(dir + 'fanfox_chapterfun_1.js', 'utf8'), base),
  desktop: g.dm5Desktop('https://m.fanfox.net/roll_manga/one_piece/v01/c001/1.html'),
  ad: g.imageSize(new Uint8Array(ad)),
}));
""" % CHAPTER_URL
    out = json.loads(subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["error"] is None
    assert out["chapter"] == dm5_chapter(fixture("fanfox_chapter.html"))
    assert out["first"] == dm5_images(fixture("fanfox_chapterfun_1.js"), CHAPTER_URL)
    assert out["desktop"] == CHAPTER_URL
    assert tuple(out["ad"]) == DM5_AD_SIZE
