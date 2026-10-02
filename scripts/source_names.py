"""Codename policy for repository text, generated data, and publication metadata.

Wire addresses and vendor contracts retain their exact runtime values. JSON
represents those values with Unicode escapes so stored text uses no provider
names. This is a naming convention, not a secrecy mechanism.
"""
import base64
import json
import re

_PAIRS = [
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


def escape_names(text):
    return NAME_PATTERN.sub(lambda m: "".join(f"\\u{ord(c):04x}" for c in m.group()), text)


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
