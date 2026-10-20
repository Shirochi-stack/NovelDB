"""Real IndexedDB/worker tests for bounded queries and persistent imports."""
import asyncio
import gzip
import hashlib
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
            worker = next(w for w in page.workers if 'catalog-worker' in w.url)
            shape=await worker.evaluate("""async()=>{
                const card=await rawGet('cards',['npia',0,0,'compact']);
                const payload=await rawGet('cards',['npia',0,'payload','compact']);
                const index=await get('index',['npia',0,0,'compact']);
                return {ids:card.ids,expanded:!!card.rows,bytes:payload.compressed.byteLength,packed:Array.isArray(index.entries[0][5])};
            }""")
            assert shape['ids']==['7','8'] and not shape['expanded'] and shape['bytes']>0 and shape['packed']
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
            stored = await worker.evaluate("get('cards',['npia',0,0,'compact'])")
            assert len(stored['rows']) == 1 and stored['rows'][0][0] == 42
            # Partial results are usable, but activation must retry the failed
            # refresh instead of memoizing its warning for the whole session.
            site.etag = 'v4'
            site.payload = {'novels': [row('npia', 43)]}
            recovered = await page.evaluate(command.replace("rpc('import'", "rpc('activate'"))
            assert recovered['total'] == 1 and not recovered['warnings']
            stored = await worker.evaluate("get('cards',['npia',0,0,'compact'])")
            assert stored['rows'][0][0] == 43
            await browser.close()
    asyncio.run(scenario())


def test_legacy_disk_blocks_and_checkpoint_resume_need_no_catalog_download():
    class Site(FixtureSite):
        downloads=0
        etag='unchanged'
        novels=[row('npia',7),row('npia',8)]
        async def route(self,route):
            path=urlsplit(route.request.url).path
            if path=='/':
                await route.fulfill(body='<html><body>Worker compatibility</body></html>',content_type='text/html')
            elif path=='/data/old.json.gz':
                self.downloads+=1
                await route.fulfill(body=gzip.compress(json.dumps({'novels':self.novels}).encode()),content_type='application/gzip',headers={'etag':self.etag})
            else:await super().route(route)
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page();site=Site()
            await page.route('**/*',site.route);await page.goto('https://metadata.test/');await page.evaluate(BOOT)
            cfg={'format':'array','chunked':True,'chunkFiles':['https://metadata.test/data/old.json.gz'],'tagVersion':'tags'}
            await page.evaluate("cfg=>rpc('activate',{source:'npia',cfg,tagLabels:{Fantasy:'Fantasy'}})",cfg)
            worker=next(w for w in page.workers if 'catalog-worker' in w.url)
            await worker.evaluate("""async()=>{
                const key=['npia',0,0],compact=[...key,'compact'];
                const index=await get('index',compact),cards=await get('cards',compact),meta=await get('meta',['npia',0,'compact']);
                for(const entry of index.entries) {
                    const value=unpack(entry,index,'npia');
                    for(const field of ['id','title','titleEn','author','tags','source']) delete value[field];
                    entry[5]=value;
                }
                const tx=(await db()).transaction(['index','cards','meta'],'readwrite'),done=transaction(tx);
                tx.objectStore('index').put(index,key);tx.objectStore('cards').put(cards,key);
                tx.objectStore('index').delete(compact);tx.objectStore('cards').delete(compact);
                tx.objectStore('cards').delete(['npia',0,'payload','compact']);
                tx.objectStore('meta').put({...meta,keys:[key]},['npia',0]);tx.objectStore('meta').delete(['npia',0,'compact']);
                await done;blocks.set('npia',[key]);
            }""")
            await page.evaluate("async()=>{window.savedRegistry=await rpc('checkpoint',null);testWorker.terminate()}")
            await page.evaluate(BOOT)
            await page.evaluate("rpc('resume',savedRegistry)")
            reused=await page.evaluate("cfg=>rpc('activate',{source:'npia',cfg})",cfg)
            assert reused['reused'] and site.downloads==1
            query=dict(sources=['npia'],text='',status='all',audience='all',page=1,size=30,
                       sort=dict(sortBy='views',order='desc',defaultSource='npia'))
            result=await page.evaluate("q=>rpc('query',q)",query)
            assert result['total']==2 and {n['id'] for n in result['rows']}=={'7','8'}
            assert site.downloads==1
            # A fresh compact import must leave an older tab's blocks intact.
            site.etag='updated';site.novels=[row('npia',42)]
            await page.evaluate("cfg=>rpc('import',{source:'npia',cfg})",cfg)
            worker=next(w for w in page.workers if 'catalog-worker' in w.url)
            assert await worker.evaluate("(async()=> (await get('cards',['npia',0,0])).rows.map(r=>r[0]))()") == [7,8]
            assert await worker.evaluate("(async()=> (await get('cards',['npia',0,0,'compact'])).rows.map(r=>r[0]))()") == [42]
            result=await page.evaluate("q=>rpc('query',q)",query)
            assert result['total']==1 and result['rows'][0]['id']=='42'
            await browser.close()
    asyncio.run(scenario())


def test_blocked_storage_explains_recovery_without_ranking_preview():
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
            assert await page.locator('.novel-card').count() == 0
            assert await page.locator('#loadFullCatalog').count() == 0
            await browser.close()
    asyncio.run(scenario())


def test_eight_downloads_and_four_parsers_are_bounded_and_cleaned_up():
    class Site(FixtureSite):
        def __init__(self):
            super().__init__()
            self.release_downloads = asyncio.Event()
            self.first_eight = asyncio.Event()
            self.release_parsers = asyncio.Event()
            self.first_four_parsers = asyncio.Event()
            self.started = []
            self.parsing = []
            self.worker = None
            self.ninth_saw_committed_first = False

        async def route(self, route):
            path = urlsplit(route.request.url).path
            if path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>', content_type='text/html')
            elif path == '/catalog-parser.js':
                suffix = """\nconst actualImport=importChunk;importChunk=async(...args)=>{
                    await fetch('/parser-gate?chunk='+args[1].chunk);return actualImport(...args)};"""
                await route.fulfill(body=(DOCS/'catalog-parser.js').read_text(encoding='utf-8')+suffix,content_type='text/javascript')
            elif path == '/parser-gate':
                self.parsing.append(route.request.url)
                if len(self.parsing) == 4:
                    self.first_four_parsers.set()
                await self.release_parsers.wait()
                await route.fulfill(body='ok')
            elif path.startswith('/data/overlap_'):
                chunk = int(path.split('_')[-1].split('.')[0])
                self.started.append(chunk)
                if len(self.started) == 8:
                    self.first_eight.set()
                if chunk < 8:
                    await self.release_downloads.wait()
                if chunk == 8:
                    saved = await self.worker.evaluate("get('cards',['npia',0,0,'compact'])")
                    self.ninth_saw_committed_first = saved['rows'][0][0] == 1
                # A failed prefetched download must preserve other chunks.
                if chunk == 2:
                    await route.abort('failed')
                else:
                    item = row('npia',chunk + 1)
                    item[4] = ['판타지']
                    await route.fulfill(body=gzip.compress(json.dumps({'novels': [item]}).encode()),
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
            pending = asyncio.create_task(page.evaluate("""rpc('import',{source:'npia',tagLabels:{'판타지':'Fantasy'},cfg:{format:'array',chunked:true,tagVersion:'v1',
                chunkFiles:Array.from({length:10},(_,i)=>`http://metadata.test/data/overlap_${i}.json.gz`)}})"""))
            await asyncio.wait_for(site.first_eight.wait(), 5)
            assert sorted(site.started) == list(range(8))
            # Hold the first response: no unbounded downloading or early parsing.
            await asyncio.sleep(.1)
            assert sorted(site.started) == list(range(8))
            for chunk in range(8):
                assert await site.worker.evaluate("chunk=>get('meta',['npia',chunk])", chunk) is None
            site.release_downloads.set()
            await asyncio.wait_for(site.first_four_parsers.wait(),5)
            await asyncio.sleep(.1)
            assert len(site.parsing) == 4
            assert sorted(set(site.started)) == list(range(8))
            assert site.started.count(2) == 2  # One bounded network retry.
            cdp = await browser.new_browser_cdp_session()
            targets = (await cdp.send('Target.getTargets'))['targetInfos']
            assert len([t for t in targets if t['type']=='worker' and '/catalog-parser.js' in t['url']]) == 4
            site.release_parsers.set()
            result = await pending
            assert result['total'] == 9 and len(result['warnings']) == 1
            assert result['groups'] == [['label:fantasy',9,[['판타지',9]]]]
            assert site.ninth_saw_committed_first
            assert sorted(set(site.started)) == list(range(10))
            assert len(site.started) == 11
            assert len(site.parsing) == 9
            assert await site.worker.evaluate("get('meta',['npia',2])") is None
            assert (await site.worker.evaluate("get('cards',['npia',9,0,'compact'])"))['rows'][0][0] == 10
            output = await page.evaluate("rpc('query',{sources:['npia'],sort:{sortBy:'views'},page:1,size:100})")
            assert [item['id'] for item in output['rows']] == [str(i+1) for i in range(10) if i != 2]
            await asyncio.sleep(.05)
            targets = (await cdp.send('Target.getTargets'))['targetInfos']
            assert not [t for t in targets if t['type']=='worker' and '/catalog-parser.js' in t['url']]
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
                        if(/cancel_[1-7]\.json$/.test(String(url))) {
                            prefetchState.started.push(String(url));
                            return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{
                                prefetchState.aborted.push(String(url));reject(new DOMException('Cancelled','AbortError'));
                            },{once:true}));
                        }
                        if(String(url).endsWith('cancel_0.json')) return new Promise(resolve=>setTimeout(()=>
                            resolve(new Response(JSON.stringify({novels:[[1,'Title']]}))),30));
                        return originalFetch(url,options);
                    };
                """
                await route.fulfill(body=prefix+(DOCS/'catalog-worker.js').read_text(encoding='utf-8'),
                                    content_type='text/javascript')
            elif path == '/catalog-parser.js':
                suffix = "\nIDBObjectStore.prototype.put=function(){throw new DOMException('Disk full','QuotaExceededError')};"
                await route.fulfill(body=(DOCS/'catalog-parser.js').read_text(encoding='utf-8')+suffix,content_type='text/javascript')
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
                chunkFiles:Array.from({length:8},(_,i)=>`http://metadata.test/data/cancel_${i}.json`)}}).then(()=>null,e=>e.message)""")
            assert error == 'Disk full'
            state = await worker.evaluate('prefetchState')
            expected = [f'http://metadata.test/data/cancel_{i}.json' for i in range(1, 8)]
            assert sorted(state['started']) == sorted(state['aborted']) == expected
            assert await worker.evaluate("get('meta',['npia',0])") is None
            await browser.close()
    asyncio.run(scenario())


def test_manifest_hashes_restore_pages_without_downloads_or_scans_and_invalidate_changes():
    class Site(FixtureSite):
        def __init__(self):
            super().__init__()
            self.title = 'First title'
            self.downloads = 0

        def payload(self):
            item = row('npia', 42)
            item[1] = self.title
            return gzip.compress(json.dumps({'novels': [item]}).encode(),mtime=0)

        def config(self):
            return {'format':'array','chunked':True,'chunkFiles':['https://metadata.test/data/cache.json.gz'],
                    'chunkHashes':[hashlib.sha256(self.payload()).hexdigest()]}

        async def route(self, route):
            path = urlsplit(route.request.url).path
            if path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>',content_type='text/html')
            elif path == '/data/cache.json.gz':
                self.downloads += 1
                await route.fulfill(body=self.payload(),content_type='application/gzip')
            else:
                await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = Site()
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            async with page.expect_worker() as worker_event:
                await page.evaluate(BOOT)
            worker = await worker_event.value
            options = {'sources':['npia'],'text':'','status':'all','audience':'all',
                       'sort':{'sortBy':'views','order':'desc'},'page':1,'size':100}
            async def load(cfg):
                return await page.evaluate("cfg=>rpc('import',{source:'npia',cfg})",cfg)
            await load(site.config())
            first = await page.evaluate("q=>rpc('query',q)",options)
            assert first['rows'][0]['title'] == 'First title'
            assert site.downloads == 1
            await page.evaluate('testWorker.terminate()')
            async with page.expect_worker() as worker_event:
                await page.evaluate(BOOT)
            worker = await worker_event.value
            await worker.evaluate("() => {self.indexReads=0;const originalGet=get;get=(store,key)=>{if(store==='index')indexReads++;return originalGet(store,key)}}")
            await load(site.config())
            assert site.downloads == 1
            assert await page.evaluate("q=>rpc('query',q)",options) == first
            assert await worker.evaluate('indexReads') == 0
            # A changed filter cannot borrow a cached unfiltered result.
            empty = await page.evaluate("q=>rpc('query',q)",{**options,'text':'not present'})
            assert empty['total'] == 0 and await worker.evaluate('indexReads') > 0
            site.title = 'Changed title'
            await load(site.config())
            changed = await page.evaluate("q=>rpc('query',q)",options)
            assert changed['rows'][0]['title'] == 'Changed title' and site.downloads == 2
            # A mixed deployment must retain the last committed chunk and page.
            fallback = await load({**site.config(),'chunkHashes':['f'*64]})
            assert fallback['total'] == 1 and fallback['warnings']
            assert (await page.evaluate("q=>rpc('query',q)",options)) == changed
            # Changing parser settings invalidates the persistent page, too.
            before = await worker.evaluate('indexReads')
            await load({**site.config(),'coverPrefix':'https://covers.test/'})
            await page.evaluate("q=>rpc('query',q)",options)
            assert await worker.evaluate('indexReads') > before
            assert len(await worker.evaluate('get("meta",["saved-pages"])')) <= 3
            await worker.evaluate("""() => {const originalPut=IDBObjectStore.prototype.put;
                IDBObjectStore.prototype.put=function(value,key){
                    if(Array.isArray(key)&&key[0]==='saved-pages')throw new DOMException('Full','QuotaExceededError');
                    return originalPut.call(this,value,key);
                }}""")
            # Optional page-cache writes must not stop an otherwise usable query.
            result = await page.evaluate("q=>rpc('query',q)",{**options,'text':'Changed'})
            assert result['total'] == 1
            await worker.evaluate("() => {Object.defineProperty(crypto,'subtle',{value:undefined})}")
            # HTTP previews without checksums or validators must never reuse
            # result pages after a newly downloaded body changes.
            for title in ['Unvalidated title one','Unvalidated title two']:
                site.title = title
                assert (await load(site.config()))['total'] == 1
                assert (await page.evaluate("q=>rpc('query',q)",options))['rows'][0]['title'] == title
            await browser.close()
    asyncio.run(scenario())


def test_combined_rankings_include_all_sources_by_likes_after_npia_ranks():
    novels = []
    for i in range(180):
        item = row('npia',i+1)
        item[1] = f'Title {179-i:03d}'
        item[13] = i//10+1 if i % 10 == 0 else 0
        novels.append(item)
    other = [row('nweb',i+1,known=True) for i in range(30)]
    for i,item in enumerate(other): item[6] = 1000-i
    class Site(FixtureSite):
        async def route(self, route):
            path = urlsplit(route.request.url).path
            if path == '/':
                await route.fulfill(body='<html><body>Worker test</body></html>',content_type='text/html')
            elif path.startswith('/data/rank_'):
                await route.fulfill(body=gzip.compress(json.dumps({'novels':novels if 'npia' in path else other}).encode()),content_type='application/gzip')
            else:
                await super().route(route)
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*',Site().route)
            await page.goto('http://metadata.test/')
            async with page.expect_worker() as event:
                await page.evaluate(BOOT)
            worker = await event.value
            for source,fmt in [('nweb','metadata-v1'),('npia','array')]:
                await page.evaluate("v=>rpc('import',v)",{'source':source,'cfg':{'format':fmt,'dataUrl':f'http://metadata.test/data/rank_{source}.json.gz'}})
            await worker.evaluate("() => {self.readSources=[];const originalGet=get;get=(store,key)=>{if(store==='index')readSources.push(key[0]);return originalGet(store,key)}}")
            options = {'sources':['nweb','npia'],'text':'','status':'all','audience':'all',
                'sort':{'sortBy':'daily','order':'desc','defaultSource':'all'},'page':1,'size':100}
            result = await page.evaluate("q=>rpc('query',q)",options)
            combined = [('nweb',i,item) for i,item in enumerate(other)]+[('npia',30+i,item) for i,item in enumerate(novels)]
            expected = sorted(combined,key=lambda entry:((entry[2][13] if entry[0]=='npia' else 0) or 10**9,-entry[2][6],entry[1]))[:100]
            assert result['total'] == 210
            assert [(item['source'],item['id'],item['_catalogOrdinal']) for item in result['rows']] == [(source,str(item[0]),i) for source,i,item in expected]
            assert set(await worker.evaluate('readSources')) == {'npia','nweb'}
            all_rows = await page.evaluate("q=>rpc('query',q)",{**options,'size':250})
            assert len(all_rows['rows']) == 210 and all_rows['rows'][-1]['source'] == 'npia'
            assert 'nweb' in await worker.evaluate('readSources')
            await worker.evaluate('readSources.length=0')
            filtered = await page.evaluate("q=>rpc('query',q)",{**options,'text':'nweb'})
            assert filtered['total'] == 30 and set(await worker.evaluate('readSources')) == {'nweb','npia'}
            await browser.close()
    asyncio.run(scenario())
