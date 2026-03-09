"""Scrape SFACG (SF轻小说) novel metadata for the NovelpiaDB site.

Uses the SFACG public API to fetch novel listings.
Outputs: docs/data/sfacg_novels.json (optimized array format matching Novelpia's)

Usage:
    python scripts/scrape_sfacg.py
"""

import sys, os, json, time, requests

sys.stdout.reconfigure(encoding='utf-8')

API_BASE = "https://api.sfacg.com"

HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
    "SFSecurity": "",
}

# SFACG genre/category IDs
CATEGORIES = [
    21,   # 奇幻·玄幻 (Fantasy)
    1,    # 都市 (Urban/Modern)
    17,   # 科幻·末世 (Sci-Fi/Apocalypse)
    4,    # 武侠·仙侠 (Wuxia/Xianxia)
    7,    # 游戏·竞技 (Game/Sports)
    22,   # 变身·百合 (Genderbend/Yuri)
    6,    # 悬疑·灵异 (Mystery/Horror)
    8,    # 历史·军事 (History/Military)
    9,    # 二次元 (ACG)
    30,   # 轻小说 (Light Novel)
    10,   # 同人 (Fan Fiction)
    12,   # 其他 (Other)
]


def fetch_sfacg_novels(max_pages_per_cat=50, delay=0.3):
    """Fetch novel metadata from SFACG API."""
    all_novels = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    for cat_id in CATEGORIES:
        print(f"\n  Category {cat_id}:")
        page = 0
        while page < max_pages_per_cat:
            try:
                url = f"{API_BASE}/novels"
                params = {
                    "page": page,
                    "size": 50,
                    "tid": cat_id,
                    "sort": "viewtimes",
                    "systag": "",
                    "isfinish": "",
                    "isfree": "",
                    "updatedays": "",
                    "expand": "typeName,sysTags",
                }

                resp = session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    print(f"    Page {page}: HTTP {resp.status_code}, stopping")
                    break

                data = resp.json()
                status = data.get("status", {})
                if status.get("httpCode", 200) != 200:
                    print(f"    Page {page}: API error: {status.get('msg', 'Unknown')}")
                    break

                novels_list = data.get("data", [])
                if not novels_list:
                    print(f"    Page {page}: empty, stopping")
                    break

                new_count = 0
                for item in novels_list:
                    nid = str(item.get("novelId", ""))
                    if not nid or nid in all_novels:
                        continue
                    new_count += 1

                    # Extract tags
                    sys_tags = item.get("sysTags", [])
                    tag_names = [t.get("sysTagName", "") for t in sys_tags if t.get("sysTagName")]
                    type_name = item.get("typeName", "")
                    if type_name and type_name not in tag_names:
                        tag_names.insert(0, type_name)

                    all_novels[nid] = {
                        "id": nid,
                        "title": item.get("novelName", ""),
                        "author": item.get("authorName", ""),
                        "cover": item.get("novelCover", ""),
                        "tags": tag_names,
                        "views": item.get("viewTimes", 0),
                        "likes": item.get("markCount", 0),
                        "chapters": item.get("charCount", 0),  # char count, not chapters
                        "complete": 1 if item.get("isFinish", False) else 0,
                        "updated": item.get("lastUpdateTime", ""),
                        "age": 19 if item.get("allowDown", 0) == 0 else 0,
                    }

                print(f"    Page {page}: {len(novels_list)} items, {new_count} new (total: {len(all_novels)})")

                if new_count == 0:
                    break
                page += 1
                time.sleep(delay)

            except Exception as e:
                print(f"    Page {page}: Error - {e}")
                break

    return list(all_novels.values())


def main():
    print("Scraping SFACG novel metadata...")
    novels = fetch_sfacg_novels()
    print(f"\nTotal: {len(novels)} novels")

    if not novels:
        print("No novels found. SFACG API may have changed.")
        print("You can manually download sfacg_novels.jsonl from SpazzTL's repo:")
        print("  https://github.com/SpazzTL/Novelpedia/raw/main/static/sfacg_novels.jsonl")
        print("Then run: python scripts/convert_jsonl.py sfacg")
        return

    # Save as optimized array format
    os.makedirs("docs/data", exist_ok=True)
    optimized = []
    for n in novels:
        tags = n.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        optimized.append([
            n["id"],                # [0]
            n["title"],             # [1]
            n["author"],            # [2]
            n.get("cover", ""),     # [3]
            tags,                   # [4]
            n.get("views", 0),      # [5]
            n.get("likes", 0),      # [6]
            n.get("chapters", 0),   # [7]
            n.get("complete", 0),   # [8]
            n.get("updated", ""),   # [9]
            0,                      # [10] weeklyRank
            n.get("age", 0),        # [11]
        ])

    path = "docs/data/sfacg_novels.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
