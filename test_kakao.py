"""Test accessibility tree and aria snapshot for real text extraction."""
import sys, os, tempfile, shutil
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

tmp_dir = os.path.join(tempfile.gettempdir(), 'kakao_test_a11y')
os.makedirs(tmp_dir, exist_ok=True)

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    tmp_dir, headless=False,
    args=['--disable-web-security', '--no-sandbox'],
    ignore_https_errors=True,
)
page = ctx.new_page()

viewer_url = 'https://page.kakao.com/content/48787313/viewer/48787418'
print(f"Navigating to {viewer_url}...")
page.goto(viewer_url, wait_until="networkidle", timeout=60000)
page.wait_for_timeout(5000)

# Click right to advance past cover
for i in range(3):
    page.evaluate("""
        (function() {
            var vw = window.innerWidth;
            var vh = window.innerHeight;
            var el = document.elementFromPoint(vw * 0.85, vh * 0.5);
            if (el) el.click();
        })()
    """)
    page.wait_for_timeout(1500)

# Take screenshot of text page
ss_path = os.path.join(os.path.dirname(__file__), 'kakao_text_page.png')
page.screenshot(path=ss_path, full_page=False)
print(f"Text page screenshot: {ss_path}")

# Try accessibility tree
try:
    snapshot = page.accessibility.snapshot()
    print(f"\nAccessibility tree root: {snapshot.get('role') if snapshot else 'None'}")
    if snapshot and snapshot.get('children'):
        for child in snapshot['children'][:20]:
            name = child.get('name', '')
            role = child.get('role', '')
            if name and len(name) > 5:
                print(f"  [{role}] {name[:200]}")
except Exception as e:
    print(f"Accessibility snapshot error: {e}")

# Also try getting computed text via range/selection
selection_text = page.evaluate("""
(function() {
    // Try to select all text and get it
    var range = document.createRange();
    range.selectNodeContents(document.body);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    var text = selection.toString();
    selection.removeAllRanges();
    return text.substring(0, 3000);
})()
""")
print(f"\nSelection text ({len(selection_text)} chars):")
print(selection_text[:1000])

# Try checking the __NEXT_DATA__ for server-side content
next_data = page.evaluate("""
(function() {
    var nd = document.getElementById('__NEXT_DATA__');
    if (nd) return nd.textContent.substring(0, 3000);
    return 'no __NEXT_DATA__';
})()
""")
print(f"\n__NEXT_DATA__ ({len(next_data)} chars):")
print(next_data[:1500])

page.close()
ctx.close()
pw.stop()
shutil.rmtree(tmp_dir, ignore_errors=True)
print("\nDone.")
