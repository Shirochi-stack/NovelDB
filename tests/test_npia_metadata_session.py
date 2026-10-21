"""Fresh password login is shared by metadata workers without exposing credentials."""
import importlib
import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    for name in ('NPIA_METADATA_EMAIL', 'NPIA_METADATA_PASSWORD', 'NPIA_METADATA_LOGINKEY'):
        monkeypatch.delenv(name, raising=False)


def scraper_module():
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, 'stdout', Output()):
        return importlib.import_module('scripts.rescrape_npia_noauth')


def credentials(monkeypatch):
    monkeypatch.setenv('NPIA_METADATA_EMAIL', 'fixture@example.invalid')
    monkeypatch.setenv('NPIA_METADATA_PASSWORD', ' fixture-password ')


def response(status=200, body='Login accepted'):
    return SimpleNamespace(status_code=status, text=body)


def accepted(session, monkeypatch):
    posts = []
    def post(url, **kwargs):
        posts.append(kwargs['data'])
        return response()
    monkeypatch.setattr(session, 'post', post)
    monkeypatch.setattr(session, 'get', lambda *a, **k: response(body="var mem_no = '42';"))
    return posts


def test_no_credentials_remains_anonymous_and_old_key_is_ignored(monkeypatch):
    scraper = scraper_module()
    monkeypatch.setenv('NPIA_METADATA_LOGINKEY', 'obsolete-fixture-key')
    with scraper.make_search_session() as session:
        monkeypatch.setattr(session, 'post', lambda *a, **k: pytest.fail('Unexpected login'))
        assert not scraper.login_metadata_session(session)
        assert not list(session.cookies)


@pytest.mark.parametrize('missing', ['NPIA_METADATA_EMAIL', 'NPIA_METADATA_PASSWORD'])
def test_partial_credentials_fail_instead_of_silently_running_anonymously(monkeypatch, missing):
    scraper = scraper_module()
    credentials(monkeypatch)
    monkeypatch.delenv(missing)
    with scraper.make_search_session() as session:
        with pytest.raises(RuntimeError, match='Both metadata email and password secrets'):
            scraper.login_metadata_session(session)


def test_fresh_login_does_not_read_config_or_expose_credentials(monkeypatch, capsys):
    import builtins
    scraper = scraper_module()
    credentials(monkeypatch)
    original = builtins.open
    def guarded(file, *args, **kwargs):
        assert str(file).replace('\\', '/').split('/')[-1] != 'config.json'
        return original(file, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', guarded)
    with scraper.make_search_session() as session:
        posts = accepted(session, monkeypatch)
        assert scraper.login_metadata_session(session)
        assert posts == [{'redirectrurl': '', 'email': 'fixture@example.invalid', 'wd': ' fixture-password '}]
        key = session.cookies['LOGINKEY']
        assert len(key) == 65 and key[32] == '_'
        own = session.prepare_request(requests.Request('GET', scraper.COVER_PREFIX + '/proc/novel'))
        foreign = session.prepare_request(requests.Request('GET', 'https://example.com/image'))
        insecure = session.prepare_request(requests.Request('GET', scraper.COVER_PREFIX.replace('https:', 'http:')))
        assert own.headers['Cookie'] == 'LOGINKEY=' + key
        assert 'Cookie' not in foreign.headers and 'Cookie' not in insecure.headers
    assert not any(value in capsys.readouterr().out for value in ('fixture@example.invalid', 'fixture-password', key))


def test_one_login_is_shared_by_all_independent_search_workers(monkeypatch, capsys):
    scraper = scraper_module()
    credentials(monkeypatch)
    clients = []
    with scraper.make_search_session() as source:
        posts = accepted(source, monkeypatch)
        assert scraper.login_metadata_session(source)
        key = source.cookies['LOGINKEY']
        def search(session, term, retries):
            assert session is not source and session.cookies['LOGINKEY'] == key
            assert session.cookies is not source.cookies
            clients.append(session)
            return [{'novel_no': term}]
        monkeypatch.setattr(scraper, 'search_novels', search)
        results = scraper.fetch_terms('Search', ['a', 'b', 'c', 'd'], 4, 1, 0, source)
        assert len(posts) == 1 and len({id(client) for client in clients}) == len(results) == 4
    assert key not in capsys.readouterr().out
    for client in clients:
        client.close()


def test_login_retries_temporary_errors_and_verifies_account(monkeypatch, capsys):
    scraper = scraper_module()
    credentials(monkeypatch)
    calls, waits = [], []
    with scraper.make_search_session() as session:
        def post(*args, **kwargs):
            calls.append(session.cookies['LOGINKEY'])
            if len(calls) == 1:
                raise requests.ConnectionError('fixture@example.invalid fixture-password')
            return response(503) if len(calls) == 2 else response()
        monkeypatch.setattr(session, 'post', post)
        monkeypatch.setattr(session, 'get', lambda *a, **k: response(body='var mem_no = 42;'))
        assert scraper.login_metadata_session(session, sleep=waits.append)
    assert len(set(calls)) == 3 and waits == [1, 2]
    output = capsys.readouterr().out
    assert 'attempt 1/4' in output and 'attempt 2/4' in output
    assert 'fixture@example.invalid' not in output and 'fixture-password' not in output


def test_rejected_login_does_not_retry_password_or_fall_back_to_old_key(monkeypatch):
    scraper = scraper_module()
    credentials(monkeypatch)
    monkeypatch.setenv('NPIA_METADATA_LOGINKEY', 'obsolete-fixture-key')
    posts = []
    with scraper.make_search_session() as session:
        monkeypatch.setattr(session, 'post', lambda *a, **k: posts.append(True) or response())
        monkeypatch.setattr(session, 'get', lambda *a, **k: response(body='var mem_no = 0;'))
        with pytest.raises(RuntimeError, match='rejected or requires an interactive check'):
            scraper.login_metadata_session(session, sleep=lambda _: pytest.fail('Rejected login retried'))
    assert len(posts) == 1


def test_exhausted_login_retries_have_safe_error_messages(monkeypatch, capsys):
    scraper = scraper_module()
    credentials(monkeypatch)
    posts = []
    with scraper.make_search_session() as session:
        def fail(*args, **kwargs):
            posts.append(True)
            raise requests.ConnectionError('fixture@example.invalid fixture-password')
        monkeypatch.setattr(session, 'post', fail)
        with pytest.raises(RuntimeError, match='unavailable after retries') as error:
            scraper.login_metadata_session(session, sleep=lambda _: None)
    assert len(posts) == 4
    text = str(error.value) + capsys.readouterr().out
    assert 'fixture@example.invalid' not in text and 'fixture-password' not in text
    assert error.value.__suppress_context__


def test_workflow_passes_password_secrets_only_to_collection_step():
    from pathlib import Path
    source = Path('.github/workflows/rescrape-npia.yml').read_text(encoding='utf-8')
    for name in ('NPIA_METADATA_EMAIL', 'NPIA_METADATA_PASSWORD'):
        assert source.count('${{ secrets.' + name + ' }}') == 1
        assert source.index('AUTHOR_SWEEP_ID:') < source.index(name + ':') < source.index('python scripts/rescrape_npia_noauth.py')
    assert 'NPIA_METADATA_LOGINKEY' not in source and 'config.json' not in source
