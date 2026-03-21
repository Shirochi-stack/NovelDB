"""Merge unique novel entries from novels_full.json into novels.json.

novels_full.json uses dict format (from scrape), novels.json uses array format
(for the site). This script adds any entries from novels_full.json that are not
already in novels.json, preserving all existing data.

Usage:
    python scripts/merge_unique_sets.py
"""

import sys, json, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

COVER_PREFIX = "https://novelpia.com"

NOVELS_JSON = "docs/data/novels.json"
NOVELS_FULL = "docs/data/novels_full.json"


def main():
    if not os.path.exists(NOVELS_JSON):
        print(f"Error: {NOVELS_JSON} not found")
        return

    if not os.path.exists(NOVELS_FULL):
        print(f"Error: {NOVELS_FULL} not found")
        return

    # Load novels.json (array format)
    with open(NOVELS_JSON, "r", encoding="utf-8") as f:
        arr_data = json.load(f)

    seen = set()
    merged = []
    for a in arr_data:
        nid = a[0]
        if nid not in seen:
            seen.add(nid)
            merged.append(a)

    print(f"novels.json: {len(merged)} unique entries")

    # Load novels_full.json (dict format)
    with open(NOVELS_FULL, "r", encoding="utf-8") as f:
        full_data = json.load(f)

    added = 0
    for n in full_data:
        nid = n.get("id")
        if nid and nid not in seen:
            seen.add(nid)
            added += 1
            cover = n.get("cover", "")
            if cover.startswith(COVER_PREFIX):
                cover = cover[len(COVER_PREFIX):]
            merged.append([
                nid,                        # [0]  id
                n.get("title", ""),          # [1]  title
                n.get("author", ""),         # [2]  author
                cover,                       # [3]  cover (relative)
                n.get("tags", []),           # [4]  tags
                n.get("views", 0),           # [5]  views
                n.get("likes", 0),           # [6]  likes
                n.get("chapters", 0),        # [7]  chapters
                n.get("complete", 0),        # [8]  complete
                n.get("updated", ""),         # [9]  updated
                0,                           # [10] weeklyRank (all)
                n.get("age", 0),             # [11] age rating
                0,                           # [12] monthlyRank (all)
                0,                           # [13] dailyRank (all)
                0,                           # [14] weeklyRankAdult
                0,                           # [15] monthlyRankAdult
                0,                           # [16] dailyRankAdult
                0,                           # [17] weeklyRankTeen
                0,                           # [18] monthlyRankTeen
                0,                           # [19] dailyRankTeen
            ])

    print(f"novels_full.json: {added} new unique entries added")
    print(f"Merged total: {len(merged)}")

    with open(NOVELS_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, separators=(",", ":"))

    sz = os.path.getsize(NOVELS_JSON) / 1024 / 1024
    print(f"Saved {NOVELS_JSON}: {sz:.1f} MB")


if __name__ == "__main__":
    main()
