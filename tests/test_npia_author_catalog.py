import importlib
import io
import sys
import threading
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
    pages = catalog.iter_author_pages(session, 42, delay=0, retries=1)
    assert next(pages) == (1, first, True)
    with pytest.raises(ValueError, match='TimeoutError'):
        next(pages)
    assert [p["paging[curPage]"] for p in session.posts] == [1, 2]


def test_repeated_page_and_author_mismatch_are_rejected():
    first = [novel(n) for n in range(20)]
    pages = catalog.iter_author_pages(Session([page(first, 41), page(first, 41)]), 42, delay=0, retries=1)
    next(pages)
    with pytest.raises(ValueError, match="repeated"):
        next(pages)
    with pytest.raises(ValueError, match="identity"):
        list(catalog.iter_author_pages(Session([page([novel(1)])]), 43, delay=0, retries=1))


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
    scraper.enrich_author_catalogs(session, {"1": dict(id=1, author_id="42", age=19)}, {}, state, page_limit=1, page_size=100,
                                   delay=0, now=1_000_000)
    assert session.posts[0]['paging[curPage]'] == 2
    assert state['author_pending']['42'] == 3 and state['author_page_size'] == 100


def test_only_r19_targets_are_scheduled_under_a_request_budget():
    scraper = scraper_module()
    authors = {str(n): {'name': f'Author {n}', 'names': [f'Author {n}'], 'novel_ids': [str(n)]}
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
                                   page_limit=5, page_size=100, delay=0, now=1_000_000, workers=1)
    assert calls == ['2', '3', '4', '5', '6']
    assert state['author_last_run']['targeted_pages'] == 5
    assert state['author_last_run']['r19_unique'] == 5
    assert state['author_last_run']['r19_new'] == 0


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
    fresh = {"1": dict(id=1, author_id="42", age=19, cover="saved-cover", updated="saved-date")}
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
    fresh = {"1": dict(id=1, author_id="42", age=19, synopsis="Saved")}
    state = {"author_pending": {"42": 3}}
    scraper.enrich_author_catalogs(Session([TimeoutError("timeout")]), fresh, {}, state,
                                   page_limit=1, delay=0, now=1_000_000)
    assert state["author_pending"] == {"42": 3}
    assert fresh["1"]["synopsis"] == "Saved"
    assert state["author_last_run"]["failed"] == 1


def test_desktop_cancel_retains_completed_author_page_and_stops(monkeypatch):
    session = Session([page([novel(n) for n in range(1, 101)], 101)])
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
    assert set(downloader.fetch_all_novels(delay=0, max_queries=1)) == {str(n) for n in range(1, 101)}
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


@pytest.mark.parametrize('known_in', ['fresh', 'full', 'compact'])
def test_author_response_without_start_date_preserves_publication_date(known_in):
    scraper = scraper_module()
    fresh = {'1': dict(id=1, author_id='42', age=19, published=None)}
    compact = [[1, 'Title', 'Writer', '', [], 100, 20, 8, 1, 'updated', 0, 19] + [0]*8 + ['']]
    old_full = {}
    if known_in == 'fresh':
        fresh['1']['published'] = '2025-02-04'
    elif known_in == 'full':
        old_full['1'] = {'published': '2025-02-04'}
    else:
        compact[0][20] = '2025-02-04'
    scraper.enrich_author_catalogs(Session([page([novel(1)])]), fresh, old_full, {},
                                   existing=compact, page_limit=1, delay=0, now=1_000_000)
    assert fresh['1']['published'] == '2025-02-04'


def public_author_session(calls, fail=()):
    def post(url, **kwargs):
        owner = kwargs['data']['mem_no']
        calls.append(owner)
        if owner in fail:
            raise TimeoutError('offline timeout')
        row = dict(novel_no=int(owner), mem_no=int(owner), novel_name='Title', novel_age=0)
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([row])))
    return SimpleNamespace(post=post)


def author_registry_fixture(count):
    return {'authors': {str(n): {'name': f'Author {n}', 'names': [], 'novel_ids': [str(n)], 'r19_novel_ids': [str(n)],
                                'last_catalog_checked': 1_000_000, 'catalog_status': 'complete'}
                        for n in range(1, count + 1)}}


def test_default_sweep_checks_over_200_recent_authors_of_every_age():
    scraper = scraper_module()
    registry = author_registry_fixture(251)
    registry['authors']['2']['novel_ids'] = ['2', '999']
    registry['authors']['2']['r19_novel_ids'] = ['2', '999']
    state = {'author_checked': {owner: 1_000_000 for owner in registry['authors']}}
    calls = []
    existing = [[2, 'R19', 'Author 2'] + [0]*8 + [19]]
    scraper.enrich_author_catalogs(public_author_session(calls), {}, {}, state,
                                   registry=registry, existing=existing, delay=0, now=1_000_001, workers=1)
    assert calls[0] == '2'  # Priority affects order only, never inclusion.
    assert len(calls) == len(set(calls)) == 251
    assert set(calls) == set(registry['authors'])
    assert state['author_sweep']['complete']
    assert not state['author_sweep']['continuation_required']


def test_continuation_finishes_queue_includes_new_author_and_new_sweep_rechecks_all():
    scraper = scraper_module()
    registry = author_registry_fixture(3)
    state, calls = {}, []
    session = public_author_session(calls)
    scraper.enrich_author_catalogs(session, {}, {}, state, registry=registry,
                                   page_limit=1, delay=0, now=1_000_000, workers=1)
    sweep_id = state['author_sweep']['id']
    assert state['author_sweep']['continuation_required']
    registry['authors']['4'] = {'name': 'New', 'names': [], 'novel_ids': ['4'], 'r19_novel_ids': ['4']}
    scraper.enrich_author_catalogs(session, {}, {}, state, registry=registry, delay=0, now=1_000_001, workers=1)
    assert len(calls) == 4 and set(calls) == {'1', '2', '3', '4'}
    assert state['author_sweep']['id'] == sweep_id
    assert state['author_sweep']['complete']
    scraper.enrich_author_catalogs(session, {}, {}, state, registry=registry,
                                   resume=False, delay=0, now=1_000_002, workers=1)
    assert len(calls) == 8 and all(calls.count(owner) == 2 for owner in ('1', '2', '3', '4'))
    assert state['author_sweep']['id'] != sweep_id


def test_failed_author_does_not_exclude_others_or_claim_complete():
    scraper = scraper_module()
    registry = author_registry_fixture(3)
    state, calls = {}, []
    session = public_author_session(calls, fail={'1'})
    for _ in range(3):
        scraper.enrich_author_catalogs(session, {}, {}, state, registry=registry,
                                       delay=0, now=1_000_001)
    assert calls.count('1') == 12 and calls.count('2') == calls.count('3') == 1
    assert state['author_last_run']['pages'] == 4  # Retries count as HTTP requests.
    assert state['author_pending'] == {'1': 1}
    assert not state['author_sweep']['complete']
    assert state['author_sweep']['remaining'] == 1
    assert not state['author_sweep']['continuation_required']
    scraper.enrich_author_catalogs(public_author_session(calls), {}, {}, state,
                                   registry=registry, resume=False, delay=0, now=1_000_002)
    assert set(calls[-3:]) == {'1', '2', '3'}
    assert state['author_sweep']['complete']


def test_time_limit_preserves_author_page_and_continuation_cursor(monkeypatch):
    scraper = scraper_module()
    clock = [0.0]
    monkeypatch.setattr(scraper.time, 'monotonic', lambda: clock[0])
    first = [novel(n) for n in range(1, 21)]
    session = Session([page(first, 21)])
    post = session.post
    def timed_post(*args, **kwargs):
        result = post(*args, **kwargs)
        clock[0] = 11.0
        return result
    session.post = timed_post
    state, fresh = {}, {'1': dict(id=1, author_id='42', age=19)}
    scraper.enrich_author_catalogs(session, fresh, {}, state, seconds=10, delay=0, now=1_000_000)
    assert len(fresh) == 20
    assert state['author_pending'] == {'42': 2}
    assert state['author_sweep']['continuation_required']
    second = Session([page([novel(21)], 21)])
    scraper.enrich_author_catalogs(second, fresh, {}, state, seconds=10, delay=0, now=1_000_001)
    assert second.posts[0]['paging[curPage]'] == 2
    assert state['author_sweep']['complete']


def test_four_workers_overlap_and_all_authors_are_merged_without_shared_state_writes():
    scraper = scraper_module()
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    calls, threads = [], set()
    def post(url, **kwargs):
        owner = kwargs['data']['mem_no']
        with lock:
            calls.append(owner)
            threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        row = dict(novel_no=int(owner), mem_no=int(owner), novel_age=19, novel_name='Concurrent')
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([row])))
    state, fresh = {}, {}
    scraper.enrich_author_catalogs(SimpleNamespace(post=post), fresh, {}, state,
                                   registry=author_registry_fixture(8), delay=0, now=1_000_001)
    assert len(threads) == 4 and len(calls) == 8
    assert len(fresh) == 8 and state['author_sweep']['complete']
    assert state['author_last_run']['pages'] == 8


@pytest.mark.parametrize('failure', ['transport', 'http429', 'http500', 'http404', 'json',
                                    'shape', 'identity', 'short-final-page'])
def test_every_response_error_retries_same_page_before_success(failure):
    calls, logs = [], []
    good = SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([novel(1)])))
    def bad_json():
        raise ValueError('Expecting value')
    def post(url, **kwargs):
        calls.append(kwargs['data'].copy())
        if len(calls) == 1:
            if failure == 'transport':
                raise TimeoutError('request failed')
            if failure.startswith('http'):
                return SimpleNamespace(status_code=int(failure[4:]))
            if failure == 'json':
                return SimpleNamespace(status_code=200, json=bad_json)
            if failure == 'shape':
                return SimpleNamespace(status_code=200, json=lambda: [])
            if failure == 'short-final-page':
                return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([], 1)))
            return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([dict(novel_no=1, mem_no=99)])))
        return good
    rows = list(catalog.iter_author_pages(SimpleNamespace(post=post), 42,
                                          delay=0, retries=4, log=logs.append))
    assert rows == [(1, [novel(1)], False)]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert 'attempt 1/4 failed' in logs[0]


def test_random_1_to_2_second_delays_apply_to_initial_requests_retries_and_pages(monkeypatch):
    choices = iter([1.0, 2.0, 1.5])
    def uniform(low, high):
        assert (low, high) == (1, 2)
        return next(choices)
    monkeypatch.setattr(catalog.random, 'uniform', uniform)
    clock, sent = [0.0], []
    session = Session([TimeoutError('retry'), page([novel(1)], 2), page([novel(2)], 2)])
    post = session.post
    def timed_post(*args, **kwargs):
        sent.append(clock[0])
        return post(*args, **kwargs)
    session.post = timed_post
    rows = list(catalog.iter_author_pages(session, 42, page_size=1, delay=1, delay_max=2,
                                          sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    assert len(rows) == 2
    assert sent == pytest.approx([1, 3, 4.5])


def test_parallel_request_budget_includes_retries_and_never_overruns():
    calls = []
    session = public_author_session(calls, fail={str(n) for n in range(1, 20)})
    events = list(catalog.parallel_author_pages(session, {str(n): 1 for n in range(1, 20)},
                                                delay=0, delay_max=0, max_requests=5))
    assert len(calls) == sum(event[-1] for event in events) == 5
    assert all(rows is None for _, _, rows, _, _, _ in events)


def test_parallel_pages_detect_repetition_across_future_boundaries_and_retry():
    first = [novel(1)]
    second = [novel(2)]
    session = Session([page(first, 2), page(first, 2), page(second, 2)])
    events = list(catalog.parallel_author_pages(session, {'42': 1}, page_size=1,
                                                delay=0, delay_max=0))
    assert [(page_no, attempts) for _, page_no, _, _, _, attempts in events] == [(1, 1), (2, 2)]
    assert [post['paging[curPage]'] for post in session.posts] == [1, 2, 2]
    assert events[-1][2] == second


def test_parallel_permanent_failure_keeps_completed_pages_and_other_authors():
    calls = []
    def post(url, **kwargs):
        owner = kwargs['data']['mem_no']
        number = kwargs['data']['paging[curPage]']
        calls.append((owner, number))
        if owner == '42' and number == 2:
            raise TimeoutError('page failed')
        row = dict(novel_no=int(owner), mem_no=int(owner), novel_name='Kept')
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([row], 2 if owner == '42' else 1)))
    state, fresh = {'author_pending': {'42': 1, '43': 1}}, {}
    scraper_module().enrich_author_catalogs(SimpleNamespace(post=post), fresh, {}, state,
                                           page_size=1, delay=0, now=1_000_001,
                                           registry={'authors': {'42': {'name':'A','names':[],'novel_ids':['42'],'r19_novel_ids':['42']},
                                                                 '43': {'name':'B','names':[],'novel_ids':['43'],'r19_novel_ids':['43']}}})
    assert set(fresh) == {'42', '43'}
    assert state['author_pending'] == {'42': 2}
    assert calls.count(('42', 1)) == 1 and calls.count(('42', 2)) == 4
    assert state['author_last_run']['pages'] == 6
    assert state['author_sweep']['completed'] == ['43']


def test_each_worker_reuses_its_own_session_and_closes_it(monkeypatch):
    barrier = threading.Barrier(4)
    requests_by_client = {}
    closed = []
    lock = threading.Lock()
    class FakeSession(catalog.HTTPSession):
        def post(self, url, **kwargs):
            assert self.headers['X-Fixture'] == 'copied'
            assert self.cookies['fixture'] == 'copied'
            assert self.cookies.get('worker-marker') in (None, str(id(self)))
            self.cookies['worker-marker'] = str(id(self))
            with lock:
                requests_by_client.setdefault(id(self), []).append(threading.get_ident())
            barrier.wait(timeout=5)
            owner = int(kwargs['data']['mem_no'])
            row = dict(novel_no=owner, mem_no=owner)
            return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([row])))
        def close(self):
            closed.append(id(self))
            super().close()
    monkeypatch.setattr(catalog, 'HTTPSession', FakeSession)
    source = FakeSession()
    source.headers['X-Fixture'] = 'copied'
    source.cookies['fixture'] = 'copied'
    events = list(catalog.parallel_author_pages(source, {str(n): 1 for n in range(1, 9)},
                                                delay=0, delay_max=0))
    assert len(events) == 8 and all(event[4] is None for event in events)
    assert len(requests_by_client) == len(closed) == 4
    assert id(source) not in requests_by_client and id(source) not in closed
    assert 'worker-marker' not in source.cookies
    assert all(len(set(threads)) == 1 and len(threads) == 2 for threads in requests_by_client.values())


def test_desktop_uses_four_author_workers_and_retries_without_losing_ids(monkeypatch):
    barrier = threading.Barrier(4)
    calls, lock = {}, threading.Lock()
    def post(url, **kwargs):
        owner = kwargs['data']['mem_no']
        with lock:
            calls[owner] = calls.get(owner, 0) + 1
            attempt = calls[owner]
        if owner == '1' and attempt == 1:
            raise TimeoutError('transient')
        barrier.wait(timeout=5)
        row = dict(novel_no=int(owner), mem_no=int(owner), novel_age=19)
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=page([row])))
    session = SimpleNamespace(post=post, get=lambda *a, **kw: SimpleNamespace(
        status_code=200, text='JSON', json=lambda: dict(status=200, list=[
            dict(novel_no=n, mem_no=n, novel_age=19) for n in range(1, 5)])))
    downloader = core.DownloaderCore(SimpleNamespace(session=session), lambda _: None)
    monkeypatch.setattr(core, 'ALL_SEARCH_TERMS', ['fixture'])
    original = catalog.iter_author_pages
    monkeypatch.setattr(core, 'iter_author_pages', lambda *a, **kw: original(*a, **kw, sleep=lambda _: None))
    assert set(downloader.fetch_all_novels(delay=0, max_queries=1)) == {'1', '2', '3', '4'}
    assert calls == {'1': 2, '2': 1, '3': 1, '4': 1}


def test_old_all_author_checkpoint_is_filtered_to_r19_without_losing_its_cursor():
    scraper = scraper_module()
    data = {'authors': {
        '1': {'name': 'General', 'names': [], 'novel_ids': ['1']},
        '42': {'name': 'Adult', 'names': [], 'novel_ids': ['2'], 'r19_novel_ids': ['2']},
    }}
    state = {'author_pending': {'1': 4, '42': 3},
             'author_sweep': {'id': 'old-sweep', 'completed': ['1'], 'failures': {'1': 2}}}
    calls = []
    def post(url, **kwargs):
        calls.append((kwargs['data']['mem_no'], kwargs['data']['paging[curPage]']))
        raise TimeoutError('keep cursor')
    scraper.enrich_author_catalogs(SimpleNamespace(post=post), {}, {}, state, registry=data,
                                   page_limit=1, delay=0, now=1_000_001)
    assert calls == [('42', 3)]
    assert state['author_pending'] == {'42': 3}
    assert state['author_sweep']['id'] == 'old-sweep' and state['author_sweep']['scope'] == 'r19'
    assert state['author_sweep']['total'] == 1
    assert '1' not in state['author_sweep']['failures']
