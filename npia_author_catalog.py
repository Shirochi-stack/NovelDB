"""Shared public npia author catalogs for metadata and desktop ID retrieval."""

import time

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


def iter_author_pages(session, author, *, start_page=1, max_pages=None, delay=2.0,
                      stopped=lambda: False, sleep=time.sleep, page_size=20):
    """Yield validated pages; callers retain each page before requesting the next."""
    if not str(author).isdigit() or int(author) <= 0:
        raise ValueError("invalid author ID")
    if not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError('author page size must be between 1 and 100')
    page = start_page
    signatures = set()
    requests_made = 0
    while max_pages is None or requests_made < max_pages:
        remaining = max(0.0, delay)
        while remaining > 0 and not stopped():
            interval = min(remaining, 0.1)
            sleep(interval)
            remaining -= interval
        if stopped():
            return
        response = session.post(AUTHOR_URL, data={
            "mode": "get_member_writer_novel", "mem_no": str(author),
            "paging[rowCount]": page_size, "paging[curPage]": page,
            "paging[order]": "date", "paging[sort][date]": 1,
        }, timeout=30, allow_redirects=False)
        requests_made += 1
        if response.status_code != 200:
            raise ValueError(f"author catalog HTTP {response.status_code}")
        data = response.json()
        if str(data.get("status")) != "200":
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
        signatures.add(signature)
        if not rows and (page - 1) * page_size < total:
            raise ValueError("author catalog returned an incomplete page")
        more = page * page_size < total
        if more and len(rows) < page_size:
            raise ValueError("author catalog returned an incomplete page")
        yield page, rows, more
        if not more:
            return
        page += 1
