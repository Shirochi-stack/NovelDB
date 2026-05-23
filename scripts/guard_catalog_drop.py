"""Fail a workflow when a scraped catalog unexpectedly shrinks.

This guard compares the current working-tree JSON catalog with the same file
from a git ref, usually HEAD before the scraper ran. It is meant to stop a
temporary upstream/API issue from being committed as a mass deletion.
"""

import argparse
import json
import subprocess
import sys


def count_json_rows_from_bytes(raw):
    return len(json.loads(raw.decode("utf-8")))


def count_previous(path, ref):
    try:
        raw = subprocess.check_output(["git", "show", f"{ref}:{path}"])
    except subprocess.CalledProcessError:
        return None
    return count_json_rows_from_bytes(raw)


def count_current(path):
    with open(path, "rb") as f:
        return count_json_rows_from_bytes(f.read())


def main():
    parser = argparse.ArgumentParser(
        description="Guard against accidental mass catalog shrinkage."
    )
    parser.add_argument("path", help="Catalog JSON file to check")
    parser.add_argument("--ref", default="HEAD", help="Git ref for previous data")
    parser.add_argument(
        "--max-drop-ratio",
        type=float,
        default=0.20,
        help="Maximum allowed drop from previous count, as a fraction",
    )
    parser.add_argument(
        "--min-previous",
        type=int,
        default=1000,
        help="Only enforce the ratio when the previous count is at least this",
    )
    args = parser.parse_args()

    previous = count_previous(args.path, args.ref)
    current = count_current(args.path)

    if previous is None:
        print(f"No previous {args.path} found at {args.ref}; current={current:,}")
        return

    drop = previous - current
    drop_ratio = drop / previous if previous else 0.0
    print(
        f"{args.path}: previous={previous:,}, current={current:,}, "
        f"drop={drop:,} ({drop_ratio:.1%})"
    )

    if (
        previous >= args.min_previous
        and drop > 0
        and drop_ratio > args.max_drop_ratio
    ):
        print(
            "Refusing to continue: catalog shrank more than "
            f"{args.max_drop_ratio:.0%}. If this is intentional, rerun with a "
            "higher --max-drop-ratio after inspecting the scrape output."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
