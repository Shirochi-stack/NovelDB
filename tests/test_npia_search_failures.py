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
    batch = [{"novel_no": 123}] * 2000
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


@pytest.mark.parametrize("total", [0, 2000, 4001])
def test_search_uses_small_pages_without_losing_results(scraper, monkeypatch, total):
    calls = []

    def fetch(session, params, *args):
        calls.append(dict(params))
        assert params["rows"] == 2000
        start = (params["page"] - 1) * params["rows"]
        return {"list": [{"novel_no": n} for n in range(start, min(start + 2000, total))]}

    monkeypatch.setattr(scraper, "get_json_with_retry", fetch)
    if scraper.__name__.endswith("rescrape_npia_noauth"):
        items = scraper.search_novels(object(), "term", 2)
    else:
        items = scraper.search_novels(object(), "term", {}, 2)
    assert [item["novel_no"] for item in items] == list(range(total))
    assert [call["page"] for call in calls] == list(range(1, total // 2000 + 2))


def test_search_retains_original_total_coverage_ceiling(scraper, monkeypatch):
    pages = []

    def fetch(session, params, *args):
        pages.append(params["page"])
        assert params["rows"] == 2000
        return {"list": [{"novel_no": params["page"]}] * 2000}

    monkeypatch.setattr(scraper, "get_json_with_retry", fetch)
    if scraper.__name__.endswith("rescrape_npia_noauth"):
        items = scraper.search_novels(object(), "term", 2)
    else:
        items = scraper.search_novels(object(), "term", {}, 2)
    assert pages == list(range(1, 301))
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


def test_memory_resize_resumes_after_completed_pages_without_gaps(scraper, monkeypatch):
    calls = []
    def fetch(session, params, *args):
        size, page = params['rows'], params['page']
        calls.append((size, page))
        offset = (page - 1) * size
        if offset >= 2000 and size > 500:
            raise http.SearchMemoryError('source server memory exhausted')
        return {'list': [{'novel_no': n} for n in range(offset, min(offset + size, 4501))]}
    monkeypatch.setattr(scraper, 'get_json_with_retry', fetch)
    if scraper.__name__.endswith('rescrape_npia_noauth'):
        items = scraper.search_novels(object(), 'wide', 4)
    else:
        items = scraper.search_novels(object(), 'wide', {}, 4)
    assert [item['novel_no'] for item in items] == list(range(4501))
    assert calls[:4] == [(2000, 1), (2000, 2), (1000, 3), (500, 5)]


def test_non_divisible_resize_discards_only_previously_collected_overlap():
    calls = []
    def fetch(params):
        size, page = params['rows'], params['page']
        calls.append((size, page))
        if size == 3 and page == 2:
            raise http.SearchMemoryError('memory')
        offset = (page - 1) * size
        return {'list': list(range(offset, min(offset + size, 7)))}
    assert http.paginated_search(fetch, {}, rows=3, min_rows=2, max_results=10) == list(range(7))
    assert calls[:3] == [(3, 1), (3, 2), (2, 2)]


def test_failure_after_resizing_retains_all_successful_results():
    def fetch(params):
        size, page = params['rows'], params['page']
        if size == 2000 and page == 2:
            raise http.SearchMemoryError('memory')
        if size == 1000 and page == 4:
            raise RuntimeError('page unavailable after retries')
        offset = (page - 1) * size
        return {'list': list(range(offset, offset + size))}
    with pytest.raises(http.PartialSearchError) as caught:
        http.paginated_search(fetch, {})
    assert caught.value.items == list(range(3000))
    assert 'page 4' in str(caught.value)


@pytest.mark.parametrize('message', ['Out of memory (allocated 19001344)',
                                   'Allowed memory size of 536870912 bytes exhausted'])
def test_memory_response_shrinks_then_retries_at_minimum_size(monkeypatch, message):
    calls = []
    body = '<br /><b>Fatal error</b>: ' + message
    response = SimpleNamespace(status_code=200, text=body, headers={'Content-Type': 'application/json'},
                               json=lambda: pytest.fail('Memory HTML must not be decoded as JSON'))
    def get(url, **kwargs):
        calls.append(kwargs['params']['rows'])
        return response
    monkeypatch.setattr(http.time, 'sleep', lambda _: None)
    session = SimpleNamespace(get=get)
    with pytest.raises(http.PartialSearchError):
        http.paginated_search(lambda params: http.get_json_with_retry(session, params, {}, 4), {})
    assert calls == [2000, 1000, 500, 250, 250, 250, 250]


@pytest.mark.parametrize('payload', [
    {'status': 503, 'list': []},
    {'status': 200, 'total_cnt': 50, 'list': []},
])
def test_unavailable_json_search_page_retries_instead_of_ending_search(monkeypatch, payload):
    calls = []
    responses = iter([payload, {'status': 200, 'list': [{'novel_no': 1}]}])
    def get(url, **kwargs):
        calls.append(kwargs['params'].copy())
        data = next(responses)
        return SimpleNamespace(status_code=200, text='fixture JSON', headers={}, json=lambda: data)
    monkeypatch.setattr(http.time, 'sleep', lambda _: None)
    result = http.get_json_with_retry(SimpleNamespace(get=get), {'page': 1, 'rows': 2000}, {}, 4)
    assert result['list'] == [{'novel_no': 1}]
    assert len(calls) == 2 and calls[0] == calls[1]


def test_valid_novel_text_about_memory_is_not_treated_as_server_error():
    data = {'list': [{'novel_no': 1, 'novel_story': 'Out of memory: an adventure'}]}
    response = SimpleNamespace(status_code=200, text='{"list":[{"novel_story":"Out of memory"}]}',
                               headers={}, json=lambda: data)
    assert http.get_json_with_retry(SimpleNamespace(get=lambda *a, **k: response), {'rows': 2000}, {}, 4) == data
