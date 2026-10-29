"""The user's two minimum waits: one for servers, one for pages (Settings > Network).

Both are whole seconds, 10-600, default 60, stored in config.json as `request_timeout` and
`page_load_timeout` (the mobile apps read and write the same keys with the same meaning).

Each value is a MINIMUM. A call site that has its own fixed timeout asks this module for the
effective one, which is the larger of the two, so a source that needs longer (90, 120, 300 s...)
keeps its own value and a short per-request value (5, 15, 30 s...) rises to the setting:

    requests.get(url, timeout=request_timeout(15))        # seconds, HTTP requests
    requests.get(url, timeout=request_pair(10, 30))       # (connect, read) tuples
    page.goto(url, timeout=page_timeout_ms(30000))         # Playwright, milliseconds
    context.request.get(url, timeout=request_timeout_ms(15000))  # Playwright's own HTTP client
    deadline = time.time() + page_timeout(45)               # polling loops that wait for a page

Not for: thread joins, subprocess waits, local lock waits, UI timers, sleeps and back-off delays,
or probes of the browser on this computer (the local DevTools port); those are not waits for a
server or a page.

The values live in module state so worker code needs no extra parameter. The app updates them
when settings load or change and when a job starts (`configure_from`). No Qt imports here.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

DEFAULT_SECONDS = 60
MIN_SECONDS = 10
MAX_SECONDS = 600

# The connect half of a `(connect, read)` tuple never drops below this: a dead address or an
# unreachable IPv6 route should fail over quickly instead of using up the whole read wait.
CONNECT_FLOOR_SECONDS = 20

_lock = threading.Lock()
_request_seconds = DEFAULT_SECONDS
_page_load_seconds = DEFAULT_SECONDS


def clamp_seconds(value: Any, default: int = DEFAULT_SECONDS) -> int:
    """A setting as whole seconds within 10-600; `default` when it is missing or not a number."""
    if isinstance(value, bool) or value is None:
        seconds = default
    else:
        try:
            seconds = int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            seconds = default
    return min(max(int(seconds), MIN_SECONDS), MAX_SECONDS)


def configure(request: Optional[Any] = None, page_load: Optional[Any] = None) -> None:
    """Set the current values (each is clamped; None leaves that one as it is)."""
    global _request_seconds, _page_load_seconds
    with _lock:
        if request is not None:
            _request_seconds = clamp_seconds(request, _request_seconds)
        if page_load is not None:
            _page_load_seconds = clamp_seconds(page_load, _page_load_seconds)


def configure_from(settings) -> None:
    """Take both values from an `AppSettings` (or anything with a `shared` that has them)."""
    shared = getattr(settings, "shared", settings)
    configure(
        request=getattr(shared, "request_timeout", None),
        page_load=getattr(shared, "page_load_timeout", None),
    )


def reset() -> None:
    """Back to the defaults (tests, and a process that starts without a config)."""
    global _request_seconds, _page_load_seconds
    with _lock:
        _request_seconds = DEFAULT_SECONDS
        _page_load_seconds = DEFAULT_SECONDS


def request_setting() -> int:
    """The current "Request timeout" in seconds."""
    return _request_seconds


def page_setting() -> int:
    """The current "Page load wait" in seconds."""
    return _page_load_seconds


def _floor(fixed: Optional[float], setting: int):
    if fixed is None:
        return setting
    try:
        return max(fixed, setting)
    except TypeError:  # not a number: leave the caller's value alone
        return fixed


def request_timeout(fixed: Optional[float] = None):
    """Seconds an HTTP request may wait for the server: max(`fixed`, the setting)."""
    return _floor(fixed, _request_seconds)


def request_pair(connect: Optional[float] = None, read: Optional[float] = None) -> tuple:
    """A requests `(connect, read)` timeout: the read half gets the floor, connect stays >= 20 s."""
    return (_floor(connect, CONNECT_FLOOR_SECONDS), _floor(read, _request_seconds))


def request_timeout_ms(fixed_ms: Optional[float] = None):
    """`request_timeout` in milliseconds, for browser-side HTTP calls (Playwright `context.request`)."""
    return _floor(fixed_ms, _request_seconds * 1000)


def page_timeout(fixed: Optional[float] = None):
    """Seconds a page may take to load, or a wait for page content may last: max(`fixed`, the setting)."""
    return _floor(fixed, _page_load_seconds)


def page_timeout_ms(fixed_ms: Optional[float] = None):
    """`page_timeout` in milliseconds, for Playwright's `timeout=` arguments."""
    return _floor(fixed_ms, _page_load_seconds * 1000)


def apply_page_defaults(target) -> None:
    """Set a Playwright page's or context's default and navigation timeouts to the page setting.

    Calls that pass an explicit `timeout=` keep it (wrap it with `page_timeout_ms`). Safe on
    objects that lack the methods and on already closed ones.
    """
    ms = _page_load_seconds * 1000
    for name in ("set_default_timeout", "set_default_navigation_timeout"):
        method = getattr(target, name, None)
        if callable(method):
            try:
                method(ms)
            except Exception:
                pass
