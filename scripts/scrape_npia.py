"""Scrape all Novelpia novel metadata for the NovelDB site.

Searches across many common tags to maximize coverage,
unions results, and exports as JSON for the static site.

Usage:
    python scripts/scrape_novelpia.py

Reads loginkey from config.json in the project root.
Outputs: docs/data/novels.json
"""

import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from novelpia_auth import NovelpiaAuth

# Common tags to search — covers ~99.5% of novels
SEARCH_TAGS = [
    '판타지', '현대', '패러디', '하렘', '라이트노벨', '일상', '로맨스',
    '현대판타지', 'TS', '먼치킨', '중세', '전생', '집착', '아카데미',
    '고수위', '드라마', 'SF', '순애', '빙의', '피폐', '성장', '착각',
    '무협', '블루아카이브', '후회', '코미디', '이세계', '기타', '백합',
    '회귀', '약피폐', '아포칼립스', '얀데레', '게임', '환생', '남성향',
    '헌터', '조교', '복수', '인터넷방송', '남녀역전', '대체역사', '모험',
    '원신', '상태창', '공포', '생존', '전쟁', '가면라이더', '액션',
]

# Korean consonant chars — catches remaining novels missed by tag search
SWEEP_CHARS = list("가나다라마바사아자차카타파하")

def main():
    auth = NovelpiaAuth()
    with open('config.json', 'r') as f:
        config = json.load(f)
    auth.loginkey = config.get('loginkey', '')
    auth.session.cookies.set('LOGINKEY', auth.loginkey, domain='novelpia.com')

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://novelpia.com/search",
    }

    seen = set()
    novels = []

    for i, tag in enumerate(SEARCH_TAGS):
        print(f"[{i+1}/{len(SEARCH_TAGS)}] Searching: {tag}...", end=" ", flush=True)

        try:
            items = []
            ROWS = 30000
            for pg in range(1, 21):
                r = auth.session.get("https://novelpia.com/proc/novel", params={
                    "cmd": "novel_search",
                    "search_type": "all",
                    "search_val": tag,
                    "page": pg,
                    "rows": ROWS,
                    "novel_type": "",
                    "start_count_book": "",
                    "end_count_book": "",
                    "novel_age": "",
                    "start_days": "",
                    "sort_col": "last_viewdate",
                    "novel_genre": "",
                    "block_out": 0,
                    "block_stop": 0,
                    "is_contest": 0,
                    "is_complete": "",
                    "is_challenge": 0,
                    "list_display": "grid",
                }, headers=headers, timeout=120)

                text = r.text.strip()
                if not text:
                    break
                try:
                    data = r.json()
                except Exception:
                    print(f"invalid JSON p{pg}", end=" ")
                    break
                batch = data.get("list", [])
                items.extend(batch)
                if len(batch) < ROWS:
                    break
            new_count = 0

            for item in items:
                novel_id = item.get("novel_no")
                if not novel_id or novel_id in seen:
                    continue
                seen.add(novel_id)
                new_count += 1

                # novel_img_all / novel_thumb_all can be the string "None" for R19 novels
                # Fall back to cover_url, novel_img, novel_thumb in order
                def _pick_cover(it):
                    for k in ("novel_img_all", "novel_thumb_all", "cover_url", "novel_img", "novel_thumb"):
                        v = it.get(k)
                        if v and str(v) not in ("", "None", "null"):
                            v = str(v)
                            if v.startswith("//"):
                                return "https:" + v
                            if not v.startswith("http"):
                                return "https://novelpia.com" + v
                            return v
                    return ""
                cover = _pick_cover(item)

                novels.append({
                    "id": novel_id,
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
                })

            print(f"{len(items)} results, {new_count} new (total: {len(novels)})")

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(0.5)

    print(f"\n--- Phase 1 complete: {len(novels)} novels from tags ---\n")

    # Phase 2: Character sweep for remaining novels
    for i, ch in enumerate(SWEEP_CHARS):
        print(f"[{i+1}/{len(SWEEP_CHARS)}] Sweep: '{ch}'...", end=" ", flush=True)

        try:
            items = []
            ROWS = 30000
            for pg in range(1, 21):
                r = auth.session.get("https://novelpia.com/proc/novel", params={
                    "cmd": "novel_search",
                    "search_type": "all",
                    "search_val": ch,
                    "page": pg,
                    "rows": ROWS,
                    "novel_type": "",
                    "sort_col": "last_viewdate",
                    "block_out": 0,
                    "block_stop": 0,
                    "is_contest": 0,
                    "is_challenge": 0,
                }, headers=headers, timeout=120)

                text = r.text.strip()
                if not text:
                    break
                try:
                    data = r.json()
                except Exception:
                    print(f"invalid JSON p{pg}", end=" ")
                    break
                batch = data.get("list", [])
                items.extend(batch)
                if len(batch) < ROWS:
                    break
            new_count = 0

            for item in items:
                novel_id = item.get("novel_no")
                if not novel_id or novel_id in seen:
                    continue
                seen.add(novel_id)
                new_count += 1

                cover = _pick_cover(item)

                novels.append({
                    "id": novel_id,
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
                })

            print(f"{len(items)} results, {new_count} new (total: {len(novels)})")

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(0.3)

    # Phase 3: Scrape weekly, monthly & daily rankings (general + R19 adult)
    print(f"\n--- Scraping rankings ---")
    weekly_rank = {}
    monthly_rank = {}
    daily_rank = {}
    try:
        import re
        RANKING_PAGES = [
            ("week",  "all/all",    "weekly general",  weekly_rank),
            ("week",  "adult/plus", "weekly R19",       weekly_rank),
            ("month", "all/all",    "monthly general",  monthly_rank),
            ("month", "adult/plus", "monthly R19",      monthly_rank),
            ("today", "all/plus",   "daily general",    daily_rank),
            ("today", "adult/plus", "daily R19",        daily_rank),
        ]
        for period, audience, label, target in RANKING_PAGES:
            url = f"https://novelpia.com/top100/all/{period}/view/{audience}"
            r = auth.session.get(url, timeout=30)
            rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
            for pos, nid in enumerate(rank_ids[:100], 1):
                target[nid] = pos
            print(f"  {label}: {min(len(rank_ids), 100)} novels")
    except Exception as e:
        print(f"  Error scraping ranking: {e}")

    print(f"  Totals (before renumbering): {len(weekly_rank)} weekly, {len(monthly_rank)} monthly, {len(daily_rank)} daily")

    # Build a lookup: id -> likes for renumbering
    likes_by_id = {str(n["id"]): n.get("likes", 0) for n in novels}

    # Renumber merged rankings 1-N sorted by likes (descending)
    def renumber_by_likes(ranking):
        sorted_ids = sorted(ranking.keys(), key=lambda nid: likes_by_id.get(nid, 0), reverse=True)
        return {nid: pos for pos, nid in enumerate(sorted_ids, 1)}

    weekly_rank = renumber_by_likes(weekly_rank)
    monthly_rank = renumber_by_likes(monthly_rank)
    daily_rank = renumber_by_likes(daily_rank)

    print(f"  Renumbered: {len(weekly_rank)} weekly, {len(monthly_rank)} monthly, {len(daily_rank)} daily (by likes)")

    # Save output
    os.makedirs("docs/data", exist_ok=True)

    # Full version (for reference)
    full_path = "docs/data/novels_full.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(novels, f, ensure_ascii=False)
    print(f"\nFull: {len(novels)} novels -> {os.path.getsize(full_path) / 1024 / 1024:.1f} MB")

    # Optimized version for the site (array format, no synopsis, stripped cover prefix)
    COVER_PREFIX = "https://novelpia.com"
    optimized = []
    for n in novels:
        cover = n.get("cover", "")
        if cover.startswith(COVER_PREFIX):
            cover = cover[len(COVER_PREFIX):]
        nid = str(n["id"])
        optimized.append([
            n["id"],                # [0] id
            n["title"],             # [1] title
            n["author"],            # [2] author
            cover,                  # [3] cover (relative)
            n.get("tags", []),      # [4] tags
            n.get("views", 0),      # [5] views
            n.get("likes", 0),      # [6] likes
            n.get("chapters", 0),   # [7] chapters
            n.get("complete", 0),   # [8] complete
            n.get("updated", ""),   # [9] updated
            weekly_rank.get(nid, 0),# [10] weeklyRank (0=unranked)
            n.get("age", 0),        # [11] age rating (0=all, 15=teen, 19=adult)
            monthly_rank.get(nid, 0),# [12] monthlyRank (0=unranked)
            daily_rank.get(nid, 0), # [13] dailyRank (0=unranked)
        ])

    opt_path = "docs/data/novels.json"
    with open(opt_path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Site: {len(optimized)} novels -> {os.path.getsize(opt_path) / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    main()
