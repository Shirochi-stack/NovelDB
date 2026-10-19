import importlib
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import npia_author_registry as registry


def test_registry_preserves_missing_authors_and_tracks_names(tmp_path):
    path = tmp_path / "authors.json"
    data = registry.load(path)
    registry.observe(data, [dict(id=1, author_id="42", author="Old name"),
                            dict(id=2, author_id="43", author="Other")], now=10)
    registry.record_search(data, [dict(id=3, author_id="42", author="New name")], now=20)
    result = registry.report_coverage(data, {"3"})
    assert result["known_authors_missing_from_search"] == ["43"]
    assert data["authors"]["42"]["names"] == ["New name", "Old name"]
    assert data["authors"]["42"]["novel_ids"] == ["1", "3"]
    registry.save(path, data)
    assert registry.load(path) == data


def test_bad_registry_is_not_silently_replaced(tmp_path):
    path = tmp_path / "authors.json"
    path.write_text('{"version":99,"authors":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        registry.load(path)


def test_legacy_names_are_retained_without_guessing_or_merging_author_ids():
    data = {"version": 1, "authors": {}}
    registry.seed_catalog_names(data, [[1, "Title", "Same name"], [2, "Other", "Same name"],
                                       [3, "Old", "Missing name"]])
    registry.record_search(data, [dict(id=1, author_id="42", author="Same name"),
                                  dict(id=2, author_id="43", author="Same name")], now=20)
    assert set(data["authors"]) == {"42", "43"}
    assert data["unresolved_names"] == {"Missing name": ["3"]}
    report = registry.report_coverage(data, {"1", "2"})
    assert report["unresolved_names_missing_from_search"] == ["Missing name"]


def test_atomic_registry_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "authors.json"
    registry.save(path, {"version": 1, "authors": {}})
    before = path.read_bytes()
    def fail(*args):
        raise PermissionError("locked")
    monkeypatch.setattr(registry.os, "replace", fail)
    with pytest.raises(PermissionError):
        registry.save(path, {"version": 1, "authors": {"42": {}}})
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def load_scraper(name):
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, "stdout", Output()):
        return importlib.import_module("scripts." + name)


def test_registry_author_absent_from_search_is_still_checked():
    scraper = load_scraper("rescrape_npia_noauth")
    data = {"version": 1, "authors": {}}
    registry.observe(data, [dict(id=1, author_id="42", author="Writer")], now=10)
    registry.record_search(data, [], now=1_000_000)
    calls = []
    item = dict(novel_no=2, mem_no=42, novel_name="Missing novel", novel_age=19, novel_story="Synopsis")
    def post(url, **kwargs):
        calls.append(kwargs["data"])
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(novel=[item], allCount=1)))
    fresh = {}
    scraper.enrich_author_catalogs(SimpleNamespace(post=post), fresh, {}, {},
                                   page_limit=1, delay=0, now=1_000_000, registry=data)
    assert calls[0]["mem_no"] == "42" and fresh["2"]["age"] == 19
    coverage = registry.report_coverage(data, set())
    assert coverage["known_authors_missing_from_search"] == ["42"]
    assert coverage["checked_catalog_novels_missing_from_search"] == ["2"]
    assert data["authors"]["42"]["catalog_status"] == "complete"


def test_failed_catalog_does_not_mark_author_complete():
    scraper = load_scraper("rescrape_npia_noauth")
    data = {"version": 1, "authors": {}}
    registry.observe(data, [dict(id=1, author_id="42", author="Writer")], now=10)
    def fail(*a, **k):
        raise TimeoutError("timeout")
    state = {}
    scraper.enrich_author_catalogs(SimpleNamespace(post=fail), {}, {}, state,
                                   page_limit=1, delay=0, now=1_000_000, registry=data)
    assert data["authors"]["42"]["catalog_status"] == "partial"
    assert "last_catalog_checked" not in data["authors"]["42"]
    assert state["author_pending"] == {"42": 1}


def test_authenticated_rescrape_updates_shared_registry_without_real_credentials(tmp_path, monkeypatch):
    scraper = load_scraper("scrape_npia")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text('{"loginkey":"offline-fixture"}', encoding="utf-8")
    data = tmp_path / "docs" / "data"
    registry_path = data / "npia_authors.json"
    saved = {"version": 1, "authors": {}}
    registry.observe(saved, [dict(id=10, author_id="43", author="Missing author")], now=1)
    registry.save(registry_path, saved)
    def item(nid, owner):
        return dict(novel_no=nid, mem_no=owner, novel_name=f"Novel {nid}", writer_nick=f"Author {owner}",
                    novel_story="Synopsis", novel_age=19)
    calls = []
    def post(url, **kwargs):
        owner = int(kwargs["data"]["mem_no"])
        calls.append(owner)
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(novel=[item(owner, owner)], allCount=1)))
    session = SimpleNamespace(post=post, get=lambda *a, **k: SimpleNamespace(text="", status_code=200))
    monkeypatch.setattr(scraper, "NpiaAuth", lambda: SimpleNamespace(session=session, set_manual_key=lambda _: None))
    monkeypatch.setattr(scraper, "fetch_terms", lambda label, terms, *a: {terms[0]: [item(1, 42)]})
    monkeypatch.setattr(sys, "argv", ["scraper", "--author-pages", "2", "--author-delay", "0"])
    scraper.main()
    updated = registry.load(registry_path)
    assert set(updated["authors"]) == {"42", "43"}
    assert calls == [42, 43]
    assert updated["coverage"]["known_authors_missing_from_search"] == ["43"]
    assert (data / "npia_public_state.json").exists()
    full = json.loads((data / "novels_full.json").read_text(encoding="utf-8"))
    assert {n["id"] for n in full} == {1, 42, 43}


def test_workflow_scraper_checks_registry_when_search_finds_nothing(tmp_path, monkeypatch):
    scraper = load_scraper("rescrape_npia_noauth")
    data = tmp_path / "docs" / "data"
    path = data / "npia_authors.json"
    saved = {"version": 1, "authors": {}}
    registry.observe(saved, [dict(id=1, author_id="42", author="Known")], now=1)
    registry.save(path, saved)
    monkeypatch.setattr(scraper, "__file__", str(tmp_path / "scripts" / "rescrape_npia_noauth.py"))
    monkeypatch.setattr(scraper, "fetch_terms", lambda *a: {})
    monkeypatch.setattr(scraper, "scrape_ranking", lambda *a: {})
    item = dict(novel_no=2, mem_no=42, writer_nick="Known", novel_name="Recovered", novel_age=19)
    def post(*a, **kw):
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(novel=[item], allCount=1)))
    session = SimpleNamespace(headers={}, post=post, get=lambda *a, **k: SimpleNamespace(text="", status_code=200))
    monkeypatch.setattr(scraper.requests, "Session", lambda: session)
    monkeypatch.setattr(sys, "argv", ["scraper", "--r19-listing-pages", "0", "--r19-detail-limit", "0",
                                     "--author-pages", "1", "--author-delay", "0"])
    scraper.main()
    result = registry.load(path)
    assert result["authors"]["42"]["catalog_status"] == "complete"
    assert result["coverage"]["known_authors_missing_from_search"] == ["42"]
    assert json.loads((data / "novels.json").read_text(encoding="utf-8"))[0][0] == 2
