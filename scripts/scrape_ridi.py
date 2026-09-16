"""Ridibooks public webnovel catalogs and native weekly/monthly bestseller lists.

Uses public category listings plus sitemap IDs and batched book metadata; no episode/reader requests,
account cookies, or challenge bypasses. HTTP restrictions remain resumable errors.
"""
import math
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests as browser_requests

try:
    from . import ridi_sitemap as sitemap
    from .metadata_common import BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult, run_cli
except ImportError:
    import ridi_sitemap as sitemap
    from metadata_common import BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult, run_cli

API = "https://api.ridibooks.com/v2/category/books"
CATEGORIES = {"1650": "Romance", "6050": "Romance fantasy", "1750": "Fantasy", "4150": "BL"}
PAGE_SIZE = 60



def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def obj(value):
    return value if isinstance(value, dict) else {}


def normalize(item, tier):
    book = obj(obj(item).get("book"))
    ident = str(book.get("bookId", ""))
    serial = obj(book.get("serial"))
    title = serial.get("title") or book.get("title")
    if not re.fullmatch(r"[1-9][0-9]*", ident) or not isinstance(title, str) or not title.strip():
        raise ValueError("Missing Ridibooks work ID/title")
    # The website's serial renderer keeps bookId as the canonical link and uses
    # serial.title/cover/total for work-level presentation.
    authors = [{"name": a["name"], "role": str(a.get("role") or "unknown").lower()}
               for a in (book.get("authors") or []) if isinstance(a, dict) and isinstance(a.get("name"), str)]
    genres = list(dict.fromkeys(c["name"] for c in (book.get("categories") or [])
                               if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"]))
    ratings = book.get("ratings")
    rating_count = rating = None
    if isinstance(ratings, list) and ratings and all(isinstance(r, dict) and number(r.get("count")) is not None
                                                   and number(r.get("rating")) is not None for r in ratings):
        rating_count = sum(r["count"] for r in ratings)
        rating = sum(r["count"] * r["rating"] for r in ratings) / rating_count if rating_count else None
    intro = obj(book.get("introduction")).get("description")
    synopsis = ""
    if isinstance(intro, str):
        synopsis = BeautifulSoup(intro, "html.parser").get_text("\n", strip=True) if re.search(r"</?[A-Za-z][^>]*>", intro) else unescape(intro).strip()
        synopsis = synopsis.replace("\r\n", "\n").replace("\r", "\n")
    cover = obj(serial.get("cover")) or obj(book.get("cover"))
    url = "https://ridibooks.com/books/" + ident
    return {"id": ident, "title": title.strip(),
            "author": ", ".join(a["name"] for a in authors if a["role"] in {"author", "story_writer"}),
            "contributors": authors, "cover": cover.get("large") or cover.get("small") or "",
            "tags": genres, "genres": genres, "synopsis": synopsis,
            "views": None, "likes": None, "episodes": number(serial.get("total")),
            "complete": int(serial["completion"]) if isinstance(serial.get("completion"), bool) else None,
            "age": 19 if book.get("adultsOnly") is True else 0 if book.get("adultsOnly") is False else None,
            "updated": None, "canonical_url": url, "purchase_url": url, "tier": tier,
            "metrics": {"rating": rating, "rating_count": rating_count},
            "publisher": obj(book.get("publisher")).get("name"),
            "ridi_categories": sorted({str(value) for c in book.get("categories", []) if isinstance(c, dict)
                                       for value in (c.get("categoryId", c.get("id")), c.get("parentId")) if value}),
            "_detail_complete": isinstance(intro, str)}


def data(payload):
    if not isinstance(payload, dict) or payload.get("success") is False or not isinstance(payload.get("data"), dict):
        raise ValueError("Malformed Ridibooks category response")
    return payload["data"]


class RidiAdapter(sitemap.SitemapCatalog):
    categories = CATEGORIES
    _normalize = staticmethod(normalize)
    _data = staticmethod(data)
    source = "ridi"
    label = "Ridibooks"
    request_errors = (browser_requests.exceptions.RequestException,)

    @staticmethod
    def create_session():
        # The public API rejects standard requests' TLS/HTTP fingerprint.
        # Start a fresh anonymous browser-compatible session; no saved cookies.
        return browser_requests.Session(impersonate="chrome", trust_env=False)

    def __init__(self):
        self._totals = {}
        self._ranking_records = {}

    @staticmethod
    def is_allowed_url(url):
        try:
            p = urlsplit(url)
            if p.scheme != "https" or p.hostname not in {"api.ridibooks.com", "ridibooks.com"} or p.username is not None or p.port not in (None, 443):
                return False
        except ValueError:
            return False
        if p.hostname == "ridibooks.com":
            return not p.query and (url == sitemap.INDEX or bool(sitemap.BOOK_MAP.fullmatch(url)))
        if p.path == "/graphql":
            return not p.query
        return p.path in {"/v2/category/books", "/v2/category/books/total-count"} and set(parse_qs(p.query, keep_blank_values=True)) <= {
            "category_id", "tab", "limit", "offset", "platform", "order_by", "period"}

    def _page(self, client, category, page, period=None, order="recent"):
        if (page - 1) * PAGE_SIZE >= 6000:
            raise ValueError("Ridibooks API rejects offsets >= 6000 for this category/sort; other catalog partitions continue, coverage remains partial")
        params = {"category_id": category, "tab": "bestsellers" if period else "books", "platform": "web"}
        if period:
            params["period"] = period
        key = (category, period)
        if key not in self._totals:
            total = data(client.get_json(API + "/total-count", params=params)).get("totalCount")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise ValueError("Invalid Ridibooks catalog total")
            self._totals[key] = total
        total = self._totals[key]
        params.update(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        if not period:
            params["order_by"] = order
        rows = data(client.get_json(API, params=params)).get("items")
        if not isinstance(rows, list) or len(rows) != min(PAGE_SIZE, max(0, total - params["offset"])):
            raise ValueError("Ridibooks catalog row count disagrees with its total")
        records = [normalize(row, "webnovel") for row in rows]
        if len({r["id"] for r in records}) != len(records):
            raise ValueError("Ridibooks repeated a work within a catalog page")
        return CatalogPage(records, page + 1 if page * PAGE_SIZE < min(total, 6000) else None, observed_total=total)

    def fetch_page(self, client, partition, page):
        try:
            if "sitemap" in partition:
                return self._sitemap_page(client, partition, page)
            result = self._page(client, partition["category"], page, order=partition.get("order", "recent"))
            for record in result.records:
                record["ridi_seen_scan"] = getattr(self, "_scan_id", "")
            return result
        except BudgetExceeded:
            raise
        except (FetchError, ValueError, TypeError, KeyError) as error:
            return CatalogPage([], None, False, f"Ridibooks {partition.get('category', partition.get('key', 'sitemap'))} page {page}: {error}")

    def detail(self, client, record):
        # The category API carries introduction.description itself, not the
        # 250-character preview rendered in the page. Absent text stays unknown.
        if record["id"] in self._ranking_records:
            return MetadataResult("success", dict(self._ranking_records[record["id"]]))
        return MetadataResult("unavailable", reason="Synopsis absent from public Ridibooks catalog metadata")

    def rankings(self, client, *, skip_keys=()):
        for category, genre in CATEGORIES.items():
            for period in ("weekly", "monthly"):
                key = f"{category}_{period}"
                if key in skip_keys:
                    continue
                label = f"Ridibooks · {genre} · {period.title()} Bestsellers"
                try:
                    records, seen = [], set()
                    for page in (1, 2):
                        result = self._page(client, category, page, period)
                        for i, record in enumerate(result.records):
                            rank = (page - 1) * PAGE_SIZE + i + 1
                            if rank > 100:
                                break
                            if record["id"] in seen:
                                raise ValueError("Ridibooks repeated a bestseller page")
                            seen.add(record["id"])
                            self._ranking_records[record["id"]] = record
                            records.append({**record, "rank": rank})
                        if result.next_page is None:
                            break
                    yield RankingResult(key, label, records)
                except BudgetExceeded:
                    raise
                except (FetchError, ValueError, TypeError, KeyError) as error:
                    yield RankingResult(key, label, [], success=False, error=f"Ridibooks bestseller request failed: {error}")


if __name__ == "__main__":
    raise SystemExit(run_cli(RidiAdapter()))
