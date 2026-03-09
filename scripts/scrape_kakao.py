"""Scrape KakaoPage novel metadata via the BFF REST API.

Uses the search API at bff-page.kakao.com which supports pagination.
No browser needed — just plain HTTP requests.

Requires: Korean VPN active

Usage:
    python scripts/scrape_kakao.py
    python scripts/scrape_kakao.py --delay 0.2

Output: docs/data/kakao_novels.json
"""

import sys, os, json, time, re, argparse
import requests

sys.stdout.reconfigure(encoding='utf-8')

BFF_SEARCH_URL = 'https://bff-page.kakao.com/api/gateway/api/v1/search/series'

# Korean syllable blocks cover all possible title prefixes
SEARCH_TERMS = [
    '가', '나', '다', '라', '마', '바', '사', '아', '자', '차', '카', '타', '파', '하',
]

THUMB_PREFIX = 'https://dn-img-page.kakao.com/download/resource?kid='


def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Referer': 'https://page.kakao.com/',
        'Origin': 'https://page.kakao.com',
    })
    return s


def scrape_search_term(session, keyword, all_novels, delay=0.3, page_size=100):
    """Paginate through all search results for a keyword."""
    total_count = None
    page = 0
    new_count = 0

    while True:
        try:
            r = session.get(BFF_SEARCH_URL, params={
                'keyword': keyword,
                'category_uid': 11,
                'is_complete': 'false',
                'sort_type': 'ACCURACY',
                'page': page,
                'size': page_size,
            }, timeout=15)

            if r.status_code != 200:
                print(f" HTTP {r.status_code}", end='')
                break

            data = r.json()
            result = data.get('result', {})

            if total_count is None:
                total_count = result.get('total_count', 0)
                print(f" ({total_count:,})", end='', flush=True)

            items = result.get('list', [])
            if not items:
                break

            for item in items:
                sid = str(item.get('series_id', ''))
                if not sid or sid in all_novels:
                    continue

                thumb = item.get('thumbnail', '')
                if thumb and not thumb.startswith('http'):
                    thumb = THUMB_PREFIX + thumb + '&filename=th3'

                age_grade = item.get('age_grade', 0)
                on_issue = item.get('on_issue', '')
                sp = item.get('service_property', {})

                all_novels[sid] = {
                    'id': sid,
                    'title': item.get('title', ''),
                    'author': item.get('authors', ''),
                    'cover': thumb,
                    'tags': [item.get('sub_category', '')] if item.get('sub_category') else [],
                    'views': sp.get('view_count', 0),
                    'likes': 0,
                    'chapters': 0,
                    'complete': 1 if on_issue in ('E', 'End', 'Complete') else 0,
                    'updated': item.get('last_slide_added_dt', ''),
                    'age': 19 if age_grade >= 19 else 0,
                }
                new_count += 1

            is_end = result.get('is_end', False)
            if is_end:
                break

            page += 1

            # Progress
            if page % 10 == 0:
                print(f" [{page * page_size}]", end='', flush=True)

            time.sleep(delay)

        except Exception as e:
            print(f" err:{e}", end='')
            break

    return new_count


def save_novels(all_novels, path="docs/data/kakao_novels.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    optimized = []
    for n in all_novels.values():
        optimized.append([
            n['id'], n['title'], n.get('author', ''), n.get('cover', ''),
            n.get('tags', []), n.get('views', 0), n.get('likes', 0),
            n.get('chapters', 0), n.get('complete', 0), n.get('updated', ''),
            0, n.get('age', 0),
        ])

    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"\nSaved {len(optimized)} novels to {path} ({size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description="Scrape KakaoPage novels via BFF API")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests")
    parser.add_argument("--page-size", type=int, default=100, help="Results per page (max 100)")
    args = parser.parse_args()

    session = make_session()

    # Test connection
    print("Testing BFF API...")
    try:
        r = session.get(BFF_SEARCH_URL, params={
            'keyword': '가', 'category_uid': 11, 'page': 0, 'size': 1,
        }, timeout=10)
        data = r.json()
        total = data.get('result', {}).get('total_count', 0)
        print(f"  OK — '가' has {total:,} results")
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Make sure Korean VPN is active!")
        sys.exit(1)

    all_novels = {}

    for i, term in enumerate(SEARCH_TERMS):
        print(f"\n[{i+1}/{len(SEARCH_TERMS)}] '{term}'", end='', flush=True)
        new = scrape_search_term(session, term, all_novels,
                                 delay=args.delay, page_size=args.page_size)
        print(f" → +{new} (total: {len(all_novels)})")

    print(f"\nTotal: {len(all_novels)} unique novels")
    save_novels(all_novels)


if __name__ == "__main__":
    main()
