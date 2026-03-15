"""Merge translated tags into docs/app.js TAG_MAP.

Reads translations from:
  1. docs/data/tags_en.txt (persistent tag|||translation records)
  2. docs/data/tags_untranslated.txt (freshly translated ID|||tag|||translation)
Then updates TAG_MAP in app.js and writes all known translations back to tags_en.txt.
"""
import re, sys, os

sys.stdout.reconfigure(encoding="utf-8")

APP_JS_PATH = os.path.join("docs", "app.js")
TAGS_FILE   = os.path.join("docs", "data", "tags_en.txt")
UNTRANSLATED = os.path.join("docs", "data", "tags_untranslated.txt")


def main():
    if not os.path.exists(APP_JS_PATH):
        print(f"Error: {APP_JS_PATH} not found"); sys.exit(1)

    # Step 1: Load existing translations from tags_en.txt
    all_translations = {}
    if os.path.exists(TAGS_FILE):
        with open(TAGS_FILE, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\r\n")
                if not stripped or "|||" not in stripped:
                    continue
                parts = stripped.split("|||")
                tag = parts[0].strip()
                translation = parts[1].strip() if len(parts) >= 2 else ""
                if tag and translation:
                    all_translations[tag] = translation
        print(f"Loaded {len(all_translations)} existing translations from {TAGS_FILE}")

    # Step 2: Read freshly translated tags from tags_untranslated.txt
    new_count = 0
    if os.path.exists(UNTRANSLATED):
        with open(UNTRANSLATED, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\r\n")
                if not stripped or "|||" not in stripped:
                    continue
                parts = stripped.split("|||")
                if len(parts) >= 3:
                    # Format: ID|||TAG|||TRANSLATION
                    tag = parts[1].strip()
                    translation = parts[2].strip()
                    if tag and translation and tag not in all_translations:
                        all_translations[tag] = translation
                        new_count += 1
        print(f"Found {new_count} new translations from {UNTRANSLATED}")
    else:
        print(f"No untranslated file found at {UNTRANSLATED}")

    if not all_translations:
        print("No translations available. Nothing to do.")
        return

    # Step 3: Update TAG_MAP in app.js
    app_js = open(APP_JS_PATH, encoding="utf-8").read()
    m = re.search(r'const TAG_MAP = \{(.+?)\};', app_js, re.DOTALL)
    if not m:
        print("Error: TAG_MAP not found in app.js"); sys.exit(1)

    existing_pairs = re.findall(r'"([^"]+)":\s*"([^"]+)"', m.group(1))
    existing = dict(existing_pairs)

    # Merge: existing TAG_MAP + all translations
    merged = dict(existing)
    added = 0
    for tag, translation in all_translations.items():
        if tag not in merged:
            merged[tag] = translation
            added += 1

    if added > 0:
        # Build the new TAG_MAP block
        lines = ['    const TAG_MAP = {']
        lines.append('        // === Novelpia (Korean) ===')

        entries = list(merged.items())
        for i in range(0, len(entries), 4):
            batch = entries[i:i+4]
            parts = []
            for k, v in batch:
                ek = k.replace('\\', '\\\\').replace('"', '\\"')
                ev = v.replace('\\', '\\\\').replace('"', '\\"')
                parts.append(f'"{ek}": "{ev}"')
            lines.append('        ' + ', '.join(parts) + ',')

        lines.append('    };')

        new_block = '\n'.join(lines)
        new_js = app_js[:m.start()] + new_block + app_js[m.end():]
        with open(APP_JS_PATH, "w", encoding="utf-8") as f:
            f.write(new_js)

        print(f"Added {added} new tag translations to TAG_MAP (total: {len(merged)})")
    else:
        print(f"TAG_MAP already up to date ({len(merged)} entries)")

    # Step 4: Persist ALL translations to tags_en.txt
    sorted_tags = sorted(all_translations.items())
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        for tag, translation in sorted_tags:
            f.write(f"{tag}|||{translation}\n")
    print(f"Saved {len(sorted_tags)} total translations to {TAGS_FILE}")


if __name__ == "__main__":
    main()
