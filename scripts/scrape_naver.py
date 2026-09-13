"""Anonymous Naver Web Novel metadata adapter (never requests a reader).

The shared metadata runner owns arguments, pacing, checkpoints and exports.
Naver Series is a possible outbound link, not an additional crawl target.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

try:
    from .metadata_common import (
        BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult,
        run_cli, utc_now,
    )
except ImportError:  # Direct ``python scripts/scrape_naver.py`` invocation.
    from metadata_common import (
        BudgetExceeded, CatalogPage, FetchError, MetadataResult, RankingResult,
        run_cli, utc_now,
    )


BASE = "https://novel.naver.com"
TIERS = ("challenge", "best", "webnovel")
TIER_LABELS = {"challenge": "Challenge League", "best": "Best League", "webnovel": "Series Edition"}
RANKING_GENRES = {
    "101": "Romance", "109": "Romance Fantasy", "102": "Fantasy",
    "110": "Modern Fantasy", "103": "Martial Arts", "104": "Mystery",
    "106": "Light Novel",
}


def _text(node):
    return node.get_text(" ", strip=True) if node is not None else ""


def _number(value):
    """Only unabridged, published numbers are exact metrics."""
    text = str(value or "").strip().replace(",", "")
    if re.search(r"[만억]", text):
        return None
    match = re.search(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", text)
    if not match:
        return None
    number = float(match.group())
    return int(number) if number.is_integer() else number


def _detail_identity(url):
    parsed = urlsplit(urljoin(BASE, url))
    match = re.fullmatch(r"/(challenge|best|webnovel)/list", parsed.path)
    ids = parse_qs(parsed.query).get("novelId", [])
    if parsed.hostname != "novel.naver.com" or not match or len(ids) != 1:
        return None
    if not re.fullmatch(r"[1-9]\d*", ids[0]):
        return None
    return ids[0], match.group(1), f"{BASE}{parsed.path}?{urlencode({'novelId': ids[0]})}"


def _empty_message(node):
    return bool(node and re.search(
        r"(작품|랭킹|소설).{0,25}(없습니다|없어요)|집계된.{0,20}없습니다",
        _text(node),
    ))


def _completion(node):
    # Never search a synopsis, site navigation or recommended work for "완결".
    return 1 if node.select_one(".bullet_comp, .bullet_comp_ex") else None


def parse_catalog(html, partition, page):
    soup = BeautifulSoup(html, "html.parser")
    catalog = soup.select_one("ul.card_list")
    if catalog is None:
        if _empty_message(soup.select_one("#content")):
            return CatalogPage(records=[], next_page=None)
        return CatalogPage(records=[], next_page=None, complete=False, error="Naver catalog markup was not found")
    records = []
    seen = set()
    for link in catalog.select("li > a.link"):
        identity = _detail_identity(link.get("href", ""))
        title = _text(link.select_one(".title"))
        if identity is None or not title:
            return CatalogPage(records=[], next_page=None, complete=False, error="Invalid Naver catalog identity or title")
        novel_id, tier, canonical = identity
        if novel_id in seen:
            continue
        seen.add(novel_id)
        image = link.select_one(".thumbnail img[src]")
        genre_name = partition.get("genre_name", "")
        rating = _number(_text(link.select_one(".meta_data_group .score_area")))
        metrics = {
            "favorites": _number(_text(link.select_one(".meta_data_group .count span"))),
            "rating": rating,
            "rating_scale": 10 if rating is not None else None,
        }
        records.append({
            "id": novel_id, "title": title, "author": _text(link.select_one(".author")),
            "contributors": ([{"name": _text(link.select_one(".author")), "role": "author"}]
                             if _text(link.select_one(".author")) else []),
            "cover": urljoin(BASE, image["src"]) if image else None,
            "tags": [genre_name] if genre_name else [],
            "genres": [genre_name] if genre_name else [], "keywords": [],
            "synopsis": None, "views": None, "likes": None,
            "episodes": _number(_text(link.select_one(".info_group .count span"))),
            "complete": _completion(link), "updated": None, "age": None, "source_dates": {},
            "canonical_url": canonical, "tier": tier, "purchase_url": None,
            "metrics": metrics,
        })
    paging = soup.select_one(".default_paging")
    higher = []
    if paging:
        for link in paging.select("a[href]"):
            parsed = urlsplit(urljoin(BASE, link["href"]))
            query = parse_qs(parsed.query)
            if parsed.hostname != "novel.naver.com" or parsed.path != f"/{partition['tier']}/genre":
                continue
            if query.get("genre", [None])[0] != str(partition["genre"]):
                continue
            value = query.get("page", [""])[0]
            if value.isdigit() and int(value) > page:
                higher.append(int(value))
    if not records and higher:
        return CatalogPage(records=[], next_page=None, complete=False, error="Empty Naver page still has further pages")
    if not records and not _empty_message(soup.select_one("#content")):
        return CatalogPage(records=[], next_page=None, complete=False, error="Unexplained empty Naver catalog")
    # The next-group arrow is page 11 on page 1. Numbered links are authoritative.
    return CatalogPage(records=records, next_page=min(higher) if higher else None)


def parse_detail(html, previous, response_url):
    soup = BeautifulSoup(html, "html.parser")
    info = soup.select_one(".section_area_info")
    identity = _detail_identity(response_url)
    if not identity or identity[0] != str(previous["id"]):
        return MetadataResult(status="failed", reason="Naver detail identity changed unexpectedly")
    if info is None or not _text(info.select_one("h2.title")):
        if re.search(r"성인.{0,15}인증|로그인.{0,15}필요", _text(soup.select_one("#content"))):
            return MetadataResult(status="restricted", reason="Naver requires verification for this metadata")
        return MetadataResult(status="failed", reason="Naver detail markup was not found")
    record = dict(previous)
    record.pop("_detail_complete", None)
    record.update(id=identity[0], tier=identity[1], canonical_url=identity[2])
    record["title"] = _text(info.select_one("h2.title"))
    authors = [_text(a) for a in info.select('a[href*="target=author"]') if _text(a)]
    if authors:
        record["author"] = authors[0]
        record["contributors"] = [{"name": name, "role": "author"} for name in dict.fromkeys(authors)]
    genre = info.select_one(".info_top .info_group .item")
    if genre and _text(genre):
        record["genres"] = [_text(genre)]
    keywords = list(dict.fromkeys(_text(a).lstrip("#").strip()
                                 for a in soup.select(".end_tag_area .tag_collection a.tag") if _text(a)))
    record["keywords"] = keywords
    record["tags"] = list(dict.fromkeys(record.get("genres", []) + keywords))
    summary = info.select_one("p.summary")
    if summary:
        for element in summary.select("a, button"):
            element.decompose()
        synopsis = summary.get_text("\n", strip=True)
        if synopsis:
            record["synopsis"] = synopsis
    image = info.select_one(".thumbnail img[src]")
    if image:
        record["cover"] = urljoin(BASE, image["src"])
    record["complete"] = 1 if _completion(info) else record.get("complete")
    metrics = dict(record.get("metrics") or {})
    rating = _number(_text(info.select_one(".score_area")))
    if rating is not None:
        metrics.update(rating=rating, rating_scale=10)
    favorite = _number(_text(info.select_one("#concernNovelIcon")))
    if favorite is not None:
        metrics["favorites"] = favorite
    # Do not interpret the unloaded reaction widget's initial zero as likes.
    for group in info.select(".info_top .info_group"):
        text = _text(group)
        if "다운로드" in text:
            download_text = text.split("다운로드", 1)[1].strip()
            metrics["downloads"] = _number(download_text)
            metrics["downloads_display"] = download_text
    record["metrics"] = metrics
    for heading in soup.select(".cont_sub .component_head h3.title"):
        match = re.search(r"작품\s*회차\s*\(([\d,]+)\)", _text(heading))
        if match:
            record["episodes"] = int(match.group(1).replace(",", ""))
            break
    for link in info.select(".link_group a[href]"):
        destination = urljoin(BASE, link["href"])
        if urlsplit(destination).hostname in {"series.naver.com", "m.series.naver.com"}:
            record["purchase_url"] = destination
            break
    record["_detail_complete"] = True
    return MetadataResult(status="success", record=record)


def parse_ranking(html, genre, period, observed_at):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for kind, side in (("free", "left"), ("paid", "right")):
        key = f"best_{genre}_{period.lower()}_{kind}"
        label = f"Naver Best League · {RANKING_GENRES[genre]} · {kind.title()} · {period.title()}"
        wrapper = soup.select_one(f".ranking_wrap_{side}")
        records, ids, ranks, error = [], set(), set(), None
        if wrapper is None:
            error = "Naver ranking board markup was not found"
        else:
            for row in wrapper.select("ul.ranking_list > li.item"):
                link = row.select_one("a.link[href]")
                identity = _detail_identity(link["href"]) if link else None
                rank_text = _text(row.select_one(".ranking .rank"))
                if not identity or not re.fullmatch(r"[1-9]\d*", rank_text):
                    error = "Invalid Naver ranking identity or explicit rank"
                    break
                rank = int(rank_text)
                if identity[0] in ids or rank in ranks:
                    error = "Repeated Naver ranking identity or rank"
                    break
                ids.add(identity[0])
                ranks.add(rank)
                records.append({"id": identity[0], "rank": rank,
                                "title": _text(row.select_one(".title")),
                                "canonical_url": identity[2], "tier": identity[1]})
            if not records and error is None and not _empty_message(wrapper):
                error = "Unexplained empty Naver ranking board"
        results.append(RankingResult(key=key, label=label, records=[] if error else records,
                                     observed_at=observed_at, success=error is None, error=error))
    return results


class NaverAdapter:
    source = "naver"
    label = "Naver Web Novel"

    @staticmethod
    def is_allowed_url(url):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "https" or parsed.hostname != "novel.naver.com" or parsed.username is not None or port not in (None, 443):
            return False
        if not re.fullmatch(r"/(challenge|best|webnovel)/(genre|genreMain|list|ranking)", parsed.path):
            return False
        return set(parse_qs(parsed.query, keep_blank_values=True)).issubset({"genre", "order", "finish", "page", "novelId", "periodType"})

    def partitions(self, client):
        partitions = []
        for tier in TIERS:
            html = client.get_text(f"{BASE}/{tier}/genre", params={"genre": "102"})
            soup = BeautifulSoup(html, "html.parser")
            if soup.select_one("ul.card_list") is None:
                raise FetchError(f"Naver {tier} genre discovery did not return a catalog")
            genres = {}
            for link in soup.select("a[href]"):
                parsed = urlsplit(urljoin(BASE, link["href"]))
                if parsed.hostname != "novel.naver.com" or parsed.path not in {f"/{tier}/genre", f"/{tier}/genreMain"}:
                    continue
                query = parse_qs(parsed.query)
                code = query.get("genre", [""])[0]
                name = _text(link)
                # Sort/paging links are not genre names.
                if (code.isdigit() and name
                        and name not in {"시리즈에디션", "베스트리그", "챌린지리그", "추천", "랭킹"}
                        and not any(key in query for key in ("page", "order", "finish"))):
                    genres.setdefault(code, name)
            if not genres:
                raise FetchError(f"Naver {tier} genre navigation could not be parsed")
            for genre, name in sorted(genres.items()):
                for finished in (False, True):
                    partitions.append({"key": f"{tier}:{genre}:finish={str(finished).lower()}",
                                       "tier": tier, "start_page": 1, "genre": genre,
                                       "genre_name": name, "finish": finished})
        return partitions

    def fetch_page(self, client, partition, page):
        try:
            html = client.get_text(f"{BASE}/{partition['tier']}/genre", params={
                "genre": partition["genre"], "order": "Update",
                "finish": str(partition["finish"]).lower(), "page": page,
            })
            return parse_catalog(html, partition, page)
        except BudgetExceeded:
            raise
        except (FetchError, ValueError, TypeError) as error:
            return CatalogPage(records=[], next_page=None, complete=False,
                               error=f"Naver catalog fetch or parse failed ({type(error).__name__})")

    def detail(self, client, record):
        canonical = record.get("canonical_url")
        if not canonical or not _detail_identity(canonical):
            return MetadataResult(status="failed", reason="Naver metadata needs a verified tier-specific detail URL")
        try:
            response = client.get(canonical)
            return parse_detail(response.text, record, response.url)
        except BudgetExceeded:
            raise
        except FetchError as error:
            status = "unavailable" if error.status_code in (404, 410) else "restricted" if error.status_code in (401, 403) else "failed"
            return MetadataResult(status=status, reason=f"Naver metadata HTTP failure ({error.status_code or 'network'})")
        except (ValueError, TypeError):
            return MetadataResult(status="failed", reason="Naver metadata could not be parsed")

    def rankings(self, client, *, skip_keys=()):
        skipped = set(skip_keys)
        for genre in RANKING_GENRES:
            for period in ("DAILY", "WEEKLY"):
                keys = {f"best_{genre}_{period.lower()}_{kind}" for kind in ("free", "paid")}
                if keys.issubset(skipped):
                    continue
                observed_at = utc_now()
                try:
                    html = client.get_text(f"{BASE}/best/ranking", params={"genre": genre, "periodType": period})
                    yield from (board for board in parse_ranking(html, genre, period, observed_at)
                                if board.key not in skipped)
                except BudgetExceeded:
                    raise
                except (FetchError, ValueError, TypeError) as error:
                    for kind in ("free", "paid"):
                        if f"best_{genre}_{period.lower()}_{kind}" in skipped:
                            continue
                        yield RankingResult(
                            key=f"best_{genre}_{period.lower()}_{kind}",
                            label=f"Naver Best League · {RANKING_GENRES[genre]} · {kind.title()} · {period.title()}",
                            records=[], observed_at=observed_at, success=False,
                            error=f"Naver ranking fetch or parse failed ({type(error).__name__})",
                        )


if __name__ == "__main__":
    raise SystemExit(run_cli(NaverAdapter()))
