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

import gzip, json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

DATA = os.path.join("docs", "data", "sfacg_novels.json")
OUTPUT = os.path.join("docs", "data", "sfacg_descriptions.txt")


def load_existing_rows():
    """Load existing synopsis rows from sfacg_descriptions.txt (or .gz) if it exists."""
    rows = {}
    # Try raw .txt first (local dev), then .gz (committed to repo)
    source = None
    if os.path.exists(OUTPUT):
        source = OUTPUT
        with open(OUTPUT, "r", encoding="utf-8") as f:
            lines = f.readlines()
    elif os.path.exists(OUTPUT + ".gz"):
        source = OUTPUT + ".gz"
        with gzip.open(OUTPUT + ".gz", "rt", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        return rows

    for line in lines:
        line = line.rstrip("\r\n")
        if not line: continue
        parts = line.split("|||", 2)
        nid = parts[0].strip()
        if not nid: continue
        raw = parts[1] if len(parts) >= 2 else ""
        en = parts[2].strip() if len(parts) >= 3 else ""
        rows[nid] = (raw, en)
    if rows:
        print(f"  Loaded {len(rows)} existing description rows from {source}")
    return rows


def main():
    if not os.path.exists(DATA):
        print(f"Error: {DATA} not found.")
        sys.exit(1)

    print(f"Loading {DATA}...")
    with open(DATA, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded.")

    # Preserve existing raw synopses and translations.
    existing_rows = load_existing_rows()

    # SFACG format: [id, title, author, cover, tags, views, likes, chapters, complete, updated, age,
    #                 popularityRank, bestSellerRank, newBooksRank, bookmarksRank, jpRank, ticketRank,
    #                 synopsis, latestChapterTitle, latestChapterId, latestChapterTime]
    # Synopsis is at index 17
    count = 0
    translated = []
    untranslated = []
    for entry in data:
        nid = str(entry[0]) if len(entry) > 0 else ""
        if not nid:
            continue
        synopsis = entry[17] if len(entry) > 17 else ""
        old_raw, en = existing_rows.get(nid, ("", ""))

        # Normalize newlines, then collapse multiple into one. If this row has
        # already had its JSON synopsis stripped, keep the previous raw text.
        if synopsis:
            normed = synopsis.replace("\r\n", "\n").replace("\r", "\n")
            normed = re.sub(r"\n{2,}", "\n", normed).strip()
            flat = normed.replace("\n", "\\n")
        else:
            flat = old_raw

        # Skip if no synopsis AND no existing translation
        if not flat and not en:
            continue

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

    # Gzip the descriptions file for web serving
    gz_path = OUTPUT + ".gz"
    with open(OUTPUT, "rb") as f_in:
        raw = f_in.read()
    gz = gzip.compress(raw, compresslevel=6)
    with open(gz_path, "wb") as f_out:
        f_out.write(gz)
    print(f"  Gzipped: {len(raw)/1024:.0f} KB -> {len(gz)/1024:.0f} KB ({gz_path})")


if __name__ == "__main__":
    main()
