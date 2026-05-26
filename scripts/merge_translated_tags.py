"""Merge translated tags into docs/data/tags_en.txt.

Reads translations from:
  1. docs/data/tags_en.txt (persistent tag|||translation records)
  2. a translated patch file (freshly translated ID|||tag|||translation)
Then writes all known translations back to tags_en.txt and tags_en.txt.gz.
"""
import argparse
import gzip, sys, os

sys.stdout.reconfigure(encoding="utf-8")

TAGS_FILE = os.path.join("docs", "data", "tags_en.txt")
TAGS_GZ_FILE = TAGS_FILE + ".gz"
UNTRANSLATED = os.path.join("docs", "data", "tags_untranslated.txt")


def open_existing_tags():
    if os.path.exists(TAGS_FILE):
        return open(TAGS_FILE, encoding="utf-8"), TAGS_FILE
    if os.path.exists(TAGS_GZ_FILE):
        return gzip.open(TAGS_GZ_FILE, "rt", encoding="utf-8"), TAGS_GZ_FILE
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Merge translated tag patch rows")
    parser.add_argument("patch", nargs="?", default=UNTRANSLATED,
                        help="Translated tag patch file")
    parser.add_argument("--recompress-only", action="store_true",
                        help="Only refresh tags_en.txt.gz from existing translations")
    args = parser.parse_args()

    # Step 1: Load existing translations from tags_en.txt
    all_translations = {}
    f, source = open_existing_tags()
    if f:
        with f:
            for line in f:
                stripped = line.rstrip("\r\n")
                if not stripped or "|||" not in stripped:
                    continue
                parts = stripped.split("|||")
                tag = parts[0].strip()
                translation = parts[1].strip() if len(parts) >= 2 else ""
                if tag and translation:
                    all_translations[tag] = translation
        print(f"Loaded {len(all_translations)} existing translations from {source}")

    # Step 2: Read freshly translated tags from the patch file
    new_count = 0
    if args.recompress_only:
        print("Recompress-only mode: skipping patch merge")
    elif os.path.exists(args.patch):
        with open(args.patch, encoding="utf-8") as f:
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
        print(f"Found {new_count} new translations from {args.patch}")
    else:
        print(f"No translated patch file found at {args.patch}")

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
