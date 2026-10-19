"""Batch metadata, audit scheduling and incremental coverage boundaries."""

from datetime import datetime, timedelta, timezone

import pytest

from scripts import metadata_common as m
from scripts.scrape_nweb import BASE, NwebAdapter, parse_bulk
from test_metadata_common import Adapter, Client, args


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def wire(nid=10):
    return dict(novelId=nid, novelTitle="Title", novelTypeCode="CHALLENGE",
                authorNicknameList="Writer", synopsisContent="Full\r\nsynopsis", serviceStatusCode="Y",
                totalVolumeCount=12, volumeUpdateYmdt="2026-10-19 14:35:00", terminationYn="N",
                concernCount=3, starScoreTotalAverage=9.5, readCountSum=100, likeCount=2)


def payload(row=None):
    return {"challengeNovelRankingList": [row or wire()]}


def listing(nid):
    return dict(id=str(nid), title="Title", author="Writer", tier="challenge", episodes=12,
                complete=None, cover=None)


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "ROOT", tmp_path / "workspace")
    monkeypatch.setattr(m.requests.Session, "request", lambda *a, **k: pytest.fail("Unexpected network"))


def test_bulk_metadata_preserves_exact_counts_and_full_synopsis():
    rows = parse_bulk(payload(), "challenge", "challengeNovelRankingList")
    assert len(rows) == 1
    row = rows[0]
    assert row["synopsis"] == "Full\r\nsynopsis"
    assert row["episodes"] == 12 and row["views"] == 100
    assert row["updated"] == "2026-10-19T14:35:00+09:00"
    assert row["canonical_url"] == BASE + "/challenge/list?novelId=10"


@pytest.mark.parametrize("change", [dict(novelId=0), dict(novelTypeCode="BEST"), dict(novelTitle=None)])
def test_invalid_bulk_identity_rejects_entire_batch(change):
    row = wire()
    row.update(change)
    with pytest.raises(ValueError):
        parse_bulk({"challengeNovelRankingList": [wire(1), row]}, "challenge", "challengeNovelRankingList")


def test_bulk_deduplicates_age_groups_and_skips_removed_or_missing_synopsis():
    p = {key: [{**wire(), "serviceStatusCode": None}, {**wire(11), "serviceStatusCode": "N"}, {**wire(12), "synopsisContent": None}]
         for key in ("age10List", "age20List", "age30List", "age40List")}
    assert [r["id"] for r in parse_bulk(p, "challenge", "age")] == ["10"]


def test_allowlist_is_specific_and_default_pacing_is_two_seconds(tmp_path):
    adapter = NwebAdapter()
    assert adapter.is_allowed_url(BASE + "/challenge/agePopularListJson?genderTypeCode=ALL")
    assert adapter.is_allowed_url(BASE + "/best/genreCostRankingListJson?genre=102")
    assert not adapter.is_allowed_url(BASE + "/best/genreCostRankingListJson?genre=102&page=2")
    assert not adapter.is_allowed_url(BASE + "/challenge/agePopularListJson?genderTypeCode=ALL&extra=")
    assert not adapter.is_allowed_url(BASE + "/my/concernNovelList")
    assert m.resolve_args(adapter, ["--output-dir", str(tmp_path), "--mode", "catalog"]).delay == 2
    assert m.resolve_args(Adapter(), ["--output-dir", str(tmp_path), "--mode", "catalog"]).delay == .5


def test_existing_baseline_is_adopted_and_due_audits_are_full():
    a = NwebAdapter()
    state = {"coverage": {"has_complete_baseline": True}, "progress": {"pass_started_at": ago(7)}}
    a.begin_catalog(state)
    p = state["progress"]["nweb_policy"]
    assert p["mode"] == "incremental" and "last_full_completed_at" not in p
    p["audit_due_at"] = ago(1)
    a.begin_catalog(state)
    assert p["mode"] == "full"
    state["coverage"]["has_complete_baseline"] = False
    p["audit_due_at"] = ago(-100)
    a.begin_catalog(state)
    assert p["mode"] == "full"


def test_changed_rows_reset_window_and_resume_overlap_is_not_counted_twice():
    a = NwebAdapter()
    row = listing(1)
    old = {**row, "synopsis": "Known", "history": {"last_success": ago(1)},
           "listing_fingerprint": m.listing_fingerprint(row, a.detail_fields)}
    state = {"records": {"1": old}, "progress": {"nweb_policy": {"mode": "incremental"}}}
    cursor = {}
    result = m.CatalogPage([row], 99)
    assert not a.observe_catalog_page(state, {}, cursor, 1, result)
    assert not a.observe_catalog_page(state, {}, cursor, 1, result)
    assert not a.observe_catalog_page(state, {}, cursor, 2, result)
    assert a.observe_catalog_page(state, {}, cursor, 3, result)
    assert not a.observe_catalog_page(state, {}, cursor, 4, m.CatalogPage([listing(2)], 99))
    assert cursor["quiet_count"] == 0
    assert not a.observe_catalog_page(state, {}, cursor, 5, result)
    assert not a.observe_catalog_page(state, {}, cursor, 6, result)


def test_episode_changes_have_cooldown_but_important_edits_do_not():
    a = NwebAdapter()
    old = {**listing(1), "synopsis": "Known", "detail_version": 2,
           "history": {"last_success": ago(1), "latest_outcome": "success"}}
    assert not a.detail_needed(old, {**listing(1), "episodes": 13}, True)
    assert a.detail_needed(old, {**listing(1), "title": "Edited"}, True)
    old["history"]["last_success"] = ago(8)
    assert a.detail_needed(old, {**listing(1), "episodes": 13}, True)
    old["bulk_metadata_at"] = ago(0)
    a._full_audit = False
    assert not a.detail_needed(old, listing(1), True)
    a._full_audit = True
    old["history"].pop("last_success")
    assert a.detail_needed(old, listing(1), False)


class Hybrid(NwebAdapter):
    supports_rankings = False

    def partitions(self, client):
        return [{"key": "challenge:102:false", "tier": "challenge", "genre": "102", "start_page": 1}]

    def bulk_metadata(self, client, *, skip_keys=()):
        if "batch" not in skip_keys:
            client.requests += 1
            yield "batch", [], None

    def fetch_page(self, client, partition, page):
        client.requests += 1
        return m.CatalogPage([listing(page)], page + 1 if page < 10 else None)

    def detail(self, client, record):
        client.requests += 1
        return m.MetadataResult("success", {**record, "synopsis": "Full"})


def seed(tmp_path):
    state = m.load_state("nweb", tmp_path / "state")
    for page in range(1, 11):
        row = listing(page)
        row.update(synopsis="Known", detail_version=2, history={"last_success": ago(1), "latest_outcome": "success"},
                   listing_fingerprint=m.listing_fingerprint(row, NwebAdapter.detail_fields))
        row["detail_listing_fingerprint"] = row["listing_fingerprint"]
        state["records"][str(page)] = row
    state["coverage"]["has_complete_baseline"] = True
    state["progress"].update(pass_complete=True, pass_started_at=ago(7))
    m.save_state(state, tmp_path / "state")


def test_runner_early_stop_retains_baseline_and_next_run_starts_new_pass(tmp_path):
    seed(tmp_path)
    opts = args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1")
    first = m.run_source(Hybrid(), opts, client=Client())
    assert first["coverage"]["pages"] == 3
    assert not first["coverage"]["complete"]
    assert first["coverage"]["has_complete_baseline"]
    state = m.load_state("nweb", tmp_path / "state")
    assert state["progress"]["pass_complete"]
    assert not state["coverage"]["catalog"]["discovery_complete"]
    assert len(state["records"]) == 10
    scan = state["progress"]["scan_id"]
    second = m.run_source(Hybrid(), opts, client=Client())
    assert second["coverage"]["pages"] == 3
    assert m.load_state("nweb", tmp_path / "state")["progress"]["scan_id"] != scan


def test_runner_full_audit_traverses_old_pages_and_advances_due_date_only_on_success(tmp_path):
    seed(tmp_path)
    state = m.load_state("nweb", tmp_path / "state")
    state["progress"]["nweb_policy"] = {"audit_due_at": ago(1)}
    m.save_state(state, tmp_path / "state")
    result = m.run_source(Hybrid(), args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1"), client=Client())
    assert result["coverage"]["complete"] and result["coverage"]["pages"] == 10
    state = m.load_state("nweb", tmp_path / "state")
    assert state["progress"]["nweb_policy"]["mode"] == "full"
    assert state["progress"]["nweb_policy"]["last_full_completed_at"]


def test_runner_bulk_resume_preserves_keywords_and_full_detail_history(tmp_path):
    seed(tmp_path)
    state = m.load_state("nweb", tmp_path / "state")
    state["records"]["1"]["keywords"] = ["Existing"]
    success = state["records"]["1"]["history"]["last_success"]
    m.save_state(state, tmp_path / "state")
    class Batch(Hybrid):
        def bulk_metadata(self, client, *, skip_keys=()):
            if "batch" not in skip_keys:
                client.requests += 1
                yield "batch", [{**listing(1), "synopsis": "Bulk", "genres": ["Genre"]}], None
        def fetch_page(self, client, partition, page):
            raise m.BudgetExceeded("budget")
    opts = args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1")
    m.run_source(Batch(), opts, client=Client())
    state = m.load_state("nweb", tmp_path / "state")
    row = state["records"]["1"]
    assert row["synopsis"] == "Bulk" and row["keywords"] == ["Existing"]
    assert row["history"]["last_success"] == success
    assert state["progress"]["bulk_completed"] == ["batch"]
    result = m.run_source(Batch(), opts, client=Client())
    assert result["coverage"]["bulk"]["successful_batches"] == 0


def test_failed_bulk_batch_falls_back_to_catalog(tmp_path):
    class BrokenBatch(Hybrid):
        def bulk_metadata(self, client, *, skip_keys=()):
            yield "broken", [], "Malformed batch"
    result = m.run_source(BrokenBatch(), args(tmp_path, "--mode", "catalog", "--workers", "1"), client=Client())
    assert result["records"] == 10 and result["coverage"]["complete"]
    assert result["coverage"]["bulk"]["errors"]


def test_full_audit_budget_stop_keeps_due_date_and_resumes_without_early_stop(tmp_path):
    seed(tmp_path)
    state = m.load_state("nweb", tmp_path / "state")
    due = ago(1)
    state["progress"]["nweb_policy"] = {"audit_due_at": due}
    m.save_state(state, tmp_path / "state")
    class Interrupted(Hybrid):
        def fetch_page(self, client, partition, page):
            if page == 4:
                raise m.BudgetExceeded("budget")
            return super().fetch_page(client, partition, page)
    opts = args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1")
    first = m.run_source(Interrupted(), opts, client=Client())
    assert first["coverage"]["continuation"]["eligible"]
    state = m.load_state("nweb", tmp_path / "state")
    assert state["progress"]["nweb_policy"]["audit_due_at"] == due
    assert not state["progress"]["pass_complete"]
    scan = state["progress"]["scan_id"]
    second = m.run_source(Hybrid(), opts, client=Client())
    assert second["coverage"]["complete"]
    state = m.load_state("nweb", tmp_path / "state")
    assert state["progress"]["scan_id"] == scan
    assert state["progress"]["nweb_policy"]["audit_due_at"] != due


def test_parallel_prefetch_stops_processing_after_incremental_boundary(tmp_path):
    seed(tmp_path)
    client = Client()
    client.requests = 0
    result = m.run_source(Hybrid(), args(tmp_path, "--mode", "catalog", "--resume", "--workers", "4"), client=client)
    assert result["coverage"]["pages"] == 3
    assert client.requests <= 8  # One batch plus at most one bounded prefetch window.


@pytest.mark.parametrize("full", [False, True])
def test_bulk_only_work_receives_missing_fields_during_full_audit(tmp_path, full):
    seed(tmp_path)
    if full:
        state = m.load_state("nweb", tmp_path / "state")
        state["progress"]["nweb_policy"] = {"audit_due_at": ago(1)}
        m.save_state(state, tmp_path / "state")
    class Batch(Hybrid):
        def bulk_metadata(self, client, *, skip_keys=()):
            yield "batch", [{**listing(99), "synopsis": "Bulk"}], None
        def detail(self, client, record):
            assert record["id"] == "99"
            return m.MetadataResult("success", {**record, "keywords": ["Verified"]})
    result = m.run_source(Batch(), args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1"), client=Client())
    assert result["coverage"]["details"] == int(full)
    state = m.load_state("nweb", tmp_path / "state")
    assert bool(state["records"]["99"]["history"].get("last_success")) == full


def test_interrupted_bulk_collection_restores_incremental_policy_on_resume(tmp_path):
    seed(tmp_path)
    class InterruptedBatch(Hybrid):
        def bulk_metadata(self, client, *, skip_keys=()):
            if "first" not in skip_keys:
                yield "first", [{**listing(99), "synopsis": "Bulk"}], None
                raise m.BudgetExceeded("budget")
            yield "second", [{**listing(100), "synopsis": "Bulk"}], None
        def detail(self, client, record):
            pytest.fail("A fresh incremental batch should not fetch full details")
    opts = args(tmp_path, "--mode", "catalog", "--resume", "--workers", "1")
    first = m.run_source(InterruptedBatch(), opts, client=Client())
    assert first["coverage"]["continuation"]["eligible"]
    second = m.run_source(InterruptedBatch(), opts, client=Client())
    assert second["coverage"]["details"] == 0 and second["coverage"]["pages"] == 3
