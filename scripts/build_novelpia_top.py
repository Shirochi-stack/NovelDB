"""Build novelpia_top.json.gz from novels.json for instant-load on the website.

Extracts the top weekly + monthly ranked Novelpia novels into a small standalone
gzipped JSON file with embedded translations and descriptions, enabling the
website to show results instantly while the full dataset loads in the background.

Usage:
    python scripts/build_novelpia_top.py
"""

import json, gzip, os, sys

def main():
    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data")
    novels_path = os.path.join(base_dir, "novels.json")
    trans_path = os.path.join(base_dir, "titles_en.txt")
    desc_path = os.path.join(base_dir, "descriptions.txt")
    output_path = os.path.join(base_dir, "novelpia_top.json.gz")

    if not os.path.exists(novels_path):
        print(f"Error: {novels_path} not found")
        sys.exit(1)

    print(f"Reading {novels_path}...")
    with open(novels_path, "r", encoding="utf-8") as f:
        all_novels = json.load(f)
    print(f"  {len(all_novels)} novels loaded")

    # Load translations (format: id|||korean|||english)
    trans = {}
    if os.path.exists(trans_path):
        with open(trans_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("|||")
                if len(parts) >= 3 and parts[2].strip():
                    trans[parts[0].strip()] = parts[2].strip()
        print(f"  {len(trans)} translations loaded")

    # Load descriptions (format: id|||korean|||english)
    descs = {}
    if os.path.exists(desc_path):
        with open(desc_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("|||")
                if len(parts) >= 3 and parts[2].strip():
                    descs[parts[0].strip()] = parts[2].strip()
                elif len(parts) >= 2 and parts[1].strip():
                    # Fallback to Korean if no English translation
                    descs[parts[0].strip()] = parts[1].strip()
        print(f"  {len(descs)} descriptions loaded")

    # Find novels with weekly rank (index 10) or monthly rank (index 12)
    weekly = [e for e in all_novels if len(e) > 10 and e[10] > 0]
    monthly = [e for e in all_novels if len(e) > 12 and e[12] > 0]

    ids_seen = set()
    top = []
    for e in sorted(weekly, key=lambda x: x[10]):
        sid = str(e[0])
        if sid not in ids_seen:
            ids_seen.add(sid)
            top.append(e)
    for e in sorted(monthly, key=lambda x: x[12]):
        sid = str(e[0])
        if sid not in ids_seen:
            ids_seen.add(sid)
            top.append(e)

    print(f"\n  Weekly ranked: {len(weekly)}, Monthly ranked: {len(monthly)}")
    print(f"  Unique top novels: {len(top)}")

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

    # Remove old uncompressed file if it exists
    old_path = os.path.join(base_dir, "novelpia_top.json")
    if os.path.exists(old_path):
        os.remove(old_path)
        print(f"  Removed old {old_path}")

    print(f"\nSaved {output_path}")
    print(f"  {len(top)} novels, {len(translations)} translations, {len(descriptions)} descriptions")
    print(f"  Raw: {len(raw)/1024:.1f} KB -> Gzipped: {len(gz)/1024:.1f} KB")


if __name__ == "__main__":
    main()
