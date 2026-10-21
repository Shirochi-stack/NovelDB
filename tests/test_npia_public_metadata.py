from types import SimpleNamespace
import importlib
import io
import json
import sys
from unittest.mock import patch

import pytest

from scripts import npia_public_metadata as public


def detail(nid=123, adult=True):
    return f'''<meta property="og:url" content="{public.ORIGIN}/novel/{nid}">
    <div class="epnew-novel-info"><div class="epnew-novel-title">Title</div>
    <p class="in-writer"><a class="writer-name">Author</a></p>
    <p class="in-badge">{'<span class="b_19">19</span>' if adult else ''}</p>
    <div class="info-graybox"><div class="synopsis">Line one<br>Line two</div></div>
    <p class="writer-tag"><span class="tag">#Tag</span></p>
    <p><span class="category-title">\uC870\uD68C</span><span>1,234</span></p>
    <p><span class="category-title">\uCD94\uCC9C</span><span>12</span></p>
    <p><span class="category-title">\uD68C\uCC28</span><span>20\uD68C\uCC28</span></p></div>
    <img class="novel-img" src="/imagebox/cover/unrelated.file">'''


def test_detail_fields_and_hidden_cover_preservation():
    parsed = public.parse_detail(detail(), "123")
    assert parsed == dict(id=123, title="Title", author="Author", age=19,
                          synopsis="Line one\nLine two", tags=["Tag"],
                          views=1234, likes=12, chapters=20)
    assert "cover" not in parsed and "updated" not in parsed and "complete" not in parsed


def test_detail_rejects_other_id_and_unavailable_metadata():
    with pytest.raises(ValueError, match="identity"):
        public.parse_detail(detail(124), "123")
    with pytest.raises(ValueError, match="unavailable"):
        public.parse_detail("<html>Login required</html>", "123")
    assert public.parse_detail(detail(adult=False), "123") is None


@pytest.mark.parametrize('notice', ['\uC798\uBABB\uB41C \uC811\uADFC\uC785\uB2C8\uB2E4.',
                                    '\uC0AD\uC81C\uB41C \uC18C\uC124 \uC785\uB2C8\uB2E4.'])
def test_invalid_access_detail_is_reported_as_unavailable_without_an_error(capsys, notice):
    unavailable = f'<div id="alert_modal">{notice}</div>'
    session = Session({'/novel/224': unavailable})
    fresh = {'224': {'title': 'Retained'}}
    state = public.enrich(session, fresh, [row(224)], {}, {'pending': ['224']},
                          listing_pages=0, detail_limit=1, now=1_000_000)
    assert fresh == {'224': {'title': 'Retained'}}
    assert state['last_run']['unavailable'] == 1 and state['last_run']['failed'] == 0
    assert state['checked']['224'] == 1_000_000 and not state['pending']
    log = capsys.readouterr().out
    assert '224 SKIPPED' in log and 'ValueError' not in log


def test_transient_failure_does_not_mark_detail_checked_for_a_week():
    state = public.enrich(Session({'/novel/123': TimeoutError('timeout')}), {}, [row(123)], {}, {},
                          listing_pages=0, detail_limit=1, now=1_000_000)
    assert '123' not in state['checked']


def test_listing_reads_visible_links_and_click_handlers_only():
    html = '''<a href="/novel/123">Title</a><b onclick="location='/novel/124'">Other</b>
              <a href="/novel/123">Duplicate</a><script>url='/novel/999'</script>'''
    assert public.listing_ids(html) == ["123", "124"]


class Session:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def get(self, url, **kwargs):
        path = url.removeprefix(public.ORIGIN)
        self.calls.append(path)
        assert kwargs["allow_redirects"] is False
        reply = self.replies[path]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(status_code=200, text=reply)


def row(nid):
    return [nid, "Old", "Writer", "/saved-cover", ["OldTag"], 5, 6, 7, 1, "saved-date", 9, 19] + [8] * 8


def test_budget_checkpoint_and_sparse_merge_preserve_existing_fields():
    session = Session({"/plus/adult/new/1/": '<a href="/novel/123">A</a><a href="/novel/124">B</a>',
                       "/novel/123": detail()})
    fresh = {}
    sleeps = []
    state = public.enrich(session, fresh, [row(123)], {"123": {"synopsis": "Old synopsis"}}, {},
                          listing_pages=1, detail_limit=1, now=1_000_000, sleep=sleeps.append)
    assert session.calls == ["/plus/adult/new/1/", "/novel/123"]
    assert sleeps == [2.0]
    assert state["listing_page"] == 2 and state["pending"] == ["124"]
    assert fresh["123"]["cover"] == "/saved-cover"
    assert fresh["123"]["complete"] == 1 and fresh["123"]["updated"] == "saved-date"
    assert fresh["123"]["synopsis"] == "Line one\nLine two"
    next_session = Session({"/novel/124": detail(124)})
    public.enrich(next_session, fresh, [row(123)], {}, state, listing_pages=0,
                  detail_limit=1, now=1_000_001)
    assert next_session.calls == ["/novel/124"]
    assert not state["pending"]


def test_failures_retain_existing_data_and_failed_queue(capsys):
    fresh = {"123": {"title": "Existing"}}
    session = Session({"/plus/adult/new/1/": "Login", "/novel/123": TimeoutError("timeout")})
    state = {"listing_page": 5, "pending": ["123"]}
    public.enrich(session, fresh, [row(123)], {}, state, listing_pages=1, detail_limit=1,
                  now=1_000_000, sleep=lambda _: None)
    assert fresh == {"123": {"title": "Existing"}}
    assert state["pending"] == ["123"] and state["listing_page"] == 5
    assert state["last_run"]["failed"] == state["last_run"]["listing_failed"] == 1
    assert "existing metadata retained" in capsys.readouterr().out


def test_old_r19_rows_receive_reserved_refresh_budget():
    session = Session({f"/novel/{nid}": detail(nid) for nid in (1, 10, 11, 12)})
    state = {"pending": ["10", "11", "12", "13"]}
    public.enrich(session, {}, [row(1)], {}, state, listing_pages=0, detail_limit=4,
                  now=1_000_000, sleep=lambda _: None)
    assert session.calls == ["/novel/1", "/novel/10", "/novel/11", "/novel/12"]
    assert state["pending"] == ["13"]


def test_planned_details_resolve_only_selected_unknown_authors():
    session = Session({'/novel/10': detail(10)})
    state = public.enrich(session, {}, [row(1), row(10), row(11)], {}, {},
                          listing_pages=0, detail_limit=200, detail_ids=['10'], now=1_000_000)
    assert session.calls == ['/novel/10'] and state['last_run']['updated'] == 1


def test_repeated_listing_page_stops_instead_of_looping():
    html = '<a href="/novel/123">A</a>'
    session = Session({"/plus/adult/new/1/": html, "/plus/adult/new/2/": html})
    state = public.enrich(session, {}, [], {}, {}, listing_pages=10, detail_limit=0,
                          now=1_000_000, sleep=lambda _: None)
    assert len(session.calls) == 2 and state["listing_page"] == 1


def test_later_run_checks_newest_page_and_continues_backfill():
    session = Session({"/plus/adult/new/1/": '<a href="/novel/1">A</a>',
                       "/plus/adult/new/11/": '<a href="/novel/2">B</a>'})
    state = {"listing_page": 11}
    public.enrich(session, {}, [], {}, state, listing_pages=2, detail_limit=0,
                  now=1_000_000, sleep=lambda _: None)
    assert session.calls == ["/plus/adult/new/1/", "/plus/adult/new/11/"]
    assert state["listing_page"] == 12


@pytest.mark.parametrize("dry_run", [False, True])
def test_workflow_scraper_integrates_public_metadata_and_checkpoint(tmp_path, monkeypatch, capsys, dry_run):
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass

    with patch.object(sys, "stdout", Output()):
        scraper = importlib.import_module("scripts.rescrape_npia_noauth")
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    catalog = data / "novels.json"
    catalog.write_text(json.dumps([row(123)]), encoding="utf-8")
    before = catalog.read_bytes()
    monkeypatch.setattr(scraper, "__file__", str(tmp_path / "scripts" / "rescrape_npia_noauth.py"))
    monkeypatch.setattr(scraper, "fetch_terms", lambda *a: {})
    def ranking(session, period, audience):
        if (audience, period) == ('all/plus', 'weekly'):
            raise RuntimeError('Temporary public board failure')
        return {'123': {'month': 1, 'today': 2}.get(period, 3)}
    monkeypatch.setattr(scraper, "scrape_ranking", ranking)

    class AnonymousSession:
        headers = {}
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200, text=detail() if url.endswith("/123") else "")

    monkeypatch.setattr(scraper.requests, "Session", AnonymousSession)
    argv = ["scraper", "--r19-listing-pages", "0", "--r19-detail-limit", "1", "--intro-requests", "0"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    scraper.main()
    output = capsys.readouterr().out
    assert 'R19 refreshed (unique):  1' in output
    assert 'R19 new IDs:             0' in output
    if dry_run:
        assert catalog.read_bytes() == before
        assert not (data / "npia_public_state.json").exists()
        assert not (data / "novels_full.json").exists()
    else:
        result = json.loads(catalog.read_text(encoding="utf-8"))[0]
        assert result[1] == "Title" and result[11] == 19
        assert result[3] == "/saved-cover" and result[9] == "saved-date"
        assert result[14:17] == row(123)[14:17]
        assert result[10] == row(123)[10]
        assert result[12:14] == [1, 2]
        assert result[17:20] == [3, 1, 2]
        full = json.loads((data / "novels_full.json").read_text(encoding="utf-8"))
        assert full[0]["synopsis"] == "Line one\nLine two"
        state = json.loads((data / "npia_public_state.json").read_text(encoding="utf-8"))
        assert state["last_run"]["updated"] == 1 and "123" in state["checked"]
