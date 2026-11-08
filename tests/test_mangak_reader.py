import json

from noveldb.core.engines.mangak import MangaKEngine


TITLE = "https://mangak.io/example-title"


def test_mangak_reads_the_full_feed_and_only_the_requested_chapter_images():
    from external_scraper import ExternalScraper
    from noveldb.core.catalog.sites import downloadable_page
    from noveldb.core.sources import detect_source

    def chapter(number):
        return {"url": f"/example-title/chapter-{number}", "slug": f"chapter-{number}",
                "name": f"Chapter {number}", "number": number}

    one, two, three = map(chapter, (1, 2.5, 3))
    asks = []

    def html(props):
        return '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": props}}) + "</script>"

    def fetch(url, referer="", headers=None):
        asks.append((url, referer))
        if url == TITLE:
            body = html({"initialManga": {"name": "Example Title", "slug": "example-title", "id": "abc123",
                                         "stats": {"chaptersCount": 3}, "chapters": [three], "firstChapter": one}})
        elif url == "https://api.mangak.io/titles/abc123/chapters":
            assert headers["Accept"] == "application/json"
            body = json.dumps({"data": {"chapters": [three, two, one, two, {"url": "/different-title/chapter-4"}]}})
        elif url == TITLE + "/chapter-1":
            body = html({"initialManga": {"slug": "example-title"},
                         "initialChapter": dict(one, images=["https://cdn.test/1.webp", "https://cdn.test/2.webp"])})
        else:
            raise AssertionError(url)
        return {"status": 200, "content": body.encode()}

    assert detect_source(TITLE) == "mangak"
    assert ExternalScraper._native_book_parser(ExternalScraper(), TITLE) is not None
    assert downloadable_page(TITLE, ["native"])
    assert not MangaKEngine.knows("https://mangak.io/search")
    engine = MangaKEngine(fetch)
    book = engine.parse_book(TITLE + "/chapter-1")
    assert [c["name"] for c in book["chapters"]] == ["Chapter 1", "Chapter 2.5", "Chapter 3"]
    first = engine.parse_chapter(book["chapters"][0], book)
    assert first["imageCount"] == 2 and first["imageReferer"] == TITLE + "/chapter-1"
    assert first["contentHtml"].index("1.webp") < first["contentHtml"].index("2.webp")
    assert asks == [(TITLE, ""), ("https://api.mangak.io/titles/abc123/chapters", TITLE),
                    (TITLE + "/chapter-1", TITLE)]
