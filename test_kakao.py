"""Verify the fix: test _kakao_extract_from_json with actual chapter data."""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

# Import the fixed scraper
from external_scraper import ExternalScraper

data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'browser_data')
os.makedirs(data_dir, exist_ok=True)

scraper = ExternalScraper(logger=lambda msg: print(f"  {msg}"))

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    data_dir, headless=False,
    args=['--disable-web-security', '--no-sandbox'],
    ignore_https_errors=True,
)
page = ctx.new_page()

ch_url = 'https://page.kakao.com/content/56510701/viewer/56523944'
ch_name = 'Chapter 1'

# Capture JSON responses
json_chunks = []
def on_response(response):
    url = response.url
    ct = response.headers.get('content-type', '')
    if 'sdownload/resource' in url and 'json' in ct:
        try:
            body = response.body()
            if body:
                json_chunks.append(body)
        except Exception:
            pass

page.on('response', on_response)
page.goto(ch_url, wait_until="networkidle", timeout=60000)
page.wait_for_timeout(5000)

# Click right to load more content
for _ in range(5):
    page.evaluate("""(function(){
        var vw = window.innerWidth;
        var vh = window.innerHeight;
        var el = document.elementFromPoint(vw * 0.85, vh * 0.5);
        if(el) el.click();
    })()""")
    page.wait_for_timeout(1500)

page.remove_listener('response', on_response)

print(f"\nCaptured {len(json_chunks)} JSON chunks")

# Use the FIXED extraction method
paragraphs = scraper._kakao_extract_from_json(json_chunks)

print(f"Extracted {len(paragraphs)} paragraphs")
print(f"\n--- First 20 paragraphs ---")
for i, p in enumerate(paragraphs[:20]):
    print(f"  [{i}] {p[:120]}")

print(f"\n--- Last 5 paragraphs ---")
for i, p in enumerate(paragraphs[-5:]):
    print(f"  [{len(paragraphs)-5+i}] {p[:120]}")

# Count non-boilerplate paragraphs
boilerplate_markers = ['글쟁이S', '다온크리에이티브', '979-11', 'ⓒ2021', '저작권법', '재가공']
real_count = sum(1 for p in paragraphs 
                 if not any(m in p for m in boilerplate_markers) and p != '&nbsp;')
print(f"\nTotal: {len(paragraphs)} paragraphs, {real_count} real content (excl boilerplate/nbsp)")

page.close(); ctx.close(); pw.stop()
print("\nDone. Fix verified!" if real_count > 50 else "\nFix may need more work.")
