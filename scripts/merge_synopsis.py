"""
Merge synopsis data from novels_full.json into novels.json (compact array format).

novels.json array format: [id, title, author, cover, tags, views, likes, chapters, complete, updated, weeklyRank, age, monthlyRank]
After merge: [...existing 13 fields, synopsis]  (index 13)

This script:
1. Reads novels_full.json (object format with synopsis field)
2. Reads novels.json (compact array format)
3. Adds synopsis as index [13] to each entry
4. Writes updated novels.json
"""
import json
import sys
from pathlib import Path

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"

def main():
    full_path = DOCS_DATA / "novels_full.json"
    compact_path = DOCS_DATA / "novels.json"

    print(f"Reading {full_path.name}...")
    with open(full_path, "r", encoding="utf-8") as f:
        full_data = json.load(f)

    # Build id -> synopsis map
    synopsis_map = {}
    for entry in full_data:
        nid = entry.get("id")
        syn = entry.get("synopsis", "")
        if nid is not None and syn:
            synopsis_map[str(nid)] = syn

    print(f"  Found {len(synopsis_map)} synopses")

    print(f"Reading {compact_path.name}...")
    with open(compact_path, "r", encoding="utf-8") as f:
        compact_data = json.load(f)

    print(f"  {len(compact_data)} novels in compact format")

    # Merge: add synopsis as index 13
    merged = 0
    for entry in compact_data:
        nid = str(entry[0])
        syn = synopsis_map.get(nid, "")
        # Pad to 13 elements if needed
        while len(entry) < 13:
            entry.append(0)
        # Add or update synopsis at index 13
        if len(entry) == 13:
            entry.append(syn)
        else:
            entry[13] = syn
        if syn:
            merged += 1

    print(f"  Merged {merged} synopses into compact data")

    # Write back
    print(f"Writing {compact_path.name}...")
    with open(compact_path, "w", encoding="utf-8") as f:
        json.dump(compact_data, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = compact_path.stat().st_size / 1024 / 1024
    print(f"  Done! New size: {size_mb:.1f} MB")

if __name__ == "__main__":
    main()
