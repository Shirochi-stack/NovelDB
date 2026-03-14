"""Update Novelpia weekly, monthly & daily rankings with authentication.

Scrapes the top100 pages for each audience (all, adult, teen) × each period
(daily, weekly, monthly), then patches novels.json with audience-specific ranks.
Also rescrapes full metadata (cover, title, views, etc.) for all ranked novels
so that data stays fresh.

Data indices:
  [0] id  [1] title  [2] author  [3] cover  [4] tags  [5] views  [6] likes
  [7] chapters  [8] complete  [9] updated
  [10] weeklyRank (all)    [12] monthlyRank (all)    [13] dailyRank (all)
  [14] weeklyRankAdult     [15] monthlyRankAdult     [16] dailyRankAdult
  [17] weeklyRankTeen      [18] monthlyRankTeen      [19] dailyRankTeen

Usage:
    python scripts/update_rankings.py
"""

import sys, os, json, re, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from novelpia_auth import NovelpiaAuth

# (period, audience, label, data_index_weekly, data_index_monthly, data_index_daily)
# We scrape per-audience and store to the matching index.
AUDIENCES = [
    ("all/plus",   "all",   10, 12, 13),  # weeklyRank, monthlyRank, dailyRank
    ("adult/plus", "adult", 14, 15, 16),  # weeklyRankAdult, monthlyRankAdult, dailyRankAdult
    ("teen/plus",  "teen",  17, 18, 19),  # weeklyRankTeen, monthlyRankTeen, dailyRankTeen
]

PERIODS = [
    ("weekly", "weekly"),
    ("month",  "monthly"),
    ("today",  "daily"),
]

COVER_PREFIX = "https://novelpia.com"


def scrape_ranking(session, period, audience):
    """Scrape top100 for a given period and audience."""
    url = f"https://novelpia.com/top100/all/{period}/view/{audience}"
    r = session.get(url, timeout=30)
    rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
    ranking = {}
    for pos, nid in enumerate(rank_ids[:100], 1):
        ranking[nid] = pos
    return ranking


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


def rescrape_metadata(session, ranked_ids):
    """Fetch fresh metadata for ranked novels via the Novelpia search API.

    Returns dict: {novel_id_str: {title, author, cover, tags, views, likes, chapters, complete, age, updated}}
    """
    fresh = {}
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://novelpia.com/search",
    }

    # Fetch each novel individually via the search API
    for i, nid in enumerate(sorted(ranked_ids, key=int)):
        try:
            r = session.get("https://novelpia.com/proc/novel", params={
                "cmd": "novel_search",
                "search_type": "novel_no",
                "search_val": nid,
                "page": 1,
                "rows": 1,
            }, headers=headers, timeout=15)

            data = r.json()
            items = data.get("list", [])
            if items:
                item = items[0]
                cover = pick_cover(item)
                if cover.startswith(COVER_PREFIX):
                    cover = cover[len(COVER_PREFIX):]
                fresh[str(nid)] = {
                    "title": item.get("novel_name", ""),
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
        except Exception as e:
            print(f"  Warning: failed to fetch {nid}: {e}")

        if (i + 1) % 50 == 0:
            print(f"  Fetched {i+1}/{len(ranked_ids)} novels...", flush=True)
        time.sleep(0.15)

    return fresh


def main():
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    config = json.load(open(config_path, "r"))

    auth = NovelpiaAuth()
    loginkey = config.get("loginkey", "")
    if not loginkey:
        print("No loginkey in config.json!")
        sys.exit(1)
    auth.set_manual_key(loginkey)

    # Phase 1: Scrape rankings
    rankings = {}
    for audience_url, audience_label, _, _, _ in AUDIENCES:
        for period_url, period_label in PERIODS:
            label = f"{period_label} {audience_label}"
            print(f"Scraping {label}...")
            try:
                ranking = scrape_ranking(auth.session, period_url, audience_url)
            except Exception as e:
                print(f"  WARNING: Failed: {e}")
                ranking = {}
            print(f"  Got {len(ranking)} ranked novels")
            rankings[(audience_url, period_url)] = ranking

    # Collect all unique ranked novel IDs
    all_ranked_ids = set()
    for rm in rankings.values():
        all_ranked_ids.update(rm.keys())
    print(f"\nTotal unique ranked IDs: {len(all_ranked_ids)}")

    # Phase 2: Rescrape full metadata for ranked novels
    print(f"\nRescraping metadata for {len(all_ranked_ids)} ranked novels...")
    fresh_data = rescrape_metadata(auth.session, all_ranked_ids)
    print(f"  Got fresh data for {len(fresh_data)} novels")

    # Load existing data
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "novels.json")
    if not os.path.exists(data_path):
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    data = json.load(open(data_path, "r", encoding="utf-8"))
    print(f"\nLoaded {len(data)} novels from {data_path}")

    # Phase 3: Patch metadata + rankings
    updated = 0
    for novel in data:
        nid = str(novel[0])

        # Ensure array is long enough for all indices (up to [19])
        while len(novel) < 20:
            novel.append(0)

        # Update metadata if we have fresh data (indices 1-9)
        if nid in fresh_data:
            f = fresh_data[nid]
            novel[1] = f["title"]
            novel[2] = f["author"]
            novel[3] = f["cover"]
            novel[4] = f["tags"]
            novel[5] = f["views"]
            novel[6] = f["likes"]
            novel[7] = f["chapters"]
            novel[8] = f["complete"]
            novel[9] = f["updated"]
            novel[11] = f["age"]

        # Update rankings
        changed = False
        for audience_url, _, idx_weekly, idx_monthly, idx_daily in AUDIENCES:
            for period_url, idx in [("weekly", idx_weekly), ("month", idx_monthly), ("today", idx_daily)]:
                new_rank = rankings.get((audience_url, period_url), {}).get(nid, 0)
                if novel[idx] != new_rank:
                    novel[idx] = new_rank
                    changed = True

        if changed or nid in fresh_data:
            updated += 1

    print(f"Updated {updated} novels")

    # Save
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
