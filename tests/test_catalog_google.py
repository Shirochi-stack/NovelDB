import threading

import pytest

from noveldb.core.catalog import generic_search as search, search_ranking


def response(html, status=200):
    return {"status": status, "text": html, "content": html.encode(), "headers": "", "url": "https://www.google.com/search"}


def test_google_reads_organic_links_redirects_and_excludes_navigation_and_ads():
    html = '''<a href="/url?q=https%3A%2F%2Fbooks.test%2Fbook%2Fshadow-slave"><h3>Shadow Slave</h3></a>
        <a href="https://google.com/search"><h3>Search</h3></a>
        <div data-text-ad="1"><a href="https://ad.test/book"><h3>Advert</h3></a></div>
        <a href="https://books.test/book/2"><h3>Another book</h3></a>
        <a href="https://books.test/book/2"><h3>Duplicate</h3></a>'''
    requested = []
    def fetch(url, **kwargs):
        requested.append(url)
        return response(html)
    hits = list(search.web_search('"Shadow Slave" site:books.test', fetch))
    assert [hit["url"] for hit in hits] == ["https://books.test/book/shadow-slave", "https://books.test/book/2"]
    assert len(requested) == 1 and requested[0].startswith("https://www.google.com/search?")


@pytest.mark.parametrize("html,status", [("detected unusual traffic", 200), ("/httpservice/retry/enablejs", 200), ("", 429)])
def test_google_challenges_are_reported_without_bogus_results(html, status):
    with pytest.raises(search.GoogleUnavailable):
        search.google_results("Title", lambda *args, **kwargs: response(html, status))


def test_dorks_quote_title_scope_sites_and_exclude_non_books(monkeypatch):
    monkeypatch.setattr(search_ranking, "profile", lambda: {"preferred": ["fast.test"], "measurements": {}})
    queries = search.google_dorks("Shadow Slave", "name", [{"site": "fast.test", "host": "fast.test"}])
    assert len(queries) == 2 and '"Shadow Slave"' in queries[0] and "site:fast.test" in queries[0]
    assert all("-inurl:chapter" in query and "-inurl:profile" in query for query in queries)
    assert not search.looks_like_book("https://books.test/profile/123", "Shadow Slave")
    assert not search.looks_like_book("https://books.test/wiki/shadow-slave", "Shadow Slave")


def test_automatic_search_uses_bounded_responsive_sources_and_rejects_partial_matches(monkeypatch):
    sites = [{"site": f"s{i}.test", "host": f"s{i}.test"} for i in range(200)]
    monkeypatch.setattr(search, "top_sites", lambda: sites)
    monkeypatch.setattr(search, "web_search", lambda *args: [])
    monkeypatch.setattr(search_ranking, "profile", lambda: {"preferred": [f"s{i}.test" for i in range(15)], "measurements": {}})
    asked = []
    class Sites:
        def search(self, home, title):
            asked.append(home)
            return [{"url": home + "book/1", "title": "Shadow Warrior", "cover": "cover"}]
    engine = search.GenericSearch(fetch=lambda *args, **kwargs: None, site_search=Sites())
    found = []
    engine.run("Shadow Slave", "name", found.extend, threading.Event(), deadline=1)
    assert len(asked) == search.STAGE_FIRST and not found
    assert all(int(home.split('s', 2)[-1].split('.')[0]) < 15 for home in asked)


def test_faster_failed_sources_do_not_rank_above_successful_ones(monkeypatch):
    import time
    monkeypatch.setattr(search_ranking, "profile", lambda: {"preferred": [], "measurements": {
        "failed.test": {"ms": 1, "ok": False, "at": time.time()},
        "fast.test": {"ms": 150, "ok": True, "at": time.time()},
        "slow.test": {"ms": 4000, "ok": True, "at": time.time()}}})
    sites = [{"host": host} for host in ("failed.test", "slow.test", "fast.test", "unknown.test")]
    assert [site["host"] for site in search_ranking.ranked(sites, automatic=True)] == ["fast.test", "slow.test"]


def test_index_sort_only_reads_the_index_and_keeps_other_work_running():
    from types import SimpleNamespace
    from noveldb.qt.catalog.generic import GenericCatalog, parse_message
    calls, replies = [], []
    class Index:
        def search_page(self, text, mode, page, limit, **kwargs):
            calls.append((text, kwargs))
            return [{"url": "https://books.test/book/1", "title": "Shadow Slave", "source": "noveldb"}], True
    engine = SimpleNamespace(noveldb=Index(), noveldb_sources=lambda: ["npia"])
    catalog = SimpleNamespace(_engine=lambda item: engine, _fetched=SimpleNamespace(emit=lambda *args: replies.append(args)))
    message = {"type": "genericIndexSort", "id": 9, "queries": [{"query": "Shadow Slave", "mode": "name", "sort": "likes"}]}
    checked = parse_message(message)
    assert checked is not None
    GenericCatalog._index_sort(catalog, checked)
    assert calls == [("Shadow Slave", {"sources": ["npia"], "sort": "likes"})]
    assert replies[0][0] == 9 and replies[0][1]["more"]
    assert replies[0][1]["cards"][0]["via"] == "name"
