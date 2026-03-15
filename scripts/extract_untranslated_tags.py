"""Extract untranslated Novelpia tags.

Reads all unique tags from docs/data/novels.json, checks which ones are
already translated in docs/app.js TAG_MAP, and outputs untranslated tags
to docs/data/tags_untranslated.txt in the ID|||ORIGINAL||| format.

Uses a sequential numeric ID as tag identifier.
"""
import re, json, sys, os

sys.stdout.reconfigure(encoding="utf-8")

NOVELS_PATH = os.path.join("docs", "data", "novels.json")
APP_JS_PATH = os.path.join("docs", "app.js")
TAGS_FILE   = os.path.join("docs", "data", "tags_en.txt")
OUT_PATH    = os.path.join("docs", "data", "tags_untranslated.txt")


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
    """Load translated tags from tags_en.txt (tag|||translation format)."""
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped or "|||" not in stripped:
                continue
            parts = stripped.split("|||")
            tag = parts[0].strip()
            translation = parts[1].strip() if len(parts) >= 2 else ""
            if tag and translation:
                result[tag] = translation
    return result


def main():
    if not os.path.exists(NOVELS_PATH):
        print(f"Error: {NOVELS_PATH} not found"); sys.exit(1)

    # Collect all unique tags from novels.json
    novels = json.load(open(NOVELS_PATH, encoding="utf-8"))
    tag_counts = {}
    for n in novels:
        tags = n[4] if len(n) > 4 else []
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    print(f"Total unique tags in novels.json: {len(tag_counts)}")

    # Load already-translated tags from both sources
    translated = {}
    if os.path.exists(APP_JS_PATH):
        translated.update(load_tag_map_from_js(APP_JS_PATH))
    if os.path.exists(TAGS_FILE):
        translated.update(load_tag_map_from_file(TAGS_FILE))
    print(f"Already translated: {len(translated)}")

    # Find untranslated tags, sorted by frequency (most common first)
    # Filter out junk: pure numbers, single characters, empty/whitespace
    def is_valid_tag(tag):
        if not tag or not tag.strip():
            return False
        if tag.isdigit():
            return False
        if len(tag) == 1 and not tag.isalpha():
            return False
        return True

    untranslated = {t: c for t, c in tag_counts.items()
                    if t not in translated and is_valid_tag(t)}
    sorted_tags = sorted(untranslated.items(), key=lambda x: -x[1])

    # Write in ID|||ORIGINAL||| format (ID = sequential number)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for i, (tag, count) in enumerate(sorted_tags):
            f.write(f"{i}|||{tag}|||\n")

    print(f"Extracted {len(sorted_tags)} untranslated tags to {OUT_PATH}")
    if sorted_tags:
        print(f"  Top 10 by frequency:")
        for tag, count in sorted_tags[:10]:
            print(f"    {tag} ({count} novels)")


if __name__ == "__main__":
    main()
