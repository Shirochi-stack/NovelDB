"""Public-data recovery and atomic visible-page updates, using local fixtures."""
import asyncio
import gzip
import hashlib
import io
import json
from urllib.parse import urlsplit

from PIL import Image
from playwright.async_api import async_playwright

from test_catalog_previews import PreviewSite, previews
from test_catalog_worker_browser import BOOT
from test_metadata_frontend_browser import FixtureSite, row, wait_loaded


def test_cover_toggle_keeps_ready_pixels_while_replacement_is_blocked():
    async def scenario():
        image = io.BytesIO()
        Image.new('RGB', (800, 1200), 'purple').save(image, format='PNG')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(covers=True)
            release, started = asyncio.Event(), asyncio.Event()
            release.set()
            async def route(r):
                if urlsplit(r.request.url).hostname == 'covers.test':
                    started.set()
                    await release.wait()
                    await r.fulfill(body=image.getvalue(), content_type='image/png', headers={'access-control-allow-origin':'*'})
                else:
                    await site.route(r)
            await page.route('**/*', route)
            try:
                await page.goto('https://metadata.test/')
                await wait_loaded(page)
                await page.locator('.card-cover.loaded').wait_for()
                await page.evaluate('window.card=document.querySelector(".novel-card");window.cover=card.querySelector(".card-cover")')
                release.clear(); started.clear()
                await page.locator('#lowResCovers').uncheck()
                await asyncio.wait_for(started.wait(), 5)
                assert await page.evaluate('cover.isConnected && cover.classList.contains("loaded") && getComputedStyle(cover).opacity==="1"')
                assert await page.evaluate('card===document.querySelector(".novel-card") && getComputedStyle(cover).transitionDuration==="0s"')
                release.set()
                await page.wait_for_function('document.querySelector(".card-cover-wrap > .card-cover")?.naturalWidth===800')
                await page.locator('#loadDescriptions').uncheck()
                assert await page.evaluate('card===document.querySelector(".novel-card")')
                # Rapid changes cancel obsolete replacements without blanking.
                await page.evaluate('NovelCovers.setLowResolution(true);NovelCovers.setLowResolution(false);NovelCovers.setLowResolution(true)')
                await page.wait_for_function('document.querySelector(".card-cover-wrap > .card-cover")?.naturalWidth<=320 && !document.querySelector(".cover-replacement")')
                assert await page.evaluate('card===document.querySelector(".novel-card")')
            finally:
                release.set()
                await browser.close()
    asyncio.run(scenario())


def test_all_sources_ranking_preview_ignores_stalled_other_manifests_and_tags(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            site = PreviewSite(previews, blocked=True)
            release = asyncio.Event()
            async def route(r):
                name = urlsplit(r.request.url).path.rsplit('/', 1)[-1]
                if (name.endswith('_manifest.json') and not name.startswith('npia_')) or name.startswith('tags_'):
                    await release.wait()
                await site.route(r)
            await page.route('**/*', route)
            try:
                await page.goto('https://metadata.test/#src=all')
                await page.wait_for_function('document.querySelectorAll(".novel-card").length===30')
                assert set(await page.locator('.novel-card').evaluate_all('(cards)=>cards.map(c=>c.dataset.source)')) == {'npia'}
                for period in ('weekly','monthly','daily'):
                    await page.select_option('#sortSelect', period)
                    await page.wait_for_function('document.querySelectorAll(".novel-card").length===30')
                    assert set(await page.locator('.novel-card').evaluate_all('(cards)=>cards.map(c=>c.dataset.source)')) == {'npia'}
                assert await page.locator('body').get_attribute('data-catalog-ready') == 'false'
            finally:
                release.set(); site.release.set()
                await page.unroute_all(behavior='ignoreErrors')
                await browser.close()
    asyncio.run(scenario())


def test_text_search_switches_to_all_sources_and_runs_before_remaining_imports_finish():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite()
            release = asyncio.Event()
            hold = False
            async def route(r):
                if hold and '/nseries_chunk_' in r.request.url and '.json.gz' in r.request.url:
                    await release.wait()
                await site.route(r)
            await page.route('**/*', route)
            try:
                await page.goto('https://metadata.test/')
                await wait_loaded(page)
                hold = True
                await page.locator('#searchInput').fill('nweb translated')
                await page.wait_for_function('document.querySelector("#sourceSelect").value==="all"')
                await page.wait_for_function('document.querySelectorAll(".novel-card").length===3 && document.querySelector(".novel-card")?.dataset.source==="nweb"')
                assert await page.locator('body').get_attribute('data-catalog-ready') == 'false'
                release.set(); await wait_loaded(page)
                assert await page.locator('.novel-card').count() == 3
            finally:
                release.set(); await browser.close()
    asyncio.run(scenario())


def test_chunk_mismatch_refreshes_manifest_once_and_keeps_saved_data_on_persistent_failure():
    class Site(FixtureSite):
        version = 1
        manifest_calls = 0
        chunk_calls = 0
        bad = False
        def payload(self):
            item = row('npia', self.version)
            return gzip.compress(json.dumps({'novels':[item]}).encode(), mtime=0)
        def manifest(self):
            return dict(files=['npia_chunk_0.json.gz'], chunks=1, totalEntries=1,
                        chunkHashes=['f'*64 if self.bad else hashlib.sha256(self.payload()).hexdigest()])
        async def route(self, r):
            path = urlsplit(r.request.url).path
            if path == '/':
                await r.fulfill(body='<html>Test</html>', content_type='text/html')
            elif path.endswith('npia_chunk_manifest.json'):
                self.manifest_calls += 1
                await r.fulfill(json=self.manifest())
            elif path.endswith('npia_chunk_0.json.gz'):
                self.chunk_calls += 1
                await r.fulfill(body=self.payload(), content_type='application/gzip')
            else:
                await super().route(r)
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            site = Site()
            await page.route('**/*', site.route)
            await page.goto('https://metadata.test/')
            await page.evaluate(BOOT)
            cfg = dict(format='array', chunked=True, chunkFiles=['https://metadata.test/data/npia_chunk_0.json.gz'],
                       chunkHashes=site.manifest()['chunkHashes'], manifestUrl='data/npia_chunk_manifest.json')
            await page.evaluate("cfg=>rpc('import',{source:'npia',cfg})", cfg)
            site.version = 2
            stale = {**cfg, 'chunkHashes':['e'*64]}
            result = await page.evaluate("cfg=>rpc('import',{source:'npia',cfg})", stale)
            assert not result['warnings'] and result['refreshed']['chunkHashes'] == site.manifest()['chunkHashes']
            assert site.manifest_calls == 1 and site.chunk_calls == 4
            options = dict(sources=['npia'], text='', status='all', audience='all', sort={'sortBy':'daily','order':'desc'}, page=1,size=30)
            assert (await page.evaluate("q=>rpc('query',q)", options))['rows'][0]['id'] == '2'
            site.bad = True
            result = await page.evaluate("cfg=>rpc('import',{source:'npia',cfg})", stale)
            assert result['warnings'] and result['total'] == 1
            assert site.manifest_calls == 2 and site.chunk_calls == 8
            assert (await page.evaluate("q=>rpc('query',q)", options))['rows'][0]['id'] == '2'
            await browser.close()
    asyncio.run(scenario())


def test_cache_cleanup_watchdog_keeps_page_and_reenables_controls():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite().route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.evaluate('''() => {
                window.savedWorker=Worker;window.savedTimeout=setTimeout;
                window.card=document.querySelector('.novel-card');
                window.Worker=function(url,...args){
                    if(String(url).startsWith('cache-worker')) return {
                        postMessage(){},terminate(){window.cleanupTerminated=true}
                    };
                    return new savedWorker(url,...args);
                };
                window.setTimeout=(fn,ms,...args)=>savedTimeout(fn,ms===40000 ? 20 : ms,...args);
            }''')
            await page.locator('#clearCache').click()
            await page.wait_for_function('document.querySelector("#cacheStatus").textContent.includes("timed out")')
            assert await page.evaluate('cleanupTerminated && card===document.querySelector(".novel-card")')
            assert not await page.locator('#clearCache').is_disabled()
            await page.evaluate('window.Worker=savedWorker;window.setTimeout=savedTimeout')
            await page.select_option('#sourceSelect','sfc')
            await wait_loaded(page)
            assert await page.locator('.novel-card').get_attribute('data-source') == 'sfc'
            await browser.close()
    asyncio.run(scenario())
