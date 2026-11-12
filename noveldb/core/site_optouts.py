"""Sites the app leaves alone because their owners asked.

- The list holds SHA-256 digests of host names, not the names. A host also covers its subdomains.
- `opted_out(url)` is asked wherever a link is about to be read: the downloader, the Catalog's
  reader and searches. The caller logs or shows `MESSAGE` and reads nothing.

The phones keep the same list (`SiteOptOuts.kt`, `SiteOptOuts.swift`); keep the three in step.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from urllib.parse import urlsplit

MESSAGE = "Support for this site has been disabled at the request of the site owner."

_DIGESTS = frozenset({
    "83aea3497826ee11580bef0f3b60a7fe6c7de15b927319f536433ea7e89b865b",
})


class SiteOptedOut(RuntimeError):
    """A link of an opted-out site was asked for."""

    def __init__(self) -> None:
        super().__init__(MESSAGE)


@lru_cache(maxsize=2048)
def _host_opted_out(host: str) -> bool:
    labels = [label for label in host.split(".") if label]
    # The host and each domain above it, down to two labels ("a.b.example.com", "b.example.com", "example.com").
    return any(
        hashlib.sha256(".".join(labels[start:]).encode("utf-8")).hexdigest() in _DIGESTS
        for start in range(max(len(labels) - 1, 1))
    )


def opted_out(url: str) -> bool:
    """The address (or bare host) belongs to a site whose owner opted out."""
    text = (url or "").strip()
    if not text:
        return False
    try:
        host = urlsplit(text if "//" in text else "//" + text).hostname or ""
    except ValueError:
        return False
    return bool(host) and _host_opted_out(host.lower().rstrip("."))
