"""Scrape KakaoPage novel metadata via the BFF REST API.

Uses KakaoPage's genre menu API, the same listing behind:
https://page.kakao.com/menu/10011/screen/84

No browser needed -- just plain HTTP requests.

Usage:
    python scripts/scrape_kakao.py
    python scripts/scrape_kakao.py --delay 0.2

Output: docs/data/kakao_novels.json
"""

import sys, os, json, time, re, argparse, gzip, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

sys.stdout.reconfigure(encoding='utf-8')

BFF_SEARCH_URL = 'https://bff-page.kakao.com/api/gateway/api/v1/search/series'
BFF_GENRE_URL = 'https://bff-page.kakao.com/api/gateway/view/v1/landing/genre'
BFF_PRODUCT_LIST_URL = 'https://bff-page.kakao.com/api/gateway/api/v2/content/product/list'
KAKAO_DESCRIPTIONS_PATH = os.path.join("docs", "data", "kakao_descriptions.txt")
KAKAO_CATEGORY_UID = 11
KAKAO_GENRE_SCREEN_UID = 84

# Search fallback only. The canonical scraper uses the menu genre endpoint.
SEARCH_TERMS = [
    '\uAC00', '\uB098', '\uB2E4', '\uB77C', '\uB9C8', '\uBC14', '\uC0AC',
    '\uC544', '\uC790', '\uCC28', '\uCE74', '\uD0C0', '\uD30C', '\uD558',
]

THUMB_PREFIX = 'https://dn-img-page.kakao.com/download/resource?kid='
_thread_local = threading.local()
DEFAULT_RETRIES = 5
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Referer': 'https://page.kakao.com/',
        'Origin': 'https://page.kakao.com',
    })
    return s


def get_thread_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = make_session()
        _thread_local.session = session
    return session


def retry_after_seconds(response):
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def get_with_retries(
        session, url, *, params=None, timeout=15, attempts=DEFAULT_RETRIES,
        backoff=1.0, max_backoff=30.0, log_retries=False):
    """GET with bounded retries for transient Kakao/BFF disconnects."""
    last_error = None
    attempts = max(1, attempts)

    for attempt in range(1, attempts + 1):
        response = None
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response
            last_error = f"HTTP {response.status_code}"
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e
        except requests.exceptions.RequestException as e:
            last_error = e

        if attempt >= attempts:
            if response is not None:
                return response
            raise last_error

        wait = None
        if response is not None:
            wait = retry_after_seconds(response)
        if wait is None:
            wait = min(max_backoff, backoff * (2 ** (attempt - 1)))

        if log_retries:
            print(f" retry{attempt}/{attempts}", end='', flush=True)
        time.sleep(wait)

    raise RuntimeError("unreachable retry state")


def normalize_description(text):
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_existing_descriptions(path=KAKAO_DESCRIPTIONS_PATH):
    """Load existing raw descriptions and English translations."""
    rows = {}
    source = None
    if os.path.exists(path):
        source = path
        opener = open
        mode = "r"
    elif os.path.exists(path + ".gz"):
        source = path + ".gz"
        opener = gzip.open
        mode = "rt"
    else:
        return rows

    with opener(source, mode, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split("|||")
            nid = parts[0].strip()
            if not nid:
                continue
            raw = parts[1] if len(parts) >= 2 else ""
            en = parts[2].strip() if len(parts) >= 3 else ""
            rows[nid] = (raw.replace("\\n", "\n"), en)
    if rows:
        print(f"Loaded {len(rows)} existing descriptions from {source}")
    return rows


def fetch_series_description(series_id, retries=DEFAULT_RETRIES):
    """Fetch one KakaoPage series description from the product-list BFF API."""
    session = get_thread_session()
    r = get_with_retries(session, BFF_PRODUCT_LIST_URL, params={
        'series_id': series_id,
        'cursor_index': 0,
        'cursor_direction': 'ANCHOR',
        'window_size': 0,
    }, timeout=15, attempts=retries)
    if r.status_code != 200:
        return ""
    result = r.json().get("result", {})
    series_item = result.get("series_item", {})
    return normalize_description(series_item.get("description", ""))


def fetch_descriptions(all_novels, existing, workers=12, retries=DEFAULT_RETRIES):
    """Fill descriptions, using existing rows as a cache on rescrapes."""
    workers = max(1, workers)
    cached = 0
    missing = []

    for sid, novel in all_novels.items():
        cached_raw = existing.get(sid, ("", ""))[0]
        if cached_raw:
            novel["description"] = normalize_description(cached_raw)
            cached += 1
        else:
            missing.append(sid)

    print(f"\nDescriptions: {cached:,} cached, {len(missing):,} to fetch")
    if not missing:
        return

    fetched = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_series_description, sid, retries): sid
            for sid in missing
        }
        for i, fut in enumerate(as_completed(futures), 1):
            sid = futures[fut]
            try:
                desc = fut.result()
            except Exception:
                desc = ""

            if desc:
                all_novels[sid]["description"] = desc
                fetched += 1
            else:
                failed += 1

            if i % 500 == 0 or i == len(missing):
                print(
                    f"  descriptions {i:,}/{len(missing):,} "
                    f"fetched={fetched:,} empty/failed={failed:,}",
                    flush=True,
                )


def normalize_thumbnail(thumb):
    if not thumb:
        return ""
    thumb = str(thumb)
    if thumb.startswith("http"):
        return thumb
    return THUMB_PREFIX + thumb + "&filename=th3"


def is_complete_item(item):
    state = str(item.get("state", ""))
    on_issue = str(item.get("on_issue", ""))
    title = str(item.get("title", ""))
    return 1 if (
        state in {"ST66", "END", "COMPLETE"}
        or on_issue in {"E", "End", "Complete"}
        or "[완결]" in title
    ) else 0


def add_kakao_item(all_novels, item):
    """Normalize one Kakao BFF series item into the compact site schema."""
    sid = str(item.get("series_id", "") or item.get("id", "")).strip()
    if not sid or sid in all_novels:
        return False

    sp = item.get("service_property") or {}
    age_grade = item.get("age_grade") or 0
    sub_category = (
        item.get("sub_category")
        or ((item.get("translate_property") or {}).get("sub_category") or {}).get("ko")
        or ""
    )

    all_novels[sid] = {
        "id": sid,
        "title": item.get("title", ""),
        "author": item.get("authors", ""),
        "cover": normalize_thumbnail(item.get("thumbnail", "")),
        "tags": [sub_category] if sub_category else [],
        "views": sp.get("view_count", 0),
        "likes": 0,
        "chapters": 0,
        "complete": is_complete_item(item),
        "updated": item.get("last_slide_added_dt", ""),
        "age": 19 if age_grade >= 19 else 0,
    }
    return True


def scrape_genre_catalog(
        session, all_novels, delay=0.3, category_uid=KAKAO_CATEGORY_UID,
        screen_uid=KAKAO_GENRE_SCREEN_UID, sort_type="PRODUCT_LATEST",
        retries=DEFAULT_RETRIES, max_pages=None):
    """Scrape Kakao's canonical web-novel genre listing.

    This endpoint powers /menu/10011/screen/84 and reports the real total
    count shown by the website. It is more complete than search-prefix
    crawling, which misses titles that do not surface for those search terms.
    """
    page = 0
    expected_total = None
    new_count = 0

    while True:
        params = {
            "category_uid": category_uid,
            "screen_uid": screen_uid,
            "page": page,
        }
        if sort_type:
            params["sort_type"] = sort_type

        r = get_with_retries(
            session, BFF_GENRE_URL, params=params, timeout=20,
            attempts=retries, log_retries=True
        )
        if r.status_code != 200:
            print(f"\n  Page {page}: HTTP {r.status_code}")
            break

        result = r.json().get("result", {})
        items = result.get("list") or []
        if expected_total is None:
            expected_total = result.get("total_count")
            if expected_total:
                print(f"  Website total: {expected_total:,}")

        if not items:
            print(f"\n  Page {page}: no items")
            break

        page_new = 0
        for item in items:
            if add_kakao_item(all_novels, item):
                page_new += 1
        new_count += page_new

        if page % 25 == 0 or result.get("is_end"):
            total = f"/{expected_total:,}" if expected_total else ""
            print(
                f"  page {page:,}: +{page_new} "
                f"(unique {len(all_novels):,}{total})",
                flush=True,
            )

        if result.get("is_end"):
            break
        page += 1
        if max_pages is not None and page >= max_pages:
            print(f"\n  Stopping early at --max-pages={max_pages}")
            break
        time.sleep(delay)

    if expected_total and max_pages is None:
        missing = expected_total - len(all_novels)
        if missing > 0:
            print(
                f"  WARNING: scraped {len(all_novels):,} unique rows, "
                f"{missing:,} below website total {expected_total:,}"
            )
    return new_count, expected_total


def scrape_search_term(
        session, keyword, all_novels, delay=0.3, page_size=100,
        retries=DEFAULT_RETRIES):
    """Paginate through all search results for a keyword."""
    total_count = None
    page = 0
    new_count = 0

    while True:
        try:
            r = get_with_retries(session, BFF_SEARCH_URL, params={
                'keyword': keyword,
                'category_uid': 11,
                'is_complete': 'false',
                'sort_type': 'ACCURACY',
                'page': page,
                'size': page_size,
            }, timeout=15, attempts=retries, log_retries=True)

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
                if add_kakao_item(all_novels, item):
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


def save_descriptions(all_novels, existing, path=KAKAO_DESCRIPTIONS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    translated = []
    untranslated = []
    for n in all_novels.values():
        nid = str(n.get("id", ""))
        if not nid:
            continue

        desc = normalize_description(n.get("description", ""))
        en = existing.get(nid, ("", ""))[1]
        if not desc and not en:
            continue

        flat = desc.replace("\n", "\\n")
        row = f"{nid}|||{flat}|||{en}\n"
        if en:
            translated.append(row)
        else:
            untranslated.append(row)

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(translated)
        f.writelines(untranslated)

    gz_path = path + ".gz"
    with open(path, "rb") as f_in:
        raw = f_in.read()
    with open(gz_path, "wb") as f_out:
        f_out.write(gzip.compress(raw, compresslevel=6))

    print(f"Saved {len(translated) + len(untranslated):,} descriptions to {path}")
    print(f"  Translated: {len(translated):,}, Untranslated: {len(untranslated):,}")


def main():
    parser = argparse.ArgumentParser(description="Scrape KakaoPage novels via BFF API")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests")
    parser.add_argument(
        "--source", choices=["genre", "search"], default="genre",
        help="Catalog source. genre matches KakaoPage's full web-novel menu."
    )
    parser.add_argument(
        "--sort-type", default="PRODUCT_LATEST",
        choices=["PRODUCT_LATEST", "UPDATE"],
        help="Genre listing sort. PRODUCT_LATEST is more stable for full crawls."
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Stop after this many genre pages, for smoke tests."
    )
    parser.add_argument("--page-size", type=int, default=100,
                        help="Search fallback page size (max 100)")
    parser.add_argument("--skip-descriptions", action="store_true",
                        help="Only scrape catalog metadata; do not fetch raw synopsis text")
    parser.add_argument("--description-workers", type=int, default=12,
                        help="Parallel workers for fetching Kakao descriptions")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                        help="Retry attempts for transient Kakao/BFF failures")
    args = parser.parse_args()

    session = make_session()

    # Test connection
    print("Testing BFF API...")
    try:
        r = get_with_retries(session, BFF_GENRE_URL, params={
            "category_uid": KAKAO_CATEGORY_UID,
            "screen_uid": KAKAO_GENRE_SCREEN_UID,
            "sort_type": args.sort_type,
            "page": 0,
        }, timeout=10, attempts=args.retries, log_retries=True)
        data = r.json()
        total = data.get('result', {}).get('total_count', 0)
        print(f"  OK -- Kakao genre menu reports {total:,} web novels")
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Check your network connection and KakaoPage API availability.")
        sys.exit(1)

    all_novels = {}

    if args.source == "genre":
        print(
            f"\nScraping Kakao genre catalog "
            f"(category={KAKAO_CATEGORY_UID}, screen={KAKAO_GENRE_SCREEN_UID}, "
            f"sort={args.sort_type})..."
        )
        scrape_genre_catalog(
            session, all_novels, delay=args.delay,
            category_uid=KAKAO_CATEGORY_UID,
            screen_uid=KAKAO_GENRE_SCREEN_UID,
            sort_type=args.sort_type,
            retries=args.retries,
            max_pages=args.max_pages,
        )
    else:
        print("\nScraping search fallback terms...")
        for i, term in enumerate(SEARCH_TERMS):
            print(f"\n[{i+1}/{len(SEARCH_TERMS)}] '{term}'", end='', flush=True)
            new = scrape_search_term(session, term, all_novels,
                                     delay=args.delay, page_size=args.page_size,
                                     retries=args.retries)
            print(f" -> +{new} (total: {len(all_novels)})")

    print(f"\nTotal: {len(all_novels)} unique novels")
    existing_descriptions = load_existing_descriptions()
    if not args.skip_descriptions:
        fetch_descriptions(
            all_novels, existing_descriptions,
            workers=args.description_workers,
            retries=args.retries,
        )
    save_novels(all_novels)
    if not args.skip_descriptions:
        save_descriptions(all_novels, existing_descriptions)


if __name__ == "__main__":
    main()
