"""Merge translated tags into docs/app.js TAG_MAP.

Reads translations from docs/data/tags_en.txt (tag|||translation format),
and inserts any new entries into TAG_MAP in app.js.
"""
import re, sys, os

sys.stdout.reconfigure(encoding="utf-8")

APP_JS_PATH = os.path.join("docs", "app.js")
TAGS_FILE   = os.path.join("docs", "data", "tags_en.txt")
UNTRANSLATED = os.path.join("docs", "data", "tags_untranslated.txt")


def main():
    if not os.path.exists(TAGS_FILE):
        print(f"Error: {TAGS_FILE} not found"); sys.exit(1)
    if not os.path.exists(APP_JS_PATH):
        print(f"Error: {APP_JS_PATH} not found"); sys.exit(1)

    # Load translated tags from tags_en.txt
    new_tags = {}
    with open(TAGS_FILE, encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped or "|||" not in stripped:
                continue
            parts = stripped.split("|||")
            tag = parts[0].strip()
            translation = parts[1].strip() if len(parts) >= 2 else ""
            if tag and translation:
                new_tags[tag] = translation

    if not new_tags:
        print("No translated tags found in tags_en.txt")
        return

    # Read app.js
    app_js = open(APP_JS_PATH, encoding="utf-8").read()

    # Parse existing TAG_MAP
    m = re.search(r'const TAG_MAP = \{(.+?)\};', app_js, re.DOTALL)
    if not m:
        print("Error: TAG_MAP not found in app.js"); sys.exit(1)

    existing_pairs = re.findall(r'"([^"]+)":\s*"([^"]+)"', m.group(1))
    existing = dict(existing_pairs)

    # Find truly new tags (not already in TAG_MAP)
    added = {k: v for k, v in new_tags.items() if k not in existing}
    if not added:
        print(f"All {len(new_tags)} tags already in TAG_MAP. Nothing to add.")
        return

    # Build new TAG_MAP lines
    # Keep existing entries, append new ones at the end grouped
    merged = dict(existing)
    merged.update(added)

    # Build the new TAG_MAP block
    lines = ['    const TAG_MAP = {']
    lines.append('        // === Novelpia (Korean) ===')

    # Group entries into lines of 4 for readability
    entries = list(merged.items())
    for i in range(0, len(entries), 4):
        batch = entries[i:i+4]
        parts = []
        for k, v in batch:
            # Escape any quotes in key/value
            ek = k.replace('\\', '\\\\').replace('"', '\\"')
            ev = v.replace('\\', '\\\\').replace('"', '\\"')
            parts.append(f'"{ek}": "{ev}"')
        lines.append('        ' + ', '.join(parts) + ',')

    lines.append('    };')

    new_block = '\n'.join(lines)

    # Replace in app.js
    new_js = app_js[:m.start()] + new_block + app_js[m.end():]
    with open(APP_JS_PATH, "w", encoding="utf-8") as f:
        f.write(new_js)

    print(f"Added {len(added)} new tag translations to TAG_MAP")
    print(f"Total TAG_MAP entries: {len(merged)}")

    # Also merge results from untranslated file back into tags_en.txt
    # so we maintain a persistent record
    if os.path.exists(UNTRANSLATED):
        from_untranslated = 0
        with open(UNTRANSLATED, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\r\n")
                if not stripped or "|||" not in stripped:
                    continue
                parts = stripped.split("|||")
                if len(parts) >= 3:
                    # ID|||TAG|||TRANSLATION
                    tag = parts[1].strip()
                    translation = parts[2].strip()
                    if tag and translation and tag not in new_tags:
                        new_tags[tag] = translation
                        from_untranslated += 1
        if from_untranslated:
            print(f"Found {from_untranslated} additional translations from untranslated file")

    # Write all translations to tags_en.txt (tag|||translation)
    sorted_tags = sorted(new_tags.items())
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        for tag, translation in sorted_tags:
            f.write(f"{tag}|||{translation}\n")
    print(f"Updated {TAGS_FILE} with {len(sorted_tags)} total entries")


if __name__ == "__main__":
    main()
