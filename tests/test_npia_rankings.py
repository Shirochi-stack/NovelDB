"""Ranking recovery must preserve only the board that actually failed."""
from types import SimpleNamespace

import pytest

from scripts import npia_rankings as ranks


def board_html(ids=range(1, 101)):
    return ''.join(f'<a href="/novel/{nid}">Title</a><a href="/novel/{nid}">Cover</a>' for nid in ids)


class Session:
    def __init__(self, html='', fragment='', status=200):
        self.html, self.fragment, self.status = html, fragment, status
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(('get', url, kwargs))
        return SimpleNamespace(status_code=self.status, text=self.html, headers={'Content-Type': 'text/html'})

    def post(self, url, **kwargs):
        self.calls.append(('post', url, kwargs))
        return SimpleNamespace(status_code=200, text='fixture JSON', headers={'Content-Type': 'application/json'},
                               json=lambda: {'status': 200, 'result': self.fragment})


def test_public_ranking_uses_site_url_and_ignores_unrelated_links():
    session = Session('<a href="/novel/999">Recommendation</a><div id="top100_page">'+board_html()+'</div>')
    result = ranks.scrape_ranking(session, 'weekly', 'all/plus')
    assert result == {str(n): n for n in range(1, 101)}
    assert len(session.calls) == 1
    assert session.calls[0][1].endswith('/top100/all/weekly/view/all/plus/?main_genre=')


@pytest.mark.parametrize('status', [200, 503])
def test_empty_or_failed_public_html_uses_bounded_native_list_request(status):
    session = Session('<div id="top100_page">No entries</div>', board_html(), status)
    assert len(ranks.scrape_ranking(session, 'month', 'teen/plus')) == 100
    assert len(session.calls) == 2
    params = session.calls[1][2]['data']
    assert params['idx'] == 0 and params['page_cut'] == 100
    assert params['req1'] == 'teen' and params['proc'] == 'month'


def test_partial_response_is_not_published_as_a_complete_top100():
    session = Session('<div id="top100_page">'+board_html(range(1, 4))+'</div>', board_html(range(1, 5)))
    with pytest.raises(RuntimeError, match='Public ranking unavailable.*ranked=3/100.*ranked=4/100'):
        ranks.scrape_ranking(session, 'today', 'all/plus', retries=1)
    assert len(session.calls) == 2


def test_only_three_adult_boards_require_auth_and_all_six_public_boards_are_attempted():
    calls, logs = [], []
    def fetch(session, period, audience):
        calls.append((audience, period))
        if (audience, period) == ('all/plus', 'weekly'):
            raise RuntimeError('HTTP 503')
        return {'123': 2}
    result = ranks.collect_rankings(object(), fetch=fetch, log=logs.append)
    assert len(calls) == 6 and all(audience != 'adult/plus' for audience, _ in calls)
    assert sum('authentication required' in line for line in logs) == 3
    assert sum('PUBLIC BOARD UNAVAILABLE' in line for line in logs) == 1
    assert 'refreshed: 5/6' in logs[-1]
    novel = [123] + [0]*9 + [8, 19, 9, 10, 11, 12, 13, 14, 15, 16]
    changes = ranks.patch_ranks(novel, result)
    assert changes == 5
    assert [novel[i] for i in [10, 12, 13]] == [8, 2, 2]
    assert novel[14:17] == [11, 12, 13]
    assert novel[17:20] == [2, 2, 2]


def test_unranked_rows_are_cleared_only_for_successful_boards():
    novel = [999] + [0]*9 + [8]*10
    ranks.patch_ranks(novel, {('all/plus', 'today'): {'123': 1}})
    assert novel[13] == 0
    assert novel[10] == novel[12] == novel[14] == 8


def test_temporarily_unavailable_board_retries_html_and_native_list_then_recovers(monkeypatch):
    class Recovering(Session):
        def get(self, url, **kwargs):
            if len(self.calls) >= 2:
                self.html = '<div id="top100_page">'+board_html()+'</div>'
            return super().get(url, **kwargs)
    session = Recovering()
    waits, logs = [], []
    monkeypatch.setattr(ranks.random, 'uniform', lambda a, b: 1.5)
    result = ranks.scrape_ranking(session, 'month', 'all/plus', sleep=waits.append, log=logs.append)
    assert len(result) == 100
    assert [method for method, *_ in session.calls] == ['get', 'post', 'get']
    assert waits == [1.5] and 'attempt 1/4 failed' in logs[0]


def test_permanently_unavailable_board_uses_four_attempts_and_preserves_old_ranks():
    session = Session()
    waits, logs = [], []
    with pytest.raises(RuntimeError, match='Public ranking unavailable'):
        ranks.scrape_ranking(session, 'month', 'all/plus', sleep=waits.append, log=logs.append)
    assert len(session.calls) == 8
    assert len(waits) == 3 and all(1 <= delay <= 2 for delay in waits)
    assert len(logs) == 4 and 'attempt 4/4 failed' in logs[-1]
    old = [123] + [0]*9 + [8]*10
    ranks.patch_ranks(old, {('all/plus', 'month'): {}})
    assert old[12] == 8


def test_adult_boards_never_make_requests_or_sleep():
    session = Session()
    assert ranks.scrape_ranking(session, 'weekly', 'adult/plus',
                               sleep=lambda _: pytest.fail('No adult retries')) == {}
    assert session.calls == []
