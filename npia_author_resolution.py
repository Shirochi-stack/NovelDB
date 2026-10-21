"""Resolve missing public R19 author identities from known novel URLs only."""

import copy
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import random
import re
import threading
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from requests.sessions import Session

import npia_author_registry as authors
from npia_search_http import response_details

ORIGIN = 'https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com'


def parse_identity(html, nid):
    """Accept only the requested novel's own author link, never a recommendation."""
    soup = BeautifulSoup(html, 'html.parser')
    canonical = soup.select_one('meta[property="og:url"]')
    if not canonical or urlsplit(canonical.get('content', '')).path.rstrip('/') != f'/novel/{nid}':
        raise ValueError('novel identity unavailable or mismatched')
    info = soup.select_one('.epnew-novel-info')
    link = info.select_one('.in-writer .writer-name[href]') if info else None
    owner = re.fullmatch(r'/user/([1-9]\d*)(?:/writer_novel)?/?', link.get('href', '')) if link else None
    if not owner:
        raise ValueError('public author identity unavailable')
    result = dict(id=int(nid), author_id=owner[1], author=link.get_text(strip=True))
    if info.select_one('.in-badge .b_19'):
        result['age'] = 19
    return result


def resolve_missing(session, registry, *, failures=None, workers=4, delay=1.0, delay_max=2.0,
                    retries=4, max_requests=None, stopped=lambda: False, checkpoint=lambda: None,
                    log=print, sleep=time.sleep):
    """Resolve a representative per unverified R19 group, retaining every verified ID.

    The following catalog pass verifies other works in that group. Any remaining
    same-name novels form fresh targets on the continuation; names never assign IDs.
    """
    if workers < 1 or retries < 1 or delay < 0 or delay_max < delay:
        raise ValueError('invalid identity request settings')
    failures = {} if failures is None else failures
    groups = authors.unresolved_r19_groups(registry, failures)
    queue = iter(ids[0] for _, ids in groups if failures.get(ids[0], 0) < 3)
    stats = dict(groups=len(groups), requests=0, resolved=0, failed=0, new_authors=0)
    lock = threading.Lock()
    local = threading.local()
    clients = []
    closing = threading.Event()

    def exhausted():
        return closing.is_set() or stopped() or (max_requests is not None and stats['requests'] >= max_requests)

    def client():
        if not hasattr(local, 'session'):
            if isinstance(session, Session):
                local.session = Session()
                local.session.headers.update(session.headers)
                local.session.cookies = copy.deepcopy(session.cookies)
                for name in ('auth', 'proxies', 'params', 'verify', 'cert', 'trust_env'):
                    setattr(local.session, name, copy.copy(getattr(session, name)))
                with lock:
                    clients.append(local.session)
            else:
                local.session = session
        return local.session

    def fetch(nid):
        last_error = None
        for attempt in range(1, retries + 1):
            remaining = random.uniform(delay, delay_max)
            while remaining > 0 and not exhausted():
                interval = min(remaining, 0.1)
                sleep(interval)
                remaining -= interval
            with lock:
                if exhausted():
                    return nid, None, last_error
                stats['requests'] += 1
            response = None
            try:
                response = client().get(ORIGIN + f'/novel/{nid}', timeout=30, allow_redirects=False)
                if response.status_code != 200:
                    raise ValueError(f'HTTP {response.status_code}')
                return nid, parse_identity(response.text, nid), None
            except Exception as error:
                # Exception URLs and raw account responses must not enter logs.
                details = response_details(response) if response is not None and hasattr(response, 'headers') else 'no response details'
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                last_error = f'{reason}; {details}'
                log(f'R19 author identity {nid} attempt {attempt}/{retries} failed: {last_error}')
        return nid, None, last_error

    log(f"Resolving {len(groups)} unverified R19 author groups with {workers} workers; "
        f'{delay:g}-{delay_max:g}s random delay; {retries} attempts per identity')
    last_save = time.monotonic()
    since_save = 0
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='npia-identity')
    try:
        running = set()
        while running or not exhausted():
            while len(running) < workers and not exhausted():
                nid = next(queue, None)
                if nid is None:
                    break
                running.add(pool.submit(fetch, nid))
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                running.remove(future)
                nid, novel, error = future.result()
                if novel is not None:
                    was_known = novel['author_id'] in registry['authors']
                    authors.observe(registry, [novel])
                    stats['resolved'] += 1
                    stats['new_authors'] += not was_known
                    failures.pop(nid, None)
                elif error is not None:
                    failures[nid] = failures.get(nid, 0) + 1
                    stats['failed'] += 1
                since_save += 1
                if since_save >= 100 or time.monotonic() - last_save >= 60:
                    checkpoint()
                    log(f"R19 author identities: {stats['resolved']} resolved, {stats['failed']} failed, {stats['requests']} requests")
                    since_save = 0
                    last_save = time.monotonic()
    finally:
        closing.set()
        pool.shutdown(wait=True, cancel_futures=True)
        for connection in clients:
            connection.close()
        registry['r19_resolution_last_run'] = stats
        checkpoint()
    log('R19 author identity backfill: ' + ', '.join(f'{key}={value}' for key, value in stats.items()))
    return stats
