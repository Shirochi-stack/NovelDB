from types import SimpleNamespace
import importlib
import io
import json
import sys
from unittest.mock import patch

import pytest

import downloader_core as core
import npia_intro_discovery as intro


HTML = '''<div class="intro-item" data-novel_no="610" data-novel_age="19">
<div class="intro-novel-info"><h3 class="novel-title">Title</h3><p>Writer</p></div></div>
<div class="intro-item" data-novel_no="1" data-novel_age="0"></div>'''
DETAIL = '''<meta property="og:url" content="/novel/610"><div class="epnew-novel-info">
<p class="in-writer"><a class="writer-name" href="/user/466">Writer</a></p></div>'''


def response(text="", payload=None):
    return SimpleNamespace(status_code=200, text=text, json=lambda: payload)


class Session:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.gets = []
        self.posts = []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if url.endswith("/intro"):
            return response('<div class="intro-item" data-novel_no="2" data-novel_age="0"></div>')
        return response(HTML if url.endswith("/intro/megabox") else DETAIL)

    def post(self, url, **kwargs):
        self.posts.append(kwargs["data"])
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        return response(payload={"result": "success", "data": {"list": value}})


def test_public_discovery_resolves_initial_author_and_paginates():
    session = Session([[dict(novel_no=99, novel_age=19, mem_no=88, writer_nick="Other")], []])
    state = {}
    rows = intro.discover(session, state, delay=0)
    assert {r["novel_no"] for r in rows} == {610, 99}
    assert rows[0]["mem_no"] == "466"
    assert session.posts[0]["page"] == 2 and session.posts[1]["page"] == 3
    assert session.posts[0]["novel_no_arr[]"] == ["610", "1"]
    assert state["intro"]["last_run"] == dict(requests=5, r19_novels=2, failed=0)


def test_budget_and_checkpoint_resume_without_refetching_author():
    state = {}
    first = Session([[dict(novel_no=99, novel_age=19, mem_no=88)]])
    intro.discover(first, state, delay=0, max_requests=4)
    assert state["intro"]["page"] == 3
    second = Session([[]])
    intro.discover(second, state, delay=0)
    assert len(second.gets) == 2
    assert second.posts[0]["page"] == 3 and "99" in second.posts[0]["novel_no_arr[]"]


def test_failure_retains_discoveries_and_pagination_cursor():
    state = {}
    session = Session([TimeoutError("timeout")])
    rows = intro.discover(session, state, delay=0)
    assert rows[0]["novel_no"] == 610 and rows[0]["mem_no"] == "466"
    assert state["intro"]["page"] == 2 and state["intro"]["last_run"]["failed"] == 1


def test_stop_during_pacing_makes_no_request():
    flag = [False]
    session = Session([])
    assert intro.discover(session, {}, stopped=lambda: flag[0],
                          sleep=lambda _: flag.__setitem__(0, True)) == []
    assert not session.gets and not session.posts


@pytest.mark.parametrize("age,expected", [("19", {"610", "999"}), ("15", set()), ("", {"610", "999"})])
def test_desktop_discovers_r19_only_author_when_search_is_empty(monkeypatch, age, expected):
    class DesktopSession(Session):
        def get(self, url, **kwargs):
            if "params" in kwargs:
                return response("JSON", dict(status=200, list=[]))
            return super().get(url, **kwargs)

        def post(self, url, **kwargs):
            if kwargs["data"].get("mode") == "get_member_writer_novel":
                assert kwargs["data"]["mem_no"] == "466"
                return response(payload=dict(status=200, result=dict(allCount=2, novel=[
                    dict(novel_no=610, novel_age=19, mem_no=466),
                    dict(novel_no=999, novel_age=19, mem_no=466)])))
            return super().post(url, **kwargs)

    session = DesktopSession([[]])
    original = intro.discover
    monkeypatch.setattr(core, "discover_intro", lambda *a, **kw: original(*a, **kw, sleep=lambda _: None))
    author_pages = core.iter_author_pages
    monkeypatch.setattr(core, "iter_author_pages", lambda *a, **kw: author_pages(*a, **kw, sleep=lambda _: None))
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    downloader = core.DownloaderCore(SimpleNamespace(session=session), lambda _: None)
    assert set(downloader.fetch_all_novels(delay=0, max_queries=1, age_filter=age)) == expected


def test_metadata_workflow_discovers_and_prioritizes_intro_authors(tmp_path, monkeypatch):
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, "stdout", Output()):
        scraper = importlib.import_module("scripts.rescrape_npia_noauth")

    class WorkflowSession(Session):
        def __init__(self):
            super().__init__([[dict(novel_no=99, novel_age=19, mem_no=88, writer_nick="Other")], []])
            self.headers = {}
            self.authors = []

        def post(self, url, **kwargs):
            if kwargs["data"].get("mode") == "get_member_writer_novel":
                owner = int(kwargs["data"]["mem_no"])
                self.authors.append(owner)
                nid = 610 if owner == 466 else 99
                return response(payload=dict(status=200, result=dict(allCount=1, novel=[
                    dict(novel_no=nid, novel_age=19, mem_no=owner, novel_name="Title",
                         writer_nick="Writer", novel_story="Synopsis") ])))
            return super().post(url, **kwargs)

    session = WorkflowSession()
    monkeypatch.setattr(scraper.requests, "Session", lambda: session)
    monkeypatch.setattr(scraper, "fetch_terms", lambda *a: {})
    monkeypatch.setattr(scraper, "scrape_ranking", lambda *a: {})
    monkeypatch.setattr(scraper, "__file__", str(tmp_path / "scripts" / "rescrape_npia_noauth.py"))
    monkeypatch.setattr(sys, "argv", ["scraper", "--r19-detail-limit", "0", "--r19-listing-pages", "0",
                                     "--author-pages", "2", "--author-delay", "0"])
    scraper.main()
    data = tmp_path / "docs" / "data"
    authors = json.loads((data / "npia_authors.json").read_text(encoding="utf-8"))["authors"]
    assert set(authors) == {"466", "88"}
    assert all(a["catalog_status"] == "complete" for a in authors.values())
    novels = json.loads((data / "novels_full.json").read_text(encoding="utf-8"))
    assert {n["id"] for n in novels} == {610, 99}
    assert all(n["synopsis"] == "Synopsis" for n in novels)
    checkpoint = json.loads((data / "npia_public_state.json").read_text(encoding="utf-8"))
    assert checkpoint["intro"]["last_run"]["requests"] == 5


def test_default_list_discovers_another_author_within_shared_budget():
    class DefaultSession(Session):
        def get(self, url, **kwargs):
            if url.endswith("/intro"):
                self.gets.append(url)
                return response(HTML.replace('610', '116'))
            if url.endswith("/novel/116"):
                self.gets.append(url)
                return response(DETAIL.replace('610', '116').replace('466', '2063'))
            return super().get(url, **kwargs)

    session = DefaultSession([[dict(novel_no=99, novel_age=19, mem_no=88)], []])
    state = {}
    rows = intro.discover(session, state, delay=0)
    assert {r["novel_no"] for r in rows} == {116, 610, 99}
    assert next(r for r in rows if r["novel_no"] == 116)["mem_no"] == "2063"
    assert state["intro"]["resolved_authors"]["116"] == "2063"
    assert state["intro"]["last_run"]["requests"] == 6
    assert all(p["cate"] == "megabox" for p in session.posts)


def test_default_failure_does_not_block_campaign():
    class BrokenDefault(Session):
        def get(self, url, **kwargs):
            if url.endswith("/intro"):
                self.gets.append(url)
                raise TimeoutError("default unavailable")
            return super().get(url, **kwargs)

    state = {}
    rows = intro.discover(BrokenDefault([[]]), state, delay=0)
    assert {r["novel_no"] for r in rows} == {610}
    assert rows[0]["mem_no"] == "466"
    assert state["intro"]["last_run"]["failed"] == 1


def test_campaign_failure_retains_default_discovery():
    class BrokenCampaign(Session):
        def get(self, url, **kwargs):
            if url.endswith("/intro"):
                self.gets.append(url)
                return response(HTML)
            if url.endswith("/intro/megabox"):
                self.gets.append(url)
                raise TimeoutError("campaign unavailable")
            return super().get(url, **kwargs)

    state = {}
    rows = intro.discover(BrokenCampaign([]), state, delay=0)
    assert rows[0]["mem_no"] == "466"
    assert state["intro"]["last_run"]["failed"] == 1
