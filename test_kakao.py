"""Verify colophon filtering works — no more interleaved copyright."""
import sys, os, json, tempfile, shutil
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright
from external_scraper import ExternalScraper

tmp_dir = os.path.join(tempfile.gettempdir(), 'kakao_test_filter')
if os.path.exists(tmp_dir):
    shutil.rmtree(tmp_dir, ignore_errors=True)
os.makedirs(tmp_dir, exist_ok=True)

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    tmp_dir, headless=True,
    args=['--disable-web-security', '--no-sandbox', '--disable-gpu'],
    ignore_https_errors=True,
)
page = ctx.new_page()

ch_url = 'https://page.kakao.com/content/56510701/viewer/56523944'
json_chunks = []

def on_response(response):
    url = response.url
    ct = response.headers.get('content-type', '')
    if 'sdownload/resource' in url and 'json' in ct:
        try:
            body = response.body()
            if body: json_chunks.append(body)
        except Exception: pass

page.on('response', on_response)
try:
    page.goto(ch_url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(5000)
    for _ in range(5):
        try:
            page.evaluate("""(function(){
                var vw = window.innerWidth; var vh = window.innerHeight;
                var el = document.elementFromPoint(vw * 0.85, vh * 0.5);
                if(el) el.click();
            })()""")
            page.wait_for_timeout(1500)
        except Exception:
            break
except Exception as e:
    print(f"Navigation issue: {e}")

page.remove_listener('response', on_response)

scraper = ExternalScraper(logger=lambda m: None)
paragraphs = scraper._kakao_extract_from_json(json_chunks)

print(f"JSON chunks captured: {len(json_chunks)}")
print(f"Extracted {len(paragraphs)} paragraphs (after colophon filtering)\n")

# Check for boilerplate
boilerplate = ['글쟁이S', '다온크리에이티브', '979-11', 'ISBN', 'ⓒ', '저작권법', '재가공']
found_boilerplate = []
for i, p in enumerate(paragraphs):
    for m in boilerplate:
        if m in p:
            found_boilerplate.append(f"  [{i}] {p[:100]}")
            break

if found_boilerplate:
    print(f"WARNING: {len(found_boilerplate)} boilerplate paragraphs still present:")
    for fb in found_boilerplate:
        print(fb)
else:
    print("✅ No boilerplate found in output!")

print(f"\n--- First 15 paragraphs ---")
for i, p in enumerate(paragraphs[:15]):
    print(f"  [{i}] {p[:120]}")

print(f"\n--- Last 5 paragraphs ---")
for i, p in enumerate(paragraphs[-5:]):
    print(f"  [{len(paragraphs)-5+i}] {p[:120]}")

try:
    page.close()
    ctx.close()
    pw.stop()
except Exception:
    pass
shutil.rmtree(tmp_dir, ignore_errors=True)
print("\nDone.")
