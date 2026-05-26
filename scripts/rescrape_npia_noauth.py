"""Full Novelpia catalog rescrape WITHOUT authentication.

Merges freshly scraped data into the existing novels.json, preserving every
existing unique novel ID that is not present in the latest scrape.

Merge strategy:
  - Novels found in fresh scrape -> update metadata (title, cover, views, etc.)
  - New novels not in existing data -> append
  - Existing novels NOT found in fresh scrape -> keep as-is

Also scrapes public top100 rankings. For audiences that fail (adult/R19),
existing ranks are preserved.

Usage:
    python scripts/rescrape_npia_noauth.py
    python scripts/rescrape_npia_noauth.py --dry-run
"""

import sys, os, json, time, re, argparse, requests
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

API_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://novelpia.com/search",
}

COVER_PREFIX = "https://novelpia.com"
IMAGE_COVER_PREFIX = "https://images.novelpia.com"
DELETED_TAG = "deleted"
PLACEHOLDER_COVER_PARTS = ("readycover", "adult_cover_img")
from novelpia_search_terms import RETRYABLE_STATUS_CODES, SEARCH_TAGS, SWEEP_CHARS

AUDIENCES = [
    ("all/plus",   "all",   10, 12, 13),
    ("adult/plus", "adult", 14, 15, 16),
    ("teen/plus",  "teen",  17, 18, 19),
]

PERIODS = [
    ("weekly", "weekly"),
    ("month",  "monthly"),
    ("today",  "daily"),
]


def pick_cover(item):
    """Pick the best cover URL from a Novelpia API response."""
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
    return {
        "id": item.get("novel_no"),
        "title": item.get("novel_name", ""),
        "synopsis": item.get("novel_story", ""),
        "author": item.get("writer_nick", ""),
        "cover": cover,
        "tags": item.get("novel_genre_arr") or [],
        "views": item.get("count_view", 0),
        "likes": item.get("count_good", 0),
        "chapters": item.get("count_book", 0),
        "complete": item.get("is_complete", 0),
        "age": item.get("novel_age", 0),
        "updated": item.get("last_viewdate", ""),
    }


def make_search_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_json_with_retry(session, params, retries, timeout=120):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(
                "https://novelpia.com/proc/novel",
                params=params,
                headers=API_HEADERS,
                timeout=timeout,
            )
            if r.status_code in RETRYABLE_STATUS_CODES:
                raise RuntimeError(f"HTTP {r.status_code}")
            text = r.text.strip()
            if not text:
                return None
            return r.json()
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise last_error


def search_novels(session, search_val, retries, search_type="all"):
    """Search Novelpia API for novels matching a search term."""
    items = []
    ROWS = 30000
    for pg in range(1, 21):
        data = get_json_with_retry(session, {
            "cmd": "novel_search",
            "search_type": search_type,
            "search_val": search_val,
            "page": pg,
            "rows": ROWS,
            "novel_type": "",
            "sort_col": "last_viewdate",
            "block_out": 0,
            "block_stop": 0,
            "is_contest": 0,
            "is_challenge": 0,
        }, retries)
        if not data:
            break
        batch = data.get("list", [])
        items.extend(batch)
        if len(batch) < ROWS:
            break
    return items


def fetch_terms(label, terms, workers, retries, delay):
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
            except Exception as e:
                results[term] = []
                print(f"{prefix} ERROR after retries: {e}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, term in enumerate(terms):
            future = pool.submit(search_novels, make_search_session(), term, retries)
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


def scrape_ranking(session, period, audience):
    """Scrape top100 for a given period and audience."""
    url = f"https://novelpia.com/top100/all/{period}/view/{audience}"
    r = session.get(url, timeout=30)
    if r.status_code != 200:
        return {}
    rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
    ranking = {}
    for pos, nid in enumerate(rank_ids[:100], 1):
        ranking[nid] = pos
    return ranking


def main():
    parser = argparse.ArgumentParser(description="Rescrape Novelpia catalog (no auth)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run scrape and show merge summary without saving")
    parser.add_argument("--search-workers", type=int, default=4,
                        help="Parallel workers for tag/sweep search")
    parser.add_argument("--search-retries", type=int, default=4,
                        help="Retries per search page")
    parser.add_argument("--search-delay", type=float, default=0.5,
                        help="Seconds to stagger parallel search submissions")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_path = os.path.join(base_dir, "docs", "data", "novels.json")
    full_path = os.path.join(base_dir, "docs", "data", "novels_full.json")

    session = requests.Session()
    session.headers.update(HEADERS)

    print("Initializing session...")
    try:
        session.get("https://novelpia.com", timeout=15)
    except Exception:
        pass

    # ── Phase 1: Tag search ──────────────────────────────────────
    fresh_novels = {}  # id -> novel dict

    tag_results = fetch_terms(
        "Searching", SEARCH_TAGS,
        args.search_workers, args.search_retries, args.search_delay,
    )
    tag_total, tag_new = merge_term_results(SEARCH_TAGS, tag_results, fresh_novels)
    print(
        f"\n--- Phase 1 complete: {len(fresh_novels)} novels from tags "
        f"({tag_total} raw results, {tag_new} unique) ---\n"
    )

    # ── Phase 2: Character sweep ─────────────────────────────────
    sweep_results = fetch_terms(
        "Sweep", SWEEP_CHARS,
        args.search_workers, args.search_retries, args.search_delay,
    )
    sweep_total, sweep_new = merge_term_results(SWEEP_CHARS, sweep_results, fresh_novels)
    print(
        f"\n--- Phase 2 complete: {len(fresh_novels)} total scraped novels "
        f"({sweep_total} raw sweep results, {sweep_new} new unique) ---\n"
    )

    # ── Phase 3: Scrape rankings ─────────────────────────────────
    print("--- Scraping rankings ---")
    rankings = {}
    failed_audiences = set()

    for audience_url, audience_label, _, _, _ in AUDIENCES:
        for period_url, period_label in PERIODS:
            label = f"{period_label} {audience_label}"
            print(f"Scraping {label}...", end=" ", flush=True)
            try:
                ranking = scrape_ranking(session, period_url, audience_url)
            except Exception as e:
                print(f"WARNING: Failed: {e}")
                ranking = {}
            if len(ranking) == 0:
                print("(may require auth — skipping)")
                failed_audiences.add(audience_url)
            else:
                print(f"{len(ranking)} novels")
            rankings[(audience_url, period_url)] = ranking

    if "adult/plus" in failed_audiences:
        print("\nR19 (adult) ranking pages unavailable — existing adult ranks will be preserved")

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

    # Build lookup: novel_id_str -> index in existing_data
    existing_lookup = {}
    for idx, novel in enumerate(existing_data):
        nid = str(novel[0])
        existing_lookup[nid] = idx

    stats = {
        "updated": 0,
        "added": 0,
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
                tags = entry[4] if len(entry) > 4 and isinstance(entry[4], list) else []
                if DELETED_TAG not in tags:
                    tags.append(DELETED_TAG)
                    stats["deleted_tagged"] += 1
                entry[4] = tags
                stats["preserved_other"] += 1

    # ── Phase 5: Patch rankings ──────────────────────────────────
    rank_changes = 0
    for novel in existing_data:
        nid = str(novel[0])
        while len(novel) < 20:
            novel.append(0)

        for audience_url, _, idx_weekly, idx_monthly, idx_daily in AUDIENCES:
            if audience_url in failed_audiences:
                continue
            for period_url, idx in [("weekly", idx_weekly), ("month", idx_monthly), ("today", idx_daily)]:
                new_rank = rankings.get((audience_url, period_url), {}).get(nid, 0)
                if novel[idx] != new_rank:
                    novel[idx] = new_rank
                    rank_changes += 1

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  Total novels in dataset: {len(existing_data)}")
    print(f"  Freshly scraped:         {len(fresh_novels)}")
    print(f"  Updated (existing):      {stats['updated']}")
    print(f"  Added (new):             {stats['added']}")
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
        json.dump(existing_data, f, ensure_ascii=False, separators=(",", ":"))
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

    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(full_novels, f, ensure_ascii=False)
    print(f"Saved {full_path} ({os.path.getsize(full_path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
