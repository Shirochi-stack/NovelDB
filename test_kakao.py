"""Full API-only chapter fetch — no page navigation needed."""
import sys, json, tempfile, shutil, os, time
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

tmp_dir = os.path.join(tempfile.gettempdir(), 'kakao_fast')
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
# Load book page once
page.goto(f'https://page.kakao.com/content/{series_id}',
          wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(2000)

# Test 3 free chapters
chapters = [
    ('56523944', 'Ch1'),
    ('56523947', 'Ch2'),
    ('56559096', 'Ch3'),
]

for product_id, label in chapters:
    t0 = time.time()
    
    # Step 1: Get viewer data (contains sdownload URLs)
    result = page.evaluate("""
    async ([s, p]) => {
        const resp = await fetch(`https://bff-page.kakao.com/api/gateway/api/v1/viewer/data?series_id=${s}&product_id=${p}`);
        const data = await resp.json();
        const vd = data.viewerData || {};
        const baseUrl = vd.atsServerUrl || '';
        const contents = vd.contentsList || [];
        
        // Fetch all content JSONs in parallel
        const fetches = contents.map(async (c) => {
            if (!c.secureUrl) return null;
            const url = baseUrl + c.secureUrl;
            try {
                const r = await fetch(url);
                const ct = r.headers.get('content-type') || '';
                if (!ct.includes('json')) return null;
                return await r.text();
            } catch(e) { return null; }
        });
        
        const results = await Promise.all(fetches);
        return results.filter(r => r !== null);
    }
    """, [series_id, product_id])
    
    t1 = time.time()
    
    # Parse the JSON chunks using our scraper logic
    from external_scraper import ExternalScraper
    scraper = ExternalScraper(logger=lambda m: None)
    
    # Convert string results to bytes for the parser
    json_chunks = [r.encode('utf-8') for r in result if r]
    paragraphs = scraper._kakao_extract_from_json(json_chunks)
    paragraphs = scraper._kakao_strip_headings(paragraphs)
    
    real_paras = [p for p in paragraphs if p.strip() and p != '&nbsp;']
    
    print(f"[{label}] {t1-t0:.2f}s — {len(json_chunks)} chunks, {len(real_paras)} real paragraphs")
    if real_paras:
        print(f"  First: {real_paras[0][:80]}")
        print(f"  Last:  {real_paras[-1][:80]}")

page.close(); ctx.close(); pw.stop()
shutil.rmtree(tmp_dir, ignore_errors=True)
print("\nDone.")
