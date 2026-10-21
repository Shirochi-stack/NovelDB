"""R19 authors absent from public search must not be silently skipped."""
import importlib
import io
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import npia_author_registry as authors
import npia_author_resolution as identities


def detail(nid, owner=50169, name='Writer'):
    return f'''<meta property="og:url" content="/novel/{nid}">
    <div class="epnew-novel-info"><div class="epnew-novel-title">Title</div>
    <p class="in-writer"><a class="writer-name" href="/user/{owner}">{name}</a></p>
    <p class="in-badge"><span class="b_19">19</span></p></div>'''


def registry_for(rows):
    registry = {'version': 1, 'authors': {}}
    authors.refresh_r19(registry, rows, now=1)
    return registry


def row(nid, name='Writer', age=19):
    return [nid, 'Title', name] + [0] * 8 + [age]


def response(html, status=200):
    return SimpleNamespace(status_code=status, text=html, headers={})


@pytest.mark.parametrize('html', [detail(2), '<a class="writer-name" href="/user/50169">Recommendation</a>',
                                  detail(1).replace('href="/user/50169"', 'href="/login"')])
def test_identity_requires_requested_novels_own_author_link(html):
    with pytest.raises(ValueError):
        identities.parse_identity(html, '1')


def test_identity_returns_verified_url_owner_without_requiring_login_or_adult_cover():
    assert identities.parse_identity(detail(449004), '449004') == dict(
        id=449004, author_id='50169', author='Writer', age=19)


def test_name_candidates_still_need_verified_ownership_and_only_r19_is_targeted():
    registry = registry_for([row(1), row(2), row(3, 'General', 0)])
    authors.observe(registry, [dict(id=10, author='Writer', author_id='42')], now=2)
    authors.refresh_r19(registry, now=3)
    assert registry['r19']['authors']['42']['evidence'] == 'name_match'
    assert authors.unresolved_r19_groups(registry) == [('Writer', ['1', '2'])]
    calls = []
    session = SimpleNamespace(get=lambda url, **kw: calls.append(url) or response(detail(1)))
    stats = identities.resolve_missing(session, registry, delay=0, delay_max=0, log=lambda _: None)
    assert calls == [identities.ORIGIN + '/novel/1']
    assert stats['new_authors'] == stats['resolved'] == 1
    assert registry['authors']['50169']['novel_ids'] == ['1']
    assert registry['authors']['42']['novel_ids'] == ['10']
    authors.refresh_r19(registry)
    assert authors.unresolved_r19_groups(registry) == [('Writer', ['2'])]


def test_unavailable_identity_retries_and_remains_unresolved_without_losing_authors():
    registry = registry_for([row(1)])
    calls, saved = [], []
    session = SimpleNamespace(get=lambda url, **kw: calls.append(url) or response('Unavailable', 503))
    failures = {}
    stats = identities.resolve_missing(session, registry, failures=failures, delay=0, delay_max=0,
        checkpoint=lambda: saved.append(True), log=lambda _: None)
    assert len(calls) == stats['requests'] == 4
    assert stats['failed'] == 1 and stats['resolved'] == 0 and failures == {'1': 1}
    assert authors.unresolved_r19_groups(registry) == [('Writer', ['1'])]
    assert saved and not registry['authors']


def test_request_budget_and_stop_do_not_discard_successful_identity():
    registry = registry_for([row(1), row(2, 'Other')])
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(detail(url.rsplit('/', 1)[1]))
    stats = identities.resolve_missing(SimpleNamespace(get=get), registry, max_requests=1,
        workers=1, delay=0, delay_max=0, log=lambda _: None)
    assert len(calls) == stats['requests'] == 1 and stats['resolved'] == 1
    assert registry['authors']['50169']['novel_ids']
    result = identities.resolve_missing(SimpleNamespace(get=get), registry, stopped=lambda: True,
                                        delay=0, delay_max=0, log=lambda _: None)
    assert result['requests'] == 0


def test_four_workers_really_run_requests_in_parallel():
    registry = registry_for([row(n, f'Writer {n}') for n in range(1, 5)])
    barrier = threading.Barrier(4)
    threads = set()
    def get(url, **kwargs):
        threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        nid = url.rsplit('/', 1)[1]
        return response(detail(nid, owner=int(nid)))
    result = identities.resolve_missing(SimpleNamespace(get=get), registry,
        workers=4, delay=0, delay_max=0, log=lambda _: None)
    assert len(threads) == result['resolved'] == 4


def scraper_module():
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, 'stdout', Output()):
        return importlib.import_module('scripts.rescrape_npia_noauth')


def test_workflow_resolves_r19_only_author_and_fetches_all_works_in_same_pass():
    scraper = scraper_module()
    registry = registry_for([row(449004), row(2910)])
    calls = []
    def get(url, **kwargs):
        calls.append(('get', url))
        return response(detail(url.rsplit('/', 1)[1]))
    def post(url, **kwargs):
        owner = kwargs['data']['mem_no']
        calls.append(('post', owner))
        assert owner == '50169'
        rows = [dict(novel_no=n, mem_no=50169, writer_nick='Writer', novel_age=19,
                     novel_name='Title', novel_story='Full synopsis') for n in (449004, 2910)]
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(allCount=2, novel=rows)))
    fresh, state = {}, {}
    scraper.enrich_author_catalogs(SimpleNamespace(get=get, post=post), fresh, {}, state,
        registry=registry, existing=[row(449004), row(2910)], delay=0, resolve_identities=True)
    assert calls == [('get', identities.ORIGIN + '/novel/2910'), ('post', '50169')]
    assert set(fresh) == {'449004', '2910'}
    assert state['author_sweep']['complete'] and not state['author_sweep']['remaining_identities']
    assert registry['r19']['authors']['50169']['verified_novel_ids'] == ['2910', '449004']


def test_same_nickname_different_authors_are_resolved_on_continuation():
    scraper = scraper_module()
    registry = registry_for([row(1), row(2)])
    def get(url, **kwargs):
        nid = url.rsplit('/', 1)[1]
        return response(detail(nid, owner=int(nid) + 40))
    def post(url, **kwargs):
        owner = int(kwargs['data']['mem_no'])
        novel = dict(novel_no=owner - 40, mem_no=owner, writer_nick='Writer', novel_age=19)
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(allCount=1, novel=[novel])))
    state, fresh = {}, {}
    for _ in range(2):
        scraper.enrich_author_catalogs(SimpleNamespace(get=get, post=post), fresh, {}, state,
            registry=registry, existing=[row(1), row(2)], delay=0, resolve_identities=True)
        if len(registry['authors']) == 1:
            assert not state['author_sweep']['complete'] and state['author_sweep']['continuation_required']
    assert set(registry['authors']) == {'41', '42'}
    assert registry['authors']['41']['novel_ids'] == ['1']
    assert registry['authors']['42']['novel_ids'] == ['2']
    assert state['author_sweep']['complete']


def test_failed_identity_is_not_reported_as_a_complete_sweep():
    scraper = scraper_module()
    registry = registry_for([row(1)])
    state = {}
    session = SimpleNamespace(get=lambda *a, **k: response('Unavailable', 503))
    for attempt in range(3):
        scraper.enrich_author_catalogs(session, {}, {}, state, registry=registry, delay=0,
                                       resolve_identities=True)
        sweep = state['author_sweep']
        assert not sweep['complete'] and sweep['remaining_identities'] == 1
        assert sweep['continuation_required'] == (attempt < 2)


def test_live_backfill_does_not_open_account_config(tmp_path, monkeypatch):
    import builtins
    import requests
    from scripts import update_npia_r19_registry as updater
    rows, path = tmp_path / 'rows.json', tmp_path / 'authors.json'
    rows.write_text(json.dumps([row(449004)]), encoding='utf-8')
    original = builtins.open
    def guarded(file, *a, **kw):
        assert str(file).replace('\\', '/').split('/')[-1] != 'config.json'
        return original(file, *a, **kw)
    monkeypatch.setattr(builtins, 'open', guarded)
    session = SimpleNamespace(headers={}, get=lambda *a, **k: response(detail(449004)), close=lambda: None)
    monkeypatch.setattr(requests, 'Session', lambda: session)
    monkeypatch.setattr(identities.random, 'uniform', lambda *a: 0)
    monkeypatch.setattr(sys, 'argv', ['updater', '--registry', str(path), '--catalog', str(rows),
        '--full', str(tmp_path / 'absent.json'), '--resolve'])
    updater.main()
    result = authors.load(path)
    assert result['r19']['authors']['50169']['verified_novel_ids'] == ['449004']


def test_pre_upgrade_complete_sweep_still_resolves_missing_author_in_main(tmp_path, monkeypatch):
    scraper = scraper_module()
    data = tmp_path / 'docs' / 'data'
    data.mkdir(parents=True)
    (data / 'novels.json').write_text(json.dumps([
        [449004, 'Title', 'Writer', '', [], 0, 0, 0, 0, '', 0, 19]]), encoding='utf-8')
    authors.save(data / 'npia_authors.json', {'version': 1, 'authors': {}})
    (data / 'npia_public_state.json').write_text(json.dumps({'author_sweep': dict(
        id='legacy', completed=[], failures={}, complete=True, scope='r19', remaining=0)}), encoding='utf-8')
    calls = []
    def get(url, **kwargs):
        if '/novel/' in url:
            calls.append(('identity', url))
            return response(detail(449004))
        return response('')
    def post(url, **kwargs):
        calls.append(('catalog', kwargs['data']['mem_no']))
        novels = [dict(novel_no=n, mem_no=50169, writer_nick='Writer', novel_age=19,
                       novel_name='Title', novel_story='Synopsis') for n in (449004, 2910)]
        return SimpleNamespace(status_code=200, json=lambda: dict(status=200, result=dict(allCount=2, novel=novels)))
    session = SimpleNamespace(headers={}, get=get, post=post)
    monkeypatch.setattr(scraper.requests, 'Session', lambda: session)
    monkeypatch.setattr(scraper, '__file__', str(tmp_path / 'scripts' / 'rescrape_npia_noauth.py'))
    monkeypatch.setattr(scraper, 'fetch_terms', lambda *a, **k: pytest.fail('Continuation repeated search'))
    monkeypatch.setattr(sys, 'argv', ['scraper', '--author-sweep-id', 'legacy', '--author-pages', '2',
                                     '--author-delay', '0', '--intro-requests', '0'])
    scraper.main()
    assert calls == [('identity', identities.ORIGIN + '/novel/449004'), ('catalog', '50169')]
    result = authors.load(data / 'npia_authors.json')
    assert result['r19']['authors']['50169']['verified_novel_ids'] == ['2910', '449004']
    sweep = json.loads((data / 'npia_public_state.json').read_text(encoding='utf-8'))['author_sweep']
    assert sweep['id'] == 'legacy' and sweep['complete'] and sweep['resolution_version'] == 1


def deleted(nid):
    return (f'<meta property="og:url" content="/novel/{nid}">'
            '<div id="alert_modal">\uC0AD\uC81C\uB41C \uC18C\uC124 \uC785\uB2C8\uB2E4.</div>')


@pytest.mark.parametrize('nid', ['436480', '121214'])
def test_explicit_deleted_notice_does_not_retry_or_lose_existing_metadata(nid, tmp_path):
    registry = registry_for([row(int(nid))])
    calls, logs, failures = [], [], {nid: 2}
    session = SimpleNamespace(get=lambda url, **kw: calls.append(url) or response(deleted(nid)))
    stats = identities.resolve_missing(session, registry, failures=failures,
        delay=0, delay_max=0, log=logs.append)
    assert stats['requests'] == stats['unavailable'] == 1 and stats['failed'] == 0
    assert not failures and authors.unresolved_r19_groups(registry) == [('Writer', [nid])]
    path = tmp_path / 'registry.json'
    authors.save(path, registry)
    restored = authors.load(path)
    result = identities.resolve_missing(session, restored, delay=0, delay_max=0, log=logs.append)
    assert result['requests'] == 0 and len(calls) == 1
    assert any('has been deleted' in log for log in logs)
    checked = restored['r19_identity_unavailable'][nid]['checked']
    assert authors.identity_candidates(restored, [nid], now=checked + 7 * 86400) == [nid]


def test_deleted_representative_tries_next_known_novel_in_same_pass():
    registry = registry_for([row(1), row(2)])
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        nid = url.rsplit('/', 1)[1]
        return response(deleted(nid) if nid == '1' else detail(nid))
    stats = identities.resolve_missing(SimpleNamespace(get=get), registry, delay=0, delay_max=0, log=lambda _: None)
    assert stats['requests'] == 2 and stats['resolved'] == stats['unavailable'] == 1
    assert registry['authors']['50169']['novel_ids'] == ['2']
    assert authors.unresolved_r19_groups(registry) == [('Writer', ['1', '2'])]


@pytest.mark.parametrize('status', [404, 410])
def test_missing_http_status_is_not_retried(status):
    registry = registry_for([row(1)])
    stats = identities.resolve_missing(SimpleNamespace(get=lambda *a, **kw: response('', status)),
        registry, delay=0, delay_max=0, log=lambda _: None)
    assert stats['requests'] == stats['unavailable'] == 1 and stats['failed'] == 0


@pytest.mark.parametrize('html', [deleted(2), detail(1).replace('Writer', '\uC0AD\uC81C\uB41C \uC18C\uC124'),
    '<meta property="og:url" content="/novel/1"><p>Login required</p>',
    '<meta property="og:url" content="/novel/1"><p>\uC0AD\uC81C\uB41C \uC18C\uC124</p>'])
def test_only_matching_novel_access_notice_is_terminal(html):
    try:
        result = identities.parse_identity(html, '1')
        assert result['author_id'] == '50169'
    except ValueError as error:
        assert not isinstance(error, identities.IdentityUnavailable)


def test_workflow_keeps_deleted_groups_visible_without_automatic_retry_loop():
    scraper = scraper_module()
    registry = registry_for([row(1)])
    state, fresh = {}, {'1': dict(title='Retained', synopsis='Keep')}
    session = SimpleNamespace(get=lambda *a, **kw: response(deleted(1)))
    for _ in range(2):
        scraper.enrich_author_catalogs(session, fresh, {}, state, registry=registry, delay=0,
            resolve_identities=True)
        sweep = state['author_sweep']
        assert not sweep['complete'] and not sweep['continuation_required']
        assert sweep['remaining'] == sweep['remaining_unavailable_identities'] == 1
        assert fresh == {'1': dict(title='Retained', synopsis='Keep')}
    assert state['author_last_run']['identity_requests'] == 0
