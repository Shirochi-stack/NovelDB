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
from html import escape, unescape
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

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

_MOJIBAKE = re.compile("[\u00c2\u00c3\u00e2][\u0080-\u00bf\u20ac\u201a-\u201e\u2018-\u201d\u2122\u0153\u0161]")
_WESTERN = ("iso-8859-1", "latin-1", "latin1", "windows-1252", "cp1252", "us-ascii", "ascii")


def repair(text: str) -> str:
    """UTF-8 text that was read as Latin-1 ("â€™" for "’", "Â " for a no-break space), put right."""
    if not text or not _MOJIBAKE.search(text):
        return text
    for western in ("cp1252", "latin-1"):
        try:
            return text.encode(western).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def decode(result: dict) -> str:
    """The page's text, in the charset its headers or its meta tag declare (GBK sites included). A page
    that declares a Western charset, or none, but is valid UTF-8 is read as UTF-8 (servers often say
    ISO-8859-1 for UTF-8 pages); text that arrived garbled that way is repaired."""
    content = result.get("content")
    if content is None:
        return repair(result.get("text") or "")
    headers = str(result.get("headers") or "")
    meta = re.search(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", content[:4096], re.I)
    match = meta or re.search(r"charset=([\w-]+)", headers, re.I)
    charset = match.group(1) if match else "utf-8"
    if isinstance(charset, bytes):
        charset = charset.decode("ascii", "ignore")
    charset = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030"}.get(charset.lower(), charset)
    if charset.lower() in _WESTERN or charset.lower() in ("utf-8", "utf8"):
        try:
            return repair(content.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    try:
        return repair(content.decode(charset, "replace"))
    except LookupError:
        return repair(content.decode("utf-8", "replace"))


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
    # The last tag of a name wins: a page whose script adds its own (Inertia, React Helmet) keeps the
    # site's default first ("Bookkor - Web novels", then the book's title).
    tags = soup.find_all("meta")
    for name in names:
        found = [tag for tag in tags if name in (tag.get("property"), tag.get("name")) and (tag.get("content") or "").strip()]
        if found:
            return found[-1]["content"].strip()
    return ""


def _strip_site_name(title: str, site_name: str, url: str) -> str:
    from noveldb.core.catalog.sites import clean_page_title

    title = clean_page_title(title, url) if title else ""
    if site_name and title.lower().endswith(site_name.lower()) and len(title) > len(site_name) + 3:
        title = title[: -len(site_name)].rstrip(" -|–—·:")
    # "Read X Online Free", "X Novel - Read X for free"
    title = re.sub(r"^(read\s+)", "", title, flags=re.I)
    title = re.sub(r"\s+(novel|light novel|manga|manhwa|webtoon)?\s*(online)?\s*(for\s+)?free.*$", "", title, flags=re.I)
    title = re.sub(r"[\s\-|:]+(all |the )?latest chapters?$", "", title, flags=re.I)  # "X Latest Chapter" (mangabats)
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
    return value[:40] if re.search(r"\d", value) or re.search(r"\bago\b", value, re.I) else ""


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


# Where a book page keeps its synopsis, the most specific first: Madara's ".summary__content" before its
# ".summary_content" stats box, which "[class*=summary]" matches too.
# A synopsis shown short with "more", its full text kept in the page: read before anything else.
_FULL_SYNOPSIS = ".fullcontent, [class*=fullcontent], [id*=fullcontent], [class*=full-summary], [class*=full-desc]"
_SYNOPSIS = ("[class*=detail-info-right-content], .summary__content, .description-summary, .manga-excerpt, [itemprop=description], .synopsis, "
             "[class*=synopsis], #synopsis, [class*=description], [id*=description], [class*=desc], "
             "[class*=summary], [class*=intro], #intro, .prose")  # .prose: Tailwind's text box (dragonholic)
# A book page's own list of tags or genres (not the site's menu of them).
_TAG_LISTS = ("[class*=tag-list], [class*=tags-list], [class*=genre-list], [class*=genres], .tags, .genre, [class*=manga-tags], "
              ".categories, [class*=book-tags], [class*=info--tags], [class*=project-tags], [class*=series-tags], "
              ".book-label, [class*=comic-chips], [class*=tagList], [class*=tag-box], [class*=tags-box]")
_COVER_IMAGES = ("[class*=cover] img, [class*=thumb] img, [class*=poster] img, img[class*=cover], "
                 "[class*=book] img")
_NOT_COVER = re.compile(r"(gravatar|googleusercontent\.com/a/|/unit/|\.svg($|\?)|(^|/)[^/?]*(logo|icon|flag|avatar|sprite|placeholder|default)[^/]*$)", re.I)
_SYNOPSIS_HEADING = re.compile(r"^\s*(synopsis|summary|description|story|about( this (novel|book|series))?|"
                               r"sinopsis|sinopse|résumé|resumen)\s*[:：]?\s*$", re.I)
# A site's notice where the synopsis goes, not a synopsis.
_ANNOUNCEMENT = re.compile(r"(📢|📌|🔔|new feature|announcement|join (us on|our) discord|we('ve| have) moved|"
                           r"our new (domain|address)|bookmark (our|this) (site|domain)|reading history for guests)", re.I)
# A synopsis box that says there is none, and the buttons that unfold one.
_NO_SYNOPSIS = re.compile(r"^(n/?a|none|null|nil|unknown|tbd|updating|coming soon|-+|"
                          r"(description|summary) (updating|not found)\W*|no (description|synopsis|summary)( yet| available)?\.?)$", re.I)
_MORE_LESS = re.compile(r"\[?(more|less|show more|show less|read more)\]?", re.I)
# Menu links that point at the tag pages but are no tag ("All Tags", "Browse", "More genres").
_NOT_TAG = re.compile(r"^(all|browse|more|see all|view all|show all)\b|^(tags?|genres?|categor(y|ies))\s*[:：]?$|"
                      r"^(\+\s*\d+|(ещё|еще|more|и ещё)\s*\d+|\d+\s*more)$", re.I)
_MENU = re.compile(r"(^|[-_ ])(menu|nav|navbar|dropdown|header|footer|sidebar)([-_ ]|$)", re.I)


def _site_chrome(node, tags) -> bool:
    """`node` sits in the site's own header, menu or footer (one of `tags`, or a menu-like class). The
    book's own header inside its <article> or <main> is not (novelphoenix's genres are in one)."""
    in_book = node.find_parent(["article", "main"]) is not None
    for depth, parent in enumerate(node.parents):
        if parent.name in ("article", "main", "body", "html"):
            return False  # (the body's classes name the theme's parts: WordPress's "header-style-1")
        # Class names only near the node: a far wrapper's "header"/"menu" classes say nothing of it.
        names = " ".join(parent.get("class") or []) if depth < 6 else ""
        if in_book and (parent.name == "header" or re.search(r"header", names, re.I)) and not re.search(r"menu|nav", names, re.I):
            continue
        if parent.name in tags or _MENU.search(names):
            return True
    return False


# The label a synopsis starts after, when an info box comes first ("Title: X Author: Y ... Description: ...",
# "作者：… 关于X：…", "… เรื่องย่อ …").
_SYNOPSIS_LABEL = re.compile(r"(description|synopsis|summary|sinopsis|sinopse|résumé|resumen|описание|简介|内容简介|"
                             r"あらすじ|줄거리|작품\s*소개|เรื่องย่อ|tóm tắt|giới thiệu|关于[^：:\n]{1,40}|關於[^：:\n]{1,40})\s*[:：]?\s+",
                             re.I)
_INFO_LABELS = re.compile(r"(title|author|artist|status|genres?|type|chapters?|year|作者|类型|類型|字数|字數|状态|狀態|"
                          r"人气|更新|分类|分類|大小|时间|時間)\s*[:：]", re.I)
# Page text that is no synopsis at all.
_NOT_SYNOPSIS = re.compile(r"(\bit has [\d.,]+\s*[kmb]?\s*(monthly\s+)?views?\b|summary not found|description updating|"
                           r"^\s*(updating|coming soon|no summary)\b|^\s*welcome to\b|\bwelcome to [\w.]+, your\b|"
                           r"log ?in to (keep|access|your)|sign in to (access|your)|masuk ke akun|"
                           r"^\s*(read|baca|leia|lee|lire|читать)\s.{0,200}\b(online|free|gratis|grátis|bahasa)\b|"
                           r"read your favou?rite|当サイト|本站|이 사이트|\b(our|this) site\b|liste des chapitres|"
                           r"\b(manga|manhwa|webtoon|novel|roman)\s+(oku|okuyun)\b|güncel oku|türkçe (oku|manga|manhwa)|"
                           r"\(\s*raw\b[^)]{0,20}\bfree\s*\)|最新章节.{0,40}(快速阅览|全文阅读|无弹窗))", re.I | re.S)
_CHAPTER_REF = re.compile(r"(\bchapter\s*\d+|\bch\.?\s*\d+|\d+\.?\s*bölüm|bölüm\s*\d+|ตอน(ที่)?\s*\d+|chương\s*\d+|"
                          r"capítulo\s*\d+|cap\.?\s*\d+|\d+\s*화|第\s*\d+\s*[话話章])", re.I)


# A label the synopsis starts after, cut wherever it stands: a CJK or Thai one, or any with a colon.
_STRONG_LABEL = re.compile(r"(内容简介|漫画简介|小说简介|作品简介|简介|關於[^：:\n]{1,40}|关于[^：:\n]{1,40}|あらすじ|줄거리|작품\s*소개|"
                           r"เรื่องย่อ)\s*[:：]?\s*|\b(synopsis|summary|description|sinopsis|sinopse|résumé|resumen|"
                           r"описание|novel summary|story summary)\s*[:：]\s*", re.I)
# A "label: value" of an info run (one word, or a date and time), and Korean "총 55화".
_INFO_VALUE = re.compile(r"((title|author|artist|status|genres?|type|year|作者|类型|類型|字数|字數|状态|狀態|人气|人氣|更新|"
                         r"分类|分類|大小|时间|時間|작가|장르)\s*[:：]\s*[^\s]+(\s\d{1,2}:\d{2}(:\d{2})?)?|(총편수\s*)?총\s*\d+\s*화)\s*",
                         re.I)
# An introduction before the synopsis: "A brief description of the manhwa X:", "Novel Summary", "Synopsis".
_INTRO = re.compile(r"^\s*(an? (brief|short|quick) (description|summary|synopsis|introduction) of (the )?"
                    r"(manga|manhwa|manhua|novel|light novel|web ?novel|webtoon|comic|book|story|series)?[^:：.!?]{0,160}[:：]|"
                    r"(novel |story )?(synopsis|summary)\b\s*[:：]?)\s*", re.I)
# A sentence's end: what comes before a label is prose, so the label is inside the synopsis.
_SENTENCE_END = re.compile(r"[.!?。！？]\s|[.!?。！？]$")
def _prose_before(text: str) -> bool:
    """Prose comes before a label (so the label is inside the synopsis): two sentences, or one in a long
    stretch; a title ending in "？" is not."""
    ends = len(_SENTENCE_END.findall(text))
    return ends >= 2 or (ends == 1 and len(text) > 80)


_INVISIBLE = re.compile("[\u200b-\u200d\u2060-\u2064\ufeff]")
# A template sentence before the synopsis ("You're Reading "X" Novel at Site", "Read X for Free (English).").
_TEMPLATE_SENTENCE = re.compile(r"^\s*((you['’]?re reading|(read|baca|leia|lee)\b)[^.!?。]{0,200}?\b(novel|manga|manhwa|online|free|gratis)\b.{0,120}?[.!?。](?=\s|$))\s*", re.I)
# The page's keyword list after the synopsis ("…시작된다!!!웹툰,만화,코믹,무료,Free,W").
_KEYWORD_TAIL = re.compile(r"(웹툰|만화|코믹|webtoons?|manga|manhwa|comics?|小说|小說|漫画|漫畫)(\s*[,，、]\s*[^,，、\s]{1,20}){3,}\s*$", re.I)


def _clean_synopsis(text: str) -> str:
    """The synopsis in `text`: after its label when an info box comes first; "" for text that is no synopsis
    (a placeholder, a rank line, a template, a login prompt, a list of figures or chapters)."""
    text = _INVISIBLE.sub("", text or "").strip()  # zero-width marks inside words ("Imperial​ Sovereign")
    if not text:
        return ""
    for _ in range(2):
        text = _TEMPLATE_SENTENCE.sub("", text, count=1).strip()  # "You're Reading "X" Novel ...", "Read X for Free."
    head = text[:400]
    strong = _STRONG_LABEL.search(head)
    found = _SYNOPSIS_LABEL.search(head)
    if strong and not _prose_before(head[:strong.start()]):
        # "关于X：", "漫画简介：", "Synopsis:", "เรื่องย่อ" after a title or an info box (not inside the synopsis);
        # a label with nothing after it means there is no synopsis.
        text = text[strong.end():].strip()
        if not text:
            return ""
    elif found and found.start() > 0 and (len(_INFO_LABELS.findall(head[:found.start()])) >= 2
                                          or len(re.findall(r"\d", head[:found.start()])) >= 4):
        text = text[found.end():].strip()
    else:
        # An info run first ("类型：都市 字数：1787364字 ... 更新：2026/11/4 16:58:01", "총편수 총 55화"): what follows it.
        values = list(_INFO_VALUE.finditer(head[:300]))
        if len(values) >= 2 and not _prose_before(head[:values[0].start()]):
            text = text[values[-1].end():].strip()
            if not text:
                return ""  # only the info run ("作者：… 大小：1.11 MB 时间：2021-10-20")
    text = _INTRO.sub("", text, count=1).strip()
    text = _KEYWORD_TAIL.sub("", text).strip()
    if not text or _NOT_SYNOPSIS.search(text[:300]):
        return ""
    words = re.findall(r"[^\W\d_]{2,}", text[:600])
    numbers = re.findall(r"\d[\d.,:]*", text[:600])
    if len(numbers) >= 4 and len(numbers) * 2 >= len(words):
        return ""  # mostly figures: ratings, counts, dates ("One Piece 4.7 1197 29 ...")
    parts = [part.strip() for part in re.split(r"[,，、|/]", text[:400]) if part.strip()]
    if len(parts) >= 3 and all(len(part.split()) <= 3 and len(part) <= 30 for part in parts) and len(text) < 400:
        return ""  # a list of genres or keywords ("Reincarnation , Action , Long Strip , Delinquents")
    if len(_CHAPTER_REF.findall(text[:400])) >= 2 and len(text) < 400:
        return ""  # a list of chapters ("Naruto 700 Naruto 699", "39. Bölüm 38. Bölüm")
    return text


_COUNTED = re.compile(r"[(\[]\s*[\d,.]+\s*[)\]]\s*$")
_LINK_WORDS = re.compile(r"^(here|more|click|link|read|download|next|prev(ious)?|home|top|see|view|show)$", re.I)


def _clean_tags(tags: list, title: str) -> list:
    """Tags as a reader wants them: no "#", no counts, no links' words ("here"), no SEO tags naming the book
    ("<title> latest", "<title> mtl"), each once."""
    out, seen = [], set()
    name = re.sub(r"\s+", " ", title or "").strip().lower()
    for tag in tags:
        tag = re.sub(r"^[#,，、;:·•|\s]+|[,，、;:·•|\s]+$", "", str(tag or "")).strip()
        if not tag or _COUNTED.search(tag) or _LINK_WORDS.match(tag) or _NOT_TAG.match(tag) or _UI_WORDS.search(tag):
            continue
        if name and len(name) > 3 and name in tag.lower() and len(tag) > len(name):
            continue
        if tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out


def _prose(text: str) -> bool:
    """Text that reads as words (a synopsis), not a box of figures ("5 43 42", a rating and its votes)."""
    return len(re.findall(r"[^\W\d_]{2,}", text or "")) >= 4


# A meta description written from a template for every book ("<title> chapters for free, don't miss out the
# latest updates"), not a synopsis.
_TEMPLATE = re.compile(r"(chapters? (for )?free|read (it |the )?(online|for free|now|full)|don'?t miss|"
                       r"latest (chapters?|updates?|releases?)|free online|online for free|in high quality)", re.I)


def _template(description: str) -> bool:
    return len(description or "") < 260 and bool(_TEMPLATE.search(description or ""))


# What a site's own blurb talks about (its site, reading, the catalogue), unlike a book's synopsis.
_BLURB_WORDS = re.compile(r"\b(read(ing)?|online|free|website|site|platform|updated?|daily|latest|library|"
                          r"collection|thousands|hundreds|manga|manhwa|manhua|webtoons?|comics?|novels|upload|"
                          r"translat(ed|ion|ions)|community|discord|favou?rite)\b|サイト|漫画|小说|网站|웹툰|사이트", re.I)


def _site_wide(description: str, title: str, site: str = "") -> bool:
    """The site's blurb, the same on every page: it never names the book, and it talks of the site or of
    reading ("Read your favorite manga online!", "Upload your manga / novel on Niadd"). A synopsis that only
    happens not to name the book is kept (kaynscans')."""
    words = [w for w in re.findall(r"\w{4,}", (title or "").lower()) if w not in ("manga", "novel", "read", "online")]
    if not description or not words or len(description) >= 600:
        return False
    text = description.lower()
    if any(word in text for word in words):
        return False
    names = [n for n in re.findall(r"\w{3,}", (site or "").lower()) if n not in ("com", "net", "org", "www")]
    return any(name in text for name in names) or len(_BLURB_WORDS.findall(description)) >= 2


_STATS_WORDS = re.compile(r"(\brating\b|\baverage\b|\bout of\b|alternative|author\(s\)|artist\(s\)|genre\(s\)|"
                          r"\brelease\b|\bstatus\b|\bcomments?\b|bookmarked|read first|read last|"
                          r"التقييم|التصنيف|الحالة|평점|评分|評価)", re.I)


def _stats_box(text: str) -> bool:
    """A block of a book page's figures (rating, alternative names, genres, status, bookmarks), not a synopsis."""
    found = {match.group(1).lower() for match in _STATS_WORDS.finditer(text[:600])}
    return len(found) >= 3 or bool(re.search(r"\d(?:\.\d)?\s*/\s*(5|10)\b.{0,20}\bout of\b", text[:600], re.I))


def _tag_text(tag: str) -> str:
    """A tag as words: a link to a genre page (some sites' structured data give their genres as links)
    becomes the genre it names ("...?genre=summoned_hero" gives "Summoned Hero")."""
    tag = (tag or "").strip()
    if re.match(r"https?://", tag):
        parts = urlsplit(tag)
        named = re.search(r"(?:^|&)(?:genre|tag|tags|category)=([^&]+)", parts.query)
        tag = named.group(1) if named else parts.path.rstrip("/").split("/")[-1]
        tag = re.sub(r"[_\-+]+|%20", " ", tag).strip().title()
    return tag if 1 < len(tag) <= 40 else ""


def _in_comments(node) -> bool:
    """`node` is (in) a comment section ("comment-summary": its replies, Login to reply...)."""
    for at in [node] + list(node.parents)[:6]:
        if re.search(r"comment|disqus|reply|review", " ".join(getattr(at, "get", lambda *_: None)("class") or []) + " "
                     + str(getattr(at, "get", lambda *_: None)("id") or ""), re.I):
            return True
    return False


# A label the book's genres follow: "Genre:", "Géneros", "ジャンル", "장르", "类型：".
_GENRE_LABEL = re.compile(r"^\s*(genres?|g[eéê]neros?|tags?|categor(y|ies)|categorias|kategori(ler)?|türler?|"
                          r"жанры?|теги|ジャンル|タグ|类型|類型|分类|分類|标签|標籤|题材|題材|장르|태그|thể loại)\s*[:：]?\s*$", re.I)


def _labelled_tags(soup) -> list:
    """The genres next to their label: links in the label's row, or the text after it ("Action, Comedy")."""
    out = []
    for label in soup.find_all(string=_GENRE_LABEL, limit=6):
        node = label.parent
        if node is None or _site_chrome(node, ("header", "nav", "footer")):
            continue
        # The links after the label, up to the next label ("类型：仙侠 … 作者：X" stops before the author).
        links = []
        for element in node.next_elements:
            if isinstance(element, str):
                if links and re.match(r"^\s*\S{1,14}\s*[:：]\s*$", element):
                    break
                continue
            if element.name in ("tr", "li", "dt", "h1", "h2", "h3", "section", "article") and links:
                break
            if element.name == "a":
                links.append(text_of(element))
            if len(links) >= 30:
                break
        if len(links) > 20:
            links = []  # a menu of every genre, not the book's
        sibling = node.find_next_sibling()
        if not links and sibling is not None:
            links = [text_of(a) for a in sibling.find_all("a")] or re.split(r"[,，、/|·•]", text_of(sibling))
        if not links:
            following = node.next_sibling
            links = re.split(r"[,，、/|·•]", following) if isinstance(following, str) else []
        out.extend(word.strip() for word in links if 1 < len(word.strip()) <= 40)
        if out:
            break
    return out


_NOT_CHIP_LINK = re.compile(r"(chapter|/read|episode|viewer|login|signin|bookmark|/user|/author|#)", re.I)
_UI_WORDS = re.compile(r"(start reading|continue|latest|bookmark|follow|subscribe|share|report|download|"
                       r"menu_book|schedule|visibility|favorite|북마크|이어보기|읽기|첫화|구독|続きを読む|読む|收藏|阅读|閱讀)", re.I)


def _chip_tags(soup) -> list:
    """A row of chip buttons (rounded links or spans side by side, Tailwind's flex-wrap): its words."""
    for row in soup.select("[class*=flex-wrap]"):
        if _site_chrome(row, ("header", "nav", "footer")):
            continue
        chips = [child for child in row.find_all(["a", "span", "button"], recursive=False)
                 if "rounded" in " ".join(child.get("class") or [])
                 and not _NOT_CHIP_LINK.search(child.get("href") or "")]
        words = [text_of(chip) for chip in chips if not _UI_WORDS.search(text_of(chip))]
        if len(words) < len(chips):
            continue  # a row of the page's buttons ("Start Reading", "북마크"), not tags
        if 2 <= len(chips) <= 30 and all(1 < len(word) <= 30 and not re.search(r"\d{2,}", word) for word in words):
            return words
    return []


def _headed_synopsis(soup) -> list:
    """The block right after a "Synopsis" (or "Summary", "Description") heading: [] when there is none."""
    out = []
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "h6", "strong", "b"], limit=400):
        if _SYNOPSIS_HEADING.match(text_of(heading)) and not heading.find_parent(["nav", "footer"]):
            block = heading.find_next_sibling()
            if block is not None:
                out.append(block)
    return out


def _html_text(text: str) -> str:
    """A synopsis that came as HTML (in a page's structured data) as plain text, its paragraphs kept."""
    if not re.search(r"<(p|br|div|span|em|strong|i|b)\b[^>]*>", text or "", re.I):
        return text
    return _plain(soup_of(text))


def extract_metadata(html: str, url: str, soup=None) -> dict:
    """{title, author, cover, description, tags, status, published, updated, rating, ratings, likes, views,
    chapters} read from any book (or search result) page; a figure the page doesn't show is ""."""
    soup = soup or soup_of(html)
    book = {}
    books = []
    for item in _json_ld(soup):
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(k) in ("Book", "CreativeWork", "CreativeWorkSeries", "BookSeries", "ComicSeries", "Article",
                          "Product", "Novel", "WebPage") for k in kinds):
            books.append(item)
            score = sum(1 for key in ("author", "image", "genre", "description", "numberOfPages") if item.get(key))
            if score >= book.get("_score", -1):
                book = dict(item, _score=score)
    # What the chosen block lacks, from the page's other blocks of the same book (wuxiaworld's synopsis is in
    # its "Product" block, its author in the "Book" one).
    for item in books:
        if _ld_text(item.get("name")) == _ld_text(book.get("name")):
            for key in ("description", "genre", "keywords", "author", "image"):
                if not book.get(key) and item.get(key):
                    book[key] = item[key]
    site_name = _meta(soup, "og:site_name", "application-name")
    title = _ld_text(book.get("name") or book.get("headline")) or _meta(soup, "og:title", "twitter:title")
    if qdn_global_catalog(url):
        title = text_of(soup.find("h1")) or title
    if not title:
        h1 = soup.find("h1")
        title = text_of(h1) if h1 else text_of(soup.title)
    # The book's author: its structured data, the page's itemprop=author, then the meta tags, unless they
    # name the site itself (novelphoenix's <meta name="author"> is "Novel Phoenix").
    author = _ld_text(book.get("author") or book.get("creator"))
    if not author:
        node = soup.select_one("[itemprop=author]")
        author = (node.get("content") or text_of(node)) if node else ""
    if not author:
        author = _meta(soup, "book:author", "author", "og:novel:author")
        if author and site_name and author.strip().lower() == site_name.strip().lower():
            author = ""
    if not author:
        label = soup.find(string=re.compile(r"^\s*(author|writer|作者|저자|작가|著者|autor|auteur|автор)(\(s\)|s)?\s*[:：]?\s*$", re.I))
        node = label.find_parent() if label else None
        if node is not None:
            # "Author: <a>Name</a>" in one element, the name as bare text after the label ("<p>Author(s):</p>
            # Shirakawa Miabi"), or the label and the name side by side.
            inside = text_of(node).replace(str(label).strip(), "", 1).strip(" :：")
            following = node.next_sibling
            bare = following.strip() if isinstance(following, str) else ""
            sibling = node.find_next_sibling()
            author = inside or bare or (text_of(sibling) if sibling else "")
        if not author:
            link = soup.find("a", href=re.compile(r"/(author|authors|writer|artist)/", re.I))
            author = text_of(link) if link else ""
    if not author:
        found = soup.find(string=re.compile(r"(author|作者|저자|著者)\s*[:：]", re.I))
        if found:
            author = re.split(r"[:：]", str(found), 1)[-1].strip()[:80]
    cover = _ld_text(book.get("image")) or _meta(soup, "og:image", "twitter:image", "og:novel:image")
    if not cover:
        # The page's marked cover first, then images in a cover-like box; never an icon, a flag or a logo
        # (novelcool's language flags sit in a "book" box before its cover).
        for image in soup.select("img[itemprop=image]") + soup.select(_COVER_IMAGES):
            source = image.get("data-src") or image.get("data-original") or image.get("lazy_url") or image.get("src") or ""
            width = re.match(r"\s*(\d+)", str(image.get("width") or ""))
            if source and not _NOT_COVER.search(source) and not (width and int(width.group(1)) <= 60):
                cover = source
                break
    cover = clean_url(cover, url) if cover else ""
    description = _ld_text(book.get("description")) or _meta(soup, "og:description", "description", "twitter:description")
    if qdn_global_catalog(url):
        description = _meta(soup, "description") or description
    if _site_wide(description, title, site_name or urlsplit(url).netloc) or not _prose(description) or _template(description):
        description = ""  # the site's own blurb on every page ("Read your favorite manga online!..."), or figures
    blocks = soup.select(_FULL_SYNOPSIS) + soup.select(_SYNOPSIS) + _headed_synopsis(soup)
    # A synopsis box that says there is none ("N/A"): neither it nor a bigger box holding it (that would
    # be the page around it); other boxes still count (niadd's genres and "N/A" boxes sit beside its text).
    empty = [block for block in blocks if _NO_SYNOPSIS.match(_MORE_LESS.sub("", text_of(block)).strip())]
    blocks = [block for block in blocks
              if not any(block is none or none in block.descendants for none in empty)]
    if _ANNOUNCEMENT.search(description[:200]):
        description = ""
    for block in blocks:
        longer = text_of(block)
        if _ANNOUNCEMENT.search(longer[:200]) or _in_comments(block):
            continue  # the site's notice in the synopsis box ("📢 New Feature: Save Reading History..."), or comments
        if len(longer) > len(description) and not _stats_box(longer) and _prose(longer):
            description = longer
            break
    tags = []
    genre = book.get("genre") or book.get("keywords")
    if genre:
        tags.extend(_tag_text(t) for t in re.split(r"[,;|]", _ld_text(genre)) if _tag_text(t))
    # Genre links by their address, and the links of the page's tag or genre list wherever they point
    # (fanfox's /directory/action/); none from the site's own menus.
    # (A footer's or a menu's genre links list the site's genres, not the book's.)
    links = [link for link in soup.find_all("a", href=re.compile(r"[/-](genres?|tags?|categor(y|ies)|keywords?)[/-]|[?&][\w-]*(genres?|tags?|categor(y|ies))[\w-]*=", re.I))
             if not _site_chrome(link, ("footer",))][:40]
    boxed = []
    for box in soup.select(_TAG_LISTS):
        if not _site_chrome(box, ("header", "nav", "footer")):
            boxed.append(box)
    if any(box.find("a", href=True) for box in boxed):
        links = []  # the page's genre box is the book's: other genre links are a site-wide table (mangabats)
    for box in boxed:
        found = box.find_all("a", href=True)[:40]
        if len(found) >= 6 and sum(1 for link in found if _COUNTED.search(text_of(link))) * 2 >= len(found):
            continue  # a site-wide genre widget, each genre with its count ("Action (215)")
        links.extend(found)
        if not found:
            # Genres with no links: each in an element of its own (zongheng's spans, mhscans' chips), or
            # written out in one (webtoons' <h2 class="genre">Fantasy</h2>, "Action, Comedy").
            leaves = [text_of(item) for item in box.find_all(True) if not item.find(True)]
            if box.name in ("h1", "h2", "h3", "h4", "h5", "h6") and len(leaves) >= 2:
                continue  # a heading of several parts (linovelib's volume list: "(共3卷) 倒序 | 正序"), not tags
            words = leaves if len(leaves) >= 2 else re.split(r"[,/|·•]", text_of(box))
            tags.extend(word.strip() for word in words if 1 < len(word.strip()) <= 40 and not _NOT_TAG.match(word.strip()))
    for link in links:
        word = text_of(link)
        if 1 < len(word) <= 40 and not _NOT_TAG.match(word):
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
        "description": _clean_synopsis(re.sub(r"[ \t]+", " ", _html_text(description or "")).strip())[:4000],
        "tags": _clean_tags(tags or _labelled_tags(soup) or _chip_tags(soup), _strip_site_name(re.sub(r"\s+", " ", title or "").strip(), site_name, url))[:30],
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
                  r"mo|mos|yrs?|[smhdwy]|"
                  r"초|분|시간|일|주|개월|년|秒|分钟?|小时|天|周|个月|年|日|週間|か月|ヶ月)\s*(ago|전|前)\s*$", re.I)
_DATE_TAIL = re.compile(r"\s*(updated\s*)?\d{4}[-./]\d{1,2}[-./]\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$", re.I)


def clean_name(text: str) -> str:
    """A chapter name as a list shows it, without its age or date, a "Read" prefix or a repeated number."""
    name = re.sub(r"\s+", " ", text or "").strip()
    # Its views and comments after its age ("Chapter 1197 5 days ago 9.0K 4", rawkuma): the age goes below.
    name = re.sub(r"(\s\d+\s*[^\W\d]+\s+ago)(\s+[\d.,]+[kmb]?)+$", r"\1", name, flags=re.I)
    for _ in range(2):
        name = _AGE.sub("", name)
        name = _DATE_TAIL.sub("", name).strip()
    name = re.sub(r"^(read|start reading|continue reading|read now)\s+", "", name, flags=re.I)
    name = re.sub(r"^(\d+(?:\.\d+)?)\s+\1\b\s*", r"\1 ", name)  # "12 12 Title" (the number in two cells)
    name = name.replace("\u00a0", " ")  # "Chapter 2\u00a0- Title"
    name = re.sub(r"\s{2,}", " ", name)
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


def qdn_global_catalog(url: str) -> str:
    """The selected qdn Global book's catalog, including links pasted from a chapter."""
    parts = urlsplit(url)
    domain = "\u0077\u0065\u0062\u006e\u006f\u0076\u0065\u006c.com"
    host = (parts.hostname or "").lower()
    if host != domain and not host.endswith("." + domain):
        return ""
    match = re.fullmatch(r"/(book|comic)/(?:[^/]+_)?(\d+)(?:/(?:catalog|(?:[^/]+_)?\d+))?/?", parts.path)
    return f"https://www.{domain}/{match[1]}/{match[2]}/catalog" if match else ""


def _qdn_global_chapters(soup, base: str, catalog: str) -> List[dict]:
    chapters, seen = [], set()
    anchors = soup.select("ul.content-list a[href], div.volume-item ol a[href]") or soup.select("a[href]")
    for anchor in anchors:
        href = urljoin(base, anchor["href"])
        segments = urlsplit(href).path.strip("/").split("/")
        if len(segments) != 3 or qdn_global_catalog(href) != catalog:
            continue
        chapter_id = segments[-1].rsplit("_", 1)[-1]
        if not chapter_id.isdigit() or chapter_id in seen:
            continue
        seen.add(chapter_id)
        title = anchor.get("title") or text_of(anchor.select_one("strong") or anchor)
        chapters.append({"url": href, "name": clean_name(title) or f"Chapter {len(chapters) + 1}"})
    return chapters


def find_chapters(soup, base: str) -> List[dict]:
    """The page's chapter links, in reading order: [{url, name}]."""
    catalog = qdn_global_catalog(base)
    if catalog:
        return _qdn_global_chapters(soup, base, catalog)
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
    # Pages a script draws as buttons (no links): the page's own data names them, as a Laravel
    # paginator does ("next_page_url", "last_page_url": "...?page=2") in Inertia's data-page JSON.
    prefix = re.sub(r"\.\w+$", "", base_parts.path.rstrip("/").split("/page/")[0])
    for href in _data_page_urls(soup, base):
        parts = urlsplit(href)
        if parts.netloc == base_parts.netloc and href not in seen and href not in out \
                and parts.path.rstrip("/").startswith(prefix) and re.search(r"[?&](page|p|pg|pageNo)=\d+", href, re.I):
            out.append(href)
    return out


_PAGE_URL = re.compile(r'"(?:first|last|next|prev)_page_url"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _data_page_urls(soup, base: str) -> List[str]:
    """The list page addresses in a page's embedded data (scripts and data-page attributes)."""
    texts = [script.string or script.get_text() or "" for script in soup.find_all("script")]
    texts += [tag.get("data-page") or "" for tag in soup.find_all(attrs={"data-page": True})]
    out: List[str] = []
    for text in texts:
        if len(text) > 3_000_000 or "_page_url" not in text:
            continue
        for match in _PAGE_URL.finditer(text):
            try:
                url = clean_url(json.loads('"' + match.group(1) + '"'), base)
            except ValueError:
                continue
            if url and url not in out:
                out.append(url)
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


def scrambled_hangul(text: str) -> bool:
    """True when Korean text is random syllables (a site's anti-copy scrambling).

    Real Korean ends about 4 syllables in 10 with a final consonant; syllables drawn at random from
    all 11,172 end 27 in 28 with one. Needs 200 syllables, so short or non-Korean text never matches."""
    syllables = [ord(c) - 0xAC00 for c in text or "" if "가" <= c <= "힣"]
    if len(syllables) < 200:
        return False
    return sum(1 for s in syllables if s % 28) / len(syllables) > 0.8


def app_page(html: str) -> dict:
    """An Inertia app's page data ({component, props, version, ...}), or {}."""
    found = re.search(r'<script[^>]*\bdata-page="app"[^>]*>(.*?)</script>', html or "", re.S)
    raw = found.group(1) if found else None
    if raw is None:
        found = re.search(r'\sdata-page="(\{.*?\})"', html or "", re.S)
        raw = unescape(found.group(1)) if found else None
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def app_episode_rows(rows, final: str) -> List[dict]:
    """Chapters from an Inertia app's episode rows ([id, title, slug, date] or {title, slug}), in list order."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            title, slug = row[1], row[2]
        elif isinstance(row, dict):
            title, slug = row.get("title"), row.get("slug")
        else:
            continue
        if isinstance(slug, str) and slug:
            out.append({"url": urljoin(final.rstrip("/") + "/", quote(slug, safe="/-_.~")), "name": str(title or slug)})
    return out


def app_data_chapter(html: str) -> Optional[str]:
    """The chapter's own page built from an Inertia app's page data, or None.

    bookkor draws its chapter text scrambled (each syllable swapped for a random one) and its script
    shows the real text from the page data (props.episode.body.html)."""
    props = app_page(html).get("props") or {}
    episode = props.get("episode") or props.get("chapter") if isinstance(props, dict) else None
    body = episode.get("body") if isinstance(episode, dict) else None
    markup = body.get("html") if isinstance(body, dict) else None
    if not isinstance(markup, str) or len(markup) < 20:
        return None
    title = escape(str(episode.get("title") or ""))
    return f"<html><head><title>{title}</title></head><body><article>{markup}</article></body></html>"


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


# A chapter address (".../c147/", "/chapter-12", "/ch_3", "/episode/9"), for stepping up to the series page.
_CHAPTER_PATH = re.compile(r"/(c|ch|chap|chapter|episode|ep|vol|v)[-_.]?\d+(\.\d+)?(/|\.html?|$)", re.I)
_SKIP_IMAGES = re.compile(r"(logo|icon|avatar|banner|sprite|loading|spinner|blank|pixel|emoji|button|ads?[-_/]|"
                          r"advert|placeholder|thumb[-_]?nav|\.svg|\.gif$)", re.I)


def is_chapter_address(url: str) -> bool:
    return bool(_CHAPTER_PATH.search(urlsplit(url).path))


def parent_addresses(url: str) -> List[str]:
    """The addresses above a chapter's, nearest first: /manga/x/c147/2.html -> /manga/x/c147/, /manga/x/."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    out = []
    while segments:
        segments = segments[:-1]
        if not segments:
            break
        path = "/" + "/".join(segments) + "/"
        if not _CHAPTER_PATH.search(path):
            out.append(f"{parts.scheme}://{parts.netloc}{path}")
    return out[:2]


def _image_source(image):
    return (image.get("data-src") or image.get("data-original") or image.get("data-lazy-src")
            or image.get("data-url") or image.get("src") or "").strip()


_READER_PAGE_LIST = re.compile(r"(?:^|[-_])(?:reader|chapter|manga|comic|webtoon)[-_](?:pages|images)(?:$|[-_])", re.I)
_READER_IMAGE_FILE = re.compile(r"\.(?:avif|gif|jpe?g|png|webp)$", re.I)
_IMAGE_TRANSFORM = re.compile(r"scrambl|encrypt|decrypt|descrambl|drm|tiles?|transform|clip-path|filter:", re.I)


def listed_comic_pages(soup, base: str) -> List[str]:
    """A plainly listed comic chapter, with no site-specific downloader needed for its image URLs."""
    if chapter_pages(soup, base):
        return []
    for block in soup.find_all(["div", "main", "section", "article", "ul"]):
        if not any(_READER_PAGE_LIST.search(token) for token in [block.get("id") or "", *(block.get("class") or [])]):
            continue
        if block.find(["canvas", "iframe"]) or _IMAGE_TRANSFORM.search(str(block.attrs)):
            continue
        nodes = block.find_all("img")
        declared = str(block.get("data-page-count") or block.get("data-total-pages") or "")
        if declared.isdecimal() and int(declared) != len(nodes):
            continue
        if len(nodes) < 2 or any(_IMAGE_TRANSFORM.search(str(image.attrs)) for image in nodes):
            continue
        links = [clean_url(_image_source(image), base) for image in nodes]
        if all(link and _READER_IMAGE_FILE.search(urlsplit(link).path) and not _SKIP_IMAGES.search(link) for link in links):
            return links
    return []


def page_images(soup, base: str) -> List[str]:
    """A manga page's own images (not logos, icons or ads), in page order."""
    out = []
    for image in soup.find_all("img"):
        source = _image_source(image)
        if not source or source.startswith("data:") or _SKIP_IMAGES.search(source):
            continue
        hint = " ".join(image.get("class") or []) + " " + (image.get("id") or "") + " " + (image.get("alt") or "")
        if re.search(r"(logo|icon|avatar|ads?\b|banner)", hint, re.I):
            continue
        width = re.match(r"\d+", str(image.get("width") or ""))
        if width and int(width.group()) < 150:
            continue
        if any(BAD_BLOCKS.search(" ".join(p.get("class") or []) + " " + (p.get("id") or ""))
               and not GOOD_BLOCKS.search(" ".join(p.get("class") or []) + " " + (p.get("id") or ""))
               for p in image.parents if getattr(p, "get", None)):
            continue
        url = clean_url(source, base)
        if url and url not in out:
            out.append(url)
    embedded = _data_images(soup, base)
    return embedded if len(embedded) > len(out) else out


def _data_images(soup, base: str) -> List[str]:
    """A chapter's images listed in the page's own data (Next.js __NEXT_DATA__, initialChapter.images or
    .pages): mangak's pages show a few as <img> and load the rest from that list."""
    script = soup.find("script", id="__NEXT_DATA__")
    try:
        props = json.loads(script.string or script.get_text())["props"]["pageProps"] if script else {}
    except (ValueError, KeyError, TypeError):
        return []
    chapter = props.get("initialChapter") or props.get("chapter") or {}
    if not isinstance(chapter, dict):
        return []
    images = chapter.get("images") or [page.get("url") for page in chapter.get("pages") or [] if isinstance(page, dict)]
    out = []
    for image in images or []:
        url = clean_url(image, base) if isinstance(image, str) else ""
        if url and url not in out:
            out.append(url)
    return out


def chapter_pages(soup, base: str) -> List[str]:
    """The other pages of a chapter shown one image per page: its page picker's options, or links
    like ".../c147/2.html" under the chapter's address."""
    parts = urlsplit(base)
    folder = parts.path if parts.path.endswith("/") else parts.path.rsplit("/", 1)[0] + "/"
    found = []
    for option in soup.select("select option[value]"):
        url = clean_url(option.get("value") or "", base)
        if url and urlsplit(url).path.startswith(folder) and url not in found:
            found.append(url)
    if len(found) < 2:
        found = []
        for anchor in soup.find_all("a", href=True):
            url = clean_url(anchor["href"], base)
            path = urlsplit(url).path if url else ""
            if path.startswith(folder) and re.fullmatch(r"\d{1,3}(\.html?)?", path[len(folder):]) and url not in found:
                found.append(url)
        found.sort(key=lambda u: int(re.search(r"(\d+)(\.html?)?$", urlsplit(u).path).group(1)))
    return [u for u in found if u.rstrip("/") != base.rstrip("/")]


# Scrolls a page to its end and clicks "Load more" until it stops growing (lists that load as you scroll):
# sets window.__noveldbScrolled when done. Shared by the downloader's browser and the Catalog's.
SCROLL_TO_END = r"""(function () {
  if (window.__noveldbScrolling) return; window.__noveldbScrolling = true; window.__noveldbScrolled = false;
  var words = /^(load|show|view|see)\s+(more|all)|more chapters|all chapters|show all|view all|더보기|더 보기|加载更多|查看更多|全部章节|もっと見る/i;
  var rounds = 0, still = 0, last = 0;
  function step() {
    rounds++;
    Array.prototype.slice.call(document.querySelectorAll('button, a, [role=button], div[class*=more], span[class*=more]'))
      .filter(function (el) { var t = (el.innerText || '').trim(); return t.length < 40 && words.test(t) && el.offsetParent; })
      .slice(0, 2).forEach(function (el) { try { if (!el.getAttribute('href') || /^#|javascript/i.test(el.getAttribute('href'))) el.click(); } catch (e) {} });
    window.scrollTo(0, document.documentElement.scrollHeight);
    var lists = document.querySelectorAll('[class*=chapter] a, [id*=chapter] a, li a').length;
    var size = document.documentElement.scrollHeight + lists;
    still = size === last ? still + 1 : 0; last = size;
    if (still >= 3 || rounds >= 60) { window.__noveldbScrolled = true; return; }
    setTimeout(step, 700);
  }
  step();
})();"""
_ALL_CHAPTERS = re.compile(r"^\s*(view|see|show|read)?\s*(all|full|complete)\s*(chapters?|episodes?|list|chapter list|toc)\b|"
                           r"^\s*(chapter list|table of contents|contents|toc|index)\s*$|全部章节|章节目录|目录|전체\s*(목록|회차)|"
                           r"目次|すべての話|^\s*(oldest|ascending|sort by oldest|from the beginning)\s*$", re.I)


def full_list_links(soup, base: str) -> List[str]:
    """Links to a book's whole chapter list ("All chapters", "Table of contents", "Oldest first")."""
    out = []
    for anchor in soup.find_all("a", href=True):
        if _ALL_CHAPTERS.search(text_of(anchor) or anchor.get("title", "")):
            url = clean_url(anchor["href"], base)
            if url and url.rstrip("/") != base.rstrip("/") and site_of(urlsplit(url).hostname or "") == \
                    site_of(urlsplit(base).hostname or "") and url not in out:
                out.append(url)
    return out[:3]


def numbered_pages(pages: List[str]) -> List[str]:
    """When list pages differ only in a page number ("?page=2", "/page/3"), every page up to the
    highest one shown (pagination shows 1 2 3 ... 47): those not linked are filled in."""
    if not pages:
        return pages
    pattern = None
    numbers = []
    for url in pages:
        found = list(re.finditer(r"(?<=[?&/_=-])(\d{1,4})(?=(\.html?)?(/|&|$))", url))
        if not found:
            return pages
        last = found[-1]
        shape_now = url[: last.start()] + "{n}" + url[last.end():]
        if pattern is None:
            pattern = shape_now
        elif shape_now != pattern:
            return pages
        numbers.append(int(last.group(1)))
    top = max(numbers)
    if top > 400 or top <= len(set(numbers)):
        return pages
    low = min(numbers)
    return [pattern.replace("{n}", str(n)) for n in range(low, top + 1)]


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


# -- DM5-platform manga sites (fanfox, mangahere) --------------------------------------------------
# A book page lists its chapters in two tabs, newest first, under two address shapes (/c1046.5/ and
# /v01/c001/). A chapter page draws no pages: its script asks chapterfun.ashx for them, two at a time,
# and the page's key and every answer are packed JavaScript (Dean Edwards' p,a,c,k,e,d). The pages'
# server wants the site as the Referer. (The mobile site's chapters say "licensed and not available".)

_PACKED = re.compile(r"\}\s*\(\s*'((?:[^'\\]|\\.)*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'((?:[^'\\]|\\.)*)'\s*\.split\(\s*'\|'\s*\)")
_RADIX = "0123456789abcdefghijklmnopqrstuvwxyz"


def unpack_js(source: str) -> List[str]:
    """The code of each packed script in `source` (`eval(function(p,a,c,k,e,d){...}('...',62,9,'...'.split('|'),0,{}))`)."""
    out = []
    for found in _PACKED.finditer(source or ""):
        payload, radix, count = found.group(1), int(found.group(2)), int(found.group(3))
        words = found.group(4).split("|")
        if not 2 <= radix <= 62:
            continue

        def name(n: int) -> str:
            return ("" if n < radix else name(n // radix)) + (chr(n % radix + 29) if n % radix > 35 else _RADIX[n % radix])

        table = {}
        for index in range(count):
            table[name(index)] = words[index] if index < len(words) and words[index] else name(index)
        payload = re.sub(r"\\(.)", r"\1", payload)
        out.append(re.sub(r"\b\w+\b", lambda m: table.get(m.group(0), m.group(0)), payload))
    return out


_DM5_MOBILE = {"m.fanfox.net": "fanfox.net", "m.mangahere.cc": "www.mangahere.cc"}
_DM5_DESKTOP = {"fanfox.net", "www.fanfox.net", "mangahere.cc", "www.mangahere.cc"}


_ASURA_HOSTS = {"asuracomic.net", "www.asuracomic.net", "asurascans.com", "www.asurascans.com"}


def asura_address(url: str) -> str:
    """An Asura comic's address as asurascans.com has it: /comics/<name>[/chapter/<n>]. Its old addresses
    (asuracomic.net/series/<name>) now lead to the home page, read as a book called "Series"; the name's
    suffix may be an old one (the site finds the comic by its name). Other addresses as they are."""
    parts = urlsplit(url or "")
    if (parts.hostname or "").lower() not in _ASURA_HOSTS:
        return url
    found = re.match(r"^/(?:series|comics)/([^/]+)(/chapter/[^/]+)?/?$", parts.path)
    if not found:
        return url
    return "https://asurascans.com/comics/" + found.group(1) + (found.group(2) or "")


def asura_pages(html: str) -> List[str]:
    """An Asura comic chapter's pictures, in order, from its page's data ({"pages": [{"url": ...}]}; the page
    draws them with a script). [] for any other page."""
    import html as markup

    text = markup.unescape(html or "")
    pages = re.findall(r'\{"url":\[0,"(https://[^"]+/asura-images/chapters/[^"]+)"\]', text)
    return list(dict.fromkeys(pages))


def dm5_desktop(url: str) -> str:
    """A DM5-platform mobile address as the desktop one (whose chapters have pages), always https (an http one's
    chapter page held no pages: a book opened from an http link read none); other addresses as they are."""
    parts = urlsplit(url or "")
    name = (parts.hostname or "").lower()
    host = _DM5_MOBILE.get(name) or (name if name in _DM5_DESKTOP else "")
    if not host:
        return url
    return urlunsplit(("https", host, re.sub(r"^/roll_manga/", "/manga/", parts.path), parts.query, ""))


def dm5_chapters(soup, base: str) -> List[dict]:
    """A DM5-platform book page's chapters, oldest first ([] for any other page)."""
    anchors = soup.select("#chapterlist ul.detail-main-list li a[href]")
    seen, out = set(), []
    for anchor in reversed(anchors):
        url = clean_url(anchor["href"], base)
        if not url or url in seen or not re.search(r"/c\d+(\.\d+)?/", url):
            continue
        seen.add(url)
        title = anchor.select_one(".title3")
        out.append({"url": url, "name": clean_name(text_of(title) if title else anchor.get("title", ""))})

    def number(chapter: dict) -> float:
        found = re.search(r"/c(\d+(?:\.\d+)?)/", chapter["url"])
        return float(found.group(1)) if found else float("inf")

    return sorted(out, key=number)


def dm5_metadata(soup) -> dict:
    """The title and whole synopsis a DM5-platform book page shows ({} for any other page)."""
    title = soup.select_one(".detail-info-right-title-font")
    if title is None:
        return {}
    synopsis = soup.select_one(".fullcontent") or soup.select_one(".detail-info-right-content")
    return {"title": text_of(title), "description": _clean_synopsis(text_of(synopsis)) if synopsis else ""}


def dm5_chapter(html: str) -> Optional[dict]:
    """{cid, count, key, images} of a DM5-platform chapter page, or None for any other page. `images` is
    the page's own list when it has one (newImgs), else [] (they come from chapterfun.ashx)."""
    cid = re.search(r"\bvar\s+chapterid\s*=\s*(\d+)", html or "")
    count = re.search(r"\bimagecount\s*=\s*(\d+)", html or "")
    if not cid or not count:
        return None
    key, images = "", []
    for code in unpack_js(html):
        code = code.replace("\\", "")
        found = re.search(r"guidkey\s*=\s*([^;]+);", code)
        if found and not key:
            key = "".join(re.findall(r"'([^']*)'", found.group(1)))
        listed = re.search(r"newImgs\s*=\s*\[([^\]]*)\]", code)
        if listed and not images:
            images = re.findall(r"['\"]([^'\"]+)['\"]", listed.group(1))
    return {"cid": cid.group(1), "count": int(count.group(1)), "key": key, "images": images}


def image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) of a JPEG, PNG, GIF or WebP from its bytes, or None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        if data[12:16] == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if data[12:16] == b"VP8 ":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if data[12:16] == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if data[:2] == b"\xff\xd8":
        at = 2
        while at + 9 < len(data):
            if data[at] != 0xFF:
                at += 1
                continue
            marker = data[at + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[at + 7:at + 9], "big"), int.from_bytes(data[at + 5:at + 7], "big")
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                at += 2 if marker != 0xFF else 1
                continue
            at += 2 + int.from_bytes(data[at + 2:at + 4], "big")
    return None


# The site's advertisement that ends every chapter (HaruNeko leaves it out the same way).
DM5_AD_SIZE = (1000, 563)


def dm5_images(answer: str, base: str) -> List[str]:
    """The page addresses in one chapterfun.ashx answer (usually two pages, from the one asked for)."""
    for code in unpack_js(answer) or [answer or ""]:
        values = re.search(r"pvalue\s*=\s*\[([^\]]*)\]", code)
        if not values:
            continue
        pix = re.search(r"\bpix\s*=\s*\"([^\"]*)\"", code)
        first = re.search(r"i\s*==\s*0\s*\)\s*\{\s*pvalue\[i\]\s*=\s*\"([^\"]*)\"\s*\+", code)
        out = []
        for index, value in enumerate(re.findall(r"\"([^\"]*)\"", values.group(1))):
            prefix = first.group(1) if first and index == 0 else (pix.group(1) if pix else "")
            out.append(urljoin(base, prefix + value))
        return out
    return []


# -- webtoons.com ---------------------------------------------------------------------------------
# A series page shows ten episodes a page; the mobile site's API lists them all. An episode's images are
# lazy (`data-url`) on the desktop page and a script list (`imageList = [{url: ...}]`) on the mobile one, and
# their server wants the site as the Referer. A phone's browser is sent to m.webtoons.com, whose search
# results are drawn by a script.

def webtoons_title(url: str) -> Optional[Tuple[str, str]]:
    """("webtoon" or "canvas", title number) of a webtoons.com series or episode address, else None."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    found = re.search(r"(?:^|&)title_no=(\d+)", parts.query)
    if not found or not (host == "webtoons.com" or host.endswith(".webtoons.com")):
        return None
    return ("canvas" if re.search(r"/(canvas|challenge)/", parts.path) else "webtoon"), found.group(1)


def webtoons_desktop(url: str) -> str:
    """A webtoons.com address on www (the mobile site's as the desktop one), without the redirect marker."""
    parts = urlsplit(url or "")
    if not webtoons_title(url):
        return url
    query = "&".join(q for q in parts.query.split("&") if q and not q.startswith("webtoon-platform-redirect="))
    return urlunsplit(("https", "www.webtoons.com", parts.path, query, ""))


def webtoons_series(url: str) -> str:
    """The series page of a webtoons.com episode address (`.../episode-slug/viewer?title_no=95&episode_no=1`)."""
    parts = urlsplit(webtoons_desktop(url))
    if not parts.path.endswith("/viewer"):
        return webtoons_desktop(url)
    kind_no = webtoons_title(url)
    path = parts.path[: -len("/viewer")].rsplit("/", 1)[0] + "/list"
    return urlunsplit(("https", parts.netloc, path, f"title_no={kind_no[1]}", ""))


def webtoons_episodes_api(url: str) -> str:
    kind, number = webtoons_title(url)
    return f"https://m.webtoons.com/api/v1/{kind}/{number}/episodes?pageSize=99999"


def webtoons_chapters(answer: str) -> List[dict]:
    """The episodes in the episodes API's answer, oldest first ([] when it lists none)."""
    try:
        rows = ((json.loads(answer or "{}").get("result") or {}).get("episodeList")) or []
    except (ValueError, AttributeError):
        return []
    rows = [r for r in rows if isinstance(r, dict) and r.get("viewerLink")]
    rows.sort(key=lambda r: int(r.get("episodeNo") or 0))
    return [{"url": webtoons_desktop(urljoin("https://www.webtoons.com/", r["viewerLink"])),
             "name": clean_name(unescape(str(r.get("episodeTitle") or f"Episode {r.get('episodeNo')}")))} for r in rows]


def webtoons_metadata(soup) -> dict:
    """Title, author, synopsis and genres of a webtoons.com series page (desktop or mobile)."""
    title = soup.select_one("h1.subj, h1.title, strong.subject")
    author = soup.select_one(".author_area, .author")
    synopsis = soup.select_one("p.summary, .summary")
    genres = [text_of(g) for g in soup.select(".info .genre, h2.genre, p.genre, .genre")]
    author_text = re.sub(r"\s*author info\s*$", "", text_of(author) if author else "", flags=re.I).strip()
    return {"title": text_of(title) if title else "", "author": re.sub(r"\s*,\s*", ", ", author_text),
            "description": _clean_synopsis(text_of(synopsis)) if synopsis else "",
            "tags": list(dict.fromkeys(g for g in genres if g))[:8]}


def webtoons_images(html: str, base: str) -> List[str]:
    """An episode's images in order: the desktop page's lazy `data-url`s, else the mobile page's script list."""
    soup = soup_of(html or "")
    images = [clean_url(img.get("data-url") or "", base) for img in soup.select("#_imageList img[data-url]")]
    if not images:
        listed = re.search(r"imageList\s*=\s*\[(.*?)\]\s*;", html or "", re.S)
        images = re.findall(r"url\s*:\s*\"([^\"]+)\"", listed.group(1)) if listed else []
    return list(dict.fromkeys(i for i in images if i))


# -- the engine ------------------------------------------------------------------------------------

class GenericEngine:
    label = LABEL
    MAX_LIST_PAGES = 150
    MAX_CHAIN = 400
    MAX_CHAPTER_PAGES = 15

    def __init__(self, fetch: Callable = None, log: Callable[[str], None] = lambda _m: None,
                 render: Optional[Callable[[str], Optional[str]]] = None, parallel: bool = False):
        self.fetch = fetch
        self.log = log
        self.render = render
        # The pages of a manga chapter at once (only where `fetch` may run on any thread: not the
        # downloader's, whose requests stay on its browser thread).
        self.parallel = parallel
        self.on_images: Callable[[List[str], int], None] = lambda _images, _total: None
        self._scrambling: set = set()  # hosts that send plain requests scrambled text (bookkor)

    @staticmethod
    def knows(url: str) -> bool:
        return url.startswith(("http://", "https://"))

    def _page(self, url: str, referer: str = "") -> Tuple[str, str]:
        result = self.fetch(url, referer=referer) if self.fetch else None
        if not result or result["status"] >= 400:
            raise RuntimeError(f"could not load {url} ({result['status'] if result else 'no answer'})")
        return decode(result), result.get("url") or url

    def _platform_chapters(self, html: str, final: str) -> List[dict]:
        """Every chapter of a MangaBuddy-platform page (mangak and its sister sites).

        Such a page holds its newest 50 chapters (initialManga in __NEXT_DATA__); the rest come from the
        site's API (its address in /api/site-config), titles/<id>/chapters. [] for any other page."""
        found = re.search(r'"initialManga"\s*:\s*\{\s*"id"\s*:\s*"([\w-]+)"', html or "")
        if not found:
            return []
        parts = urlsplit(final)
        origin = f"{parts.scheme}://{parts.netloc}"
        try:
            config, _ = self._page(origin + "/api/site-config", referer=final)
            api = re.search(r'"apiUrl"\s*:\s*"([^"]+)"', config)
            api = api.group(1) if api else (re.search(r'__API_URL__\s*=\s*"([^"]+)"', html) or [None, ""])[1]
            if not api:
                return []
            text, _ = self._page(f"{api.rstrip('/')}/titles/{found.group(1)}/chapters", referer=final)
            listed = (json.loads(text).get("data") or {}).get("chapters") or []
        except Exception:
            return []
        listed = [c for c in listed if isinstance(c, dict) and c.get("url")]
        listed.sort(key=lambda c: float(c.get("number") or 0))
        return [{"url": urljoin(origin, c["url"]), "name": c.get("name") or f"Chapter {c.get('number')}"} for c in listed]

    def _app_chapters(self, html: str, final: str) -> List[dict]:
        """Every chapter of an Inertia app's book page (bookkor), oldest first, or [].

        The page holds only its newest 10 episodes (props.episodes, out of episodesTotal); the site's script
        asks the same address for the rest as a partial reload of props.episodesAll."""
        data = app_page(html)
        props = data.get("props") if isinstance(data.get("props"), dict) else {}
        shown = props.get("episodes")
        if not isinstance(shown, list) or not shown:
            return []
        rows = shown
        try:
            total = int(props.get("episodesTotal") or 0)
        except (TypeError, ValueError):
            total = 0
        if total > len(shown) and data.get("component"):
            headers = {"X-Inertia": "true", "X-Inertia-Version": str(data.get("version") or ""),
                       "X-Inertia-Partial-Component": str(data["component"]),
                       "X-Inertia-Partial-Data": "episodesAll", "X-Requested-With": "XMLHttpRequest",
                       "Accept": "text/html, application/xhtml+xml"}
            try:
                result = self.fetch(final, referer=final, headers=headers)
                answer = json.loads(decode(result)) if result and result["status"] < 400 else {}
                full = ((answer.get("props") or {}).get("episodesAll") or {}).get("rows")
                if isinstance(full, list) and len(full) > len(shown):
                    rows = full
            except Exception:
                pass
        chapters = app_episode_rows(rows, final)
        return chapters[::-1] if str(props.get("episodeOrder") or "").lower() == "desc" else chapters

    def parse_book(self, url: str, *, on_chapters=None, active=lambda: True) -> Optional[dict]:
        url = qdn_global_catalog(url) or asura_address(webtoons_series(dm5_desktop(url)))
        html, final = self._page(url)
        soup = soup_of(html)
        chapters = find_chapters(soup, final)
        if is_chapter_address(final):
            # A chapter's link (a manga chapter's pages look like a chapter list): the series page above it.
            for parent in parent_addresses(final):
                try:
                    parent_html, parent_final = self._page(parent, referer=final)
                except Exception:
                    continue
                parent_soup = soup_of(parent_html)
                found = find_chapters(parent_soup, parent_final)
                if len(found) >= 2 and sum(is_chapter_address(c["url"]) for c in found) >= len(found) / 2:
                    self.log(f"Generic: {final} is a chapter; reading the series page {parent_final}.")
                    html, final, soup, chapters = parent_html, parent_final, parent_soup, found
                    break
        if len(chapters) < 2 and self.render is not None:
            rendered = self.render(final)
            if rendered:
                rendered_soup = soup_of(rendered)
                found = find_chapters(rendered_soup, final)
                if len(found) > len(chapters):
                    soup, chapters, html = rendered_soup, found, rendered
        meta = extract_metadata(html, final, soup)
        warnings = []
        expected = int(re.sub(r"\D", "", str(meta.get("chapters") or "")) or 0)
        if chapters and expected and len(chapters) < expected * 0.8:
            # The page shows only some of the chapters: its "All chapters" page, if it links one.
            for link in full_list_links(soup, final):
                try:
                    full_html, full_final = self._page(link, referer=final)
                except Exception:
                    continue
                full_soup = soup_of(full_html)
                found = find_chapters(full_soup, full_final)
                if len(found) > len(chapters):
                    self.log(f"Generic: reading the full chapter list at {full_final}.")
                    soup, chapters, final = full_soup, found, full_final
                    break
        episodes = []
        if webtoons_title(final):
            # Every episode from the episode list (the series pages show ten each).
            final = webtoons_desktop(final)
            try:
                episodes = webtoons_chapters(self._page(webtoons_episodes_api(final), referer=final)[0])
            except Exception:
                episodes = []
            if episodes:
                self.log(f"Generic: {len(episodes)} episodes from webtoons' episode list.")
                chapters = episodes
            meta.update({k: v for k, v in webtoons_metadata(soup).items() if v})
        published = []

        def entries(chapters):
            return [{"url": chapter["url"], "name": chapter["name"], "number": index + 1,
                     "bookUrl": final, "isVIP": False, "isPaid": False}
                    for index, chapter in enumerate(chapters)]

        def publish(chapters, *, numbered=True):
            ordered = _reading_order(chapters) if numbered else chapters
            if numbered:
                # Only expose the known beginning. A newest-first page or a gap cannot assign indices yet.
                end = 0
                for index, chapter in enumerate(ordered):
                    number = _number(chapter["name"], chapter["url"])
                    if index == 0:
                        if number not in (0, 1):
                            break
                        expected_number = number
                    if number is None or not expected_number - 1 < number <= expected_number:
                        break
                    if number == expected_number:
                        expected_number += 1
                    end += 1
                ordered = ordered[:end]
            if (len(ordered) > len(published)
                    and all(a["url"] == b["url"] for a, b in zip(published, ordered))):
                published[:] = ordered
                on_chapters(entries(published))

        if chapters and not episodes and not qdn_global_catalog(final):
            chapters = self._more_list_pages(soup, final, chapters, warnings, publish if on_chapters else None, active)
        listed = self._platform_chapters(html, final)
        if len(listed) > len(chapters):
            self.log(f"Generic: {len(listed)} chapters from the site's own chapter list.")
            chapters = listed
        listed = self._app_chapters(html, final)
        if len(listed) > len(chapters):
            self.log(f"Generic: {len(listed)} chapters from the site's page data.")
            chapters = listed
        listed = dm5_chapters(soup, final)
        if listed:
            chapters = listed  # both tabs, in reading order (the address groups split them)
            meta.update({k: v for k, v in dm5_metadata(soup).items() if v})
        if self.render is not None and (len(chapters) < 2 or (expected and len(chapters) < expected * 0.8)):
            # A list that loads as you scroll, or behind "Load more": the browser, scrolled to the end.
            try:
                rendered = self.render(final, scroll=True)
            except TypeError:
                rendered = None  # a renderer that can't scroll
            if rendered:
                found = find_chapters(soup_of(rendered), final)
                if len(found) > len(chapters):
                    self.log(f"Generic: {len(found)} chapters once the page was scrolled to its end.")
                    chapters = found
        elif not chapters:
            # No list: the page may be a chapter itself; follow its "next" links.
            follow = next_link(soup, final)
            if follow:
                chapters = self._chain(final, soup, warnings,
                                       (lambda found: publish(found, numbered=False)) if on_chapters else None, active)
        if not chapters:
            return None
        if published:
            # Keep the indices already handed to the reader, including when a later page gives a fuller title.
            by_url = {chapter["url"]: chapter for chapter in chapters}
            chapters = [by_url.pop(chapter["url"], chapter) for chapter in published] + list(by_url.values())
        listed = entries(chapters)
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

    def _more_list_pages(self, soup, base: str, chapters: List[dict], warnings: List[str],
                         publish=None, active=lambda: True) -> List[dict]:
        seen_pages = {base}
        known = {c["url"] for c in chapters}
        queue = numbered_pages(_list_pages(soup, base, seen_pages))
        if self.parallel and len(queue) > 2 and publish is None:
            return self._list_pages_at_once(base, queue, chapters, known)
        if publish is not None and queue and active():
            publish(chapters)
            first = _number(chapters[0]["name"], chapters[0]["url"])
            if first is not None and first > 1:
                queue.reverse()  # A newest-first list usually links its last (oldest) page.
        pages = 0
        while queue and pages < self.MAX_LIST_PAGES and active():
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
            known.update(c["url"] for c in more)
            chapters.extend(more)
            if publish is not None and more and active():
                publish(chapters)
            queue.extend(u for u in _list_pages(page_soup, final, seen_pages) if u not in queue)
        if pages >= self.MAX_LIST_PAGES:
            warnings.append(f"The chapter list goes on past {self.MAX_LIST_PAGES} pages; read the first ones.")
        return _reading_order(chapters) if len(chapters) > 2 else chapters

    def _list_pages_at_once(self, base: str, queue: List[str], chapters: List[dict], known: set) -> List[dict]:
        """Every numbered list page at once (8 at a time), kept in page order."""
        from concurrent.futures import ThreadPoolExecutor

        def read(page_url: str) -> List[dict]:
            try:
                html, final = self._page(page_url, referer=base)
            except Exception:
                return []
            return find_chapters(soup_of(html), final)

        with ThreadPoolExecutor(8, thread_name_prefix="generic-list") as pool:
            for found in pool.map(read, queue[: self.MAX_LIST_PAGES]):
                by_url = {c["url"]: c for c in chapters}
                for chapter in found:
                    earlier = by_url.get(chapter["url"])
                    if earlier is not None and _bare(earlier["name"]) and not _bare(chapter["name"]):
                        earlier["name"] = chapter["name"]
                more = [c for c in found if c["url"] not in known]
                known.update(c["url"] for c in more)
                chapters.extend(more)
        return _reading_order(chapters) if len(chapters) > 2 else chapters

    def _chain(self, start: str, soup, warnings: List[str], publish=None, active=lambda: True) -> List[dict]:
        chapters, seen = [], set()
        url, page_soup = start, soup
        while url and url not in seen and len(chapters) < self.MAX_CHAIN and active():
            seen.add(url)
            heading = page_soup.find(["h1", "h2"])
            chapters.append({"url": url, "name": text_of(heading) or f"Chapter {len(chapters) + 1}"})
            if publish is not None:
                publish(chapters)
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

    MAX_MANGA_PAGES = 250

    def _manga_chapter(self, html: str, final: str) -> Optional[str]:
        """A chapter of images: every image on the page, or one per page when the chapter is split over
        pages; None for a chapter of text."""
        soup = soup_of(html)
        text = len(re.sub(r"\s+", "", _plain(soup.body or soup)))
        images = page_images(soup, final)
        pages = chapter_pages(soup, final)
        if not images or (not pages and text > 3000 and len(images) < 3):
            return None  # a chapter of text (a few pictures in it stay with the text)
        if pages and len(images) <= 3:
            pages = pages[: self.MAX_MANGA_PAGES]
            found = {}  # page index -> its images
            total = len(pages) + 1

            def read(index: int, page_url: str):
                try:
                    page_html, page_final = self._page(page_url, referer=final)
                except Exception:
                    return index, []
                return index, page_images(soup_of(page_html), page_final)[:3]

            def report():
                # The images so far, in page order up to the first page still missing.
                ordered = list(images)
                for index in range(len(pages)):
                    if index not in found:
                        break
                    ordered.extend(i for i in found[index] if i not in ordered)
                self.on_images(ordered, total)

            if self.parallel:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                with ThreadPoolExecutor(8, thread_name_prefix="generic-pages") as pool:
                    for future in as_completed([pool.submit(read, i, u) for i, u in enumerate(pages)]):
                        index, page = future.result()
                        found[index] = page
                        report()
            else:
                for index, page_url in enumerate(pages):
                    found[index] = read(index, page_url)[1]
                    report()
            for index in range(len(pages)):
                images.extend(i for i in found.get(index, []) if i not in images)
        elif len(images) < 3:
            return None
        return "".join(f'<p><img src="{image}" alt=""/></p>' for image in images)

    def _dm5_chapter(self, html: str, final: str) -> Optional[str]:
        """A DM5-platform chapter (fanfox, mangahere): its pages from chapterfun.ashx; None for other pages."""
        info = dm5_chapter(html)
        if info is None:
            return None
        count = min(info["count"], self.MAX_MANGA_PAGES)
        images = [urljoin(final, image) for image in info["images"]][:count]
        if not images and count:
            ask = final.rsplit("/", 1)[0] + "/chapterfun.ashx"
            found: Dict[int, str] = {}  # page index -> its address

            def read(page: int) -> Tuple[int, List[str]]:
                result = self.fetch(f"{ask}?cid={info['cid']}&page={page}&key={info['key']}", referer=final,
                                    headers={"X-Requested-With": "XMLHttpRequest"}) if self.fetch else None
                if not result or result["status"] >= 400:
                    return page, []
                return page, dm5_images(decode(result), final)

            def take(page: int, listed: List[str]) -> None:
                for offset, image in enumerate(listed):
                    if page - 1 + offset < count:
                        found.setdefault(page - 1 + offset, image)
                ordered = []
                for index in range(count):
                    if index not in found:
                        break
                    ordered.append(found[index])
                self.on_images(ordered, count)

            # Each answer holds two pages: every other page is asked for, then any still missing.
            for wanted in (list(range(1, count + 1, 2)), None):
                if wanted is None:
                    wanted = [index + 1 for index in range(count) if index not in found]
                if not wanted:
                    break
                if self.parallel:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    with ThreadPoolExecutor(6, thread_name_prefix="generic-dm5") as pool:
                        for future in as_completed([pool.submit(read, page) for page in wanted]):
                            take(*future.result())
                else:
                    for page in wanted:
                        take(*read(page))
            images = [found[index] for index in range(count) if index in found]
            if len(images) < count:
                self.log(f"Generic: {count - len(images)} of the chapter's {count} pages could not be read.")
        if not images:
            raise ValueError("the site listed no pages for this chapter")
        if len(images) > 1 and self.fetch:
            last = self.fetch(images[-1], referer=final, binary=True, headers={"Accept": "image/*"})
            if last and last["status"] < 400 and image_size(last.get("content") or b"") == DM5_AD_SIZE:
                images.pop()
                self.on_images(images, len(images))
        return "".join(f'<p><img src="{image}" alt=""/></p>' for image in images)

    def _browser_chapter(self, url: str, final: str, name: str) -> Optional[Tuple[str, str, str]]:
        """(content, text, page) of a chapter rendered in a real browser, or None when that is not
        possible or still gives scrambled text."""
        if self.render is None:
            return None
        try:
            rendered = self.render(url, real_browser=True)
        except TypeError:  # a render without that choice (the Catalog reader's)
            rendered = self.render(url)
        if not rendered:
            return None
        content, text = extract_content(app_data_chapter(rendered) or rendered, final, name)
        if len(text) < 20 or scrambled_hangul(text):
            return None
        return content, text, rendered

    def parse_chapter(self, chapter_info: dict, book: dict, *, listed_only: bool = False) -> Optional[dict]:
        url = asura_address(webtoons_desktop(dm5_desktop(chapter_info.get("url", ""))))
        name = chapter_info.get("fullName") or chapter_info.get("name", "")
        host = urlsplit(url).netloc
        # A site that sent scrambled text before: straight to the browser.
        known = self._browser_chapter(url, url, name) if host in self._scrambling else None
        if known:
            content, text, html = known
            final = url
        else:
            html, final = self._page(url, referer=chapter_info.get("bookUrl") or book.get("bookUrl") or "")
            if listed_only:
                from html import escape

                listed = listed_comic_pages(soup_of(html), final)
                if not listed:
                    return None
                return {"chapterName": name, "contentHtml": "".join(f'<p><img src="{escape(image)}" alt=""/></p>' for image in listed),
                        "contentText": "", "imageCount": len(listed), "images": [], "comic": True, "imageReferer": final,
                        "imageFallback": True}
            manga = self._dm5_chapter(html, final)
            if manga is None and webtoons_title(final):
                images = webtoons_images(html, final)
                manga = "".join(f'<p><img src="{image}" alt=""/></p>' for image in images) if images else None
            if manga is None and (urlsplit(final).hostname or "").lower() in _ASURA_HOSTS:
                images = asura_pages(html)
                if images:
                    self.on_images(images, len(images))
                    manga = "".join(f'<p><img src="{image}" alt=""/></p>' for image in images)
            if manga is None:
                manga = self._manga_chapter(html, final)
            if manga is not None:
                return {"chapterName": name, "contentHtml": manga, "contentText": "",
                        "imageCount": manga.count("<img"), "images": [], "comic": True}
            content, text = extract_content(app_data_chapter(html) or html, final, name)
            if scrambled_hangul(text) and self.render is not None:
                # bookkor sends plain requests scrambled text; the browser gets the real chapter.
                self.log(f"  {name}: the site sent scrambled text; reading the chapter in the browser.")
                read = self._browser_chapter(url, final, name)
                if read:
                    content, text, html = read
                    self._scrambling.add(host)
            if scrambled_hangul(text):
                raise ValueError("the site returned scrambled chapter text")
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
