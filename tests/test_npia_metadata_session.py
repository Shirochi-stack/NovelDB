"""Optional metadata account sessions must propagate without reading account config."""
import importlib
import io
import sys
from unittest.mock import patch

import pytest
import requests


def scraper_module():
    class Output(io.StringIO):
        def reconfigure(self, **kwargs):
            pass
    with patch.object(sys, 'stdout', Output()):
        return importlib.import_module('scripts.rescrape_npia_noauth')


@pytest.mark.parametrize('value', [None, '', '  '])
def test_no_secret_keeps_session_anonymous(monkeypatch, value):
    scraper = scraper_module()
    if value is None:
        monkeypatch.delenv('NPIA_METADATA_LOGINKEY', raising=False)
    else:
        monkeypatch.setenv('NPIA_METADATA_LOGINKEY', value)
    with scraper.make_search_session() as session:
        assert not list(session.cookies)


def test_secret_cookie_is_source_scoped_secure_and_does_not_read_config(monkeypatch, capsys):
    import builtins
    scraper = scraper_module()
    key = 'fixture-private-session'
    monkeypatch.setenv('NPIA_METADATA_LOGINKEY', key)
    original = builtins.open
    def guarded(file, *args, **kwargs):
        assert str(file).replace('\\', '/').split('/')[-1] != 'config.json'
        return original(file, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', guarded)
    with scraper.make_search_session() as session:
        origin = scraper.COVER_PREFIX
        own = session.prepare_request(requests.Request('GET', origin + '/proc/novel'))
        foreign = session.prepare_request(requests.Request('GET', 'https://example.com/image'))
        insecure = session.prepare_request(requests.Request('GET', origin.replace('https:', 'http:')))
        assert own.headers['Cookie'] == 'LOGINKEY=' + key
        assert 'Cookie' not in foreign.headers and 'Cookie' not in insecure.headers
    assert key not in capsys.readouterr().out


def test_all_search_workers_use_independent_sessions_with_the_secret(monkeypatch, capsys):
    scraper = scraper_module()
    monkeypatch.setenv('NPIA_METADATA_LOGINKEY', 'fixture-worker-session')
    clients = []
    def search(session, term, retries):
        assert session.cookies['LOGINKEY'] == 'fixture-worker-session'
        clients.append(session)
        return [{'novel_no': term}]
    monkeypatch.setattr(scraper, 'search_novels', search)
    results = scraper.fetch_terms('Search', ['a', 'b', 'c', 'd'], 4, 1, 0)
    assert len({id(client) for client in clients}) == len(results) == 4
    assert 'fixture-worker-session' not in capsys.readouterr().out
    for client in clients:
        client.close()


def test_invalid_secret_does_not_echo_it(monkeypatch):
    scraper = scraper_module()
    monkeypatch.setenv('NPIA_METADATA_LOGINKEY', 'fixture-secret\nsecond-value')
    with pytest.raises(ValueError, match='single value') as error:
        scraper.make_search_session()
    assert 'fixture-secret' not in str(error.value)


def test_workflow_passes_secret_only_to_collection_step():
    from pathlib import Path
    source = Path('.github/workflows/rescrape-npia.yml').read_text(encoding='utf-8')
    assert source.count('${{ secrets.NPIA_METADATA_LOGINKEY }}') == 1
    collection = source.index('NPIA_METADATA_LOGINKEY:')
    assert source.index('AUTHOR_SWEEP_ID:') < collection < source.index('python scripts/rescrape_npia_noauth.py')
    assert 'config.json' not in source
