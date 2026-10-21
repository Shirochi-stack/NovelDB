import importlib
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import npia_author_registry as registry


def test_registry_preserves_missing_authors_and_tracks_names(tmp_path):
    path = tmp_path / "authors.json"
    data = registry.load(path)
    registry.observe(data, [dict(id=1, author_id="42", author="Old name"),
                            dict(id=2, author_id="43", author="Other")], now=10)
    registry.record_search(data, [dict(id=3, author_id="42", author="New name")], now=20)
    result = registry.report_coverage(data, {"3"})
    assert result["known_authors_missing_from_search"] == ["43"]
    assert data["authors"]["42"]["names"] == ["New name", "Old name"]
    assert data["authors"]["42"]["novel_ids"] == ["1", "3"]
    registry.save(path, data)
    assert registry.load(path) == data


def test_bad_registry_is_not_silently_replaced(tmp_path):
    path = tmp_path / "authors.json"
    path.write_text('{"version":99,"authors":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        registry.load(path)


def test_legacy_names_are_retained_without_guessing_or_merging_author_ids():
    data = {"version": 1, "authors": {}}
    registry.seed_catalog_names(data, [[1, "Title", "Same name"], [2, "Other", "Same name"],
                                       [3, "Old", "Missing name"]])
    registry.record_search(data, [dict(id=1, author_id="42", author="Same name"),
                                  dict(id=2, author_id="43", author="Same name")], now=20)
    assert set(data["authors"]) == {"42", "43"}
    assert data["unresolved_names"] == {"Missing name": ["3"]}
    report = registry.report_coverage(data, {"1", "2"})
    assert report["unresolved_names_missing_from_search"] == ["Missing name"]


def test_r19_plan_targets_name_candidates_without_assigning_author_identities():
    data = {'authors': {'10': {'name': 'General', 'names': ['General'], 'novel_ids': ['10']},
                        '42': {'name': 'Same', 'names': ['Same'], 'novel_ids': ['2']},
                        '43': {'name': 'Same', 'names': ['Same'], 'novel_ids': ['3']}}}
    rows = [[1, 'Title', 'Same'] + [0]*8 + [19],
            [2, 'General', 'Same'] + [0]*8 + [0],
            [4, 'Other', 'Unknown'] + [0]*8 + [19],
            [5, 'Other again', 'Unknown'] + [0]*8 + [19],
            [6, 'One more', 'Unknown'] + [0]*8 + [19]]
    before = json.dumps(data, sort_keys=True)
    plan = registry.plan_r19_requests(data, rows, now=1_000_000)
    assert plan['authors'] == ['42', '43']
    assert plan['detail_ids'] == ['4']
    assert plan['confirmed_ids'] == 0 and plan['unresolved_groups'] == 1
    assert json.dumps(data, sort_keys=True) == before


def test_r19_plan_reuses_confirmed_authors_and_resolves_larger_unknown_groups_first():
    data = {'authors': {'42': {'name': 'Known', 'names': ['Known'], 'novel_ids': ['1']}}}
    rows = [[nid, 'Title', name] + [0]*8 + [19]
            for nid, name in [(1, 'Renamed'), (2, 'Small'), (3, 'Large'), (4, 'Large')]]
    plan = registry.plan_r19_requests(data, rows, now=1_000_000)
    assert plan['authors'] == ['42'] and plan['detail_ids'] == ['3', '2']
    assert plan['confirmed_ids'] == 1


def test_unchecked_r19_authors_are_backfilled_before_repeating_prolific_authors():
    data = {'authors': {
        '42': {'name': 'Many', 'novel_ids': ['1', '2', '3'], 'last_catalog_checked': 10},
        '43': {'name': 'One', 'novel_ids': ['4']},
    }}
    rows = [[nid, 'Title', name] + [0]*8 + [19]
            for nid, name in [(1, 'Many'), (2, 'Many'), (3, 'Many'), (4, 'One')]]
    assert registry.plan_r19_requests(data, rows, now=1_000_000)['authors'] == ['43', '42']


def test_disproved_name_candidate_falls_back_to_detail_resolution():
    data = {'authors': {'42': {'name': 'Same', 'novel_ids': ['2'],
                               'catalog_novel_ids': ['2'], 'catalog_status': 'complete',
                               'last_catalog_checked': 10}}}
    rows = [[1, 'Title', 'Same'] + [0]*8 + [19]]
    plan = registry.plan_r19_requests(data, rows, now=1_000_000)
    assert plan['authors'] == [] and plan['detail_ids'] == ['1']


def test_atomic_registry_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "authors.json"
    registry.save(path, {"version": 1, "authors": {}})
    before = path.read_bytes()
    def fail(*args):
        raise PermissionError("locked")
    monkeypatch.setattr(registry.os, "replace", fail)
    with pytest.raises(PermissionError):
        registry.save(path, {"version": 1, "authors": {"42": {}}})
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def load_scraper(name):
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, "stdout", Output()):
        return importlib.import_module("scripts." + name)


def test_registry_author_absent_from_search_is_still_checked():
    scraper = load_scraper("rescrape_npia_noauth")
    data = {"version": 1, "authors": {}}
    registry.observe(data, [dict(id=1, author_id="42", author="Writer", age=19)], now=10)
    registry.record_search(data, [], now=1_000_000)
    calls = []
    item = dict(novel_no=2, mem_no=42, novel_name="Missing novel", novel_age=19, novel_story="Synopsis")
    def post(url, **kwargs):
        calls.append(kwargs["data"])
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(novel=[item], allCount=1)))
    fresh = {}
    scraper.enrich_author_catalogs(SimpleNamespace(post=post), fresh, {}, {},
                                   page_limit=1, delay=0, now=1_000_000, registry=data)
    assert calls[0]["mem_no"] == "42" and fresh["2"]["age"] == 19
    coverage = registry.report_coverage(data, set())
    assert coverage["known_authors_missing_from_search"] == ["42"]
    assert coverage["checked_catalog_novels_missing_from_search"] == ["2"]
    assert data["authors"]["42"]["catalog_status"] == "complete"


def test_failed_catalog_does_not_mark_author_complete():
    scraper = load_scraper("rescrape_npia_noauth")
    data = {"version": 1, "authors": {}}
    registry.observe(data, [dict(id=1, author_id="42", author="Writer", age=19)], now=10)
    def fail(*a, **k):
        raise TimeoutError("timeout")
    state = {}
    scraper.enrich_author_catalogs(SimpleNamespace(post=fail), {}, {}, state,
                                   page_limit=1, delay=0, now=1_000_000, registry=data)
    assert data["authors"]["42"]["catalog_status"] == "partial"
    assert "last_catalog_checked" not in data["authors"]["42"]
    assert state["author_pending"] == {"42": 1}


def test_authenticated_path_records_r19_without_catalog_or_config_execution():
    import ast
    from pathlib import Path
    source = Path('scripts/scrape_npia.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    calls = [node.func for node in ast.walk(main) if isinstance(node, ast.Call)]
    names = {node.id for node in calls if isinstance(node, ast.Name)}
    assert not {'enrich_author_catalogs', 'discover_intro', 'parallel_author_pages'} & names
    assert any(isinstance(node, ast.Attribute) and node.attr == 'refresh_r19' for node in calls)
    batch = Path('rescrape_auth.bat').read_text(encoding='utf-8')
    assert 'python scripts/scrape_npia.py\n' in batch and '--author-' not in batch
    data = {'version': 1, 'authors': {}}
    registry.record_search(data, [dict(id=1, author_id='42', author='Adult', age=19),
                                  dict(id=2, author_id='43', author='General', age=0)], now=10)
    subset = registry.refresh_r19(data, now=11)
    assert set(subset['authors']) == {'42'}
    assert subset['authors']['42']['evidence'] == 'verified'


def test_workflow_scraper_checks_registry_when_search_finds_nothing(tmp_path, monkeypatch):
    scraper = load_scraper("rescrape_npia_noauth")
    data = tmp_path / "docs" / "data"
    path = data / "npia_authors.json"
    saved = {"version": 1, "authors": {}}
    registry.observe(saved, [dict(id=1, author_id="42", author="Known", age=19)], now=1)
    registry.save(path, saved)
    monkeypatch.setattr(scraper, "__file__", str(tmp_path / "scripts" / "rescrape_npia_noauth.py"))
    monkeypatch.setattr(scraper, "fetch_terms", lambda *a: {})
    monkeypatch.setattr(scraper, "scrape_ranking", lambda *a: {})
    item = dict(novel_no=2, mem_no=42, writer_nick="Known", novel_name="Recovered", novel_age=19)
    def post(*a, **kw):
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(novel=[item], allCount=1)))
    session = SimpleNamespace(headers={}, post=post, get=lambda *a, **k: SimpleNamespace(text="", status_code=200))
    monkeypatch.setattr(scraper.requests, "Session", lambda: session)
    monkeypatch.setattr(sys, "argv", ["scraper", "--r19-listing-pages", "0", "--r19-detail-limit", "0",
                                     "--author-pages", "1", "--author-delay", "0", "--intro-requests", "0"])
    scraper.main()
    result = registry.load(path)
    assert result["authors"]["42"]["catalog_status"] == "complete"
    assert result["coverage"]["known_authors_missing_from_search"] == ["42"]
    assert json.loads((data / "novels.json").read_text(encoding="utf-8"))[0][0] == 2


def test_workflow_continuation_persists_progress_and_preserves_catalog(tmp_path, monkeypatch):
    scraper = load_scraper('rescrape_npia_noauth')
    data = tmp_path / 'docs' / 'data'
    saved = {'version': 1, 'authors': {}}
    registry.observe(saved, [dict(id=n, author_id=str(n), author=f'Author {n}', age=19)
                            for n in (1, 2)], now=1)
    registry.save(data / 'npia_authors.json', saved)
    # A retained row absent from search must not be tagged deleted by a continuation,
    # even for a tiny catalog below the partial-search deletion guard threshold.
    retained = [99, 'Saved', 'Saved author', 'cover', [], 100, 10, 5, 0, 'date', 0, 0] + [7]*8
    (data / 'novels.json').write_text(json.dumps([retained]), encoding='utf-8')
    monkeypatch.setattr(scraper, '__file__', str(tmp_path / 'scripts' / 'rescrape_npia_noauth.py'))
    monkeypatch.setattr(scraper, 'fetch_terms', lambda *a: {})
    monkeypatch.setattr(scraper, 'scrape_ranking', lambda *a: {})
    calls = []
    def post(*args, **kwargs):
        owner = kwargs['data']['mem_no']
        calls.append(owner)
        item = dict(novel_no=int(owner), mem_no=int(owner), novel_age=19,
                    novel_name='Recovered', novel_story=f'Synopsis {owner}')
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(novel=[item], allCount=1)))
    session = SimpleNamespace(headers={}, post=post, get=lambda *a, **k: SimpleNamespace(text='', status_code=200))
    monkeypatch.setattr(scraper.requests, 'Session', lambda: session)
    decision_path = tmp_path / 'continuation.json'
    argv = ['scraper', '--r19-listing-pages', '0', '--r19-detail-limit', '0', '--author-pages', '1',
            '--author-delay', '0', '--intro-requests', '0', '--author-continuation-file', str(decision_path)]
    monkeypatch.setattr(sys, 'argv', argv)
    scraper.main()
    decision = json.loads(decision_path.read_text(encoding='utf-8'))
    assert decision['continuation_required'] and decision['remaining'] == 1
    # Simulate a new runner without the ignored full metadata file.
    (data / 'novels_full.json').unlink()
    after_first = json.loads((data / 'novels.json').read_text(encoding='utf-8'))
    for row in after_first:
        row[4] = []
    (data / 'novels.json').write_text(json.dumps(after_first), encoding='utf-8')
    def unexpected(*args, **kwargs):
        pytest.fail('Continuation repeated the initial discovery phase')
    monkeypatch.setattr(scraper, 'fetch_terms', unexpected)
    monkeypatch.setattr(scraper, 'collect_rankings', unexpected)
    monkeypatch.setattr(scraper, 'enrich', unexpected)
    monkeypatch.setattr(scraper, 'discover_intro', unexpected)
    monkeypatch.setattr(sys, 'argv', argv + ['--author-sweep-id', decision['id']])
    scraper.main()
    completed = json.loads(decision_path.read_text(encoding='utf-8'))
    assert completed['id'] == decision['id'] and completed['complete']
    assert completed['remaining'] == 0 and not completed['continuation_required']
    assert calls == ['1', '2']

    rows = {row[0]: row for row in json.loads((data / 'novels.json').read_text(encoding='utf-8'))}
    assert set(rows) == {1, 2, 99}
    assert rows[99][4] == [] and rows[99][12:] == [7]*8
    assert rows[1][1] == 'Recovered'
    # Stale continuation requests cannot reset or start a different sweep.
    monkeypatch.setattr(sys, 'argv', argv + ['--author-sweep-id', 'stale'])
    with pytest.raises(SystemExit):
        scraper.main()
    assert calls == ['1', '2']


def test_r19_subset_uses_age_and_explicit_tags_without_assigning_name_candidates():
    data = {'version': 1, 'authors': {}}
    registry.observe(data, [dict(id=10, author_id='10', author='General'),
                            dict(id=30, author_id='30', author='Matched')], now=1)
    rows = [[20, 'Tagged', 'Matched', '', ['R19']],
            [21, 'Unresolved', 'Missing', '', ['19금']],
            [22, 'Genre only', 'General', '', ['고수위']]]
    full = [dict(id=40, author_id='40', author='Verified', age=19)]
    subset = registry.refresh_r19(data, rows, full, now=2)
    assert set(subset['authors']) == {'30', '40'}
    assert subset['authors']['30']['evidence'] == 'name_match'
    assert subset['authors']['30']['verified_novel_ids'] == []
    assert data['authors']['30']['novel_ids'] == ['30']
    assert 'r19_novel_ids' not in data['authors']['30']
    assert subset['authors']['40']['verified_novel_ids'] == ['40']
    assert subset['unresolved_names'] == {'Missing': ['21']}
    later = registry.refresh_r19(data, [], [], now=3)
    assert later['known_names'] == subset['known_names']
    assert set(later['authors']) == {'30', '40'}


def test_offline_registry_refresh_never_opens_account_config_or_uses_http(tmp_path, monkeypatch):
    import builtins
    import requests
    from scripts import update_npia_r19_registry as updater
    rows = tmp_path / 'novels.json'
    full = tmp_path / 'full.json'
    path = tmp_path / 'authors.json'
    rows.write_text(json.dumps([[1, 'Title', 'Writer'] + [0]*8 + [19]]), encoding='utf-8')
    full.write_text(json.dumps([dict(id=1, author_id='42', author='Writer', age=19)]), encoding='utf-8')
    original = builtins.open
    def guarded_open(file, *args, **kwargs):
        assert str(file).replace('\\', '/').split('/')[-1] != 'config.json'
        return original(file, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', guarded_open)
    def forbidden(*args, **kwargs):
        pytest.fail('Offline registry update attempted HTTP')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(sys, 'argv', ['updater', '--registry', str(path), '--catalog', str(rows), '--full', str(full)])
    updater.main()
    result = registry.load(path)
    assert result['r19']['verified_author_count'] == 1
    assert result['r19']['authors']['42']['name'] == 'Writer'


@pytest.mark.parametrize('reason', [registry.DELETED_IDENTITY_REASON, registry.MISSING_IDENTITY_REASON])
def test_confirmed_deletions_keep_all_fields_and_tags_without_marking_access_errors(reason):
    import copy
    rows = [[nid, 'Saved title', 'Writer', 'cover', ['Fantasy'], 12, 3, 4, 0, 'date', 0, 19]
            for nid in (1, 2)]
    full = [dict(id=nid, title='Saved title', author='Writer', cover='cover', tags=['Fantasy'],
                 synopsis='Saved synopsis', views=12, likes=3, age=19) for nid in (1, 2)]
    before_rows, before_full = copy.deepcopy(rows), copy.deepcopy(full)
    data = {'r19_identity_unavailable': {
        '1': dict(reason=reason, checked=1),
        '2': dict(reason='site reports invalid or unavailable novel access', checked=1)}}
    assert registry.preserve_deleted_tags(data, rows, full) == dict(compact=1, full=1)
    before_rows[0][4].append('deleted');before_full[0]['tags'].append('deleted')
    assert rows == before_rows and full == before_full
    assert registry.preserve_deleted_tags(data, rows, full) == dict(compact=0, full=0)


def test_continuation_saves_confirmed_deleted_r19_novel_and_synopsis_without_requesting_it(tmp_path, monkeypatch):
    import time
    scraper = load_scraper('rescrape_npia_noauth')
    data = tmp_path / 'docs' / 'data'
    saved = {'version': 1, 'authors': {}, 'r19_identity_unavailable': {
        '1': dict(reason=registry.DELETED_IDENTITY_REASON, checked=time.time()),
        '2': dict(reason='site reports invalid or unavailable novel access', checked=time.time())}}
    registry.save(data / 'npia_authors.json', saved)
    rows = [[nid, 'Saved title', 'Writer', 'cover', ['Fantasy'], 12, 3, 4, 0, 'date', 0, 19]+[0]*8
            for nid in (1, 2)]
    full = [dict(id=nid, title='Saved title', author='Writer', cover='cover', tags=['Fantasy'],
                 synopsis='Saved synopsis', views=12, likes=3, age=19) for nid in (1, 2)]
    (data / 'novels.json').write_text(json.dumps(rows), encoding='utf-8')
    (data / 'novels_full.json').write_text(json.dumps(full), encoding='utf-8')
    (data / 'npia_public_state.json').write_text(json.dumps({'author_sweep': dict(
        id='saved', completed=[], failures={})}), encoding='utf-8')
    monkeypatch.setattr(scraper, '__file__', str(tmp_path / 'scripts' / 'rescrape_npia_noauth.py'))
    def get(url, **kwargs):
        assert '/novel/' not in url
        return SimpleNamespace(status_code=200, text='')
    monkeypatch.setattr(scraper.requests, 'Session', lambda: SimpleNamespace(headers={}, get=get))
    monkeypatch.setattr(sys, 'argv', ['scraper', '--author-sweep-id', 'saved', '--intro-requests', '0'])
    scraper.main()
    rows[0][4].append('deleted');full[0]['tags'].append('deleted')
    assert json.loads((data / 'novels.json').read_text(encoding='utf-8')) == rows
    assert json.loads((data / 'novels_full.json').read_text(encoding='utf-8')) == full
