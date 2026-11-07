import json
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import pytest

from noveldb.core.engines import custom_engines, mangapeak, tako
from noveldb.core.settings.model import EngineSettings


def reply(url, body):
    return {"status": 200, "url": url, "text": json.dumps(body) if isinstance(body, dict) else body}


def test_article_image_queue_uses_the_public_feed_and_exact_chapter_identity():
    origin = "https://" + mangapeak.SITE
    book_url = origin + "/manga/example/"
    chapter_url = origin + "/read/example/ch1-335490/"
    asked = []

    def fetch(url, **options):
        asked.append(url)
        if url == book_url:
            return reply(url, '<body data-manga-id="335453"><h1>Example</h1></body>')
        assert options["referer"] == book_url
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if parts.path == "/auth/manga-chapters":
            assert query["manga_id"] == ["335453"] and query["order"] == ["ASC"]
            assert len(query["_t"][0]) == 16 and query["_ts"][0].isdigit()
            first = query["offset"] == ["0"]
            rows = ([{"id": "335490", "chapter": "1", "title": "N/A", "url": chapter_url}] if first else
                    [{"id": "335491", "chapter": "2", "title": "Later", "is_locked": True,
                      "url": origin + "/read/example/ch2-335491/"}])
            return reply(url, {"success": True, "chapters": rows, "has_more": first})
        assert parts.path == "/auth/chapter-content" and query == {"chapter_id": ["335490"]}
        return reply(url, {"success": True, "chapter_type": "media",
                           "images": ["https://cdn.example/p2.webp", "https://cdn.example/p1.webp"]})

    runner = mangapeak.MangaPeakEngine(fetch)
    book = runner.parse_book(chapter_url)
    assert [c["name"] for c in book["chapters"]] == ["Chapter 1", "Chapter 2: Later"]
    chapter = runner.parse_chapter(book["chapters"][0], book)
    assert chapter["imageCount"] == 2 and chapter["comic"]
    assert chapter["contentHtml"].index("p2.webp") < chapter["contentHtml"].index("p1.webp")
    assert runner.parse_chapter(book["chapters"][1], book)["_locked"]
    assert len(asked) == 4


def test_tako_maps_the_feed_and_reads_original_mangadex_pages():
    manga = "19033d6c-ff8b-4c91-9642-8d1f30f7e399"
    chapter = "34722c27-f8b8-49b0-b206-3643eb5c52d9"
    book_url = f"https://mangadex.org/title/{manga}"
    raw = "a" * 32

    def fetch(url, **_):
        path = urlsplit(url).path
        if path == f"/manga/{manga}":
            return reply(url, {"result": "ok", "data": {"attributes": {"title": {"en": "Example"}}}})
        if path == f"/manga/{manga}/feed":
            return reply(url, {"result": "ok", "total": 2, "data": [
                {"id": manga, "attributes": {"externalUrl": "https://elsewhere.example/"}},
                {"id": chapter, "attributes": {"translatedLanguage": "en", "chapter": "1", "pages": 2}},
            ]})
        assert path == f"/at-home/server/{chapter}"
        return reply(url, {"result": "ok", "baseUrl": "https://images.example",
                           "chapter": {"hash": raw, "data": ["p2.webp", "p1.webp"], "dataSaver": ["small.webp"]}})

    engine = tako.TakoEngine(fetch)
    book = engine.parse_book(book_url)
    assert [c["name"] for c in book["chapters"]] == ["Chapter 1"]
    pages = engine.parse_chapter(book["chapters"][0], book)
    assert pages["imageCount"] == 2
    assert f"/data/{raw}/p2.webp" in pages["contentHtml"] and "small.webp" not in pages["contentHtml"]


def test_tako_reads_the_packed_mangahere_pages_in_order():
    origin = "https://" + tako.HERE
    book_url = origin + "/manga/example/"
    chapter_url = book_url + "c1/"
    script = r"""eval(function(p,a,c,k,e,d){return p;}('var newImgs=[\'//zjcdn.mangahere.org/store/manga/p2.webp\',\'//zjcdn.mangahere.org/store/manga/p1.webp\'];',62,0,''.split('|'),0,{}));"""

    def fetch(url, **_):
        if url == book_url:
            return reply(url, """<h1 class="detail-top-bar-info-title">Example</h1>
                <div class="detail-chapters-list"><a href="c2/">Chapter 2</a><a href="c1/">Chapter 1</a></div>""")
        assert url == chapter_url
        return reply(url, "var chapterid = 123; var imagecount = 2;" + script)

    engine = tako.TakoEngine(fetch)
    book = engine.parse_book(book_url)
    assert [c["name"] for c in book["chapters"]] == ["Chapter 1", "Chapter 2"]
    pages = engine.parse_chapter(book["chapters"][0], book)
    assert pages["imageCount"] == 2 and pages["imageReferer"] == chapter_url
    assert pages["contentHtml"].index("p2.webp") < pages["contentHtml"].index("p1.webp")


def test_tako_uses_mangaheres_reader_endpoint_when_the_chapter_only_has_a_key():
    chapter = "https://" + tako.HERE + "/manga/example/c1/1.html"
    asked = []

    def fetch(url, **options):
        if url == chapter:
            script = r"""eval(function(p,a,c,k,e,d){return p;}('var guidkey=\'test\';',62,0,''.split('|'),0,{}));"""
            return reply(url, "var chapterid = 123; var imagecount = 3;" + script)
        parts = urlsplit(url)
        assert parts.path == "/manga/example/c1/chapterfun.ashx" and options["referer"] == chapter
        query = parse_qs(parts.query)
        assert query["cid"] == ["123"] and query["key"] == ["test"]
        page = int(query["page"][0])
        asked.append(page)
        return reply(url, f'var pix = "https://zjcdn.mangahere.org/store/manga/"; var pvalue = ["p{page}.jpg", "p{page + 1}.jpg"];')

    pages = tako.TakoEngine(fetch).parse_chapter({"url": chapter}, {})
    assert asked == [1, 3]
    assert pages["imageCount"] == 3 and "p4.jpg" not in pages["contentHtml"]


def test_dynamic_priority_lists_both_bundled_engines_for_their_own_sites():
    from external_scraper import ExternalScraper

    order = EngineSettings(order=("haruneko", "generic")).enabled()
    assert order[:4] == ("mangapeak", "tako", "haruneko", "generic")
    assert ExternalScraper._engine_knows_site("mangapeak", "https://" + mangapeak.SITE + "/manga/example/")
    assert ExternalScraper._engine_knows_site("tako", "https://newm.mangahere.cc/manga/example/")
    assert not ExternalScraper._engine_knows_site("tako", "https://unlisted.example/manga/example/")


def test_zip_import_enables_tako_without_running_its_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_engines, "folder", lambda: tmp_path / "installed")
    archive = tmp_path / "engine.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", '{"manifest_version":3,"name":"__MSG_extName__"}')
        bundle.writestr("_locales/en/messages.json", '{"extName":{"message":"Tako Manga Downloader"}}')
        for name in ("background.js", "sidepanel.html", "offscreen.html"):
            bundle.writestr(name, "throw new Error('This must never execute');")
    assert custom_engines.add(str(archive)) == "tako"
    assert not custom_engines.folder().exists()


def test_zip_import_selects_one_python_engine_without_extracting_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_engines, "folder", lambda: tmp_path / "installed")
    archive = tmp_path / "engine.zip"
    engine = 'class Engine:\n    label = "Example"\n    def parse_book(self, url): pass\n    def parse_chapter(self, chapter, book): pass\n'
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("../../example.py", engine)
        bundle.writestr("../irrelevant.js", "ignored")
    assert custom_engines.add(str(archive)) == "custom:example"
    assert (custom_engines.folder() / "example.py").read_text() == engine
    assert not (tmp_path / "irrelevant.js").exists()
    with ZipFile(archive, "a") as bundle:
        bundle.writestr("another.py", engine)
    with pytest.raises(ValueError, match="exactly one"):
        custom_engines.add(str(archive))
