"""Scrape SFACG (SF轻小说) novel metadata for the NovelpiaDB site.

Uses the SFACG public API to fetch ALL novel listings via deep pagination.
No category filter — just paginate through the entire catalog.

Usage:
    python scripts/scrape_sfacg.py
    python scripts/scrape_sfacg.py --delay 0.2

Output: docs/data/sfacg_novels.json
"""

import sys, os, json, time, requests

sys.stdout.reconfigure(encoding='utf-8')

API_URL = "https://api.sfacg.com/novels"

HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape SFACG novels")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests")
    parser.add_argument("--max-pages", type=int, default=9999, help="Max pages to fetch")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)

    # Test connection
    print("Testing SFACG API...")
    try:
        r = session.get(API_URL, params={'page': 0, 'size': 1, 'sort': 'viewtimes'}, timeout=10)
        data = r.json()
        if data.get('status', {}).get('httpCode') != 200:
            print(f"  API error: {data.get('status', {})}")
            sys.exit(1)
        print(f"  OK")
    except Exception as e:
        print(f"  Connection error: {e}")
        sys.exit(1)

    all_novels = {}
    empty_count = 0

    print(f"\nScraping all novels (50/page, max {args.max_pages} pages)...")

    for page in range(args.max_pages):
        try:
            r = session.get(API_URL, params={
                "page": page,
                "size": 50,
                "sort": "novelid",  # Sequential by ID for full coverage
                "expand": "typeName,sysTags",
            }, timeout=15)

            if r.status_code != 200:
                print(f"\n  HTTP {r.status_code} at page {page}, stopping")
                break

            data = r.json()
            if data.get("status", {}).get("httpCode", 200) != 200:
                print(f"\n  API error at page {page}: {data.get('status', {}).get('msg')}")
                break

            items = data.get("data") or []
            if not items:
                empty_count += 1
                if empty_count >= 3:
                    print(f"\n  3 consecutive empty pages at {page}, done")
                    break
                continue
            else:
                empty_count = 0

            for item in items:
                nid = str(item.get("novelId", ""))
                if not nid or nid in all_novels:
                    continue

                expand = item.get("expand", {})
                sys_tags = expand.get("sysTags") or []
                tag_names = [t.get("tagName", "") for t in sys_tags if t.get("tagName")]
                type_name = expand.get("typeName", "")
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
                    "chapters": item.get("charCount", 0),
                    "complete": 1 if item.get("isFinish", False) else 0,
                    "updated": item.get("lastUpdateTime", ""),
                    "age": 19 if item.get("allowDown", 0) == 0 else 0,
                }

            if (page + 1) % 50 == 0:
                print(f"  Page {page+1}: {len(all_novels)} novels", flush=True)

            # Auto-save every 500 pages to prevent data loss
            if (page + 1) % 500 == 0 and all_novels:
                save_novels(all_novels)
                print(f"  [auto-saved {len(all_novels)} novels]", flush=True)

            time.sleep(args.delay)

        except Exception as e:
            print(f"\n  Error at page {page}: {e}")
            time.sleep(2)

    print(f"\nTotal: {len(all_novels)} novels")

    if not all_novels:
        print("No novels found!")
        return

    save_novels(all_novels)


def save_novels(all_novels):
    """Save novels to disk."""
    COVER_PREFIX = "https://rss.sfacg.com/web/novel/images/NovelCover/Big/"
    os.makedirs("docs/data", exist_ok=True)
    optimized = []
    for n in all_novels.values():
        tags = n.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        cover = n.get("cover", "")
        if cover.startswith(COVER_PREFIX):
            cover = cover[len(COVER_PREFIX):]
        optimized.append([
            n["id"], n["title"], n["author"], cover,
            tags, n.get("views", 0), n.get("likes", 0),
            n.get("chapters", 0), n.get("complete", 0),
            n.get("updated", ""), 0, n.get("age", 0),
        ])

    path = "docs/data/sfacg_novels.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved to {path} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
