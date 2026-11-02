"""Keeps credentials out of the engine bundles (and the repository).

The engine bundles are third-party code, built from upstream: an upstream file can carry a key (the
LNReader WTR-LAB plugin had a Google API key for machine translation). Every builder passes its
output through `redact` before writing or publishing it:
- API keys that only work with their key in the code are blanked (the feature that needs one stops
  working; the app never ships the key);
- anything that must never be published (private keys, access tokens) stops the build.

`scan_paths` checks files for the same shapes (scripts/codename_policy.py runs it on the repository).
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Iterable, List, Tuple

# Blanked in bundles: (name, pattern). The literal is replaced by an empty string of the same quoting.
REDACT = (
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
)
# A build that finds one of these fails: they must never be published.
FATAL = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}\b|\bgithub_pat_[0-9A-Za-z_]{60,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}")),
)


class SecretFound(SystemExit):
    pass


def redact(text: str, where: str = "") -> Tuple[str, List[str]]:
    """The text with redactable keys blanked, and what was blanked; raises SecretFound for the rest."""
    for name, pattern in FATAL:
        if pattern.search(text):
            raise SecretFound(f"A {name} is in {where or 'the bundle'}; the build stops (nothing is published).")
    found = []
    for name, pattern in REDACT:
        count = len(pattern.findall(text))
        if count:
            text = pattern.sub("", text)
            found.append(f"{name} x{count}" + (f" in {where}" if where else ""))
    return text, found


def redact_zip(data: bytes, where: str = "") -> Tuple[bytes, List[str]]:
    """A zip with every text member passed through `redact` (member dates and order kept)."""
    found: List[str] = []
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for info in source.infolist():
            content = source.read(info)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                target.writestr(info, content)
                continue
            text, more = redact(text, f"{where}:{info.filename}")
            found.extend(more)
            target.writestr(info, text.encode("utf-8"))
    return (out.getvalue() if found else data), found


def report(found: Iterable[str]) -> None:
    import sys

    for line in found:
        print(f"Removed a credential from the bundle: {line}", file=sys.stderr)


def scan_paths(paths: Iterable[Path]) -> List[str]:
    """`path: what` for every file holding a credential shape (FATAL or REDACT)."""
    problems = []
    for path in paths:
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        if path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    text = "\n".join(archive.read(n).decode("utf-8", "ignore") for n in archive.namelist())
            except zipfile.BadZipFile:
                continue
        else:
            text = data.decode("utf-8", "ignore")
        for name, pattern in FATAL + REDACT:
            if pattern.search(text):
                problems.append(f"{path}: {name}")
    return problems


def shim_part() -> bytes:
    """This file's text, for the builders' shim keys (a change here republishes every bundle)."""
    return Path(__file__).read_bytes().replace(b"\r\n", b"\n")
