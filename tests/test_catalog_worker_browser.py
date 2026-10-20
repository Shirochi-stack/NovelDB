"""Real IndexedDB/worker tests for bounded queries and persistent imports."""
import asyncio
import gzip
import json
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from test_metadata_frontend_browser import DOCS, FixtureSite, row


BOOT = """() => {
 window.testWorker = new Worker('/catalog-worker.js');
 let sequence=0; const pending=new Map();
 testWorker.onmessage=({data})=>{if(data.progress)return;const p=pending.get(data.id);if(!p)return;pending.delete(data.id);data.error?p.reject(new Error(data.error.message)):p.resolve(data.result)};
 window.rpc=(type,value)=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});testWorker.postMessage({id,type,value})});
}"""


def test_worker_deep_pages_filters_literal_search_and_small_page_cache():
    novels = []
    for i in range(9007):
        item = row('npia', i + 1)
        item[1] = f'Original [hero] {i}' if i % 7 == 0 else f'Other {i}'
        item[2] = 'Shared author' if i % 2 == 0 else 'Other author'
        item[4] = ['하렘', '后宫', 'Harem', 'Fantasy'] if i % 2 == 0 else ['Fantasy', 'GL']
        item[5] = (i * 17) % 997
        item[8] = i % 2
        item[11] = 19 if i % 3 == 0 else 0
        novels.append(item)

    class Site(FixtureSite):
        async def route(self, route):
            if urlsplit(route.request.url).path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>',content_type='text/html')
            elif urlsplit(route.request.url).path == '/data/test_chunk.json.gz':
                await route.fulfill(body=gzip.compress(json.dumps({'novels': novels,
                    'translations': {'1': 'Translated special title'}}).encode()), content_type='application/gzip', headers={'etag': 'fixture-v1'})
            else:
                await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await page.route('**/*', Site().route)
            # Prevent the application from starting its own full import.
            await page.goto('http://metadata.test/')
            await page.evaluate(BOOT)
            info = await page.evaluate("rpc('import',{source:'npia',cfg:{format:'array',chunked:true,chunkFiles:['http://metadata.test/data/test_chunk.json.gz'],tagVersion:'v1'},tagLabels:{'하렘':'Harem','后宫':'Harem'}})")
            assert info['total'] == len(novels)
            harem = next(g for g in info['groups'] if g[0] == 'group:harem')
            assert harem[1] == 4504  # Three aliases count once for each work.
            options = dict(sources=['npia'], text='', author=None, status='all', audience='all',
                           **{'and': [], 'or': [], 'exclude': []},
                           sort={'sortBy': 'views', 'order': 'desc', 'defaultSource': 'npia'}, page=53, size=100)
            result = await page.evaluate("q=>rpc('query',q)", options)
            expected = sorted(enumerate(novels), key=lambda pair: (-pair[1][5], pair[0]))
            assert result['total'] == len(novels) and result['page'] == 53
            assert [n['id'] for n in result['rows']] == [str(n[0]) for _, n in expected[5200:5300]]
            second = await page.context.new_page()
            await second.route('**/*',Site().route)
            await second.goto('http://metadata.test/')
            await second.evaluate(BOOT)
            await second.evaluate("rpc('import',{source:'npia',cfg:{format:'array',chunked:true,chunkFiles:['http://metadata.test/data/test_chunk.json.gz'],tagVersion:'v1'},tagLabels:{'하렘':'Harem','后宫':'Harem'}})")
            left,right = await asyncio.gather(page.evaluate("q=>rpc('query',q)",{**options,'page':54}),
                                            second.evaluate("q=>rpc('query',q)",{**options,'page':55}))
            assert [n['id'] for n in left['rows']] == [str(n[0]) for _,n in expected[5300:5400]]
            assert [n['id'] for n in right['rows']] == [str(n[0]) for _,n in expected[5400:5500]]
            await second.close()
            focused = await page.evaluate("q=>rpc('query',q)", {**options,'page':1,'focus':result['rows'][0]})
            assert focused['page'] == 53 and focused['rows'][0]['id'] == result['rows'][0]['id']
            # Out-of-range pages clamp correctly, without retaining preceding pages.
            last = await page.evaluate("q=>rpc('query',q)", {**options, 'page': 99999})
            assert last['page'] == 91 and len(last['rows']) == 7
            literal = await page.evaluate("q=>rpc('query',q)", {**options, 'page': 1, 'text': '[hero]'})
            assert literal['total'] == 1287
            translated = await page.evaluate("q=>rpc('query',q)", {**options, 'page': 1, 'text': 'translated special'})
            assert translated['total'] == 1 and translated['rows'][0]['titleEn'] == 'Translated special title'
            filtered = await page.evaluate("q=>rpc('query',q)", {**options, 'page': 1, 'author': 'Shared author',
                'audience': 'adult', 'and': [['하렘', '后宫', 'Harem']], 'exclude': [['GL']]})
            assert filtered['total'] == len([i for i in range(9007) if i % 2 == 0 and i % 3 == 0])
            worker = next(w for w in page.workers if 'catalog-worker' in w.url)
            assert await worker.evaluate('pages.size') <= 3
            assert await worker.evaluate('(async()=> (await request((await db()).transaction("runs").objectStore("runs").count())))()') == 0
            await browser.close()
    asyncio.run(scenario())


def test_worker_cache_validator_refresh_and_failed_import_is_atomic():
    class Site(FixtureSite):
        etag = 'v1'
        payload = {'novels': [row('npia', 7), row('npia', 8)]}
        async def route(self, route):
            if urlsplit(route.request.url).path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>',content_type='text/html')
            elif urlsplit(route.request.url).path == '/data/test_chunk.json.gz':
                await route.fulfill(body=gzip.compress(json.dumps(self.payload).encode()), content_type='application/gzip', headers={'etag': self.etag})
            else:
                await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = Site()
            await page.route('**/*', site.route)
            await page.goto('http://metadata.test/')
            await page.evaluate(BOOT)
            command = "rpc('import',{source:'npia',cfg:{format:'array',chunked:true,chunkFiles:['http://metadata.test/data/test_chunk.json.gz']}})"
            assert (await page.evaluate(command))['total'] == 2
            site.payload = {'novels': [None]}
            # Identical validators reuse the committed index without parsing the body.
            assert (await page.evaluate(command))['total'] == 2
            site.etag = 'v2'
            site.payload = {'novels': [row('npia', 42)]}
            assert (await page.evaluate(command))['total'] == 1
            site.etag = 'v3'
            site.payload = {'novels': [row('npia', i + 1) for i in range(512)] + [None]}
            fallback = await page.evaluate(command)
            assert fallback['total'] == 1 and fallback['warnings']
            worker = next(w for w in page.workers if 'catalog-worker' in w.url)
            stored = await worker.evaluate("get('cards',['npia',0,0])")
            assert len(stored['rows']) == 1 and stored['rows'][0][0] == 42
            await browser.close()
    asyncio.run(scenario())


def test_blocked_storage_keeps_top_cards_and_explains_recovery():
    class BlockedSite(FixtureSite):
        async def route(self, route):
            if urlsplit(route.request.url).path == '/catalog-worker.js':
                prefix = "Object.defineProperty(self,'indexedDB',{value:{open(){throw new DOMException('Site storage blocked','SecurityError')}}});\n"
                await route.fulfill(body=prefix+(DOCS/'catalog-worker.js').read_text(encoding='utf-8'),content_type='text/javascript')
            else:
                await super().route(route)
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*',BlockedSite().route)
            await page.goto('http://metadata.test/')
            await page.get_by_text("Full catalog couldn't load. Check site storage/network access and reload.",exact=True).wait_for()
            assert await page.locator('.novel-card').count() == 1
            assert await page.locator('#loadFullCatalog').count() == 0
            await browser.close()
    asyncio.run(scenario())


def test_four_downloads_overlap_with_bounded_prefetch_and_sequential_imports():
    class Site(FixtureSite):
        def __init__(self):
            super().__init__()
            self.release_first = asyncio.Event()
            self.first_four = asyncio.Event()
            self.started = []
            self.worker = None
            self.fifth_saw_committed_first = False

        async def route(self, route):
            path = urlsplit(route.request.url).path
            if path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>', content_type='text/html')
            elif path.startswith('/data/overlap_'):
                chunk = int(path.split('_')[-1].split('.')[0])
                self.started.append(chunk)
                if len(self.started) == 4:
                    self.first_four.set()
                if chunk == 0:
                    await self.release_first.wait()
                if chunk == 4:
                    saved = await self.worker.evaluate("get('cards',['npia',0,0])")
                    self.fifth_saw_committed_first = saved['rows'][0][0] == 1
                # A failed prefetched download must preserve other chunks.
                if chunk == 2:
                    await route.abort('failed')
                else:
                    await route.fulfill(body=gzip.compress(json.dumps({'novels': [row('npia', chunk + 1)]}).encode()),
                                        content_type='application/gzip')
            else:
                await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = Site()
            await page.route('**/*', site.route)
            await page.goto('http://metadata.test/')
            async with page.expect_worker() as worker_event:
                await page.evaluate(BOOT)
            site.worker = await worker_event.value
            pending = asyncio.create_task(page.evaluate("""rpc('import',{source:'npia',cfg:{format:'array',chunked:true,
                chunkFiles:[0,1,2,3,4,5].map(i=>`http://metadata.test/data/overlap_${i}.json.gz`)}})"""))
            await asyncio.wait_for(site.first_four.wait(), 5)
            assert sorted(site.started) == [0, 1, 2, 3]
            # Hold the first response: no unbounded downloading or early parsing.
            await asyncio.sleep(.1)
            assert sorted(site.started) == [0, 1, 2, 3]
            for chunk in range(4):
                assert await site.worker.evaluate("chunk=>get('meta',['npia',chunk])", chunk) is None
            site.release_first.set()
            result = await pending
            assert result['total'] == 5 and len(result['warnings']) == 1
            assert site.fifth_saw_committed_first
            assert sorted(site.started) == [0, 1, 2, 3, 4, 5]
            assert await site.worker.evaluate("get('meta',['npia',2])") is None
            assert (await site.worker.evaluate("get('cards',['npia',5,0])"))['rows'][0][0] == 6
            await browser.close()
    asyncio.run(scenario())


def test_storage_failure_aborts_the_prefetched_download():
    class Site(FixtureSite):
        async def route(self, route):
            path = urlsplit(route.request.url).path
            if path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>', content_type='text/html')
            elif path == '/catalog-worker.js':
                prefix = r"""
                    self.prefetchState={started:[],aborted:[]};
                    const originalFetch=self.fetch.bind(self);
                    self.fetch=(url,options)=>{
                        if(/cancel_[123]\.json$/.test(String(url))) {
                            prefetchState.started.push(String(url));
                            return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{
                                prefetchState.aborted.push(String(url));reject(new DOMException('Cancelled','AbortError'));
                            },{once:true}));
                        }
                        if(String(url).endsWith('cancel_0.json')) return new Promise(resolve=>setTimeout(()=>
                            resolve(new Response(JSON.stringify({novels:[[1]]}))),30));
                        return originalFetch(url,options);
                    };
                """
                suffix = "\nself.NovelMetadata.parseRows=()=>{throw new DOMException('Disk full','QuotaExceededError')};"
                await route.fulfill(body=prefix+(DOCS/'catalog-worker.js').read_text(encoding='utf-8')+suffix,
                                    content_type='text/javascript')
            else:
                await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', Site().route)
            await page.goto('http://metadata.test/')
            async with page.expect_worker() as worker_event:
                await page.evaluate(BOOT)
            worker = await worker_event.value
            error = await page.evaluate("""rpc('import',{source:'npia',cfg:{format:'array',chunked:true,
                chunkFiles:[0,1,2,3].map(i=>`http://metadata.test/data/cancel_${i}.json`)}}).then(()=>null,e=>e.message)""")
            assert error == 'Disk full'
            state = await worker.evaluate('prefetchState')
            expected = [f'http://metadata.test/data/cancel_{i}.json' for i in range(1, 4)]
            assert sorted(state['started']) == sorted(state['aborted']) == expected
            assert await worker.evaluate("get('meta',['npia',0])") is None
            await browser.close()
    asyncio.run(scenario())
