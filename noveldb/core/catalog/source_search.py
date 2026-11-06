"""The Catalog's searches of NovelDB's own sites: books by tag and by author, asked of each site directly
(its own search API or search page), the way the metadata scrapers read them.

NovelDB's index knows these sites' books as of its last build; asking the sites finds books since then,
and a signed-in user's requests (see `logins.py`) find what only members see (npia's 19+ books).

A tag is asked under each site's own name for it: NovelDB's tag list says what each source calls an
English tag (`NovelDBIndex.native_tags`: "TS" on npia for "Gender Bender"); a tag NovelDB doesn't list
is asked as typed. Authors are asked as typed.

`search(source, mode, query, page, fetch)` returns (cards, more): the page's cards, and whether the site
has a next page. Each site's searcher is a function of (mode, query, page, fetch); scripts/generic/
catalog-app.js has the same searchers for the mobile apps: keep the two in step.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

log = logging.getLogger(__name__)

Fetch = Callable[..., Optional[dict]]
Result = Tuple[List[dict], bool]

# The sources and the names the page shows for them (as NovelDB's site does).
SOURCE_NAMES = {"npia": "npia", "kpage": "kpage", "sfc": "sfc", "nweb": "nweb", "mpia": "mpia", "jara": "jara",
                "rbooks": "rbooks", "nseries": "nseries"}
NATIVE_PER_SOURCE = 2  # a tag is asked under at most this many of a site's names for it
STATUS_TAGS = {"completed", "ongoing", "hiatus"}  # a book's status: the sites' tag searches have no such tag


def _json(result: Optional[dict]):
    if not result or result.get("status", 0) >= 400:
        return None
    try:
        return json.loads(result.get("text") or (result.get("content") or b"").decode("utf-8", "ignore"))
    except ValueError:
        return None


def _count(value) -> str:
    """A count as the page shows it ("1,234"); "" for none."""
    try:
        number = int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return ""
    return f"{number:,}" if number > 0 else ""


def _date(value) -> str:
    text = str(value or "").strip()
    found = re.match(r"(\d{4})[-./]?(\d{2})[-./]?(\d{2})", text)
    return f"{found.group(1)}-{found.group(2)}-{found.group(3)}" if found else ""


def card(source: str, url: str, title: str, author: str = "", cover: str = "", tags=None, description: str = "",
         **figures) -> dict:
    """A book card as the generic search page takes it; `figures`: views, likes, chapters, status,
    updated, published (empty ones are left out)."""
    from noveldb.core.engines.generic import site_of
    from urllib.parse import urlsplit

    out = {"url": url, "title": (title or "").strip() or url, "author": (author or "").strip(), "cover": cover or "",
           "description": re.sub(r"\s+", " ", description or "").strip()[:600],
           "tags": [str(tag).strip() for tag in tags or [] if str(tag).strip()],
           "site": site_of(urlsplit(url).hostname or ""), "comic": False, "source": "site", "complete": False,
           "verified": True, "sourceName": SOURCE_NAMES.get(source, source)}
    for name in ("views", "likes", "chapters"):
        if figures.get(name) not in (None, ""):
            out[name] = _count(figures[name])
    for name in ("updated", "published"):
        if figures.get(name):
            out[name] = _date(figures[name])
    if figures.get("status"):
        out["status"] = figures["status"]
    if figures.get("ageRating") in (15, 19):
        out["ageRating"] = figures["ageRating"]  # 19 adults only, 15 teens
    return {name: value for name, value in out.items() if value not in ("", None) or name in ("author", "cover", "description")}


SEARCHERS: Dict[str, Callable[[str, str, int, Fetch], Result]] = {}


def searcher(source: str):
    def register(function):
        SEARCHERS[source] = function
        return function
    return register


def search(source: str, mode: str, query: str, page: int, fetch: Fetch) -> Result:
    """Page `page` (0 the first) of `source`'s books with the tag (mode "tag") or by the author ("author")."""
    function = SEARCHERS.get(source)
    if function is None or mode not in ("tag", "author") or not query.strip():
        return [], False
    try:
        return function(mode, query.strip(), page, fetch)
    except Exception as e:
        log.warning("%s %s search failed: %s", source, mode, e)
        return [], False


# The next page of each site's list a search read: (source, mode, the search's text, the name asked) -> page.
_next: Dict[Tuple[str, str, str, str], int] = {}
_next_lock = threading.Lock()


def remember(source: str, mode: str, text: str, name: str, page: Optional[int]) -> None:
    """The page of the site's list to read next as the results are scrolled (None: it has no more)."""
    with _next_lock:
        if page is None:
            _next.pop((source, mode, text, name), None)
        else:
            _next[(source, mode, text, name)] = page


def pending(mode: str, text: str) -> List[Tuple[str, str, int]]:
    """[(source, name, page), ...]: the sites' lists of this search that have a next page."""
    with _next_lock:
        return [(source, name, page) for (source, kind, query, name), page in _next.items()
                if kind == mode and query == text]


def tag_queries(tag: str, natives: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """{source: [the names to ask it for]} for a tag search: each site's own names for the tag (at most
    NATIVE_PER_SOURCE, case variants once), or the tag as typed for every site when NovelDB doesn't list it."""
    if tag.strip().lower() in STATUS_TAGS:
        return {}
    out = {}
    for source in SEARCHERS:
        names = natives.get(source) if natives else [tag]
        seen, picked = set(), []
        for name in names or []:
            if name.lower() not in seen:
                seen.add(name.lower())
                picked.append(name)
        if picked:
            out[source] = picked[:NATIVE_PER_SOURCE]
    return out


def _soup(result: Optional[dict]):
    if not result or result.get("status", 0) >= 400 or not result.get("text"):
        return None
    from bs4 import BeautifulSoup

    return BeautifulSoup(result["text"], "lxml")


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node is not None else ""


# -- kpage: its search API; a tag is a genre (the only tag its books have) ---------------------------------

KPAGE = "https://page.\u006b\u0061\u006b\u0061\u006f.com"
KPAGE_API = "https://bff-page.\u006b\u0061\u006b\u0061\u006f.com/api/gateway"
KPAGE_COVER = "https://dn-img-page.\u006b\u0061\u006b\u0061\u006f.com/download/resource?kid={}&filename=th3"
KPAGE_GENRES = {"판타지": 86, "현판": 120, "로맨스": 89, "로판": 117, "무협": 87, "bl": 123}
KPAGE_DONE = ("ST66", "END", "COMPLETE")


def _kpage_card(row: dict) -> dict:
    done = str(row.get("state") or "") in KPAGE_DONE or str(row.get("on_issue") or "") in ("E", "End", "Complete") \
        or "[완결]" in str(row.get("title") or "")
    return card("kpage", f"{KPAGE}/content/{row.get('series_id')}", row.get("title") or "", row.get("authors") or "",
                KPAGE_COVER.format(row["thumbnail"]) if row.get("thumbnail") else "",
                [row["sub_category"]] if row.get("sub_category") else [],
                views=(row.get("service_property") or {}).get("view_count"), status="Completed" if done else "Ongoing",
                updated=row.get("last_slide_added_dt"), published=row.get("start_sale_dt"))


@searcher("kpage")
def _kpage(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    headers = {"Accept": "application/json", "Referer": KPAGE + "/", "Origin": KPAGE}
    if mode == "tag":
        genre = KPAGE_GENRES.get(query.lower())
        if genre is None:
            return [], False  # its books have no other tags; its search matches titles only
        url = f"{KPAGE_API}/view/v1/landing/genre?" + urlencode(
            {"category_uid": 11, "screen_uid": 84, "subcategory_uid": genre, "sort_type": "UPDATE", "page": page})
    else:
        url = f"{KPAGE_API}/api/v1/search/series?" + urlencode(
            {"keyword": query, "category_uid": 11, "is_complete": "false", "sort_type": "ACCURACY", "page": page, "size": 40})
    result = (_json(fetch(url, headers=headers)) or {}).get("result") or {}
    rows = [row for row in result.get("list") or [] if isinstance(row, dict) and row.get("series_id")]
    if mode == "author":  # the search matches any part of a title or an author: only the author's own books
        rows = [row for row in rows if query in [name.strip() for name in str(row.get("authors") or "").split(",")]]
    return [_kpage_card(row) for row in rows], bool(result.get("list")) and not result.get("is_end", True)


# -- nweb: its search page, by exact tag or author, in each of its three leagues ----------------------------

NWEB = "https://novel.\u006e\u0061\u0076\u0065\u0072.com"
NWEB_SECTIONS = ("webnovel", "best", "challenge")


def _nweb_cards(result: Optional[dict]) -> Tuple[List[dict], bool]:
    soup = _soup(result)
    if soup is None:
        return [], False
    cards = []
    for item in soup.select("ul.default_list > li.item"):
        link = item.select_one("a.link[href]") or item.select_one("a[href*='novelId=']")
        if link is None or "novelId=" not in link["href"]:
            continue
        tags = re.findall(r"#([^\s#]+)", _text(item.select_one("span.tag")))
        image = item.select_one(".thumbnail img")
        cards.append(card("nweb", NWEB + link["href"] if link["href"].startswith("/") else link["href"],
                          _text(item.select_one(".title")),
                          ", ".join(_text(a) for a in item.select(".info_group .author")),
                          (image.get("src") or "") if image is not None else "", tags,
                          status="Completed" if item.select_one(".bullet_comp") is not None else ""))
    more = any("다음" in _text(a) for a in soup.select(".default_paging a")) or len(cards) >= 15
    return cards, more


@searcher("nweb")
def _nweb(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    target = "tag" if mode == "tag" else "author"
    query = query.lstrip("#")
    cards, more = [], False
    for section in NWEB_SECTIONS:
        found, has_more = _nweb_cards(fetch(f"{NWEB}/search?" + urlencode(
            {"keyword": query, "target": target, "section": section, "page": page + 1})))
        cards.extend(found)
        more = more or has_more
    return cards, more


# -- nseries: a tag is a genre (its books have no others); authors through its search -----------------------

NSERIES = "https://series.\u006e\u0061\u0076\u0065\u0072.com"
NSERIES_GENRES = {"로맨스": 201, "로판": 207, "판타지": 202, "현판": 208, "무협": 206, "미스터리": 203, "라이트노벨": 205,
                  "bl": 209}


def _nseries_cards(result: Optional[dict]) -> Tuple[List[dict], bool]:
    soup = _soup(result)
    if soup is None:
        return [], False
    cards = []
    for item in soup.select("ul.lst_list > li"):
        link = item.select_one("h3 a[href*='productNo=']")
        if link is None:
            continue
        title = re.sub(r"\s*\((총\s*)?\d+\s*(화|권)\s*/\s*(미?완결)\)\s*$", "", _text(link))
        done = re.search(r"/\s*완결\)", _text(link))
        image = item.select_one("a.pic img")
        info = _text(item.select_one("p.info"))
        cards.append(card("nseries", NSERIES + link["href"] if link["href"].startswith("/") else link["href"], title,
                          _text(item.select_one(".author")), (image.get("src") or "") if image is not None else "",
                          description=_text(item.select_one(".dsc")),
                          status="Completed" if done else "Ongoing" if re.search(r"미완결\)", _text(link)) else "",
                          updated=(re.search(r"\d{4}\.\d{2}\.\d{2}", info) or [""])[0]))
        rating = _text(item.select_one(".score_num"))
        if rating:
            cards[-1]["rating"] = rating
    pages = soup.select_one(".pagenate")
    more = bool(pages and pages.select_one("a.next")) or len(cards) >= 25
    return cards, more


@searcher("nseries")
def _nseries(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    if mode == "tag":
        genre = NSERIES_GENRES.get(query.lower())
        if genre is None:
            return [], False  # its search matches titles only
        url = f"{NSERIES}/novel/categoryProductList.series?" + urlencode(
            {"categoryTypeCode": "genre", "genreCode": genre, "orderTypeCode": "new", "page": page + 1})
        return _nseries_cards(fetch(url))
    cards, more = _nseries_cards(fetch(f"{NSERIES}/search/search.series?" + urlencode(
        {"t": "novel", "fs": "default", "q": query, "page": page + 1})))
    # The search matches titles too ("회귀" finds the author 회귀한작가 and every 회귀 title): the author's own books.
    return [c for c in cards if query in [name.strip() for name in c.get("author", "").split(",")]], more


# -- npia: its search API (the one its search page and the metadata scraper use) ------------------------------

NPIA = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
NPIA_IMAGES = "https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
NPIA_ROWS = 40


def _npia_cover(row: dict) -> str:
    # cover_url is the site's card image (a ~20 KB WebP); novel_img_all is the original upload (often 10 MB
    # and more, served as a download), which a card can't show and which held the app up fetching it.
    for name in ("cover_url", "novel_thumb_all", "novel_thumb", "novel_img", "novel_img_all"):
        value = str(row.get(name) or "").strip()
        if value and "readycover" not in value and "adult_cover_img" not in value:
            return "https:" + value if value.startswith("//") else NPIA_IMAGES + value if value.startswith("/") else value
    return ""


@searcher("npia")
def _npia(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    # A signed-in user's request (the Catalog sends their login) finds their 19+ books as well.
    params = {"cmd": "novel_search", "search_type": "novel_genre" if mode == "tag" else "writer_nick",
              "search_val": query.lstrip("#"), "page": page + 1, "rows": NPIA_ROWS, "sort_col": "count_view",
              "novel_type": "", "start_count_book": "", "end_count_book": "", "novel_age": "", "start_days": "",
              "novel_genre": "", "block_out": 0, "block_stop": 0, "is_contest": 0, "is_complete": "",
              "is_challenge": 0, "list_display": "grid"}
    data = None
    for _ in range(2):  # it answers 502 now and then
        data = _json(fetch(f"{NPIA}/proc/novel?" + urlencode(params), referer=NPIA + "/search",
                           headers={"X-Requested-With": "XMLHttpRequest"}))
        if data is not None:
            break
    rows = [row for row in (data or {}).get("list") or [] if isinstance(row, dict) and row.get("novel_no")]
    want = query.lstrip("#").lower()
    cards = []
    for row in rows:
        tags = [str(tag) for tag in row.get("novel_genre_arr") or [] if str(tag).strip()]
        # Its matches are any part of a tag or a nickname ("TS" finds "TS있음"): only the exact one.
        if (mode == "tag" and want not in [tag.lower() for tag in tags]) or \
                (mode == "author" and str(row.get("writer_nick") or "").lower() != want):
            continue
        cards.append(card("npia", f"{NPIA}/novel/{row['novel_no']}", row.get("novel_name") or "", row.get("writer_nick") or "",
                          _npia_cover(row), tags, row.get("novel_story") or "", views=row.get("count_view"),
                          likes=row.get("count_good"), chapters=row.get("count_book"),
                          status="Completed" if str(row.get("is_complete")) in ("1", "True", "true") else "Ongoing",
                          updated=row.get("last_viewdate"), published=row.get("start_date"),
                          ageRating={"19": 19, "15": 15}.get(str(row.get("novel_age")), 0)))
    total = int((data or {}).get("total_cnt") or 0)
    return cards, len(rows) >= NPIA_ROWS and (page + 1) * NPIA_ROWS < total


# -- mpia: its search API, by whole tag or author ------------------------------------------------------------

MPIA = "https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com"


@searcher("mpia")
def _mpia(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    data = _json(fetch(f"{MPIA}/api/v1/main/search?" + urlencode(
        {"query": query.lstrip("#"), "tab": "TAG" if mode == "tag" else "AUTHOR", "page": page, "size": 40,
         "adultMode": "true"}), headers={"Accept": "application/json"}))
    if not data or data.get("code") != "M000_00000":
        return [], False
    result = data.get("result") or {}
    rows = [row for row in result.get("searchNovelTabDtos") or [] if isinstance(row, dict) and row.get("novelId")]
    if mode == "author":  # it finds any author whose name is like it: only the author's own books
        rows = [row for row in rows if str(row.get("author") or "").strip() == query]
    cards = [card("mpia", f"{MPIA}/novel/detail/{row['novelId']}", row.get("title") or "", row.get("author") or "",
                  row.get("coverUrl") or "",
                  list(dict.fromkeys([g for g in (row.get("mainGenre"), row.get("subGenre")) if isinstance(g, str) and g]
                                     + [t for t in row.get("tag") or [] if isinstance(t, str)])),
                  row.get("story") or "", views=row.get("viewCount"), chapters=row.get("entryCount"),
                  status="Completed" if row.get("finished") else "Ongoing", updated=row.get("updateAt"))
             for row in rows]
    return cards, bool(result.get("hasNext"))


# -- jara: its search API (the site's own; its public key is read from the site's script) ---------------------

JARA = "https://www.\u006a\u006f\u0061\u0072\u0061.com"
JARA_API = "https://api.\u006a\u006f\u0061\u0072\u0061.com"
_jara_params: Dict[str, str] = {}


def _jara_public(fetch: Fetch) -> Dict[str, str]:
    """The site's public API parameters, read once from its main script (as scripts/scrape_jara.py does)."""
    if _jara_params:
        return _jara_params
    from uuid import uuid4

    home = fetch(JARA + "/")
    found = re.findall(r'<script[^>]+src="([^"]*/static/js/main\.[a-zA-Z0-9]+(?:\.chunk)?\.js)"', (home or {}).get("text") or "")
    if not found:
        return {}
    bundle = (fetch(found[-1] if found[-1].startswith("http") else JARA + found[-1]) or {}).get("text") or ""
    bundle = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), bundle)
    at = re.search(r'["\']' + re.escape(JARA_API) + r'["\']\s*,\s*apiKey\s*:', bundle)
    if not at:
        return {}
    config = bundle[at.start():at.start() + 600]
    fields = {key: (re.search(rf'\b{key}\s*:\s*["\']([^"\']*)["\']', config) or [None, ""])[1]
              for key in ("apiKey", "device", "devicetoken", "version")}
    if not all(fields.values()):
        return {}
    _jara_params.update({"api_key": fields["apiKey"], "ver": fields["version"], "device": fields["device"],
                         "devicetoken": fields["devicetoken"], "deviceuid": uuid4().hex})
    return _jara_params


@searcher("jara")
def _jara(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    public = _jara_public(fetch)
    if not public:
        return [], False
    data = _json(fetch(f"{JARA_API}/v2/search/query?" + urlencode(dict(
        public, query=query.lstrip("#"), target="keyword" if mode == "tag" else "member_name", page=page + 1, offset=100,
        enable_fallback_match="false"))))
    if not data or data.get("status") not in (1, "1"):
        return [], False
    rows = [row for row in (data.get("data") or {}).get("list") or [] if isinstance(row, dict) and row.get("book_code")]
    want = query.lstrip("#").lower()
    cards = []
    for row in rows:
        tags = [str(t) for t in row.get("keyword") or [] if str(t).strip()]
        # Its matches are words of a tag or a name: only books with the tag itself, or by the author.
        if (mode == "tag" and want not in [t.lower() for t in tags]) or \
                (mode == "author" and str(row.get("member_name") or "").strip().lower() != want):
            continue
        cover = str(row.get("cover") or "")
        cards.append(card("jara", f"{JARA}/book/{row['book_code']}", row.get("subject") or "", row.get("member_name") or "",
                          "https:" + cover if cover.startswith("//") else cover,
                          ([row["category_name"]] if row.get("category_name") else []) + tags, row.get("intro") or "",
                          views=row.get("page_read"), likes=row.get("recommend_count"),
                          chapters=row.get("total_chapter_count"),
                          status="Completed" if str(row.get("chkfinish")) in ("Y", "1", "True", "true") else "Ongoing",
                          updated=row.get("last_regist_datetime"), published=row.get("first_regist_datetime")))
    total = int(data.get("total_cnt") or 0)
    return cards, len(rows) >= 100 and (page + 1) * 100 < total


# -- sfc: its app API (system tags, author search); other tags from its old tag pages ---------------------------

SFC_API = "https://api.\u0073\u0066\u0061\u0063\u0067.com"
SFC_BOOK = "https://book.\u0073\u0066\u0061\u0063\u0067.com"
SFC_HEADERS = {"User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5", "Accept": "application/json",
               "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE="}  # the app's public login
SFC_EXPAND = "typeName,tags,sysTags,latestChapter,intro"
# Its genres are types, not tags: books of a type are listed by its number.
SFC_TYPES = {"魔幻": 21, "玄幻": 22, "古风": 23, "科幻": 24, "校园": 25, "都市": 26, "游戏": 27, "同人": 28, "悬疑": 29}
_sfc_tags: Dict[str, int] = {}


def _sfc_data(fetch: Fetch, url: str):
    data = _json(fetch(url, headers=SFC_HEADERS))
    return data.get("data") if isinstance(data, dict) else None


def _sfc_card(row: dict) -> dict:
    expand = row.get("expand") or {}
    tags = ([expand["typeName"]] if expand.get("typeName") else []) \
        + [t.get("tagName") for t in expand.get("sysTags") or [] if isinstance(t, dict) and t.get("tagName")] \
        + [t for t in expand.get("tags") or [] if isinstance(t, str)]
    return card("sfc", f"{SFC_BOOK}/Novel/{row.get('novelId')}/", row.get("novelName") or "", row.get("authorName") or "",
                row.get("novelCover") or "", list(dict.fromkeys(tags)), expand.get("intro") or "",
                views=row.get("viewTimes"), likes=row.get("markCount"),
                status="Completed" if row.get("isFinish") else "Ongoing", updated=row.get("lastUpdateTime"),
                published=row.get("addTime"))


def _sfc_tag_page(fetch: Fetch, tag: str, page: int) -> Result:
    """Its old tag pages (books of a free tag; only the older ones have them): 20 books a page."""
    result = fetch(f"{SFC_BOOK}/tag/{quote(tag)}/{page + 1}")
    text = (result or {}).get("text") or ""
    cards = []
    for block in re.findall(r'<ul class="TAG_list[^"]*">(.*?)</ul>', text, re.S):
        found = re.search(r'href="/Novel/(\d+)/"', block)
        if not found:
            continue
        image = re.search(r"<img[^>]*>", block)
        cover = re.search(r'src="([^"]+)"', image.group(0)) if image else None
        title = re.search(r'alt="([^"]*)"', image.group(0)) if image else None
        cover, title = cover.group(1) if cover else "", html.unescape(title.group(1)) if title else ""
        date = re.search(r"(\d{4}-\d{2}-\d{2})", block)
        cards.append(card("sfc", f"{SFC_BOOK}/Novel/{found.group(1)}/", title, "", cover, [tag],
                          status="Completed" if "已完结" in block else "Ongoing" if "连载中" in block else "",
                          updated=date.group(1) if date else ""))
    pages = [int(n) for n in re.findall(r"/tag/[^/\"']+/(\d+)", text)]
    return cards, bool(pages) and max(pages) > page + 1


@searcher("sfc")
def _sfc(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    if mode == "author":
        data = _sfc_data(fetch, f"{SFC_API}/search/novels/result?" + urlencode(
            {"q": query, "page": page, "size": 20, "expand": SFC_EXPAND})) or {}
        rows = [row for row in data.get("novels") or [] if isinstance(row, dict) and row.get("novelId")]
        # It matches titles too: only the author's own books.
        return [_sfc_card(row) for row in rows if str(row.get("authorName") or "").strip() == query], len(rows) >= 20
    query = query.lstrip("#")
    kind = SFC_TYPES.get(query)
    if kind is None:
        if not _sfc_tags:
            for entry in _sfc_data(fetch, f"{SFC_API}/novels/0/sysTags") or []:
                if isinstance(entry, dict) and entry.get("tagName") and entry.get("sysTagId"):
                    _sfc_tags[str(entry["tagName"])] = int(entry["sysTagId"])
        tag = _sfc_tags.get(query)
        if tag is None:
            return _sfc_tag_page(fetch, query, page)
    url = f"{SFC_API}/novels/{kind or 0}/sysTags/novels?" + urlencode(
        {"systagids": "" if kind else tag, "page": page, "size": 50, "sort": "viewtimes", "expand": SFC_EXPAND})
    rows = [row for row in _sfc_data(fetch, url) or [] if isinstance(row, dict) and row.get("novelId")]
    return [_sfc_card(row) for row in rows], len(rows) >= 50


# -- rbooks: its keyword finder (tags) and search (authors), read from the pages' own data ---------------------

RBOOKS = "https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com"
RBOOKS_SETS = (("romance", 1), ("fantasy", 18), ("bl", 15))  # the keyword finder's genres and their keyword sets
_rbooks_keywords: Dict[str, Tuple[str, int, int]] = {}  # a keyword without spaces -> (genre, set, tag)


def _rbooks_props(fetch: Fetch, url: str) -> dict:
    """The page's own data (Next.js): its pageProps. The site refuses plain requests (a bot check): curl_cffi
    passes it, else the Catalog's fetch reads the page in a browser tab."""
    text = ""
    try:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(url, impersonate="chrome", timeout=20)
        if response.status_code < 400:
            text = response.text
    except Exception:
        pass
    if "__NEXT_DATA__" not in text:
        text = (fetch(url) or {}).get("text") or ""
    found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    try:
        return (json.loads(found.group(1)).get("props") or {}).get("pageProps") or {} if found else {}
    except ValueError:
        return {}


def _rbooks_query(props: dict, name: str):
    for entry in ((props.get("dehydratedState") or {}).get("queries") or []):
        key = entry.get("queryKey") or []
        if key and key[0] == name:
            return (entry.get("state") or {}).get("data") or {}
    return {}


def _rbooks_card(book: dict, tags=None) -> dict:
    series = book.get("series") or {}
    book_id = series.get("id") or book.get("id")
    authors = [a.get("name") for a in book.get("authors") or []
               if isinstance(a, dict) and a.get("name") and str(a.get("role") or "").upper() in ("AUTHOR", "STORY_WRITER")]
    thumbnail = (series.get("thumbnail") or {}).get("large") or (book.get("thumbnail") or {}).get("large") or \
        f"https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/cover/{book.get('id')}/large"
    own = book.get("title")
    title = series.get("title") or (own.get("main") if isinstance(own, dict) else own) or ""
    return card("rbooks", f"{RBOOKS}/books/{book_id}", title or "", ", ".join(authors), thumbnail, tags or [],
                str((book.get("introduction") or {}).get("description") or "") if isinstance(book.get("introduction"), dict) else "",
                chapters=series.get("totalEpisodeCount"),
                status="Completed" if series.get("isCompleted") else "Ongoing" if series else "")


@searcher("rbooks")
def _rbooks(mode: str, query: str, page: int, fetch: Fetch) -> Result:
    if mode == "author":
        props = _rbooks_props(fetch, f"{RBOOKS}/search?" + urlencode({"q": query, "page": page + 1}))
        cells = (((props.get("gridData") or {}).get("riGrid") or {}).get("grid") or {}).get("cells") or []
        found = next((cell["cell__SearchBookListWithTab"] for cell in cells
                      if isinstance(cell, dict) and isinstance(cell.get("cell__SearchBookListWithTab"), dict)), {})
        books = [item.get("book") or item for item in found.get("books") or [] if isinstance(item, dict)]
        mine = [b for b in books if query in [a.get("name") for a in b.get("authors") or [] if isinstance(a, dict)]]
        return [_rbooks_card(b) for b in mine], len(books) >= 24
    if not _rbooks_keywords:
        for genre, number in RBOOKS_SETS:
            finder = _rbooks_query(_rbooks_props(fetch, f"{RBOOKS}/keyword-finder/{genre}?set_id={number}"), "KeywordFinder")
            for group in [g for s in ((finder.get("keywordFinder") or {}).get("sets") or []) for g in s.get("groups") or []]:
                for keyword in group.get("keywords") or []:
                    name = ((keyword or {}).get("tag") or {}).get("name")
                    if name and keyword.get("tagId"):
                        _rbooks_keywords.setdefault(re.sub(r"\s+", "", name), (genre, number, int(keyword["tagId"])))
    found = _rbooks_keywords.get(re.sub(r"\s+", "", query.lstrip("#")))
    if found is None:
        return [], False
    genre, number, tag = found
    props = _rbooks_props(fetch, f"{RBOOKS}/keyword-finder/{genre}?" + urlencode(
        {"set_id": number, "tag_ids": tag, "page": page + 1}))
    listed = _rbooks_query(props, "KeywordFinderBooks").get("keywordFinderBooks") or {}
    items = [item for item in listed.get("items") or [] if isinstance(item, dict)]
    cards = [_rbooks_card((item.get("bookShell") or {}).get("book") or {},
                          [t.get("name") for t in item.get("tags") or [] if isinstance(t, dict) and t.get("name")])
             for item in items if (item.get("bookShell") or {}).get("book")]
    return cards, len(items) >= 20 and (page + 1) * 20 < int(listed.get("totalItemCount") or 0)


# -- one book by its id: its live figures, for a NovelDB card as it comes into view ----------------------------
# One request a book (a detail API or page; npia and rbooks have none that reads one book, so their
# author's list is read and the book picked from it), at most LOOKUPS_PER_SOURCE at a time on each site.
# scripts/generic/catalog-app.js has the same lookups: keep the two in step.

LOOKUPS_PER_SOURCE = 2
LOOKERS: Dict[str, Callable[[str, str, Fetch], Optional[dict]]] = {}
_lookup_slots: Dict[str, threading.Semaphore] = {}


def looker(source: str):
    def register(function):
        LOOKERS[source] = function
        _lookup_slots[source] = threading.Semaphore(LOOKUPS_PER_SOURCE)
        return function
    return register


def lookup(source: str, book_id: str, fetch: Fetch, author: str = "") -> Optional[dict]:
    """The book `book_id` of `source` as its site shows it now (a card: its own title and tags, figures,
    cover), or None when the site doesn't answer. `author`: its author (npia and rbooks find it by them)."""
    function = LOOKERS.get(source)
    if function is None or not str(book_id or "").strip():
        return None
    with _lookup_slots[source]:
        try:
            return function(str(book_id).strip(), author or "", fetch)
        except Exception as e:
            log.warning("%s lookup of %s failed: %s", source, book_id, e)
            return None


def _by_author(source: str, book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    if not author.strip():
        return None
    cards, _more = search(source, "author", author, 0, fetch)
    return next((c for c in cards if re.search(rf"/{re.escape(book_id)}(?:[/?#]|$)", c["url"])), None)


@looker("npia")
def _npia_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    # Its search by novel number makes its server fail (scripts/update_rankings.py).
    return _by_author("npia", book_id, author, fetch)


@looker("rbooks")
def _rbooks_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    return _by_author("rbooks", book_id, author, fetch)  # a book's page has no data of it (its script reads it)


@looker("mpia")
def _mpia_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    data = _json(fetch(f"{MPIA}/api/v1/pc/novel-detail/{quote(book_id)}", headers={"Accept": "application/json"}))
    info = ((data or {}).get("result") or {}).get("novelInfo")
    if not isinstance(info, dict) or not info.get("title"):
        return None
    tags = [g for g in info.get("genres") or [] if isinstance(g, str)] + \
        [t.get("title") for t in info.get("tags") or [] if isinstance(t, dict) and t.get("title")]
    return card("mpia", f"{MPIA}/novel/detail/{book_id}", info["title"], info.get("authorName") or "",
                info.get("coverUrl") or info.get("originCoverUrl") or "", list(dict.fromkeys(tags)),
                info.get("introduction") or "", views=info.get("viewCount"), likes=info.get("likeCount"),
                chapters=info.get("chapterCount"), status="Completed" if info.get("finish") else "Ongoing",
                updated=info.get("updatedAt"), published=info.get("createdAt"))


@looker("kpage")
def _kpage_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    data = _json(fetch(f"{KPAGE_API}/api/v1/content/overview?" + urlencode({"series_id": book_id}),
                       headers={"Accept": "application/json", "Referer": KPAGE + "/", "Origin": KPAGE}))
    content = ((data or {}).get("result") or {}).get("content")
    if not isinstance(content, dict) or not content.get("title"):
        return None
    found = _kpage_card(dict(content, series_id=book_id))
    chapters = _count(content.get("on_sale_count"))
    return dict(found, chapters=chapters) if chapters else found


@looker("jara")
def _jara_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    public = _jara_public(fetch)
    if not public:
        return None
    data = _json(fetch(f"{JARA_API}/v1/book/detail.joa?" + urlencode(dict(public, book_code=book_id, promotion_code=""))))
    book = (data or {}).get("book") if (data or {}).get("status") in (1, "1") else None
    if not isinstance(book, dict) or not book.get("subject"):
        return None
    cover = str(book.get("book_img") or "")
    return card("jara", f"{JARA}/book/{book_id}", book["subject"], book.get("writer_name") or "",
                "https:" + cover if cover.startswith("//") else cover,
                ([book["category_name"]] if book.get("category_name") else []) +
                [str(t) for t in book.get("keyword") or [] if str(t).strip()], book.get("intro") or "",
                views=book.get("cnt_page_read"), likes=book.get("cnt_recom"), chapters=book.get("cnt_chapter"),
                status="Completed" if str(book.get("chk_finish")).upper() in ("Y", "1", "TRUE") else "Ongoing",
                updated=book.get("updated"), published=book.get("created"))


@looker("sfc")
def _sfc_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    row = _sfc_data(fetch, f"{SFC_API}/novels/{quote(book_id)}?" + urlencode({"expand": SFC_EXPAND}))
    return _sfc_card(row) if isinstance(row, dict) and row.get("novelId") and row.get("novelName") else None


@looker("nseries")
def _nseries_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    url = f"{NSERIES}/novel/detail.series?" + urlencode({"productNo": book_id})
    soup = _soup(fetch(url))
    head = soup.select_one(".end_head h2") if soup is not None else None
    if head is None or not _text(head):
        return None
    info = soup.select_one(".end_info")
    rows = info.select("li") if info is not None else []
    writer = next((li for li in rows if _text(li.select_one("span")) == "글"), None)
    genre = info.select_one("a[href*='genreCode=']") if info is not None else None
    image = soup.select_one(".pic_area img") or soup.select_one(f"img[alt='{_text(head)}']")
    chapters = re.search(r"총\s*(\d+)\s*(?:화|권)", soup.get_text(" ", strip=True))
    status = _text(info.select_one("li.ing")) if info is not None else ""
    return card("nseries", url, _text(head), _text(writer.select_one("a")) if writer is not None else "",
                (image.get("src") or "") if image is not None else "", [_text(genre)] if genre is not None else [],
                chapters=chapters.group(1) if chapters else None,
                status="Completed" if "완결" in status else "Ongoing" if status else "")


@looker("nweb")
def _nweb_one(book_id: str, author: str, fetch: Fetch) -> Optional[dict]:
    result = fetch(f"{NWEB}/webnovel/list?" + urlencode({"novelId": book_id}))  # its league's page, by redirect
    soup = _soup(result)
    info = soup.select_one(".section_area_info") if soup is not None else None
    if info is None or not _text(info.select_one("h2.title")):
        return None
    url = str((result or {}).get("url") or f"{NWEB}/webnovel/list?novelId={book_id}")
    genre = info.select_one(".info_top .info_group .item")
    tags = ([_text(genre)] if genre is not None and _text(genre) else []) + \
        [_text(a).lstrip("#").strip() for a in soup.select(".end_tag_area .tag_collection a.tag") if _text(a)]
    image = info.select_one(".thumbnail img[src]")
    return card("nweb", url, _text(info.select_one("h2.title")),
                ", ".join(_text(a) for a in info.select('a[href*="target=author"]')),
                (image.get("src") or "") if image is not None else "", list(dict.fromkeys(tags)),
                _text(info.select_one("#summaryText")), likes=_text(info.select_one("._reactionModule ._count")),
                status="Completed" if "완결" in _text(info.select_one(".info_group")) else "")
