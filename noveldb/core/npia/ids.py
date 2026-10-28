"""npia novel IDs and URLs (moved from gui.py)."""

import re


def extract_novel_id(value):
    """Accept a raw novel ID or a Npia novel URL and return the numeric ID.

    Returns None when the input is empty or cannot be parsed into an ID.
    Examples:
        extract_novel_id("123456")                             -> "123456"
        extract_novel_id("https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/123456")  -> "123456"
        extract_novel_id("  /novel/123456?x=1 ")              -> "123456"
        extract_novel_id("not-an-id")                          -> None
    """
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    if value.isdigit():
        return value
    m = re.search(r"\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061\.com/novel/(\d+)", value, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"/novel/(\d+)", value)
    if m:
        return m.group(1)
    return None


def novel_url(novel_id) -> str:
    """The novel page address for an ID (what the link cart and the mobile apps store)."""
    return f"https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/{novel_id}"
