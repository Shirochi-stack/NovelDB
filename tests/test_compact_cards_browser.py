"""Bound card previews, complete detail tags and saved density controls."""
import asyncio
import gzip
import json
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

import test_metadata_frontend_browser as frontend
from test_metadata_frontend_browser import FixtureSite, open_settings, close_settings, wait_loaded


def test_compact_previews_tag_details_filtering_and_density_persist(monkeypatch):
    tags = ['판타지'] + [f'Tag number {i}' for i in range(1, 25)] + ['Long tag ' * 35]
    synopsis = 'Full synopsis with all original words. ' * 100
    original = frontend.row
    def tagged_row(*args, **kwargs):
        result = original(*args, **kwargs)
        result[4] = tags.copy()
        return result
    monkeypatch.setattr(frontend, 'row', tagged_row)

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            site=FixtureSite(covers=True)
            async def route(r):
                if 'descriptions_shard_' in urlsplit(r.request.url).path:
                    await r.fulfill(body=gzip.compress(json.dumps({str(i):synopsis for i in (7,8,9)}).encode()),content_type='application/gzip')
                else:await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.wait_for_selector('.synopsis-text')
            card=page.locator('.novel-card').first
            async def assert_rows(rows):
                await page.wait_for_function('!document.querySelector(".card-tags-more").hidden')
                geometry=await card.locator('.card-tags').evaluate('''tags=>{
                    const visible=[...tags.children].filter(e=>!e.hidden);
                    return {rows:new Set(visible.map(e=>Math.round(e.getBoundingClientRect().top))).size,
                        count:tags.querySelectorAll('.card-tag:not([hidden])').length,
                        more:tags.querySelector('.card-tags-more').textContent,
                        overflow:tags.scrollWidth-tags.clientWidth};
                }''')
                assert geometry['rows']<=rows and geometry['overflow']<=1,geometry
                assert geometry['more']==f"+{len(tags)-geometry['count']} tags"
            for width in (320,390,1920):
                await page.set_viewport_size({'width':width,'height':844 if width<1000 else 1080})
                await assert_rows(3)
                assert await card.locator('.synopsis-text').evaluate('e=>e.clientHeight<=parseFloat(getComputedStyle(e).lineHeight)*5+1 && e.scrollHeight>e.clientHeight')
                assert await card.locator('.card-synopsis').evaluate('e=>e.offsetHeight<200')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await card.screenshot(path=f'artifacts/compact-card-{width}.png')
            await page.set_viewport_size({'width':390,'height':844})
            await card.locator('.card-tags-more').click()
            dialog=page.locator('.novel-details')
            await dialog.wait_for()
            assert await dialog.locator('.details-tag').count()==len(tags)
            assert await dialog.locator('.details-tag').first.inner_text()=='Fantasy'
            # Pick a formerly hidden tag: filter with its raw value and close Details.
            chosen=dialog.locator('.details-tag').nth(24)
            assert await chosen.get_attribute('data-tag')==tags[24]
            await chosen.click()
            await page.wait_for_function('!document.querySelector(".novel-details").open && document.querySelector(".card-tags .card-tag")?.dataset.tag==="Tag number 24"')
            assert await card.locator('.card-tag').first.is_visible()
            assert 'Tag number 24' in await page.locator('#activeTagsSummary').inner_text()
            assert await card.evaluate('c=>c._record.tags')==tags
            await assert_rows(3)
            await card.locator('.synopsis-details').click()
            assert await dialog.locator('[data-field=synopsis]').inner_text()==synopsis.strip()
            assert await dialog.locator('.details-tag').first.get_attribute('data-tag')==tags[24]
            await page.get_by_role('button',name='Close Details').click()
            await page.evaluate('window.originalCard=document.querySelector(".novel-card");window.originalImage=originalCard.querySelector(".card-cover")')
            await open_settings(page)
            await page.locator('#compactCards').uncheck()
            await close_settings(page)
            assert await card.locator('.card-tag:visible').count()==len(tags)
            assert not await card.locator('.card-tags-more').is_visible()
            assert not await card.locator('.synopsis-details').is_visible()
            assert await page.locator('#synopsisLines').is_disabled()
            assert await card.locator('.card-synopsis').evaluate('e=>e.scrollHeight>e.clientHeight && getComputedStyle(e).overflowY==="auto"')
            await open_settings(page)
            await page.locator('#compactCards').check()
            for ident,value in [('synopsisLines',2),('tagRows',1)]:
                await page.locator('#'+ident).evaluate('(e,v)=>{e.value=v;e.dispatchEvent(new Event("input",{bubbles:true}))}',value)
            await close_settings(page)
            await assert_rows(1)
            assert await card.locator('.synopsis-text').evaluate('e=>e.clientHeight<=parseFloat(getComputedStyle(e).lineHeight)*2+1')
            assert await page.evaluate('originalCard===document.querySelector(".novel-card") && originalImage===originalCard.querySelector(".card-cover")')
            await page.reload();await wait_loaded(page)
            await page.wait_for_selector('.synopsis-text')
            assert await page.locator('#synopsisLines').input_value()=='2'
            assert await page.locator('#tagRows').input_value()=='1'
            assert await page.locator('#compactCards').is_checked()
            await assert_rows(1)
            await open_settings(page)
            await page.locator('#compactCards').uncheck()
            await close_settings(page)
            await page.reload();await wait_loaded(page)
            assert not await page.locator('#compactCards').is_checked()
            assert await card.locator('.card-tag:visible').count()==len(tags)
            assert not errors
            await browser.close()
    asyncio.run(scenario())
