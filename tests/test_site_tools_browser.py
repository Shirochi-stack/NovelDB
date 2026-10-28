"""Settings and app cache clearing in a local browser."""
import asyncio
import io
from urllib.parse import urlsplit

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from test_metadata_frontend_browser import open_settings, close_settings, FixtureSite, wait_loaded
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
                assert await page.locator('img.card-cover.loaded').evaluate('img=>img.naturalWidth') <= 320
            else:
                assert await page.locator('canvas.card-cover.loaded').evaluate('img=>img.width') <= 320
            await open_settings(page)
            await page.locator('#lowResCovers').uncheck()
            await close_settings(page)
            original = page.locator('img.card-cover.loaded')
            await page.wait_for_function("document.querySelector('.card-cover.loaded')?.naturalWidth===1600")
            assert await original.evaluate('img=>img.naturalWidth') == 1600
            assert await page.evaluate('card===document.querySelector(".novel-card")')
            assert await page.evaluate('localStorage.getItem("noveldb.lowResCovers")') == 'false'
            await page.reload(); await wait_loaded(page)
            assert not await page.locator('#lowResCovers').is_checked()
            await page.wait_for_function("document.querySelector('.card-cover.loaded')?.naturalWidth===1600")
            assert await original.evaluate('img=>img.naturalWidth') == 1600
            await open_settings(page)
            await page.locator('#lowResCovers').check()
            await close_settings(page)
            await page.wait_for_function("(()=>{const img=document.querySelector('.card-cover.loaded');return img && (img.naturalWidth||img.width)<=320})()")
            assert await page.locator('.card-cover.loaded').evaluate('img=>(img.naturalWidth||img.width)<=320')
            if not readable:
                await page.evaluate('window.oldCanvas=document.querySelector("canvas.card-cover");window.dispatchEvent(new Event("pageshow"))')
                await page.wait_for_function('document.querySelector("canvas.card-cover.loaded")!==oldCanvas && !document.querySelector(".cover-replacement")')
                assert await page.locator('canvas.card-cover.loaded').evaluate('img=>getComputedStyle(img).opacity==="1" && img.width<=320')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await browser.close()
    asyncio.run(scenario())


def test_clear_cache_preserves_visible_page_and_other_apps_reports_size_with_open_tabs():
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
            await open_settings(page)
            await page.locator('#lowResCovers').uncheck()
            await close_settings(page)
            await open_settings(page)
            await page.locator('#loadDescriptions').uncheck()
            await close_settings(page)
            await page.evaluate('''async()=>{
                const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('noveldb-catalog-v1');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});
                await new Promise((resolve,reject)=>{const tx=db.transaction('meta','readwrite');tx.objectStore('meta').put('old-cache','sentinel');tx.objectStore('meta').put('unused-marker',['npia',999]);tx.oncomplete=resolve;tx.onerror=reject});
                await new Promise((resolve,reject)=>{const tx=db.transaction(['cards','index'],'readwrite');
                    tx.objectStore('cards').put({rows:[[999,'Unused']],titles:[]},['npia',999,0]);
                    tx.objectStore('index').put({entries:[]},['npia',999,0]);tx.oncomplete=resolve;tx.onerror=reject});
                window.heldCacheDb=db;db.onversionchange=()=>{};
                await new Promise(resolve=>{const r=indexedDB.open('other-app');r.onsuccess=()=>{r.result.close();resolve()}});
                for(const name of ['noveldb-test','other-app']) {const c=await caches.open(name);await c.put('/cache-sentinel',new Response('keep or clear'))}
                localStorage.setItem('other-app','keep');
            }''')
            await page.evaluate('window.originalCard=document.querySelector(".novel-card")')
            await open_settings(page)
            await page.locator('#clearCache').click()
            await page.wait_for_function("document.querySelector('#cacheStatus').textContent.includes('cleared. Current page kept.')")
            assert await page.evaluate('originalCard===document.querySelector(".novel-card")')
            assert 'Approximately' in await page.locator('#cacheStatus').inner_text()
            assert 'Approximately 0 B' not in await page.locator('#cacheStatus').inner_text()
            assert not await page.locator('#clearCache').is_disabled()
            state = await page.evaluate('''async()=>{
                const db=await new Promise(resolve=>{const r=indexedDB.open('noveldb-catalog-v1');r.onsuccess=()=>resolve(r.result)});
                const sentinel=await new Promise(resolve=>{const r=db.transaction('meta').objectStore('meta').get('sentinel');r.onsuccess=()=>resolve(r.result??null)});db.close();
                const retained=await new Promise(resolve=>{const r=heldCacheDb.transaction('cards').objectStore('cards').get(['npia',0,0,'compact']);r.onsuccess=()=>resolve(!!r.result)});
                const payload=await new Promise(resolve=>{const r=heldCacheDb.transaction('cards').objectStore('cards').get(['npia',0,'payload','compact']);r.onsuccess=()=>resolve(!!r.result?.compressed)});
                const removed=await new Promise(resolve=>{const r=heldCacheDb.transaction('cards').objectStore('cards').get(['npia',999,0]);r.onsuccess=()=>resolve(!r.result)});
                return {sentinel,retained,payload,removed,caches:await caches.keys(),dbs:(await indexedDB.databases()).map(d=>d.name),other:localStorage.getItem('other-app')};
            }''')
            assert state['sentinel'] is None
            assert state['retained'] and state['payload'] and state['removed']
            assert 'noveldb-test' not in state['caches']
            assert 'other-app' in state['caches'] and 'other-app' in state['dbs'] and state['other']=='keep'
            assert not await page.locator('#lowResCovers').is_checked()
            assert not await page.locator('#loadDescriptions').is_checked()
            assert await page.locator('.novel-card').count()==1
            await other.wait_for_function("document.querySelector('#cacheStatus').textContent.includes('Current page kept')")
            assert await other.locator('#clearCache').inner_text()=='Clear cache'
            assert await other.locator('.novel-card').count()==1
            await close_settings(page)
            await page.select_option('#sourceSelect','sfc')
            await wait_loaded(page)
            assert any('fresh=' in request for request in site.requests)
            await browser.close()
    asyncio.run(scenario())


def test_cache_can_clear_mid_import(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.add_init_script("localStorage.setItem('noveldb.infiniteScroll','false')")  # page numbers
            site = PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===30")
            assert await page.locator('#viewTiming').count()==0
            assert await page.locator('#memoryUsage').count()==0
            assert await page.locator('body').get_attribute('data-catalog-ready')=='false'
            site.release.set(); await wait_loaded(page)
            site.release.clear()
            await close_settings(page)
            await page.select_option('#sourceSelect','sfc')
            await page.wait_for_function("document.querySelector('.novel-card')?.dataset.source==='sfc'")
            assert await page.locator('body').get_attribute('data-catalog-ready')=='false'
            await page.evaluate('window.originalCard=document.querySelector(".novel-card")')
            await open_settings(page)
            await page.locator('#clearCache').click()
            await page.wait_for_function("document.querySelector('#cacheStatus').textContent.includes('Current page kept')")
            assert await page.evaluate('originalCard===document.querySelector(".novel-card")')
            assert await page.locator('.novel-card').count()==30
            site.release.set()
            await close_settings(page)
            await page.select_option('#sortSelect','likes')
            await wait_loaded(page)
            await browser.close()
    asyncio.run(scenario())
