"""Search any site with its own search, and read the results as book cards, without site rules.

How a site is searched, tried in order and remembered:
1. Its OpenSearch description (`<link rel="search" type="application/opensearchdescription+xml">`), the
   standard way a site tells browsers how to search it.
2. The search form on its home page (a text or search field in a form), GET or POST.
3. The addresses most sites use: WordPress's `/?s=`, `/search?q=`, `/search?keyword=` ...

Tags use the site's own genre, tag and category pages (their links on the home page), else a search
for the tag. Each results page's next page (its "next" link, else the next page number in its address)
is remembered, and `more` reads it: the Catalog's results go on as they are scrolled. Results pages are read like the generic engine reads chapter lists: links to book pages
are grouped by the shape of their address, and the group that looks most like a list of books (with
cover images and titles) wins. Each card is the link's title, its cover image and nearby text.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, quote_plus, urlencode, urljoin, urlsplit

from noveldb.core.engines.generic import clean_url, decode, shape, site_of, soup_of, text_of

COMMON_SEARCHES = ("/?s={q}", "/search?q={q}", "/search?keyword={q}", "/search?query={q}", "/search/{q}",
                   "/search?title={q}", "/?post_type=wp-manga&s={q}")
# Containers of a site's menus and chrome, not of results.
CHROME = re.compile(r"(^|[\s_-])(nav|navbar|menu|gnb|lnb|snb|header|footer|breadcrumbs?|pagination|pager|sidebar|"
                    r"login|account|util|toolbar|topbar|top-bar|dropdown|modal|popup|banner|notice|cookie)([\s_-]|$)",
                    re.I)
TAG_LINK = re.compile(r"/(genres?|tags?|categor(y|ies)|keywords?|genero|generos|kategori|theme|themes|type|directory|"
                      r"manga-genre|manga_genre|series-genre)s?[/=]", re.I)
_lock = threading.Lock()


def card_shape(url: str) -> str:
    """What the addresses of one list of books share: the site, how deep the path is, its first part
    and the query's names (`/manga/x/`, `/en/romance/x/list?title_no=1`), not the slugs and ids."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    first = re.sub(r"\d+", "{n}", segments[0]) if len(segments) > 1 else ""
    query = "&".join(sorted(q.split("=")[0] for q in parts.query.split("&") if q))
    return f"{parts.netloc.removeprefix('www.')}|{len(segments)}|{first}|{query}"


def _same_site(url: str, base: str) -> bool:
    return site_of(urlsplit(url).hostname or "") == site_of(urlsplit(base).hostname or "")


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[\w']+", (text or "").lower()) if len(w) > 1 or re.match(r"[^\x00-\x7f]", w)]


def relevant(title: str, query: str) -> bool:
    """The title shares the query's words (all of them for short queries), ignoring case and punctuation."""
    want = _words(query)
    have = " ".join(_words(title))
    if not want:
        return True
    if len(want) == 1 and re.match(r"[^\x00-\x7f]", want[0]):
        return want[0] in have.replace(" ", "")
    hits = sum(1 for w in want if w in have)
    return hits >= max(1, len(want) - (len(want) // 3))


# -- discovering how to search -----------------------------------------------------------------

def _opensearch(soup, base: str, fetch: Callable) -> Optional[dict]:
    link = soup.find("link", attrs={"type": re.compile("opensearchdescription", re.I)}, href=True)
    if link is None:
        return None
    result = fetch(clean_url(link["href"], base))
    if not result or result["status"] != 200:
        return None
    text = decode(result)
    for match in re.finditer(r"<Url\b([^>]*)>", text, re.I):
        attributes = match.group(1)
        kind = re.search(r'type="([^"]+)"', attributes)
        template = re.search(r'template="([^"]+)"', attributes)
        if template and (not kind or "html" in kind.group(1)):
            url = template.group(1).replace("&amp;", "&")
            if "{searchTerms}" in url:
                url = url.replace("{searchTerms}", "{q}")
                # Other placeholders ({startPage?}, {count?}, ...) are optional: leave their parameters out.
                url = re.sub(r"[?&][^=&?]+=\{(?!q\})[^}]*\}", "", url)
                if "?" not in url and "&" in url:
                    url = url.replace("&", "?", 1)
                return {"kind": "get", "template": url}
    return None


# Fields of account forms (sign-in, sign-up, newsletter, codes), which are never searches.
ACCOUNT_FIELD = re.compile(r"(user|login|email|mail|pass|code|phone|account|nick|captcha)", re.I)


def _form(soup, base: str) -> Optional[dict]:
    best = None
    for form in soup.find_all("form"):
        fields = form.find_all("input")
        text = [f for f in fields if (f.get("type") or "text").lower() in ("text", "search")
                and f.get("name") and "pass" not in (f.get("name") or "").lower()]
        if not text or any(ACCOUNT_FIELD.search(f.get("name") or "") for f in fields
                           if (f.get("type") or "text").lower() not in ("hidden", "submit")):
            continue  # a sign-in, sign-up or newsletter form, not a search
        hint = " ".join([form.get("action") or "", form.get("id") or "", " ".join(form.get("class") or []),
                         text[0].get("name") or "", text[0].get("placeholder") or ""]).lower()
        score = 2 * ("search" in hint) + ("q" == text[0].get("name")) + ("s" == text[0].get("name")) + \
            ("keyword" in hint) + ("searchkey" in hint) - 3 * ("login" in hint or "comment" in hint or "mail" in hint)
        if best is None or score > best[0]:
            hidden = {f["name"]: f.get("value", "") for f in fields
                      if (f.get("type") or "").lower() == "hidden" and f.get("name")}
            best = (score, {"kind": (form.get("method") or "get").lower(),
                            "action": clean_url(form.get("action") or base, base) or base,
                            "field": text[0]["name"], "hidden": hidden})
    return best[1] if best and best[0] >= 0 else None


def _tag_name(name: str) -> str:
    """A tag as words: "Gender Bender", "gender-bender" and "gender_bender" are one tag."""
    return re.sub(r"[\s_\-]+", " ", (name or "").strip().lower())


def is_home_again(cards: List[dict], plan: dict) -> bool:
    """Results that are mostly the home page's own lists (a search that led back to the home page)."""
    home = set(plan.get("home_cards") or ())
    if not home or not cards:
        return False
    return sum(card["url"] in home for card in cards) >= len(cards) * 0.5


def _searched(final: str, home: str, query: str) -> bool:
    """The address a search ended on is a search for `query` (not the home page it redirected to)."""
    from urllib.parse import unquote_plus

    if final.rstrip("/") == home.rstrip("/"):
        return False
    words = [w for w in _tag_name(query).split() if len(w) > 1]
    text = _tag_name(unquote_plus(final))
    return not words or any(w in text for w in words)


def _tag_links(soup, base: str) -> Dict[str, str]:
    tags = {}
    for anchor in soup.find_all("a", href=True):
        href = clean_url(anchor["href"], base)
        if not href or not _same_site(href, base) or not TAG_LINK.search(href):
            continue
        name = re.sub(r"\s*\(\d+\)$", "", text_of(anchor).strip().lower())
        if 1 < len(name) <= 40 and name not in tags:
            tags[name] = href
    return tags


# A link to a list's next page, by its words or symbols (in the languages of the sites searched).
NEXT_WORDS = re.compile(r"^\s*(next( page)?|older( posts| entries)?|more|›|»|>|>>|→|다음( 페이지)?|次へ|次のページ|下一页|下页|后页|"
                        r"siguiente|suivant|próxima|proxima|weiter|далее|следующая|التالي|selanjutnya|berikutnya|sonraki|tiếp)\s*[›»>→]*\s*$", re.I)
PAGE_IN_URL = re.compile(r"([?&](?:page|p|pg|paged|pageno|page_no|pagenum)=)(\d+)|(/page/)(\d+)", re.I)


def next_page(page: str, url: str) -> str:
    """The address of a results page's next page ("" when there is none to be found): the page's own
    rel=next link, a "Next" link, else the address with its page number one up (or page 2 of it, when a
    link to that is on the page)."""
    soup = soup_of(page)
    found = soup.find(["link", "a"], rel=lambda value: value and "next" in (value if isinstance(value, list) else [value]),
                      href=True)
    if found is not None:
        href = clean_url(found["href"], url)
        if href and href != url and _same_site(href, url):
            return href
    for anchor in soup.find_all("a", href=True):
        label = text_of(anchor) or anchor.get("aria-label") or anchor.get("title") or ""
        classes = " ".join(anchor.get("class") or [])
        if NEXT_WORDS.match(label) or re.search(r"(^|[\s_-])next([\s_-]|$)", classes, re.I):
            href = clean_url(anchor["href"], url)
            if href and href.rstrip("/") != url.rstrip("/") and _same_site(href, url) and not href.startswith("javascript"):
                return href
    match = PAGE_IN_URL.search(url)
    if match:
        # A page past the last one has no books: `more` then stops there.
        number = match.group(2) if match.group(1) else match.group(4)
        start, end = match.span(2) if match.group(1) else match.span(4)
        return url[:start] + str(int(number) + 1) + url[end:]
    for anchor in soup.find_all("a", href=True):
        href = clean_url(anchor["href"], url)
        if href and _same_site(href, url) and re.search(r"([?&](page|p|paged)=2(&|$))|/page/2/?($|\?)", href) \
                and urlsplit(href).path.split("/page/")[0].rstrip("/") == urlsplit(url).path.rstrip("/"):
            return href
    return ""


# Where sites keep their popular or ranking lists, tried in turn for the Catalog's top lists.
POPULAR_PATHS = ("/popular", "/ranking", "/rankings", "/rank", "/top", "/trending", "/most-popular",
                 "/manga?order=popular", "/series?order=popular", "/comics?order=popular", "/manga/?m_orderby=views",
                 "/directory?sort=popular", "/browse?sort=popular", "/list?sort=views")

class SiteSearch:
    """Searches sites the generic way; remembers what worked for each (in `cache_file` when given)."""

    def __init__(self, fetch: Callable, cache_file=None, max_age: float = 7 * 24 * 3600):
        self.fetch = fetch
        self.render: Optional[Callable] = None  # the page as a browser renders it, for result lists built by scripts
        self.cache_file = cache_file
        self.max_age = max_age
        self._plans: Dict[str, dict] = {}
        # (home, kind, query) -> the next page of the list the last search, tag or popular list read.
        self.next_pages: Dict[tuple, str] = {}
        if cache_file is not None:
            try:
                self._plans = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._plans = {}

    def _save(self) -> None:
        if self.cache_file is None:
            return
        with _lock:
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(self._plans), encoding="utf-8")
            except OSError:
                pass

    def plan(self, home: str) -> dict:
        """How to search `home`'s site: {"ways": [...], "tags": {name: url}, "home": final address}."""
        known = self._plans.get(home)
        if known and time.time() - known.get("checked", 0) < self.max_age and "home_cards" in known:
            return known
        result = self.fetch(home)
        plan = {"checked": time.time(), "ways": [], "tags": {}, "home": home,
                "reachable": bool(result and result["status"] < 400)}
        if result and result["status"] < 400:
            final = result.get("url") or home
            soup = soup_of(decode(result))
            plan["home"] = final
            found = _opensearch(soup, final, self.fetch)
            if found:
                plan["ways"].append(found)
            form = _form(soup, final)
            if form:
                plan["ways"].append(form)
            plan["tags"] = _tag_links(soup, final)
            plan["home_cards"] = [card["url"] for card in extract_cards(decode(result), final)][:80]
        root = plan["home"].rstrip("/")
        parts = urlsplit(root)
        origin = f"{parts.scheme}://{parts.netloc}"
        plan["ways"].extend({"kind": "get", "template": origin + path, "guess": True} for path in COMMON_SEARCHES)
        self._plans[home] = plan
        self._save()
        return plan

    def _remember_next(self, home: str, kind: str, query: str, result: Optional[dict], url: str = "") -> None:
        page_url = (result or {}).get("url") or url
        found = next_page(decode(result), page_url) if result and page_url else ""
        with _lock:
            if found:
                self.next_pages[(home, kind, query)] = found
            else:
                self.next_pages.pop((home, kind, query), None)

    def has_more(self, home: str, kind: str, query: str) -> bool:
        return (home, kind, query) in self.next_pages

    def more(self, home: str, kind: str, query: str = "", limit: int = 40) -> List[dict]:
        """The next page of the list a search ("search"), tag ("tag") or popular list ("popular") of
        `home` read last: its cards (relevant ones, for a search); its own next page is remembered."""
        with _lock:
            url = self.next_pages.pop((home, kind, query), "")
        if not url:
            return []
        result = self.fetch(url)
        if not result or result["status"] >= 400:
            return []
        cards = extract_cards(decode(result), result.get("url") or url)
        if kind == "search":
            cards = [card for card in cards if relevant(card["title"], query)]
        if cards:
            self._remember_next(home, kind, query, result, url)
        return [dict(card, verified=True) if kind == "tag" else card for card in cards[:limit]]

    def _request(self, way: dict, query: str):
        if way["kind"] == "get" and "template" in way:
            template = way["template"]
            value = quote(query) if "/{q}" in template else quote_plus(query)
            if way.get("guess"):
                # A guessed address: one quick plain request, never a browser tab (most guesses miss).
                return self.fetch(template.replace("{q}", value), quick=True)
            return self.fetch(template.replace("{q}", value))
        data = dict(way.get("hidden") or {}, **{way["field"]: query})
        if way["kind"] == "post":
            return self.fetch(way["action"], method="POST", data=urlencode(data),
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        joiner = "&" if "?" in way["action"] else "?"
        return self.fetch(way["action"] + joiner + urlencode(data))

    def search(self, home: str, query: str, limit: int = 20, budget: float = 20.0) -> List[dict]:
        """Cards from the site's own search for `query` (relevant ones only), within `budget` seconds."""
        started = time.monotonic()
        plan = self.plan(home)
        if not plan.get("reachable", True):
            return []  # the site refused its home page: its search pages will not answer either
        for index, way in enumerate(list(plan["ways"])):
            if time.monotonic() - started > budget:
                break
            try:
                result = self._request(way, query)
            except Exception:
                continue
            if result and result["status"] in (401, 403, 429, 503):
                break  # the site refuses these requests; more guesses would be refused too
            if not result or result["status"] >= 400:
                continue
            cards = [card for card in extract_cards(decode(result), result.get("url") or home)
                     if relevant(card["title"], query)]
            if cards:
                if index:  # try what worked first next time
                    plan["ways"].insert(0, plan["ways"].pop(index))
                    self._save()
                self._remember_next(home, "search", query, result)
                return cards[:limit]
        # Nothing in the plain pages: the site's own way (not a guess) may build its results with scripts.
        own = next((way for way in plan["ways"] if not way.get("guess") and way["kind"] == "get"), None)
        if own is not None and self.render is not None and time.monotonic() - started <= budget:
            url = own["template"].replace("{q}", quote_plus(query)) if "template" in own else                 own["action"] + ("&" if "?" in own["action"] else "?") + urlencode(dict(own.get("hidden") or {},
                                                                                     **{own["field"]: query}))
            result = self.render(url)
            if result and result["status"] < 400:
                return [card for card in extract_cards(decode(result), result.get("url") or url)
                        if relevant(card["title"], query)][:limit]
        return []

    def popular(self, home: str, limit: int = 30) -> List[dict]:
        """Cards from the site's popular or ranking list (the first common address that has one, else the
        home page's biggest list); the address that worked is remembered."""
        plan = self.plan(home)
        if not plan.get("reachable", True):
            return []
        parts = urlsplit(plan["home"].rstrip("/"))
        origin = f"{parts.scheme}://{parts.netloc}"
        known = plan.get("popular")
        tries = ([known] if known else []) + [origin + path for path in POPULAR_PATHS if origin + path != known]
        for url in tries:
            result = self.fetch(url, quick=url != known)
            if not result or result["status"] >= 400:
                continue
            cards = extract_cards(decode(result), result.get("url") or url)
            if len(cards) >= 6:
                if plan.get("popular") != url:
                    plan["popular"] = url
                    self._save()
                self._remember_next(home, "popular", "", result, url)
                return cards[:limit]
        result = self.fetch(plan["home"])
        if result and result["status"] < 400:
            self._remember_next(home, "popular", "", result, plan["home"])
            return extract_cards(decode(result), result.get("url") or plan["home"])[:limit]
        return []

    def tag(self, home: str, tag: str, limit: int = 24) -> List[dict]:
        """Cards from the site's page for `tag` (a genre, tag or category), else its search for the tag."""
        plan = self.plan(home)
        if not plan.get("reachable", True):
            return []
        want = _tag_name(tag)
        names = {_tag_name(name): url for name, url in plan["tags"].items()}
        url = names.get(want) or next((u for name, u in names.items() if want in name or name in want), None)
        if url:
            result = self.fetch(url)
            if result and result["status"] < 400:
                cards = extract_cards(decode(result), result.get("url") or url)
                if cards:
                    self._remember_next(home, "tag", tag, result, url)
                    return [dict(card, verified=True) for card in cards[:limit]]  # the site's own page for the tag
        cards = []
        for way in plan["ways"][:3]:
            try:
                result = self._request(way, tag)
            except Exception:
                continue
            if not result or result["status"] >= 400 or not _searched(result.get("url") or "", plan["home"], tag):
                continue  # a guessed address that led back to the home page: its lists are not about the tag
            cards = extract_cards(decode(result), result.get("url") or home)
            if is_home_again(cards, plan):
                cards = []
                continue
            if cards:
                self._remember_next(home, "tag", tag, result)
                break
        return cards[:limit]


# -- reading a results page --------------------------------------------------------------------

def _card_box(anchor, url: str):
    """The element holding one result: the largest ancestor that links to this book only."""
    box = anchor
    for parent in anchor.parents:
        if parent.name in ("body", "html", "[document]"):
            break
        links = {clean_url(a.get("href"), url) for a in parent.find_all("a", href=True)}
        links.discard("")
        if len([link for link in links if card_shape(link) == card_shape(url) and link != url]) > 0:
            break
        box = parent
    return box


def _image_of(node, base: str) -> str:
    image = node.find("img") if node is not None else None
    if image is None:
        style = node.find(style=re.compile(r"background(-image)?\s*:\s*url", re.I)) if node is not None else None
        if style is not None:
            found = re.search(r"url\(['\"]?([^'\")]+)", style["style"])
            return clean_url(found.group(1), base) if found else ""
        return ""
    for attribute in ("data-src", "data-original", "data-lazy-src", "data-srcset", "srcset", "src"):
        value = image.get(attribute)
        if value and not value.startswith("data:"):
            value = value.split(",")[0].split()[0]
            return clean_url(value, base)
    return ""


def _in_chrome(anchor, depth: int = 6) -> bool:
    """The link sits in a menu, header, sidebar or the like: its nearest few containers say so.
    Page-level wrappers (body classes like "page-template", layout columns) are too far up to count."""
    for level, parent in enumerate(anchor.parents):
        if level >= depth or parent.name in ("body", "html", "main", "[document]"):
            return False
        if parent.name in ("nav", "menu"):
            return True
        hint = " ".join(parent.get("class") or []) + " " + (parent.get("id") or "") + " " + (parent.get("role") or "")
        if CHROME.search(hint):
            return True
    return False


def extract_cards(page: str, base: str) -> List[dict]:
    """Book cards on a search, tag or list page: [{url, title, cover, snippet}]. The page's app data when
    it lists more books than the page draws (bookkor draws 9 of its 40)."""
    drawn = _drawn_cards(page, base)
    listed = _app_data_cards(page, base)
    return listed if len(listed) > len(drawn) else drawn


def _app_data_cards(page: str, base: str) -> List[dict]:
    """Cards from an Inertia app's page data: a paginated list (props.<name>.data) of books with a title
    and a slug, each book at /<slug>. [] for any other page (a book's episodes included)."""
    found = re.search(r'<script[^>]*\bdata-page="app"[^>]*>(.*?)</script>', page or "", re.S)
    if not found:
        return []
    try:
        props = json.loads(found.group(1)).get("props") or {}
    except (ValueError, AttributeError):
        return []
    parts = urlsplit(base)
    origin = f"{parts.scheme}://{parts.netloc}"
    best: List[dict] = []
    for value in props.values() if isinstance(props, dict) else []:
        items = value.get("data") if isinstance(value, dict) else None
        if not isinstance(items, list):
            continue
        cards = []
        for item in items:
            if not isinstance(item, dict) or not item.get("title") or not item.get("slug") or "series_id" in item:
                continue  # (a series' episodes carry its id: not a list of books)
            cover = str(item.get("thumbnail") or "")
            if cover and not re.match(r"https?://|/", cover):
                cover = f"{origin}/storage/{cover}"  # a Laravel app's public files
            tags = [t.strip().lstrip("#") for t in str(item.get("tags_string") or "").split(",")]
            cards.append({"url": f"{origin}/{quote(str(item['slug']).strip('/'))}", "title": str(item["title"]),
                          "cover": urljoin(origin + "/", cover) if cover else "", "snippet": "",
                          "tags": list(dict.fromkeys(t for t in tags if t))})
        if len(cards) > len(best):
            best = cards
    return best if len(best) >= 6 else []


def _drawn_cards(page: str, base: str) -> List[dict]:
    """Book cards drawn on the page: [{url, title, cover, snippet}]."""
    from noveldb.core.catalog.generic_search import looks_like_book

    soup = soup_of(page)
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    groups: Dict[str, list] = {}
    for anchor in soup.find_all("a", href=True):
        url = clean_url(anchor["href"], base)
        if not url or not _same_site(url, base) or url.rstrip("/") == base.rstrip("/"):
            continue
        if _in_chrome(anchor):
            continue
        title = text_of(anchor) or anchor.get("title") or (anchor.find("img") or {}).get("alt", "") \
            if anchor.find("img") is not None else text_of(anchor) or anchor.get("title", "")
        if not looks_like_book(url, title or ""):
            continue
        groups.setdefault(card_shape(url), []).append((url, title or "", anchor))
    best, best_score = None, 0.0
    for key, members in groups.items():
        urls = list(dict.fromkeys(m[0] for m in members))
        if len(urls) < 1:
            continue
        with_images = sum(1 for m in members if m[2].find("img") is not None) / len(members)
        titled = sum(1 for m in members if len(m[1]) > 3) / len(members)
        if len(urls) < 2 and not with_images:
            continue
        average = sum(len(m[1]) for m in members) / len(members)
        if with_images < 0.2 and average < 6:
            continue  # short words without covers: a menu, not books
        score = len(urls) ** 0.7 * (1 + 2 * with_images + titled)
        if score > best_score:
            best, best_score = members, score
    if not best:
        return []
    cards: Dict[str, dict] = {}
    for url, title, anchor in best:
        card = cards.setdefault(url, {"url": url, "title": "", "cover": "", "snippet": ""})
        if len(title) > len(card["title"]) and len(title) < 200:
            card["title"] = title
        if not card["cover"]:
            card["cover"] = _image_of(anchor, base)
    for card in cards.values():
        if not card["cover"] or not card["snippet"]:
            anchor = next(m[2] for m in best if m[0] == card["url"])
            box = _card_box(anchor, card["url"])
            if not card["cover"]:
                card["cover"] = _image_of(box, base)
            snippet = text_of(box)
            if card["title"] and snippet.startswith(card["title"]):
                snippet = snippet[len(card["title"]):].strip()
            card["snippet"] = snippet[:300]
        card["title"] = _best_title(card, [m[2] for m in best if m[0] == card["url"]])
    return [card for card in cards.values() if card["title"]]


_RANK = re.compile(r"^\s*(?:#|no\.?\s*)?\"?\d+(?:\.\d+)?\"?\s*(?:up\s*\d+|new|hot|down\s*\d+)?\s+", re.I)
_NOT_TITLES = re.compile(r"^\s*\[?(cover|image|thumbnail|read( now| more)?|new|hot|more|details?|lihat detail|start reading)\]?\s*$",
                         re.I)


def _best_title(card: dict, anchors: list) -> str:
    """The book's name: a heading in its card, the link's title or its image's alt text, else the link
    text without a leading rank or rating ("9.1 Title", "#3 Title")."""
    candidates = []
    box = _card_box(anchors[0], card["url"]) if anchors else None
    if box is not None:
        heading = box.find(["h1", "h2", "h3", "h4", "h5"]) or box.select_one("[class*=title], [class*=name]")
        if heading is not None:
            candidates.append(text_of(heading))
    for anchor in anchors:
        candidates.append(anchor.get("title") or "")
        image = anchor.find("img")
        if image is not None:
            candidates.append(image.get("alt") or image.get("title") or "")
    candidates.append(card["title"])
    for candidate in candidates:
        text = _RANK.sub("", re.sub(r"\s+", " ", candidate or "")).strip()
        text = re.sub(r"\s+(cover|thumbnail|poster)$", "", text, flags=re.I)  # an image's alt text
        if 2 < len(text) <= 200 and not _NOT_TITLES.match(text):
            return text
    return card["url"].rstrip("/").split("/")[-1].replace("-", " ")
