"""Chunk and gzip-compress a JSON dataset for progressive web loading.

Splits a large JSON array into N gzipped chunks that can be loaded in parallel
by the browser using DecompressionStream.

Usage:
    python scripts/chunk_and_compress.py                          # SFACG defaults
    python scripts/chunk_and_compress.py --input data.json -n 10  # custom
"""

import json, gzip, os, sys, argparse, math

def main():
    parser = argparse.ArgumentParser(description="Chunk and gzip a JSON dataset")
    parser.add_argument("--input", default="docs/data/sfacg_novels.json", help="Input JSON file")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as input)")
    parser.add_argument("--prefix", default=None, help="Output filename prefix (default: derived from input)")
    parser.add_argument("-n", "--chunks", type=int, default=10, help="Number of chunks")
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

    total_gz = 0
    total_raw = 0
    chunk_files = []

    for i in range(args.chunks):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk = data[start:end]
        if not chunk:
            break

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
    }
    manifest_path = os.path.join(output_dir, f"{prefix}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
