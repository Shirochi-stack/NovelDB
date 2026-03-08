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

r = auth.session.get("https://novelpia.com/proc/novel", params={
    "cmd": "novel_search", "search_type": "novel_genre", "search_val": "악피폐",
    "page": 1, "rows": 30, "novel_age": "",
    "novel_type": "", "start_count_book": "", "end_count_book": "",
    "start_days": "", "sort_col": "last_viewdate", "novel_genre": "",
    "block_out": 0, "block_stop": 0, "is_contest": 0, "is_complete": "",
    "is_challenge": 0, "list_display": "grid",
}, headers=headers, timeout=15)

data = r.json()
print(f"Total: {data.get('total_cnt')}")
for n in data.get("list", []):
    genres = n.get("novel_genre_arr", [])
    print(f"  ID {n.get('novel_no')}: {n.get('novel_name')}")
    print(f"    genres: {genres}")
    print(f"    has TS: {'TS' in genres}, has 먼치킨: {'먼치킨' in genres}")
    print()
