"""Shared public npia author catalogs for metadata and desktop ID retrieval."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
import random
import threading
import time

from requests.sessions import Session as HTTPSession

from noveldb.core.net_timeouts import request_timeout

from npia_search_http import response_details

AUTHOR_URL = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/proc/user"


def author_id(item):
    for field in ("writer_mem_no", "mem_no", "author_id"):
        value = str(item.get(field) or "")
        if value.isdigit() and int(value) > 0:
            return value
    return None


def matches_age(item, age_filter=""):
    try:
        age = int(item.get("novel_age", 0))
    except (ValueError, TypeError):
        return not age_filter
    return age >= 19 if age_filter == "19" else age < 19 if age_filter == "15" else True


def iter_author_pages(session, author, *, start_page=1, max_pages=None, delay=1.0,
                      stopped=lambda: False, sleep=time.sleep, page_size=20,
                      delay_max=None, retries=4, log=lambda message: None,
                      acquire_request=lambda: True, signatures=None):
    """Yield validated pages; callers retain each page before requesting the next."""
    if not str(author).isdigit() or int(author) <= 0:
        raise ValueError("invalid author ID")
    if not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError('author page size must be between 1 and 100')
    delay_max = delay if delay_max is None else delay_max
    if delay < 0 or delay_max < delay or retries < 1:
        raise ValueError('invalid author delay range or retry count')
    page = start_page
    signatures = set() if signatures is None else signatures
    requests_made = 0
    while max_pages is None or requests_made < max_pages:
        last_error = None
        for attempt in range(1, retries + 1):
            remaining = random.uniform(delay, delay_max)
            while remaining > 0 and not stopped():
                interval = min(remaining, 0.1)
                sleep(interval)
                remaining -= interval
            if stopped() or not acquire_request():
                if last_error is not None:
                    raise last_error
                return
            response = None
            try:
                response = session.post(AUTHOR_URL, data={
                    "mode": "get_member_writer_novel", "mem_no": str(author),
                    "paging[rowCount]": page_size, "paging[curPage]": page,
                    "paging[order]": "date", "paging[sort][date]": 1,
                }, timeout=request_timeout(30), allow_redirects=False)
                if response.status_code != 200:
                    raise ValueError(f"author catalog HTTP {response.status_code}")
                data = response.json()
                if not isinstance(data, dict) or str(data.get("status")) != "200":
                    raise ValueError("author catalog API error")
                result = data.get("result", {})
                rows = result.get("novel")
                total = result.get("allCount")
                if not isinstance(rows, list) or not isinstance(total, int) or total < 0:
                    raise ValueError("invalid author catalog response")
                for row in rows:
                    if not isinstance(row, dict) or not str(row.get("novel_no", "")).isdigit():
                        raise ValueError("invalid author catalog record")
                    owner = author_id(row)
                    if owner and owner != str(author):
                        raise ValueError("author catalog identity mismatch")
                signature = tuple(str(row["novel_no"]) for row in rows)
                if rows and signature in signatures:
                    raise ValueError("author catalog repeated a page")
                if not rows and (page - 1) * page_size < total:
                    raise ValueError("author catalog returned an incomplete page")
                more = page * page_size < total
                expected = min(page_size, max(0, total - (page - 1) * page_size))
                if len(rows) < expected:
                    raise ValueError("author catalog returned an incomplete page")
                signatures.add(signature)
                break
            except Exception as exc:
                # Transport errors can contain credential-bearing URLs.
                reason = str(exc) if response is not None else type(exc).__name__
                details = response_details(response) if response is not None and hasattr(response, 'headers') else 'no response details'
                last_error = ValueError(f'{reason}; {details}')
                log(f'Author {author} page {page} attempt {attempt}/{retries} failed: {last_error}')
                if attempt == retries:
                    raise last_error from exc
        requests_made += 1
        yield page, rows, more
        if not more:
            return
        page += 1


def parallel_author_pages(session, authors, *, workers=4, delay=1.0, delay_max=2.0,
                          retries=4, page_size=100, max_requests=None,
                          stopped=lambda: False, log=lambda message: None,
                          fetch_pages=iter_author_pages):
    """Yield pages/errors on the caller thread; at most one page per author is in flight.

    Each worker has its own persistent HTTP session. Only this iterator's caller
    mutates metadata/checkpoints, so out-of-order responses cannot lose updates.
    Returned tuples are (author, page, rows, more, error, HTTP attempts).
    """
    if workers < 1:
        raise ValueError('author workers must be positive')
    queue = deque((str(owner), page) for owner, page in authors.items())
    signatures = {}
    lock = threading.Lock()
    local = threading.local()
    clients = []
    requests_made = 0
    closing = threading.Event()

    def exhausted():
        return closing.is_set() or stopped() or (max_requests is not None and requests_made >= max_requests)

    def client():
        if not hasattr(local, 'session'):
            if isinstance(session, HTTPSession):
                local.session = HTTPSession()
                local.session.headers.update(session.headers)
                local.session.cookies = copy.deepcopy(session.cookies)
                for name in ('auth', 'proxies', 'params', 'verify', 'cert', 'trust_env'):
                    setattr(local.session, name, copy.copy(getattr(session, name)))
                with lock:
                    clients.append(local.session)
            else:
                local.session = session  # Injected transports for offline checks.
        return local.session

    def fetch(owner, page):
        attempts = 0
        def acquire():
            nonlocal attempts, requests_made
            with lock:
                if exhausted():
                    return False
                attempts += 1
                requests_made += 1
                return True
        try:
            result = next(fetch_pages(client(), owner, start_page=page, max_pages=1,
                                      delay=delay, delay_max=delay_max, retries=retries,
                                      page_size=page_size, stopped=exhausted,
                                      acquire_request=acquire, signatures=signatures[owner], log=log), None)
            if result is None:
                return owner, page, None, False, None, attempts
            number, rows, more = result
            return owner, number, rows, more, None, attempts
        except Exception as exc:
            return owner, page, None, False, exc, attempts

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='npia-author')
    try:
        running = {}
        sequence = 0
        while running or (queue and not exhausted()):
            while queue and len(running) < workers and not exhausted():
                owner, page = queue.popleft()
                signatures.setdefault(owner, set())
                running[pool.submit(fetch, owner, page)] = sequence
                sequence += 1
            if not running:
                break
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(finished, key=running.get):
                del running[future]
                event = future.result()
                owner, page, rows, more, error, _ = event
                if rows is not None and more:
                    queue.appendleft((owner, page + 1))
                else:
                    signatures.pop(owner, None)
                yield event
    finally:
        closing.set()
        pool.shutdown(wait=True, cancel_futures=True)
        for connection in clients:
            connection.close()
