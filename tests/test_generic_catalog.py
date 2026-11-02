"""The Catalog's generic search: site search discovery, result cards, the search run and page messages."""
import json
import threading

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


def test_the_site_list_has_the_top_100_book_sites():
    sites = top_sites()
    assert len(sites) == 100
    assert all(site["host"] and site["search"] in ("lnreader", "lncrawl", "web") for site in sites)
    assert sites == sorted(sites, key=lambda site: site["rank"])
    assert not any(site["site"] in ("google.com", "reddit.com", "blogspot.com") for site in sites)


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


def test_empty_search_pages_are_recognised():
    from noveldb.core.catalog.generic_search import is_no_results

    assert is_no_results("リクエスト", "178019の検索結果 キーワードに該当する商品は見つかりませんでした。")
    assert is_no_results("No results found for dragon")
    assert not is_no_results("Lord of the Mysteries", "A young man wakes up in a foreign world.")


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
    assert generic.download_icon().startswith("data:image/")
