"""Refresh the R19 author subset from saved metadata, without HTTP or account config."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import npia_author_registry as authors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=ROOT / 'docs/data/npia_authors.json')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'docs/data/novels.json')
    parser.add_argument('--full', type=Path, default=ROOT / 'docs/data/novels_full.json')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    registry = authors.load(args.registry)
    with args.catalog.open(encoding='utf-8') as source:
        rows = json.load(source)
    full = []
    if args.full.exists():
        with args.full.open(encoding='utf-8') as source:
            full = json.load(source)
    subset = authors.refresh_r19(registry, rows, full)
    if not args.dry_run:
        authors.save(args.registry, registry)
    print(f"R19 registry: {subset['novel_count']} novels; {subset['author_name_count']} author names; "
          f"{subset['verified_author_count']} verified author IDs; "
          f"{subset['name_matched_author_count']} name-matched author IDs; "
          f"{len(subset['unresolved_names'])} unresolved name groups.")
    print('Saved metadata only; no HTTP requests or account config access.')


if __name__ == '__main__':
    main()
