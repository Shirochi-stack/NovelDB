"""Page patterns of a userscript header: which pages a script such as `rules-lib.js` runs on.

The novel-downloader rules bundle starts with `// @match`, `// @include` and `// @exclude` lines.
A page counts when some `@match` or `@include` accepts it and no `@exclude` does, which is how the
userscript manager decides where the script runs. The Android app has the same matcher
(`UserScriptPatterns.kt`).

Match patterns follow Greasemonkey: `*` as the scheme is http or https, `*` elsewhere is any run of
characters (in the host it stops at a slash), and a host that starts with `*.` is the site and all
its subdomains. The path is compared with its query; the fragment and port are ignored. `@include`
and `@exclude` may also be `/regular expressions/` tested against the same address. Hosts in the
bundle are partly written as `\\uXXXX` escapes; they are decoded first.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

_LINE = re.compile(r"^//\s*@(match|include|exclude)\s+(\S.*?)\s*$", re.MULTILINE)
_END = "==/UserScript=="
_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_NAMED_GROUP = re.compile(r"\(\?<([A-Za-z_]\w*)>")


def decode_escapes(text: str) -> str:
    """`text` with each backslash-u escape of four hex digits replaced by its character."""
    return _ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _glob(text: str, any_run: str) -> str:
    return any_run.join(re.escape(part) if part else "" for part in text.split("*"))


def compile_pattern(raw: str) -> Optional[re.Pattern]:
    """One pattern as a regular expression over `scheme://host/path?query`; None when it cannot be one."""
    pattern = decode_escapes(raw.strip())
    last = pattern.rfind("/")
    try:
        if pattern.startswith("/") and last > 0 and all(flag in "ims" for flag in pattern[last + 1:]):
            flags = 0
            for flag in pattern[last + 1:]:
                flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}[flag]
            return re.compile(_NAMED_GROUP.sub(r"(?P<\1>", pattern[1:last]), flags)
        separator = pattern.find("://")
        if separator < 0:
            return None
        scheme, rest = pattern[:separator], pattern[separator + 3:]
        slash = rest.find("/")
        host = rest if slash < 0 else rest[:slash]
        path = "/" if slash < 0 else rest[slash:]
        scheme_rx = "https?" if scheme == "*" else _glob(scheme, ".*")
        if host == "*":
            host_rx = "[^/]*"
        elif host.startswith("*."):
            host_rx = r"(?:[^/]*\.)?" + _glob(host[2:], "[^/]*")
        else:
            host_rx = _glob(host, "[^/]*")
        return re.compile(f"^{scheme_rx}://{host_rx}{_glob(path, '.*')}$", re.IGNORECASE)
    except re.error:
        return None


def _target(url: str) -> Optional[str]:
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host.lower()}{parts.path or '/'}{query}"


class UserScriptPatterns:
    def __init__(self, allow: list, deny: list) -> None:
        self.allow = allow
        self.deny = deny

    @classmethod
    def parse(cls, script: str) -> "UserScriptPatterns":
        """Reads the header of `script` (the text up to its `==/UserScript==` line)."""
        end = script.find(_END)
        header = script[:200_000] if end < 0 else script[:end]
        allow, deny = [], []
        for kind, raw in _LINE.findall(header):
            regex = compile_pattern(raw)
            if regex is not None:
                (deny if kind == "exclude" else allow).append(regex)
        return cls(allow, deny)

    def accepts(self, url: str) -> bool:
        target = _target(url)
        if target is None:
            return False
        return any(r.search(target) for r in self.allow) and not any(r.search(target) for r in self.deny)


def is_home_page(url: str) -> bool:
    """True for the front page of a site: an empty path or `/`, whatever the query."""
    try:
        return urlsplit((url or "").strip()).path in ("", "/")
    except ValueError:
        return False
