"""Public Joara metadata API adapter without account tokens or saved sessions."""

from __future__ import annotations

from datetime import datetime
from html import unescape
import re
import threading
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup

try:
    from .metadata_common import (
        BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult,
        run_cli, source_date, utc_now,
    )
except ImportError:  # Direct script invocation.
    from metadata_common import (
        BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult,
        run_cli, source_date, utc_now,
    )


BASE = "https://www.joara.com"
API = "https://api.joara.com"
CATALOG_PATHS = {"latest": "/v2/book/latest_book", "finished": "/v2/book/finish_book"}
API_PATHS = set(CATALOG_PATHS.values()) | {"/v1/book/detail.joa", "/v2/book/best_book"}
STORES = {"series": "Free publication", "nobless": "Noblesse", "premium": "Premium"}
RESTRICTION = re.compile(r"성인\s*인증이?\s*필요|본인\s*인증이?\s*필요|로그인이?\s*필요")


def _id(value):
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    return text if re.fullmatch(r"[1-9]\d*", text) else None


def _integer(value):
    if isinstance(value, bool):
        return None
    text = str(value).replace(",", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def _boolean(value):
    if value is True or value == 1 or str(value).upper() in {"TRUE", "Y"}:
        return True
    if value is False or value == 0 or str(value).upper() in {"FALSE", "N"}:
        return False
    return None


def _date(value):
    text = str(value or "").strip()
    for pattern in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            result = datetime.strptime(text, pattern)
            return result.date().isoformat() if pattern == "%Y-%m-%d" else result.isoformat()
        except ValueError:
            continue
    return None


def _text(value):
    if not isinstance(value, str):
        return ""
    # Preserve literal titles in angle brackets while removing ordinary markup.
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>|</(?:p|div)\s*>", "\n", value, flags=re.I)
    value = re.sub(r"</?(?:p|div|span|strong|b|em|i|a|font)\b[^>]*>", "", value, flags=re.I)
    return unescape(value).replace("\r\n", "\n").strip()


def _cover(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else None


def _keywords(value):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(_text(item) for item in value if isinstance(item, str) and _text(item)))


def normalize_listing(row, tier=None):
    if not isinstance(row, dict):
        raise ValueError("Joara catalog row is not an object")
    novel_id, title = _id(row.get("book_code")), _text(row.get("subject"))
    if not novel_id or not title:
        raise ValueError("Joara catalog row lacks identity or title")
    complete, adult = _boolean(row.get("chkfinish")), _boolean(row.get("chkadult"))
    keywords = _keywords(row.get("keyword"))
    genre = _text(row.get("category_name"))
    genres = [genre] if genre else []
    raw_synopsis = row.get("intro") if _text(row.get("intro")) else row.get("introduce")
    synopsis = _text(raw_synopsis)
    restricted = bool(RESTRICTION.search(synopsis))
    if restricted:
        synopsis = ""
    record = {
        "id": novel_id, "title": title, "author": _text(row.get("member_name")),
        "contributors": ([{"name": _text(row.get("member_name")), "role": "author"}]
                         if _text(row.get("member_name")) else []),
        "cover": _cover(row.get("cover")), "genres": genres, "keywords": keywords,
        "tags": list(dict.fromkeys(genres + keywords)), "synopsis": synopsis or None,
        "views": _integer(row.get("page_read")), "likes": None,
        "episodes": _integer(row.get("total_chapter_count")),
        "complete": int(complete) if complete is not None else None,
        "age": (19 if adult else 0) if adult is not None else None,
        "updated": _date(row.get("last_regist_datetime")),
        "created": _date(row.get("first_regist_datetime")),
        "source_dates": {key: source_date(row[field]) for key, field in
                         (("created", "first_regist_datetime"), ("updated", "last_regist_datetime"))
                         if row.get(field) not in (None, "")},
        "canonical_url": f"{BASE}/book/{novel_id}",
        "tier": _text(row.get("store")) or tier, "purchase_url": None,
        "metrics": {"favorites": _integer(row.get("favorite_count")),
                    "recommendations": _integer(row.get("recommend_count"))},
    }
    # The public list response can stop an intro at 1,000 characters mid-sentence.
    # Keep that useful preview, but require the work-detail response to enrich it.
    record["_detail_complete"] = bool(synopsis and len(raw_synopsis) < 1000
                                      and record["author"] and record["cover"])
    return record


def _listing_response(payload, page):
    if not isinstance(payload, dict) or payload.get("status") != 1:
        raise ValueError("Joara rejected the metadata request")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise ValueError("Joara catalog response has no data.list")
    actual_page, total, size = (_integer(payload.get(key)) for key in ("page", "total_cnt", "offset"))
    if actual_page != page or total is None or size is None or size < 1:
        raise ValueError(f"Pagination reset/invalid: requested={page}, returned={actual_page}, "
                         f"rows={len(data['list'])}, total={total}, size={size}")
    rows = data["list"]
    if len(rows) > size or (not rows and (page - 1) * size < total):
        raise ValueError("Joara pagination does not match returned rows")
    # Public totals move independently of page rows; a nonempty short page is not EOF.
    return rows, total, size


def parse_detail(payload, previous):
    if not isinstance(payload, dict) or payload.get("status") != 1:
        return MetadataResult(status="failed", reason="Joara rejected the metadata request")
    book = payload.get("book")
    if not isinstance(book, dict) or _id(book.get("book_code")) != str(previous["id"]):
        return MetadataResult(status="failed", reason="Joara detail identity was missing or mismatched")
    title = _text(book.get("subject"))
    if not title:
        return MetadataResult(status="failed", reason="Joara detail title was missing")
    record = dict(previous)
    record.update(title=title, canonical_url=f"{BASE}/book/{previous['id']}")
    author = _text(book.get("writer_name"))
    if author:
        record["author"] = author
        record["contributors"] = [{"name": author, "role": "author"}]
    image = _cover(book.get("book_img"))
    if image:
        record["cover"] = image
    synopsis = _text(book.get("intro"))
    restricted = bool(RESTRICTION.search(synopsis))
    if synopsis and not restricted:
        record["synopsis"] = synopsis
    keywords = _keywords(book.get("keyword"))
    if isinstance(book.get("keyword"), list):
        record["keywords"] = keywords
        record["tags"] = list(dict.fromkeys(record.get("genres", []) + keywords))
    completed = _boolean(book.get("chk_finish"))
    adult = _boolean(book.get("is_adult"))
    if completed is not None:
        record["complete"] = int(completed)
    if adult is not None:
        record["age"] = 19 if adult else 0
    for target, original in (("episodes", "cnt_chapter"), ("views", "cnt_page_read")):
        value = _integer(book.get(original))
        if value is not None:
            record[target] = value
    metrics = dict(record.get("metrics") or {})
    for target, original in (("favorites", "cnt_favorite"), ("recommendations", "cnt_recom"), ("characters", "total_bytes")):
        # total_bytes is a byte count, not a character count; preserve it as such.
        if original == "total_bytes":
            target = "bytes"
        value = _integer(book.get(original))
        if value is not None:
            metrics[target] = value
    record["metrics"] = metrics
    for field in ("created", "updated"):
        value = _date(book.get(field))
        if value is not None:
            record[field] = value
    record["source_dates"] = {**(record.get("source_dates") or {}),
                              **{key: source_date(book[key]) for key in ("created", "updated", "redate")
                                 if book.get(key) not in (None, "")}}
    record["_detail_complete"] = not restricted
    if restricted:
        return MetadataResult(status="restricted", record=record, reason="Joara limits this public synopsis to verified adults")
    return MetadataResult(status="success", record=record)


class JoaraAdapter:
    source = "joara"
    label = "Joara"

    def __init__(self):
        self._public_params = None
        self._bootstrap_lock = threading.Lock()

    @staticmethod
    def is_allowed_url(url):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "https" or parsed.username is not None or port not in (None, 443):
            return False
        if parsed.hostname == "www.joara.com":
            return not parsed.query and (parsed.path in {"", "/"} or bool(re.fullmatch(r"/static/js/main\.[a-zA-Z0-9]+(?:\.chunk)?\.js", parsed.path)))
        if parsed.hostname != "api.joara.com" or parsed.path not in API_PATHS:
            return False
        return set(parse_qs(parsed.query, keep_blank_values=True)).issubset({
            "api_key", "ver", "device", "deviceuid", "devicetoken", "category",
            "store", "orderby", "page", "offset", "book_code", "promotion_code", "best",
            "use_cursor_pagination", "cursor_point",
        })

    def _bootstrap(self, client):
        with self._bootstrap_lock:
            self._read_public_config(client)

    def _read_public_config(self, client):
        if self._public_params is not None:
            return
        html = client.get_text(BASE + "/")
        soup = BeautifulSoup(html, "html.parser")
        main_urls = [urljoin(BASE, script["src"]) for script in soup.select("script[src]")
                     if self.is_allowed_url(urljoin(BASE, script["src"])) and "/static/js/main." in script["src"]]
        if not main_urls:
            raise FetchError("Joara public main bundle could not be located")
        bundle = client.get_text(main_urls[-1])
        # Read the public production config, never signedInfo or a user's token.
        match = re.search(r'["\']https://api\.joara\.com["\']\s*,\s*apiKey\s*:', bundle)
        if not match:
            raise FetchError("Joara public API configuration changed")
        config = bundle[match.start():match.start() + 600]
        fields = {}
        for key in ("apiKey", "device", "devicetoken", "version"):
            value = re.search(rf'\b{key}\s*:\s*["\']([^"\']*)["\']', config)
            if value is None or not value.group(1):
                raise FetchError("Joara public API configuration was incomplete")
            fields[key] = value.group(1)
        self._public_params = {"api_key": fields["apiKey"], "ver": fields["version"],
                               "device": fields["device"], "devicetoken": fields["devicetoken"],
                               "deviceuid": uuid4().hex}

    def _get(self, client, path, params):
        self._bootstrap(client)
        return client.get_json(API + path, params={**self._public_params, **params})

    def partitions(self, client):
        self._bootstrap(client)
        partitions = [{"key": f"{store}:{catalog}", "tier": store, "start_page": 1,
                       "store": store, "catalog": catalog, "page_size": 20}
                      for store in STORES for catalog in CATALOG_PATHS]
        # Anonymous category responses verified on 2026-09-14. These supplement
        # the all-genre catalog; latest lists use the same public cursor protocol.
        supplemental = [{"key": f"series:latest:category:{code}", "tier": "series", "start_page": 1,
                         "store": "series", "catalog": "latest", "page_size": 20, "category": code}
                        for code in ("22", "9")]  # Romance fantasy and parody.
        partitions = partitions[:1] + supplemental + partitions[1:]
        for partition in partitions:
            if partition["catalog"] == "latest":
                partition["pagination"] = "joara-cursor-v1"
        return partitions

    def fetch_page(self, client, partition, page):
        try:
            cursor_mode = partition.get("pagination") == "joara-cursor-v1"
            params = {
                "category": partition.get("category", "0"), "store": partition["store"], "orderby": "redate",
                "page": 1 if cursor_mode else page, "offset": partition.get("page_size", 20),
            }
            if cursor_mode:
                params.update(use_cursor_pagination="y", cursor_point=partition.get("cursor_point", ""))
            payload = self._get(client, CATALOG_PATHS[partition["catalog"]], params)
            if cursor_mode:
                # Cursor responses intentionally have no numbered `page` field.
                if not isinstance(payload, dict) or payload.get("status") != 1:
                    raise ValueError("Joara rejected the cursor request")
                data = payload.get("data")
                total, size = _integer(payload.get("total_cnt")), _integer(payload.get("offset"))
                if (not isinstance(data, dict) or not isinstance(data.get("list"), list)
                        or total is None or size != params["offset"]):
                    raise ValueError("Malformed Joara cursor response")
                rows = data["list"]
                if len(rows) != min(size, max(0, total - (page - 1) * size)):
                    raise ValueError("Joara cursor response has unexplained row count")
            else:
                rows, total, size = _listing_response(payload, page)
            records, skipped_rows = [], []
            for position, row in enumerate(rows, start=1):
                # A few public listings have a real book ID but a blank title.
                # Preserve every usable row and scan later pages; report these
                # omissions so they cannot establish a complete catalog baseline.
                if (isinstance(row, dict) and _id(row.get("book_code"))
                        and isinstance(row.get("subject"), str) and not _text(row["subject"])):
                    skipped_rows.append({"row": position, "id": _id(row["book_code"]),
                                         "error": "Title unavailable in public catalog"})
                    continue
                try:
                    records.append(normalize_listing(row, partition["tier"]))
                except (ValueError, TypeError, KeyError) as error:
                    raise ValueError(f"row {position}: {error}") from error
            if skipped_rows and not records:
                raise ValueError("No usable titled rows in catalog page")
            records = list({record["id"]: record for record in records}.values())
            return CatalogPage(records=records, next_page=page + 1 if page * size < total else None,
                               observed_total=total, skipped_rows=skipped_rows,
                               next_cursor=payload.get("cursor_point") if cursor_mode else None)
        except BudgetExceeded:
            raise
        except (FetchError, ValueError, TypeError, KeyError) as error:
            return CatalogPage(records=[], next_page=None, complete=False,
                               error=f"Joara {partition.get('key', partition['store'] + ':' + partition['catalog'])} requested page {page}: {type(error).__name__}: {error}")

    def detail(self, client, record):
        if not _id(record.get("id")):
            return MetadataResult(status="failed", reason="Joara metadata identity was invalid")
        try:
            payload = self._get(client, "/v1/book/detail.joa", {"book_code": record["id"], "promotion_code": ""})
            return parse_detail(payload, record)
        except BudgetExceeded:
            raise
        except FetchError as error:
            status = "unavailable" if error.status_code in (404, 410) else "restricted" if error.status_code in (401, 403) else "failed"
            return MetadataResult(status=status, reason=f"Joara metadata HTTP failure ({error.status_code or 'network'})")
        except (ValueError, TypeError, KeyError):
            return MetadataResult(status="failed", reason="Joara metadata could not be parsed")

    def rankings(self, client, *, skip_keys=()):
        for period in ("today", "weekly", "monthly"):
            key = f"all_{period}"
            if key in skip_keys:
                continue
            label = f"Joara · All stores and genres · {period.title()} Best"
            observed_at = utc_now()
            try:
                records, seen, page, previous_size = [], set(), 1, None
                while len(records) < 100:
                    payload = self._get(client, "/v2/book/best_book", {
                        "category": "0", "store": "all", "best": period,
                        "orderby": "cnt_best", "page": page, "offset": 100,
                    })
                    rows, total, size = _listing_response(payload, page)
                    if previous_size is not None and size != previous_size:
                        raise ValueError("Joara native Best board changed its page size")
                    previous_size = size
                    for index, row in enumerate(rows):
                        rank = (page - 1) * size + index + 1
                        if rank > 100:
                            break
                        record = normalize_listing(row)
                        if record["id"] in seen:
                            raise ValueError("Joara native Best board repeated an ID")
                        seen.add(record["id"])
                        record["rank"] = rank
                        records.append(record)
                    if page * size >= total or len(records) >= 100:
                        break
                    page += 1
                # These are positions in Joara's explicit Best board, never latest/recommended order.
                yield RankingResult(key=key, label=label, records=records, observed_at=observed_at, success=True)
            except BudgetExceeded:
                raise
            except (FetchError, ValueError, TypeError, KeyError) as error:
                yield RankingResult(key=key, label=label, records=[], observed_at=observed_at, success=False,
                                    error=f"Joara ranking fetch or parse failed ({type(error).__name__})")


if __name__ == "__main__":
    raise SystemExit(run_cli(JoaraAdapter()))
