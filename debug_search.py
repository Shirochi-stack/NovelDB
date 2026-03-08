import sys, json, re
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from novelpia_auth import NovelpiaAuth

auth = NovelpiaAuth()
with open('config.json', 'r') as f:
    config = json.load(f)
auth.loginkey = config.get('loginkey', '')
auth.session.cookies.set('LOGINKEY', auth.loginkey, domain='novelpia.com')

r = auth.session.get("https://novelpia.com/search", timeout=15)

inline_scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
for i, s in enumerate(inline_scripts):
    if 'get_lists' in s and 'search_obj' in s:
        lines = s.split('\n')
        # Find get_lists and get_search functions
        for j, line in enumerate(lines):
            stripped = line.strip()
            if 'get_lists' in stripped and ('function' in stripped or 'async' in stripped):
                # Print the whole function
                for k in range(j, min(len(lines), j+30)):
                    print(f"L{k}: {lines[k].rstrip()}")
                    if k > j and lines[k].strip().startswith('},') or lines[k].strip().startswith('},'[:1]) and 'async' not in lines[k]:
                        break
                print("===")
            if 'get_search' in stripped and ('function' in stripped or 'async' in stripped) and 'get_search_option' not in stripped:
                for k in range(j, min(len(lines), j+40)):
                    print(f"L{k}: {lines[k].rstrip()}")
                    if k > j+2 and (lines[k].strip() == '},' or (lines[k].strip().startswith('},'))):
                        break
                print("===")
        break
