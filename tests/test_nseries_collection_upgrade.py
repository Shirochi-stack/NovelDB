"""Anonymous batch matching, preserved metadata and audit/resume boundaries."""
import copy
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
import requests

from scripts import metadata_common as m
from scripts.scrape_nseries import BASE, CATALOG_JSON, MOBILE, NseriesAdapter, parse_catalog_metadata
from test_metadata_common import Client, args
from test_metadata_nseries import HTML


def wire(ident=123, grade="G19"):
    return {"productNo": ident, "productTypeCode": "NOVEL", "seeingGradeTypeCode": grade,
            "starScore": 9.6, "starScoreEvaluatorCount": 150, "synopsis": None,
            "serviceStartDate": "2024.03.04"}


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class CatalogClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"productList": [wire()]}
        self.error = error
        self.calls = []

    def get_text(self, url, params=None):
        self.calls.append((url, params))
        return HTML.replace('<strong>1</strong>', f'<strong>{params["page"]}</strong>')

    def get_json(self, url, params=None):
        assert NseriesAdapter.is_allowed_url(url + "?" + urlencode(params))
        self.calls.append((url, params))
        if self.error:
            raise self.error
        return self.payload


@pytest.mark.parametrize("grade,age", [("G0", 0), ("G12", 12), ("G15", 15), ("G18", 18), ("G19", 19), ("NEW", None)])
def test_batch_preserves_explicit_ages_and_rating_counts(grade, age):
    result = parse_catalog_metadata({"productList": [wire(grade=grade)]})["123"]
    assert result["age"] == age
    assert result["metrics"] == {"rating": 9.6, "rating_scale": 10, "rating_count": 150}
    assert result["published"] == "2024-03-04"
    assert "synopsis" not in result


@pytest.mark.parametrize("payload", [None, {}, {"productList": None}, {"productList": [None]},
    {"productList": [wire(), wire()]}, {"productList": [wire(0)]},
    {"productList": [{**wire(), "productTypeCode": "COMIC"}]},
    {"productList": [wire(i + 1) for i in range(26)]}])
def test_bad_batch_is_rejected_without_partial_enrichment(payload):
    with pytest.raises(ValueError):
        parse_catalog_metadata(payload)


@pytest.mark.parametrize("rating,count", [(True, False), (-1, -1), (float('nan'), 1.5), (11, '20')])
def test_invalid_batch_numbers_are_not_published(rating, count):
    row = {**wire(), "starScore": rating, "starScoreEvaluatorCount": count}
    assert parse_catalog_metadata({"productList": [row]})["123"]["metrics"] == {}


def test_second_catalog_page_uses_zero_based_json_offset():
    client = CatalogClient()
    result = NseriesAdapter().fetch_page(client, {"genre": "201"}, 2)
    assert result.complete and len(result.records) == 1
    assert client.calls[1][1]["start"] == 25
    row = result.records[0]
    assert row["nseries_batch_complete"]
    assert row["metrics"]["rating_count"] == 150
    assert row["published"] == "2024-03-04"
    assert row["synopsis"] == "Public synopsis preview.." and row["synopsis_is_preview"]


@pytest.mark.parametrize("error", [m.FetchError("Malformed JSON"), ValueError("Bad rows"), m.BudgetExceeded("budget")])
def test_optional_batch_failure_keeps_the_completed_catalog_page(error):
    result = NseriesAdapter().fetch_page(CatalogClient(error=error), {"genre": "201"}, 1)
    assert result.complete and result.next_page == 2
    assert result.records[0]["synopsis"] == "Public synopsis preview.."
    assert not result.records[0]["nseries_batch_complete"]


def test_shifted_json_batch_never_invents_or_misassigns_records():
    result = NseriesAdapter().fetch_page(CatalogClient({"productList": [wire(456, "G0")]}), {"genre": "201"}, 1)
    assert [r["id"] for r in result.records] == ["123"]
    assert result.records[0]["age"] == 19
    assert "rating_count" not in result.records[0]["metrics"]


def test_json_grade_cannot_downgrade_the_catalog_adult_badge():
    result = NseriesAdapter().fetch_page(CatalogClient({"productList": [wire(123, "G0")]}), {"genre": "201"}, 1)
    assert result.records[0]["age"] == 19


def test_mobile_headers_are_limited_to_mobile_metadata_and_session_is_anonymous():
    session = NseriesAdapter.create_session()
    try:
        mobile = session.prepare_request(requests.Request('GET', CATALOG_JSON))
        desktop = session.prepare_request(requests.Request('GET', BASE + '/novel/categoryProductList.series'))
        assert 'iPhone' in mobile.headers['User-Agent']
        assert mobile.headers['Referer'] == MOBILE + '/novel/categoryProductList.series'
        assert mobile.headers['X-Requested-With'] == 'XMLHttpRequest'
        assert 'iPhone' not in desktop.headers['User-Agent'] and 'X-Requested-With' not in desktop.headers
        assert not session.trust_env and session.auth is None and not session.cookies
    finally:
        session.close()


@pytest.mark.parametrize("change", [{"start": -1}, {"start": 26}, {"start": '25&token=x'}, {"countPerPage": 100},
    {"genreCode": '999'}, {"isFreePassChecked": 'true'}, {"token": 'x'}])
def test_mobile_allowlist_rejects_unverified_parameters(change):
    params = dict(categoryTypeCode='genre', genreCode='201', orderTypeCode='new', isFreePassChecked='false', start=25, countPerPage=25)
    params.update(change)
    assert not NseriesAdapter.is_allowed_url(CATALOG_JSON + '?' + urlencode(params))


def test_adult_details_never_make_a_request_even_when_previously_queued():
    class NoNetwork:
        def get_text(self, *args, **kwargs):
            pytest.fail("Adult detail should not be requested")
    a = NseriesAdapter()
    old = {"id": "123", "age": 19}
    assert not a.detail_needed(old, {}, True)
    assert a.detail(NoNetwork(), old).status == "restricted"
    assert a.detail_needed(old, {"age": 15}, True)


class Hybrid(NseriesAdapter):
    def partitions(self, client):
        return [{"key": "201", "genre": "201", "tier": "series", "start_page": 1}]

    def fetch_page(self, client, partition, page):
        client.requests += 2
        row = {"id": str(page), "title": "Title", "author": "Writer", "age": 19,
               "synopsis": "Preview..", "synopsis_is_preview": True,
               "metrics": {"synopsis_is_preview": True, "rating_count": 3},
               "tier": "series", "nseries_batch_complete": True}
        row["nseries_catalog_fingerprint"] = self.catalog_fingerprint(row)
        return m.CatalogPage([row], page + 1 if page < 10 else None)


def seed(tmp_path):
    state = m.load_state("nseries", tmp_path / 'state')
    for page in range(1, 11):
        row = Hybrid().fetch_page(Client(), {}, page).records[0]
        state["records"][str(page)] = row
    state["coverage"]["has_complete_baseline"] = True
    state["progress"].update(pass_complete=True, pass_started_at=ago(7))
    m.save_state(state, tmp_path / 'state')


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(m, 'ROOT', tmp_path / 'workspace')
    monkeypatch.setattr(m.requests.Session, 'request', lambda *a, **k: pytest.fail('Unexpected network'))


def test_runner_incremental_stops_after_three_pages_and_retains_all_records(tmp_path, offline):
    seed(tmp_path)
    opts = args(tmp_path, '--mode', 'catalog', '--resume', '--workers', '1')
    first = m.run_source(Hybrid(), opts, client=Client())
    assert first['coverage']['pages'] == 3 and first['coverage']['details'] == 0
    assert first['records'] == 10 and not first['coverage']['catalog']['discovery_complete']
    state = m.load_state('nseries', tmp_path / 'state')
    assert state['progress']['pass_complete']
    scan = state['progress']['scan_id']
    m.run_source(Hybrid(), opts, client=Client())
    assert m.load_state('nseries', tmp_path / 'state')['progress']['scan_id'] != scan


def test_runner_overdue_audit_scans_old_pages_and_advances_due_date(tmp_path, offline):
    seed(tmp_path)
    state = m.load_state('nseries', tmp_path / 'state')
    state['progress']['nseries_policy'] = {'audit_due_at': ago(1)}
    m.save_state(state, tmp_path / 'state')
    report = m.run_source(Hybrid(), args(tmp_path, '--mode', 'catalog', '--resume', '--workers', '1'), client=Client())
    assert report['coverage']['pages'] == 10 and report['coverage']['complete']
    policy = m.load_state('nseries', tmp_path / 'state')['progress']['nseries_policy']
    assert policy['mode'] == 'full' and policy['last_full_completed_at']
    assert datetime.fromisoformat(policy['audit_due_at']) > datetime.now(timezone.utc)


def test_full_description_and_old_pending_adult_record_survive_runner(tmp_path, offline):
    seed(tmp_path)
    state = m.load_state('nseries', tmp_path / 'state')
    state['records']['1'].update(synopsis='Full description', synopsis_is_preview=False,
                               history={'last_success': ago(1), 'latest_outcome': 'success'})
    state['records']['1']['metrics']['synopsis_is_preview'] = False
    state['progress']['pending_details'] = ['1']
    m.save_state(state, tmp_path / 'state')
    m.run_source(Hybrid(), args(tmp_path, '--mode', 'catalog', '--resume', '--workers', '1'), client=Client())
    state = m.load_state('nseries', tmp_path / 'state')
    row = state['records']['1']
    assert row['synopsis'] == 'Full description' and not row['synopsis_is_preview']
    assert not row['metrics']['synopsis_is_preview']
    assert row['metrics']['rating_count'] == 3 and not state['progress']['pending_details']


def test_quiet_window_resets_for_changes_failed_batches_and_resume_overlap():
    a = Hybrid()
    row = a.fetch_page(Client(), {}, 1).records[0]
    state = {'records': {'1': copy.deepcopy(row)}, 'progress': {'nseries_policy': {'mode': 'incremental'}}}
    cursor = {}
    result = m.CatalogPage([row], 99)
    assert not a.observe_catalog_page(state, {}, cursor, 1, result)
    assert not a.observe_catalog_page(state, {}, cursor, 1, result)
    assert not a.observe_catalog_page(state, {}, cursor, 2, result)
    broken = copy.deepcopy(row)
    broken['nseries_batch_complete'] = False
    assert not a.observe_catalog_page(state, {}, cursor, 3, m.CatalogPage([broken], 99))
    assert cursor['quiet_count'] == 0
    assert not a.observe_catalog_page(state, {}, cursor, 4, result)
    assert not a.observe_catalog_page(state, {}, cursor, 5, result)
    assert a.observe_catalog_page(state, {}, cursor, 6, result)
    changed = {**row, 'nseries_catalog_fingerprint': 'changed'}
    assert not a.observe_catalog_page(state, {}, cursor, 7, m.CatalogPage([changed], 99))


def test_failed_audit_does_not_advance_schedule():
    a = Hybrid()
    state = {'progress': {'nseries_policy': {'mode': 'full', 'audit_due_at': ago(1)}}, 'coverage': {'catalog': {'discovery_complete': True}}}
    before = copy.deepcopy(state)
    assert not a.finish_catalog_pass(state, False)
    assert state == before


def test_budget_stop_retains_audit_and_resumes_the_same_scan(tmp_path, offline):
    seed(tmp_path)
    state = m.load_state('nseries', tmp_path / 'state')
    due = ago(1)
    state['progress']['nseries_policy'] = {'mode': 'full', 'audit_due_at': due}
    m.save_state(state, tmp_path / 'state')

    class Interrupted(Hybrid):
        def fetch_page(self, client, partition, page):
            if page == 4:
                raise m.BudgetExceeded('budget')
            return super().fetch_page(client, partition, page)

    opts = args(tmp_path, '--mode', 'catalog', '--resume', '--workers', '1')
    first = m.run_source(Interrupted(), opts, client=Client())
    assert first['coverage']['continuation']['eligible']
    state = m.load_state('nseries', tmp_path / 'state')
    scan = state['progress']['scan_id']
    assert not state['progress']['pass_complete']
    assert state['progress']['nseries_policy']['audit_due_at'] == due
    second = m.run_source(Hybrid(), opts, client=Client())
    assert second['coverage']['complete']
    state = m.load_state('nseries', tmp_path / 'state')
    assert state['progress']['scan_id'] == scan
    assert state['progress']['nseries_policy']['audit_due_at'] != due
