"""Routing and the shared browser script for the Comix reader."""

from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

_HOSTS = ("comix.to", "comix.ws")
_TITLE = re.compile(r"^/title/([a-z0-9]+)(?:-[^/]+)?(?:/\d+(?:-[^/]+)?)?/?$", re.I)


def knows(url: str) -> bool:
    try:
        page = urlsplit(url)
    except ValueError:
        return False
    return (page.scheme in ("http", "https") and page.netloc.lower().removeprefix("www.") in _HOSTS
            and bool(_TITLE.fullmatch(page.path)))


def book_pages(url: str) -> tuple[str, ...]:
    """Try the title on the supplied host, then the alternate host."""
    if not knows(url):
        return ()
    page = urlsplit(url)
    host = page.hostname.removeprefix("www.")
    other = _HOSTS[1] if host == _HOSTS[0] else _HOSTS[0]
    path = "/" + "/".join(page.path.strip("/").split("/")[:2])
    return tuple(urlunsplit(("https", name, path, page.query, "")) for name in (host, other))


def script() -> str:
    return Path(__file__).with_name("comix-reader.js").read_text(encoding="utf-8")
