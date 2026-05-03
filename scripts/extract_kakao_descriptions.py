"""Extract KakaoPage raw synopses into kakao_descriptions.txt.

The Kakao scraper writes descriptions directly to kakao_descriptions.txt.
This script keeps the workflow aligned with SFACG/Novelpia: it reconciles the
current Kakao catalog with the descriptions file, preserves existing English
translations, and writes a gzipped copy for web serving.

Format: novel_id|||korean_synopsis|||english_translation
"""

import gzip, json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

DATA = os.path.join("docs", "data", "kakao_novels.json")
OUTPUT = os.path.join("docs", "data", "kakao_descriptions.txt")
DESCRIPTION_INDEX = 20


def load_existing_rows():
    rows = {}
    source = None
    if os.path.exists(OUTPUT):
        source = OUTPUT
        opener = open
        mode = "r"
    elif os.path.exists(OUTPUT + ".gz"):
        source = OUTPUT + ".gz"
        opener = gzip.open
        mode = "rt"
    else:
        return rows

    with opener(source, mode, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("|||")
            nid = parts[0].strip()
            if not nid:
                continue
            raw = parts[1] if len(parts) >= 2 else ""
            en = parts[2].strip() if len(parts) >= 3 else ""
            rows[nid] = (raw, en)

    print(f"  Loaded {len(rows)} existing description rows from {source}")
    return rows


def flatten(text):
    if not text:
        return ""
    text = str(text).replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text.replace("\n", "\\n")


def main():
    if not os.path.exists(DATA):
        print(f"Error: {DATA} not found.")
        sys.exit(1)

    print(f"Loading {DATA}...")
    with open(DATA, "r", encoding="utf-8") as f:
        novels = json.load(f)
    print(f"  {len(novels)} novels loaded.")

    existing = load_existing_rows()

    translated = []
    untranslated = []
    count = 0
    for entry in novels:
        if not entry:
            continue
        nid = str(entry[0]).strip()
        if not nid:
            continue

        embedded = entry[DESCRIPTION_INDEX] if len(entry) > DESCRIPTION_INDEX else ""
        old_raw, old_en = existing.get(nid, ("", ""))
        raw = flatten(embedded) or old_raw
        en = old_en

        if not raw and not en:
            continue

        row = f"{nid}|||{raw}|||{en}\n"
        if en:
            translated.append(row)
        else:
            untranslated.append(row)
        count += 1

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.writelines(translated)
        f.writelines(untranslated)

    gz_path = OUTPUT + ".gz"
    with open(OUTPUT, "rb") as f_in:
        raw_bytes = f_in.read()
    with open(gz_path, "wb") as f_out:
        f_out.write(gzip.compress(raw_bytes, compresslevel=6))

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"  Wrote {count} descriptions to {OUTPUT} ({size_kb:.0f} KB)")
    print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")
    print(f"  Gzipped: {gz_path}")


if __name__ == "__main__":
    main()
