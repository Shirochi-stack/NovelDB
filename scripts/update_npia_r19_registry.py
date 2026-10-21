"""Refresh the R19 registry locally; optionally resolve missing IDs without account config."""

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import npia_author_registry as authors
from npia_author_resolution import resolve_missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=ROOT / 'docs/data/npia_authors.json')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'docs/data/novels.json')
    parser.add_argument('--full', type=Path, default=ROOT / 'docs/data/novels_full.json')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resolve', action='store_true', help='Resolve missing R19 author IDs through public known novel pages')
    parser.add_argument('--max-requests', type=int, default=0, help='Optional HTTP attempt limit; 0 resolves one novel per unverified name group')
    parser.add_argument('--seconds', type=float, default=0, help='Optional time limit for public identity requests')
    args = parser.parse_args()
    registry = authors.load(args.registry)
    with args.catalog.open(encoding='utf-8') as source:
        rows = json.load(source)
    full = []
    if args.full.exists():
        with args.full.open(encoding='utf-8') as source:
            full = json.load(source)
    subset = authors.refresh_r19(registry, rows, full)
    if args.max_requests < 0 or args.seconds < 0:
        parser.error('Request and time limits must be nonnegative')
    if args.resolve:
        import requests
        session = requests.Session()
        session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        deadline = time.monotonic() + args.seconds if args.seconds else None
        try:
            resolve_missing(session, registry, max_requests=args.max_requests or None,
                            stopped=lambda: deadline is not None and time.monotonic() >= deadline,
                            checkpoint=(lambda: None) if args.dry_run else lambda: authors.save(args.registry, registry))
        finally:
            session.close()
        subset = authors.refresh_r19(registry, rows, full)
    if not args.dry_run:
        authors.save(args.registry, registry)
    print(f"R19 registry: {subset['novel_count']} novels; {subset['author_name_count']} author names; "
          f"{subset['verified_author_count']} verified author IDs; "
          f"{subset['name_matched_author_count']} name-matched author IDs; "
          f"{len(subset['unresolved_names'])} unresolved name groups.")
    print(('Public known-novel identity requests only; no account config access.' if args.resolve else
           'Saved metadata only; no HTTP requests or account config access.'))


if __name__ == '__main__':
    main()
