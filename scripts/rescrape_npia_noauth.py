"""Full Npia metadata rescrape with optional dedicated-account password login.

NPIA_METADATA_EMAIL and NPIA_METADATA_PASSWORD establish a fresh account session
once per run, without reading account configuration or requesting age verification.

Merges freshly scraped data into the existing novels.json, preserving every
existing unique novel ID that is not present in the latest scrape.

Merge strategy:
  - Novels found in fresh scrape -> update metadata (title, cover, views, etc.)
  - New novels not in existing data -> append
  - Existing novels NOT found in fresh scrape -> keep as-is

Also scrapes all six public top100 boards. Failed periods and the three
authenticated adult boards retain their existing ranks independently.

Usage:
    python scripts/rescrape_npia_noauth.py
    python scripts/rescrape_npia_noauth.py --dry-run
"""

try:
    from . import source_names
    from .npia_public_metadata import enrich
    from .metadata_common import atomic_json
    from .npia_rankings import scrape_ranking, collect_rankings, patch_ranks
except ImportError:
    import source_names
    from npia_public_metadata import enrich
    from metadata_common import atomic_json
    from npia_rankings import scrape_ranking, collect_rankings, patch_ranks


import sys, os, json, time, re, argparse, requests, uuid, copy, secrets
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from npia_search_http import PartialSearchError, paginated_search, get_json_with_retry as _search_json
from npia_author_catalog import author_id, parallel_author_pages
from npia_intro_discovery import discover as discover_intro
import npia_author_registry as author_registry
from npia_author_resolution import resolve_missing as resolve_author_identities

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

API_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/search",
}

COVER_PREFIX = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
IMAGE_COVER_PREFIX = "https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
DELETED_TAG = "deleted"
PLACEHOLDER_COVER_PARTS = ("readycover", "adult_cover_img")
from npia_search_terms import SEARCH_TAGS, SWEEP_CHARS

def pick_cover(item):
    """Pick the best cover URL from a Npia API response."""
    for k in ("novel_img_all", "novel_thumb_all", "cover_url", "novel_img", "novel_thumb"):
        v = item.get(k)
        if v and str(v) not in ("", "None", "null"):
            v = str(v)
            if v.startswith("//"):
                return "https:" + v
            if not v.startswith("http"):
                return COVER_PREFIX + v
            return v
    return ""


def is_real_cover(cover):
    cover = str(cover or "")
    return bool(cover) and not any(part in cover for part in PLACEHOLDER_COVER_PARTS)


def absolute_cover(cover):
    cover = str(cover or "")
    if cover.startswith("//"):
        return "https:" + cover
    if cover.startswith("/"):
        return IMAGE_COVER_PREFIX + cover
    return cover


def preserve_cover(fresh_cover, existing_site_cover="", existing_full_cover=""):
    if is_real_cover(fresh_cover):
        return fresh_cover
    if is_real_cover(existing_full_cover):
        return existing_full_cover
    if is_real_cover(existing_site_cover):
        return absolute_cover(existing_site_cover)
    return fresh_cover


def extract_novel(item):
    """Extract novel metadata dict from an API response item."""
    cover = pick_cover(item)
    tags = item.get("novel_genre_arr") or item.get("novel_genre") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = []
    return {
        "id": item.get("novel_no"),
        "title": item.get("novel_name", ""),
        "synopsis": item.get("novel_story", ""),
        "author": item.get("writer_nick", ""),
        "cover": cover,
        "tags": tags if isinstance(tags, list) else [],
        "author_id": author_id(item),
        "views": item.get("count_view", 0),
        "likes": item.get("count_good", 0),
        "chapters": item.get("count_book", 0),
        "complete": item.get("is_complete", 0),
        "age": item.get("novel_age", 0),
        "updated": item.get("last_viewdate", ""),
    }


def make_search_session(source=None):
    """Clone the run's authenticated session; workers never log in individually."""
    session = requests.Session()
    session.headers.update(HEADERS)
    if source is not None:
        session.headers.update(source.headers)
        session.cookies = copy.deepcopy(source.cookies)
        for name in ('auth', 'proxies', 'params', 'verify', 'cert', 'trust_env'):
            setattr(session, name, copy.copy(getattr(source, name)))
    return session


def login_metadata_session(session, *, retries=4, sleep=time.sleep):
    """Log in once with environment credentials, verifying the returned account state."""
    email = os.environ.get('NPIA_METADATA_EMAIL', '').strip()
    password = os.environ.get('NPIA_METADATA_PASSWORD', '')
    if not email and not password:
        return False
    if not email or not password:
        raise RuntimeError('Both metadata email and password secrets are required')
    for attempt in range(1, retries + 1):
        # This is a newly generated client key, not a stored account-session key.
        session.cookies.set('LOGINKEY', secrets.token_hex(16) + '_' + secrets.token_hex(16),
            domain='\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com', path='/', secure=True)
        try:
            response = session.post(COVER_PREFIX + '/proc/login',
                data={'redirectrurl': '', 'email': email, 'wd': password}, timeout=30,
                allow_redirects=False, headers={'Origin': COVER_PREFIX, 'Referer': COVER_PREFIX + '/login',
                                               'X-Requested-With': 'XMLHttpRequest'})
            if response.status_code == 429 or response.status_code >= 500 or not response.text.strip():
                raise ValueError('login service temporarily unavailable')
            if response.status_code not in (200, 302, 303):
                raise PermissionError('login request rejected')
            check = session.get(COVER_PREFIX + '/search', timeout=30, allow_redirects=False)
            if check.status_code == 429 or check.status_code >= 500 or not check.text.strip():
                raise ValueError('account verification temporarily unavailable')
            if check.status_code != 200 or not re.search(r'''\bmem_no\s*=\s*['"]?[1-9]\d*['"]?\s*;''', check.text):
                raise PermissionError('account session not established')
            return True
        except PermissionError:
            raise RuntimeError('Metadata account login was rejected or requires an interactive check') from None
        except Exception:
            # Never include response bodies, exception text, email, or password.
            print(f'Metadata login attempt {attempt}/{retries} temporarily failed', flush=True)
            if attempt == retries:
                raise RuntimeError('Metadata account login unavailable after retries') from None
            sleep(min(2 ** (attempt - 1), 8))


def get_json_with_retry(session, params, retries, timeout=120):
    return _search_json(session, params, API_HEADERS, retries, timeout)


def search_novels(session, search_val, retries, search_type="all"):
    """Read small search pages, recovering source memory failures by result offset."""
    params = {
        "cmd": "novel_search", "search_type": search_type, "search_val": search_val,
        "novel_type": "", "sort_col": "last_viewdate", "block_out": 0,
        "block_stop": 0, "is_contest": 0, "is_challenge": 0,
    }
    return paginated_search(lambda query: get_json_with_retry(session, query, retries), params)


def fetch_terms(label, terms, workers, retries, delay, session=None):
    results = {}
    workers = max(1, workers)
    print(f"--- {label}: {len(terms)} terms ({workers} workers, {delay}s stagger) ---", flush=True)

    def _collect_done(done, futures):
        for fut in done:
            i, term = futures.pop(fut)
            prefix = f"[{i+1}/{len(terms)}] {label}: {term!r}..."
            try:
                items = fut.result()
                results[term] = items
                print(f"{prefix} {len(items)} results", flush=True)
            except PartialSearchError as exc:
                results[term] = exc.items
                state = "PARTIAL" if exc.items else "FAILED"
                print(f"{prefix} {state}: retained {len(exc.items)} results; {exc}", flush=True)
            except Exception as e:
                results[term] = []
                print(f"{prefix} ERROR after retries: {e}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, term in enumerate(terms):
            client = make_search_session(session) if session is not None else make_search_session()
            future = pool.submit(search_novels, client, term, retries)
            futures[future] = (i, term)
            done, _ = wait(futures.keys(), timeout=0, return_when=FIRST_COMPLETED)
            _collect_done(done, futures)
            if delay > 0 and i < len(terms) - 1:
                time.sleep(delay)
                done, _ = wait(futures.keys(), timeout=0, return_when=FIRST_COMPLETED)
                _collect_done(done, futures)
        while futures:
            done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
            _collect_done(done, futures)
    return results


def merge_term_results(terms, results, fresh_novels):
    before = len(fresh_novels)
    raw_count = 0
    for term in terms:
        items = results.get(term, [])
        raw_count += len(items)
        for item in items:
            novel_id = item.get("novel_no")
            if not novel_id:
                continue
            fresh_novels.setdefault(str(novel_id), extract_novel(item))
    return raw_count, len(fresh_novels) - before


def enrich_author_catalogs(session, fresh, old_full, state, page_limit=None, delay=1.0, now=None, existing=(), registry=None, priority_authors=(), page_size=20, resume=True, seconds=0, workers=4, delay_max=None, retries=4, resolve_identities=False, registry_checkpoint=lambda: None):
    """Check the registered R19 subset, retaining partial pages for continuation."""
    now = time.time() if now is None else now
    deadline = time.monotonic() + seconds if seconds else None
    stopped = lambda: deadline is not None and time.monotonic() >= deadline
    # Zero retains the CLI's unlimited default. Positive limits are useful for
    # diagnostics; production continuations use a wall-clock save deadline.
    page_limit = page_limit or None
    pending = state.setdefault("author_pending", {})
    checked = state.setdefault("author_checked", {})
    sweep = state.get("author_sweep")
    if not resume or not sweep:
        sweep = state["author_sweep"] = dict(id=uuid.uuid4().hex, started=now,
                                             completed=[], failures={})
        if not resume:
            pending = state['author_pending'] = {owner: 1 for owner in pending}
    done = set(sweep["completed"])
    failures = sweep["failures"]
    previous_size = state.get('author_page_size', 20)
    if previous_size != page_size:
        # Resume at or before the old offset; changing batch sizes must never
        # skip records from a partially collected author.
        for owner, page in pending.items():
            pending[owner] = ((page - 1) * previous_size) // page_size + 1
    state['author_page_size'] = page_size
    compact = {str(row[0]): row for row in existing}
    working_registry = registry if registry is not None else {'version': 1, 'authors': {}}
    r19 = author_registry.refresh_r19(working_registry, existing,
                                       list(old_full.values()) + list(fresh.values()), now, live=fresh.values())
    maximum_delay = (2.0 if delay == 1.0 else delay) if delay_max is None else delay_max
    identity_failures = sweep.setdefault('resolution_failures', {})
    identity_stats = dict(requests=0)
    if resolve_identities:
        sweep['resolution_version'] = 1
        identity_stats = resolve_author_identities(session, working_registry, failures=identity_failures,
            workers=workers, delay=delay, delay_max=maximum_delay, retries=retries,
            max_requests=page_limit, stopped=stopped, checkpoint=registry_checkpoint,
            log=lambda message: print(message, flush=True))
        r19 = author_registry.refresh_r19(working_registry, existing,
                                           list(old_full.values()) + list(fresh.values()), now, live=fresh.values())
    eligible = set(r19['authors'])
    # Migrate old all-author checkpoints without querying general-only authors.
    pending = {owner: page for owner, page in pending.items() if owner in eligible}
    done.intersection_update(eligible)
    for owner in list(failures):
        if owner not in eligible:
            del failures[owner]
    for owner in eligible:
        if owner not in done and owner not in pending:
            pending[owner] = 1
    sweep['scope'] = 'r19'
    plan = {'authors': list(r19['authors'])}
    priority = list(dict.fromkeys(list(priority_authors) + plan['authors']))
    pending = {**{owner: pending[owner] for owner in priority if owner in pending}, **pending}
    state["author_pending"] = pending
    pages = updated = failed = completed = 0
    targeted = [owner for owner in priority if owner in pending]
    targeted_set = set(targeted)
    ordinary = [owner for owner in pending if owner not in targeted_set]
    targeted_pages = 0
    seen, r19_seen = set(), set()
    r19_hits = 0
    earlier_r19 = {nid for nid, novel in fresh.items() if str(novel.get('age')) == '19'}
    ordered = targeted + ordinary
    queue = {owner: pending[owner] for owner in ordered if failures.get(owner, 0) < 3}
    print(f'Checking {len(queue)} known R19 author catalogs with {workers} workers; '
          f'{delay:g}-{maximum_delay:g}s random delay; {retries} attempts per page', flush=True)
    for owner, page, rows, more, error, attempts in parallel_author_pages(
            session, queue, workers=workers, delay=delay, delay_max=maximum_delay,
            retries=retries, page_size=page_size,
            max_requests=None if page_limit is None else max(0, page_limit - identity_stats['requests']),
            stopped=stopped, log=lambda message: print(message, flush=True)):
        pages += attempts
        if owner in targeted_set:
            targeted_pages += attempts
        if error is not None:
            failed += 1
            failures[owner] = failures.get(owner, 0) + 1
            if registry is not None:
                registry['authors'][owner]['catalog_status'] = 'partial'
                registry['authors'][owner]['catalog_error'] = type(error).__name__
            pending[owner] = pending.pop(owner)
            print(f'Author {owner} PARTIAL; completed pages retained: {error}', flush=True)
            continue
        if rows is None:
            continue  # Deadline, cancellation, or diagnostic request budget.
        if registry is not None:
            entry = registry["authors"][owner]
            if page == 1:
                entry["catalog_novel_ids"] = []
            entry["catalog_status"] = "partial"
            entry["catalog_novel_ids"] = sorted(set(entry.get("catalog_novel_ids", [])) |
                                                 {str(item["novel_no"]) for item in rows}, key=int)
            author_registry.observe(registry, rows, now)
            author_registry.record_live_novels(registry, rows)
        for item in rows:
            nid = str(item["novel_no"])
            base = dict(old_full.get(nid) or {})
            row = compact.get(nid, [])
            for field, index in (("title", 1), ("author", 2), ("cover", 3), ("tags", 4),
                                 ("views", 5), ("likes", 6), ("chapters", 7),
                                 ("complete", 8), ("updated", 9), ("age", 11)):
                if len(row) > index:
                    base.setdefault(field, row[index])
            base.update(fresh.get(nid, {}))
            patch = extract_novel(item)
            patch["author_id"] = owner
            # Missing fields are unknown, never erase previously known metadata.
            fields = {"novel_name": "title", "novel_story": "synopsis", "writer_nick": "author",
                      "count_view": "views", "count_good": "likes", "count_book": "chapters",
                      "is_complete": "complete", "novel_age": "age", "last_viewdate": "updated"}
            for wire, field in fields.items():
                if wire not in item or item[wire] is None or item[wire] == "":
                    if field in base:
                        patch[field] = base[field]
            for field in ("cover", "tags"):
                if not patch[field] and base.get(field):
                    patch[field] = base[field]
            base.update(patch)
            fresh[nid] = base
            updated += 1
            seen.add(nid)
            if str(base.get('age')) == '19':
                r19_hits += 1
                r19_seen.add(nid)
        if more:
            pending[owner] = page + 1
        else:
            del pending[owner]
            checked[owner] = now
            done.add(owner)
            failures.pop(owner, None)
            completed += 1
            if registry is not None:
                entry["catalog_status"] = "complete"
                entry["last_catalog_checked"] = now
                entry.pop("catalog_error", None)
        print(f"Author catalogs: {completed} complete, {pages} requests, {updated} records updated", flush=True)
    if registry is not None:
        author_registry.refresh_r19(registry, existing, list(old_full.values()) + list(fresh.values()), now, live=fresh.values())
    unresolved = author_registry.unresolved_r19_groups(working_registry, identity_failures) if resolve_identities else []
    unavailable_groups = sum(not author_registry.identity_candidates(working_registry, ids)
                             for _, ids in unresolved)
    sweep.update(completed=sorted(done, key=int), total=len(done) + len(pending) + len(unresolved),
                 remaining=len(pending) + len(unresolved), remaining_catalogs=len(pending),
                 remaining_identities=len(unresolved), complete=not pending and not unresolved,
                 remaining_unavailable_identities=unavailable_groups,
                 continuation_required=any(failures.get(owner, 0) < 3 for owner in pending) or
                    any(author_registry.identity_candidates(working_registry, ids, identity_failures)
                        for _, ids in unresolved))
    state["author_last_run"] = dict(pages=pages, updated=updated, completed=completed, failed=failed,
                                    pending=len(pending), unique_updated=len(seen), r19_hits=r19_hits,
                                    r19_unique=len(r19_seen), r19_new=len(r19_seen - compact.keys() - old_full.keys()),
                                    r19_already_refreshed=len(r19_seen & earlier_r19), targeted_pages=targeted_pages)
    state['author_last_run']['identity_requests'] = identity_stats['requests']
    print(f"Author R19 metadata: {len(r19_seen)} unique, {r19_hits} hits, "
          f"{state['author_last_run']['r19_new']} new IDs, {len(r19_seen & earlier_r19)} already refreshed; "
          f"{targeted_pages}/{pages} requests targeted R19", flush=True)
    print(f"Author sweep: {len(done)}/{sweep['total']} complete; {len(pending)} catalogs and {len(unresolved)} identities remaining "
          f"({len(failures)} failed authors). "
          + ("All registered R19 authors checked." if sweep['complete'] else
             "Continuation required to finish this sweep." if sweep['continuation_required'] else
             f"INCOMPLETE: {unavailable_groups} groups have only unavailable novels; existing metadata retained. "
             "Other failed authors retained for the next sweep."), flush=True)
    return state


def main():
    parser = argparse.ArgumentParser(description="Rescrape Npia metadata with optional email/password login")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run scrape and show merge summary without saving")
    parser.add_argument("--search-workers", type=int, default=4,
                        help="Parallel workers for tag/sweep search")
    parser.add_argument("--search-retries", type=int, default=4,
                        help="Retries per search page")
    parser.add_argument("--search-delay", type=float, default=0.5,
                        help="Seconds to stagger parallel search submissions")
    parser.add_argument("--r19-listing-pages", type=int, default=0)
    parser.add_argument("--r19-detail-limit", type=int, default=0)
    parser.add_argument("--r19-delay", type=float, default=2.0)
    parser.add_argument("--author-pages", type=int, default=0,
                        help="Optional diagnostic request limit; 0 checks every registered R19 author (default)")
    parser.add_argument("--author-seconds", type=float, default=0,
                        help="Save and pause author collection after this many seconds; 0 is unlimited")
    parser.add_argument("--author-sweep-id", default="",
                        help="Continue this saved sweep without repeating searches or completed authors")
    parser.add_argument("--author-continuation-file",
                        help="Write the workflow continuation decision after saving metadata")
    parser.add_argument("--author-delay", type=float, default=1.0)
    parser.add_argument('--author-delay-max', type=float, default=None,
                        help='Maximum random per-worker delay (default: 2 seconds)')
    parser.add_argument('--author-workers', type=int, default=4)
    parser.add_argument('--author-retries', type=int, default=4,
                        help='Total attempts per author page, for any request or response error')
    parser.add_argument('--author-page-size', type=int, default=100,
                        help='Novels per public author request (1-100)')
    parser.add_argument("--intro-requests", type=int, default=6,
                        help="Maximum requests for public R19 author discovery per run")
    args = parser.parse_args()
    if min(args.r19_listing_pages, args.r19_detail_limit, args.r19_delay,
           args.author_pages, args.author_delay, args.author_seconds, args.intro_requests) < 0:
        parser.error("R19 request budgets and delay must be nonnegative")
    if not 1 <= args.author_page_size <= 100:
        parser.error('Author page size must be between 1 and 100')
    if args.author_workers < 1 or args.author_retries < 1:
        parser.error('Author workers and attempts must be positive')
    if args.author_delay_max is not None and args.author_delay_max < args.author_delay:
        parser.error('Maximum author delay must be at least the minimum delay')

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_path = os.path.join(base_dir, "docs", "data", "novels.json")
    full_path = os.path.join(base_dir, "docs", "data", "novels_full.json")
    public_state_path = os.path.join(base_dir, "docs", "data", "npia_public_state.json")
    registry_path = os.path.join(base_dir, "docs", "data", "npia_authors.json")
    registry = author_registry.load(registry_path)
    public_state = {}
    if os.path.exists(public_state_path):
        with open(public_state_path, encoding="utf-8") as handle:
            public_state = json.load(handle)
    if args.author_sweep_id and public_state.get('author_sweep', {}).get('id') != args.author_sweep_id:
        parser.error('Saved author sweep has changed; refusing a stale continuation')

    session = make_search_session()

    print('Initializing metadata session...')
    authenticated = login_metadata_session(session)
    print('Metadata account login verified.' if authenticated else 'Anonymous metadata session.', flush=True)
    try:
        session.get("https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com", timeout=15)
    except Exception:
        pass

    # ── Phase 1: Tag search ──────────────────────────────────────
    fresh_novels = {}  # id -> novel dict

    rankings = {}
    search_ids = set(public_state.get('author_search_ids', []))
    if not args.author_sweep_id:
        tag_results = fetch_terms(
            "Searching", SEARCH_TAGS,
            args.search_workers, args.search_retries, args.search_delay, session,
        )
        tag_total, tag_new = merge_term_results(SEARCH_TAGS, tag_results, fresh_novels)
        print(
            f"\n--- Phase 1 complete: {len(fresh_novels)} novels from tags "
            f"({tag_total} raw results, {tag_new} unique) ---\n"
        )

        # ── Phase 2: Character sweep ─────────────────────────────────
        sweep_results = fetch_terms(
            "Sweep", SWEEP_CHARS,
            args.search_workers, args.search_retries, args.search_delay, session,
        )
        sweep_total, sweep_new = merge_term_results(SWEEP_CHARS, sweep_results, fresh_novels)
        print(
            f"\n--- Phase 2 complete: {len(fresh_novels)} total scraped novels "
            f"({sweep_total} raw sweep results, {sweep_new} new unique) ---\n"
        )
        search_ids = set(fresh_novels)
        author_registry.record_search(registry, fresh_novels.values())

        # ── Phase 3: Scrape rankings ─────────────────────────────────
        print("--- Scraping rankings ---")
        rankings = collect_rankings(session, fetch=scrape_ranking, log=lambda message: print(message, flush=True))

        public_state['author_search_ids'] = sorted(search_ids, key=int)

    # ── Phase 4: Merge into existing data ────────────────────────
    print(f"\n--- Merging into existing data ---")

    if not os.path.exists(data_path):
        print(f"No existing {data_path} — creating fresh dataset")
        existing_data = []
    else:
        with open(data_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
        print(f"Loaded {len(existing_data)} existing novels")

    old_full_lookup = {}
    if os.path.exists(full_path):
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                old_full = json.load(f)
            for entry in old_full:
                oid = str(entry.get("id", ""))
                if oid:
                    old_full_lookup[oid] = entry
        except Exception as e:
            print(f"  Warning: Could not load old full data for cover preservation: {e}")

    author_registry.seed_catalog_names(registry, existing_data)
    intro_authors = set()
    if not args.author_sweep_id:
        if args.r19_listing_pages or args.r19_detail_limit:
            print("\n--- Refreshing public R19 metadata (no auth) ---", flush=True)
            plan = author_registry.plan_r19_requests(registry, existing_data, old_full_lookup.values(),
                                                   checked=public_state.get('checked', {}))
            print(f"R19 request plan: {plan['confirmed_ids']}/{plan['r19_total']} verified author mappings; "
                  f"{len(plan['authors'])} candidate authors; {plan['unresolved_groups']} unresolved author groups", flush=True)
            public_state = enrich(session, fresh_novels, existing_data, old_full_lookup, public_state,
                                  listing_pages=args.r19_listing_pages,
                                  detail_limit=args.r19_detail_limit, delay=args.r19_delay, detail_ids=plan['detail_ids'])
        else:
            print('R19 refresh: author catalogs and targeted missing-author ID resolution; individual metadata refresh disabled.', flush=True)
        print("\n--- Checking public author catalogs (no auth) ---", flush=True)
        intro_rows = discover_intro(session, public_state, max_requests=args.intro_requests,
                                    delay=args.author_delay)
        intro_authors = author_registry.observe(registry, intro_rows)
        author_registry.record_live_novels(registry, intro_rows)
    public_state = enrich_author_catalogs(session, fresh_novels, old_full_lookup, public_state,
                                          page_limit=args.author_pages, delay=args.author_delay,
                                          existing=existing_data, registry=registry,
                                          priority_authors=sorted(intro_authors, key=int), page_size=args.author_page_size,
                                          resume=bool(args.author_sweep_id), seconds=args.author_seconds,
                                          workers=args.author_workers, delay_max=args.author_delay_max,
                                          retries=args.author_retries, resolve_identities=True,
                                          registry_checkpoint=(lambda: None) if args.dry_run else
                                              lambda: author_registry.save(registry_path, registry))
    author_registry.report_coverage(registry, search_ids)

    # Build lookup: novel_id_str -> index in existing_data
    existing_lookup = {}
    for idx, novel in enumerate(existing_data):
        nid = str(novel[0])
        existing_lookup[nid] = idx

    stats = {
        "updated": 0,
        "added": 0,
        "r19_refreshed": sum(str(n.get('age')) == '19' for n in fresh_novels.values()),
        "r19_added": sum(str(n.get('age')) == '19' and nid not in existing_lookup for nid, n in fresh_novels.items()),
        "preserved_r19": 0,
        "preserved_other": 0,
        "deleted_tagged": 0,
    }

    def to_relative_cover(cover):
        """Strip the cover prefix for storage."""
        cover = str(cover or "")
        for prefix in (COVER_PREFIX, IMAGE_COVER_PREFIX):
            if cover.startswith(prefix):
                return cover[len(prefix):]
        return cover

    def get_rank(nid, audience, period):
        return rankings.get((audience, period), {}).get(nid, 0)

    # Update existing novels or add new ones
    preserved_cover_updates = 0
    for nid, novel in fresh_novels.items():
        existing_site_cover = ""
        if nid in existing_lookup:
            existing_entry = existing_data[existing_lookup[nid]]
            if len(existing_entry) > 3:
                existing_site_cover = existing_entry[3]
        existing_full_cover = (old_full_lookup.get(nid) or {}).get("cover", "")
        cover_for_full = preserve_cover(novel["cover"], existing_site_cover, existing_full_cover)
        if cover_for_full != novel["cover"]:
            novel["cover"] = cover_for_full
            preserved_cover_updates += 1
        cover = to_relative_cover(novel["cover"])

        if nid in existing_lookup:
            # Update existing entry's metadata
            idx = existing_lookup[nid]
            entry = existing_data[idx]

            # Ensure array is long enough
            while len(entry) < 20:
                entry.append(0)

            entry[1] = novel["title"]
            entry[2] = novel["author"]
            entry[3] = cover
            entry[4] = novel["tags"]
            entry[5] = novel["views"]
            entry[6] = novel["likes"]
            entry[7] = novel["chapters"]
            entry[8] = novel["complete"]
            entry[9] = novel["updated"]
            entry[11] = novel["age"]
            stats["updated"] += 1
        else:
            # New novel — append in array format
            entry = [
                novel["id"],              # [0]  id
                novel["title"],           # [1]  title
                novel["author"],          # [2]  author
                cover,                    # [3]  cover
                novel["tags"],            # [4]  tags
                novel["views"],           # [5]  views
                novel["likes"],           # [6]  likes
                novel["chapters"],        # [7]  chapters
                novel["complete"],        # [8]  complete
                novel["updated"],         # [9]  updated
                0,                        # [10] weeklyRank (all)
                novel["age"],             # [11] age rating
                0,                        # [12] monthlyRank (all)
                0,                        # [13] dailyRank (all)
                0,                        # [14] weeklyRankAdult
                0,                        # [15] monthlyRankAdult
                0,                        # [16] dailyRankAdult
                0,                        # [17] weeklyRankTeen
                0,                        # [18] monthlyRankTeen
                0,                        # [19] dailyRankTeen
            ]
            existing_data.append(entry)
            existing_lookup[nid] = len(existing_data) - 1
            stats["added"] += 1

    # ── Guard against a partial scrape mass-flagging live novels ─────
    # A no-auth scrape legitimately misses R19 novels, and novels already
    # tagged "deleted" are expected to stay missing. Measure how many
    # *currently-live* non-R19 novels vanished this run: if that exceeds
    # MAX_MISSING_RATIO the scrape is almost certainly partial/broken, so
    # preserve rows untouched instead of flagging real novels as deleted.
    # (Mirrors the intent of scripts/guard_catalog_drop.py, but checks scrape
    # coverage rather than row count — rescrape never drops rows, only tags.)
    MAX_MISSING_RATIO = 0.20
    MIN_CATALOG = 1000
    live_total = live_missing = 0
    for nid in existing_lookup:
        entry = existing_data[existing_lookup[nid]]
        age = entry[11] if len(entry) > 11 else 0
        if age == 19:
            continue
        tags = entry[4] if len(entry) > 4 and isinstance(entry[4], list) else []
        if DELETED_TAG in tags:
            continue  # already deleted -> expected to stay missing
        live_total += 1
        if nid not in fresh_novels:
            live_missing += 1
    missing_ratio = live_missing / live_total if live_total else 0.0
    skip_delete_tagging = bool(args.author_sweep_id) or (live_total >= MIN_CATALOG and missing_ratio > MAX_MISSING_RATIO)
    if skip_delete_tagging and not args.author_sweep_id:
        print(
            f"\n  GUARD TRIPPED: only {live_total - live_missing:,}/{live_total:,} "
            f"live non-R19 novels were re-found "
            f"({missing_ratio:.1%} missing > {MAX_MISSING_RATIO:.0%} limit).\n"
            f"  Scrape looks partial -> SKIPPING 'deleted' tagging this run. "
            f"Metadata and rankings are still updated."
        )

    # Count preserved novels (ones in existing data but NOT in fresh scrape)
    for nid in existing_lookup:
        if nid not in fresh_novels:
            # Check if it's an R19 novel (age == 19 at index 11)
            idx = existing_lookup[nid]
            entry = existing_data[idx]
            while len(entry) < 20:
                entry.append(0)
            age = entry[11] if len(entry) > 11 else 0
            if age == 19:
                stats["preserved_r19"] += 1
            else:
                if not skip_delete_tagging:
                    tags = entry[4] if len(entry) > 4 and isinstance(entry[4], list) else []
                    if DELETED_TAG not in tags:
                        tags.append(DELETED_TAG)
                        stats["deleted_tagged"] += 1
                    entry[4] = tags
                stats["preserved_other"] += 1

    # Explicit source deletion notices also apply to retained R19 rows.
    stats['deleted_tagged'] += author_registry.preserve_deleted_tags(registry, existing_data)['compact']

    # ── Phase 5: Patch rankings ──────────────────────────────────
    rank_changes = sum(patch_ranks(novel, rankings) for novel in existing_data)

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  Total novels in dataset: {len(existing_data)}")
    print(f"  Freshly scraped:         {len(fresh_novels)}")
    print(f"  Updated (existing):      {stats['updated']}")
    print(f"  Added (new):             {stats['added']}")
    print(f"  R19 refreshed (unique):  {stats['r19_refreshed']}")
    print(f"  R19 new IDs:             {stats['r19_added']}")
    print(f"  Preserved (R19):         {stats['preserved_r19']}")
    print(f"  Preserved (other):       {stats['preserved_other']}")
    print(f"  Tagged deleted:          {stats['deleted_tagged']}")
    print(f"  Preserved cover URLs:    {preserved_cover_updates}")
    print(f"  Rank changes:            {rank_changes}")
    print(f"{'='*50}")

    if args.dry_run:
        print("\n*** DRY RUN — no files written ***")
        return

    # ── Save ─────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(data_path), exist_ok=True)

    # Save optimized version (array format, no synopsis)
    with open(data_path, "w", encoding="utf-8") as f:
        source_names.dump(existing_data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nSaved {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")

    # Save full version with synopses for description extraction
    full_novels = []
    for novel in fresh_novels.values():
        full_novels.append(novel)

    # Also include every old-only full entry. A scrape result is an update set,
    # not a deletion set; missing IDs keep their last known synopsis data.
    if old_full_lookup:
        try:
            preserved_full = 0
            full_ids = {str(n["id"]) for n in full_novels}
            for oid, entry in old_full_lookup.items():
                if oid not in full_ids:
                    full_novels.append(entry)
                    preserved_full += 1
            if preserved_full:
                print(
                    f"  Preserved {preserved_full} old-only novels in "
                    "novels_full.json"
                )
        except Exception as e:
            print(f"  Warning: Could not merge old full data: {e}")

    author_registry.preserve_deleted_tags(registry, existing_data, full_novels)
    with open(full_path, "w", encoding="utf-8") as f:
        source_names.dump(full_novels, f, ensure_ascii=False)
    print(f"Saved {full_path} ({os.path.getsize(full_path) / 1024 / 1024:.1f} MB)")
    atomic_json(public_state_path, public_state)
    author_registry.save(registry_path, registry)
    if args.author_continuation_file:
        atomic_json(args.author_continuation_file, public_state['author_sweep'])


if __name__ == "__main__":
    main()
