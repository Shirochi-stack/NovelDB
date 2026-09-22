"""Offline checks against small public work-metadata HTML fragments."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from scripts.metadata_common import BudgetExceeded, FetchError
from scripts.scrape_naver import NaverAdapter, parse_catalog, parse_detail, parse_ranking


FIXTURES = Path(__file__).parent / "fixtures" / "metadata" / "naver"
PARTITION = {"tier": "best", "genre": "102", "genre_name": "판타지", "finish": False}
CANONICAL = "https://novel.naver.com/best/list?novelId=1161705"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get_text(self, url, params=None):
        self.calls.append((url, params))
        result = self.handler(url, params)
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, url, params=None):
        return SimpleNamespace(text=self.get_text(url, params), url=url)


def test_catalog_exact_counts_and_smallest_next_page():
    result = parse_catalog(fixture("catalog.html"), PARTITION, 1)
    assert result.complete and result.error is None
    assert result.next_page == 2  # The next group arrow points to page 11.
    assert [row["id"] for row in result.records] == ["1161705", "1235715"]
    record = result.records[0]
    assert record["canonical_url"] == CANONICAL
    assert record["episodes"] == 47
    assert record["metrics"] == {"favorites": 14, "rating": 9.67, "rating_scale": 10}
    assert record["views"] is record["likes"] is record["age"] is record["updated"] is None
    assert record["complete"] is None
    assert not record.get("_detail_complete")


def test_recommendation_cards_do_not_enter_catalog():
    recommendation = '<ul class="carousel_list"><li><a class="link" href="/best/list?novelId=999"><span class="title">Recommendation</span></a></li></ul>'
    result = parse_catalog(recommendation + fixture("catalog.html"), PARTITION, 1)
    assert "999" not in {row["id"] for row in result.records}


def test_missing_catalog_fails_but_explicit_empty_catalog_is_terminal():
    assert not parse_catalog("<html>Temporary failure</html>", PARTITION, 1).complete
    empty = '<div id="content"><ul class="card_list"></ul><p>등록된 작품이 없습니다.</p></div>'
    result = parse_catalog(empty, PARTITION, 1)
    assert result.complete and result.records == [] and result.next_page is None
    malformed = '<div id="content"><ul class="card_list"></ul></div>'
    assert not parse_catalog(malformed, PARTITION, 1).complete


def test_catalog_rejects_reader_link_and_abbreviated_metrics_remain_unknown():
    html = fixture("catalog.html")
    assert not parse_catalog(html.replace("/best/list?novelId=1161705", "/best/detail?novelId=1161705&volumeNo=1"), PARTITION, 1).complete
    result = parse_catalog(html.replace('>14<', '>1만<'), PARTITION, 1)
    assert result.records[0]["metrics"]["favorites"] is None


def test_detail_uses_work_metadata_and_declared_episode_total():
    previous = parse_catalog(fixture("catalog.html"), PARTITION, 1).records[0]
    html = fixture("detail.html") + '<div class="date">2099.01.01</div><div class="recommended"><span class="bullet_comp">완결</span></div>'
    result = parse_detail(html, previous, CANONICAL)
    assert result.status == "success"
    record = result.record
    assert record["title"] == "어느 날 능력자가 되었다"
    assert record["author"] == "루온RUON"
    assert record["contributors"] == [{"name": "루온RUON", "role": "author"}]
    assert record["genres"] == ["판타지"] and "능력자" in record["keywords"]
    assert "더보기" not in record["synopsis"]
    assert record["episodes"] == 47
    assert record["complete"] is record["updated"] is record["likes"] is None
    assert record["_detail_complete"] is True


def test_detail_migration_requires_same_id_and_updates_canonical_tier():
    result = parse_detail(fixture("detail.html"), {"id": "1161705"}, "https://novel.naver.com/webnovel/list?novelId=1161705")
    assert result.status == "success" and result.record["tier"] == "webnovel"
    wrong = parse_detail(fixture("detail.html"), {"id": "1161705"}, "https://novel.naver.com/best/list?novelId=999")
    assert wrong.status == "failed" and wrong.record is None


def test_verification_page_is_restricted_and_does_not_replace_existing_metadata():
    result = parse_detail('<div id="content">성인 인증이 필요합니다.</div>', {"id": "1161705", "title": "Known title"}, CANONICAL)
    assert result.status == "restricted" and result.record is None


def test_deleted_work_is_terminal_not_a_retryable_failure():
    # Naver answers a taken-down work with HTTP 200 and an empty container
    # holding only this alert. Scoring it "failed" kept resumed runs retrying
    # it forever, so a pass could never complete.
    page = ('<div id="container">'
            '<script type="text/javascript">alert("삭제된 게시물입니다."); history.back();</script>'
            '</div>')
    result = parse_detail(page, {"id": "1161705", "title": "Known title"}, CANONICAL)
    assert result.status == "unavailable" and result.record is None


def test_maintenance_banner_is_not_mistaken_for_a_deletion():
    # gRosAlertMessage ships on every page, including healthy ones; a planned
    # outage must stay retryable.
    page = ('<script>var gRosAlertMessage = \'정기점검중 입니다.\';</script>'
            '<div id="container"></div>')
    result = parse_detail(page, {"id": "1161705", "title": "Known title"}, CANONICAL)
    assert result.status == "failed"


def test_outbound_purchase_link_is_recorded_without_fetching_it():
    html = fixture("detail.html").replace('<div class="link_group">', '<div class="link_group"><a href="https://series.naver.com/novel/detail.series?productNo=123">구매</a>')
    client = FakeClient(lambda url, params: html)
    result = NaverAdapter().detail(client, {"id": "1161705", "canonical_url": CANONICAL})
    assert result.record["purchase_url"] == "https://series.naver.com/novel/detail.series?productNo=123"
    assert client.calls == [(CANONICAL, None)]


def test_ranking_uses_explicit_positions_and_separates_free_and_paid():
    results = parse_ranking(fixture("ranking.html"), "102", "DAILY", "2026-09-13T00:00:00Z")
    assert [board.key for board in results] == ["best_102_daily_free", "best_102_daily_paid"]
    assert all(board.success for board in results)
    assert [[row["rank"] for row in board.records] for board in results] == [[1, 2], [1, 2]]
    assert results[0].records[0]["id"] == "1241498"
    assert results[1].records[0]["id"] == "1150988"


def test_missing_or_duplicate_native_rank_fails_board_without_fabricating_order():
    html = fixture("ranking.html").replace('<em class="rank">1</em>', '<em class="rank">?</em>', 1)
    # Keep the assertion independent of the platform's tag name around rank text.
    if html == fixture("ranking.html"):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        soup.select_one(".ranking_wrap_left .ranking .rank").string = "?"
        html = str(soup)
    boards = parse_ranking(html, "102", "WEEKLY", "observed")
    assert boards[0].success is False and boards[0].records == []
    assert boards[1].success is True
    missing = parse_ranking("<html></html>", "102", "DAILY", "observed")
    assert all(not board.success for board in missing)


def test_genres_are_discovered_per_tier_without_using_section_labels():
    def page(url, params):
        tier = url.split("/")[-2]
        return f'''<a href="/{tier}/genre?genre=101">시리즈에디션</a>
            <a href="/{tier}/genreMain?genre=101">로맨스</a>
            <a href="/{tier}/genre?genre=102">판타지</a>
            <a href="/{tier}/genre?genre=111">자유</a>
            <a href="/{tier}/genre?genre=102&amp;order=Update">최신순</a>
            <ul class="card_list"></ul>'''
    client = FakeClient(page)
    partitions = NaverAdapter().partitions(client)
    assert len(client.calls) == 3 and len(partitions) == 18
    assert {p["tier"] for p in partitions} == {"challenge", "best", "webnovel"}
    assert {p["genre_name"] for p in partitions if p["genre"] == "101"} == {"로맨스"}
    assert {p["finish"] for p in partitions} == {False, True}
    with pytest.raises(FetchError):
        NaverAdapter().partitions(FakeClient(lambda *_: "maintenance"))


def test_adapter_requests_explicit_ranking_genre_and_period():
    client = FakeClient(lambda *_: fixture("ranking.html"))
    boards = list(NaverAdapter().rankings(client))
    assert len(boards) == 28 and len(client.calls) == 14
    assert {params["periodType"] for _, params in client.calls} == {"DAILY", "WEEKLY"}
    assert {params["genre"] for _, params in client.calls} == {"101", "109", "102", "110", "103", "104", "106"}
    assert all(NaverAdapter.is_allowed_url(url + "?" + urlencode(params)) for url, params in client.calls)


def test_completed_ranking_pairs_skip_requests_and_partial_pairs_keep_only_pending_side():
    all_keys = {f"best_{genre}_{period}_{kind}" for genre in ("101", "109", "102", "110", "103", "104", "106")
                for period in ("daily", "weekly") for kind in ("free", "paid")}
    client = FakeClient(lambda *_: fixture("ranking.html"))
    assert list(NaverAdapter().rankings(client, skip_keys=all_keys)) == [] and not client.calls
    pending = "best_102_daily_paid"
    boards = list(NaverAdapter().rankings(client, skip_keys=all_keys - {pending}))
    assert [board.key for board in boards] == [pending] and len(client.calls) == 1


@pytest.mark.parametrize("path", [
    "https://novel.naver.com/best/detail?novelId=1&volumeNo=1",
    "https://series.naver.com/novel/detail.series?productNo=1",
    "https://nid.naver.com/nidlogin.login", "https://novel.naver.com/best/list?novelId=1&token=secret",
    "https://novel.naver.com/best/list?novelId=1&token=", "https://:pass@novel.naver.com/best/list?novelId=1",
    "https://novel.naver.com:invalid/best/list?novelId=1",
    "https://novel.naver.com.evil.test/best/list?novelId=1", "http://novel.naver.com/best/list?novelId=1",
])
def test_allowlist_excludes_readers_authentication_and_external_hosts(path):
    assert not NaverAdapter.is_allowed_url(path)


@pytest.mark.parametrize("status,expected", [(404, "unavailable"), (403, "restricted"), (503, "failed")])
def test_detail_http_outcomes(status, expected):
    client = FakeClient(lambda *_: FetchError("safe message", status_code=status))
    result = NaverAdapter().detail(client, {"id": "1161705", "canonical_url": CANONICAL})
    assert result.status == expected


def test_budget_is_not_disguised_as_empty_catalog_or_failed_board():
    client = FakeClient(lambda *_: BudgetExceeded("sample limit"))
    with pytest.raises(BudgetExceeded):
        NaverAdapter().fetch_page(client, PARTITION, 1)
    with pytest.raises(BudgetExceeded):
        NaverAdapter().detail(client, {"id": "1161705", "canonical_url": CANONICAL})
    with pytest.raises(BudgetExceeded):
        next(NaverAdapter().rankings(client))
