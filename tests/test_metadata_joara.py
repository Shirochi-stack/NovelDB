"""Offline Joara public-configuration, pagination and metadata checks."""

import copy
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scripts.metadata_common import BudgetExceeded, FetchError
from scripts.scrape_joara import JoaraAdapter, normalize_listing, parse_detail


FIXTURES = Path(__file__).parent / "fixtures" / "metadata" / "joara"
PARTITION = {"tier": "series", "store": "series", "catalog": "latest", "page_size": 2}


def test_adult_verification_response_marks_age_without_fabricating_synopsis():
    result = parse_detail({"status": 0, "message": "본 작품은 성인 콘텐츠가 포함되어 있습니다. 인증을 위해 로그인 후 이용하시기 바랍니다."},
                          {"id": "976754", "title": "Known title"})
    assert result.status == "restricted" and result.record["age"] == 19
    assert result.record["title"] == "Known title" and "synopsis" not in result.record


def test_latest_cursor_response_has_no_page_field_and_keeps_api_page_one():
    payload = fixture("catalog.json")
    payload.pop("page", None)
    payload.update(total_cnt=10000, cursor_point="next-opaque-cursor")
    client = FakeClient(payload)
    partition = {**PARTITION, "pagination": "joara-cursor-v1", "cursor_point": "previous-cursor"}
    result = JoaraAdapter().fetch_page(client, partition, 101)
    assert result.complete and result.next_page == 102
    assert result.next_cursor == "next-opaque-cursor"
    params = client.calls[-1][1]
    assert params["page"] == 1 and params["use_cursor_pagination"] == "y"
    assert params["cursor_point"] == "previous-cursor"


def test_cursor_rejects_a_truncated_batch_before_its_reported_end():
    payload = fixture("catalog.json")
    payload.update(total_cnt=10000, cursor_point="next")
    payload["data"]["list"] = payload["data"]["list"][:1]
    result = JoaraAdapter().fetch_page(FakeClient(payload), {**PARTITION, "pagination": "joara-cursor-v1"}, 101)
    assert not result.complete and "row count" in result.error


def fixture(name):
    content = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(content) if name.endswith(".json") else content


class FakeClient:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def get_text(self, url, params=None):
        self.calls.append((url, params))
        if url == "https://www.joara.com/":
            return fixture("bootstrap.html")
        if url == "https://www.joara.com/static/js/main.fixture123.chunk.js":
            return fixture("bootstrap.js")
        raise AssertionError("Unexpected public asset request")

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        result = self.response(url, params) if callable(self.response) else copy.deepcopy(self.response)
        if isinstance(result, Exception):
            raise result
        return result


def test_bootstrap_reads_current_asset_config_once_and_never_adds_account_token():
    client = FakeClient(fixture("catalog.json"))
    adapter = JoaraAdapter()
    partitions = adapter.partitions(client)
    result = adapter.fetch_page(client, PARTITION, 1)
    assert result.complete and len(partitions) == 8
    assert {p["tier"] for p in partitions} == {"series", "nobless", "premium"}
    assert len(client.calls) == 3
    params = client.calls[-1][1]
    assert params["api_key"] == "TEST_PUBLIC_CONFIG_NOT_A_CREDENTIAL"
    assert params["device"] == "mw" and params["ver"] == "3.2.0"
    assert params["deviceuid"] and "token" not in params
    assert all(adapter.is_allowed_url(url + ("?" + urlencode(query) if query else "")) for url, query in client.calls)
    adapter.fetch_page(client, PARTITION, 1)
    assert len(client.calls) == 4


def test_unrecognized_public_bundle_stops_instead_of_using_a_static_key():
    class ChangedClient(FakeClient):
        def get_text(self, url, params=None):
            return "<html></html>" if url.endswith("/") else "changed"
    with pytest.raises(FetchError, match="main bundle"):
        JoaraAdapter().partitions(ChangedClient())
    client = FakeClient()
    original = client.get_text
    client.get_text = lambda url, params=None: "changed" if url.endswith(".js") else original(url, params)
    with pytest.raises(FetchError, match="configuration changed"):
        JoaraAdapter().partitions(client)


def test_catalog_keeps_exact_native_metrics_types_and_pagination():
    payload = fixture("catalog.json")
    client = FakeClient(payload)
    result = JoaraAdapter().fetch_page(client, PARTITION, 1)
    assert result.complete and result.next_page == 2
    record, native = result.records[0], payload["data"]["list"][0]
    assert record["id"] == str(native["book_code"])
    assert record["title"] == native["subject"]
    assert record["author"] == native["member_name"]
    assert record["contributors"] == [{"name": native["member_name"], "role": "author"}]
    assert record["source_dates"]["updated"] == {"raw": native["last_regist_datetime"], "precision": "second", "timezone": None}
    assert record["canonical_url"] == f'https://www.joara.com/book/{native["book_code"]}'
    assert record["metrics"]["recommendations"] == native["recommend_count"]
    assert record["metrics"]["favorites"] == native["favorite_count"]
    assert record["views"] == native["page_read"] and record["likes"] is None
    assert record["complete"] == 0 and record["age"] == 0
    assert "T" in record["updated"] and record["_detail_complete"]


def test_sparse_metadata_stays_unknown_and_needs_detail():
    record = normalize_listing({"book_code": 12, "subject": "A <novel title>", "chkfinish": "FALSE"})
    assert record["title"] == "A <novel title>"
    assert record["complete"] == 0
    assert record["views"] is record["likes"] is record["episodes"] is record["age"] is None
    assert record["metrics"] == {"favorites": None, "recommendations": None}
    assert record["updated"] is None and not record["_detail_complete"]


def test_listing_intro_at_observed_character_limit_needs_detail_enrichment():
    native = fixture("ranking.json")["data"]["list"][1]
    assert len(native["intro"]) == 1000
    record = normalize_listing(native)
    assert record["synopsis"] == native["intro"].replace("\r\n", "\n").strip()
    assert not record["_detail_complete"]


@pytest.mark.parametrize("change", [
    lambda p: p.update(status=0),
    lambda p: p.update(page=2),
    lambda p: p.update(offset=0),
    lambda p: p.update(data={"list": []}),
])
def test_malformed_or_partial_catalog_does_not_report_terminal_success(change):
    payload = fixture("catalog.json")
    change(payload)
    result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 1)
    assert not result.complete and result.records == [] and result.error


def test_explicit_zero_total_is_successful_empty_catalog():
    payload = {"status": 1, "page": 1, "offset": 20, "total_cnt": 0, "data": {"list": []}}
    result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 1)
    assert result.complete and result.next_page is None and result.records == []


def test_v1_detail_false_flags_and_dates_are_not_truthy_strings_or_epoch_guesses():
    result = parse_detail(fixture("detail.json"), {"id": "412770", "tier": "series", "genres": ["판타지"]})
    assert result.status == "success"
    record = result.record
    assert record["title"] == "Book" and record["author"] == "낙화花"
    assert record["complete"] == 0 and record["age"] == 0
    assert record["episodes"] == 3 and record["views"] == 7
    assert record["metrics"]["recommendations"] == 0
    assert record["metrics"]["favorites"] == 1
    assert "characters" not in record["metrics"]
    assert record["created"] == "2010-02-02T14:47:06"
    assert record["updated"] == "2010-02-03T19:30:35"
    assert record["synopsis"] == "판타지판타지!"
    assert record["contributors"] == [{"name": "낙화花", "role": "author"}]
    assert record["source_dates"]["updated"] == {"raw": "20100203193035", "precision": "second", "timezone": None}
    assert record["source_dates"]["redate"]["precision"] is None


def test_restricted_synopsis_preserves_known_public_metadata():
    payload = fixture("detail.json")
    payload["book"]["intro"] = "성인 인증이 필요합니다."
    result = parse_detail(payload, {"id": "412770", "synopsis": "Previously public synopsis"})
    assert result.status == "restricted"
    assert result.record["synopsis"] == "Previously public synopsis"
    assert not result.record["_detail_complete"]
    row = {"book_code": 12, "subject": "Known title", "intro": "로그인이 필요합니다."}
    assert normalize_listing(row)["synopsis"] is None


def test_detail_identity_mismatch_and_missing_values_do_not_replace_known_fields():
    assert parse_detail(fixture("detail.json"), {"id": "999"}).status == "failed"
    payload = fixture("detail.json")
    payload["book"].update(intro="", book_img="", updated="not a date", cnt_page_read=None)
    result = parse_detail(payload, {"id": "412770", "synopsis": "Known", "views": 7, "updated": "2010-02-03T19:30:35"})
    assert result.record["synopsis"] == "Known" and result.record["views"] == 7
    assert result.record["updated"] == "2010-02-03T19:30:35"


def test_native_best_boards_have_ordinal_positions_and_explicit_periods():
    payload = fixture("ranking.json")
    payload["total_cnt"] = 2  # Bound this offline board to the two retained native rows.
    client = FakeClient(payload)
    boards = list(JoaraAdapter().rankings(client))
    assert [b.key for b in boards] == ["all_today", "all_weekly", "all_monthly"]
    assert all(b.success and [r["rank"] for r in b.records] == [1, 2] for b in boards)
    calls = [(url, params) for url, params in client.calls if url.startswith("https://api.")]
    assert {p["best"] for _, p in calls} == {"today", "weekly", "monthly"}
    assert all(p["category"] == "0" and p["store"] == "all" and p["orderby"] == "cnt_best" for _, p in calls)
    assert all(url.endswith("/v2/book/best_book") for url, _ in calls)


def test_completed_boards_skip_bootstrap_and_metadata_requests():
    client = FakeClient()
    assert list(JoaraAdapter().rankings(client, skip_keys={"all_today", "all_weekly", "all_monthly"})) == []
    assert client.calls == []


def test_concurrent_bootstrap_uses_one_fresh_public_configuration():
    from concurrent.futures import ThreadPoolExecutor
    import time
    client = FakeClient()
    get_text = client.get_text
    def slow_asset(url, params=None):
        time.sleep(0.01)
        return get_text(url, params)
    client.get_text = slow_asset
    adapter = JoaraAdapter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: adapter.partitions(client), range(4)))
    assert all(len(partitions) == 8 for partitions in results)
    assert len(client.calls) == 2


def test_failed_board_is_distinct_from_native_empty_board():
    payload = {"status": 1, "page": 1, "offset": 100, "total_cnt": 0, "data": {"list": []}}
    def response(url, params):
        return payload if params["best"] == "weekly" else FetchError("upstream unavailable", 503)
    boards = list(JoaraAdapter().rankings(FakeClient(response)))
    assert [b.success for b in boards] == [False, True, False]
    assert boards[1].records == [] and boards[1].error is None
    assert boards[0].records == [] and boards[0].error


def test_native_rank_positions_continue_across_pages_and_reject_page_size_changes():
    payload = fixture("ranking.json")
    payload["total_cnt"] = 4
    def response(url, params):
        result = copy.deepcopy(payload)
        result["page"] = params["page"]
        if params["page"] == 2:
            for row in result["data"]["list"]:
                row["book_code"] += 100
        return result
    boards = list(JoaraAdapter().rankings(FakeClient(response)))
    assert all(b.success and [r["rank"] for r in b.records] == [1, 2, 3, 4] for b in boards)
    def changing_response(url, params):
        result = response(url, params)
        if params["page"] == 2:
            result["offset"] = 3
        return result
    assert all(not b.success for b in JoaraAdapter().rankings(FakeClient(changing_response)))


@pytest.mark.parametrize("url", [
    "https://www.joara.com/viewer/412770", "https://www.joara.com/login",
    "https://api.joara.com/v1/book/chapter.joa?book_code=412770",
    "https://api.joara.com/v1/book/detail.joa?book_code=412770&token=account",
    "https://api.joara.com/v1/book/detail.joa?book_code=412770&token=",
    "https://api.joara.com:invalid/v1/book/detail.joa", "https://:pass@api.joara.com/v1/book/detail.joa",
    "http://api.joara.com/v1/book/detail.joa?book_code=412770",
    "https://evil.test/static/js/main.fake.js", "https://api.joara.com:8443/v1/book/detail.joa",
])
def test_allowlist_rejects_readers_accounts_and_unrelated_hosts(url):
    assert not JoaraAdapter.is_allowed_url(url)


@pytest.mark.parametrize("status,expected", [(404, "unavailable"), (403, "restricted"), (503, "failed")])
def test_detail_http_outcomes(status, expected):
    result = JoaraAdapter().detail(FakeClient(FetchError("safe message", status)), {"id": "412770"})
    assert result.status == expected


def test_budget_propagates_from_catalog_detail_and_rankings():
    client = FakeClient(BudgetExceeded("request limit"))
    with pytest.raises(BudgetExceeded):
        JoaraAdapter().fetch_page(client, PARTITION, 1)
    with pytest.raises(BudgetExceeded):
        JoaraAdapter().detail(client, {"id": "412770"})
    with pytest.raises(BudgetExceeded):
        next(JoaraAdapter().rankings(client))


def test_moving_short_and_duplicate_catalog_rows_remain_usable():
    for duplicate in (False, True):
        payload = fixture("catalog.json")
        if duplicate:
            payload["data"]["list"][1] = copy.deepcopy(payload["data"]["list"][0])
        else:
            payload["data"]["list"] = payload["data"]["list"][:1]
        result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 1)
        assert result.complete and len(result.records) == 1 and result.next_page == 2
        assert result.observed_total == payload["total_cnt"]


def test_page_101_reset_reports_requested_and_returned_boundaries():
    payload = {"status": 1, "page": 1, "offset": 0, "total_cnt": 0, "data": {"list": []}}
    result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 101)
    assert not result.complete and result.next_page is None
    assert "requested=101" in result.error and "returned=1" in result.error and "total=0" in result.error


@pytest.mark.parametrize("title", ["", "  "])
def test_blank_title_omits_only_that_row_and_keeps_pagination(title):
    payload = fixture("catalog.json")
    payload["data"]["list"][1]["subject"] = title
    skipped_id = str(payload["data"]["list"][1]["book_code"])
    result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 1)
    assert result.complete and result.next_page == 2 and not result.error
    assert len(result.records) == 1
    assert result.skipped_rows == [{"row": 2, "id": skipped_id,
                                    "error": "Title unavailable in public catalog"}]


@pytest.mark.parametrize("changes", [{"book_code": None}, {"subject": None}, {"subject": 123}])
def test_malformed_catalog_identity_or_schema_still_stops_page(changes):
    payload = fixture("catalog.json")
    payload["data"]["list"][1].update(changes)
    result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 1)
    assert not result.complete and not result.records and result.next_page is None
    assert "row 2" in result.error


def test_entirely_titleless_catalog_page_does_not_hide_response_failure():
    payload = fixture("catalog.json")
    for row in payload["data"]["list"]:
        row["subject"] = ""
    result = JoaraAdapter().fetch_page(FakeClient(payload), PARTITION, 1)
    assert not result.complete and result.next_page is None
    assert "No usable titled rows" in result.error


def test_verified_supplemental_categories_are_passed_to_catalog():
    client = FakeClient()
    adapter = JoaraAdapter()
    supplemental = [p for p in adapter.partitions(client) if p.get("category")]
    assert {p["category"] for p in supplemental} == {"22", "9"}
    for partition in supplemental:
        adapter.fetch_page(client, partition, 1)
        assert client.calls[-1][1]["category"] == partition["category"]
