"""Offline checks for search pagination failures and response diagnostics."""

import importlib
import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import npia_search_http as http


class Output(io.StringIO):
    def reconfigure(self, **kwargs):
        pass


@pytest.fixture(params=["scrape_npia", "rescrape_npia_noauth"])
def scraper(request):
    with patch.object(sys, "stdout", Output()):
        return importlib.import_module("scripts." + request.param)


@pytest.mark.parametrize("fail_page", [1, 2])
def test_failed_page_retains_completed_pages(scraper, monkeypatch, capsys, fail_page):
    batch = [{"novel_no": 123}] * 15000
    pages = []

    def fetch(session, params, *args):
        pages.append(params["page"])
        if params["page"] == fail_page:
            raise RuntimeError("HTTP 503; preview='maintenance'")
        return {"list": batch}

    monkeypatch.setattr(scraper, "get_json_with_retry", fetch)
    if scraper.__name__.endswith("rescrape_npia_noauth"):
        monkeypatch.setattr(scraper, "make_search_session", lambda: object())
        results = scraper.fetch_terms("Searching", ["term"], 1, 2, 0)
    else:
        monkeypatch.setattr(scraper, "make_session", lambda key: object())
        results = scraper.fetch_terms("Searching", ["term"], "fixture", {}, 1, 2, 0)

    expected = batch if fail_page == 2 else []
    assert results == {"term": expected}
    assert pages == list(range(1, fail_page + 1))
    log = capsys.readouterr().out
    assert ("PARTIAL" if expected else "FAILED") in log
    assert f"retained {len(expected)} results" in log
    assert f"page {fail_page}" in log


@pytest.mark.parametrize("total", [0, 15000, 30001])
def test_search_uses_half_size_pages_without_losing_results(scraper, monkeypatch, total):
    calls = []

    def fetch(session, params, *args):
        calls.append(dict(params))
        assert params["rows"] == 15000
        start = (params["page"] - 1) * params["rows"]
        return {"list": [{"novel_no": n} for n in range(start, min(start + 15000, total))]}

    monkeypatch.setattr(scraper, "get_json_with_retry", fetch)
    if scraper.__name__.endswith("rescrape_npia_noauth"):
        items = scraper.search_novels(object(), "term", 2)
    else:
        items = scraper.search_novels(object(), "term", {}, 2)
    assert [item["novel_no"] for item in items] == list(range(total))
    assert [call["page"] for call in calls] == list(range(1, total // 15000 + 2))


def test_search_retains_original_total_coverage_ceiling(scraper, monkeypatch):
    pages = []

    def fetch(session, params, *args):
        pages.append(params["page"])
        assert params["rows"] == 15000
        return {"list": [{"novel_no": params["page"]}] * 15000}

    monkeypatch.setattr(scraper, "get_json_with_retry", fetch)
    if scraper.__name__.endswith("rescrape_npia_noauth"):
        items = scraper.search_novels(object(), "term", 2)
    else:
        items = scraper.search_novels(object(), "term", {}, 2)
    assert pages == list(range(1, 41))
    assert len(items) == 600000


@pytest.mark.parametrize("status,body,payload", [
    (200, "<html>maintenance</html>", ValueError("Expecting value")),
    (503, "<html>busy</html>", None),
    (200, "", None),
    (200, '{"error":"denied"}', {"error": "denied"}),
])
def test_invalid_responses_retry_with_details(monkeypatch, capsys, status, body, payload):
    calls = []

    def parse():
        if isinstance(payload, Exception):
            raise payload
        return payload

    response = SimpleNamespace(status_code=status, text=body,
                               headers={"Content-Type": "text/html"}, json=parse)
    session = SimpleNamespace(get=lambda *a, **kw: calls.append(kw) or response)
    monkeypatch.setattr(http.time, "sleep", lambda delay: None)
    with pytest.raises(RuntimeError):
        http.get_json_with_retry(session, {"search_val": "term", "page": 2}, {}, 2)
    assert len(calls) == 2
    log = capsys.readouterr().out
    assert "attempt 1/2" in log and "attempt 2/2" in log
    assert f"HTTP {status}" in log and "content-type='text/html'" in log
    assert "page 2" in log and "preview=" in log


def test_valid_empty_page_is_success():
    response = SimpleNamespace(status_code=200, text='{"list":[]}', json=lambda: {"list": []})
    assert http.get_json_with_retry(SimpleNamespace(get=lambda *a, **k: response), {}, {}, 2) == {"list": []}


def test_transient_failure_recovers_without_logging_request_credentials(monkeypatch, capsys):
    responses = iter([
        SimpleNamespace(status_code=200, text="maintenance", headers={},
                        json=lambda: (_ for _ in ()).throw(ValueError("invalid JSON"))),
        SimpleNamespace(status_code=200, text='{"list":[{"novel_no":123}]}',
                        json=lambda: {"list": [{"novel_no": 123}]}),
    ])
    session = SimpleNamespace(get=lambda *a, **k: next(responses))
    monkeypatch.setattr(http.time, "sleep", lambda delay: None)
    result = http.get_json_with_retry(session, {"search_val": "term", "page": 1},
                                      {"Cookie": "private-cookie"}, 2)
    assert result == {"list": [{"novel_no": 123}]}
    log = capsys.readouterr().out
    assert "attempt 1/2" in log and "private-cookie" not in log


def test_response_preview_is_bounded_and_redacts_credentials():
    response = SimpleNamespace(status_code=403, headers={},
                               text='{"loginkey":"secret-value"} ' + 'x' * 1000)
    details = http.response_details(response)
    assert "secret-value" not in details and "[redacted]" in details
    assert len(details) < 350
