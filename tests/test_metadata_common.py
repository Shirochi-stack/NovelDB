"""Offline contract tests for anonymous collection and historical metadata."""
from datetime import datetime, timedelta, timezone
import gzip
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import requests

from scripts import metadata_common as m


class Adapter:
    source = "naver"
    label = "Fixture"

    def is_allowed_url(self, url):
        return url.startswith("https://example.test/catalog")

    def partitions(self, client):
        return [{"key": "best", "tier": "best", "start_page": 1}]

    def fetch_page(self, client, partition, page):
        client.requests += 1
        return m.CatalogPage([{"id": str(page), "title": "소설 " + str(page)}], page + 1 if page < 3 else None)

    def detail(self, client, record):
        client.requests += 1
        return m.MetadataResult("success", {"id": record["id"], "synopsis": "한 줄\n두 줄|||셋", "views": 0})

    def rankings(self, client, *, skip_keys=()):
        if "best" not in skip_keys:
            yield m.RankingResult("best", "Native Best", [{"id": "1", "rank": 1}])


class Client:
    requests = 0
    log = []


def test_cursor_resume_migrates_numbered_checkpoint_and_preserves_records(tmp_path):
    state = m.empty_state("naver")
    state["records"]["99"] = {"id": "99", "title": "Saved", "translations": {"title": {"english": "Saved translation"}}}
    state["progress"]["partitions"] = {"best": {"next_page": 101, "complete": False, "error": "old page limit"}}
    m.save_state(state, tmp_path / "state")
    seen = []
    class CursorAdapter(Adapter):
        def partitions(self, client):
            return [{"key": "best", "tier": "best", "start_page": 1, "pagination": "cursor-v1"}]
        def fetch_page(self, client, partition, page):
            seen.append((page, partition["cursor_point"]))
            return m.CatalogPage([{"id": str(page), "title": "Title", "_detail_complete": True}],
                                 page + 1 if page < 3 else None, next_cursor=f"cursor-{page}")
    adapter = CursorAdapter()
    for _ in range(3):
        m.run_source(adapter, args(tmp_path, "--mode", "catalog", "--resume", "--max-pages", "1"), client=Client())
    assert seen == [(1, ""), (2, "cursor-1"), (3, "cursor-2")]
    saved = m.load_state("naver", tmp_path / "state")
    assert saved["records"]["99"]["translations"]["title"]["english"] == "Saved translation"
    assert saved["progress"]["partitions"]["best"]["complete"]


@pytest.mark.parametrize("next_cursor", [None, "", "existing"])
def test_bad_cursor_does_not_advance_checkpoint(tmp_path, next_cursor):
    state = m.empty_state("naver")
    state["progress"]["partitions"] = {"best": {"next_page": 101, "complete": False,
        "pagination": "cursor-v1", "cursor_point": "existing"}}
    m.save_state(state, tmp_path / "state")
    class BadCursor(Adapter):
        def partitions(self, client):
            return [{"key": "best", "tier": "best", "start_page": 1, "pagination": "cursor-v1"}]
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": "1", "title": "Title"}], 102, next_cursor=next_cursor)
    report = m.run_source(BadCursor(), args(tmp_path, "--mode", "catalog", "--resume"), client=Client())
    cursor = m.load_state("naver", tmp_path / "state")["progress"]["partitions"]["best"]
    assert cursor["next_page"] == 101 and cursor["cursor_point"] == "existing"
    assert report["coverage"]["catalog"]["errors"][0]["page"] == 101


def test_skipped_catalog_rows_preserve_later_pages_and_partial_coverage(tmp_path):
    seen = []
    class MissingTitle(Adapter):
        def fetch_page(self, client, partition, page):
            seen.append(page)
            result = super().fetch_page(client, partition, page)
            if page == 1:
                result.skipped_rows = [{"row": 2, "id": "99", "error": "Title unavailable in public catalog"}]
            return result
    report = m.run_source(MissingTitle(), args(tmp_path, "--mode", "catalog"), client=Client())
    assert seen == [1, 2, 3] and report["records"] == 3
    catalog = report["coverage"]["catalog"]
    assert not catalog["errors"]
    assert catalog["skipped_rows"] == [{"partition": "best", "page": 1, "row": 2, "id": "99",
                                        "error": "Title unavailable in public catalog"}]
    assert not catalog["discovery_complete"] and not catalog["has_complete_baseline"]
    assert not report["coverage"]["complete"] and not report["coverage"]["has_complete_baseline"]
    assert report["coverage"]["successful_details"] == 3
    refreshed = m.run_source(Adapter(), args(tmp_path, "--mode", "rankings"), client=Client())
    assert refreshed["coverage"]["catalog"] == catalog
    retried = m.run_source(Adapter(), args(tmp_path, "--mode", "catalog"), client=Client())
    assert retried["coverage"]["complete"]
    assert retried["coverage"]["catalog"]["skipped_rows"] == []


def test_catalog_failure_reports_actual_overlap_page(tmp_path):
    state = m.empty_state("naver")
    state["progress"]["partitions"] = {"best": {"next_page": 3, "complete": False}}
    m.save_state(state, tmp_path / "state")
    class BrokenOverlap(Adapter):
        def fetch_page(self, client, partition, page):
            assert page == 2
            return m.CatalogPage([], None, False, "Malformed payload")
    report = m.run_source(BrokenOverlap(), args(tmp_path, "--mode", "catalog", "--resume"), client=Client())
    assert report["coverage"]["catalog"]["errors"] == [
        {"partition": "best", "page": 2, "error": "Malformed payload"}]


def test_full_catalog_metadata_on_rankings_is_persisted_without_detail_request(tmp_path):
    class RichRanking(Adapter):
        def rankings(self, client, *, skip_keys=()):
            yield m.RankingResult("best", "Native Best", [{"id": "10", "title": "Novel", "rank": 1,
                "synopsis": "Full public synopsis", "episodes": 75, "_detail_complete": True}])
        def detail(self, client, record):
            pytest.fail("Already complete metadata should not be fetched again")
    m.run_source(RichRanking(), args(tmp_path, "--mode", "rankings"), client=Client())
    state = m.load_state("naver", tmp_path / "state")
    assert state["records"]["10"]["synopsis"] == "Full public synopsis"
    assert state["records"]["10"]["episodes"] == 75
    assert not state["progress"]["pending_details"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError("Network forbidden in offline test")
    monkeypatch.setattr(requests.Session, "request", unexpected)
    monkeypatch.setattr(m, "ROOT", tmp_path / "workspace")


def args(tmp_path, *extra):
    return m.resolve_args(Adapter(), ["--output-dir", str(tmp_path / "stage"), "--state-dir", str(tmp_path / "state"), *extra])


def test_imports_and_dry_run_do_not_write_or_connect(tmp_path, monkeypatch):
    for name in ("scripts.metadata_common", "scripts.scrape_naver", "scripts.scrape_munpia", "scripts.scrape_joara", "scripts.scrape_ridi"):
        importlib.import_module(name)
    def unexpected(*args, **kwargs):
        raise AssertionError("Dry run constructed a network client")
    monkeypatch.setattr(m, "AnonymousClient", unexpected)
    result = m.run_source(Adapter(), args(tmp_path, "--dry-run"))
    assert result["requests"] == result["writes"] == 0
    assert list(tmp_path.iterdir()) == []


def test_all_entrypoints_dry_run_and_import_safety(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = "import requests,pathlib; requests.Session.request=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); import scripts.scrape_naver,scripts.scrape_munpia,scripts.scrape_joara,scripts.scrape_ridi"
    subprocess.run([sys.executable, "-B", "-c", code], cwd=root, check=True, capture_output=True)
    for source in m.SOURCE_LABELS:
        result = subprocess.run([sys.executable, "-B", str(root / "scripts" / ("scrape_" + source + ".py")),
                                 "--output-dir", str(tmp_path / source), "--dry-run"], check=True, capture_output=True, text=True)
        assert json.loads(result.stdout)["writes"] == 0
        assert not (tmp_path / source).exists()


def test_sample_output_rejects_production(tmp_path):
    opts = args(tmp_path)
    opts.output_dir = m.ROOT / "docs" / "data" / "sample"
    with pytest.raises(ValueError, match="staging"):
        m.run_source(Adapter(), opts, client=Client())


def test_state_roundtrip_source_identity_and_exact_export(tmp_path):
    state = m.empty_state("naver")
    record = {"id": "7", "title": "한글|||title\nline", "author": "글쓴이", "cover": "x", "tags": ["a"],
              "views": None, "likes": 0, "episodes": 3, "complete": None, "updated": "2026-09", "age": None,
              "canonical_url": "https://novel.naver.com/best/list?novelId=7", "tier": "best",
              "purchase_url": None, "metrics": {"characters": 800}, "synopsis": "newline\n|||한글"}
    m.merge_record(state, record, detail=True)
    m.merge_board(state, m.RankingResult("board", "Board", [{"id": "7", "rank": 11}]))
    m.save_state(state, tmp_path)
    loaded = m.load_state("naver", tmp_path)
    assert loaded == state
    row = m.export_rows(loaded)[0]
    assert row == ["7", record["title"], "글쓴이", "x", ["a"], None, 0, 3, None, "2026-09", None,
                   record["canonical_url"], "best", None, {"characters": 800}, {"board": 11}]
    assert len(row) == 16
    assert m.load_state("munpia", tmp_path)["records"] == {}


def test_history_preserves_fields_after_partial_or_restricted_observations():
    state = m.empty_state("naver")
    record = m.merge_record(state, {"id": "1", "title": "Old", "views": 15, "synopsis": "Old synopsis",
                                    "metrics": {"favorites": 4}}, detail=True)
    record["translations"] = {"title": {"original": "Old", "english": "Original"}}
    m.merge_record(state, {"id": "1", "title": None, "views": None, "metrics": {"favorites": None}, "tags": []})
    m.record_outcome(record, m.MetadataResult("restricted", reason="Age gate"))
    assert record["title"] == "Old" and record["views"] == 15
    assert record["metrics"]["favorites"] == 4 and record["translations"]
    assert "deleted" not in record and record["history"]["last_success"]
    m.record_outcome(record, m.MetadataResult("unavailable", reason="Explicit missing metadata"))
    assert record["history"]["explicit_unavailability"]["reason"]
    assert record["synopsis"] == "Old synopsis"


def test_refresh_uses_successful_listing_fingerprint_and_30_day_ttl():
    now = datetime.now(timezone.utc)
    record = {"detail_listing_fingerprint": "a", "history": {"last_success": now.isoformat()}}
    assert not m.needs_detail(record, "a", now)
    assert m.needs_detail(record, "b", now)
    assert m.needs_detail(record, "a", now + timedelta(days=31))


def test_failed_board_retains_observation_and_all_rows():
    state = m.empty_state("munpia")
    items = [{"id": str(i), "rank": i, "window_views": i * 50} for i in range(1, 219)]
    m.merge_board(state, m.RankingResult("contest", "Contest", items, "2026-09-13T00:00:00Z"))
    m.merge_board(state, m.RankingResult("contest", "Contest", [], success=False, error="Unavailable"))
    board = state["boards"]["contest"]
    assert len(board["records"]) == 218 and board["stale"]
    assert board["observed_at"] == "2026-09-13T00:00:00Z"


def test_budget_limited_resume_overlaps_and_preserves_history(tmp_path):
    first = m.run_source(Adapter(), args(tmp_path, "--max-pages", "1"), client=Client())
    assert first["records"] == 1 and first["coverage"]["status"] == "partial"
    state = m.load_state("naver", tmp_path / "state")
    assert state["progress"]["partitions"]["best"]["next_page"] == 2
    second = m.run_source(Adapter(), args(tmp_path, "--mode", "catalog", "--resume"), client=Client())
    assert second["records"] == 3 and second["coverage"]["has_complete_baseline"]
    assert m.load_state("naver", tmp_path / "state")["records"]["1"]["synopsis"] == "한 줄\n두 줄|||셋"


def test_interrupted_detail_is_checkpointed_and_retried(tmp_path):
    class Interrupted(Adapter):
        def detail(self, client, record):
            raise m.BudgetExceeded("Request budget reached")
    report = m.run_source(Interrupted(), args(tmp_path), client=Client())
    assert "budget" in report["coverage"]["stop_reason"]
    assert m.load_state("naver", tmp_path / "state")["progress"]["pending_details"] == ["1", "2"]
    m.run_source(Adapter(), args(tmp_path, "--resume"), client=Client())
    assert m.load_state("naver", tmp_path / "state")["records"]["1"]["synopsis"]


def test_completed_catalog_restarts_after_an_incomplete_ranking_refresh(tmp_path):
    m.run_source(Adapter(), args(tmp_path, "--mode", "catalog"), client=Client())
    state = m.load_state("naver", tmp_path / "state")
    assert state["progress"]["pass_complete"]
    state["coverage"] = {"mode": "rankings", "complete": False, "has_complete_baseline": True}
    m.save_state(state, tmp_path / "state")
    report = m.run_source(Adapter(), args(tmp_path, "--mode", "catalog", "--resume"), client=Client())
    assert report["coverage"]["pages"] == 3
    assert report["coverage"]["complete"]


@pytest.mark.parametrize("kind", ["repeated", "empty", "malformed", "backwards"])
def test_incomplete_page_never_establishes_deletion(tmp_path, kind):
    state = m.empty_state("naver")
    m.merge_record(state, {"id": "99", "title": "Historical"})
    m.save_state(state, tmp_path / "state")
    class Broken(Adapter):
        def fetch_page(self, client, partition, page):
            if kind == "repeated":
                return m.CatalogPage([{"id": "1", "title": "One"}], page + 1)
            if kind == "empty":
                return m.CatalogPage([], page + 1)
            if kind == "malformed":
                return m.CatalogPage([], None, False, "Malformed payload")
            return m.CatalogPage([{"id": "1", "title": "One"}], page)
    report = m.run_source(Broken(), args(tmp_path), client=Client())
    assert not report["coverage"]["complete"] and report["coverage"]["errors"]
    preserved = m.load_state("naver", tmp_path / "state")["records"]["99"]
    assert preserved["title"] == "Historical" and "deleted" not in preserved


def test_request_allowlist_and_budget_before_network():
    client = m.AnonymousClient(Adapter(), max_requests=0)
    with pytest.raises(m.FetchError, match="allowlist"):
        client.get("https://example.test/login")
    with pytest.raises(m.FetchError, match="allowlist"):
        client.get("https://user:secret@example.test/catalog")
    with pytest.raises(m.BudgetExceeded):
        client.get("https://example.test/catalog")
    assert client.requests == 0


def test_retries_pacing_retry_after_and_redacted_request_log(monkeypatch):
    times = [0.0]
    starts = []
    def sleep(seconds):
        times[0] += seconds
    def request(session, method, url, **kwargs):
        starts.append(times[0])
        response = requests.Response()
        response.status_code = 429 if len(starts) == 1 else 200
        response.headers["Retry-After"] = "3"
        response._content = b'{"ok":true}'
        response._content_consumed = True
        response.url = url
        assert not session.trust_env and not session.auth
        assert kwargs["allow_redirects"] is False
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    client = m.AnonymousClient(Adapter(), max_requests=3, clock=lambda: times[0], sleep=sleep)
    assert client.get_json("https://example.test/catalog", {"api_key": "secret", "deviceuid": "private"}) == {"ok": True}
    client.get_json("https://example.test/catalog")
    assert starts == [0, 3, 3.5]
    assert "secret" not in json.dumps(client.log) and "private" not in json.dumps(client.log)
    with pytest.raises(m.BudgetExceeded):
        client.get("https://example.test/catalog")
    client.close()


def test_redirect_to_login_is_not_followed(monkeypatch):
    def request(session, method, url, **kwargs):
        response = requests.Response()
        response.status_code = 302
        response.headers["Location"] = "/login"
        response.url = url
        response._content = b""
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    client = m.AnonymousClient(Adapter())
    with pytest.raises(m.FetchError, match="allowlist"):
        client.get("https://example.test/catalog")
    assert client.requests == 1


def test_separate_query_params_are_checked_before_any_network_request():
    from scripts.scrape_naver import NaverAdapter
    from scripts.scrape_munpia import MunpiaAdapter
    for adapter, url in ((NaverAdapter(), "https://novel.naver.com/best/list"),
                         (MunpiaAdapter(), "https://www.munpia.com/api/v1/pc/novel-detail/1")):
        client = m.AnonymousClient(adapter)
        with pytest.raises(m.FetchError, match="allowlist"):
            client.get(url, {"token": "account-value"})
        assert client.requests == 0 and client.log == []


def test_one_page_resumes_eventually_advance_past_overlap(tmp_path):
    m.run_source(Adapter(), args(tmp_path, "--max-pages", "1"), client=Client())
    m.run_source(Adapter(), args(tmp_path, "--max-pages", "1", "--resume"), client=Client())
    state = m.load_state("naver", tmp_path / "state")
    assert state["progress"]["partitions"]["best"]["overlap_checked_for"] == 2
    m.run_source(Adapter(), args(tmp_path, "--max-pages", "1", "--resume"), client=Client())
    state = m.load_state("naver", tmp_path / "state")
    assert state["progress"]["partitions"]["best"]["next_page"] == 3
    assert "2" in state["records"]


def test_repeated_page_detection_survives_bounded_resume(tmp_path):
    class Repeated(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": "1", "title": "Repeated", "_detail_complete": True}], page + 1)
    for resume in (False, True, True):
        extra = ["--max-pages", "1"] + (["--resume"] if resume else [])
        report = m.run_source(Repeated(), args(tmp_path, *extra), client=Client())
    assert not report["coverage"]["complete"]
    assert "repeated catalog page" in report["coverage"]["errors"][0]["error"]


def test_partial_listing_cannot_erase_complete_synopsis_before_detail_succeeds(tmp_path):
    state = m.empty_state("naver")
    m.merge_record(state, {"id": "1", "title": "Known", "synopsis": "A complete synopsis"}, detail=True)
    m.save_state(state, tmp_path / "state")
    class Preview(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": "1", "title": "Known", "synopsis": "A complete..."}], None)
        def detail(self, client, record):
            raise m.BudgetExceeded("request budget")
    m.run_source(Preview(), args(tmp_path), client=Client())
    result = m.load_state("naver", tmp_path / "state")
    assert result["records"]["1"]["synopsis"] == "A complete synopsis"
    assert result["progress"]["pending_details"] == ["1"]


@pytest.mark.parametrize("malformed", [None, m.MetadataResult("success"), m.MetadataResult("unexpected"),
                                      m.MetadataResult("restricted", record=[])])
def test_malformed_detail_cannot_drop_pending_work(tmp_path, malformed):
    class Broken(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": "1", "title": "One"}], None)
        def detail(self, client, record):
            return malformed
    report = m.run_source(Broken(), args(tmp_path), client=Client())
    state = m.load_state("naver", tmp_path / "state")
    assert report["coverage"]["successful_details"] == 0
    assert state["progress"]["pending_details"] == ["1"]
    assert state["records"]["1"]["history"]["latest_outcome"] == "failed"


def test_restricted_detail_merges_available_metadata_without_erasing_history(tmp_path):
    state = m.empty_state("naver")
    m.merge_record(state, {"id": "1", "title": "Old", "synopsis": "Known synopsis"}, detail=True)
    previous_success = state["records"]["1"]["history"]["last_success"]
    m.save_state(state, tmp_path / "state")
    class Restricted(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": "1", "title": "Old"}], None)
        def detail(self, client, record):
            return m.MetadataResult("restricted", {"id": "1", "title": "Updated public title", "synopsis": None}, "Age gate")
    m.run_source(Restricted(), args(tmp_path), client=Client())
    record = m.load_state("naver", tmp_path / "state")["records"]["1"]
    assert record["title"] == "Updated public title" and record["synopsis"] == "Known synopsis"
    assert record["history"]["last_success"] == previous_success
    assert record["history"]["latest_outcome"] == "restricted"


@pytest.mark.parametrize("mode", ["catalog", "rankings"])
@pytest.mark.parametrize("interruption", ["budget", "failed_board"])
def test_resume_skips_only_successful_boards_from_same_unfinished_pass(tmp_path, mode, interruption):
    class Boards(Adapter):
        def __init__(self, interrupted=False):
            self.interrupted, self.pages, self.board_calls = interrupted, [], []
        def fetch_page(self, client, partition, page):
            self.pages.append(page)
            return m.CatalogPage([{"id": "1", "title": "One", "_detail_complete": True}], None)
        def rankings(self, client, *, skip_keys=()):
            for key in ("first", "second"):
                if key in skip_keys:
                    continue
                self.board_calls.append(key)
                if self.interrupted and key == "second":
                    if interruption == "budget":
                        raise m.BudgetExceeded("request budget")
                    yield m.RankingResult(key, key, [], success=False, error="temporary failure")
                else:
                    yield m.RankingResult(key, key, [{"id": "1", "rank": 1}])
    first = Boards(interrupted=True)
    report = m.run_source(first, args(tmp_path, "--mode", mode), client=Client())
    assert not report["coverage"]["complete"]
    second = Boards()
    report = m.run_source(second, args(tmp_path, "--mode", mode, "--resume"), client=Client())
    assert second.pages == [] and second.board_calls == ["second"]
    assert report["coverage"]["complete"]
    fresh = Boards()
    m.run_source(fresh, args(tmp_path, "--mode", "rankings"), client=Client())
    assert fresh.board_calls == ["first", "second"]


def test_new_fully_enriched_listing_clears_a_previous_failed_detail(tmp_path):
    state = m.empty_state("naver")
    m.merge_record(state, {"id": "1", "title": "Known"})
    state["progress"]["pending_details"] = ["1"]
    m.save_state(state, tmp_path / "state")
    class Enriched(Adapter):
        def fetch_page(self, client, partition, page):
            return m.CatalogPage([{"id": "1", "title": "Known", "synopsis": "Available", "_detail_complete": True}], None)
        def detail(self, client, record):
            raise AssertionError("Enriched listing should remove pending detail")
    m.run_source(Enriched(), args(tmp_path), client=Client())
    assert m.load_state("naver", tmp_path / "state")["progress"]["pending_details"] == []


def test_concurrent_requests_cannot_exceed_shared_request_budget(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    count, lock = [0], threading.Lock()
    def request(session, method, url, **kwargs):
        with lock:
            count[0] += 1
        response = requests.Response()
        response.status_code, response._content, response._content_consumed = 200, b"{}", True
        response.url = url
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    client = m.AnonymousClient(Adapter(), max_requests=3, delay=0)
    def fetch(_):
        try:
            client.get_json("https://example.test/catalog")
            return "success"
        except m.BudgetExceeded:
            return "budget"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, range(12)))
    assert results.count("success") == count[0] == client.requests == 3
    client.close()


@pytest.mark.parametrize("raw,precision,zone", [
    ("2026-09-13", "day", None), ("20100203193035", "second", None),
    ("2026-09-13T12:30", "minute", None), ("2026-09-13T12:30:15", "second", None),
    ("2026-09-13T12:30:15+09:00", "second", "+09:00"),
    ("2026-09-13T12:30:15.123Z", "fractional_second", "UTC"),
    ("1265193035", None, None), ("unknown", None, None),
])
def test_source_date_retains_raw_value_without_inferred_timezone(raw, precision, zone):
    assert m.source_date(raw) == {"raw": raw, "precision": precision, "timezone": zone}
