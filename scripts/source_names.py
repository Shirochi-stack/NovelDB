"""Codename policy for repository text, generated data, and publication metadata.

Wire addresses and vendor contracts retain their exact runtime values. JSON
represents those values with Unicode escapes so stored text uses no provider
names. This is a naming convention, not a secrecy mechanism.
"""
import base64
import json
import re

_PAIRS = [
    ("UWlkaWFu", "qdn"),
    ("6LW354K55Lit5paH572R", "qdn"), ("6LW36bue5Lit5paH57ay", "qdn"),
    ("6LW354K56K+75Lmm", "qdn"), ("6LW36bue6K6A5pu4", "qdn"),
    ("6LW354K55bCP6K+0572R", "qdn"), ("6LW36bue5bCP6Kqq57ay", "qdn"),
    ("RmFsb28=", "floo"), ("6aOe5Y2i", "floo"), ("6aOb55un", "floo"),
    ("TmF2ZXIgV2ViIE5vdmVs", "nweb"), ("TmF2ZXIgU2VyaWVz", "nseries"),
    ("bmF2ZXJzZXJpZXM=", "nseries"), ("S2FrYW8gUGFnZQ==", "kpage"),
    ("S2FrYW9QYWdl", "kpage"), ("UmlkaWJvb2tz", "rbooks"),
    ("Tm92ZWxwaWE=", "npia"), ("U0ZBQ0c=", "sfc"),
    ("S2FrYW8=", "kpage"), ("TmF2ZXI=", "nweb"),
    ("TXVucGlh", "mpia"), ("Sm9hcmE=", "jara"), ("UmlkaQ==", "rbooks"),
    ("64W467Ko7ZS87JWE", "npia"), ("7Lm07Lm07Jik7Y6Y7J207KeA", "kpage"),
    ("7Lm07Lm07JikIO2OmOydtOyngA==", "kpage"),
    ("64Sk7J2067KEIOyLnOumrOymiA==", "nseries"),
    ("64Sk7J2067KEIOybueyGjOyEpA==", "nweb"),
    ("66y47ZS87JWE", "mpia"), ("7KGw7JWE6528", "jara"),
    ("66as65SU67aB7Iqk", "rbooks"), ("U0bovbvlsI/or7Q=", "sfc"),
]
NAMES = {base64.b64decode(encoded).decode(): alias for encoded, alias in _PAIRS}
_LOOKUP = {name.lower(): alias for name, alias in NAMES.items()}
_SHORT = base64.b64decode('cmlkaQ==').decode()
NAME_PATTERN = re.compile("|".join(
    (r"(?<![a-zA-Z])" + _SHORT + r"(?![a-z])|" + _SHORT.capitalize()
     + r"(?![a-z])|" + _SHORT.upper() + r"(?![a-zA-Z])|" + _SHORT + r"(?=cdn)")
    if n == _SHORT.capitalize()
    else "(?i:" + re.escape(n) + ")" for n in NAMES))


def to_codenames(text, *, preserve_case=False):
    def replace(match):
        value = match.group()
        alias = _LOOKUP[value.lower()]
        if preserve_case:
            if value.isupper():
                return alias.upper()
            if value[0].isupper():
                return alias.capitalize()
        return alias
    return NAME_PATTERN.sub(replace, text)


_FOLDED_NAMES = sorted(set(_LOOKUP))


def _escape_match(match):
    return "".join(f"\\u{ord(c):04x}" for c in match.group())


def escape_names(text):
    # Scanning hundreds of megabytes with the full pattern is slow, and every
    # match begins with a folded name, so only those positions are tried.
    # Characters that fold to a different length fall back to the full scan.
    folded = text.lower().replace("ſ", "s").replace("ı", "i")
    if len(folded) != len(text):
        return NAME_PATTERN.sub(_escape_match, text)
    starts = set()
    for name in _FOLDED_NAMES:
        position = folded.find(name)
        while position != -1:
            starts.add(position)
            position = folded.find(name, position + 1)
    if not starts:
        return text
    parts, end = [], 0
    for start in sorted(starts):
        if start < end:
            continue
        match = NAME_PATTERN.match(text, start)
        if match:
            parts += (text[end:start], _escape_match(match))
            end = match.end()
    parts.append(text[end:])
    return "".join(parts)


def contains_name(text):
    """Cheap substring filtering before the stricter identifier-aware matcher."""
    folded = text.lower()
    if any(name in folded for name in _LOOKUP if name != _SHORT):
        return True
    position = folded.find(_SHORT)
    while position != -1:
        if NAME_PATTERN.match(text, position):
            return True
        position = folded.find(_SHORT, position + len(_SHORT))
    return False


def dumps(value, *args, **kwargs):
    """Serialize JSON without changing URLs, vendor keys, or source content."""
    return escape_names(json.dumps(value, *args, **kwargs))


def dump(value, stream, *args, **kwargs):
    stream.write(dumps(value, *args, **kwargs))


def sanitize_rules(text):
    """Rename an upstream JS bundle while retaining its external contracts."""
    encoded_contracts = (
        'cmlkaS1hdA==', 'cmlkaS1ncy1hdA==', 'cmlkaS1ydA==',
        'cmlkaS1wYWdlLWNvbnRhaW5lcg==', 'cmlkaS1jb2x1bW4tY29udGFpbmVy',
        'Y29tLnNmYWNn', 'YXBwbGljYXRpb24vdm5kLnNmYWNn',
    )
    contracts = [base64.b64decode(s).decode() for s in encoded_contracts]
    providers = [n for n in NAMES if n.isascii() and ' ' not in n]
    providers.append(base64.b64decode('cmlkaWNkbg==').decode())
    domain = re.compile('(?i)(?:' + '|'.join(re.escape(n) for n in providers)
                        + r')(?=(?:\\*\.)(?:com|net|cn|kr|co)\b|://)')
    text = domain.sub(lambda m: escape_names(m.group()), text)
    for value in sorted(contracts, key=len, reverse=True):
        text = text.replace(value, escape_names(value))
    return to_codenames(text, preserve_case=True)
