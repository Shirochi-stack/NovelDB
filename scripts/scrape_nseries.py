"""Public Nseries novel metadata, including explicit 19+ catalog badges.

Series product IDs are a separate source from Nweb IDs. Public
catalog previews are retained when full metadata requires age verification.
"""
import re
import math
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

try:
    from .metadata_common import BudgetExceeded, CatalogPage, FetchError, MetadataResult, log_progress, run_cli, source_date
    from .nseries_collection_policy import CollectionPolicy
    from .publication_dates import explicit_publication_date, structured_publication_date
except ImportError:
    from metadata_common import BudgetExceeded, CatalogPage, FetchError, MetadataResult, log_progress, run_cli, source_date
    from nseries_collection_policy import CollectionPolicy
    from publication_dates import explicit_publication_date, structured_publication_date

BASE = "https://series.\u006e\u0061\u0076\u0065\u0072.com"
CATALOG = BASE + "/novel/categoryProductList.series"
MOBILE = "https://m.series.\u006e\u0061\u0076\u0065\u0072.com"
CATALOG_JSON = MOBILE + "/novel/categoryProductMoreList.series"
PAGE_SIZE = 25  # Larger countPerPage values are ignored by the public route.
GENRES = {"201": "로맨스", "207": "로판", "202": "판타지", "208": "현판",
          "206": "무협", "203": "미스터리", "205": "라이트노벨", "209": "BL"}


def parse_catalog(html, partition, page):
    soup = BeautifulSoup(html, "html.parser")
    listing = soup.select_one("ul.lst_list")
    if listing is None:
        return CatalogPage([], None, False, "Nseries catalog markup unavailable")
    current = soup.select_one(".pagenate strong")
    if current and current.get_text(strip=True) != str(page):
        return CatalogPage([], None, False, "Nseries returned a different catalog page")
    records = []
    for item in listing.select(":scope > li"):
        link = item.select_one("h3 a[href]")
        if not link:
            return CatalogPage([], None, False, "Nseries catalog row lacks title/link")
        parsed = urlsplit(link["href"])
        ident = parse_qs(parsed.query).get("productNo", [""])[0]
        if parsed.path != "/novel/detail.series" or parsed.netloc not in ("", "series.\u006e\u0061\u0076\u0065\u0072.com") or not re.fullmatch(r"[1-9][0-9]*", ident):
            return CatalogPage([], None, False, "Invalid Nseries product identity")
        title = link.get("title", "").strip()
        if not title:
            return CatalogPage([], None, False, "Nseries title unavailable")
        # Ongoing works print "미완결"; "연재중" is kept for older markup.
        units = re.search(r"\(([\d,]+)(화|권)/(완결|미완결|연재중)\)\s*$", link.get_text(strip=True))
        info = item.select_one("p.info")
        # The row's own date is its newest episode or volume.
        day = re.search(r"(?<![\d.])(\d{4})\.(\d{2})\.(\d{2})\.?(?![\d])", info.get_text(" ", strip=True)) if info else None
        author, cover, intro = item.select_one(".author"), item.select_one("a.pic img[src]"), item.select_one(".dsc")
        score = item.select_one(".score_num")
        score_text = score.get_text(strip=True) if score else ""
        url = BASE + "/novel/detail.series?productNo=" + ident
        records.append({"id": ident, "title": title, "author": author.get_text(strip=True) if author else "",
            "cover": cover["src"] if cover else "", "tags": [GENRES[partition["genre"]]],
            "synopsis": intro.get_text(" ", strip=True) if intro else "",
            "age": 19 if item.select_one("h3 .n19") else None,
            "episodes": int(units[1].replace(",", "")) if units and units[2] == "화" else None,
            "complete": int(units[3] == "완결") if units else None,
            "metrics": {"rating": float(score_text) if re.fullmatch(r"\d+(?:\.\d+)?", score_text) else None,
                        "rating_scale": 10, "volumes": int(units[1].replace(",", "")) if units and units[2] == "권" else None,
                        "synopsis_is_preview": True},
            "views": None, "likes": None, "updated": "-".join(day.groups()) if day else None,
            "source_dates": {"updated": source_date(day[0])} if day else {},
            "canonical_url": url, "purchase_url": url,
            "tier": "series", "synopsis_is_preview": True})
    higher = []
    for link in soup.select(".pagenate a[href]"):
        query = parse_qs(urlsplit(link["href"]).query)
        candidate = query.get("page", [""])[0]
        if query.get("genreCode") == [partition["genre"]] and candidate.isdigit() and int(candidate) > page:
            higher.append(int(candidate))
    if not records:
        return CatalogPage([], None, False, "Empty Nseries catalog could not be verified")
    return CatalogPage(records, min(higher) if higher else None)


def parse_catalog_metadata(payload):
    """Only metadata matched to desktop catalog IDs will be used."""
    rows = payload.get("productList") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError("Malformed Nseries metadata batch")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed Nseries metadata row")
        ident = str(row.get("productNo", ""))
        if (not re.fullmatch(r"[1-9][0-9]*", ident) or ident in result
                or row.get("productTypeCode") != "NOVEL"):
            raise ValueError("Invalid or duplicate Nseries metadata identity")
        grade = row.get("seeingGradeTypeCode")
        age = {"G0": 0, "G12": 12, "G15": 15, "G18": 18, "G19": 19}.get(grade)
        count = row.get("starScoreEvaluatorCount")
        rating = row.get("starScore")
        metrics = {}
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            metrics["rating_count"] = count
        if (isinstance(rating, (int, float)) and not isinstance(rating, bool)
                and math.isfinite(rating) and 0 <= rating <= 10):
            metrics.update(rating=rating, rating_scale=10)
        published = explicit_publication_date(
            row, "publicationDate", "serviceStartDate", "openDate", "registerDate"
        )
        result[ident] = {"age": age, "metrics": metrics, "published": published}
    return result


class CatalogSession(requests.Session):
    def prepare_request(self, request):
        prepared = super().prepare_request(request)
        if urlsplit(prepared.url).hostname == urlsplit(MOBILE).hostname:
            # JSON negotiation applies to the mobile route only. Sending a
            # mobile user agent to the desktop host causes a catalog redirect.
            prepared.headers.update({"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1",
                                     "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
                                     "Referer": MOBILE + "/novel/categoryProductList.series"})
        return prepared


class NseriesAdapter(CollectionPolicy):
    source = "nseries"
    label = "Nseries"
    supports_rankings = False  # Catalog sort order is not a native ranking board.
    # A detail request only adds the full synopsis and age rating. New episodes,
    # dates and ratings arrive with every catalog row and need no detail request.
    detail_fields = ("title", "synopsis", "age", "published")
    detail_refresh_days = 90
    detail_version = 2

    @staticmethod
    def create_session():
        session = CatalogSession()
        session.trust_env = False
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PublicMetadata/1.0)"})
        return session

    @staticmethod
    def is_allowed_url(url):
        try:
            p = urlsplit(url)
            if p.scheme != "https" or p.username is not None or p.port not in (None, 443) or p.fragment:
                return False
        except ValueError:
            return False
        query = parse_qs(p.query, keep_blank_values=True)
        keys = set(query)
        if p.hostname == "m.series.\u006e\u0061\u0076\u0065\u0072.com":
            if p.path != "/novel/categoryProductMoreList.series":
                return False
            if not p.query:
                return True
            return (keys == {"categoryTypeCode", "genreCode", "orderTypeCode", "isFreePassChecked", "start", "countPerPage"}
                    and query["categoryTypeCode"] == ["genre"] and query["genreCode"] in [[g] for g in GENRES]
                    and query["orderTypeCode"] == ["new"] and query["isFreePassChecked"] == ["false"]
                    and query["countPerPage"] == [str(PAGE_SIZE)] and len(query["start"]) == 1
                    and re.fullmatch(r"0|[1-9][0-9]*", query["start"][0]) is not None
                    and int(query["start"][0]) % PAGE_SIZE == 0)
        if p.hostname != "series.\u006e\u0061\u0076\u0065\u0072.com":
            return False
        if p.path == "/novel/categoryProductList.series":
            return keys <= {"categoryTypeCode", "genreCode", "orderTypeCode", "page"}
        return p.path == "/novel/detail.series" and keys <= {"productNo"}

    def partitions(self, client):
        return [{"key": genre, "genre": genre, "tier": "series", "start_page": 1} for genre in GENRES]

    def fetch_page(self, client, partition, page):
        try:
            html = client.get_text(CATALOG, params={"categoryTypeCode": "genre", "genreCode": partition["genre"],
                "orderTypeCode": "new", "page": page})
            result = parse_catalog(html, partition, page)
            if not result.complete or result.error:
                return result
            metadata = {}
            try:
                metadata = parse_catalog_metadata(client.get_json(CATALOG_JSON, params={
                    "categoryTypeCode": "genre", "genreCode": partition["genre"], "orderTypeCode": "new",
                    "isFreePassChecked": "false", "start": (page - 1) * PAGE_SIZE, "countPerPage": PAGE_SIZE}))
            except BudgetExceeded:
                # Preserve this already collected page; the next primary request
                # observes the budget and checkpoints the advancing cursor.
                pass
            except (FetchError, ValueError, TypeError) as error:
                log_progress(self.source, f"Optional metadata batch {partition['genre']} page {page}: {type(error).__name__}; keeping catalog records")
            if metadata:
                missing = sum(record["id"] not in metadata for record in result.records)
                if missing:
                    log_progress(self.source, f"Optional metadata batch {partition['genre']} page {page}: {missing} unmatched catalog IDs; keeping catalog records")
            for record in result.records:
                extra = metadata.get(record["id"])
                record["nseries_batch_complete"] = extra is not None
                if extra:
                    # A public R19 badge cannot be downgraded by a shifting batch.
                    if record["age"] != 19 and extra["age"] is not None:
                        record["age"] = extra["age"]
                    record["metrics"].update(extra["metrics"])
                    if extra.get("published"):
                        record["published"] = extra["published"]
                        record["source_dates"] = {**record.get("source_dates", {}),
                                                  "published": source_date(extra["published"])}
                record["nseries_catalog_fingerprint"] = self.catalog_fingerprint(record)
            return result
        except BudgetExceeded:
            raise
        except (FetchError, ValueError) as error:
            return CatalogPage([], None, False, f"Nseries {partition['genre']} page {page}: {error}")

    def detail(self, client, record):
        if record.get("age") == 19:
            return MetadataResult("restricted", reason="Nseries R19 details require age verification; retaining public catalog metadata")
        try:
            html = client.get_text(BASE + "/novel/detail.series", params={"productNo": record["id"]})
        except BudgetExceeded:
            raise
        except FetchError as error:
            restricted = error.status_code in (401, 403) or "allowlist" in str(error)
            return MetadataResult("restricted" if restricted else "failed", reason="Nseries metadata requires sign-in/verification" if restricted else str(error))
        soup = BeautifulSoup(html, "html.parser")
        head, info = soup.select_one(".end_head h2"), soup.select_one(".end_info")
        if not head or not info:
            return MetadataResult("failed", reason="Nseries detail markup unavailable")
        result = {"id": record["id"], "title": head.get_text(strip=True)}
        published = structured_publication_date(soup)
        if published:
            result["published"] = published
            result["source_dates"] = {"published": source_date(published)}
        age = re.search(r"(\d+)세\s*이용가", info.get_text(" ", strip=True))
        if age:
            result["age"] = int(age[1])
        elif "전체 이용가" in info.get_text(" ", strip=True):
            result["age"] = 0
        descriptions = soup.select(".end_dsc ._synopsis")
        if not descriptions:
            return MetadataResult("failed", reason="Nseries full synopsis unavailable")
        description = descriptions[-1]
        for control in description.select(".al_r"):
            control.decompose()
        result.update(synopsis=description.get_text("\n", strip=True), synopsis_is_preview=False,
                      metrics={"synopsis_is_preview": False})
        return MetadataResult("success", result)

    def rankings(self, client, *, skip_keys=()):
        return iter(())


if __name__ == "__main__":
    raise SystemExit(run_cli(NseriesAdapter()))
