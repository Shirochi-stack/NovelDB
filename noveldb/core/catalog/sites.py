"""What the Catalog page does for the page it shows: NovelDB, a supported site, or any other site.

- NovelDB (the default catalog) and NovelDB Next: outside links open in the system browser, cards
  keep their cart buttons.
- A supported site (one of our scrapers, or a site the novel-downloader rules or the WebToEpub parsers
  list): outside links open in the system browser, and a page the app can download gets a floating
  Download button that adds it to the link cart.
- Any other site: outside links are renamed and sent to the link cart when that setting is on.

The Android app has the same classes (`CatalogSite.kt`, `SourceCatalog.supportedSite` and
`downloadablePage`); keep the two in step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlsplit

from noveldb.core import engine_updates
from noveldb.core.catalog.urls import is_noveldb_site
from noveldb.core.settings.model import ENGINE_NATIVE, SITE_ENGINES

KIND_NOVELDB = "noveldb"
KIND_SUPPORTED = "supported"
KIND_OTHER = "other"

# The sites each built-in scraper handles, as a person browses them (no API or image hosts), by source
# codename. A host also covers its subdomains. Hosts of the scrapers' own predicates in
# `external_scraper.py`; `sources.detect_source` decides whether a page of them is one the scraper takes.
BUILTIN_SITES = {
    "npia": ("\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com",),
    "global_npia": ("global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com",),
    "kpage": ("page.\u006b\u0061\u006b\u0061\u006f.com",),
    "nweb": ("novel.\u006e\u0061\u0076\u0065\u0072.com", "m.novel.\u006e\u0061\u0076\u0065\u0072.com"),
    "nseries": ("series.\u006e\u0061\u0076\u0065\u0072.com", "m.series.\u006e\u0061\u0076\u0065\u0072.com"),
    "mpia": ("\u006d\u0075\u006e\u0070\u0069\u0061.com",),
    "jara": ("\u006a\u006f\u0061\u0072\u0061.com",),
    "rbooks": ("\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com",),
    "qdn": ("\u0071\u0069\u0064\u0069\u0061\u006e.com",),
    "floo": ("b.\u0066\u0061\u006c\u006f\u006f.com", "wap.\u0066\u0061\u006c\u006f\u006f.com", "www.\u0066\u0061\u006c\u006f\u006f.com"),
    "fenrir": ("fenrirealm.com",),
    "xiyuwx": ("xiyuwx.com", "yywenxuan.com"),
    "yeduji": ("yeduji.com",),
    "kobo": ("kobo.com", "books.rakuten.co.jp"),
    "1qxs": ("1qxs.com",),
    "69shuba": ("69shuba.tw",),
}

# Sites with a numbered domain that keeps moving: recognised by their name pattern.
BUILTIN_SITE_PATTERNS = {
    "maru": re.compile(r"^(?:www\.)?marumaru\d*\.[a-z]{2,}$", re.I),
    "ntk": re.compile(r"^(?:www\.)?(?:ntk|newtoki|sbxh)\d+\.(?:com|org)$", re.I),
}


@dataclass(frozen=True)
class PageMode:
    """What to do for one page: its kind, whether outside links are renamed, whether to offer Download."""

    kind: str
    cart_links: bool
    download: bool


def _web_url(url: str) -> Optional[tuple]:
    """`(host, split result)` for an http(s) address with a host, else None."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    return host, parts


def _builtin_site(host: str) -> bool:
    for hosts in BUILTIN_SITES.values():
        if any(host == known or host.endswith("." + known) for known in hosts):
            return True
    return any(pattern.match(host) for pattern in BUILTIN_SITE_PATTERNS.values())


def _bundles(enabled: Iterable[str]):
    for name in enabled:
        engine = engine_updates.BY_NAME.get(name)
        bundle = engine_updates.current(engine) if engine is not None else None
        if bundle is not None:
            yield bundle


def supported_site(url: str, enabled: Iterable[str] = SITE_ENGINES) -> bool:
    """A built-in scraper handles the URL's site, or a switched-on script engine lists it."""
    found = _web_url(url)
    if found is None:
        return False
    if _builtin_site(found[0]):
        return True
    return any(bundle.knows(url) for bundle in _bundles(enabled))


def downloadable_page(url: str, enabled: Iterable[str] = SITE_ENGINES) -> bool:
    """This page, not just its site, can be downloaded.

    - A built-in scraper takes the page (its predicate looks at the page) while the built-in scrapers are on.
    - Or a switched-on script engine lists the page: the novel-downloader rules by the page patterns of
      their header, WebToEpub by host with any path but the home page.
    The Download tab still accepts any web link; this decides where to offer the shortcut.
    """
    if _web_url(url) is None:
        return False
    enabled = tuple(enabled)
    if ENGINE_NATIVE in enabled:
        from noveldb.core.sources import detect_source

        if detect_source(url) not in (None, "reader"):
            return True
    return any(bundle.lists_page(url) for bundle in _bundles(name for name in enabled if name != ENGINE_NATIVE))


def classify(url: str, enabled: Iterable[str] = SITE_ENGINES, cart_links_setting: bool = True) -> PageMode:
    if is_noveldb_site(url):
        return PageMode(KIND_NOVELDB, False, False)
    if supported_site(url, enabled):
        return PageMode(KIND_SUPPORTED, False, downloadable_page(url, enabled))
    return PageMode(KIND_OTHER, bool(cart_links_setting), False)


_FRAGMENT = re.compile(r"#$")
_SLASHES = re.compile(r"/+(?=$|[?#])")
_SPACES = re.compile(r"\s+")
_SEPARATORS = re.compile(r"\s+[-–—|·•]\s+|\s*\|\s*")


def same_address(a: str, b: str) -> bool:
    """Same page, ignoring a trailing slash before the query or fragment and an empty fragment (as the bridge does)."""
    def plain(url):
        return _SLASHES.sub("", _FRAGMENT.sub("", url))

    return plain(a) == plain(b)


def site_label(url: str) -> str:
    """`global.example.co.uk` gives `example`: the name people use for the site, for spotting it in a title."""
    found = _web_url(url)
    if found is None:
        return ""
    labels = [part for part in found[0].removeprefix("www.").split(".") if part]
    if len(labels) < 2:
        return ""
    second_level = {"co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu"}
    drop = 2 if len(labels[-1]) == 2 and len(labels) >= 3 and labels[-2] in second_level else 1
    return labels[len(labels) - 1 - drop]


def clean_page_title(title: str, url: str) -> str:
    """A page title for the link cart, without the site's name: "Solo Leveling - Site" becomes "Solo Leveling".

    Segments split by " - ", " | " and the like that name the site (or are only "Home") are dropped; a
    title made of nothing else is kept as it is.
    """
    text = _SPACES.sub(" ", title or "").strip()
    if not text:
        return ""
    label = site_label(url)
    parts = [part.strip() for part in _SEPARATORS.split(text) if part.strip()]
    kept = [part for part in parts
            if not ((len(label) >= 3 and label.lower() in part.lower())
                    or part.lower() in ("home", "official site"))]
    return (" - ".join(kept) if kept else text)[:200]
