"""Anonymous Munpia catalog, detail metadata, and named ranking snapshots.

No chapter listing, reader, profile, or account endpoints are used. Collection
and publication are separate; the shared runner controls staging and budgets.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
import math
import re

try:
    from .metadata_common import (
        BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult,
        run_cli, source_date, utc_now,
    )
except ImportError:
    from metadata_common import (
        BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult,
        run_cli, source_date, utc_now,
    )


ORIGIN = "https://www.munpia.com"
CATALOG_URL = ORIGIN + "/api/v1/pc/remocon/novels"
DETAIL_URL = ORIGIN + "/api/v1/pc/novel-detail/{}"
RANKING_URL = ORIGIN + "/api/v1/main/best24"
KOREA = timezone(timedelta(hours=9))
BOARDS = (
    ("TODAY_BEST", "Free web novels: Today Best"),
    ("PLATINUM_TODAY_BEST", "Paid web novels: Today Best"),
    ("CONTEST_BEST", "Contest: Today Best"),
)


def numeric_id(value):
    text = str(value or "").strip()
    return text if re.fullmatch(r"[0-9]+", text) and int(text) > 0 else None


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value) if "." in value else int(value)
    return None


def flag(value):
    return int(value) if isinstance(value, bool) else None


def labels(values):
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(
        value.strip() for value in values if isinstance(value, str) and value.strip()
    ))


def result_of(payload):
    if not isinstance(payload, dict) or payload.get("code") != "M000_00000":
        raise ValueError("Munpia returned an unsuccessful metadata response")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Munpia metadata result is not an object")
    return result


def normalize_detail(info):
    """Map explicit source fields without treating absent values as zero."""
    if not isinstance(info, dict):
        raise ValueError("Missing novelInfo metadata")
    novel_id = numeric_id(info.get("id"))
    title = info.get("title")
    if not novel_id or not isinstance(title, str) or not title.strip():
        raise ValueError("Missing reliable Munpia novel ID/title")
    genres = labels(info.get("genres"))
    keywords = labels([
        item.get("title") for item in (info.get("tags") or []) if isinstance(item, dict)
    ])
    metrics = {
        "favorites": number(info.get("preferenceCount")),
        "characters": number(info.get("characters")),
        "free_episodes": number(info.get("freeChapterCount")),
        "episode_unit": info.get("unitType") or None,
    }
    tier = "ebook" if info.get("ebook") is True else (
        "paid" if info.get("paidSerial") is True else (
            "free" if info.get("free") is True else info.get("groupName") or None
        )
    )
    contributors = []
    for field, role in (("authorName", "author"), ("illustratorName", "illustrator")):
        if isinstance(info.get(field), str) and info[field].strip():
            contributors.append({"name": info[field].strip(), "role": role})
    return {
        "id": novel_id,
        "title": title.strip(),
        "author": info.get("authorName") or "",
        "contributors": contributors,
        "cover": info.get("coverUrl") or "",
        "genres": genres,
        "keywords": keywords,
        "tags": list(dict.fromkeys(genres + keywords)),
        "synopsis": info.get("introduction") or None,
        "views": number(info.get("viewCount")),
        "likes": number(info.get("likeCount")),
        "episodes": number(info.get("chapterCount")),
        "complete": flag(info.get("finish")),
        "paused": info.get("pause") if isinstance(info.get("pause"), bool) else None,
        "updated": info.get("updatedAt") or None,
        "published": info.get("createdAt") or None,
        "source_dates": {key: source_date(info[field]) for key, field in
                         (("published", "createdAt"), ("updated", "updatedAt"))
                         if info.get(field) not in (None, "")},
        "age": (19 if info["adult"] else 0) if isinstance(info.get("adult"), bool) else None,
        "canonical_url": ORIGIN + "/novel/detail/" + novel_id,
        "purchase_url": None,
        "tier": tier,
        "metrics": metrics,
        "isbn": info.get("isbn") or None,
        "publication_flags": {
            key: info[key] for key in ("free", "paidSerial", "ebook", "epub", "rental")
            if isinstance(info.get(key), bool)
        },
    }


def fetch_failure(error):
    status_code = getattr(error, "status_code", None)
    status = "restricted" if status_code in (401, 403) else (
        "unavailable" if status_code in (404, 410) else "failed"
    )
    return MetadataResult(status=status, record=None, reason=str(error))


class MunpiaAdapter:
    source = "munpia"
    label = "Munpia"

    @staticmethod
    def is_allowed_url(url):
        try:
            parts = urlsplit(url)
            invalid_port = parts.port not in (None, 443)
        except ValueError:
            return False
        if (parts.scheme != "https" or parts.hostname != "www.munpia.com"
                or parts.username is not None or invalid_port):
            return False
        if not set(parse_qs(parts.query, keep_blank_values=True)).issubset({
            "novelType", "gradeType", "serialStatus", "genreCode", "contestOnly",
            "adultOnly", "keyword", "order", "adultMode", "page", "size", "sort",
            "section", "startDateTime", "endDateTime",
        }):
            return False
        return parts.path in ("/api/v1/pc/remocon/novels", "/api/v1/main/best24") or bool(
            re.fullmatch(r"/(?:api/v1/pc/novel-detail|novel/detail)/[0-9]+", parts.path)
        )

    def partitions(self, client):
        return [{"key": "all-public", "tier": "all", "start_page": 0,
                 "adult_mode": False}]

    def fetch_page(self, client, partition, page):
        payload = client.get_json(CATALOG_URL, params={
            "novelType": "ALL", "gradeType": "", "serialStatus": "",
            "genreCode": "ALL", "contestOnly": "false", "adultOnly": "false",
            "keyword": "", "order": "UPDATED_AT", "adultMode": "false",
            "page": page, "size": 40, "sort": "",
        })
        result = result_of(payload)
        items = result.get("items")
        if not isinstance(items, list) or not isinstance(result.get("hasNext"), bool):
            raise ValueError("Invalid Munpia catalog items/pagination")
        records = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid Munpia catalog row")
            novel_id = numeric_id(item.get("novelId"))
            title = item.get("novelTitle")
            if not novel_id or not isinstance(title, str) or not title.strip():
                raise ValueError("Munpia catalog row lacks ID/title")
            records.append({
                "id": novel_id, "title": title.strip(),
                "author": item.get("authorName") or "",
                "canonical_url": ORIGIN + "/novel/detail/" + novel_id,
                "tier": None,
            })
        if not records and result["hasNext"]:
            raise ValueError("Empty Munpia catalog page still advertises another page")
        return CatalogPage(records=records, next_page=page + 1 if result["hasNext"] else None,
                           observed_total=result.get("total"))

    def detail(self, client, record):
        novel_id = numeric_id(record.get("id"))
        if not novel_id:
            return MetadataResult(status="failed", record=None, reason="Invalid Munpia source ID")
        try:
            info = result_of(client.get_json(DETAIL_URL.format(novel_id))).get("novelInfo")
            normalized = normalize_detail(info)
            if normalized["id"] != novel_id:
                raise ValueError("Munpia detail returned a different novel ID")
            return MetadataResult(status="success", record=normalized)
        except BudgetExceeded:
            raise
        except FetchError as error:
            return fetch_failure(error)
        except (ValueError, TypeError, KeyError) as error:
            return MetadataResult(status="failed", record=None, reason=str(error))

    def rankings(self, client, *, skip_keys=()):
        # Ask for one completed hourly snapshot, not 24 histories per run.
        snapshot = (datetime.now(KOREA) - timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0,
        )
        source_time = snapshot.strftime("%Y-%m-%dT%H:%M:%S")
        for key, label in BOARDS:
            if key in skip_keys:
                continue
            try:
                result = result_of(client.get_json(RANKING_URL, params={
                    "section": key, "startDateTime": source_time, "endDateTime": source_time,
                }))
                hours = result.get("hours")
                if not isinstance(hours, list):
                    raise ValueError("Missing Munpia hourly ranking snapshots")
                matching = [hour for hour in hours if isinstance(hour, dict)
                            and hour.get("hour") == snapshot.hour]
                if len(matching) != 1 or not isinstance(matching[0].get("novels"), list):
                    raise ValueError("Requested Munpia ranking hour is unavailable")
                records = []
                seen = set()
                for item in matching[0]["novels"]:
                    novel_id = numeric_id(item.get("novelId")) if isinstance(item, dict) else None
                    rank = item.get("rank") if isinstance(item, dict) else None
                    if not novel_id or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                        raise ValueError("Invalid Munpia source ranking row")
                    if novel_id in seen:
                        raise ValueError("Duplicate novel in Munpia ranking snapshot")
                    seen.add(novel_id)
                    records.append({
                        "id": novel_id, "rank": rank,
                        "title": item.get("title") or "",
                        "author": item.get("author") or "",
                        "canonical_url": ORIGIN + "/novel/detail/" + novel_id,
                        "age": (19 if item["adult"] else 0) if isinstance(item.get("adult"), bool) else None,
                        # These belong to this ranking window, not the detail catalog.
                        "metrics": {"views": number(item.get("viewCount")),
                                    "favorites": number(item.get("preferCount")),
                                    "score": number(item.get("score"))},
                        "window": {"start": source_time, "end": source_time,
                                   "timezone": "Asia/Seoul", "hour": snapshot.hour},
                    })
                if not records:
                    raise ValueError("Empty Munpia ranking snapshot")
                yield RankingResult(key=key, label=label, records=records,
                                    observed_at=snapshot.astimezone(timezone.utc).isoformat(), success=True)
            except BudgetExceeded:
                raise
            except (FetchError, ValueError, TypeError, KeyError) as error:
                yield RankingResult(key=key, label=label, records=[], observed_at=utc_now(),
                                    success=False, error=str(error))


if __name__ == "__main__":
    raise SystemExit(run_cli(MunpiaAdapter()))
