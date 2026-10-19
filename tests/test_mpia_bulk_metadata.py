"""Anonymous search enrichment, field provenance and discovery fallback."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest

from scripts import metadata_common as common
from scripts import scrape_mpia as mpia
from scripts.metadata_common import BudgetExceeded, FetchError


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def row(nid=1, **changes):
    result = {"novelId": nid, "title": f"Title {nid}", "author": "Writer", "story": "Full\n\nsynopsis",
              "coverUrl": "https://covers.test/image.jpg", "mainGenre": "Genre", "subGenre": "Subgenre",
              "tag": ["Tag"], "viewCount": 123, "entryCount": 12, "finished": False,
              "adult": False, "ebook": False, "updateAt": "2026-10-20T12:30:00"}
    result.update(changes)
    return result


def envelope(value):
    return {"code": "M000_00000", "result": value}


class Client:
    def __init__(self, search=None):
        self.search = search or (lambda params: envelope({"searchNovelTabDtos": [row(1), row(2)], "hasNext": False}))
        self.requests, self.log, self.calls, self.details = 0, [], [], []

    def get_json(self, url, params=None):
        assert mpia.MpiaAdapter.is_allowed_url(url)
        if params:
            assert mpia.MpiaAdapter.is_allowed_url(url + "?" + urlencode(params))
        self.requests += 1
        self.calls.append((url, params))
        if url == mpia.SEARCH_URL:
            return self.search(params)
        if url == mpia.CATALOG_URL:
            assert params["size"] == 100 and params["page"] == 0
            return envelope({"items": [{"novelId": n, "novelTitle": f"Title {n}", "authorName": "Writer"}
                                       for n in (1, 2, 3)], "hasNext": False, "total": 3})
        nid = int(url.rsplit("/", 1)[-1])
        self.details.append(nid)
        return envelope({"novelInfo": {"id": nid, "title": f"Title {nid}", "authorName": "Writer",
                         "introduction": "Full detail", "likeCount": 7, "preferenceCount": 9}})


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ROOT", tmp_path / "workspace")
    monkeypatch.setattr(mpia, "SEARCH_QUERIES", ("판타지",))
    monkeypatch.setattr(common.requests.Session, "request", lambda *a, **k: pytest.fail("Unexpected network"))


def test_search_metadata_retains_native_fields_without_inventing_detail_counters():
    value = mpia.normalize_search(row())
    assert value["synopsis"] == "Full\n\nsynopsis" and value["views"] == 123 and value["episodes"] == 12
    assert value["tags"] == ["Genre", "Subgenre", "Tag"]
    assert value["complete"] == 0 and value["age"] == 0
    assert value["source_dates"]["updated"]["precision"] == "second"
    assert "likes" not in value and "metrics" not in value and "_detail_complete" not in value


@pytest.mark.parametrize("change", [{"novelId": 0}, {"novelId": True}, {"title": None}])
def test_invalid_search_identity_is_rejected(change):
    with pytest.raises(ValueError):
        mpia.normalize_search(row(**change))


@pytest.mark.parametrize("change", [{"story": None}, {"story": ""}, {"author": None}])
def test_missing_enrichment_fields_leave_work_for_detail_fallback(change):
    assert mpia.normalize_search(row(**change)) is None


def test_bulk_pagination_and_completed_batches_resume_without_requests():
    client = Client(lambda p: envelope({"searchNovelTabDtos": [row(p["page"]+1)], "hasNext": p["page"] == 0}))
    batches = list(mpia.MpiaAdapter().bulk_metadata(client))
    assert len(batches) == 2 and [b[1][0]["id"] for b in batches] == ["1", "2"]
    assert batches[-1][0].endswith(":end")
    assert all(p["size"] == 200 and p["adultMode"] == "false" for _, p in client.calls)
    resumed = Client()
    assert list(mpia.MpiaAdapter().bulk_metadata(resumed, skip_keys=[b[0] for b in batches])) == []
    assert not resumed.calls


def test_repeated_page_reports_error_after_retaining_first_batch():
    batches = list(mpia.MpiaAdapter().bulk_metadata(Client(lambda p: envelope({
        "searchNovelTabDtos": [row(1)], "hasNext": True}))))
    assert len(batches) == 2 and batches[0][1][0]["id"] == "1"
    assert batches[1][2] and not batches[1][1]


def test_malformed_batch_rejected_atomically_and_page_cap_bounds_calls(monkeypatch):
    bad = list(mpia.MpiaAdapter().bulk_metadata(Client(lambda p: envelope({
        "searchNovelTabDtos": [row(1), row(2, title=None)], "hasNext": False}))))
    assert len(bad) == 1 and bad[0][2] and bad[0][1] == []
    monkeypatch.setattr(mpia, "SEARCH_MAX_PAGES", 2)
    client = Client(lambda p: envelope({"searchNovelTabDtos": [row(p["page"]+1)], "hasNext": True}))
    assert len(list(mpia.MpiaAdapter().bulk_metadata(client))) == 2 and client.requests == 2


def test_allowlist_limits_search_to_verified_anonymous_parameters():
    params = {"query": "판타지", "tab": "ALL", "page": 0, "size": 200, "adultMode": "false"}
    assert mpia.MpiaAdapter.is_allowed_url(mpia.SEARCH_URL + "?" + urlencode(params))
    for change in ({"adultMode": "true"}, {"query": ""}, {"size": 1000}, {"page": -1}, {"token": "account"}):
        assert not mpia.MpiaAdapter.is_allowed_url(mpia.SEARCH_URL + "?" + urlencode({**params, **change}))


def test_catalog_checkpoint_migrates_once_and_keeps_records_and_details():
    adapter = mpia.MpiaAdapter()
    state = {"records": {"1": {"synopsis": "Saved"}}, "progress": {"pending_details": ["1"],
             "partitions": {"all-public": {"next_page": 400, "complete": True}}}}
    adapter.prepare_catalog(state, adapter.partitions(None))
    cursor = state["progress"]["partitions"]["all-public"]
    assert cursor == {"next_page": 0, "complete": False, "page_size": 100}
    cursor.update(next_page=5, last_page=4)
    adapter.prepare_catalog(state, adapter.partitions(None))
    assert cursor["next_page"] == 5
    assert state["records"]["1"]["synopsis"] == "Saved" and state["progress"]["pending_details"] == ["1"]


class Adapter(mpia.MpiaAdapter):
    supports_rankings = False


def options(tmp_path):
    return common.resolve_args(Adapter(), ["--mode", "catalog", "--resume", "--workers", "1",
        "--output-dir", str(tmp_path / "stage"), "--state-dir", str(tmp_path / "state")])


def seed(tmp_path, days=1):
    state = common.load_state("mpia", tmp_path / "state")
    state["records"]["1"] = {"id": "1", "title": "Title 1", "author": "Writer", "synopsis": "Cached",
        "likes": 777, "metrics": {"favorites": 33}, "history": {"last_success": ago(days), "first_seen": ago(60),
        "latest_outcome": "success"}}
    state["progress"]["partitions"] = {"all-public": {"next_page": 500, "complete": False}}
    common.save_state(state, tmp_path / "state")


def test_runner_uses_bulk_then_detail_only_for_uncovered_work_and_preserves_cached_fields(tmp_path):
    seed(tmp_path)
    client = Client()
    result = common.run_source(Adapter(), options(tmp_path), client=client)
    assert result["coverage"]["complete"] and result["records"] == 3
    assert client.details == [3]
    state = common.load_state("mpia", tmp_path / "state")
    assert state["records"]["1"]["likes"] == 777 and state["records"]["1"]["metrics"]["favorites"] == 33
    assert state["records"]["2"]["synopsis"] == "Full\n\nsynopsis"
    assert "last_success" not in state["records"]["2"]["history"]
    assert state["records"]["3"]["likes"] == 7


def test_old_full_detail_fields_are_refreshed_despite_fresh_bulk_synopsis(tmp_path):
    seed(tmp_path, days=31)
    client = Client()
    common.run_source(Adapter(), options(tmp_path), client=client)
    assert set(client.details) == {1, 3}
    assert common.load_state("mpia", tmp_path / "state")["records"]["1"]["likes"] == 7


def test_failed_or_incomplete_search_falls_back_to_individual_details(tmp_path):
    client = Client(lambda p: envelope({"searchNovelTabDtos": [row(1, story=None)], "hasNext": False}))
    report = common.run_source(Adapter(), options(tmp_path), client=client)
    assert report["coverage"]["complete"] and set(client.details) == {1, 2, 3}


def test_interrupted_bulk_scan_resumes_from_next_unfinished_page(tmp_path):
    def interrupted(params):
        if params["page"] == 1:
            raise BudgetExceeded("budget")
        return envelope({"searchNovelTabDtos": [row(1)], "hasNext": True})
    first = common.run_source(Adapter(), options(tmp_path), client=Client(interrupted))
    assert first["coverage"]["continuation"]["eligible"]
    client = Client(lambda p: envelope({"searchNovelTabDtos": [row(2)], "hasNext": False}))
    second = common.run_source(Adapter(), options(tmp_path), client=client)
    assert second["coverage"]["complete"]
    assert [p["page"] for url, p in client.calls if url == mpia.SEARCH_URL] == [1]
    assert client.details == [3]


def test_bulk_budget_propagates():
    def exhausted(params):
        raise BudgetExceeded("budget")
    with pytest.raises(BudgetExceeded):
        list(mpia.MpiaAdapter().bulk_metadata(Client(exhausted)))


def test_failed_search_still_finishes_full_discovery_and_details(tmp_path):
    def unavailable(params):
        raise FetchError("Public search unavailable", 503)
    client = Client(unavailable)
    report = common.run_source(Adapter(), options(tmp_path), client=client)
    assert report["coverage"]["complete"] and report["coverage"]["bulk"]["errors"]
    assert set(client.details) == {1, 2, 3}


def test_bulk_only_scan_cannot_claim_a_complete_catalog_baseline(tmp_path):
    class InterruptedCatalog(Adapter):
        def fetch_page(self, client, partition, page):
            raise BudgetExceeded("budget")
    report = common.run_source(InterruptedCatalog(), options(tmp_path), client=Client())
    assert report["records"] == 2
    assert not report["coverage"]["complete"] and not report["coverage"]["has_complete_baseline"]
    assert report["coverage"]["continuation"]["eligible"]


def test_changed_identity_and_stale_bulk_response_do_not_suppress_detail_checks():
    adapter = mpia.MpiaAdapter()
    record = {"title": "Title", "author": "Writer", "synopsis": "Bulk", "bulk_metadata_at": ago(1),
              "history": {"first_seen": ago(2), "last_success": ago(2)}}
    assert adapter.detail_needed(record, {"title": "Edited", "author": "Writer"}, True)
    record["bulk_metadata_at"] = ago(8)
    assert adapter.detail_needed(record, record, True)
    assert common.resolve_args(adapter, ["--output-dir", "unused", "--mode", "catalog"]).delay == 2
