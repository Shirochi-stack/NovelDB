from copy import deepcopy
import json

import pytest

from scripts.backfill_npia_publication_dates import apply_dates, collect_dates, ranked_authors
from scripts import backfill_npia_publication_dates as backfill


def test_backfill_only_changes_the_publication_slot_and_preserves_known_dates():
    rows = [[7, 'Title', 'Writer', '', [], 123, 15, 4, 1, 'updated', 0, 19] + [0]*8,
            [8, 'Other', 'Writer', '', [], 456, 20, 8, 0, 'updated', 0, 0] + [0]*8 + ['2020-01-02']]
    before = deepcopy(rows)
    assert apply_dates(rows, {'7': '2025-02-04 21:24:15', '8': '2026-01-01', '9': '2020-01-01'}) == 1
    assert rows[0][:20] == before[0]
    assert rows[0][20] == '2025-02-04T21:24:15'
    assert rows[1] == before[1]
    assert apply_dates(rows, {'7': '2025-02-04'}) == 0


def test_backfill_never_infers_dates_from_updates_or_registration():
    dates = {'1': '2020-01-02'}
    collect_dates([{'novel_no': 1, 'start_date': None},
                   {'novel_no': 2, 'reg_date': '2025-01-01', 'last_viewdate': '2026-01-01'},
                   {'novel_no': 3, 'start_date': '2025-02-30'},
                   {'novel_no': 4, 'start_date': '2025-02-04 21:24:15'}], dates)
    assert dates == {'1': '2020-01-02', '4': '2025-02-04T21:24:15'}


def test_author_fallback_prioritizes_ranked_missing_dates():
    def row(nid, rank):
        return [nid, 'Title', 'Writer', '', [], 123, 15, 4, 1, 'updated', 0, 19, 0, rank] + [0]*6 + ['']
    registry = {'authors': {'42': {'novel_ids': ['1']}, '43': {'novel_ids': ['2']}, '44': {'novel_ids': ['3']}}}
    assert ranked_authors([row(1, 0), row(2, 1), row(3, 2)], registry, {'3': '2020-01-02'}, 1) == ['43']


def test_search_clones_the_verified_metadata_session(monkeypatch):
    source_session = object()
    received = []
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(backfill, 'make_search_session', lambda source: received.append(source) or Session())
    monkeypatch.setattr(backfill, 'paginated_search', lambda fetch, params: [{'novel_no': 7, 'start_date': '2025-02-04'}])
    assert backfill.search_dates('query', 0, 1, source_session) == ({'7': '2025-02-04'}, None)
    assert received == [source_session]


def test_adult_author_scope_excludes_general_deleted_and_already_dated_rows():
    def row(nid, age, tags=None, date=''):
        return [nid, 'Title', 'Writer', '', tags or [], 123, 15, 4, 1, 'updated', 0, age] + [0]*8 + [date]
    rows = [row(1, 0), row(2, 19), row(3, 19, ['deleted']), row(4, 19, date='2020-01-02')]
    registry = {'authors': {str(n): {'novel_ids': [str(n)]} for n in range(1, 5)}}
    assert ranked_authors(rows, registry, {}, 100, adult_only=True) == ['2']


@pytest.mark.parametrize('error', [None, 'ValueError'])
def test_author_only_cli_saves_dates_and_reports_partial_results(monkeypatch, tmp_path, error):
    rows = [[7, 'Title', 'Writer', '', [], 123, 15, 4, 1, 'updated', 0, 19] + [0]*8]
    (tmp_path/'novels.json').write_text(json.dumps(rows))
    (tmp_path/'npia_authors.json').write_text(json.dumps({'authors': {'42': {'novel_ids': ['7']}}}))
    checkpoint = tmp_path/'dates.json'
    monkeypatch.setattr(backfill.sys, 'argv', ['backfill', '--data-dir', str(tmp_path), '--checkpoint', str(checkpoint),
                                           '--skip-search', '--adult-authors', '--author-limit', '100'])
    monkeypatch.setattr(backfill, 'make_search_session', lambda: object())
    monkeypatch.setattr(backfill, 'login_metadata_session', lambda session: False)
    monkeypatch.setattr(backfill, 'search_dates', lambda *args: pytest.fail('Search must be skipped'))
    monkeypatch.setattr(backfill, 'author_dates', lambda *args: ({'7': '2025-02-04'}, error))
    if error:
        with pytest.raises(SystemExit) as result:
            backfill.main()
        assert result.value.code == 1
    else:
        backfill.main()
    updated = json.loads((tmp_path/'novels.json').read_text())
    assert updated[0][:20] == rows[0]
    assert updated[0][20] == '2025-02-04'
    state = json.loads(checkpoint.read_text())
    assert state['dates'] == {'7': '2025-02-04'}
    assert state['completed_authors'] == ([] if error else ['42'])
    assert bool(state['failures']) == bool(error)
