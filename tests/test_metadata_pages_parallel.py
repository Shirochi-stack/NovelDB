import threading
import time

from scripts import metadata_common as m
from test_metadata_common import Adapter, Client, args


def test_eight_catalog_workers_overlap_and_commit_in_order(tmp_path):
    barrier = threading.Barrier(8)
    completed = []
    class Parallel(Adapter):
        def fetch_page(self, client, partition, page):
            if page <= 8:
                barrier.wait(timeout=5)
                time.sleep((9 - page) * .005)
            completed.append(page)
            return m.CatalogPage([{'id': str(page), 'title': 'Title', '_detail_complete': True}],
                                 page + 1 if page < 8 else None)
    result = m.run_source(Parallel(), args(tmp_path, '--mode', 'catalog', '--workers', '8', '--max-pages', '8'), client=Client())
    saved = m.load_state('naver', tmp_path / 'state')
    assert completed[0] != 1
    assert len(completed) == 8 and result['records'] == 8
    assert saved['progress']['partitions']['best']['last_page'] == 8
    assert saved['progress']['partitions']['best']['complete']


def test_failed_middle_page_never_advances_over_gap(tmp_path):
    class Failing(Adapter):
        def fetch_page(self, client, partition, page):
            if page == 3:
                return m.CatalogPage([], None, False, 'temporary failure')
            return m.CatalogPage([{'id': str(page), 'title': 'Title', '_detail_complete': True}], page + 1)
    m.run_source(Failing(), args(tmp_path, '--mode', 'catalog', '--workers', '8', '--max-pages', '8'), client=Client())
    saved = m.load_state('naver', tmp_path / 'state')
    assert set(saved['records']) == {'1', '2'}
    assert saved['progress']['partitions']['best']['next_page'] == 3
    class Resumed(Failing):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{'id': str(page), 'title': 'Title', '_detail_complete': True}], page + 1 if page < 8 else None)
    m.run_source(Resumed(), args(tmp_path, '--mode', 'catalog', '--resume', '--workers', '8', '--max-pages', '8'), client=Client())
    assert set(m.load_state('naver', tmp_path / 'state')['records']) == set(map(str, range(1, 9)))


def test_cursor_feeds_overlap_but_each_cursor_is_sequential(tmp_path):
    barrier = threading.Barrier(2)
    seen = []
    class Cursor(Adapter):
        def partitions(self, client):
            return [{'key': key, 'tier': key, 'start_page': 1, 'pagination': 'cursor-v1'} for key in ('a', 'b')]
        def fetch_page(self, client, partition, page):
            assert partition['cursor_point'] == ('' if page == 1 else 'next')
            if page == 1:
                barrier.wait(timeout=5)
            seen.append((partition['key'], page))
            return m.CatalogPage([{'id': str(page), 'title': 'Title', '_detail_complete': True}],
                                 2 if page == 1 else None, next_cursor='next' if page == 1 else None)
    m.run_source(Cursor(), args(tmp_path, '--mode', 'catalog', '--workers', '8'), client=Client())
    assert sorted(seen) == [('a', 1), ('a', 2), ('b', 1), ('b', 2)]
