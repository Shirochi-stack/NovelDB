"""Extract novel titles from scraped data for translation.

Supports multiple sources. Each source reads its own JSON and writes
its own title files so nothing conflicts.

Usage:
    python scripts/extract_titles.py              # Novelpia (default)
    python scripts/extract_titles.py kakao         # KakaoPage
    python scripts/extract_titles.py sfacg         # SFACG
    python scripts/extract_titles.py all           # All sources

Output files in docs/data/:
    Novelpia:  titles.txt,       titles_en.txt
    KakaoPage: kakao_titles.txt, kakao_titles_en.txt
    SFACG:     sfacg_titles.txt, sfacg_titles_en.txt

Format: novel_id|||Raw Title|||English Title
The site auto-loads the *_titles_en.txt for each source.
"""

import json, os, sys

sys.stdout.reconfigure(encoding="utf-8")

SOURCES = {
    "novelpia": {
        "data": os.path.join("docs", "data", "novels.json"),
        "raw":  os.path.join("docs", "data", "titles.txt"),
        "en":   os.path.join("docs", "data", "titles_en.txt"),
    },
    "kakao": {
        "data": os.path.join("docs", "data", "kakao_novels.json"),
        "raw":  os.path.join("docs", "data", "kakao_titles.txt"),
        "en":   os.path.join("docs", "data", "kakao_titles_en.txt"),
    },
    "sfacg": {
        "data": os.path.join("docs", "data", "sfacg_novels.json"),
        "raw":  os.path.join("docs", "data", "sfacg_titles.txt"),
        "en":   os.path.join("docs", "data", "sfacg_titles_en.txt"),
    },
}


def extract(source):
    cfg = SOURCES.get(source)
    if not cfg:
        print(f"Unknown source: {source}")
        print(f"Available: {', '.join(SOURCES.keys())}, all")
        sys.exit(1)

    if not os.path.exists(cfg["data"]):
        print(f"  Skipping {source}: {cfg['data']} not found.")
        return

    print(f"\n[{source.upper()}] Loading {cfg['data']}...")
    with open(cfg["data"], "r", encoding="utf-8") as f:
        raw = json.load(f)
    print(f"  {len(raw)} novels loaded.")

    # Write raw titles
    with open(cfg["raw"], "w", encoding="utf-8") as f:
        for r in raw:
            novel_id = r[0]
            title = r[1] or ""
            f.write(f"{novel_id}|||{title}\n")
    print(f"  Wrote {len(raw)} titles to {cfg['raw']}")

    # Write translation template
    with open(cfg["en"], "w", encoding="utf-8") as f:
        for r in raw:
            novel_id = str(r[0])
            title = r[1] or ""
            f.write(f"{novel_id}|||{title}|||\n")

    print(f"  Wrote {cfg['en']} ({len(raw)} titles)")


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "novelpia"

    if source == "all":
        for s in SOURCES:
            extract(s)
    else:
        extract(source)

    print("\nDone! To translate:")
    print("  1. Open the *_titles_en.txt file for your source")
    print("  2. Fill in the 3rd column (after the 2nd |||) with English titles")
    print("  3. The website auto-loads translations per source")


if __name__ == "__main__":
    main()
