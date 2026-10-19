"""Anonymous Mpia catalog, detail metadata, and named ranking snapshots.

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


ORIGIN = "https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com"
CATALOG_URL = ORIGIN + "/api/v1/pc/remocon/novels"
DETAIL_URL = ORIGIN + "/api/v1/pc/novel-detail/{}"
RANKING_URL = ORIGIN + "/api/v1/main/best24"
SEARCH_URL = ORIGIN + "/api/v1/main/search"
CATALOG_BATCH_SIZE = 100
SEARCH_BATCH_SIZE = 200
SEARCH_MAX_PAGES = 200
SEARCH_QUERIES = ("판타지", "무협", "로맨스")
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
        raise ValueError("Mpia returned an unsuccessful metadata response")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Mpia metadata result is not an object")
    return result


def normalize_detail(info):
    """Map explicit source fields without treating absent values as zero."""
    if not isinstance(info, dict):
        raise ValueError("Missing novelInfo metadata")
    novel_id = numeric_id(info.get("id"))
    title = info.get("title")
    if not novel_id or not isinstance(title, str) or not title.strip():
        raise ValueError("Missing reliable Mpia novel ID/title")
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


def normalize_search(item):
    """Only fields explicitly supplied by the anonymous search response."""
    if not isinstance(item, dict):
        raise ValueError("Invalid Mpia search row")
    novel_id = numeric_id(item.get("novelId"))
    title = item.get("title")
    if not novel_id or not isinstance(title, str) or not title.strip():
        raise ValueError("Mpia search row lacks ID/title")
    synopsis = item.get("story")
    author = item.get("author")
    if not isinstance(synopsis, str) or not synopsis.strip() or not isinstance(author, str) or not author.strip():
        return None  # Ordinary discovery/detail collection still covers this ID.
    genres = labels([item.get("mainGenre"), item.get("subGenre")])
    keywords = labels(item.get("tag"))
    contributors = [{"name": author.strip(), "role": "author"}]
    illustrator = item.get("illustrator")
    if isinstance(illustrator, str) and illustrator.strip():
        contributors.append({"name": illustrator.strip(), "role": "illustrator"})
    updated = item.get("updateAt")
    return {
        "id": novel_id, "title": title.strip(), "author": author.strip(), "contributors": contributors,
        "synopsis": synopsis.strip(), "cover": item.get("coverUrl") or None,
        "genres": genres, "keywords": keywords, "tags": list(dict.fromkeys(genres + keywords)),
        "views": number(item.get("viewCount")), "episodes": number(item.get("entryCount")),
        "complete": flag(item.get("finished")),
        "age": (19 if item["adult"] else 0) if isinstance(item.get("adult"), bool) else None,
        "updated": updated or None, "source_dates": {"updated": source_date(updated)} if updated else {},
        "canonical_url": ORIGIN + "/novel/detail/" + novel_id,
        "tier": "ebook" if item.get("ebook") is True else None,
        "source_group": item.get("groupName") or None,
    }


class MpiaAdapter:
    source = "mpia"
    label = "Mpia"
    detail_fields = ("title", "author")  # The public discovery list only carries these fields.
    detail_refresh_days = 30
    default_delay = 2.0

    def bulk_metadata(self, client, *, skip_keys=()):
        completed = set(skip_keys)
        for query in SEARCH_QUERIES:
            seen = set()
            for page in range(SEARCH_MAX_PAGES):
                key = f"search:{query}:{SEARCH_BATCH_SIZE}:{page}"
                if key + ":end" in completed:
                    break
                if key in completed:
                    continue
                try:
                    result = result_of(client.get_json(SEARCH_URL, params={
                        "query": query, "tab": "ALL", "page": page,
                        "size": SEARCH_BATCH_SIZE, "adultMode": "false",
                    }))
                    items, more = result.get("searchNovelTabDtos"), result.get("hasNext")
                    if (not isinstance(items, list) or not isinstance(more, bool)
                            or len(items) > SEARCH_BATCH_SIZE or (not items and more)):
                        raise ValueError("Invalid Mpia search pagination")
                    records, ids = [], set()
                    for item in items:
                        record = normalize_search(item)
                        ident = numeric_id(item.get("novelId"))
                        if ident in seen or ident in ids:
                            raise ValueError("Mpia search repeated a novel across pages")
                        ids.add(ident)
                        if record is not None:
                            records.append(record)
                    seen.update(ids)
                    yield key if more else key + ":end", records, None
                    if not more:
                        break
                except BudgetExceeded:
                    raise
                except (FetchError, ValueError, TypeError, KeyError) as error:
                    yield key, [], f"Mpia bulk search failed ({type(error).__name__}); query page {page}"
                    break

    def detail_needed(self, old, item, default):
        if not old.get("bulk_metadata_at") or not old.get("synopsis"):
            return default
        if any(old.get(k) != item.get(k) for k in self.detail_fields):
            return True
        try:
            now = datetime.now(timezone.utc)
            # A bulk response never advances the full-detail timestamp. Missing
            # detail-only fields are collected on the ordinary monthly refresh.
            history = old.get("history", {})
            last = history.get("last_success") or history.get("first_seen")
            last = datetime.fromisoformat(last.replace("Z", "+00:00"))
            bulk = datetime.fromisoformat(old["bulk_metadata_at"].replace("Z", "+00:00"))
            if (now - last).total_seconds() >= self.detail_refresh_days * 86400:
                return True
            if (now - bulk).total_seconds() < 7 * 86400:
                return False
        except (AttributeError, TypeError, ValueError):
            pass
        return default

    def prepare_catalog(self, state, partitions):
        cursors = state["progress"].setdefault("partitions", {})
        for partition in partitions:
            cursor = cursors.setdefault(partition["key"], {})
            if cursor.get("page_size") != partition["page_size"]:
                cursor.clear()
                cursor.update(next_page=partition["start_page"], complete=False,
                              page_size=partition["page_size"])

    @staticmethod
    def is_allowed_url(url):
        try:
            parts = urlsplit(url)
            invalid_port = parts.port not in (None, 443)
        except ValueError:
            return False
        if (parts.scheme != "https" or parts.hostname != "www.\u006d\u0075\u006e\u0070\u0069\u0061.com"
                or parts.username is not None or invalid_port):
            return False
        if not set(parse_qs(parts.query, keep_blank_values=True)).issubset({
            "novelType", "gradeType", "serialStatus", "genreCode", "contestOnly",
            "adultOnly", "keyword", "order", "adultMode", "page", "size", "sort",
            "section", "startDateTime", "endDateTime",
            "query", "tab",
        }):
            return False
        if parts.path == "/api/v1/main/search":
            query = parse_qs(parts.query, keep_blank_values=True)
            return not parts.query or (set(query).issubset({"query", "tab", "page", "size", "adultMode"})
                and all(len(values) == 1 for values in query.values())
                and query.get("query", [""])[0] in SEARCH_QUERIES
                and query.get("tab") == ["ALL"] and query.get("adultMode") == ["false"]
                and query.get("size") == [str(SEARCH_BATCH_SIZE)]
                and query.get("page", [""])[0].isdigit()
                and int(query["page"][0]) < SEARCH_MAX_PAGES)
        return parts.path in ("/api/v1/pc/remocon/novels", "/api/v1/main/best24") or bool(
            re.fullmatch(r"/(?:api/v1/pc/novel-detail|novel/detail)/[0-9]+", parts.path)
        )

    def partitions(self, client):
        return [{"key": "all-public", "tier": "all", "start_page": 0,
                 "adult_mode": False, "page_size": CATALOG_BATCH_SIZE}]

    def fetch_page(self, client, partition, page):
        payload = client.get_json(CATALOG_URL, params={
            "novelType": "ALL", "gradeType": "", "serialStatus": "",
            "genreCode": "ALL", "contestOnly": "false", "adultOnly": "false",
            "keyword": "", "order": "UPDATED_AT", "adultMode": "false",
            "page": page, "size": partition.get("page_size", CATALOG_BATCH_SIZE), "sort": "",
        })
        result = result_of(payload)
        items = result.get("items")
        if not isinstance(items, list) or not isinstance(result.get("hasNext"), bool):
            raise ValueError("Invalid Mpia catalog items/pagination")
        records = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid Mpia catalog row")
            novel_id = numeric_id(item.get("novelId"))
            title = item.get("novelTitle")
            if not novel_id or not isinstance(title, str) or not title.strip():
                raise ValueError("Mpia catalog row lacks ID/title")
            records.append({
                "id": novel_id, "title": title.strip(),
                "author": item.get("authorName") or "",
                "canonical_url": ORIGIN + "/novel/detail/" + novel_id,
                "tier": None,
            })
        if not records and result["hasNext"]:
            raise ValueError("Empty Mpia catalog page still advertises another page")
        return CatalogPage(records=records, next_page=page + 1 if result["hasNext"] else None,
                           observed_total=result.get("total"))

    def detail(self, client, record):
        novel_id = numeric_id(record.get("id"))
        if not novel_id:
            return MetadataResult(status="failed", record=None, reason="Invalid Mpia source ID")
        try:
            info = result_of(client.get_json(DETAIL_URL.format(novel_id))).get("novelInfo")
            normalized = normalize_detail(info)
            if normalized["id"] != novel_id:
                raise ValueError("Mpia detail returned a different novel ID")
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
                    raise ValueError("Missing Mpia hourly ranking snapshots")
                matching = [hour for hour in hours if isinstance(hour, dict)
                            and hour.get("hour") == snapshot.hour]
                if len(matching) != 1 or not isinstance(matching[0].get("novels"), list):
                    raise ValueError("Requested Mpia ranking hour is unavailable")
                records = []
                seen = set()
                for item in matching[0]["novels"]:
                    novel_id = numeric_id(item.get("novelId")) if isinstance(item, dict) else None
                    rank = item.get("rank") if isinstance(item, dict) else None
                    if not novel_id or isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                        raise ValueError("Invalid Mpia source ranking row")
                    if novel_id in seen:
                        raise ValueError("Duplicate novel in Mpia ranking snapshot")
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
                    raise ValueError("Empty Mpia ranking snapshot")
                yield RankingResult(key=key, label=label, records=records,
                                    observed_at=snapshot.astimezone(timezone.utc).isoformat(), success=True)
            except BudgetExceeded:
                raise
            except (FetchError, ValueError, TypeError, KeyError) as error:
                yield RankingResult(key=key, label=label, records=[], observed_at=utc_now(),
                                    success=False, error=str(error))


if __name__ == "__main__":
    raise SystemExit(run_cli(MpiaAdapter()))
