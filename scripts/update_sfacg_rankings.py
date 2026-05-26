"""Scrape SFACG rankings and patch them into existing sfacg_novels.json.

Also refreshes full metadata (cover, title, views, etc.) for ranked novels
through their broad SFACG type/char-count buckets so that data stays fresh.

Updates sfacg_novels.json in-place, then re-extracts descriptions,
re-chunks data, and rebuilds the top file.

Usage:
    python scripts/update_sfacg_rankings.py
"""

import sys, os, json, time, re, requests

import scrape_sfacg as sfacg

sys.stdout.reconfigure(encoding="utf-8")

RANK_CATEGORIES = [
    ("original", "Popularity"),
    ("sale",     "Best Seller"),
    ("new",      "New Books"),
    ("bm",       "Bookmarks"),
    ("jp",       "JP Light Novels"),
]

HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}

COVER_PREFIX = "https://rss.sfacg.com/web/novel/images/NovelCover/Big/"
TYPE_NAME_TO_ID = {
    item["typeName"]: int(item["typeId"])
    for item in sfacg.FALLBACK_NOVEL_TYPES
}


def scrape_rankings():
    rankings = {}
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
    })
    print("Scraping SFACG rankings...")
    for slug, label in RANK_CATEGORIES:
        url = f"https://m.sfacg.com/rank/{slug}.html"
        r = s.get(url, timeout=15)
        ids = list(dict.fromkeys(re.findall(r'/b/(\d+)/', r.text)))
        rank_map = {nid: pos for pos, nid in enumerate(ids[:20], 1)}
        print(f"  {label}: {len(rank_map)} novels")
        rankings[slug] = rank_map
        time.sleep(0.3)
    return rankings


def broad_context_from_row(row):
    tags = row[4] if len(row) > 4 and isinstance(row[4], list) else []
    type_name = tags[0] if tags else ""
    type_id = TYPE_NAME_TO_ID.get(type_name)
    char_count = row[7] if len(row) > 7 else None
    if not type_id or char_count is None:
        return None
    return {
        "id": str(row[0]),
        "type_id": type_id,
        "chapters": char_count,
    }


def rescrape_metadata(session, ranked_ids, existing_rows):
    """Fetch fresh metadata for ranked novels from broad SFACG buckets.

    Returns dict: {novel_id_str: {title, author, cover, tags, views, likes, chapters,
    complete, updated, age, synopsis, latest chapter fields}}
    """
    fresh = {}
    for i, nid in enumerate(sorted(ranked_ids, key=int)):
        try:
            context = broad_context_from_row(existing_rows.get(str(nid), []))
            if not context:
                print(f"  Warning: no broad bucket context for ranked novel {nid}")
                continue
            data = sfacg.fetch_broad_item_for_novel(session, context)
            if not data:
                print(f"  Warning: ranked novel {nid} missing from broad bucket")
                continue

            cover = data.get("cover", "")
            if cover.startswith(COVER_PREFIX):
                cover = cover[len(COVER_PREFIX):]

            fresh[str(nid)] = {
                "title": data.get("title", ""),
                "author": data.get("author", ""),
                "cover": cover,
                "tags": data.get("tags", []),
                "views": data.get("views", 0),
                "likes": data.get("likes", 0),
                "chapters": data.get("chapters", 0),
                "complete": data.get("complete", 0),
                "updated": data.get("updated", ""),
                "age": data.get("age", 0),
                "synopsis": data.get("synopsis", ""),
                "latest_chapter_title": data.get("latest_chapter_title", ""),
                "latest_chapter_id": data.get("latest_chapter_id", 0),
                "latest_chapter_time": data.get("latest_chapter_time", ""),
            }
        except Exception as e:
            print(f"  Warning: failed to refresh {nid}: {e}")

        if (i + 1) % 20 == 0:
            print(f"  Fetched {i+1}/{len(ranked_ids)} novels...", flush=True)
        time.sleep(0.2)

    return fresh


def main():
    path = os.path.join("docs", "data", "sfacg_novels.json")
    if not os.path.exists(path):
        print(f"Error: {path} not found. Run the full scraper first.")
        sys.exit(1)

    print(f"Loading {path}...")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded")

    # Phase 1: Scrape rankings
    rankings = scrape_rankings()

    all_ranked = set()
    for rm in rankings.values():
        all_ranked.update(rm.keys())
    print(f"\nTotal unique ranked IDs: {len(all_ranked)}")

    # Phase 2: Rescrape full metadata for ranked novels
    session = requests.Session()
    session.headers.update(HEADERS)

    # Broad bucket refresh needs the local row's type + char count context.
    rows_by_id = {str(e[0]): e for e in data if e}

    print(f"\nRescraping metadata for {len(all_ranked)} ranked novels...")
    fresh_data = rescrape_metadata(session, all_ranked, rows_by_id)
    print(f"  Got fresh data for {len(fresh_data)} novels")

    # Build ID -> index map
    id_map = {str(e[0]): i for i, e in enumerate(data)}
    found = sum(1 for rid in all_ranked if rid in id_map)
    print(f"Ranked IDs found in dataset: {found}/{len(all_ranked)}")

    # Phase 3: Clear old rankings and patch new ones + metadata
    for entry in data:
        if len(entry) > 11:
            for j in range(11, min(17, len(entry))):
                entry[j] = 0

    patched = 0
    for i, entry in enumerate(data):
        nid = str(entry[0])
        # Ensure entry has at least 21 fields
        while len(entry) < 21:
            entry.append(0 if len(entry) == 19 else "")

        # Update metadata if we have fresh data
        if nid in fresh_data:
            f = fresh_data[nid]
            entry[1] = f["title"]
            entry[2] = f["author"]
            entry[3] = f["cover"]
            entry[4] = f["tags"]
            entry[5] = f["views"]
            entry[6] = f["likes"]
            entry[7] = f["chapters"]
            entry[8] = f["complete"]
            entry[9] = f["updated"]
            entry[10] = f["age"]
            if f["synopsis"]:
                entry[17] = f["synopsis"]
            entry[18] = f["latest_chapter_title"]
            entry[19] = f["latest_chapter_id"]
            entry[20] = f["latest_chapter_time"]

        # Patch rankings
        entry[11] = rankings.get("original", {}).get(nid, 0)
        entry[12] = rankings.get("sale", {}).get(nid, 0)
        entry[13] = rankings.get("new", {}).get(nid, 0)
        entry[14] = rankings.get("bm", {}).get(nid, 0)
        entry[15] = rankings.get("jp", {}).get(nid, 0)
        entry[16] = rankings.get("ticket", {}).get(nid, 0)

        if any(entry[j] > 0 for j in range(11, 17)) or nid in fresh_data:
            patched += 1

        # Strip trailing zeros/empty to save space
        while entry and (entry[-1] == 0 or entry[-1] == "" or entry[-1] == []):
            entry.pop()

        data[i] = entry

    print(f"\nPatched {patched} novels with ranking + metadata")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
