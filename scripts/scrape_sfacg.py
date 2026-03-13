"""Scrape SFACG (SF轻小说) novel metadata for the NovelDB site.

Uses the SFACG public API to fetch ALL novel listings via deep pagination.
No category filter — just paginate through the entire catalog.
Also scrapes rankings from the mobile site and synopses from the API.

Usage:
    python scripts/scrape_sfacg.py
    python scripts/scrape_sfacg.py --delay 0.2
    python scripts/scrape_sfacg.py --max-pages 5   # quick test

Output: docs/data/sfacg_novels.json
"""

import sys, os, json, time, re, requests

sys.stdout.reconfigure(encoding='utf-8')

API_URL = "https://api.sfacg.com/novels"

HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}

# SFACG mobile ranking pages — each returns up to 20 novels
RANK_CATEGORIES = [
    ("original", "Popularity"),
    ("sale",     "Best Seller"),
    ("new",      "New Books"),
    ("bm",       "Bookmarks"),
]


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


def fetch_synopsis(session, novel_id):
    """Fetch synopsis for a single novel via API."""
    try:
        r = session.get(f"{API_URL}/{novel_id}", params={"expand": "intro"}, timeout=10)
        data = r.json()
        novel = data.get("data", {})
        intro = novel.get("expand", {}).get("intro", "")
        if intro:
            # Normalize newlines
            intro = intro.replace("\r\n", "\n").replace("\r", "\n")
            intro = re.sub(r"\n{3,}", "\n\n", intro).strip()
        return intro
    except Exception:
        return ""


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape SFACG novels")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests")
    parser.add_argument("--max-pages", type=int, default=9999, help="Max pages to fetch")
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

    # === Phase 2: Scrape full catalog ===
    all_novels = {}
    empty_count = 0

    print(f"\nScraping all novels (50/page, max {args.max_pages} pages)...")

    for page in range(args.max_pages):
        try:
            params = {
                "page": page,
                "size": 50,
                "sort": "novelid",  # Sequential by ID for full coverage
                "expand": "typeName,sysTags",
            }
            # Add intro expand for synopsis (on every page)
            if not args.skip_synopsis:
                params["expand"] += ",intro"

            r = session.get(API_URL, params=params, timeout=15)

            if r.status_code != 200:
                print(f"\n  HTTP {r.status_code} at page {page}, stopping")
                break

            data = r.json()
            if data.get("status", {}).get("httpCode", 200) != 200:
                print(f"\n  API error at page {page}: {data.get('status', {}).get('msg')}")
                break

            items = data.get("data") or []
            if not items:
                empty_count += 1
                if empty_count >= 3:
                    print(f"\n  3 consecutive empty pages at {page}, done")
                    break
                continue
            else:
                empty_count = 0

            for item in items:
                nid = str(item.get("novelId", ""))
                if not nid or nid in all_novels:
                    continue

                expand = item.get("expand", {})
                sys_tags = expand.get("sysTags") or []
                tag_names = [t.get("tagName", "") for t in sys_tags if t.get("tagName")]
                type_name = expand.get("typeName", "")
                if type_name and type_name not in tag_names:
                    tag_names.insert(0, type_name)

                # Synopsis from expand
                synopsis = ""
                if not args.skip_synopsis:
                    intro = expand.get("intro", "")
                    if intro:
                        intro = intro.replace("\r\n", "\n").replace("\r", "\n")
                        synopsis = re.sub(r"\n{3,}", "\n\n", intro).strip()

                all_novels[nid] = {
                    "id": nid,
                    "title": item.get("novelName", ""),
                    "author": item.get("authorName", ""),
                    "cover": item.get("novelCover", ""),
                    "tags": tag_names,
                    "views": item.get("viewTimes", 0),
                    "likes": item.get("markCount", 0),
                    "chapters": item.get("charCount", 0),
                    "complete": 1 if item.get("isFinish", False) else 0,
                    "updated": item.get("lastUpdateTime", ""),
                    "age": 19 if item.get("allowDown", 0) == 0 else 0,
                    "synopsis": synopsis,
                }

            if (page + 1) % 50 == 0:
                print(f"  Page {page+1}: {len(all_novels)} novels", flush=True)

            # Auto-save every 500 pages to prevent data loss
            if (page + 1) % 500 == 0 and all_novels:
                save_novels(all_novels, rankings)
                print(f"  [auto-saved {len(all_novels)} novels]", flush=True)

            time.sleep(args.delay)

        except Exception as e:
            print(f"\n  Error at page {page}: {e}")
            time.sleep(2)

    print(f"\nTotal: {len(all_novels)} novels")

    if not all_novels:
        print("No novels found!")
        return

    # === Phase 3: Fetch synopsis for ranked novels that might be missing ===
    if not args.skip_synopsis:
        all_ranked_ids = set()
        for slug, rank_map in rankings.items():
            all_ranked_ids.update(rank_map.keys())

        missing_synopsis = [nid for nid in all_ranked_ids
                           if nid in all_novels and not all_novels[nid].get("synopsis")]
        if missing_synopsis:
            print(f"\nFetching synopsis for {len(missing_synopsis)} ranked novels missing synopsis...")
            for nid in missing_synopsis:
                synopsis = fetch_synopsis(session, nid)
                if synopsis:
                    all_novels[nid]["synopsis"] = synopsis
                time.sleep(0.3)

    save_novels(all_novels, rankings)


def save_novels(all_novels, rankings=None):
    """Save novels to disk.

    Output format per entry (up to 16 fields, trailing zeros stripped):
        [id, title, author, cover, tags, views, likes, chapters, complete, updated, age,
         popularityRank, bestSellerRank, newBooksRank, bookmarksRank, synopsis]

    Indices 11-14 are SFACG ranking positions (1-20, or 0 if unranked).
    Index 15 is the Chinese synopsis string.
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
        synopsis = n.get("synopsis", "")

        entry = [
            nid, n["title"], n["author"], cover,
            tags, n.get("views", 0), n.get("likes", 0),
            n.get("chapters", 0), n.get("complete", 0),
            n.get("updated", ""), n.get("age", 0),
            pop_rank, sale_rank, new_rank, bm_rank,
            synopsis,
        ]
        # Strip trailing zeros/empty values to save space
        while entry and (entry[-1] == 0 or entry[-1] == "" or entry[-1] == []):
            entry.pop()
        optimized.append(entry)

    path = "docs/data/sfacg_novels.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
