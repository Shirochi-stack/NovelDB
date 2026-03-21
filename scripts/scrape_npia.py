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
    '디스토피아',
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

    # Helper to pick the best cover URL from a search result item
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

    # Phase 3: Scrape rankings per audience (all, adult, teen) × period (weekly, monthly, daily)
    print(f"\n--- Scraping rankings ---")
    # rankings[(audience, period)] = {novel_id: rank}
    rankings = {}
    try:
        import re
        AUDIENCES = [
            ("all/plus",   "all"),
            ("adult/plus", "adult"),
            ("teen/plus",  "teen"),
        ]
        PERIODS = [
            ("weekly", "weekly"),
            ("month",  "monthly"),
            ("today",  "daily"),
        ]
        for audience_url, audience_label in AUDIENCES:
            for period_url, period_label in PERIODS:
                label = f"{period_label} {audience_label}"
                url = f"https://novelpia.com/top100/all/{period_url}/view/{audience_url}"
                r = auth.session.get(url, timeout=30)
                rank_ids = list(dict.fromkeys(re.findall(r'/novel/(\d+)', r.text)))
                ranking = {}
                for pos, nid in enumerate(rank_ids[:100], 1):
                    ranking[nid] = pos
                rankings[(audience_url, period_url)] = ranking
                print(f"  {label}: {len(ranking)} novels")
    except Exception as e:
        print(f"  Error scraping ranking: {e}")

    def get_rank(nid, audience, period):
        return rankings.get((audience, period), {}).get(nid, 0)

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
            n["id"],                             # [0]  id
            n["title"],                          # [1]  title
            n["author"],                         # [2]  author
            cover,                               # [3]  cover (relative)
            n.get("tags", []),                   # [4]  tags
            n.get("views", 0),                   # [5]  views
            n.get("likes", 0),                   # [6]  likes
            n.get("chapters", 0),                # [7]  chapters
            n.get("complete", 0),                # [8]  complete
            n.get("updated", ""),                # [9]  updated
            get_rank(nid, "all/plus", "weekly"),  # [10] weeklyRank (all)
            n.get("age", 0),                     # [11] age rating
            get_rank(nid, "all/plus", "month"),   # [12] monthlyRank (all)
            get_rank(nid, "all/plus", "today"),   # [13] dailyRank (all)
            get_rank(nid, "adult/plus", "weekly"),# [14] weeklyRankAdult
            get_rank(nid, "adult/plus", "month"), # [15] monthlyRankAdult
            get_rank(nid, "adult/plus", "today"), # [16] dailyRankAdult
            get_rank(nid, "teen/plus", "weekly"), # [17] weeklyRankTeen
            get_rank(nid, "teen/plus", "month"),  # [18] monthlyRankTeen
            get_rank(nid, "teen/plus", "today"),  # [19] dailyRankTeen
        ])

    opt_path = "docs/data/novels.json"
    with open(opt_path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Site: {len(optimized)} novels -> {os.path.getsize(opt_path) / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    main()
