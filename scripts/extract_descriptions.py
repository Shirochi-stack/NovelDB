"""Extract novel descriptions/synopses from novels_full.json.

Produces a single descriptions.txt file. If an existing descriptions.txt
is found, preserves any English translations (column 3) already present.

Usage:
    python scripts/extract_descriptions.py

Output:
    docs/data/descriptions.txt

Format: novel_id|||korean_synopsis|||english_translation
  - Column 3 (english) may be empty if not yet translated.
  - The site prefers column 3 when present, falls back to column 2.
  - Newlines in synopsis are replaced with literal \\n.
"""

import json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

FULL_DATA = os.path.join("docs", "data", "novels_full.json")
OUTPUT = os.path.join("docs", "data", "descriptions.txt")


def load_existing_translations():
    """Load existing English translations from descriptions.txt if it exists."""
    translations = {}
    if os.path.exists(OUTPUT):
        with open(OUTPUT, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n").rstrip("\r")
                parts = line.split("|||")
                if len(parts) >= 3 and parts[2].strip():
                    translations[parts[0].strip()] = parts[2].strip()
        if translations:
            print(f"  Preserved {len(translations)} existing English translations")
    return translations


def main():
    if not os.path.exists(FULL_DATA):
        print(f"Error: {FULL_DATA} not found.")
        sys.exit(1)

    print(f"Loading {FULL_DATA}...")
    with open(FULL_DATA, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded.")

    # Preserve existing translations
    existing_en = load_existing_translations()

    count = 0
    translated = []
    untranslated = []
    for entry in data:
        nid = entry.get("id")
        synopsis = entry.get("synopsis", "")
        if nid is None:
            continue
        if synopsis:
            # Normalize newlines, then collapse multiple into one
            normed = synopsis.replace("\r\n", "\n").replace("\r", "\n")
            normed = re.sub(r"\n{2,}", "\n", normed).strip()
            flat = normed.replace("\n", "\\n")
            en = existing_en.get(str(nid), "")
            row = f"{nid}|||{flat}|||{en}\n"
            if en:
                translated.append(row)
            else:
                untranslated.append(row)
            count += 1

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.writelines(translated)
        f.writelines(untranslated)

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"  Wrote {count} descriptions to {OUTPUT} ({size_kb:.0f} KB)")
    print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")


if __name__ == "__main__":
    main()
