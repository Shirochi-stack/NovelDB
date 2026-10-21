"""Bounded anonymous R19 metadata discovery; never enumerate novel IDs."""

import re
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

ORIGIN = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
REFRESH_SECONDS = 7 * 86400


class PublicDetailUnavailable(ValueError):
    """The site explicitly reports a missing or inaccessible public detail."""


def listing_ids(html):
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for node in soup.select("[href], [onclick]"):
        value = node.get("href", "") + " " + node.get("onclick", "")
        for nid in re.findall(r"/novel/(\d+)(?!\d)", value):
            if nid not in ids:
                ids.append(nid)
    return ids


def parse_detail(html, nid):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one('meta[property="og:url"]')
    if canonical:
        path = urlsplit(canonical.get("content", "")).path.rstrip("/")
        if path != f"/novel/{nid}":
            raise ValueError("detail identity mismatch")
    info = soup.select_one(".epnew-novel-info")
    if not info:
        alert = soup.select_one('#alert_modal')
        if alert and re.search(r'\uC0AD\uC81C\uB41C\s*\uC18C\uC124', alert.get_text(' ', strip=True)):
            raise PublicDetailUnavailable('site reports this novel has been deleted')
        if alert and '\uC798\uBABB\uB41C \uC811\uADFC' in alert.get_text(' ', strip=True):
            raise PublicDetailUnavailable('site reports invalid or unavailable novel access')
        raise ValueError("public detail metadata unavailable")
    title = info.select_one(".epnew-novel-title")
    author = info.select_one(".in-writer .writer-name")
    if not title or not author or not title.get_text(strip=True) or not author.get_text(strip=True):
        raise ValueError("incomplete public detail metadata")
    if not info.select_one(".in-badge .b_19"):
        return None
    result = {"id": int(nid), "age": 19, "title": title.get_text(strip=True),
              "author": author.get_text(strip=True)}
    owner = re.fullmatch(r"/user/(\d+)/?", author.get("href", ""))
    if owner:
        result["author_id"] = owner[1]
    synopsis = info.select_one(".info-graybox .synopsis, .synopsis-story")
    if synopsis and synopsis.get_text(strip=True):
        result["synopsis"] = synopsis.get_text("\n", strip=True)
    tags = list(dict.fromkeys(n.get_text(strip=True).lstrip("#")
                             for n in info.select(".writer-tag .tag")))
    if tags:
        result["tags"] = tags
    labels = {"\uC870\uD68C": "views", "\uCD94\uCC9C": "likes", "\uD68C\uCC28": "chapters"}
    for label in info.select(".category-title"):
        field = labels.get(label.get_text(strip=True))
        if field:
            value = label.find_next_sibling("span")
            match = re.fullmatch(r"([\d,]+)(?:\uD68C\uCC28)?", value.get_text(strip=True)) if value else None
            if match:
                result[field] = int(match[1].replace(",", ""))
    # Adult covers can be hidden; never substitute an OG logo or recommendation.
    cover = info.select_one('img[src*="/imagebox/cover/"]')
    if cover:
        result["cover"] = urljoin(ORIGIN, cover["src"])
    return result


def enrich(session, fresh, existing, old_full, state, *, listing_pages=10,
           detail_limit=200, delay=2.0, now=None, sleep=time.sleep, detail_ids=None):
    now = time.time() if now is None else now
    checked = state.setdefault("checked", {})
    pending = list(dict.fromkeys(str(n) for n in state.get("pending", []) if str(n).isdigit()))
    page = max(1, int(state.get("listing_page", 1)))
    stats = dict(discovered=0, updated=0, skipped=0, unavailable=0, failed=0, listing_failed=0)
    requests_made = 0

    def fetch(path):
        nonlocal requests_made
        if requests_made:
            sleep(delay)
        requests_made += 1
        response = session.get(ORIGIN + path, timeout=30, allow_redirects=False)
        if response.status_code in (404, 410):
            raise PublicDetailUnavailable(f'HTTP {response.status_code}')
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        return response.text

    seen_pages = set()
    budget = listing_pages if len(pending) < max(1, detail_limit) * 2 else 0
    pages = list(range(page, page + budget))
    if page > 1 and budget:
        # Check the head for newly published titles while continuing the backfill.
        pages = [1] + list(range(page, page + budget - 1))
    for current_page in pages:
        try:
            ids = listing_ids(fetch(f"/plus/adult/new/{current_page}/"))
            if not ids:
                # Could be a gate/error page: retain the cursor and try next run.
                raise ValueError("listing contained no novel links")
            signature = tuple(ids)
            if signature in seen_pages:
                page = 1
                break
            seen_pages.add(signature)
            for nid in ids:
                if nid not in pending:
                    pending.append(nid)
                    stats["discovered"] += 1
            if current_page >= page:
                page = current_page + 1
        except Exception as exc:
            stats["listing_failed"] += 1
            print(f"R19 listing page {current_page} FAILED: {type(exc).__name__}: {exc}", flush=True)
            break

    known = {str(row[0]): row for row in existing if len(row) > 11 and str(row[11]) == "19"}
    for nid, row in old_full.items():
        if str(row.get("age")) == "19":
            known.setdefault(nid, None)
    known_ids = sorted(known, key=lambda nid: (checked.get(nid, 0), int(nid)))
    due = lambda nid: now - checked.get(nid, 0) >= REFRESH_SECONDS
    # Reserve a quarter of each run for older catalog entries, avoiding starvation.
    refresh = [nid for nid in known_ids if due(nid)][:detail_limit // 4]
    candidates = list(dict.fromkeys(refresh + [nid for nid in pending if due(nid)] +
                                    [nid for nid in known_ids if due(nid)]))[:detail_limit]
    if detail_ids is not None:
        candidates = list(dict.fromkeys([nid for nid in pending if due(nid)] +
                                        [str(nid) for nid in detail_ids if due(str(nid))]))[:detail_limit]
    for nid in candidates:
        try:
            patch = parse_detail(fetch(f"/novel/{nid}"), nid)
            if patch is None:
                stats["skipped"] += 1
            else:
                base = dict(old_full.get(nid) or {})
                row = known.get(nid)
                if row:
                    for field, index in (("title", 1), ("author", 2), ("cover", 3), ("tags", 4),
                                         ("views", 5), ("likes", 6), ("chapters", 7),
                                         ("complete", 8), ("updated", 9), ("age", 11)):
                        base.setdefault(field, row[index])
                base.update(fresh.get(nid, {}))
                base.update(patch)
                for field, default in (("synopsis", ""), ("cover", ""), ("tags", []),
                                       ("views", 0), ("likes", 0), ("chapters", 0),
                                       ("complete", 0), ("updated", "")):
                    base.setdefault(field, default)
                fresh[nid] = base
                stats["updated"] += 1
            checked[nid] = now
            if nid in pending:
                pending.remove(nid)
        except PublicDetailUnavailable as exc:
            checked[nid] = now
            if nid in pending:
                pending.remove(nid)
            stats['unavailable'] += 1
            print(f'R19 {nid} SKIPPED (public detail unavailable); existing metadata retained: {exc}', flush=True)
        except Exception as exc:
            stats["failed"] += 1
            print(f"R19 {nid} FAILED; existing metadata retained: {type(exc).__name__}: {exc}", flush=True)
    state.update(listing_page=page, pending=pending, checked=checked, last_run=stats)
    print("R19 public metadata: " + ", ".join(f"{key}={value}" for key, value in stats.items()), flush=True)
    return state
