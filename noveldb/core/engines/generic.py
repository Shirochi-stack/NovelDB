"""The generic fallback engine: any site's metadata, chapter list and chapter text, without site rules.

It runs last, after every rule set. What it reads:
- Metadata: JSON-LD (Book, CreativeWork, ...), Open Graph and other meta tags, then the page itself
  (the main heading, an "Author" label, a cover image near the title, genre and tag links).
- The chapter list: links on the book's site are grouped by the shape of their address (numbers
  become placeholders); the group that looks most like chapters wins (chapter words in the link
  text, numbers that count up, addresses under the book's own). Chapter-list pages that continue on
  further pages are followed. A page that is itself a chapter is followed through its "next" links.
- Chapter text: the block with the most paragraph text and the fewest links (a readability-style
  score), cleaned of scripts, ads, sharing and navigation; chapters split over pages are joined.

Pages come through `fetch` (the app's transport, which falls back to the browser), and `render`
(the browser's rendered page) is used when the plain page has no chapter list, for sites that build
their pages with scripts. `extract_metadata` is shared with the Catalog's generic search.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

LABEL = "Generic"

# "Chapter 12", "Ch. 12", "第12章", "12화", "第12話", "Episode 12", "Глава 12", "Capítulo 12", ...
CHAPTER_WORDS = re.compile(
    r"(chapter|chap\b|ch\.?\s*\d|episode|ep\.?\s*\d|\bpart\s*\d|prologue|epilogue|side ?story|extra|"
    r"глава|capítulo|capitulo|chapitre|kapitel|bölüm|bab\s*\d|chương|ตอน|"
    r"第\s*[\d零一二三四五六七八九十百千两]+\s*[章节話话回卷集]|\d+\s*[화話话章回]|[화話]\s*$)",
    re.I)
NEXT_WORDS = re.compile(r"^\s*(next(\s+chapter)?|next\s*»|»|›|→|下一章|下一页|下章|下一話|下一话|다음\s*화?|다음 ?페이지|次へ|次の話|"
                        r"siguiente|suivant|próximo|weiter|далее|следующая|sonraki)\s*[»›>→]*\s*$", re.I)
NEXT_PAGE_WORDS = re.compile(r"(下一页|下页|next\s*page|다음\s*페이지|次のページ|página siguiente|page suivante)", re.I)
BAD_BLOCKS = re.compile(r"(comment|share|social|sidebar|footer|header|nav|menu|breadcrumb|related|recommend|"
                        r"advert|\bads?\b|sponsor|banner|popup|modal|cookie|subscribe|newsletter|login|rating|"
                        r"widget|pagination|pager|toolbar|copyright)", re.I)
GOOD_BLOCKS = re.compile(r"(chapter|content|text|reader|reading|entry|article|post-body|novel|story|txt|"
                         r"main|nr_?content|booktxt|bookcontent)", re.I)
_SECOND_LEVEL = {"co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu"}


# -- pages -----------------------------------------------------------------------------------------

def decode(result: dict) -> str:
    """The page's text, in the charset its headers or its meta tag declare (GBK sites included)."""
    content = result.get("content")
    if content is None:
        return result.get("text") or ""
    headers = str(result.get("headers") or "")
    match = re.search(r"charset=([\w-]+)", headers, re.I) or re.search(
        rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", content[:4096], re.I)
    charset = match.group(1) if match else "utf-8"
    if isinstance(charset, bytes):
        charset = charset.decode("ascii", "ignore")
    charset = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030"}.get(charset.lower(), charset)
    try:
        return content.decode(charset, "replace")
    except LookupError:
        return content.decode("utf-8", "replace")


def soup_of(html: str):
    from bs4 import BeautifulSoup

    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def site_of(host: str) -> str:
    """`m.example.co.uk` gives `example.co.uk`: links on any of the site's hosts count as its own."""
    labels = [label for label in (host or "").lower().split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    keep = 3 if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def clean_url(href: str, base: str) -> str:
    """An absolute http(s) address without its fragment, or ""."""
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
        return ""
    try:
        parts = urlsplit(urljoin(base, href))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def text_of(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True) if node is not None else "").strip()


# -- metadata --------------------------------------------------------------------------------------

def _json_ld(soup) -> List[dict]:
    out = []
    for script in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                out.append(item)
                for key in ("@graph", "mainEntity", "itemListElement"):
                    value = item.get(key)
                    if isinstance(value, list):
                        stack.extend(value)
                    elif isinstance(value, dict):
                        stack.append(value)
    return out


def _ld_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(filter(None, (_ld_text(v) for v in value)))
    if isinstance(value, dict):
        return str(value.get("name") or value.get("url") or value.get("@id") or "")
    return str(value or "")


def _meta(soup, *names) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _strip_site_name(title: str, site_name: str, url: str) -> str:
    from noveldb.core.catalog.sites import clean_page_title

    title = clean_page_title(title, url) if title else ""
    if site_name and title.lower().endswith(site_name.lower()) and len(title) > len(site_name) + 3:
        title = title[: -len(site_name)].rstrip(" -|–—·:")
    # "Read X Online Free", "X Novel - Read X for free"
    title = re.sub(r"^(read\s+)", "", title, flags=re.I)
    title = re.sub(r"\s+(novel|light novel|manga|manhwa|webtoon)?\s*(online)?\s*(for\s+)?free.*$", "", title, flags=re.I)
    return title.strip()


_DATE = re.compile(r"(\d{4})[-./年]\s*(\d{1,2})[-./月]\s*(\d{1,2})")
_COUNT = re.compile(r"(\d[\d,.\s]*\d|\d)\s*([kmb万萬억천千])?", re.I)
_LABELS = {
    "published": r"(published|release[sd]?|first published|created|start(ed)?|连载时间|發布|发布|出版|연재\s*시작|公開日|配信開始)",
    "updated": r"(updated?|last updated?|last update|latest update|更新(时间|時間|日)?|최근\s*업데이트|업데이트|最終更新)",
    "rating": r"(rating|rated|score|评分|評分|평점|評価)",
    "likes": r"(likes?|favou?rites?|bookmarks?|followers?|收藏|推荐|좋아요|선호작|お気に入り|ブックマーク)",
    "views": r"(views?|reads?|点击|點擊|阅读|조회수?|閲覧数|PV)",
    "chapters": r"(chapters?|episodes?|章节数|章節|회차|話数)",
}


def _date(value: str) -> str:
    """An ISO day (2024-03-09) for anything date-like, else the text as shown."""
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    match = _DATE.search(value)
    if match:
        year, month, day = (int(part) for part in match.groups())
        if 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return value[:40] if re.search(r"\d", value) or re.search(r"ago", value, re.I) else ""


def _count(value: str) -> str:
    """A count as shown, without its label: "12.3K", "4,521"; "" for none or zero (a page's empty counter)."""
    match = _COUNT.search(str(value or ""))
    if not match or not re.sub(r"[0.,\s]", "", match.group(1)):
        return ""
    return match.group(1).replace(" ", "") + (match.group(2) or "").upper()


def _labelled(soup, label: str) -> str:
    """The text after a label like "Updated:" or "Rating 4.5", in the same element or the next one."""
    pattern = re.compile(rf"^\s*{label}\s*[:：]?", re.I)
    for found in soup.find_all(string=pattern, limit=8):
        node = found.find_parent()
        if node is None or node.name in ("script", "style", "a", "option", "title"):
            continue
        rest = pattern.sub("", str(found), 1).strip(" :：|·")
        if not re.search(r"\d", rest):
            whole = text_of(node)
            rest = pattern.sub("", whole, 1).strip(" :：|·") if len(whole) < 80 else ""
        if not re.search(r"\d", rest):
            sibling = node.find_next_sibling()
            rest = text_of(sibling)[:60] if sibling is not None else ""
        if re.search(r"\d", rest):
            return rest[:60]
    return ""


def _interaction(book: dict, *kinds: str) -> str:
    stats = book.get("interactionStatistic")
    for stat in stats if isinstance(stats, list) else [stats]:
        if isinstance(stat, dict) and any(kind in _ld_text(stat.get("interactionType")) for kind in kinds):
            return str(stat.get("userInteractionCount") or "")
    return ""


def _figures(soup, book: dict) -> dict:
    """Dates and counts a book page shows: from its structured data, its meta tags, then labelled text."""
    rating = book.get("aggregateRating") if isinstance(book.get("aggregateRating"), dict) else {}
    value = str(rating.get("ratingValue") or "") or (_meta(soup, "og:rating", "rating") or "")
    if not value:
        node = soup.select_one("[itemprop=ratingValue]")
        value = (node.get("content") or text_of(node)) if node is not None else ""
    if not value:
        value = _labelled(soup, _LABELS["rating"])
    value = (re.search(r"\d+(?:[.,]\d+)?", value) or [""])[0].replace(",", ".")
    if value and not float(value):
        value = ""  # "0" stars: nobody rated it
    best = str(rating.get("bestRating") or "")
    if value and best and best not in ("5", "5.0"):
        value = f"{value}/{best}"
    published = (_ld_text(book.get("datePublished") or book.get("dateCreated"))
                 or _meta(soup, "article:published_time", "book:release_date", "og:novel:release_time", "datePublished")
                 or _labelled(soup, _LABELS["published"]))
    updated = (_ld_text(book.get("dateModified"))
               or _meta(soup, "article:modified_time", "og:updated_time", "og:novel:update_time", "dateModified")
               or _labelled(soup, _LABELS["updated"]))
    return {
        "published": _date(published),
        "updated": _date(updated),
        "rating": value,
        "ratings": _count(str(rating.get("ratingCount") or rating.get("reviewCount") or "")),
        "likes": _count(_interaction(book, "Like", "Bookmark", "Follow") or _labelled(soup, _LABELS["likes"])),
        "views": _count(_interaction(book, "View", "Read") or _labelled(soup, _LABELS["views"])),
        "chapters": _count(str(book.get("numberOfEpisodes") or book.get("numberOfPages") or "")
                           or _labelled(soup, _LABELS["chapters"])),
    }


def extract_metadata(html: str, url: str, soup=None) -> dict:
    """{title, author, cover, description, tags, status, published, updated, rating, ratings, likes, views,
    chapters} read from any book (or search result) page; a figure the page doesn't show is ""."""
    soup = soup or soup_of(html)
    book = {}
    for item in _json_ld(soup):
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(k) in ("Book", "CreativeWork", "CreativeWorkSeries", "BookSeries", "ComicSeries", "Article",
                          "Product", "Novel", "WebPage") for k in kinds):
            score = sum(1 for key in ("author", "image", "genre", "description", "numberOfPages") if item.get(key))
            if score >= book.get("_score", -1):
                book = dict(item, _score=score)
    site_name = _meta(soup, "og:site_name", "application-name")
    title = _ld_text(book.get("name") or book.get("headline")) or _meta(soup, "og:title", "twitter:title")
    if not title:
        h1 = soup.find("h1")
        title = text_of(h1) if h1 else text_of(soup.title)
    author = _ld_text(book.get("author") or book.get("creator")) or _meta(soup, "book:author", "author", "og:novel:author")
    if not author:
        label = soup.find(string=re.compile(r"^\s*(author|writer|作者|저자|작가|著者|autor|auteur|автор)\s*[:：]?\s*$", re.I))
        node = label.find_parent() if label else None
        if node is not None:
            # "Author: <a>Name</a>" in one element, or the label and the name side by side.
            inside = text_of(node).replace(str(label).strip(), "", 1).strip(" :：")
            sibling = node.find_next_sibling()
            author = inside or (text_of(sibling) if sibling else "")
        if not author:
            link = soup.find("a", href=re.compile(r"/(author|authors|writer|artist)/", re.I))
            author = text_of(link) if link else ""
    if not author:
        found = soup.find(string=re.compile(r"(author|作者|저자|著者)\s*[:：]", re.I))
        if found:
            author = re.split(r"[:：]", str(found), 1)[-1].strip()[:80]
    cover = _ld_text(book.get("image")) or _meta(soup, "og:image", "twitter:image", "og:novel:image")
    if not cover:
        image = soup.select_one("[class*=cover] img, [class*=thumb] img, [class*=poster] img, img[class*=cover], "
                                "img[itemprop=image], [class*=book] img")
        if image is not None:
            cover = image.get("data-src") or image.get("data-original") or image.get("src") or ""
    cover = clean_url(cover, url) if cover else ""
    description = _ld_text(book.get("description")) or _meta(soup, "og:description", "description", "twitter:description")
    block = soup.select_one("[class*=summary], [class*=synopsis], [class*=description], [id*=description], "
                            "[class*=desc], [itemprop=description], [class*=intro], #intro")
    if block is not None:
        longer = text_of(block)
        if len(longer) > len(description):
            description = longer
    tags = []
    genre = book.get("genre") or book.get("keywords")
    if genre:
        tags.extend(t.strip() for t in re.split(r"[,;|]", _ld_text(genre)) if t.strip())
    for link in soup.find_all("a", href=re.compile(r"/(genres?|tags?|categor(y|ies)|keywords?)/", re.I))[:40]:
        word = text_of(link)
        if 1 < len(word) <= 40:
            tags.append(word)
    status = _meta(soup, "og:novel:status")
    if not status:
        found = soup.find(string=re.compile(r"\b(ongoing|completed|complete|hiatus)\b", re.I))
        status = re.search(r"(ongoing|completed|complete|hiatus)", str(found or ""), re.I)
        status = status.group(1).title() if status else ""
    return {
        **_figures(soup, book),
        "title": _strip_site_name(re.sub(r"\s+", " ", title or "").strip(), site_name, url)[:300],
        "author": re.sub(r"\s+", " ", author or "").strip()[:200],
        "cover": cover,
        "description": re.sub(r"[ \t]+", " ", description or "").strip()[:4000],
        "tags": list(dict.fromkeys(tags))[:30],
        "status": status,
        "site": site_name,
    }


# -- the chapter list ------------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+")


def shape(url: str) -> str:
    """The address with its numbers as placeholders: chapters of one book share a shape."""
    parts = urlsplit(url)
    path = _NUMBER.sub("{n}", parts.path.rstrip("/"))
    query = "&".join(sorted(_NUMBER.sub("{n}", q.split("=")[0]) + "=" for q in parts.query.split("&") if q))
    # Slugs differ per chapter ("chapter-12-the-end"): keep only the part before the first number.
    segments = path.split("/")
    if segments and "{n}" in segments[-1]:
        head = segments[-1].split("{n}")[0]
        segments[-1] = head + "{n}*"
    return parts.netloc.removeprefix("www.") + "/".join(segments) + ("?" + query if query else "")


# "3 days ago", "Updated 2 years ago", "2024-03-09": when a list shows a chapter's age next to its name.
_AGE = re.compile(r"\s*(updated\s*)?(\d+|an?|one)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|"
                  r"초|분|시간|일|주|개월|년|秒|分钟?|小时|天|周|个月|年|日|週間|か月|ヶ月)\s*(ago|전|前)\s*$", re.I)
_DATE_TAIL = re.compile(r"\s*(updated\s*)?\d{4}[-./]\d{1,2}[-./]\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$", re.I)


def clean_name(text: str) -> str:
    """A chapter name as a list shows it, without its age or date, a "Read" prefix or a repeated number."""
    name = re.sub(r"\s+", " ", text or "").strip()
    for _ in range(2):
        name = _AGE.sub("", name)
        name = _DATE_TAIL.sub("", name).strip()
    name = re.sub(r"^(read|start reading|continue reading|read now)\s+", "", name, flags=re.I)
    name = re.sub(r"^(\d+(?:\.\d+)?)\s+\1\b\s*", r"\1 ", name)  # "12 12 Title" (the number in two cells)
    name = re.sub(r"^(novel chapters|chapters?\s+list|latest chapters?)\s+", "", name, flags=re.I)
    return name.strip(" -|:·")


def _number(text: str, url: str) -> Optional[float]:
    text = clean_name(text)
    for source in (text, urlsplit(url).path):
        found = re.search(r"(?:(?<![a-z])(?:chapter|chap|ch|episode|ep)(?![a-z])|第|(?<![a-z])no\.?|#)\s*[-_.]?\s*(\d+(?:\.\d+)?)",
                          source, re.I)
        if found:
            return float(found.group(1))
    numbers = re.findall(r"\d+(?:\.\d+)?", text or "") or re.findall(r"\d+", urlsplit(url).path.split("/")[-1] or "")
    return float(numbers[0]) if numbers else None


def _bare(name: str) -> bool:
    """A name that says nothing but the number ("Chapter 12", "Ep. 3", "12")."""
    return bool(re.fullmatch(r"\s*((chapter|chap|ch|episode|ep)\.?\s*)?\d+(\.\d+)?\s*", name or "", re.I))


def _url_numbers(chapters: List[dict]) -> Optional[List[float]]:
    """Each chapter's number from its address, when the addresses differ in one number only
    (".../book_12.html", "?chapter=12"); None when they don't."""
    found = []
    for chapter in chapters:
        parts = urlsplit(chapter["url"])
        numbers = re.findall(r"\d+", parts.path.split("/")[-1] + "?" + parts.query)
        found.append(numbers)
    if len(found) < 2 or len({len(n) for n in found}) != 1 or not found[0]:
        return None
    columns = list(zip(*found))
    varying = [i for i, column in enumerate(columns) if len(set(column)) > 1]
    if len(varying) != 1:
        return None
    values = [float(n[varying[0]]) for n in found]
    return values if len(set(values)) == len(values) else None


def _reading_order(chapters: List[dict]) -> List[dict]:
    """Chapters sorted by their number (from the addresses, else the names) when every one has a
    different number; a list that runs newest first is turned round; otherwise left as it is."""
    numbers = _url_numbers(chapters)
    if numbers is None:
        numbers = [_number(c["name"], c["url"]) for c in chapters]
        known = [n for n in numbers if n is not None]
        if not (len(known) == len(chapters) and len(set(known)) == len(known)):
            if len(known) >= max(2, len(chapters) * 0.6) and known[0] > known[-1]:
                return list(reversed(chapters))
            return chapters
    return [c for _n, c in sorted(zip(numbers, chapters), key=lambda pair: pair[0])]


def _links(soup, base: str) -> List[Tuple[str, str, object]]:
    site = site_of(urlsplit(base).hostname or "")
    out = []
    for anchor in soup.find_all("a", href=True):
        href = clean_url(anchor["href"], base)
        if not href or site_of(urlsplit(href).hostname or "") != site:
            continue
        if re.search(r"\.(jpe?g|png|gif|webp|zip|pdf|epub|css|js)(\?|$)", href, re.I):
            continue
        out.append((href, text_of(anchor) or anchor.get("title", ""), anchor))
    return out


def find_chapters(soup, base: str) -> List[dict]:
    """The page's chapter links, in reading order: [{url, name}]."""
    links = _links(soup, base)
    if not links:
        return []
    book_path = urlsplit(base).path.rstrip("/")
    groups: Dict[str, List[Tuple[str, str, object]]] = {}
    for link in links:
        if link[0].rstrip("/") == base.rstrip("/"):
            continue
        groups.setdefault(shape(link[0]), []).append(link)
    best, best_score = None, 0.0
    for key, members in groups.items():
        unique = list(dict.fromkeys(m[0] for m in members))
        if len(unique) < 2 and not (len(unique) == 1 and CHAPTER_WORDS.search(members[0][1] or "")):
            continue
        texts = [m[1] for m in members]
        worded = sum(bool(CHAPTER_WORDS.search(t or "")) for t in texts) / len(texts)
        numbered = sum(_number(m[1], m[0]) is not None for m in members) / len(members)
        under_book = sum(urlsplit(m[0]).path.startswith(book_path + "/") for m in members) / len(members) if book_path else 0
        in_bad = sum(bool(BAD_BLOCKS.search(" ".join(
            (p.get("class") and " ".join(p.get("class"))) or p.get("id") or ""
            for p in m[2].parents if getattr(p, "get", None)))) for m in members[:20]) / min(len(members), 20)
        score = len(unique) ** 0.5 * (1 + 3 * worded + numbered + 2 * under_book) * (1 - 0.6 * in_bad)
        if "{n}" not in key:
            score *= 0.3  # links without numbers are rarely a chapter list
        if score > best_score:
            best, best_score = members, score
    if not best:
        return []
    seen, chapters = {}, []
    for href, text, _anchor in best:
        text = clean_name(text)
        if href in seen:
            # The same chapter linked twice ("Read first" and "Chapter 1"): keep the chapter-like name.
            chapter = seen[href]
            if text and CHAPTER_WORDS.search(text) and not CHAPTER_WORDS.search(chapter["name"]):
                chapter["name"] = text
            continue
        seen[href] = chapter = {"url": href, "name": text or f"Chapter {len(chapters) + 1}"}
        chapters.append(chapter)
    # Lists often lead with "Read first" or the newest chapters, or run newest first.
    return _reading_order(chapters)


def _list_pages(soup, base: str, seen: set) -> List[str]:
    """Further pages of a chapter list (?page=2, /page/2, rel=next), not yet read."""
    out = []
    base_parts = urlsplit(base)
    # The book's slug ("my-book" of /novel/my-book.html): list pages elsewhere on the site name it
    # in their query (/e/extend/fy.php?page=2&wjm=my-book).
    slug = re.sub(r"\.\w+$", "", base_parts.path.rstrip("/").split("/")[-1])
    for anchor in soup.find_all(["a", "link"], href=True):
        href = clean_url(anchor["href"], base)
        if not href or href in seen:
            continue
        parts = urlsplit(href)
        if parts.netloc != base_parts.netloc:
            continue
        rel = " ".join(anchor.get("rel") or [])
        same_book = parts.path.rstrip("/").startswith(re.sub(r"\.\w+$", "", base_parts.path.rstrip("/").split("/page/")[0])) \
            or (len(slug) > 3 and slug in parts.query)
        paged = re.search(r"([?&](page|p|pg|pageNo|epage)=\d+)|(/page/\d+/?$)|(_\d+/?$)", href, re.I)
        if same_book and paged or ("next" in rel and same_book):
            out.append(href)
    return out


# -- chapter text ----------------------------------------------------------------------------------

def _score(node) -> float:
    text = node.get_text(" ", strip=True)
    if len(text) < 80:
        return 0.0
    link_text = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
    density = link_text / max(1, len(text))
    paragraphs = len(node.find_all("p", recursive=False)) + len(node.find_all("br", recursive=False)) * 0.5
    hint = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
    score = len(text) * (1 - density) ** 2 + paragraphs * 40
    if GOOD_BLOCKS.search(hint):
        score *= 1.5
    if BAD_BLOCKS.search(hint):
        score *= 0.2
    # Text written straight into the block (sites that use <br> instead of <p>) counts most.
    direct = sum(len(str(s).strip()) for s in node.find_all(string=True, recursive=False))
    return score + direct * 0.5


def extract_content(html: str, url: str, title: str = "") -> Tuple[str, str]:
    """(content HTML, plain text) of the chapter's main text block."""
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "iframe", "form", "button", "nav", "footer", "header",
                     "aside", "svg", "select", "input", "textarea", "ins"]):
        tag.decompose()
    for node in soup.find_all(True):
        if node.attrs is None:
            continue
        hint = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
        style = (node.get("style") or "").replace(" ", "").lower()
        if (BAD_BLOCKS.search(hint) and not GOOD_BLOCKS.search(hint)) or "display:none" in style:
            node.decompose()
    candidates = [node for node in soup.find_all(["div", "article", "section", "td", "main", "pre"])]
    best = max(candidates, key=_score, default=None)
    if best is None or _score(best) == 0:
        best = soup.body or soup
    # Navigation inside the text block: "Next chapter", "Table of contents" and the like.
    for anchor in best.find_all("a"):
        words = text_of(anchor)
        if NEXT_WORDS.match(words) or re.search(r"(previous|prev|上一章|이전|前へ|index|table of contents|目录|목록)",
                                                 words, re.I):
            anchor.decompose()
    for image in best.find_all("img"):
        source = image.get("data-src") or image.get("data-original") or image.get("data-lazy-src") or image.get("src")
        source = clean_url(source, url) if source else ""
        for attribute in list(image.attrs):
            del image[attribute]
        if source:
            image["src"] = source
        else:
            image.decompose()
    for node in best.find_all(True):
        for attribute in [a for a in list(node.attrs) if a not in ("src", "href", "alt")]:
            del node[attribute]
    heading = best.find(["h1", "h2", "h3"])
    if heading is not None and title and _words(text_of(heading)) == _words(title):
        heading.decompose()
    text = _plain(best)
    return "".join(str(child) for child in best.children).strip(), text


def _words(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").lower())


def _plain(node) -> str:
    clone = soup_of(str(node))
    for br in clone.find_all("br"):
        br.replace_with("\n")
    for block in clone.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "tr", "hr"]):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = (re.sub(r"\s+", " ", line).strip() for line in clone.get_text().split("\n"))
    return "\n".join(line for line in lines if line)


def next_link(soup, base: str, words=NEXT_WORDS) -> str:
    link = soup.find(["a", "link"], rel=lambda r: r and "next" in (r if isinstance(r, list) else [r]), href=True)
    if link is not None:
        href = clean_url(link["href"], base)
        if href:
            return href
    for anchor in soup.find_all("a", href=True):
        if words.search(text_of(anchor) or anchor.get("title", "") or anchor.get("aria-label", "")):
            href = clean_url(anchor["href"], base)
            if href and href.rstrip("/") != base.rstrip("/"):
                return href
    return ""


# -- the engine ------------------------------------------------------------------------------------

class GenericEngine:
    label = LABEL
    MAX_LIST_PAGES = 150
    MAX_CHAIN = 400
    MAX_CHAPTER_PAGES = 15

    def __init__(self, fetch: Callable = None, log: Callable[[str], None] = lambda _m: None,
                 render: Optional[Callable[[str], Optional[str]]] = None):
        self.fetch = fetch
        self.log = log
        self.render = render

    @staticmethod
    def knows(url: str) -> bool:
        return url.startswith(("http://", "https://"))

    def _page(self, url: str, referer: str = "") -> Tuple[str, str]:
        result = self.fetch(url, referer=referer) if self.fetch else None
        if not result or result["status"] >= 400:
            raise RuntimeError(f"could not load {url} ({result['status'] if result else 'no answer'})")
        return decode(result), result.get("url") or url

    def parse_book(self, url: str) -> Optional[dict]:
        html, final = self._page(url)
        soup = soup_of(html)
        chapters = find_chapters(soup, final)
        if len(chapters) < 2 and self.render is not None:
            rendered = self.render(final)
            if rendered:
                rendered_soup = soup_of(rendered)
                found = find_chapters(rendered_soup, final)
                if len(found) > len(chapters):
                    soup, chapters, html = rendered_soup, found, rendered
        meta = extract_metadata(html, final, soup)
        warnings = []
        if chapters:
            chapters = self._more_list_pages(soup, final, chapters, warnings)
        else:
            # No list: the page may be a chapter itself; follow its "next" links.
            follow = next_link(soup, final)
            if follow:
                chapters = self._chain(final, soup, warnings)
        if not chapters:
            return None
        listed = []
        for index, chapter in enumerate(chapters):
            listed.append({"url": chapter["url"], "name": chapter["name"], "number": index + 1,
                           "bookUrl": final, "isVIP": False, "isPaid": False})
        return {
            "engine": "generic",
            "ruleName": "Generic (no site rules)",
            "bookUrl": final,
            "bookname": meta["title"] or urlsplit(final).hostname,
            "author": meta["author"],
            "introduction": meta["description"],
            "introductionHTML": "",
            "coverUrl": meta["cover"] or None,
            "tags": meta["tags"],
            "status": meta["status"],
            "chapterCount": len(listed),
            "chapters": listed,
            "warnings": warnings,
        }

    def _more_list_pages(self, soup, base: str, chapters: List[dict], warnings: List[str]) -> List[dict]:
        seen_pages = {base}
        known = {c["url"] for c in chapters}
        queue = _list_pages(soup, base, seen_pages)
        order_reversed = False
        pages = 0
        while queue and pages < self.MAX_LIST_PAGES:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            pages += 1
            try:
                html, final = self._page(page_url, referer=base)
            except Exception:
                continue
            page_soup = soup_of(html)
            found = find_chapters(page_soup, final)
            # A chapter seen before under a bare name ("Chapter 12" in a "latest" box) takes the list's fuller one.
            by_url = {c["url"]: c for c in chapters}
            for chapter in found:
                earlier = by_url.get(chapter["url"])
                if earlier is not None and _bare(earlier["name"]) and not _bare(chapter["name"]):
                    earlier["name"] = chapter["name"]
            more = [c for c in found if c["url"] not in known]
            if not more:
                continue
            known.update(c["url"] for c in more)
            chapters.extend(more)
            queue.extend(u for u in _list_pages(page_soup, final, seen_pages) if u not in queue)
        if pages >= self.MAX_LIST_PAGES:
            warnings.append(f"The chapter list goes on past {self.MAX_LIST_PAGES} pages; read the first ones.")
        return _reading_order(chapters) if len(chapters) > 2 else chapters

    def _chain(self, start: str, soup, warnings: List[str]) -> List[dict]:
        chapters, seen = [], set()
        url, page_soup = start, soup
        while url and url not in seen and len(chapters) < self.MAX_CHAIN:
            seen.add(url)
            heading = page_soup.find(["h1", "h2"])
            chapters.append({"url": url, "name": text_of(heading) or f"Chapter {len(chapters) + 1}"})
            following = next_link(page_soup, url)
            if not following or following in seen:
                break
            try:
                html, final = self._page(following, referer=url)
            except Exception:
                break
            url, page_soup = final, soup_of(html)
        if len(chapters) >= self.MAX_CHAIN:
            warnings.append(f"Followed {self.MAX_CHAIN} chapters through their 'next' links; stopped there.")
        return chapters

    def parse_chapter(self, chapter_info: dict, book: dict) -> Optional[dict]:
        url = chapter_info.get("url", "")
        name = chapter_info.get("fullName") or chapter_info.get("name", "")
        html, final = self._page(url, referer=chapter_info.get("bookUrl") or book.get("bookUrl") or "")
        content, text = extract_content(html, final, name)
        # A chapter split over several pages ("下一页", "Next page"): join them.
        seen = {final}
        soup = soup_of(html)
        for _ in range(self.MAX_CHAPTER_PAGES):
            following = next_link(soup, final, NEXT_PAGE_WORDS)
            if not following or following in seen or shape(following) != shape(final):
                break
            seen.add(following)
            try:
                page_html, final = self._page(following, referer=url)
            except Exception:
                break
            more, more_text = extract_content(page_html, final, name)
            content += "\n" + more
            text += "\n" + more_text
            soup = soup_of(page_html)
        if len(text) < 20 and "<img" not in content:
            raise ValueError("no chapter text found on the page")
        return {"chapterName": name, "contentHtml": content, "contentText": text, "imageCount": 0, "images": []}
