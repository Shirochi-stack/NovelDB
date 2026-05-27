"""Split delimited description text files into deterministic gzip chunks.

The website lazy-loads descriptions after the main catalog chunks. Splitting the
description files lets browsers request those lazy synopsis payloads in parallel.

Usage:
    python scripts/chunk_descriptions.py docs/data/descriptions.txt --prefix descriptions_chunk -n 3
"""

import argparse
import glob
import gzip
import json
import math
import os
import stat
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")


def open_text_with_gzip_fallback(path):
    if os.path.exists(path):
        return open(path, "r", encoding="utf-8"), path
    gz_path = path if path.endswith(".gz") else path + ".gz"
    if os.path.exists(gz_path):
        return gzip.open(gz_path, "rt", encoding="utf-8"), gz_path
    return None, None


def replace_with_retries(src, dst, attempts=12, delay=0.5):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            if os.path.exists(dst):
                try:
                    os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)
                except OSError:
                    pass
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(delay)
    raise PermissionError(
        f"Could not replace {dst} after {attempts} attempts. "
        "Close any browser, local server, editor, or sync/indexing process using it, then rerun. "
        f"Temporary output was left at {src}."
    ) from last_error


def load_lines(path):
    handle, source = open_text_with_gzip_fallback(path)
    if not handle:
        raise FileNotFoundError(f"{path} not found, and no gzip fallback exists")
    with handle:
        lines = [line.rstrip("\r\n") for line in handle if line.rstrip("\r\n")]
    return source, lines


def remove_stale_chunks(output_dir, prefix):
    pattern = os.path.join(output_dir, f"{prefix}_*.txt.gz")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def write_gzip(path, lines):
    raw = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6, mtime=0)
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(gz)
    replace_with_retries(tmp_path, path)
    return len(raw), len(gz)


def main():
    parser = argparse.ArgumentParser(description="Chunk a descriptions.txt file into gzipped text chunks")
    parser.add_argument("input", help="Input description file (falls back to .gz)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as input)")
    parser.add_argument("--prefix", required=True, help="Output filename prefix")
    parser.add_argument("-n", "--chunks", type=int, required=True, help="Number of chunks to write")
    args = parser.parse_args()

    input_path = os.path.normpath(args.input)
    output_dir = os.path.normpath(args.output_dir or os.path.dirname(input_path) or ".")
    os.makedirs(output_dir, exist_ok=True)

    if args.chunks < 1:
        raise ValueError("--chunks must be at least 1")

    source, lines = load_lines(input_path)
    total = len(lines)
    chunk_size = max(1, math.ceil(total / args.chunks))

    print(f"Reading {source}...")
    print(f"  {total:,} description rows")
    print(f"  Splitting into {args.chunks} chunks of ~{chunk_size:,} rows...")

    remove_stale_chunks(output_dir, args.prefix)

    files = []
    total_raw = 0
    total_gz = 0
    for i in range(args.chunks):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        chunk = lines[start:end]
        filename = f"{args.prefix}_{i}.txt.gz"
        path = os.path.join(output_dir, filename)
        raw_size, gz_size = write_gzip(path, chunk)
        total_raw += raw_size
        total_gz += gz_size
        files.append(filename)
        print(f"  [{i}] {len(chunk):,} rows, {raw_size/1024/1024:.1f} MB raw -> {gz_size/1024/1024:.2f} MB gz")

    manifest = {
        "chunks": args.chunks,
        "totalRows": total,
        "files": files,
        "source": source.replace("\\", "/"),
    }
    manifest_path = os.path.join(output_dir, f"{args.prefix}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Total: {total_raw/1024/1024:.1f} MB raw -> {total_gz/1024/1024:.1f} MB gz")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
