import sys
from playwright.sync_api import sync_playwright
url = "http://localhost:18437/apps/"
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, w, h, scheme in [("desk-dark", 1400, 900, "dark"), ("desk-light", 1400, 900, "light"), ("phone-dark", 390, 844, "dark")]:
        pg = b.new_page(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(url, wait_until="networkidle")
        pg.evaluate("document.querySelectorAll('img[loading=lazy]').forEach(i=>i.loading='eager')")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=f"page-{name}.png", full_page=True)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        print(name, "scrollWidth", sw, "errors", errors)
    b.close()
