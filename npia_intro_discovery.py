"""Bounded public intro-catalog discovery, including anonymous R19 authors."""

import re
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from noveldb.core.net_timeouts import request_timeout

ORIGIN = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"


def discover(session, state, *, max_requests=6, delay=2.0, stopped=lambda: False,
             log=print, sleep=time.sleep):
    checkpoint = state.setdefault("intro", {})
    resolved = checkpoint.setdefault("resolved_authors", {})
    found = {}
    requests_made = 0
    failures = 0

    def request(method, path, **kwargs):
        nonlocal requests_made
        remaining = max(0.0, delay)
        while remaining > 0 and not stopped():
            interval = min(0.1, remaining)
            sleep(interval)
            remaining -= interval
        if stopped() or requests_made >= max_requests:
            return None
        requests_made += 1
        response = getattr(session, method)(ORIGIN + path, timeout=request_timeout(30), allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise ValueError(f"public intro HTTP {response.status_code}")
        return response

    def read_cards(path):
        response = request("get", path)
        if response is None:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select(".intro-item[data-novel_no][data-novel_age]")
        if not cards:
            raise ValueError("public intro cards unavailable")
        initial_ids = []
        for card in cards:
            nid = card["data-novel_no"]
            if not nid.isdigit():
                continue
            initial_ids.append(nid)
            if card["data-novel_age"] != "19":
                continue
            writer = card.select_one(".intro-novel-info p")
            title = card.select_one(".novel-title")
            row = dict(novel_no=int(nid), novel_age=19,
                       writer_nick=writer.get_text(strip=True) if writer else "",
                       novel_name=title.get_text(strip=True) if title else "")
            found[nid] = row
            owner = resolved.get(nid)
            if not owner:
                response = request("get", f"/novel/{nid}")
                if response is None:
                    continue
                detail = BeautifulSoup(response.text, "html.parser")
                canonical = detail.select_one('meta[property="og:url"]')
                if canonical and urlsplit(canonical.get("content", "")).path.rstrip("/") != f"/novel/{nid}":
                    raise ValueError("intro detail identity mismatch")
                link = detail.select_one(".epnew-novel-info .in-writer .writer-name[href]")
                match = re.fullmatch(r"/user/(\d+)/?", link.get("href", "")) if link else None
                if match:
                    owner = match[1]
                    resolved[nid] = owner
            if owner:
                row["mem_no"] = owner

        return initial_ids

    # The default list has different seeds and no working pagination.
    # Fetch it before campaign pagination can consume the shared request budget.
    try:
        read_cards("/intro")
    except Exception as exc:
        failures += 1
        log(f"Public intro default PARTIAL; discoveries retained: {type(exc).__name__}: {exc}")

    try:
        initial_ids = read_cards("/intro/megabox")
        if not initial_ids:
            return list(found.values())
        excluded = list(dict.fromkeys(checkpoint.get("excluded_ids", []) + initial_ids))
        page = max(2, int(checkpoint.get("page", 2)))
        checkpoint.update(page=page, excluded_ids=excluded)
        while requests_made < max_requests and not stopped():
            response = request("post", "/proc/intro", data={
                "cmd": "ajax_intro_list", "page": page, "cate": "megabox", "csrf": "",
                "novel_no_arr[]": excluded,
            })
            if response is None:
                break
            payload = response.json()
            if payload.get("result") != "success":
                raise ValueError("public intro API error")
            rows = payload.get("data", {}).get("list")
            if not isinstance(rows, list):
                raise ValueError("invalid public intro response")
            if not rows:
                checkpoint.update(page=2, excluded_ids=initial_ids)
                break
            new_ids = []
            for row in rows:
                if not isinstance(row, dict) or not str(row.get("novel_no", "")).isdigit():
                    raise ValueError("invalid public intro record")
                nid = str(row["novel_no"])
                if nid not in excluded:
                    new_ids.append(nid)
                if str(row.get("novel_age")) == "19":
                    found[nid] = row
            if not new_ids:
                raise ValueError("public intro repeated an excluded page")
            excluded = list(dict.fromkeys(excluded + new_ids))
            page += 1
            checkpoint.update(page=page, excluded_ids=excluded)
    except Exception as exc:
        failures += 1
        log(f"Public intro PARTIAL; discoveries retained: {type(exc).__name__}: {exc}")
    finally:
        checkpoint["last_run"] = dict(requests=requests_made, r19_novels=len(found), failed=failures)
        log(f"Public intro: {len(found)} R19 novels discovered in {requests_made} requests")
    return list(found.values())
