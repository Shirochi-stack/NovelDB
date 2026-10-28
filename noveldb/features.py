"""Which optional parts this build has.

Three builds share one codebase: Full (Playwright + Chromium + QtWebEngine), Lite (no
Playwright; keeps the embedded Catalog) and TurboLite (no Chromium at all). Frozen builds record
their variant in `_build_info.VARIANT`; a source checkout reports "source" and enables whatever
is installed. `NOVELDB_FORCE_VARIANT` simulates a smaller build from source, and
`NOVELDB_DISABLE_WEBENGINE` / `NOVELDB_DISABLE_PLAYWRIGHT` switch single parts off (tests).
"""

from __future__ import annotations

import importlib.util
import os
from functools import lru_cache

VARIANTS = ("full", "lite", "turbolite")


def variant() -> str:
    forced = os.environ.get("NOVELDB_FORCE_VARIANT", "").strip().lower()
    if forced in VARIANTS:
        return forced
    try:
        from _build_info import VARIANT  # written by scripts/build_desktop.py
    except ImportError:
        return "source"
    return VARIANT if VARIANT in VARIANTS else "source"


def _disabled(name: str) -> bool:
    return os.environ.get(f"NOVELDB_DISABLE_{name}", "").strip().lower() in {"1", "true", "yes"}


@lru_cache(maxsize=None)
def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def has_playwright() -> bool:
    """The external scraper can drive a browser (Full builds)."""
    if _disabled("PLAYWRIGHT") or variant() in {"lite", "turbolite"}:
        return False
    return _installed("playwright")


def has_webengine() -> bool:
    """The Catalog and the Google login can be embedded (Full and Lite builds)."""
    if _disabled("WEBENGINE") or variant() == "turbolite":
        return False
    return _installed("PySide6.QtWebEngineWidgets")


def has_pywebview() -> bool:
    """pywebview is available for the Google login when QtWebEngine is not."""
    return _installed("webview")


def has_weasyprint() -> bool:
    """PDF output is possible (WeasyPrint and its native libraries load)."""
    if not _installed("weasyprint"):
        return False
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


def reset_cache() -> None:
    _installed.cache_clear()
