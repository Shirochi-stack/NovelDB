"""Scrape all Novelpia novel metadata for the NovelDB site.

Searches across many common tags to maximize coverage,
unions results, and exports as JSON for the static site.

Usage:
    python scripts/scrape_novelpia.py

Reads loginkey from config.json in the project root.
Outputs: docs/data/novels.json
"""

import sys, os, json, time, argparse, re, tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from novelpia_auth import NovelpiaAuth
from novelpia_search_terms import RETRYABLE_STATUS_CODES, SEARCH_TAGS, SWEEP_CHARS

COVER_PREFIX = "https://novelpia.com"
DELETED_TAG = "deleted"


def make_session(loginkey):
    auth = NovelpiaAuth()
    auth.set_manual_key(loginkey)
    return auth.session


def pick_cover(item):
    """Pick the best cover URL from a search result item."""
    for k in ("novel_img_all", "novel_thumb_all", "cover_url", "novel_img", "novel_thumb"):
        v = item.get(k)
        if v and str(v) not in ("", "None", "null"):
            v = str(v)
            if v.startswith("//"):
                return "https:" + v
            if not v.startswith("http"):
                return "https://novelpia.com" + v
            return v
    return ""


def extract_novel(item):
    return {
        "id": item.get("novel_no"),
        "title": item.get("novel_name", ""),
        "synopsis": item.get("novel_story", ""),
        "author": item.get("writer_nick", ""),
        "cover": pick_cover(item),
        "tags": item.get("novel_genre_arr") or [],
        "views": item.get("count_view", 0),
        "likes": item.get("count_good", 0),
        "chapters": item.get("count_book", 0),
        "complete": item.get("is_complete", 0),
        "age": item.get("novel_age", 0),
        "updated": item.get("last_viewdate", ""),
    }


def get_json_with_retry(session, params, headers, retries, timeout=120):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                "https://novelpia.com/proc/novel",
                params=params,
                headers=headers,
                timeout=timeout,
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise RuntimeError(f"HTTP {response.status_code}")
            text = response.text.strip()
            if not text:
                return None
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise last_error


def search_novels(session, search_val, headers, retries, search_type="all"):
    """Search Novelpia API for novels matching a term."""
    items = []
    rows = 30000
    for pg in range(1, 21):
        params = {
            "cmd": "novel_search",
            "search_type": search_type,
            "search_val": search_val,
            "page": pg,
            "rows": rows,
            "novel_type": "",
            "start_count_book": "",
            "end_count_book": "",
            "novel_age": "",
            "start_days": "",
            "sort_col": "last_viewdate",
            "novel_genre": "",
            "block_out": 0,
            "block_stop": 0,
            "is_contest": 0,
            "is_complete": "",
            "is_challenge": 0,
            "list_display": "grid",
        }
        data = get_json_with_retry(session, params, headers, retries)
        if not data:
            break
        batch = data.get("list", [])
        items.extend(batch)
        if len(batch) < rows:
            break
    return items


def fetch_terms(label, terms, loginkey, headers, workers, retries, delay):
    results = {}
    workers = max(1, workers)
    print(f"--- {label}: {len(terms)} terms ({workers} workers, {delay}s stagger) ---", flush=True)

    def _collect_done(done, futures):
        for future in done:
            i, term = futures.pop(future)
            prefix = f"[{i + 1}/{len(terms)}] {label}: {term!r}..."
            try:
                items = future.result()
                results[term] = items
                print(f"{prefix} {len(items)} results", flush=True)
            except Exception as exc:
                results[term] = []
                print(f"{prefix} ERROR after retries: {exc}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, term in enumerate(terms):
            future = pool.submit(
                search_novels,
                make_session(loginkey),
                term,
                headers,
                retries,
            )
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


def merge_term_results(terms, results, novels_by_id):
    total_results = 0
    before = len(novels_by_id)
    for term in terms:
        items = results.get(term, [])
        total_results += len(items)
        for item in items:
            novel_id = item.get("novel_no")
            if not novel_id:
                continue
            novels_by_id.setdefault(str(novel_id), extract_novel(item))
    return total_results, len(novels_by_id) - before


def write_json_file(path, data, separators=None):
    tmp_path = None
    try:
        directory = os.path.dirname(path) or "."
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=directory,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
        ) as f:
            tmp_path = f.name
            json.dump(data, f, ensure_ascii=False, separators=separators)
            f.flush()
            os.fsync(f.fileno())
        deadline = time.time() + 30
        while True:
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if time.time() >= deadline:
                    break
                time.sleep(1)

        if not os.path.exists(path):
            os.replace(tmp_path, path)
            return
        with open(path, "r+", encoding="utf-8", newline="") as f:
            f.seek(0)
            with open(tmp_path, "r", encoding="utf-8") as tmp:
                for chunk in iter(lambda: tmp.read(1024 * 1024), ""):
                    f.write(chunk)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        os.unlink(tmp_path)
    except Exception:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_deleted_tag(tags, age):
    tags = list(tags or [])
    if str(age) != "19" and DELETED_TAG not in tags:
        tags.append(DELETED_TAG)
    return tags


def full_from_site_row(row):
    cover = str(row[3] or "") if len(row) > 3 else ""
    if cover.startswith("//"):
        cover = "https:" + cover
    elif cover and not cover.startswith("http"):
        cover = COVER_PREFIX + cover
    return {
        "id": row[0],
        "title": row[1] if len(row) > 1 else "",
        "synopsis": "",
        "author": row[2] if len(row) > 2 else "",
        "cover": cover,
        "tags": row[4] if len(row) > 4 and isinstance(row[4], list) else [],
        "views": row[5] if len(row) > 5 else 0,
        "likes": row[6] if len(row) > 6 else 0,
        "chapters": row[7] if len(row) > 7 else 0,
        "complete": row[8] if len(row) > 8 else 0,
        "age": row[11] if len(row) > 11 else 0,
        "updated": row[9] if len(row) > 9 else "",
    }

def main():
    parser = argparse.ArgumentParser(description="Scrape Novelpia novels with auth")
    parser.add_argument("--search-workers", type=int, default=4,
                        help="Parallel workers for tag/sweep search")
    parser.add_argument("--search-retries", type=int, default=4,
                        help="Retries per search page")
    parser.add_argument("--search-delay", type=float, default=0.5,
                        help="Seconds to stagger parallel search submissions")
    args = parser.parse_args()

    auth = NovelpiaAuth()
    with open('config.json', 'r') as f:
        config = json.load(f)
    loginkey = config.get('loginkey', '')
    auth.set_manual_key(loginkey)

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://novelpia.com/search",
    }

    novels_by_id = {}

    tag_results = fetch_terms(
        "Searching", SEARCH_TAGS, loginkey, headers,
        args.search_workers, args.search_retries, args.search_delay,
    )
    tag_total, tag_new = merge_term_results(SEARCH_TAGS, tag_results, novels_by_id)
    print(
        f"\n--- Phase 1 complete: {len(novels_by_id)} novels from tags "
        f"({tag_total} raw results, {tag_new} unique) ---\n"
    )

    # Phase 2: Character sweep for remaining novels
    sweep_results = fetch_terms(
        "Sweep", SWEEP_CHARS, loginkey, headers,
        args.search_workers, args.search_retries, args.search_delay,
    )
    sweep_total, sweep_new = merge_term_results(SWEEP_CHARS, sweep_results, novels_by_id)
    print(
        f"\n--- Phase 2 complete: {len(novels_by_id)} total scraped novels "
        f"({sweep_total} raw sweep results, {sweep_new} new unique) ---\n"
    )

    novels = list(novels_by_id.values())

    # Phase 3: Scrape rankings per audience (all, adult, teen) × period (weekly, monthly, daily)
    print(f"\n--- Scraping rankings ---")
    # rankings[(audience, period)] = {novel_id: rank}
    rankings = {}
    try:
        AUDIENCES = [
            ("all/plus",   "all"),
            ("adult/plus", "adult"),
            ("teen/plus",  "teen"),
        ]
        PERIODS = [
            ("weekly", "weekly"),
            ("month",  "monthly"),
            ("today",  "daily"),
        ]
        for audience_url, audience_label in AUDIENCES:
            for period_url, period_label in PERIODS:
                label = f"{period_label} {audience_label}"
                url = f"https://novelpia.com/top100/all/{period_url}/view/{audience_url}"
                r = auth.session.get(url, timeout=30)
                rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
                ranking = {}
                for pos, nid in enumerate(rank_ids[:100], 1):
                    ranking[nid] = pos
                rankings[(audience_url, period_url)] = ranking
                print(f"  {label}: {len(ranking)} novels")
    except Exception as e:
        print(f"  Error scraping ranking: {e}")

    def get_rank(nid, audience, period):
        return rankings.get((audience, period), {}).get(nid, 0)

    # Save output, preserving old-only/deleted rows from the previous dataset.
    os.makedirs("docs/data", exist_ok=True)
    full_path = "docs/data/novels_full.json"
    opt_path = "docs/data/novels.json"
    existing_site = load_json_file(opt_path, [])
    existing_full = load_json_file(full_path, [])
    existing_full_by_id = {
        str(n.get("id")): n
        for n in existing_full
        if isinstance(n, dict) and n.get("id") is not None
    }
    fresh_ids = {str(n.get("id")) for n in novels if n.get("id") is not None}
    preserved_full = []
    preserved_site_rows = []

    for row in existing_site:
        if not isinstance(row, list) or not row:
            continue
        nid = str(row[0])
        if nid in fresh_ids:
            continue
        row = list(row)
        age = row[11] if len(row) > 11 else 0
        tags = row[4] if len(row) > 4 and isinstance(row[4], list) else []
        row[4] = ensure_deleted_tag(tags, age)
        preserved_site_rows.append(row)

        full_entry = dict(existing_full_by_id.get(nid) or full_from_site_row(row))
        full_tags = full_entry.get("tags") or []
        full_entry["tags"] = ensure_deleted_tag(full_tags, age)
        preserved_full.append(full_entry)

    if preserved_site_rows:
        novels.extend(preserved_full)
        print(
            f"  Preserved {len(preserved_site_rows)} old-only novels "
            f"({sum(1 for r in preserved_site_rows if DELETED_TAG in (r[4] if len(r) > 4 else []))} tagged deleted)"
        )

    # Optimized version for the site (array format, no synopsis, stripped cover prefix)
    optimized = []
    for n in novels_by_id.values():
        cover = n.get("cover", "")
        if cover.startswith(COVER_PREFIX):
            cover = cover[len(COVER_PREFIX):]
        nid = str(n["id"])
        optimized.append([
            n["id"],                             # [0]  id
            n["title"],                          # [1]  title
            n["author"],                         # [2]  author
            cover,                               # [3]  cover (relative)
            n.get("tags", []),                   # [4]  tags
            n.get("views", 0),                   # [5]  views
            n.get("likes", 0),                   # [6]  likes
            n.get("chapters", 0),                # [7]  chapters
            n.get("complete", 0),                # [8]  complete
            n.get("updated", ""),                # [9]  updated
            get_rank(nid, "all/plus", "weekly"),  # [10] weeklyRank (all)
            n.get("age", 0),                     # [11] age rating
            get_rank(nid, "all/plus", "month"),   # [12] monthlyRank (all)
            get_rank(nid, "all/plus", "today"),   # [13] dailyRank (all)
            get_rank(nid, "adult/plus", "weekly"),# [14] weeklyRankAdult
            get_rank(nid, "adult/plus", "month"), # [15] monthlyRankAdult
            get_rank(nid, "adult/plus", "today"), # [16] dailyRankAdult
            get_rank(nid, "teen/plus", "weekly"), # [17] weeklyRankTeen
            get_rank(nid, "teen/plus", "month"),  # [18] monthlyRankTeen
            get_rank(nid, "teen/plus", "today"),  # [19] dailyRankTeen
        ])

    optimized.extend(preserved_site_rows)

    write_json_file(full_path, novels)
    print(f"\nFull: {len(novels)} novels -> {os.path.getsize(full_path) / 1024 / 1024:.1f} MB")

    write_json_file(opt_path, optimized, separators=(",", ":"))

    print(f"Site: {len(optimized)} novels -> {os.path.getsize(opt_path) / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    main()
