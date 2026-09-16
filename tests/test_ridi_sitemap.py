import copy
import gzip
import json
from types import SimpleNamespace

import pytest

from scripts import ridi_sitemap as sm
from scripts.scrape_ridi import RidiAdapter, CATEGORIES
from scripts.metadata_common import catalog_coverage, AnonymousClient, FetchError


def test_sitemap_rejects_foreign_paths_and_duplicate_ids():
    class Client:
        def get(self, url):
            return SimpleNamespace(content=self.content, close=lambda: None)
    client = Client()
    url = 'https://ridibooks.com/sitemap-books-1.xml.gz'
    client.content = gzip.compress(b'<urlset><url><loc>https://ridibooks.com/books/123</loc></url></urlset>')
    assert sm.sitemap_ids(client, url) == ['123']
    for body in (b'<urlset><loc>https://other.test/books/123</loc></urlset>',
                 b'<urlset><loc>https://ridibooks.com/books/123</loc><loc>https://ridibooks.com/books/123</loc></urlset>',
                 b'<!DOCTYPE urlset><urlset/>'):
        client.content = body
        with pytest.raises(ValueError):
            sm.sitemap_ids(client, url)


def test_canonical_series_filter_and_empty_batches():
    adapter = RidiAdapter()
    adapter._scan_id = 'scan'
    adapter._sitemap_ids = {'map': ['1', '2', '3']}
    class Client:
        calls = []
        def get_json(self, url, *, json_body):
            self.calls.append(json_body)
            if json_body['query'] == sm.IDS_QUERY:
                return {'data': {'books': [
                    {'id': '1', 'categories': [{'id': 1753, 'parentId': 1750}], 'series': {'id': '10'}},
                    {'id': '2', 'categories': [{'id': 1710}], 'series': {'id': '20'}},
                    {'id': '3', 'categories': [{'id': 1753, 'parentId': 1750}], 'series': {'id': '10'}}]}}
            assert json_body['variables']['bookIds'] == ['10']
            return {'data': {'books': [{'id': '10', 'title': {'main': 'First episode'},
                'categories': [{'id': 1750, 'name': 'Fantasy'}], 'authors': [],
                'series': {'id': '10', 'title': 'Whole novel', 'totalEpisodeCount': 99},
                'introduction': {'description': 'Full synopsis'}}]}}
    result = adapter._sitemap_page(Client(), {'sitemap': 'map'}, 1)
    assert len(result.records) == 1 and result.records[0]['id'] == '10'
    assert result.records[0]['title'] == 'Whole novel'
    assert result.records[0]['synopsis'] == 'Full synopsis'
    assert result.scanned_items == 3
    class Empty:
        def get_json(self, *args, **kwargs):
            return {'data': {'books': []}}
    adapter._sitemap_ids['map'] = [str(i) for i in range(1, 1002)]
    result = adapter._sitemap_page(Empty(), {'sitemap': 'map'}, 1)
    assert result.records == [] and result.next_page == 2 and result.scanned_items == 1000


@pytest.mark.parametrize('payload', [
    {'errors': [{'message': 'failed'}], 'data': {'books': []}},
    {'data': {'books': [{'id': '999'}]}},
    {'data': {'books': [{'id': '1'}, {'id': '1'}]}},
    {'data': {'books': None}},
])
def test_bad_metadata_batches_fail_without_advancing(payload):
    client = SimpleNamespace(get_json=lambda *a, **kw: payload)
    with pytest.raises(ValueError):
        sm.books(client, ['1'])


def test_resume_freezes_sitemap_ids_and_preserves_records(monkeypatch):
    adapter = RidiAdapter()
    url = 'https://ridibooks.com/sitemap-books-1.xml.gz'
    monkeypatch.setattr(sm, 'download_locations', lambda *a: [url])
    calls = []
    def ids(*args):
        calls.append(args)
        return ['123']
    monkeypatch.setattr(sm, 'sitemap_ids', ids)
    client = SimpleNamespace(get_json=lambda *a, **kw: {'data': {'totalCount': 1}})
    parts = adapter.partitions(client)
    state = {'progress': {'scan_id': 'scan', 'partitions': {'1750': {'next_page': 101}}},
             'records': {'123': {'title': 'Saved', 'translations': {'title': 'English'}}}}
    saved = copy.deepcopy(state['records'])
    adapter.prepare_catalog(state, parts)
    adapter.prepare_catalog(state, parts)
    assert len(calls) == 1
    assert state['records'] == saved
    assert state['progress']['retired_partitions']['1750'] == {'next_page': 101}
    assert adapter._sitemap_ids[url] == ['123']
    # A restarted process uses the frozen index, even if the live index changes.
    resumed = RidiAdapter()
    resumed.restore_catalog(state)
    monkeypatch.setattr(sm, 'download_locations', lambda *a: pytest.fail('Refetched frozen sitemap index'))
    resumed_parts = resumed.partitions(client)
    resumed.prepare_catalog(state, resumed_parts)
    assert resumed._sitemap_ids == adapter._sitemap_ids


def test_completion_requires_current_scan_counts_not_old_records():
    adapter = RidiAdapter()
    adapter._scan_id = 'new'
    adapter._parts = adapter.partitions(None)
    adapter._scan_id = 'new'
    state = {'progress': {'ridi_expected': {c: 1 for c in CATEGORIES},
                          'partitions': {p['key']: {'complete': True} for p in adapter._parts}},
             'records': {'1': {'ridi_seen_scan': 'old', 'ridi_categories': list(CATEGORIES)}},
             'coverage': {'errors': []}}
    adapter.finalize_catalog(state)
    assert not catalog_coverage(state['progress']['partitions'])['discovery_complete']
    assert state['coverage']['errors']
    state['records']['1']['ridi_seen_scan'] = 'new'
    state['coverage']['errors'] = []
    adapter.finalize_catalog(state)
    assert catalog_coverage(state['progress']['partitions'])['discovery_complete']
    assert not state['coverage']['errors']


def test_public_post_uses_request_budget_and_refuses_redirects(monkeypatch):
    import requests
    class Session:
        headers = {}
        def post(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            response = requests.Response()
            response.url = url
            response.status_code = 302
            response.headers['Location'] = 'https://account.ridibooks.com/'
            response._content = b''
            response._content_consumed = True
            return response
        def close(self):
            pass
    monkeypatch.setattr(RidiAdapter, 'create_session', staticmethod(Session))
    client = AnonymousClient(RidiAdapter(), max_requests=1)
    with pytest.raises(FetchError, match='POST redirects'):
        sm.books(client, ['123'])
    assert client.requests == 1 and client.log[0]['method'] == 'POST'
    assert '123' not in json.dumps(client.log)
    client.close()


def test_empty_sitemap_batch_is_resumable_progress(tmp_path):
    from scripts import metadata_common as m
    from test_metadata_common import Adapter, Client, args
    class Indexed(Adapter):
        def fetch_page(self, client, partition, page):
            if page == 1:
                return m.CatalogPage([], 2, scanned_items=1000)
            raise m.BudgetExceeded('request budget')
    result = m.run_source(Indexed(), args(tmp_path, '--mode', 'catalog', '--workers', '1'), client=Client())
    saved = m.load_state('naver', tmp_path / 'state')
    assert saved['progress']['partitions']['best']['next_page'] == 2
    assert result['coverage']['continuation']['eligible']


def test_repeated_work_in_distinct_sitemap_batches_is_deduplicated(tmp_path):
    from scripts import metadata_common as m
    from test_metadata_common import Adapter, Client, args
    class Indexed(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{'id': '1', 'title': 'Work', '_detail_complete': True}],
                                 page + 1 if page < 3 else None, scanned_items=1000)
    result = m.run_source(Indexed(), args(tmp_path, '--mode', 'catalog', '--workers', '2'), client=Client())
    assert result['records'] == 1
    assert result['coverage']['catalog']['discovery_complete']
    assert not result['coverage']['errors']
