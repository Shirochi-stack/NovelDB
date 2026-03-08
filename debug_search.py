import sys, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from novelpia_auth import NovelpiaAuth

auth = NovelpiaAuth()
with open('config.json', 'r') as f:
    config = json.load(f)
auth.loginkey = config.get('loginkey', '')
auth.session.cookies.set('LOGINKEY', auth.loginkey, domain='novelpia.com')

headers = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://novelpia.com/search"}

# Test: 약피폐 + novel_genre=TS -> 636
# Then check if those 636 also have 먼치킨 in genre_arr
r = auth.session.get("https://novelpia.com/proc/novel", params={
    "cmd": "novel_search", "search_type": "novel_genre", "search_val": "약피폐",
    "page": 1, "rows": 30, "novel_age": "",
    "novel_type": "", "start_count_book": "", "end_count_book": "",
    "start_days": "", "sort_col": "last_viewdate", "novel_genre": "TS",
    "block_out": 0, "block_stop": 0, "is_contest": 0, "is_complete": "",
    "is_challenge": 0, "list_display": "grid",
}, headers=headers, timeout=15)
data = r.json()
print(f"약피폐 + novel_genre=TS -> total={data.get('total_cnt')}")
for n in data.get("list", []):
    genres = n.get("novel_genre_arr", [])
    has_ts = "TS" in genres
    has_munch = "먼치킨" in genres
    print(f"  ID {n.get('novel_no')}: TS={has_ts}, 먼치킨={has_munch}, genres={genres[:8]}")

# Also test: which gives smallest combined: 약피폐+TS, 약피폐+먼치킨, TS+먼치킨
print()
combos = [
    ("약피폐", "TS"), ("약피폐", "먼치킨"), ("TS", "먼치킨"), ("먼치킨", "TS"),
]
for sv, ng in combos:
    r2 = auth.session.get("https://novelpia.com/proc/novel", params={
        "cmd": "novel_search", "search_type": "novel_genre", "search_val": sv,
        "page": 1, "rows": 30, "novel_age": "", "novel_type": "",
        "start_count_book": "", "end_count_book": "", "start_days": "",
        "sort_col": "last_viewdate", "novel_genre": ng,
        "block_out": 0, "block_stop": 0, "is_contest": 0, "is_complete": "",
        "is_challenge": 0, "list_display": "grid",
    }, headers=headers, timeout=15)
    d = r2.json()
    print(f"  {sv} + genre={ng} -> {d.get('total_cnt')}")
