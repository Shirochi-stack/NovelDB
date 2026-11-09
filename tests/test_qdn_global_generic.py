"""qdn Global downloads read the selected book's catalog, preserving its source order."""
import json
from pathlib import Path

import pytest

from noveldb.core.engines.generic import GenericEngine, find_chapters, qdn_global_catalog, soup_of
from noveldb.core.engines.scraper_mixin import generic_first

DOMAIN = "\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com"
BASE = "https://www." + DOMAIN
HOME = BASE + "/book/my-three-wives-are-beautiful-vampires._20553633305398405"
CATALOG = BASE + "/book/20553633305398405/catalog"
PROFILES = '<a href="https://m.' + DOMAIN + '/profile/4309108112">Victor_Weismann</a><a href="/profile/4312827604">Dav0_2138</a><a href="/profile/4316089147">DontCallMeSimp</a>'
OVERVIEW = '<h1>My Three Wives Are Beautiful Vampires.</h1>' + PROFILES
CATALOG_HTML = '''<script type="application/ld+json">{"@type":"WebPage","name":"Your Adventures - Fiction Stories","description":"Discover fictional stories and enjoy reading thousands of books online."}</script>
<meta name="description" content="Follow this book's adventures as its characters discover a mysterious new world.">
<h1>My Three Wives Are Beautiful Vampires.</h1>''' + PROFILES + '''
<a href="/book/title_20553633305398405/prologue_10">Read first</a>
<a href="/book/title_20553633305398405/latest_99">Latest Chapter</a>
<div class="volume-item"><ol>
<li><a title="Author notice" href="/book/title_20553633305398405/notice_55"><strong>Author notice</strong><small>3 years ago</small></a></li>
<li><a title="Chapter 01: Prologue" href="/book/title_20553633305398405/prologue_10"><strong>Chapter 01: Prologue</strong></a></li>
<li><a title="Chapter 02: Awakening" href="/book/title_20553633305398405/awakening_11"><strong>Chapter 02: Awakening</strong></a></li>
<li><a title="Chapter 02 duplicate" href="/book/20553633305398405/11">Duplicate</a></li>
<li><a title="Other book" href="/book/other_9999/chapter_12">Other book Chapter 1</a></li>
<li><a title="External link" href="https://unrelated.example/book/title_20553633305398405/no_13">Chapter 1</a></li>
</ol></div>
'''
EXPECTED = ["Author notice", "Chapter 01: Prologue", "Chapter 02: Awakening"]


def test_profile_links_are_never_chapters_and_catalog_order_is_preserved():
    assert find_chapters(soup_of(OVERVIEW), HOME) == []
    chapters = find_chapters(soup_of(CATALOG_HTML), CATALOG)
    assert [c["name"] for c in chapters] == EXPECTED
    assert all("/profile/" not in c["url"] for c in chapters)


@pytest.mark.parametrize("url", [HOME, CATALOG, HOME + "/prologue_10", HOME.replace("www.", "m.")])
def test_book_catalog_and_chapter_inputs_use_the_catalog(url):
    assert qdn_global_catalog(url) == CATALOG
    assert generic_first(url)
    seen = []
    def fetch(address, **options):
        seen.append(address)
        assert address == CATALOG
        return {"status": 200, "url": CATALOG, "content": CATALOG_HTML.encode(), "headers": ""}
    book = GenericEngine(fetch=fetch).parse_book(url)
    assert seen == [CATALOG]
    assert [c["name"] for c in book["chapters"]] == EXPECTED
    assert book["chapterCount"] == 3
    assert book["bookname"] == "My Three Wives Are Beautiful Vampires."
    assert book["introduction"].startswith("Follow this book's adventures")


@pytest.mark.parametrize("path", [
    "scripts/generic/generic-lib.js",
    "android/sources/src/main/resources/com/shirochi/npia/sources/reader/generic/generic-lib.js",
    "ios/NpiaKit/Sources/NpiaSources/Reader/Resources/generic/generic-lib.js",
])
def test_mobile_download_entry_point_reads_catalog_instead_of_profiles(path):
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as manager:
        browser = manager.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 412, "height": 900})
            requests = []
            def route(request):
                address = request.request.url
                requests.append(address)
                assert address in (HOME, CATALOG)
                request.fulfill(status=200, content_type="text/html", body=CATALOG_HTML if address == CATALOG else OVERVIEW)
            page.route("**/*", route)
            page.goto(HOME)
            page.evaluate(Path(path).read_text(encoding="utf-8"))
            assert page.evaluate("window.__GEN_lib.findChapters(document, location.href)") == []
            book = json.loads(page.evaluate("u => window.__GEN_parseBook(u)", HOME))
            assert [c["name"] for c in book["chapters"]] == EXPECTED
            assert book["chapterCount"] == 3
            assert book["bookname"] == "My Three Wives Are Beautiful Vampires."
            assert book["introduction"].startswith("Follow this book's adventures")
            assert CATALOG in requests
        finally:
            browser.close()
