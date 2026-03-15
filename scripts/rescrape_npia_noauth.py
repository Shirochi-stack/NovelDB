"""Full Novelpia catalog rescrape WITHOUT authentication.

Merges freshly scraped data into the existing novels.json, preserving
R19 novel data that cannot be reached without a login cookie.

Merge strategy:
  - Novels found in fresh scrape -> update metadata (title, cover, views, etc.)
  - New novels not in existing data -> append
  - Existing novels NOT found in fresh scrape (R19) -> keep as-is

Also scrapes public top100 rankings. For audiences that fail (adult/R19),
existing ranks are preserved.

Usage:
    python scripts/rescrape_npia_noauth.py
    python scripts/rescrape_npia_noauth.py --dry-run
"""

import sys, os, json, time, re, argparse, requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

API_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://novelpia.com/search",
}

COVER_PREFIX = "https://novelpia.com"

# Same tag list as scrape_npia.py for maximum coverage
SEARCH_TAGS = [
    '판타지', '현대', '패러디', '하렘', '라이트노벨', '일상', '로맨스',
    '현대판타지', 'TS', '먼치킨', '중세', '전생', '집착', '아카데미',
    '고수위', '드라마', 'SF', '순애', '빙의', '피폐', '성장', '착각',
    '무협', '블루아카이브', '후회', '코미디', '이세계', '기타', '백합',
    '회귀', '약피폐', '아포칼립스', '얀데레', '게임', '환생', '남성향',
    '헌터', '조교', '복수', '인터넷방송', '남녀역전', '대체역사', '모험',
    '원신', '상태창', '공포', '생존', '전쟁', '가면라이더', '액션',
]

SWEEP_CHARS = list("가나다라마바사아자차카타파하")

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


def extract_novel(item):
    """Extract novel metadata dict from an API response item."""
    cover = pick_cover(item)
    return {
        "id": item.get("novel_no"),
        "title": item.get("novel_name", ""),
        "synopsis": item.get("novel_story", ""),
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


def search_novels(session, search_val, search_type="all"):
    """Search Novelpia API for novels matching a search term."""
    items = []
    ROWS = 30000
    for pg in range(1, 21):
        try:
            r = session.get("https://novelpia.com/proc/novel", params={
                "cmd": "novel_search",
                "search_type": search_type,
                "search_val": search_val,
                "page": pg,
                "rows": ROWS,
                "novel_type": "",
                "sort_col": "last_viewdate",
                "block_out": 0,
                "block_stop": 0,
                "is_contest": 0,
                "is_challenge": 0,
            }, headers=API_HEADERS, timeout=120)

            text = r.text.strip()
            if not text:
                break
            try:
                data = r.json()
            except Exception:
                break
            batch = data.get("list", [])
            items.extend(batch)
            if len(batch) < ROWS:
                break
        except Exception as e:
            print(f"ERROR page {pg}: {e}")
            break
    return items


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
    parser = argparse.ArgumentParser(description="Rescrape Novelpia catalog (no auth)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run scrape and show merge summary without saving")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_path = os.path.join(base_dir, "docs", "data", "novels.json")
    full_path = os.path.join(base_dir, "docs", "data", "novels_full.json")

    session = requests.Session()
    session.headers.update(HEADERS)

    print("Initializing session...")
    try:
        session.get("https://novelpia.com", timeout=15)
    except Exception:
        pass

    # ── Phase 1: Tag search ──────────────────────────────────────
    seen = set()
    fresh_novels = {}  # id -> novel dict

    for i, tag in enumerate(SEARCH_TAGS):
        print(f"[{i+1}/{len(SEARCH_TAGS)}] Searching: {tag}...", end=" ", flush=True)
        try:
            items = search_novels(session, tag)
            new_count = 0
            for item in items:
                novel_id = item.get("novel_no")
                if not novel_id or novel_id in seen:
                    continue
                seen.add(novel_id)
                new_count += 1
                fresh_novels[str(novel_id)] = extract_novel(item)
            print(f"{len(items)} results, {new_count} new (total: {len(fresh_novels)})")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(0.5)

    print(f"\n--- Phase 1 complete: {len(fresh_novels)} novels from tags ---\n")

    # ── Phase 2: Character sweep ─────────────────────────────────
    for i, ch in enumerate(SWEEP_CHARS):
        print(f"[{i+1}/{len(SWEEP_CHARS)}] Sweep: '{ch}'...", end=" ", flush=True)
        try:
            items = search_novels(session, ch)
            new_count = 0
            for item in items:
                novel_id = item.get("novel_no")
                if not novel_id or novel_id in seen:
                    continue
                seen.add(novel_id)
                new_count += 1
                fresh_novels[str(novel_id)] = extract_novel(item)
            print(f"{len(items)} results, {new_count} new (total: {len(fresh_novels)})")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(0.3)

    print(f"\n--- Phase 2 complete: {len(fresh_novels)} total scraped novels ---\n")

    # ── Phase 3: Scrape rankings ─────────────────────────────────
    print("--- Scraping rankings ---")
    rankings = {}
    failed_audiences = set()

    for audience_url, audience_label, _, _, _ in AUDIENCES:
        for period_url, period_label in PERIODS:
            label = f"{period_label} {audience_label}"
            print(f"Scraping {label}...", end=" ", flush=True)
            try:
                ranking = scrape_ranking(session, period_url, audience_url)
            except Exception as e:
                print(f"WARNING: Failed: {e}")
                ranking = {}
            if len(ranking) == 0:
                print("(may require auth — skipping)")
                failed_audiences.add(audience_url)
            else:
                print(f"{len(ranking)} novels")
            rankings[(audience_url, period_url)] = ranking

    if "adult/plus" in failed_audiences:
        print("\nR19 (adult) ranking pages unavailable — existing adult ranks will be preserved")

    # ── Phase 4: Merge into existing data ────────────────────────
    print(f"\n--- Merging into existing data ---")

    if not os.path.exists(data_path):
        print(f"No existing {data_path} — creating fresh dataset")
        existing_data = []
    else:
        with open(data_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
        print(f"Loaded {len(existing_data)} existing novels")

    # Build lookup: novel_id_str -> index in existing_data
    existing_lookup = {}
    for idx, novel in enumerate(existing_data):
        nid = str(novel[0])
        existing_lookup[nid] = idx

    stats = {"updated": 0, "added": 0, "preserved_r19": 0, "preserved_other": 0}

    def to_relative_cover(cover):
        """Strip the cover prefix for storage."""
        if cover.startswith(COVER_PREFIX):
            return cover[len(COVER_PREFIX):]
        return cover

    def get_rank(nid, audience, period):
        return rankings.get((audience, period), {}).get(nid, 0)

    # Update existing novels or add new ones
    for nid, novel in fresh_novels.items():
        cover = to_relative_cover(novel["cover"])

        if nid in existing_lookup:
            # Update existing entry's metadata
            idx = existing_lookup[nid]
            entry = existing_data[idx]

            # Ensure array is long enough
            while len(entry) < 20:
                entry.append(0)

            entry[1] = novel["title"]
            entry[2] = novel["author"]
            entry[3] = cover
            entry[4] = novel["tags"]
            entry[5] = novel["views"]
            entry[6] = novel["likes"]
            entry[7] = novel["chapters"]
            entry[8] = novel["complete"]
            entry[9] = novel["updated"]
            entry[11] = novel["age"]
            stats["updated"] += 1
        else:
            # New novel — append in array format
            entry = [
                novel["id"],              # [0]  id
                novel["title"],           # [1]  title
                novel["author"],          # [2]  author
                cover,                    # [3]  cover
                novel["tags"],            # [4]  tags
                novel["views"],           # [5]  views
                novel["likes"],           # [6]  likes
                novel["chapters"],        # [7]  chapters
                novel["complete"],        # [8]  complete
                novel["updated"],         # [9]  updated
                0,                        # [10] weeklyRank (all)
                novel["age"],             # [11] age rating
                0,                        # [12] monthlyRank (all)
                0,                        # [13] dailyRank (all)
                0,                        # [14] weeklyRankAdult
                0,                        # [15] monthlyRankAdult
                0,                        # [16] dailyRankAdult
                0,                        # [17] weeklyRankTeen
                0,                        # [18] monthlyRankTeen
                0,                        # [19] dailyRankTeen
            ]
            existing_data.append(entry)
            existing_lookup[nid] = len(existing_data) - 1
            stats["added"] += 1

    # Count preserved novels (ones in existing data but NOT in fresh scrape)
    for nid in existing_lookup:
        if nid not in fresh_novels:
            # Check if it's an R19 novel (age == 19 at index 11)
            idx = existing_lookup[nid]
            entry = existing_data[idx]
            age = entry[11] if len(entry) > 11 else 0
            if age == 19:
                stats["preserved_r19"] += 1
            else:
                stats["preserved_other"] += 1

    # ── Phase 5: Patch rankings ──────────────────────────────────
    rank_changes = 0
    for novel in existing_data:
        nid = str(novel[0])
        while len(novel) < 20:
            novel.append(0)

        for audience_url, _, idx_weekly, idx_monthly, idx_daily in AUDIENCES:
            if audience_url in failed_audiences:
                continue
            for period_url, idx in [("weekly", idx_weekly), ("month", idx_monthly), ("today", idx_daily)]:
                new_rank = rankings.get((audience_url, period_url), {}).get(nid, 0)
                if novel[idx] != new_rank:
                    novel[idx] = new_rank
                    rank_changes += 1

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  Total novels in dataset: {len(existing_data)}")
    print(f"  Freshly scraped:         {len(fresh_novels)}")
    print(f"  Updated (existing):      {stats['updated']}")
    print(f"  Added (new):             {stats['added']}")
    print(f"  Preserved (R19):         {stats['preserved_r19']}")
    print(f"  Preserved (other):       {stats['preserved_other']}")
    print(f"  Rank changes:            {rank_changes}")
    print(f"{'='*50}")

    if args.dry_run:
        print("\n*** DRY RUN — no files written ***")
        return

    # ── Save ─────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(data_path), exist_ok=True)

    # Save optimized version (array format, no synopsis)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nSaved {data_path} ({os.path.getsize(data_path) / 1024 / 1024:.1f} MB)")

    # Save full version with synopses for description extraction
    full_novels = []
    for novel in fresh_novels.values():
        full_novels.append(novel)

    # Also include existing R19 novels in full output if novels_full.json exists
    if os.path.exists(full_path):
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                old_full = json.load(f)
            old_full_lookup = {}
            for entry in old_full:
                oid = str(entry.get("id", ""))
                if oid:
                    old_full_lookup[oid] = entry
            # Merge R19 novels from old full data
            r19_added = 0
            full_ids = {str(n["id"]) for n in full_novels}
            for oid, entry in old_full_lookup.items():
                if oid not in full_ids:
                    full_novels.append(entry)
                    r19_added += 1
            if r19_added:
                print(f"  Merged {r19_added} R19 novels into novels_full.json")
        except Exception as e:
            print(f"  Warning: Could not merge old full data: {e}")

    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(full_novels, f, ensure_ascii=False)
    print(f"Saved {full_path} ({os.path.getsize(full_path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
