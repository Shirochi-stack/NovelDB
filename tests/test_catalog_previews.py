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
from test_metadata_frontend_browser import DOCS, FixtureSite, SOURCES, row, wait_loaded


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
            source_index=SOURCES.index(source)
            item[1] = f'{chr(65+(i+source_index)%26)} {source} original {i:04}'
            item[5]=(i*17+source_index*113)%811 if source in ('npia','kpage','sfc') or i%13 else None
            item[6]=(i*3+source_index*11)%79 if source in ('npia','kpage','sfc') or i%11 else None
            item[7]=i%57
            item[9] = f'2026-10-{i%28+1:02}'
            if source not in ('npia','kpage','sfc'):
                item[14].update(rating=((i*7+source_index)%51)/10 if i%5 else None,
                    downloads=(i*31+source_index*999)%10000,favorites=(i*19)%1000,
                    recommendations=(i*13+source_index)%211)
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
        if name=='app.js':
            script=(DOCS/'app.js').read_text(encoding='utf-8')
            hook='''window.testCatalogQuery=q=>workerRequest('query',q);
            window.testPreviewStats=()=>({rows:[...previewBundles.values()].reduce((n,b)=>n+b.novels.length,0),profiles:new Set(previewProfiles.values()).size,pending:previewRequests.size});
            window.testCombinedPreview=async(sortBy,order)=>{
                const q=diskQuery();q.sort={...q.sort,sortBy,order};q.page=1;q.size=300;
                await loadQueryPreview(q);
                const r=previewResult(q);return r ? {ids:r.rows.map(n=>n.source+':'+n.id),ordinals:r.rows.map(n=>n._catalogOrdinal)} : null;
            };'''
            await route.fulfill(body=script.replace('// === State persistence via URL hash ===',hook+'\n// === State persistence via URL hash ==='),content_type='text/javascript')
            return
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
            for source in SOURCES:
                bundle = json.loads(gzip.decompress((previews/f'{source}_chunk_preview_sorts.json.gz').read_bytes()))
                assert 'descriptions' not in bundle
                if source=='sfc':assert all(not r[17] for r in bundle['novels'])
                for key,positions in bundle['profiles'].items():
                    sort,order=json.loads(key)
                    options = dict(sources=[source],text='',author=None,status='all',audience='all',
                        **{'and':[],'or':[],'exclude':[]},focus=None,page=1,size=300,
                        sort=dict(sortBy=sort,order=order,audience='all',defaultSource=source))
                    actual = await page.evaluate('q=>testCatalogQuery(q)',options)
                    assert [n['id'] for n in actual['rows']] == [str(bundle['novels'][i][0]) for i in positions],(source,sort)
                    assert [n['_catalogOrdinal'] for n in actual['rows']] == [bundle['ordinals'][i] for i in positions]
            # Compare combined previews against a complete scan for every
            # exposed sort, including foreign boards, metrics and both orders.
            assert await page.evaluate("async()=>await testCombinedPreview('views','asc')!==null")
            board_map={source:json.loads((previews/f'{source}_chunk_manifest.json').read_text(encoding='utf-8')).get('boards',{}) for source in SOURCES}
            sorts=await page.evaluate("boards=>NovelMetadata.previewSorts('all',boards)",board_map)
            enabled=await page.locator('#sortSelect option').evaluate_all('opts=>opts.filter(o=>!o.disabled&&!o.hidden).map(o=>o.value)')
            assert set(enabled).issubset(sorts)
            for sort in sorts:
                for order in ['desc','asc']:
                    options=dict(sources=list(SOURCES),text='',author=None,status='all',audience='all',
                        **{'and':[],'or':[],'exclude':[]},focus=None,page=1,size=300,
                        sort=dict(sortBy=sort,order=order,audience='all',defaultSource='all'))
                    expected=await page.evaluate('q=>testCatalogQuery(q)',options)
                    actual=await page.evaluate('([sort,order])=>testCombinedPreview(sort,order)',[sort,order])
                    assert actual is not None,(sort,order)
                    assert actual['ids']==[f"{n['source']}:{n['id']}" for n in expected['rows']],(sort,order)
                    assert actual['ordinals']==[n['_catalogOrdinal'] for n in expected['rows']],(sort,order)
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('feed',[False,True])
def test_first_page_order_uses_ready_catalog_when_sort_preview_is_stalled(previews,feed):
    class StalledSortPreview(PreviewSite):
        def __init__(self):
            super().__init__(previews)
            self.sort_release=asyncio.Event()
        async def route(self,route):
            if urlsplit(route.request.url).path.endswith('npia_chunk_preview_sorts.json.gz'):
                await self.sort_release.wait()
            await super().route(route)
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390 if feed else 1440,'height':900})
            site=StalledSortPreview()
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            before=await page.locator('.novel-card').first.get_attribute('data-novel-id')
            try:
                await page.select_option('#orderSelect','asc')
                await page.wait_for_function('(before)=>document.querySelector(".novel-card")?.dataset.novelId!==before',arg=before,timeout=3000)
                assert await page.locator('.novel-card').first.get_attribute('data-novel-id')=='100'
            finally:
                site.sort_release.set()
                await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('feed',[False,True])
def test_order_preview_does_not_wait_for_background_warming(previews,feed):
    class StalledWarmup(PreviewSite):
        def __init__(self):
            super().__init__(previews,blocked=True)
            self.warming=asyncio.Event()
            self.warm_release=asyncio.Event()
        async def route(self,route):
            if urlsplit(route.request.url).path.endswith('npia_chunk_preview_sorts.json.gz') and not self.warming.is_set():
                self.warming.set()
                await self.warm_release.wait()
            await super().route(route)
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390 if feed else 1440,'height':900})
            site=StalledWarmup()
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await page.wait_for_function('document.querySelectorAll(".novel-card").length>0')
            try:
                await asyncio.wait_for(site.warming.wait(),timeout=10)
                await page.select_option('#orderSelect','asc')
                await page.wait_for_function('document.querySelector(".novel-card")?.dataset.novelId==="100"',timeout=3000)
                assert await page.locator('body').get_attribute('data-catalog-ready')=='false'
            finally:
                site.warm_release.set();site.release.set()
                await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('feed',[False,True])
def test_first_preview_page_changes_order_without_page_navigation(previews,feed):
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390 if feed else 1440,'height':900})
            site=PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/#sort=likes')
            await page.wait_for_function('document.querySelectorAll(".novel-card").length>0')
            bundle=json.loads(gzip.decompress((previews/'npia_chunk_preview_sorts.json.gz').read_bytes()))
            for ready in (False,True):
                if ready:site.release.set();await wait_loaded(page)
                for sort in ('likes','updated','daily'):
                    await page.select_option('#sortSelect',sort)
                    for order in ('asc','desc','asc'):
                        expected=[str(bundle['novels'][i][0]) for i in bundle['profiles'][json.dumps([sort,order],separators=(',',':'))][:30]]
                        await page.select_option('#orderSelect',order)
                        await page.wait_for_function('(expected)=>{const ids=[...document.querySelectorAll(".novel-card")].map(c=>c.dataset.novelId);return ids.length>0&&ids.every((id,i)=>id===expected[i]);}',arg=expected)
                        if not feed:assert await page.locator('#paginationTop [aria-current=page]').inner_text()=='1'
            await browser.close()
    asyncio.run(scenario())


def test_unused_previews_warm_on_disk_without_expanding_or_replacing_cards(previews):
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page();site=PreviewSite(previews)
            await page.route('**/*',site.route);await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.evaluate('window.savedCards=[...document.querySelectorAll(".novel-card")]')
            await page.evaluate("""async()=>{
                for(let i=0;i<200;i++) {
                    const cache=await caches.open('noveldb-previews-v1');
                    if((await cache.keys()).length===16 && testPreviewStats().pending===0) return;
                    await new Promise(resolve=>setTimeout(resolve,25));
                }
                throw new Error('Preview warming did not finish');
            }""")
            stats=await page.evaluate('testPreviewStats()')
            assert stats['rows']<=900 and stats['profiles']==1
            retained_stats=stats
            assert await page.evaluate('savedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])')
            requests=len(site.preview_requests)
            await page.select_option('#sortSelect','views')
            bundle=json.loads(gzip.decompress((previews/'npia_chunk_preview_sorts.json.gz').read_bytes()))
            first=str(bundle['novels'][bundle['profiles'][json.dumps(['views','desc'],separators=(',',':'))][0]][0])
            await page.wait_for_function('(id)=>document.querySelector(".novel-card")?.dataset.novelId===id',arg=first)
            assert len(site.preview_requests)==requests  # The ready index needs no additional preview download.
            stats=await page.evaluate('testPreviewStats()')
            assert stats==retained_stats  # Unused previews stay compressed on disk.
            await browser.close()
    asyncio.run(scenario())


def test_three_preview_pages_lock_during_import_and_repeat_switches_are_immediate(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            await page.add_init_script("localStorage.setItem('noveldb.infiniteScroll','false')")
            site = PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===30")
            assert await page.locator('body').get_attribute('data-catalog-ready') == 'false'
            assert not site.release.is_set()
            bundle = json.loads(gzip.decompress((previews/'npia_chunk_preview.json.gz').read_bytes()))
            for sort in ['daily','weekly','monthly']:
                await page.select_option('#sortSelect',sort)
                for number in [1,2,3]:
                    # Use the existing page-jump control while import is held.
                    if number>1:
                        await page.locator('#paginationBottom .page-input').fill(str(number))
                        await page.locator('#paginationBottom .page-input').press('Enter')
                    expected = [str(bundle['novels'][i][0]) for i in bundle['profiles'][sort][(number-1)*30:number*30]]
                    assert await page.locator('.novel-card').evaluate_all('cs=>cs.map(c=>c.dataset.novelId)') == expected
                await page.select_option('#sortSelect','daily' if sort!='daily' else 'weekly')
            await page.evaluate('''window.lockedCards=[...document.querySelectorAll('.novel-card')];window.cardMutations=0;
                new MutationObserver(()=>cardMutations++).observe(document.querySelector('#results'),{childList:true})''')
            site.release.set()
            await wait_loaded(page)
            assert await page.evaluate('cardMutations===0&&lockedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])')
            assert await page.locator('.novel-card').count()==30
            await page.locator('#paginationBottom .page-input').fill('11')
            await page.locator('#paginationBottom .page-input').press('Enter')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===30")
            # Warm each source, then monitor the existing status during returns.
            for source in ['sfc','nweb','all']:
                await page.select_option('#sourceSelect',source);await wait_loaded(page)
            downloads = len(site.chunk_requests)
            await page.evaluate('''window.searchFlashes=0;new MutationObserver(()=>{if(document.querySelector('#resultCount').textContent.includes('Searching catalog'))searchFlashes++}).observe(document.querySelector('#resultCount'),{childList:true})''')
            for source in ['npia','sfc','nweb','all']:
                await page.select_option('#sourceSelect',source)
                assert await page.locator('.novel-card').count()==30
                await wait_loaded(page)
            assert await page.evaluate('searchFlashes')==0
            assert len(site.chunk_requests)==downloads
            assert len(site.preview_requests)==len(set(site.preview_requests))
            await browser.close()
    asyncio.run(scenario())


def test_generic_native_and_combined_sort_pages_never_shift_on_import(previews):
    cases=[('npia','likes','desc'),('npia','title','asc'),('sfc','chapters','asc'),
           ('nweb','rank:nweb:best_102_daily_free','asc'),('rbooks','metric:rating','asc'),
           ('all','views','desc'),('all','title','asc'),('all','sfc_popularity','asc'),
           ('all','rank:jara:all_today','desc')]
    cases += [(source,period,order) for source,period in [('nweb','monthly'),('mpia','weekly'),
        ('rbooks','daily'),('sfc','monthly'),('kpage','daily'),('nseries','weekly'),
        ('rbooks','rank:nweb:best_102_daily_free'),('npia','sfc_popularity')]
        for order in ('desc','asc')]
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            for source,sort,order in cases:
                page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
                await page.add_init_script("localStorage.setItem('noveldb.infiniteScroll','false')")
                site=PreviewSite(previews,blocked=True)
                await page.route('**/*',site.route)
                await page.goto(f'https://metadata.test/#src={source}&sort={sort}&order={order}&batch=90')
                await page.wait_for_function("document.querySelectorAll('.novel-card').length===90")
                assert await page.locator('body').get_attribute('data-catalog-ready')=='false'
                assert await page.locator('#sortSelect').input_value()==sort
                shown=[]
                for number in (1,2,3):
                    if number>1:
                        await page.locator('#paginationBottom .page-input').fill(str(number))
                        await page.locator('#paginationBottom .page-input').press('Enter')
                    shown.extend(await page.locator('.novel-card').evaluate_all("cs=>cs.map(c=>c.dataset.source+':'+c.dataset.novelId)"))
                await page.evaluate('''window.lockedCards=[...document.querySelectorAll('.novel-card')];window.changes=0;
                    new MutationObserver(()=>changes++).observe(document.querySelector('#results'),{childList:true})''')
                site.release.set();await wait_loaded(page)
                assert await page.evaluate('changes===0&&lockedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])'),(source,sort,order)
                effective=await page.evaluate('([sort,source])=>NovelMetadata.resolveSourceSort(sort,source)',[sort,source])
                query=dict(sources=list(SOURCES) if source=='all' else [source],text='',author=None,status='all',audience='all',
                    **{'and':[],'or':[],'exclude':[]},focus=None,page=1,size=270,
                    sort=dict(sortBy=effective,order=order,audience='all',defaultSource=source))
                expected=await page.evaluate('q=>testCatalogQuery(q)',query)
                assert shown==[f"{n['source']}:{n['id']}" for n in expected['rows']],(source,sort,order)
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_bad_preview_falls_back_to_catalog_and_saved_filters_skip_preview(previews):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            for suffix,broken,count in [('',True,30),('#src=npia&q=original%200042',False,1),('#src=all&q=original%200042',False,8),('#src=npia&page=12',False,20)]:
                page = await browser.new_page()
                await page.route('**/*',PreviewSite(previews,broken_preview=broken).route)
                await page.goto('https://metadata.test/'+suffix)
                await wait_loaded(page)
                assert await page.locator('.novel-card').count()==count
                if count in (1,8):
                    assert await page.locator('#sourceSelect').input_value()==('all' if count==8 else 'npia')
                    assert set(await page.locator('.novel-card').evaluate_all('(cards)=>cards.map(c=>c.dataset.novelId)'))=={'42'}
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_combined_ranking_continues_by_likes_after_top_100_and_page_sizes_are_ordered(previews):
    expected=[]
    for source in SOURCES:
        manifest=json.loads((previews/f'{source}_chunk_manifest.json').read_text(encoding='utf-8'))
        for file in manifest['files']:
            for item in json.loads(gzip.decompress((previews/file).read_bytes()))['novels']:
                expected.append((source,item))
    expected.sort(key=lambda pair:((pair[1][13] if pair[0]=='npia' else 0) or 10**9,
        pair[1][6] is None,-pair[1][6] if pair[1][6] is not None else 0))
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page()
            site=PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/#src=all')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length===30")
            assert await page.locator('#batchSelect').input_value()=='30'
            assert await page.locator('#batchSelect option').evaluate_all('opts=>opts.map(o=>o.value)')==['30','60','90','120','250']
            # Page four at the default size crosses the actual rank boundary.
            await page.locator('#paginationBottom .page-input').fill('4')
            await page.locator('#paginationBottom .page-input').press('Enter')
            await page.wait_for_function("document.querySelector('#resultCount').textContent.includes('page 4 of')")
            ids=await page.locator('.novel-card').evaluate_all("cs=>cs.map(c=>c.dataset.source+':'+c.dataset.novelId)")
            assert ids==[f'{s}:{item[0]}' for s,item in expected[90:120]]
            assert ids[:10]==[f'npia:{i}' for i in range(91,101)]
            await page.select_option('#batchSelect','90')
            await page.locator('#paginationBottom .page-input').fill('2')
            await page.locator('#paginationBottom .page-input').press('Enter')
            await page.wait_for_function("document.querySelector('#resultCount').textContent.includes('page 2 of')")
            shown=await page.locator('.novel-card').evaluate_all("cs=>cs.map(c=>c.dataset.source+':'+c.dataset.novelId)")
            assert shown==[f'{s}:{item[0]}' for s,item in expected[90:180]]
            await page.evaluate('window.savedCards=[...document.querySelectorAll(".novel-card")]')
            site.release.set();await wait_loaded(page)
            assert await page.evaluate('savedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])')
            # Old bookmarks requesting 100 migrate to the replacement option.
            await page.goto('https://metadata.test/#src=all&batch=100&page=2')
            await wait_loaded(page)
            assert await page.locator('#batchSelect').input_value()=='90'
            assert await page.locator('.novel-card').evaluate_all("cs=>cs.map(c=>c.dataset.source+':'+c.dataset.novelId)")==shown
            await browser.close()
    asyncio.run(scenario())
