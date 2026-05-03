"""Test getting all 703 episodes in one BFF API call."""
import sys, json, tempfile, shutil, os
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

tmp_dir = os.path.join(tempfile.gettempdir(), 'kakao_api5')
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

series_id = '56510701'
page.goto(f'https://page.kakao.com/content/{series_id}',
          wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(3000)

# Try window_size=2000 to get all at once
result = page.evaluate("""
async (seriesId) => {
    const url = `https://bff-page.kakao.com/api/gateway/api/v2/content/product/list?series_id=${seriesId}&cursor_index=0&cursor_direction=ANCHOR&window_size=2000`;
    const resp = await fetch(url);
    return await resp.json();
}
""", series_id)

r = result.get('result', {})
eps = r.get('list', [])
print(f"total_count: {r.get('total_count')}")
print(f"Returned: {len(eps)}, has_next: {r.get('has_next')}")

if eps:
    # Build viewer URLs
    first_item = eps[0].get('item', {})
    last_item = eps[-1].get('item', {})
    free_count = sum(1 for e in eps if e.get('item', {}).get('is_free'))
    
    print(f"\nFree: {free_count}, Paid: {len(eps) - free_count}")
    print(f"First: #{first_item.get('order_value')} '{first_item.get('title','')[:50]}' (product_id={first_item.get('product_id')})")
    print(f"Last:  #{last_item.get('order_value')} '{last_item.get('title','')[:50]}' (product_id={last_item.get('product_id')})")
    
    # Show how to build viewer URL
    pid = first_item.get('product_id')
    viewer_url = f"https://page.kakao.com/content/{series_id}/viewer/{pid}"
    print(f"\nViewer URL for ch1: {viewer_url}")

page.close(); ctx.close(); pw.stop()
shutil.rmtree(tmp_dir, ignore_errors=True)
print("\nDone.")
