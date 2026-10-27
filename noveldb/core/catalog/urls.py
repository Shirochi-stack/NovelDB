"""Catalog site addresses, ported from the mobile `DownloadService.kt` helpers."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from noveldb.core.settings.model import DEFAULT_CATALOG_URL

NOVELDB_NEXT_URL = "https://noveldb.pages.dev/"


def is_noveldb_site(url: str) -> bool:
    """The default NovelDB catalog, whose cards carry their own cart buttons."""
    return (url or "").rstrip("/") == DEFAULT_CATALOG_URL.rstrip("/")


def is_noveldb_next(url: str) -> bool:
    return (url or "").rstrip("/") == NOVELDB_NEXT_URL.rstrip("/")


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
