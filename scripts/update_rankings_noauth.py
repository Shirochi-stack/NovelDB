"""Update Novelpia weekly, monthly & daily rankings WITHOUT authentication.

The rankings page (top100) is publicly accessible — no login required.
This script scrapes the public rankings (general + R19 adult) and patches
the existing novels.json.

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
    if r.status_code != 200:
        print(f"  Warning: got status {r.status_code} for {url}")
        return {}
    rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
    ranking = {}
    for pos, nid in enumerate(rank_ids[:100], 1):
        ranking[nid] = pos
    return ranking


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    # Visit main page first to get any session cookies
    print("Initializing session...")
    try:
        session.get("https://novelpia.com", timeout=15)
    except Exception:
        pass

    weekly = {}
    monthly = {}
    daily = {}

    for period, audience, label in RANKING_PAGES:
        print(f"Scraping {label}...")
        ranking = scrape_ranking(session, period, audience)
        print(f"  Got {len(ranking)} ranked novels")

        # Merge into the appropriate dict (general + adult don't overlap)
        if period == "week":
            weekly.update(ranking)
        elif period == "month":
            monthly.update(ranking)
        elif period == "today":
            daily.update(ranking)

    if len(weekly) == 0 and len(monthly) == 0 and len(daily) == 0:
        print("ERROR: Could not fetch any rankings. The page may require auth.")
        sys.exit(1)

    print(f"\nTotals: {len(weekly)} weekly, {len(monthly)} monthly, {len(daily)} daily")

    # Load existing data
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "novels.json")
    if not os.path.exists(data_path):
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    data = json.load(open(data_path, "r", encoding="utf-8"))
    print(f"Loaded {len(data)} novels from {data_path}")

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
