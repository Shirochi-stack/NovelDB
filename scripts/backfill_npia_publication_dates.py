"""Backfill source-declared npia publication dates without changing other metadata.

Uses the normal public search sweep and optional ranked-author catalogs. A local
checkpoint retains dates and completed requests so an interrupted run can resume.
Rebuild catalog chunks, previews and top rankings after updating novels.json.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from npia_author_catalog import iter_author_pages
from npia_search_http import PartialSearchError, paginated_search
from npia_search_terms import ALL_SEARCH_TERMS
from scripts.metadata_common import atomic_json
from scripts.publication_dates import explicit_publication_date, normalize_publication_date
from scripts.rescrape_npia_noauth import make_search_session, get_json_with_retry, login_metadata_session


def collect_dates(items, dates):
    for item in items:
        nid = str(item.get('novel_no') or '')
        date = explicit_publication_date(item, 'start_date', 'novel_regdate', 'first_viewdate', 'regdate', 'created_at')
        if nid.isdigit() and date:
            dates[nid] = date


def apply_dates(rows, dates):
    """Fill missing dates only; keep every original row and all other fields."""
    changed = 0
    for row in rows:
        if len(row) > 20 and normalize_publication_date(row[20]):
            continue
        date = normalize_publication_date(dates.get(str(row[0])))
        if not date:
            continue
        row.extend([''] * max(0, 21 - len(row)))
        row[20] = date
        changed += 1
    return changed


def search_dates(term, delay, retries, source_session=None):
    with make_search_session(source_session) as session:
        def fetch(params):
            time.sleep(delay)
            return get_json_with_retry(session, params, retries)
        params = dict(cmd='novel_search', search_type='all', search_val=term,
                      novel_type='', sort_col='last_viewdate', block_out=0,
                      block_stop=0, is_contest=0, is_challenge=0)
        try:
            items = paginated_search(fetch, params)
            error = None
        except PartialSearchError as exc:
            items, error = exc.items, str(exc)
        dates = {}
        collect_dates(items, dates)
        return dates, error


def ranked_authors(rows, registry, dates, limit, adult_only=False):
    ranks = (10, 12, 13, 14, 15, 16, 17, 18, 19)
    missing = {str(row[0]): min((row[i] for i in ranks if len(row) > i and row[i] > 0), default=999999)
               for row in rows if not (len(row) > 20 and row[20]) and str(row[0]) not in dates
               and (not adult_only or (row[11] == 19 and 'deleted' not in row[4]))}
    scored = []
    for owner, entry in registry.get('authors', {}).items():
        relevant = [missing[nid] for nid in entry.get('novel_ids', []) if nid in missing]
        if relevant:
            scored.append((min(relevant), -len(relevant), owner))
    return [owner for _, _, owner in sorted(scored)[:limit]]


def author_dates(owner, delay, retries, source_session=None):
    dates = {}
    try:
        with make_search_session(source_session) as session:
            for _, items, _ in iter_author_pages(session, owner, page_size=100, delay=delay, retries=retries):
                collect_dates(items, dates)
        return dates, None
    except Exception as exc:
        return dates, type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'docs/data')
    parser.add_argument('--checkpoint', type=Path, default=ROOT / '.cache/npia-publication-dates.json')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--delay', type=float, default=1)
    parser.add_argument('--retries', type=int, default=4)
    parser.add_argument('--author-limit', type=int, default=0)
    parser.add_argument('--skip-search', action='store_true', help='Use author catalogs without repeating the search sweep')
    parser.add_argument('--adult-authors', action='store_true', help='Target missing dates in live R19 entries only')
    args = parser.parse_args()
    if args.workers < 1 or args.delay < 0 or args.retries < 1 or args.author_limit < 0:
        parser.error('Invalid request limits')
    rows = json.loads((args.data_dir / 'novels.json').read_text(encoding='utf-8'))
    state = json.loads(args.checkpoint.read_text(encoding='utf-8')) if args.checkpoint.exists() else {}
    dates = state.setdefault('dates', {})
    state.setdefault('completed_terms', [])
    state.setdefault('completed_authors', [])
    state.setdefault('failures', {})
    session = make_search_session()
    authenticated = login_metadata_session(session)
    print('Metadata account login verified.' if authenticated else 'Anonymous metadata session.', flush=True)

    def run(values, worker, completed_key, label):
        pending = [value for value in values if value not in state[completed_key]]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(worker, value, args.delay, args.retries, session): value for value in pending}
            for future in as_completed(futures):
                value = futures[future]
                try:
                    found, error = future.result()
                except Exception as exc:
                    found, error = {}, type(exc).__name__
                dates.update(found)
                key = f'{label}:{value}'
                if error:
                    state['failures'][key] = error
                else:
                    state[completed_key].append(value)
                    state['failures'].pop(key, None)
                atomic_json(args.checkpoint, state)
                print(f'{label}: {len(state[completed_key])}/{len(values)} complete; '
                      f'{len(dates)} dates retained; latest {"partial" if error else "success"}', flush=True)

    if not args.skip_search:
        run(ALL_SEARCH_TERMS, search_dates, 'completed_terms', 'search')
    if args.author_limit:
        registry = json.loads((args.data_dir / 'npia_authors.json').read_text(encoding='utf-8'))
        owners = ranked_authors(rows, registry, dates, args.author_limit, args.adult_authors)
        run(owners, author_dates, 'completed_authors', 'author')
    # Re-read before writing so another metadata step's changes are retained.
    rows = json.loads((args.data_dir / 'novels.json').read_text(encoding='utf-8'))
    changed = apply_dates(rows, dates)
    if changed:
        atomic_json(args.data_dir / 'novels.json', rows)
    known = sum(len(row) > 20 and bool(row[20]) for row in rows)
    print(f'Updated {changed} publication dates; {known}/{len(rows)} catalog entries have dates; '
          f'{len(state["failures"])} requests need retry.', flush=True)
    if state['failures']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
