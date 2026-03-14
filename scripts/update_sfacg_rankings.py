"""Scrape SFACG rankings and patch them into existing sfacg_novels.json.

Also rescrapes full metadata (cover, title, views, etc.) for all ranked novels
so that data stays fresh.

Updates sfacg_novels.json in-place, then re-extracts descriptions,
re-chunks data, and rebuilds the top file.

Usage:
    python scripts/update_sfacg_rankings.py
"""

import sys, os, json, time, re, requests

sys.stdout.reconfigure(encoding="utf-8")

RANK_CATEGORIES = [
    ("original", "Popularity"),
    ("sale",     "Best Seller"),
    ("new",      "New Books"),
    ("bm",       "Bookmarks"),
    ("jp",       "JP Light Novels"),
]

API_URL = "https://api.sfacg.com/novels"
HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}

COVER_PREFIX = "https://rss.sfacg.com/web/novel/images/NovelCover/Big/"


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


def rescrape_metadata(session, ranked_ids):
    """Fetch fresh metadata for ranked novels from the SFACG API.

    Returns dict: {novel_id_str: {title, author, cover, tags, views, likes, chapters, complete, updated, age, synopsis}}
    """
    fresh = {}
    for i, nid in enumerate(sorted(ranked_ids, key=int)):
        try:
            r = session.get(f"{API_URL}/{nid}",
                            params={"expand": "intro,sysTags,typeName"},
                            timeout=10)
            data = r.json().get("data", {})
            if not data:
                continue

            expand = data.get("expand", {})
            sys_tags = expand.get("sysTags") or []
            tag_names = [t.get("tagName", "") for t in sys_tags if t.get("tagName")]
            type_name = expand.get("typeName", "")
            if type_name and type_name not in tag_names:
                tag_names.insert(0, type_name)

            cover = data.get("novelCover", "")
            if cover.startswith(COVER_PREFIX):
                cover = cover[len(COVER_PREFIX):]

            synopsis = expand.get("intro", "")
            if synopsis:
                synopsis = synopsis.replace("\r\n", "\n").replace("\r", "\n")
                synopsis = re.sub(r"\n{3,}", "\n\n", synopsis).strip()

            fresh[str(nid)] = {
                "title": data.get("novelName", ""),
                "author": data.get("authorName", ""),
                "cover": cover,
                "tags": tag_names,
                "views": data.get("viewTimes", 0),
                "likes": data.get("markCount", 0),
                "chapters": data.get("charCount", 0),
                "complete": 1 if data.get("isFinish", False) else 0,
                "updated": data.get("lastUpdateTime", ""),
                "age": 19 if data.get("allowDown", 0) == 0 else 0,
                "synopsis": synopsis,
            }
        except Exception as e:
            print(f"  Warning: failed to fetch {nid}: {e}")

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

    print(f"\nRescraping metadata for {len(all_ranked)} ranked novels...")
    fresh_data = rescrape_metadata(session, all_ranked)
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
        # Ensure entry has at least 18 fields
        while len(entry) < 18:
            entry.append(0 if len(entry) < 17 else "")

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
