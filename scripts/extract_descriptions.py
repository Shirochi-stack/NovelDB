"""Extract novel descriptions/synopses from novels_full.json.

Unlike extract_titles.py which produces both raw and _en files,
descriptions only need a single file since they don't require
translation — they're displayed as-is in the original language.

Usage:
    python scripts/extract_descriptions.py

Output:
    docs/data/descriptions.txt

Format: novel_id|||synopsis (newlines in synopsis replaced with \\n literal)
The site loads this file to display synopses on Novelpia novel cards.
"""

import json, os, sys

sys.stdout.reconfigure(encoding="utf-8")

FULL_DATA = os.path.join("docs", "data", "novels_full.json")
OUTPUT = os.path.join("docs", "data", "descriptions.txt")


def main():
    if not os.path.exists(FULL_DATA):
        print(f"Error: {FULL_DATA} not found.")
        sys.exit(1)

    print(f"Loading {FULL_DATA}...")
    with open(FULL_DATA, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded.")

    count = 0
    with open(OUTPUT, "w", encoding="utf-8") as f:
        for entry in data:
            nid = entry.get("id")
            synopsis = entry.get("synopsis", "")
            if nid is None:
                continue
            if synopsis:
                # Flatten newlines to literal \n so it's one line per novel
                flat = synopsis.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
                f.write(f"{nid}|||{flat}\n")
                count += 1

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"  Wrote {count} descriptions to {OUTPUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
