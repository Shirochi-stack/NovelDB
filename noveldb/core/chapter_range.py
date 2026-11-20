"""A chapter range typed in one field, the way a print dialog takes pages: `4` or `4-7`.

`4` is chapter 4 alone, `4-7` chapters 4 to 7 and `4,7` chapters 4 and 7. Text that is not a
range keeps its first number: `7-3` is chapter 7 and `3-` chapter 3. No number at all is no range.
The mobile apps read the same text the same way (`ChapterRangeText`).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

Range = Tuple[Optional[int], Optional[int]]

_FIRST = re.compile(r"\d+")
_SECOND = re.compile(r"\s*[-–—~]\s*(\d+)")

PLACEHOLDER = "All  (4, 4-7 or 4,7)"
PURCHASE_PLACEHOLDER = "4, 4-7 or 4,7"


def parse_list(value: str) -> list:
    """Every range in a comma list such as `4,7` or `1-3,9`, as `(first, last)` pairs in order,
    with overlapping and touching ones joined. Each part is read like a single range."""
    ranges = sorted(pair for pair in (parse(part) for part in re.split(r"[,;，、]", value or "")) if pair[0] is not None)
    joined: list = []
    for low, high in ranges:
        if joined and low <= joined[-1][1] + 1:
            joined[-1] = (joined[-1][0], max(joined[-1][1], high))
        else:
            joined.append((low, high))
    return joined


def list_text(ranges) -> str:
    """`4,7` or `1-3,9` for two or more ranges; "" for one or none (a plain range needs no list)."""
    if len(ranges) < 2:
        return ""
    return ",".join(text(low, high) for low, high in ranges)


def read(value: str) -> tuple:
    """What a typed field means: `(first, last, list)`. `first..last` spans everything named; `list`
    is the canonical comma list when the field names separate ranges (`4,7`), else ""."""
    ranges = parse_list(value)
    if not ranges:
        return None, None, ""
    return ranges[0][0], ranges[-1][1], list_text(ranges)


def _listed(low, high, spec: str) -> list:
    """The ranges of `spec` when it belongs to the range `low..high` (it is stored beside it and
    is stale once the range was changed without it), else nothing."""
    ranges = parse_list(spec) if spec else []
    if len(ranges) > 1 and ranges[0][0] == low and ranges[-1][1] == high:
        return ranges
    return []


def selects(low: Optional[int], high: Optional[int], spec: str, number: int) -> bool:
    """True when chapter `number` is chosen by the range and its comma list; no range chooses nothing."""
    ranges = _listed(low, high, spec)
    if ranges:
        return any(first <= number <= last for first, last in ranges)
    return contains(low, high, number)


def shown(low: Optional[int], high: Optional[int], spec: str = "") -> str:
    """The field's text for a stored range and list."""
    ranges = _listed(low, high, spec)
    return list_text(ranges) if ranges else text(low, high)


def named(low: Optional[int], high: Optional[int], spec: str = "") -> str:
    """The range and list as file names and logs show them: `4`, `4-7`, `4,7`, `4-end`."""
    ranges = _listed(low, high, spec)
    return list_text(ranges) if ranges else label(low, high)


_NAMED = re.compile(r"\s*\((\d+(?:-(?:\d+|end))?(?:,\d+(?:-\d+)?)*)\)$")


def split_name(name: str) -> Tuple[str, str]:
    """`("Title", "4-7")` for a file name `Title (4-7)` a ranged download was saved under;
    `(name, "")` for a name without a range."""
    found = _NAMED.search(name or "")
    if found is None or not name[:found.start()].strip():
        return name, ""
    return name[:found.start()], found.group(1)


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
