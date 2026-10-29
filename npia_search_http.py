"""Shared npia search response diagnostics and partial-result failures."""

import re
import time

from noveldb.core.net_timeouts import request_timeout
from npia_search_terms import RETRYABLE_STATUS_CODES

SEARCH_PAGE_ROWS = 2000
MIN_SEARCH_PAGE_ROWS = 250
MAX_SEARCH_RESULTS = 600000


class SearchMemoryError(RuntimeError):
    """The public service could not construct this search response in memory."""


class PartialSearchError(RuntimeError):
    def __init__(self, items, page, error):
        self.items = items
        super().__init__(f"page {page}: {error}")


def response_details(response):
    """Log bounded body text without request URLs, cookies, or auth headers."""
    body = response.text
    body = re.sub(
        r'''(?ix)(["']?(?:loginkey|access_token|refresh_token|api_key|authorization|password|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s<>&,;]+)''',
        r'\1[redacted]', body,
    )
    preview = " ".join(body.split())[:240]
    content_type = response.headers.get("Content-Type", "unknown")
    return (f"HTTP {response.status_code}; content-type={content_type!r}; "
            f"body-chars={len(response.text)}; preview={preview!r}")


def get_json_with_retry(session, params, headers, retries, timeout=120):
    last_error = None
    for attempt in range(1, max(1, retries) + 1):
        response = None
        memory_error = False
        try:
            response = session.get(
                "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/proc/novel",
                params=params, headers=headers, timeout=request_timeout(timeout),
            )
            body = response.text.lstrip('\ufeff \t\r\n')
            memory_error = not body.startswith(('{', '[')) and bool(
                re.search(r'out of memory|allowed memory size.*exhausted', body, re.I | re.S))
            if memory_error:
                raise ValueError('source server memory exhausted')
            if response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 400:
                raise ValueError(f"HTTP {response.status_code}")
            if not response.text.strip():
                raise ValueError("empty response body")
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise ValueError("expected a JSON object with a list field")
            if str(data.get('status', 200)) != '200':
                raise ValueError(f"search API status {data['status']}")
            try:
                total = int(data.get('total_cnt', 0))
            except (TypeError, ValueError):
                total = 0
            offset = (int(params.get('page', 1)) - 1) * int(params.get('rows', 0))
            if not data['list'] and offset < total:
                raise ValueError('search page unavailable: empty page before reported total')
            return data
        except Exception as exc:
            # Transport exception messages can contain credential-bearing URLs.
            reason = str(exc) if response is not None else type(exc).__name__
            details = response_details(response) if response is not None else "no response received"
            error_type = SearchMemoryError if memory_error else RuntimeError
            last_error = error_type(f"{reason}; {details}")
            print(f"Search {params.get('search_val')!r} page {params.get('page')} "
                  f"attempt {attempt}/{max(1, retries)} failed: {last_error}", flush=True)
            if memory_error and int(params.get('rows', 0)) > MIN_SEARCH_PAGE_ROWS:
                # Let the paginator retry this offset with fewer rows instead
                # of repeatedly sending the same oversized response request.
                raise last_error from exc
            if attempt < max(1, retries):
                time.sleep(min(2 ** (attempt - 1), 8))
    raise last_error


def paginated_search(fetch, params, *, rows=SEARCH_PAGE_ROWS,
                     min_rows=MIN_SEARCH_PAGE_ROWS, max_results=MAX_SEARCH_RESULTS):
    """Shrink on memory failures, preserving the exact collected result offset."""
    if min_rows < 1 or rows < min_rows or max_results < 1:
        raise ValueError('invalid search pagination limits')
    items = []
    offset = 0
    while offset < max_results:
        page = offset // rows + 1
        skip = offset % rows
        request = dict(params, page=page, rows=rows)
        try:
            data = fetch(request)
        except SearchMemoryError as exc:
            if rows <= min_rows:
                raise PartialSearchError(items, page, exc) from exc
            smaller = max(min_rows, rows // 2)
            print(f"Search {params.get('search_val')!r}: source memory failure; "
                  f"reducing page size {rows} -> {smaller}; resuming at result {offset}", flush=True)
            rows = smaller
            continue
        except Exception as exc:
            raise PartialSearchError(items, page, exc) from exc
        if not data:
            break
        batch = data['list']
        new_rows = batch[skip:skip + max_results - offset]
        items.extend(new_rows)
        offset += len(new_rows)
        if len(batch) < rows:
            break
    return items
