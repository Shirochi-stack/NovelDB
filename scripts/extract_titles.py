"""Extract novel titles from scraped data for translation.

Supports multiple sources. Each source reads its own JSON and writes
to its *_en.txt file only. Preserves existing English translations
and sorts untranslated rows to the bottom.

Usage:
    python scripts/extract_titles.py              # Novelpia (default)
    python scripts/extract_titles.py kakao         # KakaoPage
    python scripts/extract_titles.py sfacg         # SFACG
    python scripts/extract_titles.py all           # All sources

Output files in docs/data/:
    Novelpia:  titles_en.txt
    KakaoPage: kakao_titles_en.txt
    SFACG:     sfacg_titles_en.txt

Format: novel_id|||Raw Title|||English Title
  - Translated rows are sorted to the top.
  - Untranslated rows (empty column 3) are at the bottom.
  - The site auto-loads the *_titles_en.txt for each source.
"""

import json, os, sys

sys.stdout.reconfigure(encoding="utf-8")

SOURCES = {
    "novelpia": {
        "data": os.path.join("docs", "data", "novels.json"),
        "en":   os.path.join("docs", "data", "titles_en.txt"),
    },
    "kakao": {
        "data": os.path.join("docs", "data", "kakao_novels.json"),
        "en":   os.path.join("docs", "data", "kakao_titles_en.txt"),
    },
    "sfacg": {
        "data": os.path.join("docs", "data", "sfacg_novels.json"),
        "en":   os.path.join("docs", "data", "sfacg_titles_en.txt"),
    },
}


def load_existing_translations(path):
    """Load existing English translations from a _en.txt file."""
    translations = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n").rstrip("\r")
                parts = line.split("|||")
                if len(parts) >= 3 and parts[2].strip():
                    translations[parts[0].strip()] = parts[2].strip()
    return translations


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

    # Preserve existing translations
    existing_en = load_existing_translations(cfg["en"])
    if existing_en:
        print(f"  Preserved {len(existing_en)} existing translations")

    # Build rows, split into translated and untranslated
    translated = []
    untranslated = []
    for r in raw:
        novel_id = str(r[0])
        title = r[1] or ""
        en = existing_en.get(novel_id, "")
        row = f"{novel_id}|||{title}|||{en}\n"
        if en:
            translated.append(row)
        else:
            untranslated.append(row)

    # Write: translated first, untranslated at bottom
    with open(cfg["en"], "w", encoding="utf-8") as f:
        f.writelines(translated)
        f.writelines(untranslated)

    total = len(translated) + len(untranslated)
    print(f"  Wrote {cfg['en']} ({total} titles)")
    print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "novelpia"

    if source == "all":
        for s in SOURCES:
            extract(s)
    else:
        extract(source)

    print("\nDone!")


if __name__ == "__main__":
    main()
