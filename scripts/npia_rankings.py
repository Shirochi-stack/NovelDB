"""Anonymous public ranking collection with board-scoped preservation."""
import re
import random
import time

from bs4 import BeautifulSoup

ORIGIN = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
AUDIENCES = [
    ("all/plus", "all", 10, 12, 13),
    ("adult/plus", "adult", 14, 15, 16),
    ("teen/plus", "teen", 17, 18, 19),
]
PERIODS = [("weekly", "weekly"), ("month", "monthly"), ("today", "daily")]


def parse_ranking(html, *, fragment=False):
    soup = BeautifulSoup(html, "html.parser")
    root = soup if fragment else soup.select_one('#top100_page')
    if root is None:
        return {}
    ids = []
    for node in root.select('[href], [onclick]'):
        value = node.get('href', '') + ' ' + node.get('onclick', '')
        for nid in re.findall(r'/novel/(\d+)(?!\d)', value):
            if nid not in ids:
                ids.append(nid)
    return {nid: rank for rank, nid in enumerate(ids[:100], 1)}


def response_details(response):
    headers = getattr(response, 'headers', {})
    return (f"HTTP {response.status_code}; content-type={headers.get('Content-Type', '')!r}; "
            f"body-chars={len(response.text)}")


def _ranking_attempt(session, period, audience):
    """Read the public board, recovering empty HTML through its own list endpoint."""
    if audience not in ('all/plus', 'teen/plus'):
        return {}
    if period not in dict(PERIODS):
        raise ValueError('Unsupported ranking period')
    # Match the site's navigation URL, including its explicit unfiltered genre.
    url = f'{ORIGIN}/top100/all/{period}/view/{audience}/?main_genre='
    diagnostics = []
    try:
        response = session.get(url, timeout=30)
        ranking = parse_ranking(response.text) if response.status_code == 200 else {}
        if len(ranking) == 100:
            return ranking
        diagnostics.append(f'HTML: {response_details(response)}; ranked={len(ranking)}/100')
    except Exception as error:
        diagnostics.append(f'HTML: {type(error).__name__}: {error}')
    try:
        # This is the public page's "more" request, starting at the first 100.
        response = session.post(ORIGIN + '/proc/rank_more', timeout=30,
            headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': url},
            data={'load': 'top100', 'cate': 'all', 'proc': period, 'info': 'view',
                  'req1': audience.split('/')[0], 'req2': 'plus', 'req3': '',
                  'idx': 0, 'page_cut': 100, 'main_genre': ''})
        data = response.json() if response.status_code == 200 else {}
        ranking = parse_ranking(data.get('result', ''), fragment=True) if isinstance(data, dict) and str(data.get('status', 200)) == '200' else {}
        if len(ranking) == 100:
            return ranking
        diagnostics.append(f'list: {response_details(response)}; ranked={len(ranking)}/100')
    except Exception as error:
        diagnostics.append(f'list: {type(error).__name__}: {error}')
    raise RuntimeError('Public ranking unavailable; ' + '; '.join(diagnostics))


def scrape_ranking(session, period, audience, *, retries=4, sleep=time.sleep, log=print):
    """Retry unavailable public HTML/list responses without querying adult boards."""
    if audience not in ('all/plus', 'teen/plus'):
        return {}
    if period not in dict(PERIODS):
        raise ValueError('Unsupported ranking period')
    for attempt in range(1, max(1, retries) + 1):
        try:
            return _ranking_attempt(session, period, audience)
        except Exception as exc:
            log(f'Public ranking {period} {audience} attempt {attempt}/{max(1, retries)} failed: {exc}')
            if attempt == max(1, retries):
                raise
            sleep(random.uniform(1.0, 2.0))


def collect_rankings(session, *, fetch=scrape_ranking, log=print):
    rankings = {}
    refreshed = 0
    for audience, label, *_ in AUDIENCES:
        for period, period_label in PERIODS:
            board = (audience, period)
            rankings[board] = {}
            if audience == 'adult/plus':
                log(f'{period_label} adult: authentication required; existing board ranks retained')
                continue
            try:
                ranking = fetch(session, period, audience)
                if not ranking:
                    raise RuntimeError('Public ranking contained no entries')
                rankings[board] = ranking
                refreshed += 1
                log(f'{period_label} {label}: {len(ranking)} novels')
            except Exception as error:
                log(f'{period_label} {label}: PUBLIC BOARD UNAVAILABLE; existing board ranks retained; {error}')
    log(f'Public ranking boards refreshed: {refreshed}/6; authenticated adult boards preserved: 3')
    return rankings


def patch_ranks(novel, rankings):
    """A failed period must not suppress another period's successful update."""
    while len(novel) < 20:
        novel.append(0)
    changed = 0
    for audience, _, weekly, monthly, daily in AUDIENCES:
        for period, index in [('weekly', weekly), ('month', monthly), ('today', daily)]:
            board = rankings.get((audience, period))
            if not board:
                continue
            value = board.get(str(novel[0]), 0)
            if novel[index] != value:
                novel[index] = value
                changed += 1
    return changed
