"""Scrape KakaoPage novel metadata for the NovelpiaDB site.

Uses curl_cffi to bypass TLS fingerprinting. Discovers novels from:
  1. Ranking pages across all novel subcategories  
  2. Category browsing pages with different sort/filter combos
  3. Search pages with many Korean search terms
  4. Theme/recommendation sections
Then fetches full metadata from each content page.

Requires: pip install curl_cffi
Note: KakaoPage may be geo-restricted. Use a Korean VPN if needed.

Usage:
    python scripts/scrape_kakao.py
    python scripts/scrape_kakao.py --delay 0.3 --threads 8

Output: docs/data/kakao_novels.json
"""

import sys, os, json, time, re, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi_requests

sys.stdout.reconfigure(encoding='utf-8')

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
)

# Novel subcategory UIDs on KakaoPage
NOVEL_SUBCATEGORIES = {
    86: '판타지', 87: '현대판타지', 88: '로맨스판타지',
    89: '로맨스', 90: '무협', 91: 'BL',
    92: '드라마', 93: '미스터리', 94: '라이트노벨',
    95: 'GL',
}

# Screen UIDs for novel sections
NOVEL_SCREENS = [93, 94, 95, 96, 97, 98, 99, 100]

# Search terms — diverse to maximize coverage
SEARCH_TERMS = [
    # Korean consonants (broad)
    'ㄱ', 'ㄴ', 'ㄷ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅅ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ',
    # Two-character consonant combos for different sorting
    'ㄱㄴ', 'ㄱㄷ', 'ㄴㄱ', 'ㄴㄷ', 'ㄷㄱ', 'ㅁㄴ', 'ㅂㅅ', 'ㅅㄱ', 'ㅇㄱ', 'ㅈㄱ',
    # Common genre/setting words
    '판타지', '로맨스', '무협', '현대', '게임', '회귀', '전생', '빙의', '헌터',
    '아카데미', '먼치킨', '하렘', '액션', '드라마', '일상', '코미디', '추리',
    '공포', '생존', '마법', '던전', '영지', '기사', '용사', '마왕', '학원',
    '복수', '성장', '환생', '소설', '사랑', '결혼', '왕자', '공주', '마녀',
    '천재', '재벌', '회장', '비서', '의사', '변호사', '형사', '군인', '소환',
    '능력', '시스템', '스킬', '레벨', '퀘스트', '몬스터', '기사단',
    '집착', '계약', '후회', '악역', '빌런', '흑막', '각성', '귀환',
    '차원', '이세계', '랭커', '솔로', '전쟁', '제국', '왕국', '탑',
    '유일무이', '만렙', '최강', '불멸', '무한', '절대', '궁극',
    '평범', '평화', '힐링', '감동', '달달', '순정', '짝사랑',
    # English / numbers
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
    'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
    '1', '2', '3', '4', '5', '6', '7', '8', '9', '0',
]


def make_session():
    return cffi_requests.Session(impersonate='chrome')


def extract_next_data(html):
    match = NEXT_DATA_RE.search(html)
    return json.loads(match.group(1)) if match else None


def extract_novels_from_page_data(data):
    """Extract all novel info from __NEXT_DATA__ JSON, walking card structures.
    
    Ranking/search pages don't include categoryType — items only have 
    seriesId, title, thumbnail, ageGrade. Since we only visit novel pages,
    any item with a seriesId + title is a novel.
    """
    novels = {}

    def walk(obj):
        if isinstance(obj, dict):
            sid = obj.get('seriesId')
            title = obj.get('title', '')

            if sid and title and str(sid) not in novels:
                cover = obj.get('thumbnail', obj.get('landThumbnail', ''))
                if cover and not cover.startswith('http'):
                    cover = 'https:' + cover

                age_str = obj.get('ageGrade', 'All')
                is_adult = age_str in ('Nineteen', '19') or '19' in str(age_str)

                on_issue = obj.get('onIssue', obj.get('state', ''))
                is_complete = on_issue in ('End', 'Complete')

                novels[str(sid)] = {
                    'id': str(sid),
                    'title': title,
                    'author': obj.get('authors', obj.get('author', '')),
                    'cover': cover,
                    'tags': [obj.get('subcategory', '')] if obj.get('subcategory') else [],
                    'views': 0,
                    'likes': 0,
                    'chapters': 0,
                    'complete': 1 if is_complete else 0,
                    'updated': obj.get('lastSlideAddedDate', ''),
                    'age': 19 if is_adult else 0,
                }

            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return novels


def fetch_page_novels(session, url):
    """Fetch a page and extract all novel data from __NEXT_DATA__."""
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return {}
        data = extract_next_data(r.text)
        if not data:
            return {}
        return extract_novels_from_page_data(data)
    except Exception:
        return {}


def fetch_content_metadata(session, series_id):
    """Fetch full metadata from a single content page."""
    try:
        r = session.get(f'https://page.kakao.com/content/{series_id}', timeout=10)
        if r.status_code != 200:
            return None
        data = extract_next_data(r.text)
        if not data:
            return None

        queries = data.get('props', {}).get('pageProps', {}).get('initialProps', {}).get('dehydratedState', {}).get('queries', [])
        for q in queries:
            content = q.get('state', {}).get('data', {}).get('contentHomeOverview', {}).get('content', {})
            if not content or not content.get('title'):
                continue
            if content.get('categoryType') != 'Webnovel':
                return None

            cover = content.get('thumbnail', '')
            if cover and not cover.startswith('http'):
                cover = 'https:' + cover

            age = content.get('ageGrade', 'All')
            is_adult = age in ('Nineteen', '19') or '19' in str(age)
            sp = content.get('serviceProperty', {})

            return {
                'id': str(series_id),
                'title': content.get('title', ''),
                'author': content.get('authors', ''),
                'cover': cover,
                'tags': [content.get('subcategory', '')] if content.get('subcategory') else [],
                'views': sp.get('commentCount', 0),
                'likes': sp.get('ratingCount', 0),
                'chapters': 0,
                'complete': 1 if content.get('onIssue') in ('End', 'Complete') else 0,
                'updated': content.get('lastSlideAddedDate', ''),
                'age': 19 if is_adult else 0,
            }
    except Exception:
        pass
    return None


def phase_rankings(session, all_novels, delay):
    """Phase 1: Scrape ranking/browsing pages for all subcategories."""
    print("\n=== Phase 1: Rankings & category pages ===")

    urls = []
    for screen in NOVEL_SCREENS:
        urls.append(f'https://page.kakao.com/menu/10011/screen/{screen}')
    for sc_uid in NOVEL_SUBCATEGORIES:
        for screen in [94, 95]:  # ranking and browsing screens
            urls.append(f'https://page.kakao.com/menu/10011/screen/{screen}?subcategoryUid={sc_uid}')
    # Also try extended subcategory range
    for sc_uid in range(80, 120):
        urls.append(f'https://page.kakao.com/menu/10011/screen/94?subcategoryUid={sc_uid}')

    before = len(all_novels)
    for i, url in enumerate(urls):
        found = fetch_page_novels(session, url)
        new = 0
        for sid, novel in found.items():
            if sid not in all_novels:
                all_novels[sid] = novel
                new += 1
        if new > 0:
            print(f"  [{i+1}/{len(urls)}] +{new} new (total: {len(all_novels)})")
        time.sleep(delay * 0.3)

    print(f"  Rankings: +{len(all_novels) - before} novels")


def phase_search(session, all_novels, delay):
    """Phase 2: Search with many terms."""
    print("\n=== Phase 2: Search discovery ===")
    before = len(all_novels)

    for i, term in enumerate(SEARCH_TERMS):
        url = f'https://page.kakao.com/search?keyword={term}&categoryUid=11'
        found = fetch_page_novels(session, url)
        new = 0
        for sid, novel in found.items():
            if sid not in all_novels:
                all_novels[sid] = novel
                new += 1
        if (i + 1) % 20 == 0 or new > 0:
            print(f"  [{i+1}/{len(SEARCH_TERMS)}] '{term}': +{new} new (total: {len(all_novels)})")
        time.sleep(delay)

    print(f"  Search: +{len(all_novels) - before} novels")


def phase_homepage(session, all_novels, delay):
    """Phase 3: Scrape homepage & theme pages for any extras."""
    print("\n=== Phase 3: Homepage & themes ===")
    before = len(all_novels)

    # Main homepage
    found = fetch_page_novels(session, 'https://page.kakao.com/')
    for sid, novel in found.items():
        if sid not in all_novels:
            all_novels[sid] = novel

    # Novel genre home pages  
    for sc in NOVEL_SUBCATEGORIES:
        url = f'https://page.kakao.com/menu/10011/screen/93?subcategoryUid={sc}'
        found = fetch_page_novels(session, url)
        for sid, novel in found.items():
            if sid not in all_novels:
                all_novels[sid] = novel
        time.sleep(delay * 0.3)

    print(f"  Themes: +{len(all_novels) - before} novels")


def phase_enrich(session, all_novels, delay, threads):
    """Phase 4: Enrich metadata from individual content pages."""
    # Only enrich novels that have minimal data (no views/likes)
    to_enrich = [sid for sid, n in all_novels.items() if n.get('views', 0) == 0]

    if not to_enrich:
        print("\n=== Phase 4: All novels already have metadata ===")
        return

    print(f"\n=== Phase 4: Enriching {len(to_enrich)} novels with full metadata ===")

    def worker(sid):
        s = make_session()
        return sid, fetch_content_metadata(s, sid)

    done = 0
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {executor.submit(worker, sid): sid for sid in to_enrich}
        for future in as_completed(futures):
            sid, result = future.result()
            done += 1
            if result:
                all_novels[sid] = result
            if done % 100 == 0:
                print(f"  Enriched {done}/{len(to_enrich)}")

    print(f"  Done enriching {done} novels")


def save_novels(all_novels, path="docs/data/kakao_novels.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    optimized = []
    for n in all_novels.values():
        optimized.append([
            n['id'], n['title'], n['author'], n.get('cover', ''),
            n.get('tags', []), n.get('views', 0), n.get('likes', 0),
            n.get('chapters', 0), n.get('complete', 0), n.get('updated', ''),
            0, n.get('age', 0),
        ])

    with open(path, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"\nSaved {len(optimized)} novels to {path} ({size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description="Scrape KakaoPage novel metadata")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Delay between requests (default: 0.5)")
    parser.add_argument("--threads", type=int, default=5,
                        help="Threads for metadata enrichment (default: 5)")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip Phase 4 (individual page fetching)")
    args = parser.parse_args()

    session = make_session()

    # Test connection
    print("Testing connection to KakaoPage...")
    try:
        r = session.get('https://page.kakao.com/content/56510701', timeout=15)
        data = extract_next_data(r.text)
        if not data:
            print("ERROR: Cannot parse page data. Are you connected to a Korean VPN?")
            sys.exit(1)
        meta = data.get('props', {}).get('pageProps', {}).get('initialProps', {}).get('metaInfo', {})
        print(f"  Connected — test: '{meta.get('ogTitle', '?')}'")
    except Exception as e:
        print(f"ERROR: {e}")
        print("Make sure you're connected to a Korean VPN.")
        sys.exit(1)

    all_novels = {}

    phase_rankings(session, all_novels, args.delay)
    phase_search(session, all_novels, args.delay)
    phase_homepage(session, all_novels, args.delay)

    print(f"\nDiscovered {len(all_novels)} unique novels")

    if not args.skip_enrich:
        phase_enrich(session, all_novels, args.delay, args.threads)

    save_novels(all_novels)


if __name__ == "__main__":
    main()
