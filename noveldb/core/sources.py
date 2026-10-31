"""Which source a URL or line belongs to, and which engine downloads it.

npia novel pages, bare npia IDs and "Title,ID" batch lines use the native npia pipeline (login,
font mapping, author notices). Everything else - and npia viewer links, global npia, or npia with
the Browser engine chosen - goes to the external scraper. Detection checks the scraper's own
predicates first, in the order `ExternalScraper.parse_book` uses, so a `/novel/<id>` path on
another site is never mistaken for npia.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

NPIA_NATIVE = "npia_native"
BROWSER = "browser"

# (scraper predicate, source codename), in `parse_book` order.
_PREDICATES = (
    ("is_sfc", "sfc"),
    ("is_kobo", "kobo"),
    ("is_xiyuwx", "xiyuwx"),
    ("is_floo", "floo"),
    ("is_fenrir", "fenrir"),
    ("is_maru", "maru"),
    ("is_1qxs", "1qxs"),
    ("is_69shuba", "69shuba"),
    ("is_global_npia", "global_npia"),
    ("is_rbooks", "rbooks"),
    ("is_npia", "npia"),
    ("is_kpage", "kpage"),
    ("is_qdn", "qdn"),
    ("is_ntk_novel", "ntk"),
    ("is_yeduji", "yeduji"),
    ("is_mpia", "mpia"),
    ("is_jara", "jara"),
    ("is_nweb_series", "nweb"),
    ("is_nweb_novel", "nweb"),
)

_BARE_ID = re.compile(r"^\s*\d{1,10}\s*$")
_BATCH_LINE = re.compile(r"^\s*[^,]*,\s*\d{1,10}\s*$")


def _scraper():
    from external_scraper import ExternalScraper

    return ExternalScraper


def is_url(text: str) -> bool:
    parts = urlsplit(text.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def detect_source(text: str) -> Optional[str]:
    """The source codename for a URL, ID or batch line; "reader" for other web pages; None if neither."""
    value = (text or "").strip()
    if not value:
        return None
    if _BARE_ID.match(value) or _BATCH_LINE.match(value):
        return "npia"
    if not is_url(value):
        return None
    scraper = _scraper()
    for predicate, source in _PREDICATES:
        check = getattr(scraper, predicate, None)
        try:
            if check is not None and check(value):
                return source
        except Exception:
            continue
    return "reader"


def is_npia_viewer(url: str) -> bool:
    return "/viewer/" in urlsplit(url).path


def engine_for(text: str, source: Optional[str] = None, npia_engine: str = "native") -> str:
    """NPIA_NATIVE or BROWSER for an input line (see the module docstring)."""
    source = source or detect_source(text)
    if source == "npia" and npia_engine != "browser":
        value = text.strip()
        if not is_url(value) or not is_npia_viewer(value):
            return NPIA_NATIVE
    return BROWSER


def split_inputs(text: str) -> list:
    """Non-empty input lines (URLs, IDs, batch lines), without comments or duplicates."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
