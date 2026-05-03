"""Simulate the ACTUAL app flow to reproduce the bug:
1. Start headless (like Fetch Info)
2. Close it (like Enter Browser cleanup)
3. Restart headless (like Download after Enter Browser)
4. Try API fetch WITHOUT navigating to kakao.com first
5. Compare with fetch AFTER navigating to kakao.com
"""
import sys, json, os
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

browser_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'browser_data')

# --- Step 1: Simulate post-Enter-Browser restart ---
print("=== Test A: Fresh headless on about:blank (mimics post-Enter-Browser) ===")
pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    browser_data, headless=True,
    args=['--disable-web-security', '--no-sandbox',
          '--disable-features=IsolateOrigins,site-per-process'],
    ignore_https_errors=True,
)
page = ctx.new_page()
# page is at about:blank — this is what happens after start()

# Try API fetch from about:blank
series_id = '68736342'
pid_rented = '68769814'  # Ch6 (rented)
pid_free = '68769789'    # Ch1 (free)

for pid, label in [(pid_free, 'Ch1 FREE'), (pid_rented, 'Ch6 RENTED')]:
    result = page.evaluate("""
    async ([s, p]) => {
        try {
            const resp = await fetch(
                `https://bff-page.kakao.com/api/gateway/api/v1/viewer/data?series_id=${s}&product_id=${p}`
            );
            const data = await resp.json();
            const vd = data.viewerData || {};
            return {
                ok: (vd.contentsList || []).length > 0,
                count: (vd.contentsList || []).length,
                msg: data.message || null,
            };
        } catch(e) {
            return { ok: false, count: 0, msg: e.toString() };
        }
    }
    """, [series_id, pid])
    status = "✅" if result['ok'] else "❌"
    print(f"  {status} {label}: chunks={result['count']}, msg={result['msg']}")

page.close()
ctx.close()
pw.stop()

# --- Step 2: Same but navigate to kakao.com first ---
print("\n=== Test B: Navigate to kakao.com first (mimics Fetch Info flow) ===")
pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    browser_data, headless=True,
    args=['--disable-web-security', '--no-sandbox',
          '--disable-features=IsolateOrigins,site-per-process'],
    ignore_https_errors=True,
)
page = ctx.new_page()
page.goto(f'https://page.kakao.com/content/{series_id}',
          wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(2000)

for pid, label in [(pid_free, 'Ch1 FREE'), (pid_rented, 'Ch6 RENTED')]:
    result = page.evaluate("""
    async ([s, p]) => {
        try {
            const resp = await fetch(
                `https://bff-page.kakao.com/api/gateway/api/v1/viewer/data?series_id=${s}&product_id=${p}`
            );
            const data = await resp.json();
            const vd = data.viewerData || {};
            return {
                ok: (vd.contentsList || []).length > 0,
                count: (vd.contentsList || []).length,
                msg: data.message || null,
            };
        } catch(e) {
            return { ok: false, count: 0, msg: e.toString() };
        }
    }
    """, [series_id, pid])
    status = "✅" if result['ok'] else "❌"
    print(f"  {status} {label}: chunks={result['count']}, msg={result['msg']}")

page.close()
ctx.close()
pw.stop()
print("\nDone.")
