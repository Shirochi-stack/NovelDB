"""Scrape SFACG (SF轻小说) novel metadata for the NovelDB site.

Uses SFACG's public API to fetch novel listings through both the generic
/novels sweep and broad type/category calls. The generic list is denser, while
the type buckets can recover older/de-indexed rows that the generic list omits.
Also scrapes rankings from the mobile site and synopses from the API.

Usage:
    python scripts/scrape_sfacg.py
    python scripts/scrape_sfacg.py --delay 0.2
    python scripts/scrape_sfacg.py --max-pages 5   # quick smoke test

Output: docs/data/sfacg_novels.json
"""

import sys, os, json, time, re, threading, requests
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

API_URL = "https://api.sfacg.com/novels"
NOVEL_TYPES_URL = "https://api.sfacg.com/novelTypes"
TYPE_NOVELS_URL = "https://api.sfacg.com/novels/{type_id}/sysTags/novels"
SFACG_NOVELS_PATH = os.path.join("docs", "data", "sfacg_novels.json")
DELETED_TAG = "deleted"
PAGE_SIZE = 50
GENERIC_WORKERS = 8

HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}
THREAD_LOCAL = threading.local()

# SFACG mobile ranking pages — each returns up to 20 novels
RANK_CATEGORIES = [
    ("original", "Popularity"),
    ("sale",     "Best Seller"),
    ("new",      "New Books"),
    ("bm",       "Bookmarks"),
    ("jp",       "JP Light Novels"),
]

# /novelTypes currently omits type 28, but the type bucket exists and contains
# the large 同人 catalog. Keep this fallback so a changed type response does not
# silently drop that bucket.
FALLBACK_NOVEL_TYPES = [
    {"typeId": 21, "typeName": "魔幻"},
    {"typeId": 22, "typeName": "玄幻"},
    {"typeId": 23, "typeName": "古风"},
    {"typeId": 24, "typeName": "科幻"},
    {"typeId": 25, "typeName": "校园"},
    {"typeId": 26, "typeName": "都市"},
    {"typeId": 27, "typeName": "游戏"},
    {"typeId": 28, "typeName": "同人"},
    {"typeId": 29, "typeName": "悬疑"},
]


def char_count_ranges():
    """Ranges used to make broad SFACG type calls deep enough for old rows."""
    ranges = []
    for start in range(0, 200_000, 10_000):
        ranges.append((start, start + 9_999))
    for start in range(200_000, 1_000_000, 100_000):
        ranges.append((start, start + 99_999))
    ranges.extend([
        (1_000_000, 1_499_999),
        (1_500_000, 1_999_999),
        (2_000_000, 2_999_999),
        (3_000_000, 4_999_999),
        (5_000_000, 0),
    ])
    return ranges


def range_label(begin, end):
    if end == 0:
        return f"{begin:,}+"
    return f"{begin:,}-{end:,}"


def novel_row_to_dict(row):
    """Convert the compact SFACG row schema back into scraper dict form."""
    if not row:
        return None
    sid = str(row[0]).strip()
    if not sid:
        return None
    return {
        "id": sid,
        "title": row[1] if len(row) > 1 else "",
        "author": row[2] if len(row) > 2 else "",
        "cover": row[3] if len(row) > 3 else "",
        "tags": row[4] if len(row) > 4 and isinstance(row[4], list) else [],
        "views": row[5] if len(row) > 5 else 0,
        "likes": row[6] if len(row) > 6 else 0,
        "chapters": row[7] if len(row) > 7 else 0,
        "complete": row[8] if len(row) > 8 else 0,
        "updated": row[9] if len(row) > 9 else "",
        "age": row[10] if len(row) > 10 else 0,
        "synopsis": row[17] if len(row) > 17 else "",
        "latest_chapter_title": row[18] if len(row) > 18 else "",
        "latest_chapter_id": row[19] if len(row) > 19 else 0,
        "latest_chapter_time": row[20] if len(row) > 20 else "",
    }


def load_existing_novels(path=None):
    """Load the existing SFACG catalog so old-only entries are preserved."""
    if path is None:
        path = SFACG_NOVELS_PATH
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    novels = {}
    for row in rows:
        novel = novel_row_to_dict(row)
        if novel:
            novels[novel["id"]] = novel

    print(f"Loaded {len(novels):,} existing novels from {path}")
    return novels


def merge_with_existing_novels(scraped_novels, path=None, tag_deleted=True):
    """Preserve old-only rows while letting freshly scraped rows win."""
    if path is None:
        path = SFACG_NOVELS_PATH
    existing = load_existing_novels(path)
    if not existing:
        return scraped_novels

    merged = dict(existing)
    merged.update(scraped_novels)

    preserved_ids = set(existing) - set(scraped_novels)
    if tag_deleted:
        for sid in preserved_ids:
            tags = merged[sid].setdefault("tags", [])
            if not isinstance(tags, list):
                tags = []
                merged[sid]["tags"] = tags
            if DELETED_TAG not in tags:
                tags.append(DELETED_TAG)

    print(
        f"Catalog merge: {len(set(existing) & set(scraped_novels)):,} updated, "
        f"{len(set(scraped_novels) - set(existing)):,} new, "
        f"{len(preserved_ids):,} old-only preserved"
        + (f"/tagged {DELETED_TAG!r}" if tag_deleted else " without new deleted tags")
    )
    return merged


def fetch_novel_types(session):
    """Fetch SFACG type IDs, with a local fallback for omitted live buckets."""
    types_by_id = {}
    try:
        r = session.get(NOVEL_TYPES_URL, timeout=10)
        data = r.json()
        if data.get("status", {}).get("httpCode") == 200:
            for item in data.get("data") or []:
                type_id = item.get("typeId")
                if type_id is None:
                    continue
                types_by_id[int(type_id)] = {
                    "typeId": int(type_id),
                    "typeName": item.get("typeName", str(type_id)),
                }
    except Exception as e:
        print(f"  Warning: failed to fetch SFACG novel types: {e}")

    for item in FALLBACK_NOVEL_TYPES:
        types_by_id.setdefault(int(item["typeId"]), item)

    return [types_by_id[type_id] for type_id in sorted(types_by_id)]


def normalize_intro(intro):
    if not intro:
        return ""
    intro = intro.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", intro).strip()


def extract_tag_names(expand):
    tag_names = []
    type_name = expand.get("typeName", "")
    if type_name:
        tag_names.append(type_name)

    for key in ("sysTags", "tags"):
        for tag in expand.get(key) or []:
            if isinstance(tag, dict):
                name = tag.get("tagName") or tag.get("name")
            else:
                name = str(tag)
            if name and name not in tag_names:
                tag_names.append(name)

    return tag_names


def novel_item_to_dict(item, skip_synopsis=False):
    nid = str(item.get("novelId", "")).strip()
    if not nid:
        return None

    expand = item.get("expand", {}) or {}
    synopsis = ""
    if not skip_synopsis:
        synopsis = normalize_intro(expand.get("intro", ""))
    latest_chapter = expand.get("latestChapter") or {}

    return {
        "id": nid,
        "title": item.get("novelName", ""),
        "author": item.get("authorName", ""),
        "author_id": item.get("authorId"),
        "type_id": item.get("typeId"),
        "cover": item.get("novelCover", ""),
        "tags": extract_tag_names(expand),
        "views": item.get("viewTimes", 0),
        "likes": item.get("markCount", 0),
        "chapters": item.get("charCount", 0),
        "complete": 1 if item.get("isFinish", False) else 0,
        "updated": item.get("lastUpdateTime", ""),
        "age": 19 if item.get("allowDown", 0) == 0 else 0,
        "synopsis": synopsis,
        "latest_chapter_title": latest_chapter.get("title", ""),
        "latest_chapter_id": latest_chapter.get("chapId", 0),
        "latest_chapter_time": latest_chapter.get("addTime", ""),
    }


def build_broad_params(page, begin, end, skip_synopsis):
    expand = "typeName,tags,sysTags,latestChapter"
    if not skip_synopsis:
        expand += ",intro"
    return {
        "sort": "latest",
        "systagids": "",
        "isfree": "both",
        "isfinish": "both",
        "updatedays": -1,
        "charcountbegin": begin,
        "charcountend": end,
        "page": page,
        "size": PAGE_SIZE,
        "expand": expand,
    }


def build_generic_params(page, skip_synopsis):
    expand = "typeName,sysTags,latestChapter"
    if not skip_synopsis:
        expand += ",intro"
    return {
        "page": page,
        "size": PAGE_SIZE,
        "sort": "novelid",
        "expand": expand,
    }


def get_worker_session():
    """Return one requests session per worker thread."""
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        THREAD_LOCAL.session = session
    return session


def fetch_generic_page(page, skip_synopsis):
    session = get_worker_session()
    try:
        r = session.get(API_URL, params=build_generic_params(page, skip_synopsis), timeout=15)
        if r.status_code != 200:
            return page, None, f"HTTP {r.status_code}"

        data = r.json()
        if data.get("status", {}).get("httpCode", 200) != 200:
            msg = data.get("status", {}).get("msg") or data.get("status", {})
            return page, None, f"API error {msg}"

        return page, data.get("data") or [], None
    except Exception as e:
        return page, None, str(e)


def scrape_rankings(session):
    """Scrape ranking lists from SFACG mobile site."""
    rankings = {}  # category_slug -> {novel_id_str: rank_position}

    web_session = requests.Session()
    web_session.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    print("\nScraping SFACG rankings...")
    for slug, label in RANK_CATEGORIES:
        url = f"https://m.sfacg.com/rank/{slug}.html"
        try:
            r = web_session.get(url, timeout=15)
            if r.status_code != 200:
                print(f"  {label}: HTTP {r.status_code}")
                rankings[slug] = {}
                continue

            novel_ids = list(dict.fromkeys(re.findall(r'/b/(\d+)/', r.text)))
            rank_map = {}
            for pos, nid in enumerate(novel_ids[:20], 1):
                rank_map[nid] = pos

            print(f"  {label}: {len(rank_map)} novels")
            rankings[slug] = rank_map

        except Exception as e:
            print(f"  {label}: ERROR - {e}")
            rankings[slug] = {}

        time.sleep(0.3)

    return rankings


def fetch_broad_item_for_novel(session, novel, skip_synopsis=False):
    """Refresh a known novel through its broad type/char-count bucket."""
    type_id = novel.get("type_id")
    char_count = novel.get("chapters")
    if not type_id or char_count is None:
        return None

    try:
        char_count = int(char_count)
    except (TypeError, ValueError):
        return None

    url = TYPE_NOVELS_URL.format(type_id=int(type_id))
    for page in range(20):
        params = build_broad_params(page, char_count, char_count, skip_synopsis)
        try:
            r = session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                return None
            data = r.json()
            items = data.get("data") or []
            if not items:
                return None
            for item in items:
                if str(item.get("novelId", "")) == str(novel.get("id", "")):
                    return novel_item_to_dict(item, skip_synopsis=skip_synopsis)
            if len(items) < PAGE_SIZE:
                return None
        except Exception:
            return None

    return None


def scrape_generic_catalog(args, rankings):
    """Scrape the dense /novels endpoint in parallel by page number."""
    all_novels = {}
    generic_pages = 0
    empty_count = 0
    error_count = 0
    stop_scrape = False
    workers = max(1, args.generic_workers)
    batch_size = max(workers, workers * 10)

    print(
        f"\nScraping generic /novels catalog in parallel "
        f"({PAGE_SIZE}/page, max {args.max_pages} pages, {workers} workers)..."
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        next_page = 0
        while next_page < args.max_pages and not stop_scrape:
            batch_pages = list(range(next_page, min(next_page + batch_size, args.max_pages)))
            futures = {
                executor.submit(fetch_generic_page, page, args.skip_synopsis): page
                for page in batch_pages
            }
            results = {}
            for future in as_completed(futures):
                page, items, error = future.result()
                results[page] = (items, error)

            for page in batch_pages:
                items, error = results.get(page, (None, "missing result"))
                generic_pages += 1

                if error:
                    error_count += 1
                    print(f"  Generic page {page}: {error}")
                    if error_count >= workers * 3 and not all_novels:
                        print("  Too many generic errors before any rows were scraped; stopping generic sweep")
                        stop_scrape = True
                        break
                    continue

                error_count = 0
                if not items:
                    empty_count += 1
                    if empty_count >= 3:
                        print(f"\n  3 consecutive empty generic pages at {page}, done")
                        stop_scrape = True
                        break
                    continue

                empty_count = 0
                for item in items:
                    novel = novel_item_to_dict(item, skip_synopsis=args.skip_synopsis)
                    if not novel:
                        continue
                    all_novels[novel["id"]] = novel

                if (page + 1) % 50 == 0:
                    print(f"  Generic page {page + 1}: {len(all_novels):,} novels", flush=True)

                # Autosaves must not newly tag deleted rows while this scrape is partial.
                if (page + 1) % 500 == 0 and all_novels:
                    save_novels(merge_with_existing_novels(all_novels, tag_deleted=False), rankings)
                    print(f"  [generic auto-saved {len(all_novels):,} novels]", flush=True)

            next_page += len(batch_pages)
            if args.delay > 0 and not stop_scrape:
                time.sleep(args.delay)

    print(f"\nGeneric sweep scraped: {len(all_novels):,} novels from {generic_pages:,} page requests")
    return all_novels, generic_pages


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape SFACG novels")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests")
    parser.add_argument("--max-pages", type=int, default=9999, help="Max page requests per catalog pass")
    parser.add_argument("--generic-workers", type=int, default=GENERIC_WORKERS,
                        help="Parallel workers for the generic /novels sweep")
    parser.add_argument("--skip-synopsis", action="store_true", help="Skip synopsis scraping")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)

    # Test connection
    print("Testing SFACG API...")
    try:
        r = session.get(API_URL, params={'page': 0, 'size': 1, 'sort': 'viewtimes'}, timeout=10)
        data = r.json()
        if data.get('status', {}).get('httpCode') != 200:
            print(f"  API error: {data.get('status', {})}")
            sys.exit(1)
        print(f"  OK")
    except Exception as e:
        print(f"  Connection error: {e}")
        sys.exit(1)

    # === Phase 1: Scrape rankings first ===
    rankings = scrape_rankings(session)

    # === Phase 2: Scrape full catalog through the dense generic API sweep ===
    all_novels, generic_pages = scrape_generic_catalog(args, rankings)

    # === Phase 3: Add anything recoverable through broad type buckets ===
    type_rows = fetch_novel_types(session)
    ranges = char_count_ranges()
    pages_fetched = 0
    stop_scrape = False

    print(
        f"\nScraping additional novels via {len(type_rows)} type buckets, "
        f"{len(ranges)} char-count ranges, {PAGE_SIZE}/page "
        f"(max {args.max_pages} pages)..."
    )

    for type_row in type_rows:
        if stop_scrape:
            break

        type_id = int(type_row["typeId"])
        type_name = type_row.get("typeName", str(type_id))
        type_start_count = len(all_novels)
        print(f"\n  Type {type_id} {type_name}...")

        for begin, end in ranges:
            if stop_scrape:
                break

            url = TYPE_NOVELS_URL.format(type_id=type_id)
            page = 0
            range_rows = 0
            range_new = 0

            while pages_fetched < args.max_pages:
                try:
                    params = build_broad_params(page, begin, end, args.skip_synopsis)
                    r = session.get(url, params=params, timeout=15)

                    if r.status_code != 200:
                        print(
                            f"    {range_label(begin, end)} page {page}: "
                            f"HTTP {r.status_code}, stopping range"
                        )
                        break

                    data = r.json()
                    if data.get("status", {}).get("httpCode", 200) != 200:
                        print(
                            f"    {range_label(begin, end)} page {page}: "
                            f"API error {data.get('status', {}).get('msg')}, stopping range"
                        )
                        break

                    items = data.get("data") or []
                    pages_fetched += 1

                    if not items:
                        break

                    range_rows += len(items)
                    for item in items:
                        novel = novel_item_to_dict(item, skip_synopsis=args.skip_synopsis)
                        if not novel:
                            continue
                        nid = novel["id"]
                        if nid not in all_novels:
                            range_new += 1
                        all_novels[nid] = novel

                    if len(items) < PAGE_SIZE:
                        break

                    if pages_fetched % 50 == 0:
                        print(
                            f"    Page requests {pages_fetched}: "
                            f"{len(all_novels):,} novels",
                            flush=True,
                        )

                    # Autosaves must not newly tag deleted rows while this scrape is partial.
                    if pages_fetched % 500 == 0 and all_novels:
                        save_novels(merge_with_existing_novels(all_novels, tag_deleted=False), rankings)
                        print(f"    [bucket auto-saved {len(all_novels):,} novels]", flush=True)

                    page += 1
                    time.sleep(args.delay)

                except Exception as e:
                    print(f"    Error in {range_label(begin, end)} page {page}: {e}")
                    time.sleep(2)
                    break

            if range_rows:
                print(
                    f"    {range_label(begin, end)}: "
                    f"{range_rows:,} rows, {range_new:,} new "
                    f"(total {len(all_novels):,})",
                    flush=True,
                )

            if pages_fetched >= args.max_pages:
                print(f"\n  Reached max page request limit ({args.max_pages}); stopping")
                stop_scrape = True
                break

        print(
            f"  Type {type_id} complete: "
            f"{len(all_novels) - type_start_count:,} new, "
            f"{len(all_novels):,} total",
            flush=True,
        )

    print(
        f"\nCombined scrape before preserve/delete merge: {len(all_novels):,} novels "
        f"({generic_pages:,} generic page requests, {pages_fetched:,} broad page requests)"
    )

    if not all_novels:
        print("No novels found!")
        return

    all_novels = merge_with_existing_novels(all_novels)
    print(f"Total after preserving existing unique novels: {len(all_novels):,}")

    # === Phase 4: Recheck ranked synopses through broad buckets if needed ===
    if not args.skip_synopsis:
        all_ranked_ids = set()
        for slug, rank_map in rankings.items():
            all_ranked_ids.update(rank_map.keys())

        missing_synopsis = [nid for nid in all_ranked_ids
                           if nid in all_novels and not all_novels[nid].get("synopsis")]
        if missing_synopsis:
            print(f"\nRefreshing {len(missing_synopsis)} ranked synopses via broad buckets...")
            for nid in missing_synopsis:
                refreshed = fetch_broad_item_for_novel(session, all_novels[nid])
                if refreshed and refreshed.get("synopsis"):
                    all_novels[nid].update(refreshed)
                time.sleep(0.3)

    save_novels(all_novels, rankings)


def save_novels(all_novels, rankings=None):
    """Save novels to disk.

    Output format per entry (up to 21 fields, trailing zeros stripped):
        [id, title, author, cover, tags, views, likes, chapters, complete, updated, age,
         popularityRank, bestSellerRank, newBooksRank, bookmarksRank, jpRank, ticketRank, synopsis,
         latestChapterTitle, latestChapterId, latestChapterTime]

    Indices 11-16 are SFACG ranking positions (1-20, or 0 if unranked).
    Index 17 is the Chinese synopsis string.
    Indices 18-20 are optional latest chapter metadata.
    """
    if rankings is None:
        rankings = {}

    COVER_PREFIX = "https://rss.sfacg.com/web/novel/images/NovelCover/Big/"
    os.makedirs("docs/data", exist_ok=True)
    optimized = []
    for n in all_novels.values():
        tags = n.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        cover = n.get("cover", "")
        if cover.startswith(COVER_PREFIX):
            cover = cover[len(COVER_PREFIX):]

        nid = n["id"]
        pop_rank = rankings.get("original", {}).get(nid, 0)
        sale_rank = rankings.get("sale", {}).get(nid, 0)
        new_rank = rankings.get("new", {}).get(nid, 0)
        bm_rank = rankings.get("bm", {}).get(nid, 0)
        jp_rank = rankings.get("jp", {}).get(nid, 0)
        ticket_rank = rankings.get("ticket", {}).get(nid, 0)
        synopsis = n.get("synopsis", "")
        latest_chapter_title = n.get("latest_chapter_title", "")
        latest_chapter_id = n.get("latest_chapter_id", 0)
        latest_chapter_time = n.get("latest_chapter_time", "")

        entry = [
            nid, n["title"], n["author"], cover,
            tags, n.get("views", 0), n.get("likes", 0),
            n.get("chapters", 0), n.get("complete", 0),
            n.get("updated", ""), n.get("age", 0),
            pop_rank, sale_rank, new_rank, bm_rank,
            jp_rank, ticket_rank, synopsis,
            latest_chapter_title, latest_chapter_id, latest_chapter_time,
        ]
        # Strip trailing zeros/empty values to save space
        while entry and (entry[-1] == 0 or entry[-1] == "" or entry[-1] == []):
            entry.pop()
        optimized.append(entry)

    path = SFACG_NOVELS_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
