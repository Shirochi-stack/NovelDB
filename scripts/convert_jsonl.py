"""Convert SpazzTL's JSONL files to our optimized array JSON format.

If you can't run the scrapers directly, download the JSONL files from:
  - https://github.com/SpazzTL/Novelpedia/raw/main/static/kakao_novels.jsonl
  - https://github.com/SpazzTL/Novelpedia/raw/main/static/sfacg_novels.jsonl

Place them in docs/data/ and run:
  python scripts/convert_jsonl.py kakao
  python scripts/convert_jsonl.py sfacg

This converts the JSONL (one JSON object per line) to our optimized array format.
"""

import sys, os, json

sys.stdout.reconfigure(encoding="utf-8")

SOURCE_MAP = {
    "kakao": {
        "input": "docs/data/kakao_novels.jsonl",
        "output": "docs/data/kakao_novels.json",
    },
    "sfacg": {
        "input": "docs/data/sfacg_novels.jsonl",
        "output": "docs/data/sfacg_novels.json",
    },
    "novelpia": {
        "input": "docs/data/novelpia_metadata.jsonl",
        "output": "docs/data/novels.json",
    },
}

STATUS_MAP = {
    # Korean
    "완결": 1, "연재": 0,
    # Chinese
    "完结": 1, "连载": 0,
}


def convert(source):
    cfg = SOURCE_MAP.get(source)
    if not cfg:
        print(f"Unknown source: {source}")
        print(f"Available: {', '.join(SOURCE_MAP.keys())}")
        sys.exit(1)

    if not os.path.exists(cfg["input"]):
        print(f"Error: {cfg['input']} not found.")
        print(f"Download it first from SpazzTL's repo.")
        sys.exit(1)

    print(f"Converting {cfg['input']}...")
    novels = []
    seen = set()

    with open(cfg["input"], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            nid = str(obj.get("id", ""))
            if not nid or nid in seen:
                continue
            seen.add(nid)

            # Normalize tags
            tags = obj.get("tags", [])
            if isinstance(tags, str):
                tags = [tags] if tags else []

            # Determine completion status
            status = obj.get("publication_status", "")
            complete = STATUS_MAP.get(status, 0)

            # Age rating
            age = 19 if obj.get("is_adult", False) else 0

            # Cover URL
            cover = obj.get("cover_url", obj.get("large_cover_url", ""))

            novels.append([
                nid,                                # [0] id
                obj.get("title", ""),               # [1] title
                obj.get("author", ""),              # [2] author
                cover,                              # [3] cover (full URL)
                tags,                               # [4] tags
                obj.get("views", obj.get("view_count", 0)),  # [5] views
                obj.get("like_count", 0),           # [6] likes
                obj.get("chapter_count", 0),        # [7] chapters
                complete,                           # [8] complete
                obj.get("time_scraped", ""),         # [9] updated
                0,                                  # [10] weeklyRank
                age,                                # [11] age
            ])

    print(f"  Parsed {len(novels)} novels")

    os.makedirs(os.path.dirname(cfg["output"]), exist_ok=True)
    with open(cfg["output"], "w", encoding="utf-8") as f:
        json.dump(novels, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(cfg["output"]) / 1024 / 1024
    print(f"  Saved to {cfg['output']} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/convert_jsonl.py <source>")
        print(f"Sources: {', '.join(SOURCE_MAP.keys())}")
        sys.exit(1)

    convert(sys.argv[1])
