"""The Catalog's generic search: site search discovery, result cards, the search run and page messages."""
import json
import threading
from pathlib import Path

from noveldb.core.catalog import generic_search, site_search
from noveldb.core.catalog.generic_search import GenericSearch, looks_like_book, top_sites
from noveldb.core.catalog.site_search import SiteSearch, extract_cards, relevant
from noveldb.core.catalog.urls import is_generic_catalog, valid_catalog_url
from noveldb.core.settings.model import GENERIC_CATALOG_URL

HOME = """<html><head>
<link rel="search" type="application/opensearchdescription+xml" href="/opensearch.xml"></head><body>
<form action="/login" method="post"><input name="email"><input type="password" name="pass"></form>
<form action="/search" class="search-form"><input type="text" name="keyword" placeholder="Search"></form>
<a href="/genre/fantasy">Fantasy (120)</a><a href="/genre/romance">Romance</a>
</body></html>"""
OPENSEARCH = """<?xml version="1.0"?><OpenSearchDescription>
<Url type="text/html" template="https://books.example/find?q={searchTerms}&amp;page={startPage?}"/>
</OpenSearchDescription>"""
RESULTS = """<html><body><header><a href="/book/top-pick">Top pick</a></header>
<div class="list">
  <div class="item"><a href="/book/dragon-king"><img data-src="/c/dragon.jpg" alt="Dragon King"></a>
       <h3><a href="/book/dragon-king">Dragon King Returns</a></h3><p>A king returns to claim his throne.</p></div>
  <div class="item"><a href="/book/dragon-girl"><img src="/c/girl.jpg" alt=""></a>
       <h3><a href="/book/dragon-girl">The Dragon Girl</a></h3><p>She keeps a dragon in her garden.</p></div>
  <div class="item"><a href="/book/sea-wolf"><img src="/c/wolf.jpg" alt=""></a>
       <h3><a href="/book/sea-wolf">Sea Wolf</a></h3></div>
</div>
<a href="/book/dragon-king/chapter-1">Chapter 1</a>
</body></html>"""


def page(html, url, status=200):
    return {"status": status, "url": url, "content": html.encode("utf-8"), "text": html, "headers": ""}


def test_the_generic_catalog_address_is_accepted_and_kept():
    assert is_generic_catalog(GENERIC_CATALOG_URL)
    assert valid_catalog_url(GENERIC_CATALOG_URL) == GENERIC_CATALOG_URL
    from noveldb.core.settings.desktop_config import import_config

    assert import_config({"catalog_url": GENERIC_CATALOG_URL}).catalog.url == GENERIC_CATALOG_URL


def test_the_site_list_has_every_site_the_engines_know():
    sites = top_sites()
    # Every site an engine knows (well over a thousand), the most visited first, and the sites added by
    # hand (build_generic_sites.EXTRA_SITES); a search asks all of them.
    assert len(sites) > 1000
    assert all(site["searched"] for site in sites)
    bookkor = next(site for site in sites if site["site"] == "bookkor.com")
    assert bookkor["searched"] and "009.bookkor.com" in bookkor["hosts"]
    assert all(site["host"] and site["search"] in ("lnreader", "lncrawl", "web") for site in sites)
    assert sites == sorted(sites, key=lambda site: site["rank"])
    assert len({site["site"] for site in sites}) == len(sites)
    assert not any(site["site"] in ("google.com", "reddit.com", "blogspot.com") for site in sites)
    # A search asks the searched sites; a book from any listed site is known.
    engine = GenericSearch(fetch=lambda *a, **k: None)
    assert len(engine.sites) == len(sites)
    assert engine.site_for("https://" + sites[-1]["host"] + "/x") is not None


def test_book_pages_are_told_apart_from_chapters_and_lists():
    assert looks_like_book("https://a.example/novel/dragon-king")
    assert not looks_like_book("https://a.example/novel/dragon-king/chapter-12")
    assert not looks_like_book("https://a.example/genre/fantasy")
    assert not looks_like_book("https://a.example/")


def test_cards_are_read_from_a_results_page():
    cards = extract_cards(RESULTS, "https://books.example/find?q=dragon")
    by_title = {card["title"]: card for card in cards}
    assert set(by_title) == {"Dragon King Returns", "The Dragon Girl", "Sea Wolf"}
    assert by_title["Dragon King Returns"]["cover"] == "https://books.example/c/dragon.jpg"
    assert "claim his throne" in by_title["Dragon King Returns"]["snippet"]
    assert relevant("The Dragon Girl", "dragon") and not relevant("Sea Wolf", "dragon")


def test_site_search_uses_opensearch_then_remembers_it(tmp_path):
    asked = []
    pages = {"https://books.example/": HOME, "https://books.example/opensearch.xml": OPENSEARCH,
             "https://books.example/find?q=dragon": RESULTS}

    def fetch(url, **_options):
        asked.append(url)
        return page(pages[url], url) if url in pages else page("", url, 404)

    search = SiteSearch(fetch, cache_file=tmp_path / "plans.json")
    cards = search.search("https://books.example/", "dragon")
    assert [card["title"] for card in cards] == ["Dragon King Returns", "The Dragon Girl"]
    plan = json.loads((tmp_path / "plans.json").read_text(encoding="utf-8"))["https://books.example/"]
    assert plan["ways"][0]["template"] == "https://books.example/find?q={q}"
    assert plan["ways"][1]["field"] == "keyword"  # the search form, not the login form
    assert plan["tags"]["fantasy"] == "https://books.example/genre/fantasy"


def test_tags_use_the_sites_genre_pages():
    pages = {"https://books.example/": HOME, "https://books.example/genre/fantasy": RESULTS}
    search = SiteSearch(lambda url, **_o: page(pages[url], url) if url in pages else page("", url, 404))
    assert len(search.tag("https://books.example/", "Fantasy")) == 3


def test_a_search_streams_cards_from_every_site_once():
    class FakeSites:
        def search(self, home, query):
            return [{"url": home + "book/dragon-king", "title": "Dragon King", "cover": home + "c.jpg", "snippet": ""}]

        def tag(self, home, tag):
            return []

    sites = [{"site": "a.example", "host": "a.example", "hosts": ["a.example"], "search": "web", "comic": False},
             {"site": "b.example", "host": "b.example", "hosts": ["b.example"], "search": "web", "comic": True}]
    batches = []
    summary = {}
    engine = GenericSearch(fetch=lambda url, **_o: None, sites=sites, site_search=FakeSites())
    engine.run("dragon", "name", batches.append, threading.Event(), summary.update)
    cards = [card for batch in batches for card in batch]
    assert sorted(card["site"] for card in cards) == ["a.example", "b.example"]
    assert next(card for card in cards if card["site"] == "b.example")["comic"] is True
    assert summary["results"] == 2


def test_page_messages_are_checked():
    import pytest

    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog import generic

    assert generic.parse_message({"type": "genericSearch", "query": "dragon", "mode": "tag"})["mode"] == "tag"
    assert generic.parse_message({"type": "genericSearch", "query": "  "}) is None
    assert generic.parse_message({"type": "genericFetch", "id": 1, "request": {"url": "file:///etc/passwd"}}) is None
    assert generic.parse_message({"type": "genericImage", "id": 2, "url": "https://a.example/c.jpg"}) is not None
    assert generic.parse_message({"type": "somethingElse"}) is None
    assert generic.parse_message({"type": "genericInstant", "id": 1, "key": "b/0", "token": "mesugaki", "prefix": True})
    assert generic.parse_message({"type": "genericInstant", "id": 1, "key": "../../config"}) is None
    assert generic.parse_message({"type": "genericInstant", "id": 1, "key": "c/2", "rows": [-1]}) is None
    assert generic.parse_message({"type": "genericSiteQuery", "id": 1, "web": True, "query": '"Dragon" site:novels.test'})
    assert generic.parse_message({"type": "genericSiteQuery", "id": 2, "host": "novels.test", "query": "Dragon"})
    assert generic.parse_message({"type": "genericSiteQuery", "id": 3, "url": "https://novels.test/book/dragon"})
    assert generic.parse_message({"type": "genericSiteQuery", "id": 4, "url": "file:///book"}) is None
    assert generic.parse_message({"type": "genericSiteQuery", "id": 5, "host": "novels.test", "query": ""}) is None
    assert generic.parse_message({"type": "genericSearch", "queries": [{"query": "Dragon", "full": True}]})["queries"][0]["full"]


def test_empty_search_pages_are_recognised():
    from noveldb.core.catalog.generic_search import is_no_results

    assert is_no_results("リクエスト", "178019の検索結果 キーワードに該当する商品は見つかりませんでした。")
    assert is_no_results("No results found for dragon")
    assert not is_no_results("Lord of the Mysteries", "A young man wakes up in a foreign world.")


def test_local_catalogue_replies_bypass_a_saturated_network_queue(monkeypatch):
    import types
    import pytest
    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog import generic

    network = generic._Workers(1)
    monkeypatch.setattr(generic, "_PAGE_WORKERS", network)
    occupied, release, answered = threading.Event(), threading.Event(), threading.Event()
    received = []

    def occupy():
        occupied.set()
        release.wait(5)

    def emit(identity, reply):
        received.append(reply)
        answered.set()

    bridge = types.SimpleNamespace(_fetched=types.SimpleNamespace(emit=emit))
    bridge._background = types.MethodType(generic.GenericCatalog._background, bridge)
    bridge._instant_reply = types.MethodType(generic.GenericCatalog._instant_reply, bridge)
    network.submit(occupy)
    try:
        assert occupied.wait(1)
        generic.GenericCatalog.handle(bridge, {"type": "genericInstant", "id": 1, "key": "manifest"})
        assert answered.wait(1), "Local results waited behind network requests"
        assert received[0]["data"]["books"] > 1_000_000
    finally:
        release.set()


def test_book_addresses_are_learned_for_the_id_search(tmp_path):
    from noveldb.core.catalog import generic_search

    generic_search.learn_template("https://books.example/novel/178019")
    generic_search.learn_template("https://books.example/genre/12345")  # a list, not a book
    generic_search.save_learned(tmp_path / "learned.json")
    saved = json.loads((tmp_path / "learned.json").read_text(encoding="utf-8"))
    assert saved["books.example"] == "https://books.example/novel/{id}"
    assert generic_search.ID_QUERY.match("178019") and generic_search.ID_QUERY.match("n1234ab")
    assert not generic_search.ID_QUERY.match("solo leveling")
    assert all("{id}" in entry["template"] for entry in generic_search.id_templates())


def test_id_and_top_list_messages_are_checked():
    import pytest

    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog import generic

    message = generic.parse_message({"type": "genericSearch", "queries": [{"query": "178019", "mode": "id"}]})
    assert message["queries"] == [{"query": "178019", "mode": "id"}]
    assert generic.parse_message({"type": "genericTop", "id": 1, "kind": "manga"}) is not None
    assert generic.parse_message({"type": "genericTop", "id": 1, "kind": "bogus"}) is None
    assert generic.parse_message({"type": "genericChapter", "id": 3, "url": "https://a.example/b", "index": -1}) is None
    recovered = generic.parse_message({"type": "genericChapter", "id": 3, "url": "https://a.example/b", "index": 0,
                                       "recovery": "cloudflare"})
    assert recovered["recovery"] == "cloudflare"
    assert generic.parse_message({"type": "genericChapter", "id": 3, "url": "https://a.example/b", "index": 0,
                                  "recovery": "unknown"}) is None
    assert generic.download_icon().startswith("data:image/")
    switched = generic.parse_message({"type": "genericSearch", "queries": [
        {"query": "TS", "mode": "tag", "noveldb": False, "deep": False}, {"query": "x", "mode": "name", "deep": "no"}]})
    assert switched["queries"] == [{"query": "TS", "mode": "tag", "noveldb": False, "deep": False}, {"query": "x", "mode": "name"}]


def test_the_settings_searches_switch_noveldb_and_the_deep_search_off(monkeypatch):
    import threading
    from noveldb.core.catalog import generic_search, source_search

    asked = []
    monkeypatch.setattr(source_search, "search", lambda source, mode, name, page, fetch, sort="": asked.append(source) or ([], False))
    engine = generic_search.GenericSearch(fetch=lambda *a, **k: None, sites=[])
    engine.deep = False
    engine.run("Gender Bender", "tag", lambda cards: None, threading.Event(), deadline=5)
    engine.run("R19", "tag", lambda cards: None, threading.Event(), deadline=5)
    assert asked == []
    engine.deep = True
    engine.run("R19", "tag", lambda cards: None, threading.Event(), deadline=5)
    assert asked == sorted(source_search.RATED)


def test_the_offline_cache_keeps_entries_within_its_limit(tmp_path):
    import os
    import time

    from noveldb.core.catalog.offline_cache import OfflineCache

    limit = [1]
    cache = OfflineCache(lambda: limit[0], tmp_path / "cache")
    cache.put_json("details", "https://a.example/book", {"title": "A"})
    assert cache.get_json("details", "https://a.example/book")["title"] == "A"
    assert cache.get_json("details", "https://a.example/book", max_age=-1) is None  # too old
    for n in range(3):
        cache.put("covers", f"cover{n}", b"x" * 400_000)
        path = cache._path("covers", f"cover{n}")
        os.utime(path, (time.time() - 100 + n, time.time() - 100 + n))
    cache.trim()
    assert cache.size() <= 1024 * 1024
    assert cache.get("covers", "cover2") is not None and cache.get("covers", "cover0") is None  # oldest went first
    limit[0] = 0
    assert cache.get("covers", "cover2") is None  # off


def test_library_epubs_open_in_the_reader(tmp_path):
    import zipfile

    from noveldb.core.catalog.live_reader import LiveReader

    book = tmp_path / "Book.epub"
    with zipfile.ZipFile(book, "w") as epub:
        epub.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                                                '</rootfiles></container>')
        epub.writestr("OEBPS/content.opf", '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata>'
                                           '<dc:title>Book</dc:title></metadata><manifest>'
                                           '<item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/>'
                                           '<item id="i" href="Images/a.png" media-type="image/png"/></manifest>'
                                           '<spine><itemref idref="c1"/></spine></package>')
        epub.writestr("OEBPS/Text/c1.xhtml", '<html><head><title>One</title></head><body><p>Hello</p>'
                                             '<img src="../Images/a.png"/></body></html>')
        epub.writestr("OEBPS/Images/a.png", b"\x89PNG\r\n")
    reader = LiveReader(fetch=lambda *_a, **_k: None)
    contents = reader.book(book.as_uri())
    assert contents["title"] == "Book" and contents["chapters"][0]["name"] == "c1"
    chapter = reader.chapter(book.as_uri(), 0)
    assert "<p>Hello</p>" in chapter["html"] and 'src="data:image/png;base64,' in chapter["html"]


def test_a_results_pages_next_page_is_found():
    from noveldb.core.catalog.site_search import next_page

    assert next_page('<link rel="next" href="/find?q=a&page=2">', "https://x.example/find?q=a") == \
        "https://x.example/find?q=a&page=2"
    assert next_page('<div class="pagination"><a href="/manga/page/3/">Next ›</a></div>', "https://x.example/manga/page/2/") == \
        "https://x.example/manga/page/3/"
    assert next_page("<p>no links</p>", "https://x.example/popular?page=4") == "https://x.example/popular?page=5"
    assert next_page('<a href="/popular/page/2/">2</a>', "https://x.example/popular") == "https://x.example/popular/page/2/"
    assert next_page("<p>no links</p>", "https://x.example/popular") == ""
    assert next_page('<a href="https://other.example/next">Next</a>', "https://x.example/a") == ""  # another site


def test_more_reads_the_next_results_page_of_a_search():
    first = RESULTS.replace("</body>", '<a rel="next" href="/find?q=dragon&page=2">Next</a></body>')
    second = RESULTS.replace("dragon-king", "dragon-queen").replace("Dragon King Returns", "Dragon Queen Rises") \
        .replace("dragon-girl", "dragon-boy").replace("The Dragon Girl", "The Dragon Boy")
    pages = {"https://books.example/": HOME, "https://books.example/opensearch.xml": OPENSEARCH,
             "https://books.example/find?q=dragon": first, "https://books.example/find?q=dragon&page=2": second}
    search = SiteSearch(lambda url, **_o: page(pages[url], url) if url in pages else page("", url, 404))
    home = "https://books.example/"
    assert len(search.search(home, "dragon")) == 2
    assert search.has_more(home, "search", "dragon")
    assert [card["title"] for card in search.more(home, "search", "dragon")] == ["Dragon Queen Rises", "The Dragon Boy"]
    # No next link on page 2: page 3 is guessed from its address, finds nothing, and the list ends there.
    assert search.more(home, "search", "dragon") == []
    assert not search.has_more(home, "search", "dragon")


def test_more_messages_are_checked():
    from noveldb.qt.catalog.generic import parse_message

    assert parse_message({"type": "genericMore", "id": 2, "page": 1, "queries": [{"query": "dragon", "mode": "name"}]})
    assert parse_message({"type": "genericMore", "id": 2, "page": 0, "queries": [{"query": "dragon"}]}) is None
    assert parse_message({"type": "genericTopMore", "id": 2})
    assert parse_message({"type": "genericFullscreen", "on": True})


def test_downloader_can_take_over_on_the_first_failed_chapter():
    """LNReader may read the TOC before Cloudflare blocks a chapter, so no downloader book is cached yet."""
    import pytest

    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog.generic import GenericCatalog

    class Immediate:
        def __init__(self, value):
            self.value = value

        def result(self, timeout):
            assert timeout == 240
            return self.value

    class Scraper:
        _book_url = None

        def __init__(self):
            self.book_calls = 0

        def parse_book(self, url):
            self.book_calls += 1
            self._book_url = url
            return {"bookname": "Cloudflare Book", "chapters": [{"name": "Chapter 1"}]}

        def parse_chapter(self, index, chapter, interval=0):
            assert index == 0 and chapter["name"] == "Chapter 1" and interval == 0
            return {"chapterName": "Chapter 1", "contentHtml": "<p>Recovered</p>"}

    scraper = Scraper()
    catalog = type("Catalog", (), {})()
    catalog.downloader = lambda operation: Immediate(operation(scraper))
    catalog._downloader_books = {}
    catalog._note = lambda _text: None
    catalog._apply_engine_settings = lambda _scraper: None

    chapter = GenericCatalog._downloader_read(catalog, "https://books.example/book/1", 0)

    assert scraper.book_calls == 1
    assert chapter["html"] == "<p>Recovered</p>"
    assert catalog._downloader_books["https://books.example/book/1"]["chapters"][0]["name"] == "Chapter 1"


def test_catalog_reader_reuses_the_downloader_book_already_opened_by_download():
    import pytest

    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog.generic import GenericCatalog

    url = "https://fuck\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/example"

    class Immediate:
        def __init__(self, value):
            self.value = value

        def result(self, timeout):
            assert timeout == 240
            return self.value

    class Scraper:
        _book_url = url
        _book_data = {"bookname": "Already open", "chapters": [{"name": "Chapter 1"}]}

        def parse_book(self, _url):
            raise AssertionError("Read Now should reuse the downloader's current book")

        def parse_chapter(self, index, chapter, interval=0):
            assert index == 0 and interval == 0
            return {"chapterName": chapter["name"], "contentHtml": "<p>Reused</p>"}

    scraper = Scraper()
    catalog = type("Catalog", (), {})()
    catalog.downloader = lambda operation: Immediate(operation(scraper))
    catalog._downloader_books = {}
    catalog._note = lambda _text: None
    catalog._apply_engine_settings = lambda _scraper: None

    chapter = GenericCatalog._downloader_read(catalog, url, 0)

    assert chapter["html"] == "<p>Reused</p>"


def test_cloudflare_recovery_skips_the_quick_reader():
    import pytest

    pytest.importorskip("PySide6.QtWebEngineCore")
    from noveldb.qt.catalog.generic import GenericCatalog

    emitted = []
    catalog = type("Catalog", (), {})()
    catalog.downloader = object()
    catalog._fetched = type("Signal", (), {"emit": lambda _self, request_id, value: emitted.append((request_id, value))})()
    catalog._reader_answer = lambda *args: GenericCatalog._reader_answer(catalog, *args)
    catalog._note = lambda _text: None
    catalog._downloader_read = lambda url, index: {"name": "Recovered", "html": "<p>Browser result</p>"}

    def quick_reader(_url, _index):
        raise AssertionError("the quick reader must not run again after a Cloudflare response")

    GenericCatalog._read(catalog, 12, quick_reader, "https://books.example/book/1", 0, force_downloader=True)

    assert emitted == [(12, {"name": "Recovered", "html": "<p>Browser result</p>"})]


def test_catalog_reader_retries_a_failed_lnreader_chapter_through_the_app():
    source = (Path(__file__).resolve().parents[1] / "noveldb" / "qt" / "catalog" / "generic" / "generic.js") \
        .read_text(encoding="utf-8")

    chapter = source[source.index("function loadChapter(index)"):source.index("function openChapter(index, at)")]
    assert "if (result && !result.error) return result" in chapter
    assert "cloudflare ? 'cloudflare' : null" in chapter
    assert "Cloudflare detected; switching to the downloader browser" in chapter
    assert r"fuck\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061\.com" in chapter
    assert "return appChapter('cloudflare')" in chapter
    assert "hidden downloader browser…', 45" not in chapter


def test_a_search_of_picked_sites_asks_only_them(monkeypatch):
    import threading
    from noveldb.core.catalog import generic_search, source_search

    asked_sources, asked_sites, web = [], [], []
    monkeypatch.setattr(source_search, "search", lambda source, mode, name, page, fetch, sort="": asked_sources.append(source) or ([], False))
    monkeypatch.setattr(generic_search, "web_search", lambda query, fetch: web.append(query) or [])
    npia_site = source_search.source_sites()["npia"]

    class Sites:
        def tag(self, home, text):
            asked_sites.append(home)
            return []

    sites = [{"site": npia_site, "host": npia_site}, {"site": "example.org", "host": "www.example.org"}]
    engine = generic_search.GenericSearch(fetch=lambda *a, **k: None, sites=sites, site_search=Sites())
    engine.only = {npia_site}
    engine.run("Gender Bender", "tag", lambda cards: None, threading.Event(), deadline=5)
    assert asked_sites == [f"https://{npia_site}/"] and web == []
    assert set(asked_sources) <= {"npia"}
    assert engine.noveldb_sources() == ["npia"]
    engine.only = {npia_site, "example.org"}  # a site NovelDB has no source for: NovelDB's books from all
    assert engine.noveldb_sources() is None


def test_a_search_asks_its_sites_in_stages_the_web_search_matches_first(monkeypatch):
    import threading
    from noveldb.core.catalog import generic_search

    sites = [{"site": f"s{n}.example", "host": f"s{n}.example"} for n in range(170)]
    monkeypatch.setattr(generic_search, "web_search",
                        lambda query, fetch: [{"url": "https://s169.example/book/1", "title": "A Title Returns", "snippet": ""}])
    asked = []

    class Sites:
        def search(self, home, text):
            import time

            time.sleep(0.02)  # a site takes a moment: the web search's answer comes while the stage runs
            asked.append(home)
            return []

        def has_more(self, home, kind, text):
            return False

    engine = generic_search.GenericSearch(fetch=lambda *a, **k: None, sites=sites, site_search=Sites(), workers=4)
    engine.run("A Title", "name", lambda cards: None, threading.Event(), deadline=20)
    assert len(asked) == generic_search.STAGE_FIRST
    assert "https://s169.example/" in asked  # the web search's site, though last in the list
    asked.clear()
    states = []
    engine.more("A Title", "name", 1, lambda cards: None, threading.Event(), states.append, ndb_page=0)
    assert len(asked) == generic_search.STAGE_MORE and states[-1]["more"]
    asked.clear()
    page_number = 2
    while states[-1]["more"]:
        engine.more("A Title", "name", page_number, lambda cards: None, threading.Event(), states.append, ndb_page=0)
        page_number += 1
    assert len(asked) == 170 - generic_search.STAGE_FIRST - generic_search.STAGE_MORE and not states[-1]["more"]


def test_a_title_search_ends_soon_after_a_book_of_exactly_that_title(monkeypatch):
    import threading
    import time
    from noveldb.core.catalog import generic_search

    title = "萌妹精灵与她的幸福生活"
    monkeypatch.setattr(generic_search, "web_search", lambda query, fetch: [])
    monkeypatch.setattr(generic_search.GenericSearch, "_detail_and_emit", lambda *a, **k: None)
    sites = [{"site": f"s{n}.example", "host": f"s{n}.example"} for n in range(40)]

    class Sites:
        def search(self, home, text):
            if home == "https://s0.example/":
                return [{"url": "https://s0.example/book/1", "title": title + " ", "cover": "c"}]
            time.sleep(8)  # the other sites are slow
            return []

    engine = generic_search.GenericSearch(fetch=lambda *a, **k: None, sites=sites, site_search=Sites(), workers=8)
    found, summaries = [], []
    started = time.monotonic()
    engine.run(title, "name", found.extend, threading.Event(), summaries.append, deadline=45)
    assert time.monotonic() - started < generic_search.EXACT_GRACE + 3
    assert [c["url"] for c in found] == ["https://s0.example/book/1"] and not summaries[-1]["timed_out"]


def test_closing_the_reader_stops_the_downloader_chapter_and_skips_queued_reads():
    from noveldb.qt.catalog.generic import GenericCatalog
    from noveldb.qt.catalog.reader_media import ReaderSession

    class Immediate:
        def __init__(self, operation):
            self.operation = operation

        def result(self, timeout):
            return self.operation(scraper)

    class Scraper:
        _book_url = "https://books.example/book/1"
        _stop_requested = False

        def parse_chapter(self, index, chapter, interval=0):
            raise AssertionError("a read queued for a closed book is not started")

    scraper = Scraper()
    catalog = type("Catalog", (), {})()
    catalog.reader_session = ReaderSession()
    catalog._reader_visible = True
    catalog._background = lambda work: work()
    catalog._downloader_books = {"https://books.example/book/1": {"chapters": [{"name": "Chapter 1"}]}}
    catalog._note = lambda _text: None
    catalog._apply_engine_settings = lambda _scraper: None
    queued = []
    catalog.downloader = lambda operation: queued.append(operation) or Immediate(operation)

    # The reader closes while the chapter waits in the downloader's queue.
    catalog._reader_scraper = scraper
    original = catalog.downloader
    catalog.downloader = lambda operation: (GenericCatalog.stop_reader(catalog), original(operation))[1]
    try:
        GenericCatalog._downloader_read(catalog, "https://books.example/book/1", 0)
    except RuntimeError as e:
        assert "closed" in str(e)
    else:
        raise AssertionError("the stale read went ahead")
    assert scraper._stop_requested is True


def test_a_title_remembered_a_little_wrong_is_close_and_others_are_not():
    from noveldb.core.catalog.generic_search import similar_title

    typed = "Move aside ill become a magical girl"
    assert similar_title(typed, "Move Aside, I'll Be the Magical Girl!") >= 0.6
    assert similar_title(typed, "MOVE TV") < 0.6
    assert similar_title(typed, "Magical Girl Raising Project") < 0.6
