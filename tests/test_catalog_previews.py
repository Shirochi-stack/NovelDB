"""Real preview generation and browser handoff checks against the full index."""
import asyncio
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright

from scripts.build_catalog_preview import build
from test_metadata_frontend_browser import FixtureSite, SOURCES, row, wait_loaded


@pytest.fixture(scope='module')
def previews(tmp_path_factory):
    directory = tmp_path_factory.mktemp('previews')
    boards = {
        'nweb':['best_102_daily_free','best_102_weekly_free'],
        'jara':['all_today','all_weekly','all_monthly'],
        'mpia':['TODAY_BEST'],'rbooks':['1650_weekly','1650_monthly'],
    }
    for source in SOURCES:
        novels = []
        for i in range(1,351):
            item = row(source,i,True,i%2==0)
            item[1] = f'{source} original {i:04}'
            item[9] = f'2026-10-{i%28+1:02}'
            if source=='npia':
                item[13] = i if i<=100 else 0
                item[10] = 351-i if i>250 else 0
                item[12] = (i*17)%99+1 if i<=120 else 0
            elif source=='sfc':
                item[11] = 351-i if i>230 else 0
                item[17] = 'Deferred synopsis'
            elif source in boards:
                item[15] = {board:((i*(index+3))%100)+1 for index,board in enumerate(boards[source])} if i<140 else {}
            novels.append(item)
        names,hashes = [],[]
        for number,rows in enumerate([novels[:175],novels[175:]]):
            payload = {'novels':rows,'translations':{str(r[0]):f'{source} translated {r[0]}' for r in rows}}
            body = gzip.compress(json.dumps(payload).encode(),mtime=0)
            name = f'{source}_chunk_{number}.json.gz'
            (directory/name).write_bytes(body)
            names.append(name);hashes.append(hashlib.sha256(body).hexdigest())
        manifest = dict(files=names,chunks=2,chunkHashes=hashes,totalEntries=350,embedded=True)
        if source not in ('npia','kpage','sfc'):
            manifest.update(format='metadata-v1',descriptionShardCount=128,
                descriptionShardPrefix=f'{source}_descriptions_shard_',
                boards={b:{'label':b,'stale':False} for b in boards.get(source,[])},coverage={'complete':True})
        (directory/f'{source}_chunk_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        build(source,directory)
    return directory


class PreviewSite(FixtureSite):
    def __init__(self,directory,*,blocked=False,broken_preview=False):
        super().__init__()
        self.directory = directory
        self.release = asyncio.Event()
        self.chunk_started = asyncio.Event()
        self.chunk_requests = []
        self.preview_requests = []
        self.broken_preview = broken_preview
        if not blocked:self.release.set()

    async def route(self,route):
        name = urlsplit(route.request.url).path.rsplit('/',1)[-1]
        file = self.directory/name
        if file.is_file():
            if '_preview' in name:self.preview_requests.append(name)
            if '_chunk_' in name and name.endswith('.json.gz') and '_preview' not in name:
                self.chunk_requests.append(name);self.chunk_started.set()
                await self.release.wait()
            if self.broken_preview and '_preview' in name:
                await route.fulfill(body=gzip.compress(b'{"version":1}'),content_type='application/gzip')
            elif name.endswith('.gz'):
                await route.fulfill(body=file.read_bytes(),content_type='application/gzip')
            else:
                await route.fulfill(body=file.read_bytes(),content_type='application/json')
            return
        await super().route(route)


def test_generated_previews_match_full_worker_order_for_every_profile(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            site = PreviewSite(previews)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/#src=all')
            await wait_loaded(page)
            worker = next(w for w in page.workers if 'catalog-worker' in w.url)
            for source in SOURCES:
                bundle = json.loads(gzip.decompress((previews/f'{source}_chunk_preview.json.gz').read_bytes()))
                assert 'descriptions' not in bundle
                if source=='sfc':assert all(not r[17] for r in bundle['novels'])
                for sort,positions in bundle['profiles'].items():
                    options = dict(sources=[source],text='',author=None,status='all',audience='all',
                        **{'and':[],'or':[],'exclude':[]},focus=None,page=1,size=300,
                        sort=dict(sortBy=sort,order='desc',audience='all',defaultSource=source))
                    actual = await worker.evaluate('q=>query(q,latestQuery)',options)
                    assert [n['id'] for n in actual['rows']] == [str(bundle['novels'][i][0]) for i in positions],(source,sort)
                    assert [n['_catalogOrdinal'] for n in actual['rows']] == [bundle['ordinals'][i] for i in positions]
            await browser.close()
    asyncio.run(scenario())


def test_three_preview_pages_lock_during_import_and_repeat_switches_are_immediate(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            site = PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===100")
            assert await page.locator('body').get_attribute('data-catalog-ready') == 'false'
            assert not site.release.is_set()
            bundle = json.loads(gzip.decompress((previews/'npia_chunk_preview.json.gz').read_bytes()))
            for sort in ['daily','weekly','monthly']:
                await page.select_option('#sortSelect',sort)
                for number in [1,2,3]:
                    # Use the existing page-jump control while import is held.
                    if number>1:
                        await page.locator('#paginationBottom .page-input').fill(str(number))
                        await page.locator('#paginationBottom .go-page').click()
                    expected = [str(bundle['novels'][i][0]) for i in bundle['profiles'][sort][(number-1)*100:number*100]]
                    assert await page.locator('.novel-card').evaluate_all('cs=>cs.map(c=>c.dataset.novelId)') == expected
                await page.select_option('#sortSelect','daily' if sort!='daily' else 'weekly')
            await page.evaluate('''window.lockedCards=[...document.querySelectorAll('.novel-card')];window.cardMutations=0;
                new MutationObserver(()=>cardMutations++).observe(document.querySelector('#results'),{childList:true})''')
            site.release.set()
            await wait_loaded(page)
            assert await page.evaluate('cardMutations===0&&lockedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])')
            assert await page.locator('.novel-card').count()==100
            await page.locator('#paginationBottom .page-input').fill('4')
            await page.locator('#paginationBottom .go-page').click()
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===50")
            # Warm each source, then monitor the existing status during returns.
            for source in ['sfc','nweb','all']:
                await page.select_option('#sourceSelect',source);await wait_loaded(page)
            downloads = len(site.chunk_requests)
            await page.evaluate('''window.searchFlashes=0;new MutationObserver(()=>{if(document.querySelector('#resultCount').textContent.includes('Searching catalog'))searchFlashes++}).observe(document.querySelector('#resultCount'),{childList:true})''')
            for source in ['npia','sfc','nweb','all']:
                await page.select_option('#sourceSelect',source)
                assert await page.locator('.novel-card').count()==100
                await wait_loaded(page)
            assert await page.evaluate('searchFlashes')==0
            assert len(site.chunk_requests)==downloads
            assert len(site.preview_requests)==len(set(site.preview_requests))
            await browser.close()
    asyncio.run(scenario())


def test_bad_preview_falls_back_to_catalog_and_saved_filters_skip_preview(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            for suffix,broken,count in [('',True,100),('#src=npia&q=original%200042',False,1),('#src=npia&page=4',False,50)]:
                page = await browser.new_page()
                await page.route('**/*',PreviewSite(previews,broken_preview=broken).route)
                await page.goto('https://metadata.test/'+suffix)
                await wait_loaded(page)
                assert await page.locator('.novel-card').count()==count
                if count==1:assert await page.locator('.novel-card').get_attribute('data-novel-id')=='42'
                await page.close()
            await browser.close()
    asyncio.run(scenario())
