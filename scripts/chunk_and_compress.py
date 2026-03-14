"""Chunk and gzip-compress a JSON dataset for progressive web loading.

Splits a large JSON array into N gzipped chunks that can be loaded in parallel
by the browser using DecompressionStream.

Optionally embeds title translations and/or descriptions into each chunk,
eliminating the need for separate translation/description file downloads.

Usage:
    python scripts/chunk_and_compress.py                          # SFACG defaults
    python scripts/chunk_and_compress.py --input data.json -n 10  # custom
    python scripts/chunk_and_compress.py --input data.json -n 10 --translations titles.txt --descriptions desc.txt
"""

import json, gzip, os, sys, argparse, math

sys.stdout.reconfigure(encoding="utf-8")


def load_translations_file(path):
    """Load translations from a |||‐delimited text file.

    Supports formats:
        id|||original|||english
        id||||||english  (stripped original)
    Returns dict of {id_str: english_text}.
    """
    trans = {}
    if not path or not os.path.exists(path):
        return trans
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("|||")
            nid = parts[0].strip()
            if not nid:
                continue
            # Column 3 (index 2) = English
            if len(parts) >= 3 and parts[2].strip():
                trans[nid] = parts[2].strip()
    return trans


def load_descriptions_file(path):
    """Load descriptions from a |||‐delimited text file.

    Prefers column 3 (English translation) when available,
    falls back to column 2 (original language) otherwise.
    Returns dict of {id_str: description_text}.
    """
    descs = {}
    if not path or not os.path.exists(path):
        return descs
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("|||")
            nid = parts[0].strip()
            if not nid:
                continue
            # Prefer English (col3), fall back to original (col2)
            eng = parts[2].strip() if len(parts) >= 3 else ""
            orig = parts[1].strip() if len(parts) >= 2 else ""
            text = eng or orig
            if text and text != "N/A":
                descs[nid] = text
    return descs


def main():
    parser = argparse.ArgumentParser(description="Chunk and gzip a JSON dataset")
    parser.add_argument("--input", default="docs/data/sfacg_novels.json", help="Input JSON file")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as input)")
    parser.add_argument("--prefix", default=None, help="Output filename prefix (default: derived from input)")
    parser.add_argument("-n", "--chunks", type=int, default=10, help="Number of chunks")
    parser.add_argument("--translations", default=None,
                        help="Path to title translations file (id|||orig|||english) to embed in chunks")
    parser.add_argument("--descriptions", default=None,
                        help="Path to descriptions file (id|||orig|||english) to embed in chunks")
    args = parser.parse_args()

    input_path = os.path.normpath(args.input)
    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found")
        sys.exit(1)

    output_dir = args.output_dir or os.path.dirname(input_path)
    if args.prefix:
        prefix = args.prefix
    else:
        base = os.path.splitext(os.path.basename(input_path))[0]
        prefix = base.replace("_novels", "_chunk")

    print(f"Reading {input_path}...")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    original_size = os.path.getsize(input_path)
    total = len(data)
    chunk_size = math.ceil(total / args.chunks)

    print(f"  {total} entries, {original_size / 1024 / 1024:.1f} MB")
    print(f"  Splitting into {args.chunks} chunks of ~{chunk_size} entries...")

    # Load optional embedded data
    all_translations = {}
    all_descriptions = {}
    embed = args.translations or args.descriptions

    if args.translations:
        all_translations = load_translations_file(args.translations)
        print(f"  Loaded {len(all_translations)} title translations to embed")

    if args.descriptions:
        all_descriptions = load_descriptions_file(args.descriptions)
        print(f"  Loaded {len(all_descriptions)} descriptions to embed")

    total_gz = 0
    total_raw = 0
    chunk_files = []

    for i in range(args.chunks):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk = data[start:end]
        if not chunk:
            break

        if embed:
            # Get the IDs in this chunk (first element of each entry)
            chunk_ids = {str(entry[0]) for entry in chunk if entry}

            # Build per-chunk translation/description dicts
            chunk_trans = {nid: all_translations[nid]
                           for nid in chunk_ids if nid in all_translations}
            chunk_descs = {nid: all_descriptions[nid]
                           for nid in chunk_ids if nid in all_descriptions}

            # Wrap in object format
            chunk_obj = {"novels": chunk}
            if all_translations:
                chunk_obj["translations"] = chunk_trans
            if all_descriptions:
                chunk_obj["descriptions"] = chunk_descs

            raw = json.dumps(chunk_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        else:
            # Legacy: plain array format
            raw = json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        gz = gzip.compress(raw, compresslevel=6)

        filename = f"{prefix}_{i}.json.gz"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "wb") as f:
            f.write(gz)

        total_raw += len(raw)
        total_gz += len(gz)
        chunk_files.append(filename)
        print(f"  [{i}] {len(chunk)} entries, {len(raw)/1024/1024:.1f} MB raw → {len(gz)/1024/1024:.2f} MB gz  ({filepath})")

    print(f"\nTotal: {total_raw/1024/1024:.1f} MB raw → {total_gz/1024/1024:.1f} MB gz ({len(chunk_files)} files)")
    print(f"Savings vs original: {original_size/1024/1024:.1f} MB → {total_gz/1024/1024:.1f} MB ({total_gz/original_size*100:.0f}%)")

    # Write manifest
    manifest = {
        "chunks": len(chunk_files),
        "totalEntries": total,
        "files": chunk_files,
        "embedded": bool(embed),
    }
    manifest_path = os.path.join(output_dir, f"{prefix}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
