"""Offline contracts using minimal representative public metadata responses."""
from datetime import datetime

import pytest

from scripts import scrape_munpia as munpia
from scripts.metadata_common import BudgetExceeded, FetchError


def envelope(result):
    return {"code": "M000_00000", "message": "OK", "result": result}


class Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get_json(self, url, params=None):
        assert munpia.MunpiaAdapter.is_allowed_url(url)
        self.calls.append((url, params))
        if self.error:
            raise self.error
        return self.response


def detail(**changes):
    info = {
        "id": 216129, "title": "테스트 작품", "authorName": "작가",
        "illustratorName": "그림 작가", "coverUrl": "https://cdn1.munpia.com/cover.jpg",
        "genres": ["대체역사", "현대판타지"], "tags": [{"id": 1, "title": "성장"}],
        "introduction": "첫 문단.\n\n둘째 문단.", "adult": False, "free": False,
        "paidSerial": True, "ebook": False, "epub": False, "rental": False,
        "finish": True, "pause": False, "viewCount": 33441712,
        "preferenceCount": 46637, "likeCount": 962889, "chapterCount": 603,
        "freeChapterCount": 25, "characters": 3571746, "unitType": "화",
        "createdAt": "2020-06-29T19:00:00", "updatedAt": "2026-03-16T19:00:00",
    }
    info.update(changes)
    return info


def test_detail_keeps_native_counts_status_and_contributor_roles():
    result = munpia.normalize_detail(detail())
    assert result["id"] == "216129"
    assert result["canonical_url"] == "https://www.munpia.com/novel/detail/216129"
    assert result["views"] == 33441712
    assert result["likes"] == 962889
    assert result["episodes"] == 603
    assert result["metrics"] == {"favorites": 46637, "characters": 3571746,
                                 "free_episodes": 25, "episode_unit": "화"}
    assert result["complete"] == 1 and result["age"] == 0 and result["tier"] == "paid"
    assert result["contributors"][1] == {"name": "그림 작가", "role": "illustrator"}
    assert result["tags"] == ["대체역사", "현대판타지", "성장"]
    assert "\n\n" in result["synopsis"]
    assert result["source_dates"]["updated"] == {"raw": "2026-03-16T19:00:00", "precision": "second", "timezone": None}
    date_only = munpia.normalize_detail(detail(createdAt="2020-06-29", updatedAt="2026-03-16T19:00:00+09:00"))
    assert date_only["source_dates"]["published"]["precision"] == "day"
    assert date_only["source_dates"]["updated"]["timezone"] == "+09:00"


def test_optional_absence_stays_unknown_and_ebook_count_keeps_unit():
    sparse = munpia.normalize_detail({"id": 1, "title": "제목"})
    for key in ("views", "likes", "episodes", "age", "complete", "updated", "tier", "synopsis"):
        assert sparse[key] is None
    ebook = munpia.normalize_detail(detail(ebook=True, unitType="권", chapterCount=8))
    assert ebook["tier"] == "ebook" and ebook["metrics"]["episode_unit"] == "권"
    assert ebook["episodes"] == 8
    assert munpia.number(float("inf")) is None


def test_catalog_uses_observed_novel_title_shape_and_pagination_only():
    client = Client(envelope({"items": [{"novelId": 603016, "authorName": "HADAL",
        "novelTitle": "무림맹 묘지기", "groupTitle": "", "badges": {"isAdult": False}}],
        "total": 86711, "hasNext": True, "policy": "SEARCH_BLOCKED_POLICY_DEFAULT"}))
    adapter = munpia.MunpiaAdapter()
    page = adapter.fetch_page(client, adapter.partitions(client)[0], 0)
    assert page.next_page == 1
    assert page.records == [{"id": "603016", "title": "무림맹 묘지기", "author": "HADAL",
                            "canonical_url": "https://www.munpia.com/novel/detail/603016", "tier": None}]
    assert client.calls[0][1]["size"] == 40
    assert client.calls[0][1]["adultMode"] == "false"
    assert "views" not in page.records[0]
    client.response["result"]["hasNext"] = False
    assert adapter.fetch_page(client, {}, 1).next_page is None


@pytest.mark.parametrize("payload", [
    {"code": "ERROR", "result": {}}, envelope({"items": [], "hasNext": True}),
    envelope({"items": [{"novelId": 1, "novelTitle": ""}], "hasNext": False}),
    envelope({"items": [], "hasNext": "false"}),
])
def test_broken_catalog_is_not_successful_enumeration(payload):
    with pytest.raises(ValueError):
        munpia.MunpiaAdapter().fetch_page(Client(payload), {}, 0)


@pytest.mark.parametrize("status,expected", [(401, "restricted"), (403, "restricted"),
                                            (404, "unavailable"), (410, "unavailable"), (500, "failed")])
def test_detail_classifies_http_failures_without_chapter_or_login_fallback(status, expected):
    client = Client(error=FetchError("Metadata request failed", status_code=status))
    result = munpia.MunpiaAdapter().detail(client, {"id": "216129"})
    assert result.status == expected and result.record is None
    assert len(client.calls) == 1


def test_detail_rejects_identity_mismatch_and_ignores_reader_hints():
    client = Client(envelope({"novelInfo": detail(), "chapterViewInfo": {"chapterId": 123}, "login": False}))
    adapter = munpia.MunpiaAdapter()
    result = adapter.detail(client, {"id": "216129"})
    assert result.status == "success" and "chapterViewInfo" not in result.record
    assert adapter.detail(client, {"id": "99"}).status == "failed"
    assert len(client.calls) == 2


@pytest.mark.parametrize("url", [
    "https://www.munpia.com/api/v1/pc/novel-detail/216129/chapters",
    "https://www.munpia.com/api/key-exchange", "https://www.munpia.com/login",
    "https://www.munpia.com/novel/216129/page/1", "http://www.munpia.com/api/v1/main/best24",
    "https://www.munpia.com.evil.test/api/v1/main/best24",
    "https://user@www.munpia.com/api/v1/main/best24",
    "https://www.munpia.com:invalid/api/v1/main/best24",
])
def test_endpoint_allowlist_rejects_nonmetadata_and_foreign_routes(url):
    assert not munpia.MunpiaAdapter.is_allowed_url(url)


def test_rankings_preserve_source_positions_and_more_than_100_rows(monkeypatch):
    class FrozenDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 13, 23, 30, tzinfo=munpia.KOREA)
    monkeypatch.setattr(munpia, "datetime", FrozenDate)
    rows = [{"novelId": index + 1, "rank": index + 1, "title": "작품", "author": "작가",
             "viewCount": 99, "preferCount": 17, "score": 7, "adult": False}
            for index in range(103)]
    client = Client(envelope({"total": 1, "hours": [{"hour": 22, "novels": rows}]}))
    boards = list(munpia.MunpiaAdapter().rankings(client))
    assert [board.key for board in boards] == [entry[0] for entry in munpia.BOARDS]
    assert all(board.success and len(board.records) == 103 for board in boards)
    last = boards[0].records[-1]
    assert last["id"] == "103" and last["rank"] == 103
    assert last["title"] == "작품" and last["author"] == "작가"
    assert last["metrics"] == {"views": 99, "favorites": 17, "score": 7}
    assert last["window"] == {"start": "2026-09-13T22:00:00", "end": "2026-09-13T22:00:00",
                              "timezone": "Asia/Seoul", "hour": 22}
    assert "views" not in last and "likes" not in last
    assert boards[0].observed_at == "2026-09-13T13:00:00+00:00"
    assert len(client.calls) == 3
    assert all(call[1]["startDateTime"] == call[1]["endDateTime"] == "2026-09-13T22:00:00"
               for call in client.calls)


def test_empty_rankings_are_failure_and_request_budget_propagates():
    boards = list(munpia.MunpiaAdapter().rankings(Client(envelope({"hours": []}))))
    assert all(not board.success for board in boards)
    client = Client(error=BudgetExceeded("request limit"))
    with pytest.raises(BudgetExceeded):
        munpia.MunpiaAdapter().detail(client, {"id": "1"})
    with pytest.raises(BudgetExceeded):
        list(munpia.MunpiaAdapter().rankings(client))


def test_completed_native_boards_are_skipped_without_requests():
    client = Client(error=AssertionError("No request expected"))
    assert list(munpia.MunpiaAdapter().rankings(client, skip_keys={key for key, _ in munpia.BOARDS})) == []
    assert client.calls == []
