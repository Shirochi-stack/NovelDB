"""Title queries and the direct comic catalog searches."""

import json
import re
import unicodedata
from urllib.parse import quote, urlencode

from noveldb.core.engines.generic import clean_url, decode, soup_of


DIRECT_SITES = ("mangak.io", "mangadex.org")
_FILLER = set("a an the of to in on and is be i my ill im novel manga manhwa manhua webtoon".split())
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)


def _words(text):
    normal = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", re.sub(r"['’`]", "", normal))
                              if word not in _FILLER))


def exact_title(title):
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(title or "")).casefold() if ch.isalnum())


def similar_title(query, title):
    key = exact_title(query)
    if key and key == exact_title(title):
        return 1.0
    wanted, have = _words(query), _words(title)
    if not wanted or not have:
        return 0.0
    return sum(any(other == word or (min(len(word), len(other)) >= 4 and word[:4] == other[:4])
                   for other in have) for word in wanted) / len(wanted)


def title_queries(text):
    """Try the wording as given, then its opening and ending words for long or approximate titles."""
    text = text.strip()[:240]
    words = _words(text)
    compact = " ".join(words[:2] + words[-2:] if len(words) > 4 else words)
    return list(dict.fromkeys(query for query in (text, compact) if query))


def _localized(values):
    if not isinstance(values, dict):
        return ""
    return values.get("en") or next((v for v in values.values() if isinstance(v, str)), "")


def _mangak_rows(result):
    payload = soup_of(decode(result)).find("script", id="__NEXT_DATA__")
    rows = json.loads(payload.string or "{}").get("props", {}).get("pageProps", {}).get("ssrItems", []) if payload else []
    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        slug = row.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,220}", slug):
            continue
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
        cover = row.get("cover")
        yield {"url": "https://mangak.io/" + slug, "title": row.get("name"),
               "alternateTitles": [item.get("name") for item in row.get("altNames") or [] if isinstance(item, dict)],
               "description": row.get("summary"), "cover": clean_url(cover, "https://mangak.io/") if isinstance(cover, str) else "",
               "status": row.get("status"), "chapters": stats.get("chaptersCount"), "views": stats.get("views"),
               "likes": stats.get("bookmarksCount")}


def _mangadex_rows(result):
    payload = json.loads(decode(result))
    if payload.get("result") != "ok":
        return
    for row in payload.get("data", [])[:40]:
        if not isinstance(row, dict):
            continue
        ident = row.get("id") or ""
        if not isinstance(ident, str) or not _UUID.fullmatch(ident):
            continue
        attrs = row.get("attributes") or {}
        links = {item.get("type"): item.get("attributes") or {} for item in row.get("relationships") or []
                 if isinstance(item, dict)}
        cover = links.get("cover_art", {}).get("fileName")
        yield {"url": "https://mangadex.org/title/" + ident, "title": _localized(attrs.get("title")),
               "alternateTitles": [value for names in attrs.get("altTitles") or [] if isinstance(names, dict)
                                   for value in names.values()],
               "description": _localized(attrs.get("description")), "status": attrs.get("status"),
               "author": links.get("author", {}).get("name"),
               "cover": f"https://uploads.mangadex.org/covers/{ident}/{quote(cover, safe='')}.256.jpg" if cover else ""}


def direct_titles(site, text, fetch, active):
    """Search the source without probing its home page or waiting for a general web search."""
    key = exact_title(text)
    matches = {}
    for query in title_queries(text):
        if not active():
            return []
        if site == "mangak.io":
            url = "https://mangak.io/search?" + urlencode({"q": query})
            parser = _mangak_rows
        elif site == "mangadex.org":
            url = "https://api.mangadex.org/manga?" + urlencode(
                [("title", query), ("limit", 24), ("includes[]", "cover_art"), ("includes[]", "author")])
            parser = _mangadex_rows
        else:
            return []
        try:
            result = fetch(url, quick=True, timeout=5, binary=True)
            if not result or result["status"] != 200:
                break
            for row in parser(result):
                if not isinstance(row.get("title"), str) or not row["title"].strip():
                    continue
                names = [name for name in row["alternateTitles"] if isinstance(name, str) and name.strip()]
                titles = [row["title"], *names]
                score = max(similar_title(text, name) for name in titles)
                if score >= 0.6:
                    exact = any(key == exact_title(name) for name in titles)
                    matched = (exact, score, dict(row, alternateTitles=names[:30]))
                    previous = matches.get(row["url"])
                    if previous is None or matched[:2] > previous[:2]:
                        matches[row["url"]] = matched
        except (TypeError, ValueError, AttributeError, KeyError):
            break
        if any(score >= 0.8 for _, score, _ in matches.values()):
            break
    return [card for _, _, card in sorted(matches.values(), key=lambda item: item[:2], reverse=True)[:12]]
