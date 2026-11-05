"""The user's logins for the Catalog's own requests, so a signed-in user sees what a browser would show
them (a site's 19+ books, figures only members see).

A request's cookies come from three places, the later ones winning a name they share:
- the Catalog's own browser profile (what the user signed in to while browsing the Catalog), handed in
  as it changes (`observe` / `forget`, from the profile's cookie store);
- the Enter Browser profile, the downloader's login browser (only for the sites of the built-in
  scrapers: reading it means decrypting Chrome's cookie database, so it is read once a few minutes per site);
- the app's own logins (Settings): npia's `LOGINKEY` and sfc's cookie. A visitor's npia key is a
  random one until a login binds it, so the app's key wins over one a browser holds.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

PROFILE_SECONDS = 300  # how long the Enter Browser's cookies for a site are kept before reading again

Cookie = Tuple[str, str, str, str]  # (domain, path, name, value); a domain starting with "." takes subdomains


def _matches(host: str, path: str, domain: str, cookie_path: str) -> bool:
    domain = domain.lower()
    if domain.startswith("."):
        if host != domain[1:] and not host.endswith(domain):
            return False
    elif host != domain:
        return False
    return path.startswith(cookie_path or "/")


def login_sites() -> Dict[str, str]:
    """{registered domain: source codename} of the built-in scrapers' sites (the Enter Browser's logins)."""
    from noveldb.core.catalog.sites import BUILTIN_SITES
    from noveldb.core.engines.generic import site_of

    out: Dict[str, str] = {}
    for source, hosts in BUILTIN_SITES.items():
        for host in hosts:
            out.setdefault(site_of(host), source)  # the first source of a domain names it (npia, not global_npia)
    out.setdefault(site_of(SFC_HOST), "sfc")
    return out


SFC_HOST = "book.\u0073\u0066\u0061\u0063\u0067.com"


class SavedLogins:
    """`header(url)`: the Cookie header for a request to `url` ("" when there is none to send)."""

    def __init__(self, secrets: Callable[[], object] = lambda: None,
                 profile_cookies: Optional[Callable[[str], List[dict]]] = None) -> None:
        self.secrets = secrets  # the app's Secrets (settings.secrets), read at each request
        self._profile_cookies = profile_cookies if profile_cookies is not None else _enter_browser_cookies
        self._browser: Dict[Tuple[str, str, str], str] = {}  # (domain, path, name) -> value, the Catalog's profile
        self._profiles: Dict[str, Tuple[float, List[Cookie]]] = {}
        self._sites: Optional[Dict[str, str]] = None
        self._lock = threading.Lock()

    # -- the Catalog's browser profile ------------------------------------------------------------------

    def observe(self, domain: str, path: str, name: str, value: str) -> None:
        if domain and name:
            with self._lock:
                self._browser[(domain.lower(), path or "/", name)] = value

    def forget(self, domain: str, path: str, name: str) -> None:
        with self._lock:
            self._browser.pop(((domain or "").lower(), path or "/", name), None)

    # -- a request's cookies ----------------------------------------------------------------------------

    def cookies(self, url: str) -> Dict[str, str]:
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), parts.path or "/"
        if not host or parts.scheme not in ("http", "https"):
            return {}
        out: Dict[str, str] = {}
        with self._lock:
            browser = list(self._browser.items())
        for (domain, cookie_path, name), value in browser:
            if _matches(host, path, domain, cookie_path):
                out[name] = value
        source = self._source(host)
        if source:
            for domain, cookie_path, name, value in self._enter_browser(host, source):
                if _matches(host, path, domain, cookie_path):
                    out[name] = value
            out.update(self._app_login(source))
        return out

    def header(self, url: str) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies(url).items())

    def _source(self, host: str) -> str:
        from noveldb.core.engines.generic import site_of

        if self._sites is None:
            try:
                self._sites = login_sites()
            except Exception:
                self._sites = {}
        return self._sites.get(site_of(host), "")

    def _enter_browser(self, host: str, source: str) -> List[Cookie]:
        from noveldb.core.engines.generic import site_of

        site = site_of(host)
        with self._lock:
            kept = self._profiles.get(site)
        if kept and time.monotonic() - kept[0] < PROFILE_SECONDS:
            return kept[1]
        try:
            records = self._profile_cookies(site)
        except Exception as e:
            log.debug("Enter Browser cookies for %s unreadable: %s", source, e)
            records = []
        found = [(str(r.get("domain") or site), str(r.get("path") or "/"), str(r["name"]), str(r.get("value") or ""))
                 for r in records if r.get("name") and r.get("value") is not None]
        with self._lock:
            self._profiles[site] = (time.monotonic(), found)
        return found

    def _app_login(self, source: str) -> Dict[str, str]:
        secrets = self.secrets()
        if secrets is None:
            return {}
        if source == "npia":
            from noveldb.core.npia.ids import SESSION_COOKIE

            key = str(getattr(secrets, "loginkey", "") or "").strip()
            return {SESSION_COOKIE: key} if key else {}
        if source == "sfc":
            out = {}
            for item in str(getattr(secrets, "sfc_cookie", "") or "").split(";"):
                name, _, value = item.strip().partition("=")
                if name and value:
                    out[name] = value
            return out
        return {}


_scraper = None
_scraper_lock = threading.Lock()


def _enter_browser_cookies(site: str) -> List[dict]:
    """The Enter Browser's cookies for `site`: its saved snapshot, then its live profile (newer), then
    the session cookies kept when its window closed (a site's login that lasts only a session)."""
    global _scraper
    from external_scraper import ExternalScraper

    with _scraper_lock:
        if _scraper is None:
            _scraper = ExternalScraper()
    scraper = _scraper
    records = list(scraper._storage_cookies_for_url(f"https://{site}/"))
    records.extend(scraper._profile_cookie_records(site))
    try:
        import json

        with open(scraper._get_session_cookie_path(), encoding="utf-8") as handle:
            saved = json.load(handle).get("cookies") or []
        have = {record.get("name") for record in records}
        records.extend(cookie for cookie in saved
                       if (cookie.get("domain") or "").lstrip(".").endswith(site) and cookie.get("name") not in have)
    except (OSError, ValueError):
        pass
    return records
