"""Catalog site addresses, ported from the mobile `DownloadService.kt` helpers."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from noveldb.core.settings.model import DEFAULT_CATALOG_URL

NOVELDB_NEXT_URL = "https://noveldb.pages.dev/"


def _same_site(url: str, site: str, *, any_path: bool, subdomains: bool = False) -> bool:
    try:
        have, want = urlsplit((url or "").strip()), urlsplit(site)
        host = (have.hostname or "").lower()
    except ValueError:
        return False
    if have.scheme.lower() not in ("http", "https") or not host:
        return False
    if host != want.hostname and not (subdomains and host.endswith("." + want.hostname)):
        return False
    root = want.path.rstrip("/")
    return any_path or have.path == root or have.path.startswith(root + "/")


def is_noveldb_default(url: str) -> bool:
    """A page of the default NovelDB catalog (GitHub Pages), whose cards carry their own cart buttons."""
    return _same_site(url, DEFAULT_CATALOG_URL, any_path=False)


def is_noveldb_next(url: str) -> bool:
    """A page of NovelDB Next (or one of its preview deployments)."""
    return _same_site(url, NOVELDB_NEXT_URL, any_path=True, subdomains=True)


def is_noveldb_site(url: str) -> bool:
    """A page of NovelDB or NovelDB Next: outside links open in the browser and are never renamed or sent to the cart."""
    return is_noveldb_default(url) or is_noveldb_next(url)


def valid_catalog_url(text: Optional[str]) -> Optional[str]:
    """`text` when the Catalog page can open it: http(s) with a site name ("https://" alone is not)."""
    value = (text or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return value
