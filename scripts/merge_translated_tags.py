"""Merge translated Novelpia tags into docs/data/tags_en.txt.

Reads translations from:
  1. docs/data/tags_en.txt (persistent tag|||translation records)
  2. docs/data/tags_untranslated.txt (freshly translated ID|||tag|||translation)
Then writes all known translations back to tags_en.txt and tags_en.txt.gz.
"""
import gzip, sys, os

sys.stdout.reconfigure(encoding="utf-8")

TAGS_FILE   = os.path.join("docs", "data", "tags_en.txt")
TAGS_GZ_FILE = TAGS_FILE + ".gz"
UNTRANSLATED = os.path.join("docs", "data", "tags_untranslated.txt")


def main():
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

    # Step 3: Persist ALL translations to tags_en.txt and gzip it for the site.
    sorted_tags = sorted(all_translations.items())
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        for tag, translation in sorted_tags:
            f.write(f"{tag}|||{translation}\n")
    print(f"Saved {len(sorted_tags)} total translations to {TAGS_FILE}")

    with open(TAGS_FILE, "rb") as src:
        compressed = gzip.compress(src.read(), compresslevel=6, mtime=0)
    with open(TAGS_GZ_FILE, "wb") as dst:
        dst.write(compressed)
    print(f"Saved gzipped tag translations to {TAGS_GZ_FILE}")


if __name__ == "__main__":
    main()
