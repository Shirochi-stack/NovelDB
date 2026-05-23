"""Update Novelpia weekly, monthly & daily rankings WITHOUT authentication.

Also captures novel synopses from the API and merges them into descriptions.txt.

Scrapes top100 pages for each audience (all, adult, teen) × each period.
Also rescrapes full metadata (cover, title, views, etc.) for all ranked novels
so that data stays fresh.
R19 pages require auth and may fail in CI — existing R19 ranks are preserved.

Data indices:
  [0] id  [1] title  [2] author  [3] cover  [4] tags  [5] views  [6] likes
  [7] chapters  [8] complete  [9] updated
  [10] weeklyRank (all)    [12] monthlyRank (all)    [13] dailyRank (all)
  [14] weeklyRankAdult     [15] monthlyRankAdult     [16] dailyRankAdult
  [17] weeklyRankTeen      [18] monthlyRankTeen      [19] dailyRankTeen

Usage:
    python scripts/update_rankings_noauth.py
"""

import sys, os, json, re, time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

COVER_PREFIX = "https://novelpia.com"

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
        raise RuntimeError(f"HTTP {r.status_code} for {url}")
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
    """Fetch fresh metadata for ranked novels via bulk tag searches.

    Uses search_type=novel_genre with common tags to fetch large batches,
    then filters to only the ranked IDs we need. This avoids the broken
    search_type=novel_no endpoint which causes PHP memory crashes.

    Returns dict: {novel_id_str: {title, author, cover, tags, views, likes, chapters, complete, age, updated}}
    """
    fresh = {}
    remaining = set(str(nid) for nid in ranked_ids)
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://novelpia.com/search",
    }

    # Common tags that collectively cover most novels
    SEARCH_TAGS = [
        '판타지', '현대', '로맨스', '라이트노벨', '패러디', '하렘',
        '일상', '현대판타지', '먼치킨', '전생', '아카데미', '회귀',
    ]

    def extract_item(item):
        cover = pick_cover(item)
        if cover.startswith(COVER_PREFIX):
            cover = cover[len(COVER_PREFIX):]
        return {
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
            "synopsis": item.get("novel_story", ""),
        }

    for tag in SEARCH_TAGS:
        if not remaining:
            break
        try:
            r = session.get("https://novelpia.com/proc/novel", params={
                "cmd": "novel_search",
                "search_type": "novel_genre",
                "search_val": tag,
                "page": 1,
                "rows": 30000,
                "sort_col": "last_viewdate",
                "block_out": 0,
                "block_stop": 0,
            }, headers=headers, timeout=60)

            data = r.json()
            items = data.get("list", [])
            found_count = 0
            for item in items:
                nid = str(item.get("novel_no", ""))
                if nid in remaining:
                    fresh[nid] = extract_item(item)
                    remaining.discard(nid)
                    found_count += 1
            print(f"  [{tag}] {len(items)} novels, {found_count} ranked matches "
                  f"({len(remaining)} still missing)")
        except Exception as e:
            print(f"  [{tag}] Warning: {e}")
        time.sleep(0.5)

    if remaining:
        print(f"  {len(remaining)} ranked novels not found via tags, "
              f"trying character sweep...")
        # Try broad character-based searches for remaining
        for ch in "가나다라마바사아자차카타파하":
            if not remaining:
                break
            try:
                r = session.get("https://novelpia.com/proc/novel", params={
                    "cmd": "novel_search",
                    "search_type": "all",
                    "search_val": ch,
                    "page": 1,
                    "rows": 30000,
                    "sort_col": "last_viewdate",
                    "block_out": 0,
                    "block_stop": 0,
                }, headers=headers, timeout=60)

                data = r.json()
                items = data.get("list", [])
                found_count = 0
                for item in items:
                    nid = str(item.get("novel_no", ""))
                    if nid in remaining:
                        fresh[nid] = extract_item(item)
                        remaining.discard(nid)
                        found_count += 1
                if found_count:
                    print(f"  [sweep '{ch}'] found {found_count} "
                          f"({len(remaining)} still missing)")
            except Exception as e:
                print(f"  [sweep '{ch}'] Warning: {e}")
            time.sleep(0.5)

    if remaining:
        print(f"  Note: {len(remaining)} novels could not be found "
              f"(may be deleted or R19-only)")

    return fresh



def main():
    from novelpia_auth import NovelpiaAuth

    auth = NovelpiaAuth()
    session = auth.session
    session.headers.update(HEADERS)

    print("Initializing session...")
    try:
        session.get("https://novelpia.com", timeout=15)
    except Exception:
        pass

    print("  Using generated Novelpia session key; no username/password required")

    # Phase 1: Scrape rankings
    rankings = {}
    failed_audiences = set()
    public_failures = []

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
                if audience_url == "adult/plus":
                    print(f"  (R19 may require authentication - preserving existing ranks)")
                else:
                    print(f"  ERROR: public ranking returned no novels")
                    public_failures.append(label)
                failed_audiences.add(audience_url)
            else:
                print(f"  Got {len(ranking)} ranked novels")
            rankings[(audience_url, period_url)] = ranking

    if public_failures:
        print("ERROR: Could not fetch required public rankings: "
              + ", ".join(public_failures))
        sys.exit(1)

    if "adult/plus" in failed_audiences:
        print("\nR19 (adult) pages unavailable - existing adult ranks will be preserved")

    # Collect all unique ranked novel IDs
    all_ranked_ids = set()
    for rm in rankings.values():
        all_ranked_ids.update(rm.keys())
    print(f"\nTotal unique ranked IDs: {len(all_ranked_ids)}")

    # Phase 2: Rescrape full metadata for ranked novels
    print(f"\nRescraping metadata for {len(all_ranked_ids)} ranked novels...")
    fresh_data = rescrape_metadata(session, all_ranked_ids)
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

        # Update metadata if we have fresh data (indices 1-9, 11)
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
            # Skip this audience if it failed (preserve existing ranks)
            if audience_url in failed_audiences:
                continue

            for period_url, idx in [("weekly", idx_weekly), ("month", idx_monthly), ("today", idx_daily)]:
                new_rank = rankings.get((audience_url, period_url), {}).get(nid, 0)
                if novel[idx] != new_rank:
                    novel[idx] = new_rank
                    changed = True

        if changed or nid in fresh_data:
            updated += 1

    print(f"\nUpdated {updated} novels")

    # Save
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")

    # Phase 4: Merge fresh descriptions into descriptions.txt
    desc_path = os.path.join(BASE_DIR, "docs", "data", "descriptions.txt")
    # Load existing descriptions (preserve translations)
    existing = {}  # nid -> (korean, english)
    if os.path.exists(desc_path):
        with open(desc_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split("|||")
                nid = parts[0].strip()
                if not nid:
                    continue
                korean = parts[1].strip() if len(parts) > 1 else ""
                english = parts[2].strip() if len(parts) >= 3 else ""
                existing[nid] = (korean, english)

    # Merge fresh synopses from ranked novels
    new_count = 0
    updated_count = 0
    for nid, meta in fresh_data.items():
        synopsis = meta.get("synopsis", "")
        if not synopsis:
            continue
        # Normalize newlines
        normed = synopsis.replace("\r\n", "\n").replace("\r", "\n")
        normed = re.sub(r"\n{2,}", "\n", normed).strip()
        flat = normed.replace("\n", "\\n")
        if nid in existing:
            old_korean, english = existing[nid]
            if old_korean != flat:
                existing[nid] = (flat, english)
                updated_count += 1
        else:
            existing[nid] = (flat, "")
            new_count += 1

    # Write back: translated first, untranslated at bottom
    translated = []
    untranslated = []
    for nid, (korean, english) in existing.items():
        row = f"{nid}|||{korean}|||{english}\n"
        if english:
            translated.append(row)
        else:
            untranslated.append(row)

    with open(desc_path, "w", encoding="utf-8") as f:
        f.writelines(translated)
        f.writelines(untranslated)

    total = len(translated) + len(untranslated)
    print(f"\nDescriptions: {total} total ({new_count} new, {updated_count} updated)")


if __name__ == "__main__":
    main()
