"""Update Novelpia weekly, monthly & daily rankings with authentication.

Scrapes the top100 pages for each audience (all, adult, teen) × each period
(daily, weekly, monthly), then patches novels.json with audience-specific ranks.

Data indices:
  [10] weeklyRank (all)    [12] monthlyRank (all)    [13] dailyRank (all)
  [14] weeklyRankAdult     [15] monthlyRankAdult     [16] dailyRankAdult
  [17] weeklyRankTeen      [18] monthlyRankTeen      [19] dailyRankTeen

Usage:
    python scripts/update_rankings.py
"""

import sys, os, json, re

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


def scrape_ranking(session, period, audience):
    """Scrape top100 for a given period and audience."""
    url = f"https://novelpia.com/top100/all/{period}/view/{audience}"
    r = session.get(url, timeout=30)
    rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
    ranking = {}
    for pos, nid in enumerate(rank_ids[:100], 1):
        ranking[nid] = pos
    return ranking


def main():
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    config = json.load(open(config_path, "r"))

    auth = NovelpiaAuth()
    loginkey = config.get("loginkey", "")
    if not loginkey:
        print("No loginkey in config.json!")
        sys.exit(1)
    auth.set_manual_key(loginkey)

    # rankings[audience_url][(period_url)] = {novel_id: rank}
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

    # Load existing data
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "novels.json")
    if not os.path.exists(data_path):
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    data = json.load(open(data_path, "r", encoding="utf-8"))
    print(f"\nLoaded {len(data)} novels from {data_path}")

    updated = 0
    for novel in data:
        nid = str(novel[0])

        # Ensure array is long enough for all indices (up to [19])
        while len(novel) < 20:
            novel.append(0)

        changed = False
        for audience_url, _, idx_weekly, idx_monthly, idx_daily in AUDIENCES:
            for period_url, idx in [("weekly", idx_weekly), ("month", idx_monthly), ("today", idx_daily)]:
                new_rank = rankings.get((audience_url, period_url), {}).get(nid, 0)
                if novel[idx] != new_rank:
                    novel[idx] = new_rank
                    changed = True

        if changed:
            updated += 1

    print(f"Updated {updated} novels")

    # Save
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
