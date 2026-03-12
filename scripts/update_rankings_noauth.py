"""Update Novelpia weekly, monthly & daily rankings WITHOUT authentication.

Scrapes top100 pages for each audience (all, adult, teen) × each period.
R19 pages require auth and may fail in CI — existing R19 ranks are preserved.

Data indices:
  [10] weeklyRank (all)    [12] monthlyRank (all)    [13] dailyRank (all)
  [14] weeklyRankAdult     [15] monthlyRankAdult     [16] dailyRankAdult
  [17] weeklyRankTeen      [18] monthlyRankTeen      [19] dailyRankTeen

Usage:
    python scripts/update_rankings_noauth.py
"""

import sys, os, json, re, requests

sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

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
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Initializing session...")
    try:
        session.get("https://novelpia.com", timeout=15)
    except Exception:
        pass

    rankings = {}
    failed_audiences = set()

    for audience_url, audience_label, _, _, _ in AUDIENCES:
        for period_url, period_label in PERIODS:
            label = f"{period_label} {audience_label}"
            print(f"Scraping {label}...")
            try:
                ranking = scrape_ranking(session, period_url, audience_url)
            except Exception as e:
                print(f"  WARNING: Failed: {e}")
                ranking = {}
            if len(ranking) == 0:
                print(f"  (may require authentication — skipping)")
                failed_audiences.add(audience_url)
            else:
                print(f"  Got {len(ranking)} ranked novels")
            rankings[(audience_url, period_url)] = ranking

    # Check if we got anything at all
    total = sum(len(r) for r in rankings.values())
    if total == 0:
        print("ERROR: Could not fetch any rankings.")
        sys.exit(1)

    if "adult/plus" in failed_audiences:
        print("\nR19 (adult) pages unavailable — existing adult ranks will be preserved")

    # Load existing data
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "novels.json")
    if not os.path.exists(data_path):
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    data = json.load(open(data_path, "r", encoding="utf-8"))
    print(f"Loaded {len(data)} novels from {data_path}")

    updated = 0
    for novel in data:
        nid = str(novel[0])

        # Ensure array is long enough for all indices (up to [19])
        while len(novel) < 20:
            novel.append(0)

        changed = False
        for audience_url, _, idx_weekly, idx_monthly, idx_daily in AUDIENCES:
            # Skip this audience if it failed (preserve existing ranks)
            if audience_url in failed_audiences:
                continue

            for period_url, idx in [("weekly", idx_weekly), ("month", idx_monthly), ("today", idx_daily)]:
                new_rank = rankings.get((audience_url, period_url), {}).get(nid, 0)
                if novel[idx] != new_rank:
                    novel[idx] = new_rank
                    changed = True

        if changed:
            updated += 1

    print(f"\nUpdated {updated} novels")

    # Save
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
