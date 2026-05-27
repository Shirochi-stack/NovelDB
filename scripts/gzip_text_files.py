"""Deterministically gzip text/data files for web serving."""

import gzip
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")


def gzip_file(path):
    if not os.path.exists(path):
        print(f"  Skipping missing {path}")
        return
    with open(path, "rb") as src:
        raw = src.read()
    out_path = path + ".gz"
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "wb") as dst:
        dst.write(gzip.compress(raw, compresslevel=6, mtime=0))
    os.replace(tmp_path, out_path)
    print(f"  Wrote {out_path} ({os.path.getsize(out_path) / 1024:.1f} KB)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/gzip_text_files.py <file> [<file> ...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        gzip_file(path)


if __name__ == "__main__":
    main()
