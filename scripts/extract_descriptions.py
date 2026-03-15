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


def load_existing_rows():
    """Load existing data from descriptions.txt if it exists.

    Returns:
        translations: dict of {nid: english_text} for preserving translations
        full_rows: dict of {nid: "nid|||korean|||english"} for preserving
                   entries not found in the new novels_full.json
    """
    translations = {}
    full_rows = {}
    if os.path.exists(OUTPUT):
        with open(OUTPUT, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line: continue
                parts = line.split("|||")
                nid = parts[0].strip()
                if not nid: continue
                full_rows[nid] = line
                if len(parts) >= 3 and parts[2].strip():
                    translations[nid] = parts[2].strip()
        if translations:
            print(f"  Preserved {len(translations)} existing English translations")
    return translations, full_rows


def main():
    if not os.path.exists(FULL_DATA):
        print(f"Warning: {FULL_DATA} not found, preserving existing descriptions.txt")
        return

    print(f"Loading {FULL_DATA}...")
    with open(FULL_DATA, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded.")

    # Preserve existing translations and full rows
    existing_en, existing_rows = load_existing_rows()

    seen_ids = set()
    count = 0
    translated = []
    untranslated = []
    for entry in data:
        nid = entry.get("id")
        synopsis = entry.get("synopsis", "")
        if nid is None:
            continue
        seen_ids.add(str(nid))
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

    # Preserve rows from existing descriptions.txt that weren't in novels_full.json
    # (e.g. R19 novels that can't be scraped without auth)
    preserved = 0
    for nid, row in existing_rows.items():
        if nid not in seen_ids:
            parts = row.split("|||")
            en = parts[2].strip() if len(parts) >= 3 else ""
            if en:
                translated.append(row + "\n")
            else:
                untranslated.append(row + "\n")
            preserved += 1
            count += 1

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.writelines(translated)
        f.writelines(untranslated)

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"  Wrote {count} descriptions to {OUTPUT} ({size_kb:.0f} KB)")
    print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")
    if preserved:
        print(f"  Preserved {preserved} entries not in {FULL_DATA} (R19/missing novels)")


if __name__ == "__main__":
    main()
