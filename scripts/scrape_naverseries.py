"""Public Naver Series novel metadata, including explicit 19+ catalog badges.

Series product IDs are a separate source from Naver Web Novel IDs. Public
catalog previews are retained when full metadata requires age verification.
"""
import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

try:
    from .metadata_common import BudgetExceeded, CatalogPage, FetchError, MetadataResult, run_cli
except ImportError:
    from metadata_common import BudgetExceeded, CatalogPage, FetchError, MetadataResult, run_cli

BASE = "https://series.naver.com"
CATALOG = BASE + "/novel/categoryProductList.series"
GENRES = {"201": "로맨스", "207": "로판", "202": "판타지", "208": "현판",
          "206": "무협", "203": "미스터리", "205": "라이트노벨", "209": "BL"}


def parse_catalog(html, partition, page):
    soup = BeautifulSoup(html, "html.parser")
    listing = soup.select_one("ul.lst_list")
    if listing is None:
        return CatalogPage([], None, False, "Naver Series catalog markup unavailable")
    current = soup.select_one(".pagenate strong")
    if current and current.get_text(strip=True) != str(page):
        return CatalogPage([], None, False, "Naver Series returned a different catalog page")
    records = []
    for item in listing.select(":scope > li"):
        link = item.select_one("h3 a[href]")
        if not link:
            return CatalogPage([], None, False, "Naver Series catalog row lacks title/link")
        parsed = urlsplit(link["href"])
        ident = parse_qs(parsed.query).get("productNo", [""])[0]
        if parsed.path != "/novel/detail.series" or parsed.netloc not in ("", "series.naver.com") or not re.fullmatch(r"[1-9][0-9]*", ident):
            return CatalogPage([], None, False, "Invalid Naver Series product identity")
        title = link.get("title", "").strip()
        if not title:
            return CatalogPage([], None, False, "Naver Series title unavailable")
        units = re.search(r"\(([\d,]+)(화|권)/(완결|연재중)\)\s*$", link.get_text(strip=True))
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
            "views": None, "likes": None, "updated": None, "canonical_url": url, "purchase_url": url,
            "tier": "series", "synopsis_is_preview": True})
    higher = []
    for link in soup.select(".pagenate a[href]"):
        query = parse_qs(urlsplit(link["href"]).query)
        candidate = query.get("page", [""])[0]
        if query.get("genreCode") == [partition["genre"]] and candidate.isdigit() and int(candidate) > page:
            higher.append(int(candidate))
    if not records:
        return CatalogPage([], None, False, "Empty Naver Series catalog could not be verified")
    return CatalogPage(records, min(higher) if higher else None)


class NaverSeriesAdapter:
    source = "naverseries"
    label = "Naver Series"
    supports_rankings = False  # Catalog sort order is not a native ranking board.

    @staticmethod
    def is_allowed_url(url):
        try:
            p = urlsplit(url)
            if p.scheme != "https" or p.hostname != "series.naver.com" or p.username or p.port not in (None, 443):
                return False
        except ValueError:
            return False
        keys = set(parse_qs(p.query, keep_blank_values=True))
        if p.path == "/novel/categoryProductList.series":
            return keys <= {"categoryTypeCode", "genreCode", "orderTypeCode", "page"}
        return p.path == "/novel/detail.series" and keys <= {"productNo"}

    def partitions(self, client):
        return [{"key": genre, "genre": genre, "tier": "series", "start_page": 1} for genre in GENRES]

    def fetch_page(self, client, partition, page):
        try:
            html = client.get_text(CATALOG, params={"categoryTypeCode": "genre", "genreCode": partition["genre"],
                "orderTypeCode": "new", "page": page})
            return parse_catalog(html, partition, page)
        except BudgetExceeded:
            raise
        except (FetchError, ValueError) as error:
            return CatalogPage([], None, False, f"Naver Series {partition['genre']} page {page}: {error}")

    def detail(self, client, record):
        try:
            html = client.get_text(BASE + "/novel/detail.series", params={"productNo": record["id"]})
        except BudgetExceeded:
            raise
        except FetchError as error:
            restricted = error.status_code in (401, 403) or "allowlist" in str(error)
            return MetadataResult("restricted" if restricted else "failed", reason="Naver Series metadata requires sign-in/verification" if restricted else str(error))
        soup = BeautifulSoup(html, "html.parser")
        head, info = soup.select_one(".end_head h2"), soup.select_one(".end_info")
        if not head or not info:
            return MetadataResult("failed", reason="Naver Series detail markup unavailable")
        result = {"id": record["id"], "title": head.get_text(strip=True)}
        age = re.search(r"(\d+)세\s*이용가", info.get_text(" ", strip=True))
        if age:
            result["age"] = int(age[1])
        elif "전체 이용가" in info.get_text(" ", strip=True):
            result["age"] = 0
        descriptions = soup.select(".end_dsc ._synopsis")
        if not descriptions:
            return MetadataResult("failed", reason="Naver Series full synopsis unavailable")
        description = descriptions[-1]
        for control in description.select(".al_r"):
            control.decompose()
        result.update(synopsis=description.get_text("\n", strip=True), synopsis_is_preview=False,
                      metrics={"synopsis_is_preview": False})
        return MetadataResult("success", result)

    def rankings(self, client, *, skip_keys=()):
        return iter(())


if __name__ == "__main__":
    raise SystemExit(run_cli(NaverSeriesAdapter()))
