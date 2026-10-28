"""Find the file or folder a log line points at ("Output file: C:/... (2.96 MB)", "Saved: /home/...")."""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

_BS = chr(92)  # backslash, built so editors and tools cannot mangle the patterns
# A path starts at a drive ("C:/", "C:\"), a UNC share ("\\server") or a slash after a space/colon.
_START = re.compile("[A-Za-z]:[/" + _BS * 2 + "]|" + _BS * 4 + "|(?<=[ :])/(?=[^ /])|^/(?=[^ /])")
# A trailing size such as " (2.96 MB)" or " (8.0 KB)".
_SIZE = re.compile(r"\s*\(\d[\d.,]*\s*[KMGT]?i?B\)\s*$")


def find_path(line: str, exists: Callable[[str], bool] = os.path.exists) -> Optional[tuple[int, int, str]]:
    """(start, end, path) of the first existing file or folder named in `line`, else None.

    Paths may contain spaces and brackets, so the longest candidate that exists wins: the rest of
    the line from the path's start, without a trailing size, shortened word by word.
    """
    text = line.rstrip()
    for match in _START.finditer(text):
        start = match.start()
        candidate = _SIZE.sub("", text[start:]).rstrip().rstrip(".,;")
        while candidate:
            if exists(candidate):
                return start, start + len(candidate), candidate
            cut = candidate.rfind(" ")
            if cut <= 0:
                break
            candidate = candidate[:cut].rstrip()
    return None
