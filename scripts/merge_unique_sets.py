"""Merge unique novel entries from novels_full.json into novels.json.

novels_full.json uses dict format (from scrape), novels.json uses array format
(for the site). This script adds any entries from novels_full.json that are not
already in novels.json, preserving all existing data.

Usage:
    python scripts/merge_unique_sets.py
"""

import sys, json, os, tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

COVER_PREFIX = "https://novelpia.com"

ROOT = Path(__file__).resolve().parents[1]
NOVELS_JSON_REL = "docs/data/novels.json"
NOVELS_FULL_REL = "docs/data/novels_full.json"
NOVELS_JSON = ROOT / NOVELS_JSON_REL
NOVELS_FULL = ROOT / NOVELS_FULL_REL


def atomic_write_json(path, data):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def main():
    if not NOVELS_JSON.exists():
        print(f"Error: {NOVELS_JSON_REL} not found")
        sys.exit(1)

    if not NOVELS_FULL.exists():
        print(f"Skipping: {NOVELS_FULL_REL} not found")
        return

    # Load novels.json (array format)
    with NOVELS_JSON.open("r", encoding="utf-8") as f:
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
    with NOVELS_FULL.open("r", encoding="utf-8") as f:
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

    atomic_write_json(NOVELS_JSON, merged)

    sz = os.path.getsize(NOVELS_JSON) / 1024 / 1024
    print(f"Saved {NOVELS_JSON_REL}: {sz:.1f} MB")


if __name__ == "__main__":
    main()
