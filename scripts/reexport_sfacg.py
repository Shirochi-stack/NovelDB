"""Re-export sfacg_novels.json in the optimized format.

Reads the existing data, removes the always-zero weeklyRank field (old r[10]),
moves age from r[11] to r[10], and strips trailing zeros.

Old format (12 fields):
    [id, title, author, cover, tags, views, likes, chapters, complete, updated, weeklyRank, age]

New format (11 fields, trailing zeros stripped):
    [id, title, author, cover, tags, views, likes, chapters, complete, updated, age]
"""

import json, os, sys

def main():
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "sfacg_novels.json")
    path = os.path.normpath(path)

    print(f"Reading {path}...")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    old_size = os.path.getsize(path)
    print(f"  {len(data)} entries, {old_size / 1024 / 1024:.2f} MB")

    optimized = []
    for r in data:
        # Old layout: r[0..9] = id..updated, r[10] = weeklyRank (always 0), r[11] = age
        # New layout: r[0..9] = id..updated, r[10] = age
        age = r[11] if len(r) > 11 else 0
        entry = list(r[:10]) + [age]

        # Strip trailing zeros/empty values
        while entry and (entry[-1] == 0 or entry[-1] == "" or entry[-1] == []):
            entry.pop()
        optimized.append(entry)

    # Write back
    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))

    new_size = os.path.getsize(path)
    saved = old_size - new_size
    print(f"  Rewritten: {new_size / 1024 / 1024:.2f} MB (saved {saved / 1024 / 1024:.2f} MB)")

    # Verify
    with open(path, "r", encoding="utf-8") as f:
        check = json.load(f)
    assert len(check) == len(data), f"Entry count mismatch: {len(check)} vs {len(data)}"
    over_11 = sum(1 for r in check if len(r) > 11)
    assert over_11 == 0, f"{over_11} entries still have >11 fields"
    print(f"  Verified: {len(check)} entries, max {max(len(r) for r in check)} fields per entry")

if __name__ == "__main__":
    main()
