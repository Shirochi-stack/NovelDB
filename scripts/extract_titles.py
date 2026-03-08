"""Extract all novel titles from the scraped data for translation.

Reads: docs/data/novels.json (optimized array format)
Outputs:
  docs/data/titles.txt       - One title per line, format: ID<TAB>Korean Title
  docs/data/titles_en.txt    - Template for translations: ID<TAB>Korean Title<TAB>English Title

After translating, fill in the 3rd column of titles_en.txt.
The site will load titles_en.txt and overlay English titles if present.

Usage:
    python scripts/extract_titles.py
"""

import json, os, sys

sys.stdout.reconfigure(encoding="utf-8")

DATA_PATH = os.path.join("docs", "data", "novels.json")
OUT_KR = os.path.join("docs", "data", "titles.txt")
OUT_EN = os.path.join("docs", "data", "titles_en.txt")

if not os.path.exists(DATA_PATH):
    print(f"Error: {DATA_PATH} not found. Run the scraper first.")
    sys.exit(1)

print(f"Loading {DATA_PATH}...")
with open(DATA_PATH, "r", encoding="utf-8") as f:
    raw = json.load(f)

print(f"  {len(raw)} novels loaded.")

# Format: [id, title, author, cover, tags, views, likes, chapters, complete, updated, weeklyRank]
# Write Korean titles
with open(OUT_KR, "w", encoding="utf-8") as f:
    for r in raw:
        novel_id = r[0]
        title = r[1] or ""
        f.write(f"{novel_id}\t{title}\n")

print(f"  Wrote {len(raw)} titles to {OUT_KR}")

# Write English template (preserve existing translations if file exists)
existing = {}
if os.path.exists(OUT_EN):
    print(f"  Found existing {OUT_EN}, preserving translations...")
    with open(OUT_EN, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[2].strip():
                existing[parts[0]] = parts[2]
    print(f"  {len(existing)} existing translations found.")

with open(OUT_EN, "w", encoding="utf-8") as f:
    preserved = 0
    for r in raw:
        novel_id = str(r[0])
        title = r[1] or ""
        en = existing.get(novel_id, "")
        if en:
            preserved += 1
        f.write(f"{novel_id}\t{title}\t{en}\n")

print(f"  Wrote {OUT_EN} ({preserved} translations preserved, {len(raw) - preserved} need translation)")
print("\nDone! To translate:")
print(f"  1. Open {OUT_EN}")
print(f"  2. Fill in the 3rd column (after the 2nd tab) with English titles")
print(f"  3. The website will auto-load translations from data/titles_en.txt")
