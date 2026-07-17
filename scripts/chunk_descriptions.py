"""Build deterministic, on-demand synopsis shards for the static website.

Each novel ID is assigned to a small gzip-compressed JSON object with the
numeric-modulo-v1 algorithm. The browser can therefore calculate the exact
file needed for a visible card without downloading a large ID index or every
description in the catalog.

Usage:
    python scripts/chunk_descriptions.py docs/data/descriptions.txt \
        --prefix descriptions_shard --output-dir docs/data -n 128
"""

import argparse
import glob
import gzip
import json
import os
import stat
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")


def open_text_with_gzip_fallback(path):
    if path.endswith(".gz"):
        if os.path.exists(path):
            return gzip.open(path, "rt", encoding="utf-8"), path
        return None, None
    gz_path = path + ".gz"
    if os.path.exists(path) and os.path.exists(gz_path):
        # Some scrapers update the compressed translation corpus directly.
        # Prefer it when it is newer so a stale plain-text export cannot drop rows.
        if os.path.getmtime(gz_path) > os.path.getmtime(path):
            return gzip.open(gz_path, "rt", encoding="utf-8"), gz_path
    if os.path.exists(path):
        return open(path, "r", encoding="utf-8"), path
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


def parse_delimited_row(line):
    first = line.find("|||")
    if first < 0:
        return line.strip(), "", ""
    novel_id = line[:first].strip()
    rest = line[first + 3 :]
    last = rest.rfind("|||")
    if last < 0:
        return novel_id, rest, ""
    return novel_id, rest[:last], rest[last + 3 :]


def has_cjk(text):
    return any(
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        or "\uac00" <= char <= "\ud7af"
        for char in text
    )


def is_valid_english(text):
    text = (text or "").strip()
    return bool(text) and not has_cjk(text) and any(char.isascii() and char.isalpha() for char in text)


def select_description(line):
    novel_id, original, english = parse_delimited_row(line)
    if not novel_id:
        return None
    original = original.strip()
    english = english.strip()
    text = english if is_valid_english(english) else original
    if not text or text == "N/A":
        return None
    return novel_id, text


def shard_index(novel_id, shard_count):
    """Mirror descriptionShardIndex() in docs/app.js."""
    try:
        return abs(int(novel_id)) % shard_count
    except ValueError:
        value = 0
        for char in novel_id:
            value = ((value * 31) + ord(char)) & 0xFFFFFFFF
        return value % shard_count


def remove_stale_shards(output_dir, prefix):
    for path in glob.glob(os.path.join(output_dir, f"{prefix}_*.json.gz")):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def write_gzip_json(path, descriptions):
    raw = json.dumps(descriptions, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as handle:
        handle.write(compressed)
    replace_with_retries(tmp_path, path)
    return len(raw), len(compressed)


def main():
    parser = argparse.ArgumentParser(description="Shard descriptions for on-demand browser loading")
    parser.add_argument("input", help="Input description file (falls back to .gz)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as input)")
    parser.add_argument("--prefix", required=True, help="Output filename prefix")
    parser.add_argument(
        "-n",
        "--shards",
        type=int,
        required=True,
        help="Number of deterministic shards to write",
    )
    args = parser.parse_args()

    input_path = os.path.normpath(args.input)
    output_dir = os.path.normpath(args.output_dir or os.path.dirname(input_path) or ".")
    os.makedirs(output_dir, exist_ok=True)

    if args.shards < 1:
        raise ValueError("--shards must be at least 1")

    handle, source = open_text_with_gzip_fallback(input_path)
    if not handle:
        raise FileNotFoundError(f"{input_path} not found, and no gzip fallback exists")

    buckets = [dict() for _ in range(args.shards)]
    total_rows = 0
    descriptions = 0
    print(f"Reading {source}...")
    with handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            total_rows += 1
            selected = select_description(line)
            if not selected:
                continue
            novel_id, text = selected
            buckets[shard_index(novel_id, args.shards)][novel_id] = text
            descriptions += 1

    print(f"  {total_rows:,} rows; {descriptions:,} usable descriptions")
    print(f"  Writing {args.shards} numeric-modulo-v1 shards...")
    remove_stale_shards(output_dir, args.prefix)

    width = max(3, len(str(args.shards - 1)))
    files = []
    total_raw = 0
    total_gzip = 0
    for index, bucket in enumerate(buckets):
        filename = f"{args.prefix}_{index:0{width}d}.json.gz"
        path = os.path.join(output_dir, filename)
        raw_size, gzip_size = write_gzip_json(path, bucket)
        total_raw += raw_size
        total_gzip += gzip_size
        files.append(filename)

    manifest = {
        "shards": args.shards,
        "totalRows": total_rows,
        "descriptions": descriptions,
        "algorithm": "numeric-modulo-v1",
        "format": "id-to-synopsis-json",
        "files": files,
        "source": source.replace("\\", "/"),
    }
    manifest_path = os.path.join(output_dir, f"{args.prefix}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    print(f"Total: {total_raw / 1024 / 1024:.1f} MB raw -> {total_gzip / 1024 / 1024:.1f} MB gz")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
