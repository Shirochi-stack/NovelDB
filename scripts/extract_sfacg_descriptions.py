"""Extract SFACG novel synopses from sfacg_novels.json into sfacg_descriptions.txt.

Produces a single descriptions file. If an existing sfacg_descriptions.txt
is found, preserves any English translations (column 3) already present.

Usage:
    python scripts/extract_sfacg_descriptions.py

Output:
    docs/data/sfacg_descriptions.txt

Format: novel_id|||chinese_synopsis|||english_translation
  - Column 3 (english) may be empty if not yet translated.
  - The site prefers column 3 when present, falls back to column 2.
  - Newlines in synopsis are replaced with literal \\n.
"""

import json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

DATA = os.path.join("docs", "data", "sfacg_novels.json")
OUTPUT = os.path.join("docs", "data", "sfacg_descriptions.txt")


def load_existing_translations():
    """Load existing English translations from sfacg_descriptions.txt if it exists."""
    translations = {}
    if os.path.exists(OUTPUT):
        with open(OUTPUT, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line: continue
                parts = line.split("|||")
                nid = parts[0].strip()
                if not nid: continue
                if len(parts) >= 3 and parts[2].strip():
                    translations[nid] = parts[2].strip()
        if translations:
            print(f"  Preserved {len(translations)} existing English translations")
    return translations


def main():
    if not os.path.exists(DATA):
        print(f"Error: {DATA} not found.")
        sys.exit(1)

    print(f"Loading {DATA}...")
    with open(DATA, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded.")

    # Preserve existing translations
    existing_en = load_existing_translations()

    # SFACG format: [id, title, author, cover, tags, views, likes, chapters, complete, updated, age,
    #                 popularityRank, bestSellerRank, newBooksRank, bookmarksRank, jpRank, ticketRank, synopsis]
    # Synopsis is at index 17
    count = 0
    translated = []
    untranslated = []
    for entry in data:
        nid = str(entry[0]) if len(entry) > 0 else ""
        synopsis = entry[17] if len(entry) > 17 else ""
        if not nid or not synopsis:
            continue

        # Normalize newlines, then collapse multiple into one
        normed = synopsis.replace("\r\n", "\n").replace("\r", "\n")
        normed = re.sub(r"\n{2,}", "\n", normed).strip()
        flat = normed.replace("\n", "\\n")
        en = existing_en.get(nid, "")
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

    # Strip synopsis from JSON to keep file size under GitHub's 100MB limit.
    # The website loads descriptions from sfacg_descriptions.txt instead.
    stripped = 0
    for entry in data:
        if len(entry) > 17 and entry[17]:
            entry[17] = ""
            stripped += 1
        # Also trim trailing empty/zero values
        while entry and (entry[-1] == 0 or entry[-1] == "" or entry[-1] == []):
            entry.pop()

    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    new_size = os.path.getsize(DATA) / 1024 / 1024
    print(f"  Stripped {stripped} synopses from {DATA} ({new_size:.1f} MB)")


if __name__ == "__main__":
    main()
