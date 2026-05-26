"""Extract untranslated SFACG tags.

Reads all unique tags from docs/data/sfacg_novels.json, checks which ones are
already translated in docs/data/tags_en.txt or the legacy app.js TAG_MAP, and
outputs untranslated Chinese tags to docs/data/sfacg_tags_untranslated.txt.

Output format: ID|||ORIGINAL|||
"""
import argparse
import gzip
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

NOVELS_PATH = os.path.join("docs", "data", "sfacg_novels.json")
APP_JS_PATH = os.path.join("docs", "app.js")
TAGS_FILE = os.path.join("docs", "data", "tags_en.txt")
OUT_PATH = os.path.join("docs", "data", "sfacg_tags_untranslated.txt")


def load_tag_map_from_js(path):
    """Parse existing TAG_MAP entries from app.js."""
    text = open(path, encoding="utf-8").read()
    m = re.search(r'const TAG_MAP = \{(.+?)\};', text, re.DOTALL)
    if not m:
        print("Warning: TAG_MAP not found in app.js")
        return {}
    pairs = re.findall(r'"([^"]+)":\s*"([^"]+)"', m.group(1))
    return dict(pairs)


def load_tag_map_from_file(path):
    """Load translated tags from tags_en.txt or tags_en.txt.gz."""
    result = {}
    source = None
    if os.path.exists(path):
        source = path
        opener = open
    elif os.path.exists(path + ".gz"):
        source = path + ".gz"
        opener = gzip.open
    else:
        return result

    with opener(source, "rt", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped or "|||" not in stripped:
                continue
            parts = stripped.split("|||", 1)
            tag = parts[0].strip()
            translation = parts[1].strip() if len(parts) >= 2 else ""
            if tag and translation:
                result[tag] = translation
    return result


def has_cjk(text):
    return any(
        "\u3400" <= c <= "\u4dbf" or
        "\u4e00" <= c <= "\u9fff" or
        "\uf900" <= c <= "\ufaff"
        for c in text
    )


def is_valid_tag(tag):
    tag = (tag or "").strip()
    if not tag:
        return False
    if tag.isdigit():
        return False
    if len(tag) == 1 and not tag.isalpha():
        return False
    return has_cjk(tag)


def main():
    parser = argparse.ArgumentParser(description="Extract untranslated SFACG tags")
    parser.add_argument("--output", default=OUT_PATH, help="Output patch file")
    args = parser.parse_args()

    if not os.path.exists(NOVELS_PATH):
        print(f"Error: {NOVELS_PATH} not found")
        sys.exit(1)

    novels = json.load(open(NOVELS_PATH, encoding="utf-8"))
    tag_counts = {}
    for row in novels:
        tags = row[4] if len(row) > 4 and isinstance(row[4], list) else []
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    print(f"Total unique tags in sfacg_novels.json: {len(tag_counts)}")

    translated = {}
    if os.path.exists(APP_JS_PATH):
        translated.update(load_tag_map_from_js(APP_JS_PATH))
    translated.update(load_tag_map_from_file(TAGS_FILE))
    print(f"Already translated: {len(translated)}")

    untranslated = {
        tag: count
        for tag, count in tag_counts.items()
        if tag not in translated and is_valid_tag(tag)
    }
    sorted_tags = sorted(untranslated.items(), key=lambda item: -item[1])

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for i, (tag, _count) in enumerate(sorted_tags):
            f.write(f"{i}|||{tag}|||\n")

    print(f"Extracted {len(sorted_tags)} untranslated SFACG tags to {args.output}")
    if sorted_tags:
        print("  Top 10 by frequency:")
        for tag, count in sorted_tags[:10]:
            print(f"    {tag} ({count} novels)")


if __name__ == "__main__":
    main()
