"""Build sfacg_top.json.gz from sfacg_novels.json for instant-load on the website.

Extracts the SFACG-ranked novels (popularity, best seller, new books, bookmarks)
into a small standalone gzipped JSON file with embedded translations and descriptions,
enabling the website to show results instantly while the full dataset loads in the background.

Usage:
    python scripts/build_sfacg_top.py
"""

import json, gzip, os, sys

TOP_LIMIT = 100

def has_cjk(text):
    return any(
        "\u3400" <= c <= "\u4dbf" or
        "\u4e00" <= c <= "\u9fff" or
        "\uf900" <= c <= "\ufaff"
        for c in text
    )

def has_ascii_letter(text):
    return any(("a" <= c.lower() <= "z") for c in text)

def is_valid_english(text):
    text = (text or "").strip()
    return bool(text) and not has_cjk(text) and has_ascii_letter(text)

def parse_delimited_row(line):
    """Parse id|||original|||english while allowing ||| inside original text."""
    if "|||" not in line:
        return line.strip(), "", ""
    nid, rest = line.split("|||", 1)
    if "|||" not in rest:
        return nid.strip(), rest, ""
    original, english = rest.rsplit("|||", 1)
    return nid.strip(), original, english

def open_text_with_gzip_fallback(path):
    """Open text, falling back to path + .gz when only compressed data exists."""
    if os.path.exists(path):
        return open(path, "r", encoding="utf-8"), path
    gz_path = path if path.endswith(".gz") else path + ".gz"
    if os.path.exists(gz_path):
        return gzip.open(gz_path, "rt", encoding="utf-8"), gz_path
    return None, None

def main():
    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data")
    novels_path = os.path.join(base_dir, "sfacg_novels.json")
    trans_path = os.path.join(base_dir, "sfacg_titles_en.txt")
    desc_path = os.path.join(base_dir, "sfacg_descriptions.txt")
    output_path = os.path.join(base_dir, "sfacg_top.json.gz")

    if not os.path.exists(novels_path):
        print(f"Error: {novels_path} not found")
        sys.exit(1)

    print(f"Reading {novels_path}...")
    with open(novels_path, "r", encoding="utf-8") as f:
        all_novels = json.load(f)
    print(f"  {len(all_novels)} novels loaded")

    # Load translations (format: id|||chinese|||english)
    trans = {}
    f, source = open_text_with_gzip_fallback(trans_path)
    if f:
        with f:
            for line in f:
                nid, _orig, eng = parse_delimited_row(line.rstrip("\r\n"))
                if nid and is_valid_english(eng):
                    trans[nid] = eng.strip()
        if source != trans_path:
            print(f"  Loaded translations from {source}")
        print(f"  {len(trans)} translations loaded")

    # Load descriptions (format: id|||chinese|||english)
    descs = {}
    f, source = open_text_with_gzip_fallback(desc_path)
    if f:
        with f:
            for line in f:
                nid, orig, eng = parse_delimited_row(line.rstrip("\r\n"))
                if not nid:
                    continue
                if is_valid_english(eng):
                    descs[nid] = eng.strip()
                elif orig.strip():
                    # Fallback to Chinese if no English translation
                    descs[nid] = orig.strip()
        if source != desc_path:
            print(f"  Loaded descriptions from {source}")
        print(f"  {len(descs)} descriptions loaded")

    # Find novels with any SFACG ranking
    # SFACG indices: [11]=popularity, [12]=bestSeller, [13]=newBooks, [14]=bookmarks, [15]=jp, [16]=ticket
    RANK_INDICES = [11, 12, 13, 14, 15, 16]
    ranked = [e for e in all_novels if any(len(e) > i and e[i] > 0 for i in RANK_INDICES)]

    ids_seen = set()
    top = []
    # Sort by popularity rank first, then by any rank
    for e in sorted(ranked, key=lambda x: x[11] if len(x) > 11 and x[11] > 0 else 9999):
        sid = str(e[0])
        if sid not in ids_seen:
            ids_seen.add(sid)
            top.append(e)
            if len(top) >= TOP_LIMIT:
                break

    print(f"\n  Ranked novels: {len(ranked)}, Unique top novels: {len(top)} (limit {TOP_LIMIT})")

    # Build output with embedded translations and descriptions
    translations = {str(e[0]): trans[str(e[0])] for e in top if str(e[0]) in trans}
    descriptions = {str(e[0]): descs[str(e[0])] for e in top if str(e[0]) in descs}

    output = {
        "novels": top,
        "translations": translations,
        "descriptions": descriptions,
    }

    # Write as gzipped JSON
    raw = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    with open(output_path, "wb") as f:
        f.write(gz)

    print(f"\nSaved {output_path}")
    print(f"  {len(top)} novels, {len(translations)} translations, {len(descriptions)} descriptions")
    print(f"  Raw: {len(raw)/1024:.1f} KB -> Gzipped: {len(gz)/1024:.1f} KB")


if __name__ == "__main__":
    main()
