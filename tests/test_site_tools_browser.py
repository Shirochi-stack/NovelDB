"""Settings, app cache clearing, and display diagnostics in a local browser."""
import asyncio
import io
from urllib.parse import urlsplit

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from test_metadata_frontend_browser import FixtureSite, wait_loaded
from test_catalog_previews import PreviewSite, previews


@pytest.mark.parametrize('readable', [True, False])
def test_cover_resolution_toggle_changes_pixels_without_moving_cards_and_survives_reload(readable):
    async def scenario():
        data = io.BytesIO()
        Image.new('RGB',(1600,2400),'#8040c0').save(data,format='PNG')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            site = FixtureSite(covers=True)
            async def route(r):
                if urlsplit(r.request.url).hostname == 'covers.test':
                    await r.fulfill(body=data.getvalue(),content_type='image/png',
                        headers={'access-control-allow-origin':'*' if readable else 'https://different.test'})
                else: await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.locator('.card-cover.loaded').wait_for()
            await page.evaluate('window.card=document.querySelector(".novel-card")')
            if readable:
                assert await page.locator('img.card-cover.loaded').evaluate('img=>img.naturalWidth') <= 480
            else:
                assert await page.locator('canvas.card-cover.loaded').evaluate('img=>img.width') <= 480
            await page.locator('#lowResCovers').uncheck()
            original = page.locator('img.card-cover.loaded')
            await original.wait_for()
            assert await original.evaluate('img=>img.naturalWidth') == 1600
            assert await page.evaluate('card===document.querySelector(".novel-card")')
            assert await page.evaluate('localStorage.getItem("noveldb.lowResCovers")') == 'false'
            await page.reload(); await wait_loaded(page)
            assert not await page.locator('#lowResCovers').is_checked()
            await original.wait_for()
            assert await original.evaluate('img=>img.naturalWidth') == 1600
            await page.locator('#lowResCovers').check()
            await page.locator('.card-cover.loaded').wait_for()
            assert await page.locator('.card-cover.loaded').evaluate('img=>(img.naturalWidth||img.width)<=480')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await browser.close()
    asyncio.run(scenario())


def test_clear_cache_removes_app_data_preserves_settings_and_other_apps_then_reloads():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            site = FixtureSite()
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            other = await page.context.new_page()
            await other.route('**/*',site.route)
            await other.goto('https://metadata.test/')
            await wait_loaded(other)
            await page.locator('#lowResCovers').uncheck()
            await page.locator('#loadDescriptions').uncheck()
            await page.evaluate('''async()=>{
                const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('noveldb-catalog-v1');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});
                await new Promise((resolve,reject)=>{const tx=db.transaction('meta','readwrite');tx.objectStore('meta').put('old-cache','sentinel');tx.oncomplete=resolve;tx.onerror=reject});db.close();
                await new Promise(resolve=>{const r=indexedDB.open('other-app');r.onsuccess=()=>{r.result.close();resolve()}});
                for(const name of ['noveldb-test','other-app']) {const c=await caches.open(name);await c.put('/cache-sentinel',new Response('keep or clear'))}
                localStorage.setItem('other-app','keep');
            }''')
            async with page.expect_navigation():
                await page.locator('#clearCache').click()
            await wait_loaded(page)
            state = await page.evaluate('''async()=>{
                const db=await new Promise(resolve=>{const r=indexedDB.open('noveldb-catalog-v1');r.onsuccess=()=>resolve(r.result)});
                const sentinel=await new Promise(resolve=>{const r=db.transaction('meta').objectStore('meta').get('sentinel');r.onsuccess=()=>resolve(r.result??null)});db.close();
                return {sentinel,caches:await caches.keys(),dbs:(await indexedDB.databases()).map(d=>d.name),other:localStorage.getItem('other-app')};
            }''')
            assert state['sentinel'] is None
            assert 'noveldb-test' not in state['caches']
            assert 'other-app' in state['caches'] and 'other-app' in state['dbs'] and state['other']=='keep'
            assert not await page.locator('#lowResCovers').is_checked()
            assert not await page.locator('#loadDescriptions').is_checked()
            assert any('fresh=' in request for request in site.requests)
            assert await page.locator('.novel-card').count()==1
            await other.wait_for_function("document.querySelector('#cacheStatus').textContent.includes('Cache cleared in another tab')")
            assert await other.locator('#clearCache').inner_text()=='Reload'
            async with other.expect_navigation():
                await other.locator('#clearCache').click()
            await wait_loaded(other)
            assert await other.locator('.novel-card').count()==1
            await browser.close()
    asyncio.run(scenario())


def test_preview_timing_stays_fixed_when_background_import_finishes_and_cache_can_clear_mid_import(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            site = PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.elapsedMs!==''")
            elapsed = await page.locator('#viewTiming').get_attribute('data-elapsed-ms')
            assert await page.locator('body').get_attribute('data-catalog-ready')=='false'
            site.release.set(); await wait_loaded(page)
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.catalogMs!==''")
            assert await page.locator('#viewTiming').get_attribute('data-elapsed-ms')==elapsed
            assert int(await page.locator('#viewTiming').get_attribute('data-catalog-ms'))>=int(elapsed)
            await page.locator('#searchInput').fill('original 0042')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===1")
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.elapsedMs!==''")
            assert int(await page.locator('#viewTiming').get_attribute('data-elapsed-ms'))>=200
            await page.locator('#searchInput').fill('')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===100")
            site.release.clear()
            await page.select_option('#sourceSelect','sfc')
            await page.wait_for_function("document.querySelector('.novel-card')?.dataset.source==='sfc'")
            assert await page.locator('body').get_attribute('data-catalog-ready')=='false'
            async with page.expect_navigation():
                await page.locator('#clearCache').click()
            await page.wait_for_function("document.querySelector('.novel-card')?.dataset.source==='sfc'")
            site.release.set(); await wait_loaded(page)
            await browser.close()
    asyncio.run(scenario())


def test_source_picker_restarts_timing_and_separates_slow_catalog_loading_from_cached_results(previews):
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page()
            site=PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.elapsedMs!==''")
            await asyncio.sleep(.35)
            site.release.set();await wait_loaded(page)
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.catalogMs!==''")
            assert int(await page.locator('#viewTiming').get_attribute('data-catalog-ms'))>=350
            await page.evaluate('''()=>{
                document.querySelector('#sourceSelect').addEventListener('change',()=>window.sourceStartedAt=performance.now(),true);
                new MutationObserver(()=>{
                    const timing=document.querySelector('#viewTiming');
                    if(timing.dataset.elapsedMs!=='') window.lastTiming={elapsed:Number(timing.dataset.elapsedMs),
                        source:timing.dataset.source,ended:performance.now(),started:window.sourceStartedAt};
                }).observe(document.querySelector('#viewTiming'),{childList:true});
            }''')
            site.release.clear()
            await page.locator('#sourcePickerButton').click()
            await page.get_by_role('option',name='SFC',exact=True).click()
            await page.wait_for_function("window.lastTiming?.source==='sfc'")
            timing=await page.evaluate('lastTiming')
            assert abs((timing['ended']-timing['started'])-timing['elapsed'])<80
            assert await page.locator('#viewTiming').get_attribute('data-catalog-ms')==''
            assert 'Catalog: loading' in await page.locator('#viewTiming').inner_text()
            await asyncio.sleep(.35)
            site.release.set();await wait_loaded(page)
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.catalogMs!==''")
            assert int(await page.locator('#viewTiming').get_attribute('data-catalog-ms'))>=350
            assert int(await page.locator('#viewTiming').get_attribute('data-elapsed-ms'))==timing['elapsed']
            await page.locator('#sourcePickerButton').click()
            await page.get_by_role('option',name='Npia',exact=True).click()
            await wait_loaded(page)
            await page.wait_for_function("document.querySelector('#viewTiming').dataset.source==='npia'&&document.querySelector('#viewTiming').dataset.catalogMs!==''")
            returned=await page.evaluate('lastTiming')
            assert returned['source']=='npia'
            # Both cached source-switch readings start at this activation,
            # rather than retaining the earlier deliberately delayed import.
            catalog=int(await page.locator('#viewTiming').get_attribute('data-catalog-ms'))
            assert abs((returned['ended']-returned['started'])-catalog)<80
            assert await page.locator('#memoryUsage').inner_text()=='Total RAM: unavailable'
            await browser.close()
    asyncio.run(scenario())
