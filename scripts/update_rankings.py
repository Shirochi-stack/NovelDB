"""Update Novelpia weekly, monthly & daily rankings with authentication.

Scrapes the top100 pages (general + R19 adult) for daily, weekly, and monthly
views, then patches the existing novels.json with the new ranking data.

Usage:
    python scripts/update_rankings.py
"""

import sys, os, json, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from novelpia_auth import NovelpiaAuth

# (period_url, audience_url, label)
RANKING_PAGES = [
    ("week",  "all/all",    "weekly general"),
    ("week",  "adult/plus", "weekly R19"),
    ("month", "all/all",    "monthly general"),
    ("month", "adult/plus", "monthly R19"),
    ("today", "all/plus",   "daily general"),
    ("today", "adult/plus", "daily R19"),
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

    weekly = {}
    monthly = {}
    daily = {}

    for period, audience, label in RANKING_PAGES:
        print(f"Scraping {label}...")
        try:
            ranking = scrape_ranking(auth.session, period, audience)
        except Exception as e:
            print(f"  WARNING: Failed to scrape {label}: {e}")
            ranking = {}
        print(f"  Got {len(ranking)} ranked novels")

        # Merge into the appropriate dict (general + adult don't overlap)
        if period == "week":
            weekly.update(ranking)
        elif period == "month":
            monthly.update(ranking)
        elif period == "today":
            daily.update(ranking)

    print(f"\nTotals (before renumbering): {len(weekly)} weekly, {len(monthly)} monthly, {len(daily)} daily")

    # Load existing data
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "novels.json")
    if not os.path.exists(data_path):
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    data = json.load(open(data_path, "r", encoding="utf-8"))
    print(f"Loaded {len(data)} novels from {data_path}")

    # Build a lookup: id -> likes for renumbering
    likes_by_id = {str(novel[0]): (novel[6] if len(novel) > 6 else 0) for novel in data}

    # Renumber merged rankings 1-N sorted by likes (descending)
    def renumber_by_likes(ranking):
        """Take {id: rank} dict, re-rank 1-N sorted by likes descending."""
        sorted_ids = sorted(ranking.keys(), key=lambda nid: likes_by_id.get(nid, 0), reverse=True)
        return {nid: pos for pos, nid in enumerate(sorted_ids, 1)}

    weekly = renumber_by_likes(weekly)
    monthly = renumber_by_likes(monthly)
    daily = renumber_by_likes(daily)

    print(f"Renumbered: {len(weekly)} weekly, {len(monthly)} monthly, {len(daily)} daily (by likes)")

    updated_weekly = 0
    updated_monthly = 0
    updated_daily = 0

    for novel in data:
        nid = str(novel[0])

        # [10] = weeklyRank
        old_weekly = novel[10] if len(novel) > 10 else 0
        new_weekly = weekly.get(nid, 0)
        novel[10] = new_weekly
        if new_weekly != old_weekly:
            updated_weekly += 1

        # [12] = monthlyRank (ensure array is long enough)
        while len(novel) < 13:
            novel.append(0)
        old_monthly = novel[12]
        new_monthly = monthly.get(nid, 0)
        novel[12] = new_monthly
        if new_monthly != old_monthly:
            updated_monthly += 1

        # [13] = dailyRank (ensure array is long enough)
        while len(novel) < 14:
            novel.append(0)
        old_daily = novel[13]
        new_daily = daily.get(nid, 0)
        novel[13] = new_daily
        if new_daily != old_daily:
            updated_daily += 1

    print(f"\nUpdated {updated_weekly} weekly, {updated_monthly} monthly, {updated_daily} daily ranks")

    # Save
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
