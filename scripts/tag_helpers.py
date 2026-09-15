"""Common tag discovery and legacy dictionary compatibility."""
from collections import Counter
from pathlib import Path
import re


def legacy_tags(path):
    path = Path(path)
    if not path.exists():
        return {}
    match = re.search(r'const TAG_MAP = \{(.+?)\};', path.read_text(encoding='utf-8'), re.DOTALL)
    return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', match.group(1))) if match else {}


def pending_tags(records, known):
    counts = Counter(tag for record in records for tag in set(record.get('tags') or [])
                     if isinstance(tag, str) and tag.strip() and not tag.isdigit() and tag not in known)
    return sorted(counts, key=lambda tag: (-counts[tag], tag))
