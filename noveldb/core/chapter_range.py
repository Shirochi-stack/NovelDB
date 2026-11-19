"""A chapter range typed in one field, the way a print dialog takes pages: `4` or `4-7`.

`4` is chapter 4 alone and `4-7` chapters 4 to 7. Text that is not a range keeps its first number:
`7-3` is chapter 7 and `3-` chapter 3. No number at all is no range.
The mobile apps read the same text the same way (`ChapterRangeText`).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

Range = Tuple[Optional[int], Optional[int]]

_FIRST = re.compile(r"\d+")
_SECOND = re.compile(r"\s*[-–—~]\s*(\d+)")

PLACEHOLDER = "All  (4 or 4-7)"


def parse(text: str) -> Range:
    """`(first, last)` of the chapters `text` names, or `(None, None)` when it names none."""
    first = _FIRST.search(text or "")
    if first is None:
        return None, None
    low = int(first.group())
    if low < 1:
        return None, None
    second = _SECOND.match(text, first.end())
    high = int(second.group(1)) if second else low
    return low, (high if high >= low else low)


def text(low: Optional[int], high: Optional[int]) -> str:
    """The field's text for a stored range. An open end (older settings) shows as `4-` or `1-7`."""
    if low is None and high is None:
        return ""
    if low is None:
        return f"1-{high}"
    if high is None:
        return f"{low}-"
    return str(low) if high <= low else f"{low}-{high}"


def label(low: Optional[int], high: Optional[int]) -> str:
    """The range as file names and logs show it: `4`, `4-7`, `4-end`."""
    if low is None and high is None:
        return ""
    if high is None:
        return f"{low}-end"
    low = low or 1
    return str(low) if high <= low else f"{low}-{high}"


def contains(low: Optional[int], high: Optional[int], number: int) -> bool:
    """True when chapter `number` (from 1) is inside the range; no range holds nothing."""
    if low is None and high is None:
        return False
    return (low or 1) <= number and (high is None or number <= high)
