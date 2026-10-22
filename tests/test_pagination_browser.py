"""Exercise mobile pagination with a large catalog served by a local fixture."""
import asyncio
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from test_metadata_frontend_browser import DOCS, FixtureSite, wait_loaded


def test_mobile_pagination_stays_in_one_row_and_keeps_navigation_working():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True)
            await page.add_init_script("localStorage.setItem('noveldb.mobileInfiniteScroll','false')")
            site = FixtureSite()

            async def route(request):
                if urlsplit(request.request.url).path == '/catalog-worker.js':
                    # Model a large result set while retaining the real import and rendering paths.
                    script = (DOCS / 'catalog-worker.js').read_text(encoding='utf-8')
                    query = 'else result = await query(value,id);'
                    assert query in script
                    script = script.replace(query, '''else {
                        result = await query({...value,page:1},id);
                        result = {...result,total:1117912,page:value.page};
                    }''')
                    await request.fulfill(body=script, content_type='text/javascript')
                else:
                    await site.route(request)

            await page.route('**/*', route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            top = page.locator('#paginationTop')

            async def jump(target, enter=False):
                await top.locator('.page-input').fill(str(target))
                if enter:
                    await top.locator('.page-input').press('Enter')
                else:
                    await top.locator('.page-input').press('Enter')
                await page.wait_for_function('''target => [...document.querySelectorAll('.pagination')]
                    .every(bar=>bar.querySelector('[aria-current=page]')?.textContent===String(target))''', arg=target)

            async def mobile_layout():
                for bar in await page.locator('.pagination').all():
                    assert await bar.evaluate('''bar=>{
                        const rect=bar.getBoundingClientRect(), numbers=bar.querySelector('.page-numbers');
                        const buttons=[...numbers.children].map(el=>el.getBoundingClientRect());
                        const controls=[...bar.querySelectorAll('.prev-page,.page-jump,.next-page')].map(el=>el.getBoundingClientRect());
                        return rect.width<=innerWidth && bar.scrollWidth<=bar.clientWidth
                            && numbers.scrollWidth<=numbers.clientWidth && rect.height<=60
                            && buttons.every(r=>Math.abs(r.top+r.height/2-buttons[0].top-buttons[0].height/2)<1)
                            && controls.every(r=>Math.abs(r.top-controls[0].top)<1)
                            && Math.abs(controls[0].top-buttons[0].top)<1;
                    }''')
                    assert await bar.locator('.page-numbers .page-btn').count() <= 4
                    assert await bar.locator('[aria-current=page]').count() == 1
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')

            for width in (320, 360, 390, 430):
                await page.set_viewport_size({'width': width, 'height': 844})
                for target in (1, 5, 18632, 37264):
                    await jump(target, enter=target == 18632)
                    await mobile_layout()
                    if target in (5, 18632):
                        await top.screenshot(path=f'artifacts/mobile-pagination-{width}-{target}.png')
                    if target == 1:
                        assert await top.locator('.prev-page').is_disabled()
                    if target == 37264:
                        assert await top.locator('.next-page').is_disabled()
                await jump(5)
                await top.locator('.next-page').click()
                await page.wait_for_function("document.querySelector('#paginationTop [aria-current=page]')?.textContent==='6'")
                await top.locator('.prev-page').click()
                await page.wait_for_function("document.querySelector('#paginationTop [aria-current=page]')?.textContent==='5'")
                await top.get_by_role('button', name='Page 4', exact=True).click()
                await page.wait_for_function("document.querySelector('#paginationTop [aria-current=page]')?.textContent==='4'")
            for invalid in ('0','37265','2.5'):
                await top.locator('.page-input').fill(invalid)
                await top.locator('.page-input').press('Enter')
                assert await top.locator('[aria-current=page]').inner_text() == '4'
            await jump(18632)
            await page.evaluate('window.savedCards=[...document.querySelectorAll(".novel-card")]')
            await page.set_viewport_size({'width': 1200, 'height': 900})
            await page.wait_for_function("document.querySelectorAll('#paginationTop .page-numbers .page-btn').length===7")
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.wait_for_function("document.querySelectorAll('#paginationTop .page-numbers .page-btn').length===4")
            await mobile_layout()
            assert await page.evaluate('savedCards.length>0 && savedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])')
            await browser.close()

    asyncio.run(scenario())
