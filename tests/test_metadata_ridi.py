"""Offline contracts based on Ridibooks' public category API frontend schema."""
import copy
from urllib.parse import urlencode

import pytest

from scripts.metadata_common import FetchError
from scripts.scrape_ridi import API, CATEGORIES, RidiAdapter, normalize
from scripts import metadata_common as common
from scripts.scrape_ridi import browser_requests
import requests


def item(ident=123):
    return {"book": {"bookId": str(ident), "title": "Title episode 7",
        "serial": {"serialId": "series", "title": "Whole novel", "total": 75, "completion": False,
                   "cover": {"large": "https://img.ridicdn.net/cover/123/large"}},
        "authors": [{"name": "Writer", "role": "author"}, {"name": "Artist", "role": "illustrator"}],
        "categories": [{"name": "Fantasy", "useSeries": "Y"}], "adultsOnly": False,
        "ratings": [{"rating": 4, "count": 3}, {"rating": 5, "count": 1}],
        "introduction": {"description": "First line<br>Second line"}}}


class Client:
    def __init__(self, total=61):
        self.total = total
        self.calls = []
    def get_json(self, url, params=None):
        self.calls.append((url, copy.deepcopy(params)))
        assert RidiAdapter.is_allowed_url(url + "?" + urlencode(params))
        if url.endswith("total-count"):
            return {"data": {"totalCount": self.total}}
        return {"data": {"items": [item(i + 1) for i in range(params["offset"], min(self.total, params["offset"] + params["limit"]))]}}


def test_normalization_uses_whole_work_and_native_rating_without_invented_views():
    record = normalize(item(), "webnovel")
    assert record["id"] == "123" and record["title"] == "Whole novel"
    assert record["author"] == "Writer" and len(record["contributors"]) == 2
    assert record["episodes"] == 75 and record["complete"] == 0 and record["age"] == 0
    assert record["metrics"] == {"rating": 4.25, "rating_count": 4}
    assert record["views"] is record["likes"] is record["updated"] is None
    assert record["synopsis"] == "First line\nSecond line" and record["_detail_complete"]
    assert record["canonical_url"] == "https://ridibooks.com/books/123"


def test_sparse_values_remain_unknown():
    record = normalize({"book": {"bookId": "1", "title": "Title"}}, "webnovel")
    assert record["episodes"] is record["complete"] is record["age"] is None
    assert record["metrics"] == {"rating": None, "rating_count": None}
    assert not record["_detail_complete"]


def test_catalog_offsets_and_four_genres():
    a, c = RidiAdapter(), Client()
    partitions = a.partitions(c)
    assert {p["category"] for p in partitions} == set(CATEGORIES)
    first = a.fetch_page(c, partitions[0], 1)
    second = a.fetch_page(c, partitions[0], 2)
    assert len(first.records) == 60 and first.next_page == 2 and first.observed_total == 61
    assert len(second.records) == 1 and second.next_page is None
    assert c.calls[-1][1]["offset"] == 60
    assert c.calls[-1][1]["order_by"] == "recent"


def test_rankings_are_explicit_periods_and_top_100():
    c = Client(105)
    boards = list(RidiAdapter().rankings(c))
    assert len(boards) == 8 and all(b.success for b in boards)
    assert [r["rank"] for r in boards[0].records] == list(range(1, 101))
    assert {p["period"] for _, p in c.calls} == {"weekly", "monthly"}
    assert all(p["tab"] == "bestsellers" and "order_by" not in p for _, p in c.calls)


def test_restriction_and_malformed_pages_do_not_report_empty_success():
    class Restricted(Client):
        def get_json(self, *args, **kwargs):
            raise FetchError("Metadata endpoint returned HTTP 403", 403)
    a = RidiAdapter()
    result = a.fetch_page(Restricted(), a.partitions(None)[0], 1)
    assert not result.complete and "403" in result.error
    class Truncated(Client):
        def get_json(self, url, params=None):
            return {"data": {"totalCount": 60}} if url.endswith("total-count") else {"data": {"items": [item()]}}
    assert not RidiAdapter().fetch_page(Truncated(), a.partitions(None)[0], 1).complete


@pytest.mark.parametrize("url", ["https://ridibooks.com/account/login", "https://api.ridibooks.com/v1/books/123/reader",
    API + "?token=secret", API + "?adults_only=1", "https://user@api.ridibooks.com/v2/category/books", API.replace("https:", "http:")])
def test_allowlist_excludes_accounts_readers_and_unexpected_parameters(url):
    assert not RidiAdapter.is_allowed_url(url)


def test_browser_transport_preserves_bounded_retries_and_pacing(monkeypatch):
    now, starts, config = [0.0], [], {}
    class Session:
        headers = {}
        trust_env = True
        closed = False
        def get(self, url, **kwargs):
            assert not self.trust_env and kwargs["allow_redirects"] is False
            starts.append(now[0])
            if len(starts) == 1:
                raise browser_requests.exceptions.RequestException("network failure")
            response = requests.Response()
            response.url = url
            response.status_code = 429 if len(starts) == 2 else 200
            response.headers["Retry-After"] = "3"
            response._content = b'{"data":{"totalCount":0}}'
            response._content_consumed = True
            return response
        def close(self):
            self.closed = True
    session = Session()
    def factory(**kwargs):
        config.update(kwargs)
        return session
    monkeypatch.setattr(browser_requests, "Session", factory)
    client = common.AnonymousClient(RidiAdapter(), max_requests=3, clock=lambda: now[0],
                                   sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert client.get_json(API)["data"]["totalCount"] == 0
    assert config == {"impersonate": "chrome", "trust_env": False}
    assert "User-Agent" not in session.headers  # Retain curl's matching browser header.
    assert starts == [0, 1, 4] and client.requests == 3
    with pytest.raises(common.BudgetExceeded):
        client.get_json(API)
    client.close()
    assert session.closed


def test_browser_transport_does_not_follow_account_redirect(monkeypatch):
    class Session:
        headers = {}
        trust_env = False
        def get(self, url, **kwargs):
            response = requests.Response()
            response.url = url
            response.status_code = 302
            response.headers["Location"] = "https://ridibooks.com/account/login"
            response._content = b""
            response._content_consumed = True
            return response
        def close(self):
            pass
    monkeypatch.setattr(browser_requests, "Session", lambda **kwargs: Session())
    client = common.AnonymousClient(RidiAdapter(), max_requests=2)
    with pytest.raises(FetchError, match="allowlist"):
        client.get_json(API)
    assert client.requests == 1
    client.close()


def test_deep_catalog_limit_stays_incomplete_without_retrying_invalid_offset():
    adapter, client = RidiAdapter(), Client()
    result = adapter.fetch_page(client, {"category": "1750"}, 101)
    assert not result.complete and "offset below 6000" in result.error
    assert not client.calls
