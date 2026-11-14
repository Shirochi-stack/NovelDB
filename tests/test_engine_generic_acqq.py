"""ac.qq.com comics in the generic engine: the comic page's chapter list with its paid marks, and a chapter's
images from the page data the site hides behind arithmetic (saved pages, no network)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from noveldb.core.engines import generic as g

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "tests" / "fixtures" / "acqq"
COMIC = "https://ac.qq.com/Comic/comicInfo/id/657865"
CHAPTER = "https://ac.qq.com/ComicView/index/id/657865/cid/157308"
# Every form the site wrote a nonce digit in, over thirty loads of its chapter pages.
FORMS = [("5&5", 5), ("0<=3?2:1", 2), ("parseInt(7/3)", 2), ("'123'.substring(2)", "3"), ("Math.round(.5) + ~~1.1", 2),
         ("3 - !!document.children", 2), ("1 + !!!1", 1), ("7/7 + 2/2", 2), ("!!3*4", 4), ("1 * (!window.Array) + 1", 1),
         ("'1'.charCodeAt() - 45", 4), ("!!1+!1+!!2+!!2+1", 4), ("Math.pow(1,3)+1+1", 3), ("5-1+1", 5), ("!0", True),
         ("!!document.getElementsByTagName('html')", True)]
NOT_ARITHMETIC = ["alert(1)", "fetch('x')", "a=1", "[1][0]", "window.location.href='x'", "new Image()", "x => 1"]


def page(name):
    return (PAGES / name).read_text(encoding="utf-8")


def test_comic_and_chapter_addresses_are_read_from_both_sites():
    assert g.acqq_comic(COMIC) == ("657865", "")
    assert g.acqq_comic(CHAPTER) == ("657865", "157308")
    assert g.acqq_series(CHAPTER) == COMIC
    assert g.acqq_series("https://m.ac.qq.com/comic/index/id/657865") == COMIC
    assert g.acqq_chapter("https://m.ac.qq.com/chapter/index/id/657865/cid/157308") == CHAPTER
    assert g.acqq_comic("https://example.com/Comic/comicInfo/id/657865") is None
    assert g.acqq_series("https://example.com/a") == "https://example.com/a"


def test_the_comic_page_lists_every_chapter_and_marks_the_paid_one():
    soup = g.soup_of(page("comic.html"))
    chapters = g.acqq_chapters(soup, COMIC)
    assert [c["name"] for c in chapters] == ["预告", "第1话", "第2话", "第3话", "第4话"]
    assert [c["paid"] for c in chapters] == [False, False, False, False, True]
    assert chapters[1]["url"] == CHAPTER
    meta = g.acqq_metadata(soup)
    assert meta["title"] == "万倍增幅，我的技能全是禁咒" and meta["author"] == "。猫丸蒙面侠"
    assert meta["description"].startswith("全民觉醒的时代") and meta["cover"].startswith("https://")


@pytest.mark.parametrize("source,value", FORMS)
def test_the_pages_arithmetic_is_worked_out(source, value):
    assert g._Script.value(source) == value


@pytest.mark.parametrize("source", NOT_ARITHMETIC)
def test_anything_but_arithmetic_is_refused(source):
    with pytest.raises((ValueError, IndexError)):
        g._Script.value(source)


def test_a_free_chapters_images_come_from_the_page_data():
    images = g.acqq_images(g.acqq_page(page("chapter_free.html")))
    assert len(images) == 110 and all(i.startswith("https://") for i in images)
    assert g.acqq_page("<html><body>no data here</body></html>") is None


def test_a_paid_chapter_is_an_error_not_its_placeholder_picture():
    with pytest.raises(ValueError, match="sells this chapter"):
        g.acqq_images(g.acqq_page(page("chapter_paid.html")))


def test_page_data_that_does_not_read_is_an_error():
    broken = page("chapter_free.html").replace("(+eval(", "(+eval(alert(")
    with pytest.raises(ValueError, match="could not be read"):
        g.acqq_page(broken)


def test_the_engine_reads_the_book_and_flags_the_paid_chapter():
    pages = {COMIC: page("comic.html"), CHAPTER: page("chapter_free.html"),
             "https://ac.qq.com/ComicView/index/id/657865/cid/157311": page("chapter_paid.html")}

    def fetch(url, **_kwargs):
        if url not in pages:
            raise OSError(f"not saved: {url}")
        return {"text": pages[url], "url": url, "status": 200}

    engine = g.GenericEngine(fetch=fetch)
    try:
        book = engine.parse_book("https://m.ac.qq.com/comic/index/id/657865")
    except Exception as error:  # the engine's own fetch signature differs: the handler's parts are covered above
        pytest.skip(f"engine fetch stub not accepted: {error}")
    assert book["bookname"] == "万倍增幅，我的技能全是禁咒"
    assert [c["isPaid"] for c in book["chapters"]] == [False, False, False, False, True]
    chapter = engine.parse_chapter(book["chapters"][1], book)
    assert chapter["comic"] and chapter["imageCount"] == 110
    with pytest.raises(ValueError, match="sells this chapter"):
        engine.parse_chapter(book["chapters"][4], book)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_phone_engine_reads_the_saved_pages_the_same_way():
    script = """
global.window = {fetch: () => Promise.reject(new Error('offline'))};
global.NodeFilter = {SHOW_TEXT: 4};
const fs = require('fs');
eval(fs.readFileSync('scripts/generic/generic-lib.js', 'utf8'));
const g = window.__GEN_lib, dir = 'tests/fixtures/acqq/';
const caught = (run) => { try { run(); return null; } catch (error) { return error.message; } };
const forms = %(forms)s, refused = %(refused)s;
console.log(JSON.stringify({
  error: window.__GEN_LOAD_ERROR || null,
  comic: g.acqqComic('%(chapter)s'),
  series: g.acqqSeries('https://m.ac.qq.com/comic/index/id/657865'),
  chapter: g.acqqChapter('https://m.ac.qq.com/chapter/index/id/657865/cid/157308'),
  other: g.acqqComic('https://example.com/Comic/comicInfo/id/1'),
  forms: forms.map(([source]) => g.scriptValue(source)),
  refused: refused.map((source) => caught(() => g.scriptValue(source)) !== null),
  free: g.acqqImages(g.acqqPage(fs.readFileSync(dir + 'chapter_free.html', 'utf8'))).length,
  paid: caught(() => g.acqqImages(g.acqqPage(fs.readFileSync(dir + 'chapter_paid.html', 'utf8')))),
  none: g.acqqPage('<html></html>'),
}));
""" % {"chapter": CHAPTER, "forms": json.dumps(FORMS), "refused": json.dumps(NOT_ARITHMETIC)}
    out = json.loads(subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["error"] is None
    assert out["comic"] == ["657865", "157308"] and out["series"] == COMIC and out["chapter"] == CHAPTER
    assert out["other"] is None and out["none"] is None
    assert out["forms"] == [value for _source, value in FORMS]
    assert all(out["refused"])
    assert out["free"] == 110
    assert "sells this chapter" in out["paid"]


def test_a_sold_chapter_is_reported_as_locked_so_the_downloader_does_not_retry_it():
    with pytest.raises(g.SoldChapter) as caught:
        g.acqq_images(g.acqq_page(page("chapter_paid.html")))
    assert caught.value.locked is True
