import importlib
import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import downloader_core as core
import npia_author_catalog as catalog


@pytest.fixture(autouse=True)
def isolate_public_intro(monkeypatch):
    monkeypatch.setattr(core, "discover_intro", lambda *a, **kw: [])


def novel(nid, age=19):
    return dict(novel_no=nid, mem_no=42, novel_age=age, novel_name=f"Title {nid}",
                writer_nick="Writer", novel_story="Synopsis", novel_genre='["Tag"]')


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append(kwargs["data"])
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=result))


def page(rows, total=None):
    return dict(novel=rows, allCount=len(rows) if total is None else total)


def test_pagination_and_failure_preserve_completed_page():
    first = [novel(n) for n in range(1, 21)]
    session = Session([page(first, 21), TimeoutError("timeout")])
    pages = catalog.iter_author_pages(session, 42, delay=0)
    assert next(pages) == (1, first, True)
    with pytest.raises(TimeoutError):
        next(pages)
    assert [p["paging[curPage]"] for p in session.posts] == [1, 2]


def test_repeated_page_and_author_mismatch_are_rejected():
    first = [novel(n) for n in range(20)]
    pages = catalog.iter_author_pages(Session([page(first, 41), page(first, 41)]), 42, delay=0)
    next(pages)
    with pytest.raises(ValueError, match="repeated"):
        next(pages)
    with pytest.raises(ValueError, match="identity"):
        list(catalog.iter_author_pages(Session([page([novel(1)])]), 43, delay=0))


def test_hundred_row_batches_collect_106_records_in_two_calls():
    first = [novel(n) for n in range(1, 101)]
    last = [novel(n) for n in range(101, 107)]
    session = Session([page(first, 106), page(last, 106)])
    pages = list(catalog.iter_author_pages(session, 42, page_size=100, delay=0))
    assert sum(len(rows) for _, rows, _ in pages) == 106
    assert len(session.posts) == 2
    assert [p['paging[curPage]'] for p in session.posts] == [1, 2]
    assert all(p['paging[rowCount]'] == 100 for p in session.posts)


def test_larger_batch_migration_resumes_before_old_offset_without_skipping():
    scraper = scraper_module()
    session = Session([page([novel(n) for n in range(101, 201)], 220)])
    state = {'author_pending': {'42': 8}, 'author_page_size': 20}
    scraper.enrich_author_catalogs(session, {}, {}, state, page_limit=1, page_size=100,
                                   delay=0, now=1_000_000)
    assert session.posts[0]['paging[curPage]'] == 2
    assert state['author_pending']['42'] == 3 and state['author_page_size'] == 100


def test_r19_targets_precede_general_queue_and_reserve_general_exploration():
    scraper = scraper_module()
    authors = {str(n): {'name': f'Author {n}', 'names': [f'Author {n}'], 'novel_ids': []}
               for n in range(1, 8)}
    existing = [[n, 'Title', f'Author {n}'] + [0]*8 + [19] for n in range(2, 8)]
    calls = []
    def post(url, **kwargs):
        owner = kwargs['data']['mem_no'];calls.append(owner)
        row = dict(novel_no=int(owner), mem_no=int(owner), novel_age=19, novel_name='Title')
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([row])))
    state = {'author_pending': {str(n): 1 for n in range(1, 8)}}
    scraper.enrich_author_catalogs(SimpleNamespace(post=post), {}, {}, state,
                                   registry={'authors': authors}, existing=existing,
                                   page_limit=5, page_size=100, delay=0, now=1_000_000)
    assert calls == ['2', '3', '4', '5', '1']
    assert state['author_last_run']['targeted_pages'] == 4
    assert state['author_last_run']['r19_unique'] == 5
    assert state['author_last_run']['r19_new'] == 1


def test_cancellation_during_delay_makes_no_request():
    session = Session([])
    flag = [False]
    assert list(catalog.iter_author_pages(session, 42, stopped=lambda: flag[0],
                                          sleep=lambda _: flag.__setitem__(0, True))) == []
    assert not session.posts


@pytest.mark.parametrize("age,expected", [("", {"1", "2", "3"}), ("19", {"2"}), ("15", {"1", "3"})])
def test_desktop_all_ids_checks_each_author_once_and_filters_results(monkeypatch, age, expected):
    session = Session([page([novel(1, 0), novel(2, 19), novel(3, 15)])])
    searches = []

    def search(url, **kwargs):
        searches.append(kwargs["params"])
        return SimpleNamespace(status_code=200, text="JSON", json=lambda: dict(status=200, list=[novel(1, 0)]))

    session.get = search
    downloader = core.DownloaderCore(SimpleNamespace(session=session), lambda _: None)
    monkeypatch.setattr(core, "ALL_SEARCH_TERMS", ["first", "second"])
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    original = catalog.iter_author_pages
    monkeypatch.setattr(core, "iter_author_pages", lambda *a, **kw: original(*a, **kw, sleep=lambda _: None))
    result = downloader.fetch_all_novels(delay=0, max_queries=2, age_filter=age, threads=2)
    assert set(result) == expected
    assert len(session.posts) == 1
    assert all(p["rows"] == 15000 for p in searches)
    assert all(p["novel_age"] == ("" if age == "19" else age) for p in searches)


def scraper_module():
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, "stdout", Output()):
        return importlib.import_module("scripts.rescrape_npia_noauth")


def test_metadata_author_checkpoint_resume_and_sparse_merge():
    scraper = scraper_module()
    session = Session([page([novel(n) for n in range(1, 21)], 21)])
    fresh = {"1": dict(id=1, author_id="42", cover="saved-cover", updated="saved-date")}
    state = {}
    scraper.enrich_author_catalogs(session, fresh, {}, state, page_limit=1, delay=0, now=1_000_000)
    assert state["author_pending"] == {"42": 2}
    assert fresh["1"]["cover"] == "saved-cover" and fresh["1"]["updated"] == "saved-date"
    assert fresh["2"]["tags"] == ["Tag"] and fresh["2"]["age"] == 19
    second = Session([page([novel(21)], 21)])
    scraper.enrich_author_catalogs(second, fresh, {}, state, page_limit=1, delay=0, now=1_000_001)
    assert second.posts[0]["paging[curPage]"] == 2
    assert not state["author_pending"] and state["author_checked"]["42"] == 1_000_001
    assert "21" in fresh


def test_metadata_failed_author_keeps_cursor_and_existing_metadata():
    scraper = scraper_module()
    fresh = {"1": dict(id=1, author_id="42", synopsis="Saved")}
    state = {"author_pending": {"42": 3}}
    scraper.enrich_author_catalogs(Session([TimeoutError("timeout")]), fresh, {}, state,
                                   page_limit=1, delay=0, now=1_000_000)
    assert state["author_pending"] == {"42": 3}
    assert fresh["1"]["synopsis"] == "Saved"
    assert state["author_last_run"]["failed"] == 1


def test_desktop_cancel_retains_completed_author_page_and_stops(monkeypatch):
    session = Session([page([novel(n) for n in range(1, 21)], 21)])
    downloader = core.DownloaderCore(SimpleNamespace(session=session), lambda _: None)
    session.get = lambda *a, **kw: SimpleNamespace(status_code=200, text="JSON", json=lambda: dict(status=200, list=[novel(1)]))
    post = session.post
    def cancel_after_response(*a, **kw):
        response = post(*a, **kw)
        downloader.stop_signal = True
        return response
    session.post = cancel_after_response
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    original = catalog.iter_author_pages
    monkeypatch.setattr(core, "iter_author_pages", lambda *a, **kw: original(*a, **kw, sleep=lambda _: None))
    assert set(downloader.fetch_all_novels(delay=0, max_queries=1)) == {str(n) for n in range(1, 21)}
    assert len(session.posts) == 1


def test_metadata_partial_author_page_preserves_compact_metadata():
    scraper = scraper_module()
    item = novel(1)
    fresh = {"1": {"id": 1, "author_id": "42"}}
    compact = [[1, "Old", "Writer", "/saved-cover", ["Tag"], 100, 20, 8, 1, "saved-date", 0, 19]]
    scraper.enrich_author_catalogs(Session([page([item])]), fresh, {}, {}, page_limit=1,
                                   delay=0, now=1_000_000, existing=compact)
    assert fresh["1"]["updated"] == "saved-date" and fresh["1"]["complete"] == 1
    assert fresh["1"]["views"] == 100 and fresh["1"]["cover"] == "/saved-cover"
