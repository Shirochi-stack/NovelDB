"""Conservative helpers for source-declared original publication dates."""

from datetime import datetime
import json
import re


FORMATS = (
    ("%Y%m%d%H%M%S", False),
    ("%Y-%m-%d %H:%M:%S", False),
    ("%Y.%m.%d %H:%M:%S", False),
    ("%Y/%m/%d %H:%M:%S", False),
    ("%Y.%m.%d", True),
    ("%Y/%m/%d", True),
    ("%Y%m%d", True),
)


def normalize_publication_date(value):
    """Return a sortable ISO value only when the source supplied a valid date."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for pattern in ("%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date().isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else parsed.isoformat()
    except ValueError:
        pass
    for pattern, date_only in FORMATS[:-3]:
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.date().isoformat() if date_only else parsed.isoformat()
        except ValueError:
            continue
    return None


def explicit_publication_date(mapping, *fields):
    """Read only named source fields; never estimate a date from IDs or updates."""
    if not isinstance(mapping, dict):
        return None
    for field in fields:
        result = normalize_publication_date(mapping.get(field))
        if result:
            return result
    return None


def structured_publication_date(soup):
    """Read an explicit publication date from standard page metadata or JSON-LD."""
    selectors = (
        ('meta[itemprop="datePublished"][content]', "content"),
        ('meta[property="article:published_time"][content]', "content"),
        ('time[itemprop="datePublished"][datetime]', "datetime"),
    )
    for selector, attribute in selectors:
        node = soup.select_one(selector)
        result = normalize_publication_date(node.get(attribute)) if node else None
        if result:
            return result

    def visit(value):
        if isinstance(value, dict):
            for key in ("datePublished", "dateCreated", "publicationDate"):
                result = normalize_publication_date(value.get(key))
                if result:
                    return result
            for nested in value.values():
                result = visit(nested)
                if result:
                    return result
        elif isinstance(value, list):
            for nested in value:
                result = visit(nested)
                if result:
                    return result
        return None

    for node in soup.select('script[type="application/ld+json"]'):
        try:
            result = visit(json.loads(node.string or node.get_text() or ""))
        except (TypeError, ValueError):
            continue
        if result:
            return result
    return None
