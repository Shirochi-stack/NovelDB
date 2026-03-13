"""Scrape SFACG rankings and patch them into existing sfacg_novels.json.

Lightweight alternative to a full rescrape — only fetches:
  1. Rankings from m.sfacg.com (4 categories, ~80 novels total)
  2. Synopses for ranked novels via API

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


def fetch_synopsis(session, novel_id):
    try:
        r = session.get(f"{API_URL}/{novel_id}", params={"expand": "intro"}, timeout=10)
        data = r.json()
        intro = data.get("data", {}).get("expand", {}).get("intro", "")
        if intro:
            intro = intro.replace("\r\n", "\n").replace("\r", "\n")
            intro = re.sub(r"\n{3,}", "\n\n", intro).strip()
        return intro
    except Exception:
        return ""


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

    # Build ID -> index map
    id_map = {str(e[0]): i for i, e in enumerate(data)}
    found = sum(1 for rid in all_ranked if rid in id_map)
    print(f"Ranked IDs found in dataset: {found}/{len(all_ranked)}")

    # Phase 2: Fetch synopsis for ranked novels missing one
    session = requests.Session()
    session.headers.update(HEADERS)

    need_synopsis = [rid for rid in sorted(all_ranked)
                     if rid in id_map and (len(data[id_map[rid]]) <= 17 or not data[id_map[rid]][17] if len(data[id_map[rid]]) > 17 else True)]
    if need_synopsis:
        print(f"\nFetching synopsis for {len(need_synopsis)} ranked novels...")
        synopses = {}
        for rid in need_synopsis:
            synopsis = fetch_synopsis(session, rid)
            if synopsis:
                synopses[rid] = synopsis
            time.sleep(0.2)
        print(f"  Got {len(synopses)} synopses")
    else:
        synopses = {}

    # Phase 3: Clear old rankings and patch new ones
    # First, clear all existing rankings (indices 11-16) so stale ranks are removed
    for entry in data:
        if len(entry) > 11:
            for j in range(11, min(17, len(entry))):
                entry[j] = 0

    # Patch new rankings + synopses
    patched = 0
    for i, entry in enumerate(data):
        nid = str(entry[0])
        # Ensure entry has at least 18 fields
        while len(entry) < 18:
            entry.append(0 if len(entry) < 17 else "")

        entry[11] = rankings.get("original", {}).get(nid, 0)
        entry[12] = rankings.get("sale", {}).get(nid, 0)
        entry[13] = rankings.get("new", {}).get(nid, 0)
        entry[14] = rankings.get("bm", {}).get(nid, 0)
        entry[15] = rankings.get("jp", {}).get(nid, 0)
        entry[16] = rankings.get("ticket", {}).get(nid, 0)

        if nid in synopses:
            entry[17] = synopses[nid]

        if any(entry[j] > 0 for j in range(11, 17)):
            patched += 1

        # Strip trailing zeros/empty to save space
        while entry and (entry[-1] == 0 or entry[-1] == "" or entry[-1] == []):
            entry.pop()

        data[i] = entry

    print(f"\nPatched {patched} novels with ranking data")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
