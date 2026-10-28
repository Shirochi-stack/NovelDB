"""Helpers for the external scraper's manual tools (Enter Browser), shared by every interface."""

from __future__ import annotations

from typing import Optional

# Sources whose sign-in must happen in the saved installed-Chrome profile (as the old dialog did).
REGULAR_PROFILE_SOURCES = ("npia", "mpia", "floo", "fenrir", "qdn", "rbooks", "kobo")


def login_browser_mode(url: Optional[str], regular_setting: bool, source: Optional[str]) -> tuple[bool, str]:
    """(use the regular Chrome profile, message for the log) for Enter Browser on `url`."""
    if url and source in REGULAR_PROFILE_SOURCES:
        if source == "npia":
            return True, ("[npia] Enter Browser uses the downloader's regular installed-Chrome profile. "
                          "Sign in normally, then close that window.")
        if source == "mpia":
            return True, ("[mpia] Enter Browser uses the downloader's saved browser profile. Sign in to read "
                          "your purchased chapters, then close that window. Skip paid selects only free chapters.")
        return True, ("Enter Browser uses the saved installed-Chrome profile for this site. "
                      "Sign in, then close that window.")
    if regular_setting:
        return True, "Opening the regular sign-in browser... Close it when you are done."
    return False, "Opening the browser for sign-in... Close it when you are done."
