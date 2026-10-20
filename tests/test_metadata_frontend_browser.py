"""Real browser checks; every request is fulfilled by local in-memory fixtures."""
import asyncio
import gzip
import hashlib
import json
import io
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from PIL import Image


DOCS = Path(__file__).resolve().parents[1] / "docs"
SOURCES = ("npia", "kpage", "sfc", "nweb", "jara", "mpia", "rbooks", "nseries")


def test_period_sort_uses_source_boards_and_all_sources_prioritizes_npia(monkeypatch):
    boards = {
        "nweb": {"daily": "best_102_daily_free", "weekly": "best_102_weekly_free"},
        "jara": {"daily": "all_today", "weekly": "all_weekly", "monthly": "all_monthly"},
        "rbooks": {"weekly": "1650_weekly", "monthly": "1650_monthly"},
        "mpia": {"daily": "TODAY_BEST"},
    }
    original_row = row
    def ranked_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source in boards:
            result[5] = result[6] = 999999 if ident == 9 else 0
            result[15] = {key: (1 if ident == (8 if period == "daily" else 7) else 9)
                          for period, key in boards[source].items()} if ident != 9 else {}
        return result
    monkeypatch.setitem(globals(), "row", ranked_row)
    class RankingSite(FixtureSite):
        async def route(self, request):
            name = urlsplit(request.request.url).path.rsplit("/", 1)[-1]
            source = name.split("_", 1)[0]
            if name.endswith("_chunk_manifest.json") and source in boards:
                await request.fulfill(json={"format": "metadata-v1", "files": [f"{source}_chunk_0.json.gz"],
                    "chunks": 1, "totalEntries": 3, "descriptionShardCount": 128,
                    "descriptionShardPrefix": f"{source}_descriptions_shard_", "topUrl": None,
                    "boards": {key: {"label": f"{source} {period} native board", "stale": False}
                               for period, key in boards[source].items()}, "coverage": {"complete": True}})
                return
            await super().route(request)
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route("**/*", RankingSite().route)
            for source in boards:
                await page.goto(f"http://metadata.test/#src={source}")
                await wait_loaded(page)
                period = "weekly" if source == "rbooks" else "daily"
                assert await page.locator("#sortSelect").input_value() == "daily"
                first = page.locator(".novel-card").first
                ident = 8 if period == "daily" else 7
                assert await first.locator(".card-title").inner_text() == f"{source} translated {ident}"
                assert await first.locator(".badge-rank").inner_text() == "#1"
                assert f"{source} {period} native board" in await first.locator(".badge-rank").get_attribute("title")
                if source in ("nweb", "mpia"):
                    assert await page.locator('#sortSelect option[value="monthly"]').is_enabled()
                    await page.locator('#sortSelect').select_option('monthly')
                    expected_period = 'weekly' if source == 'nweb' else 'daily'
                    await page.wait_for_function('(period)=>document.querySelector(".badge-rank")?.title.includes(period)', arg=expected_period)
                    assert await page.locator('#sortSelect').input_value() == 'monthly'
                    assert 'uses' in await page.locator('#sortSelect option[value="monthly"]').inner_text()
                if source == "jara":
                    await page.locator("#sortSelect").select_option("monthly")
                    assert await page.locator(".novel-card").first.locator(".card-title").inner_text() == "jara translated 7"
            await page.locator("#sourceSelect").select_option("all")
            await wait_loaded(page)
            await page.locator("#sortSelect").select_option("daily")
            assert await page.locator(".novel-card").first.locator(".card-title").inner_text() == "npia translated 7"
            await browser.close()
    asyncio.run(scenario())


def test_source_switch_keeps_requested_sort_and_explains_fallback(monkeypatch):
    original_row = row
    def rated_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source == 'nseries':
            result[14]['rating'] = {7:4,8:6,9:9}[ident]
        return result
    monkeypatch.setitem(globals(), 'row', rated_row)
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite().route)
            await page.goto('https://metadata.test/#sort=monthly')
            await wait_loaded(page)
            for source, fallback in [('rbooks','Monthly Rank'),('nweb','Weekly Rank'),('mpia','Daily Rank'),
                                     ('sfc','SFC: Popularity'),('kpage','Views'),('nseries','Rating'),('npia','Monthly Rank'),('all','Monthly Rank')]:
                await page.select_option('#sourceSelect', source)
                await wait_loaded(page)
                assert await page.locator('#sortSelect').input_value() == 'monthly'
                assert await page.locator('#sortSelect option[value="monthly"]').is_enabled()
                assert 'unavailable' not in await page.locator('#sortSelect').inner_text()
                if source not in ('rbooks','npia','all'):
                    assert fallback in await page.locator('#sortSelect').get_attribute('title')
                if source == 'nseries':
                    assert await page.locator('.novel-card').first.get_attribute('data-novel-id') == '9'
                assert 'sort=monthly' in page.url
            await page.select_option('#sourceSelect','sfc')
            await wait_loaded(page)
            await page.select_option('#sortSelect','sfc_popularity')
            await page.select_option('#sourceSelect','npia')
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'sfc_popularity'
            assert await page.locator('#sortSelect option[value="sfc_popularity"]').evaluate('option=>!option.hidden')
            await page.select_option('#sourceSelect','nweb')
            await wait_loaded(page)
            await page.select_option('#sortSelect','rank:nweb:native')
            await page.select_option('#sourceSelect','nseries')
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'rank:nweb:native'
            assert await page.locator('.novel-card').first.get_attribute('data-novel-id') == '9'
            await page.reload()
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'rank:nweb:native'
            await page.select_option('#sourceSelect','nweb')
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'rank:nweb:native'
            await browser.close()
    asyncio.run(scenario())


def test_sort_changes_update_url_refresh_and_browser_history():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite(delay_nweb=True).route)
            await page.goto('http://metadata.test/#sort=updated&src=npia')
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'updated'
            await page.locator('#sortSelect').select_option('daily')
            assert 'sort=' not in page.url and 'src=' not in page.url
            await page.reload()
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'daily'
            await page.locator('#sortSelect').select_option('weekly')
            assert 'sort=weekly' in page.url
            await page.go_back()
            await page.wait_for_function('document.querySelector("#sortSelect").value === "daily"')
            await page.go_forward()
            await page.wait_for_function('document.querySelector("#sortSelect").value === "weekly"')
            await page.locator('#sourceSelect').select_option('nweb')
            await page.locator('#sortSelect').select_option('updated')
            await wait_loaded(page)
            assert 'sort=updated' in page.url
            await page.locator('#sortSelect').select_option('daily')
            assert 'sort=updated' not in page.url
            await browser.close()
    asyncio.run(scenario())


def test_jara_and_rbooks_use_shared_tags_in_cards_and_filters(monkeypatch):
    original_row = row
    def tagged_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source in ('jara', 'rbooks'):
            result[4] = ['검증전용태그']
        return result
    monkeypatch.setitem(globals(), 'row', tagged_row)
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            fixture = FixtureSite()
            async def route(request):
                if urlsplit(request.request.url).path.endswith('/tags_en.txt.gz'):
                    await request.fulfill(body=gzip.compress('검증전용태그|||Shared translated tag\n'.encode()), content_type='application/gzip')
                else:
                    await fixture.route(request)
            await page.route('**/*', route)
            for source in ('jara', 'rbooks'):
                await page.goto(f'http://metadata.test/#src={source}')
                await wait_loaded(page)
                chip = page.locator('.card-tag', has_text='Shared translated tag').first
                await chip.click()
                assert 'Shared translated tag' in await page.locator('#activeTagsSummary').inner_text()
                assert await page.locator('.novel-card').count() > 0
            await browser.close()
    asyncio.run(scenario())


def row(source, ident=7, known=False, completed=False):
    if source == "npia":
        return [ident, "Npia original", "Author", "", [], 100, 8, 3, 0, "2026-01-01", 1, 0, 1, 1, 0, 0, 0, 1, 1, 1]
    if source == "kpage":
        return [ident, "Kpage original", "Author", "", [], 100, 0, 0, 0, "2026-01-01", 0, 0]
    if source == "sfc":
        return [ident, "SFC original", "Author", "", [], 100, 7, 9000, 0, "2026-01-01", 19, 1, 0, 0, 0, 0, 0, "", "Chapter", 9, "2026-01-01"]
    url = {"nweb": f"https://novel.\u006e\u0061\u0076\u0065\u0072.com/best/list?novelId={ident}",
           "jara": f"https://www.\u006a\u006f\u0061\u0072\u0061.com/book/{ident}",
           "mpia": f"https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com/novel/detail/{ident}", "rbooks": f"https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com/books/{ident}", "nseries": f"https://series.\u006e\u0061\u0076\u0065\u0072.com/novel/detail.series?productNo={ident}"}[source]
    return [str(ident), f"{source} original {ident}", "작가", "", ["판타지"],
            100 if known else None, None, 5 if known else None,
            int(completed) if known else None, "2026-09-13" if known else None,
            0 if known else None, url, "public", None,
            {"favorites": 17, "recommendations": 23} if known else {}, {"native": ident} if known else {}]


class FixtureSite:
    def __init__(self, *, missing=(), broken=(), delay_nweb=False, many_npia=False, covers=False, coverage=None, coverage_by_source=None):
        self.coverage = coverage
        self.coverage_by_source = coverage_by_source or {}
        self.covers = covers
        self.missing = set(missing)
        self.broken = set(broken)
        self.delay_nweb = delay_nweb
        self.many_npia = many_npia
        self.requests = []

    async def route(self, route):
        url = urlsplit(route.request.url)
        path = url.path
        self.requests.append(route.request.url)
        if url.hostname == "covers.test":
            await route.fulfill(status=200, body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="150"><rect width="100" height="150" fill="purple"/></svg>', content_type="image/svg+xml")
            return
        if url.hostname != "metadata.test":
            await route.fulfill(status=200, body="", content_type="text/plain")
            return
        if path in ("/", "/index.html", "/app.js", "/metadata-core.js", "/catalog-worker.js", "/catalog-import.js", "/catalog-parser.js", "/synopsis-worker.js", "/preview-worker.js", "/source-picker.js", "/cover-loader.js", "/cover-save.js", "/site-tools.js", "/cache-worker.js", "/style.css"):
            name = "index.html" if path == "/" else path.lstrip("/")
            mime = "text/html" if name.endswith("html") else "text/css" if name.endswith("css") else "text/javascript"
            await route.fulfill(status=200, body=(DOCS / name).read_bytes(), content_type=mime)
            return
        if path == '/halgakos.ico' or path.startswith('/icons/'):
            name = path.lstrip('/')
            await route.fulfill(body=(DOCS/name).read_bytes(),content_type='image/png' if name.endswith('.png') else 'image/x-icon')
            return
        name = path.rsplit("/", 1)[-1]
        if name == "tags_en.txt.gz":
            await route.fulfill(body=gzip.compress("판타지|||Fantasy\n".encode()), content_type="application/gzip")
            return
        if name == "tags_extra.json.gz":
            await route.fulfill(body=gzip.compress(json.dumps({"판타지": "Wrong overwrite", "새|||태그": "New tag"}).encode()), content_type="application/gzip")
            return
        source = next((source for source in SOURCES if name.startswith(source + "_")), "npia")
        if name.endswith("_chunk_manifest.json"):
            if source in self.missing:
                await route.fulfill(status=404, body="Not published")
                return
            if source in ('npia', 'kpage', 'sfc'):
                await route.fulfill(json={'files': [f'{source}_chunk_0.json.gz'], 'chunks': 1,
                    'totalEntries': 61 if self.many_npia and source == 'npia' else 1, 'embedded': True})
                return
            manifest = {"format": "metadata-v1", "files": [f"{source}_chunk_0.json.gz"],
                        "chunks": 1, "totalEntries": 3, "descriptionShardCount": 128,
                        "descriptionShardPrefix": f"{source}_descriptions_shard_", "topUrl": None,
                        "boards": {"native": {"label": "Native board", "observed_at": "2026-09-13T00:00:00Z", "stale": False}},
                        "coverage": self.coverage_by_source.get(source, self.coverage or {"complete": True})}
            await route.fulfill(json=manifest)
            return
        if "_descriptions_shard_" in name or name.startswith("descriptions_shard_"):
            if self.delay_nweb and source == "nweb":
                await asyncio.sleep(0.3)
            payload = {str(ident): f"{source} synopsis {ident} literal " + chr(92) + "n" + chr(10) + "second line"
                       for ident in (7, 8, 9)}
        elif "_top.json.gz" in name:
            payload = {"novels": [row(source)], "translations": {"7": f"{source} translated"}, "descriptions": {"7": f"{source} synopsis 7"}}
        elif "_chunk_" in name:
            if source in self.broken:
                await route.fulfill(status=503, body="Fixture failure")
                return
            if self.delay_nweb and source == "nweb":
                await asyncio.sleep(0.4)
            if source in ("nweb", "jara", "mpia", "rbooks", "nseries"):
                novels = [row(source), row(source, 8, True, True), row(source, 9, True, False)]
            else:
                novels = [row(source)] if name.endswith("_0.json.gz") else []
                if self.many_npia and source == "npia" and novels:
                    novels = [row(source, ident) for ident in range(7, 68)]
            payload = {"novels": novels, "translations": {str(entry[0]): f"{source} translated {entry[0]}" for entry in novels}}
        else:
            await route.fulfill(status=404, body="Unknown fixture")
            return
        if self.covers and isinstance(payload, dict) and "novels" in payload:
            for entry in payload["novels"]:
                entry[3] = f"https://covers.test/{source}/{entry[0]}.svg"
        await route.fulfill(body=gzip.compress(json.dumps(payload, ensure_ascii=False).encode()), content_type="application/gzip")


async def wait_loaded(page):
    await page.wait_for_function("document.body.dataset.catalogReady==='true' && !document.querySelector('#resultCount').textContent.includes('loading') && !/^(Loading|Searching)/.test(document.querySelector('#resultCount').textContent)")


def test_startup_loads_only_the_complete_npia_catalog_once():
    class SlowSite(FixtureSite):
        def __init__(self):
            super().__init__(many_npia=True)
            self.started = asyncio.Event()
            self.release = asyncio.Event()
        async def route(self, route):
            path = urlsplit(route.request.url).path
            if '_chunk_' in path and not '_manifest' in path:
                self.started.set()
                await self.release.wait()
            await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            site = SlowSite()
            await page.route('**/*',site.route)
            await page.goto('http://metadata.test/')
            await site.started.wait()
            assert await page.locator('#sourceSelect').input_value() == 'npia'
            assert await page.locator('.novel-card').count() == 0
            assert not any('_top.json' in url for url in site.requests)
            await page.evaluate("window.cardChanges=0;new MutationObserver(()=>cardChanges++).observe(document.querySelector('#results'),{childList:true})")
            # Filter changes during loading apply to the final complete result.
            await page.locator('#searchInput').fill('npia')
            site.release.set()
            await wait_loaded(page)
            assert await page.locator('.novel-card').count() == 30
            assert await page.evaluate('cardChanges') == 1
            chunks = [url for url in site.requests if '_chunk_' in url and '_manifest' not in url]
            assert len(chunks) == 1 and 'npia_chunk_0' in chunks[0]
            assert await page.locator('#loadFullCatalog').count() == 0
            await browser.close()
    asyncio.run(scenario())


def test_source_switches_reuse_worker_and_reload_reuses_disk_until_hash_changes():
    class HashedSite(FixtureSite):
        def __init__(self):
            super().__init__()
            self.bodies = {}
            self.downloads = []
            for source in SOURCES:
                novels = [row(source)] if source in ('npia','kpage','sfc') else [row(source),row(source,8,True,True),row(source,9,True)]
                self.bodies[source] = gzip.compress(json.dumps({'novels':novels}).encode(),mtime=0)

        async def route(self, route):
            name = urlsplit(route.request.url).path.rsplit('/',1)[-1]
            source = name.split('_',1)[0]
            if source in SOURCES and name.endswith('_chunk_manifest.json'):
                manifest = {'files':[f'{source}_chunk_0.json.gz'],'chunks':1,
                    'chunkHashes':[hashlib.sha256(self.bodies[source]).hexdigest()],
                    'totalEntries':1 if source in ('npia','kpage','sfc') else 3}
                if source not in ('npia','kpage','sfc'):
                    manifest.update(format='metadata-v1',topUrl=None,coverage={'complete':True},
                        descriptionShardCount=128,descriptionShardPrefix=f'{source}_descriptions_shard_')
                await route.fulfill(json=manifest)
            elif source in SOURCES and '_chunk_' in name:
                self.downloads.append(source)
                await route.fulfill(body=self.bodies[source],content_type='application/gzip')
            else:
                await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            site = HashedSite()
            await context.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            assert site.downloads == ['npia']
            async def parked():
                for _ in range(200):
                    if not any('catalog-worker' in w.url for w in page.workers):return
                    await asyncio.sleep(.025)
                raise AssertionError('Idle catalog worker did not release its heap')
            await parked()
            await page.locator('#sourceSelect').select_option('all')
            await wait_loaded(page)
            assert site.downloads == list(SOURCES)
            assert await page.locator('.novel-card').count() == 18
            await page.evaluate('window.idleCards=[...document.querySelectorAll(".novel-card")]')
            await parked()
            assert await page.evaluate('idleCards.every((card,i)=>card===document.querySelectorAll(".novel-card")[i])')
            for source in ['nweb','all','npia','all']:
                await page.locator('#sourceSelect').select_option(source)
                await wait_loaded(page)
                await parked()
            assert site.downloads == list(SOURCES)
            assert '#src=all' in page.url
            await page.reload()
            await wait_loaded(page)
            assert await page.locator('#sourceSelect').input_value() == 'all'
            assert await page.locator('.novel-card').count() == 18
            assert site.downloads == list(SOURCES)
            assert not any('catalog-parser' in w.url for w in page.workers)
            # Reopening the page in the same profile also uses the disk index.
            await page.close()
            page = await context.new_page()
            await page.goto('https://metadata.test/#src=all')
            await wait_loaded(page)
            assert site.downloads == list(SOURCES)
            # An upstream manifest change invalidates only its changed chunk.
            changed = row('npia',42)
            site.bodies['npia'] = gzip.compress(json.dumps({'novels':[changed]}).encode(),mtime=0)
            await page.reload()
            await wait_loaded(page)
            assert site.downloads == [*SOURCES,'npia']
            assert await page.locator('.novel-card[data-source="npia"]').get_attribute('data-novel-id') == '42'
            assert not any('_top.json' in url for url in site.requests)
            await browser.close()
    asyncio.run(scenario())


def test_synopsis_failure_has_retry_and_empty_shard_has_unavailable_message():
    class SynopsisSite(FixtureSite):
        attempts = 0
        empty = False
        async def route(self, route):
            path = urlsplit(route.request.url).path
            if path.endswith('/npia_top.json.gz'):
                await route.fulfill(body=gzip.compress(json.dumps({'novels': [row('npia')]}).encode()), content_type='application/gzip')
                return
            if 'descriptions_shard_' in path:
                self.attempts += 1
                if self.attempts <= 2:
                    await route.fulfill(status=503, body='Temporary failure')
                else:
                    await route.fulfill(body=gzip.compress(json.dumps({} if self.empty else {'7': 'Recovered synopsis'}).encode()), content_type='application/gzip')
                return
            await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = SynopsisSite()
            await page.route('**/*', site.route)
            await page.goto('http://metadata.test/#src=npia')
            await page.get_by_text('Loading synopsis…', exact=True).wait_for()
            await page.get_by_role('button', name="Synopsis couldn't load. Tap to retry.").click()
            await page.get_by_text('Recovered synopsis', exact=False).wait_for()
            assert site.attempts == 3
            site.empty = True
            await page.reload()
            await page.get_by_text('No synopsis available.', exact=True).wait_for()
            assert await page.locator('.synopsis-status:visible').count() == 1
            await browser.close()
    asyncio.run(scenario())



def test_startup_downloads_tag_dictionaries_together_without_overwriting_primary_labels():
    class ParallelTagsSite(FixtureSite):
        def __init__(self):
            super().__init__()
            self.extra_started = asyncio.Event()
        async def route(self,route):
            path = urlsplit(route.request.url).path
            if path.endswith('/tags_extra.json.gz'):
                self.extra_started.set()
            if path.endswith('/tags_en.txt.gz'):
                await asyncio.wait_for(self.extra_started.wait(),5)
            await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = ParallelTagsSite()
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/#src=nweb')
            await wait_loaded(page)
            assert site.extra_started.is_set()
            assert await page.locator('.card-tag').first.inner_text() == 'Fantasy'
            await browser.close()
    asyncio.run(scenario())


def test_catalog_renders_before_synopses_and_loading_bars_fill_without_card_changes():
    class DelayedSite(FixtureSite):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.active = self.peak = 0

        async def route(self, route):
            path = urlsplit(route.request.url).path
            if 'descriptions_shard_' in path:
                self.started.set()
                self.active += 1
                self.peak = max(self.peak,self.active)
                await self.release.wait()
                source = next((s for s in SOURCES if f'/{s}_descriptions' in path),'npia')
                payload = {str(i):f'{source} loaded synopsis {i}' for i in (7,8,9)}
                payload.update({str(i):'UNUSED_SHARD_TEXT'*100 for i in range(1000,1100)})
                await route.fulfill(body=gzip.compress(json.dumps(payload).encode()),content_type='application/gzip')
                self.active -= 1
                return
            await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            for source,width in [('npia',390),('all',1440),('nweb',390)]:
                page = await browser.new_page(viewport={'width':width,'height':844},is_mobile=width==390)
                site = DelayedSite()
                await page.add_init_script('''window.synopsisParses=0;window.synopsisReplies=[];window.synopsisStarts=[];window.synopsisStops=0;
                    const parse=JSON.parse;JSON.parse=function(text,...rest){if(typeof text==='string'&&text.includes('UNUSED_SHARD_TEXT'))synopsisParses++;return parse.call(this,text,...rest)};
                    const OriginalWorker=window.Worker;
                    window.Worker=class extends OriginalWorker{constructor(url,...args){super(url,...args);this.isSynopsis=String(url).includes('synopsis-worker');if(this.isSynopsis){
                        synopsisStarts.push(document.querySelectorAll('.novel-card').length);
                        this.addEventListener('message',({data})=>{if(data.result)synopsisReplies.push(Object.keys(data.result))});
                    }}terminate(){if(this.isSynopsis)synopsisStops++;super.terminate()}};''')
                await page.route('**/*',site.route)
                await page.goto(f'https://metadata.test/#src={source}')
                await wait_loaded(page)
                assert await page.locator('.novel-card').count() == (18 if source=='all' else 3 if source=='nweb' else 1)
                first = page.locator('.novel-card').first
                section = first.locator('.card-synopsis')
                await section.scroll_into_view_if_needed()
                await asyncio.wait_for(site.started.wait(),5)
                assert await section.get_attribute('aria-busy') == 'true'
                bar = section.get_by_role('progressbar',name='Loading synopsis')
                assert await bar.is_visible()
                assert await bar.get_attribute('aria-valuenow') is None
                await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                before = await first.bounding_box()
                section_before = await section.bounding_box()
                await page.evaluate('''window.savedCards=[...document.querySelectorAll('.novel-card')];window.cardChanges=0;
                    new MutationObserver(()=>cardChanges++).observe(document.querySelector('#results'),{childList:true})''')
                if source == 'npia':
                    Path('artifacts/synopsis-ui').mkdir(parents=True,exist_ok=True)
                    await first.screenshot(path='artifacts/synopsis-ui/loading-mobile.png')
                site.release.set()
                await page.wait_for_function("document.querySelector('.novel-card .card-synopsis')?.textContent.includes('loaded synopsis')")
                assert await section.get_attribute('aria-busy') == 'false'
                assert await section.get_by_role('progressbar').count() == 0
                assert await page.evaluate('cardChanges===0&&savedCards.every((c,i)=>c===document.querySelectorAll(".novel-card")[i])')
                after = await first.bounding_box()
                section_after = await section.bounding_box()
                assert abs(before['height']-after['height']) < 1
                assert abs(section_before['height']-section_after['height']) < 1
                assert await page.evaluate('synopsisParses') == 0
                assert await page.evaluate('synopsisStarts.every(count=>count>0)')
                assert await page.evaluate('synopsisReplies.length>0&&synopsisReplies.flat().every(id=>["7","8","9"].includes(id))')
                assert site.peak <= 2
                if source == 'npia':
                    await first.screenshot(path='artifacts/synopsis-ui/loaded-mobile.png')
                    await page.wait_for_function('synopsisStops>0')
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_synopsis_worker_failure_is_retryable_without_blocking_catalog():
    class WorkerSite(FixtureSite):
        script_broken = True
        async def route(self,route):
            if self.script_broken and urlsplit(route.request.url).path == '/synopsis-worker.js':
                await route.fulfill(body="throw new Error('Worker fixture failure')",content_type='text/javascript')
                return
            await super().route(route)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = WorkerSite()
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.locator('.card-synopsis').scroll_into_view_if_needed()
            retry = page.get_by_role('button',name="Synopsis couldn't load. Tap to retry.")
            await retry.wait_for()
            assert await page.locator('.novel-card').count() == 1
            assert await page.get_by_role('progressbar').count() == 0
            site.script_broken = False
            await retry.click()
            await page.wait_for_function("document.querySelector('.card-synopsis')?.textContent.includes('npia synopsis 7')")
            assert await page.locator('.card-synopsis').get_attribute('aria-busy') == 'false'
            await browser.close()
    asyncio.run(scenario())


def test_nonranking_saved_sort_loads_the_complete_catalog_without_preview():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(many_npia=True)
            await page.route('**/*', site.route)
            await page.goto('http://metadata.test/#src=npia&sort=updated')
            await page.wait_for_function('document.querySelector("#resultCount").textContent.includes("61 novel(s)")')
            assert not await page.locator('#loadFullCatalog').is_visible()
            assert await page.locator('#sortSelect').input_value() == 'updated'
            await browser.close()
    asyncio.run(scenario())


def test_new_sources_r19_badges_audience_filter_and_reload(monkeypatch):
    original_row = row
    new_sources = ("nweb", "jara", "mpia", "rbooks", "nseries")
    def age_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source in new_sources:
            result[10] = {7: None, 8: 19, 9: 0}[ident]
            if source == "nseries":
                result[14]["synopsis_is_preview"] = True
        elif source == "npia":
            result[11] = 19
        return result
    monkeypatch.setitem(globals(), "row", age_row)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route("**/*", FixtureSite().route)
            await page.goto("http://metadata.test/#src=all")
            await wait_loaded(page)
            await page.select_option("#audienceSelect", "adult")
            await page.wait_for_function("document.querySelectorAll('.novel-card').length === 6")
            assert set(await page.locator('.novel-card').evaluate_all("cards => cards.map(c => c.dataset.source)")) == {"npia", *new_sources}
            assert await page.locator('.badge-r19').all_text_contents() == ["19+"] * 6
            for source in new_sources:
                await page.select_option("#sourceSelect", source)
                await wait_loaded(page)
                await page.wait_for_function("document.querySelectorAll('.novel-card').length === 1")
                card = page.locator('.novel-card')
                assert await card.get_attribute('data-novel-id') == '8'
                assert await card.locator('.badge-r19').get_attribute('aria-label') == 'Rated 19+'
            await page.reload()
            await wait_loaded(page)
            assert await page.locator('#audienceSelect').input_value() == 'adult'
            assert await page.locator('#sourceSelect').input_value() == 'nseries'
            assert await page.locator('.novel-card').get_attribute('data-novel-id') == '8'
            await page.wait_for_function("document.querySelector('.synopsis-label')?.textContent === 'Synopsis preview:'")
            await page.select_option('#audienceSelect', 'general')
            await page.wait_for_function("document.querySelector('.novel-card')?.dataset.novelId === '9'")
            assert await page.locator('.novel-card').count() == 1
            assert await page.locator('.badge-r19').count() == 0
            await page.select_option('#audienceSelect', 'all')
            await page.wait_for_function("document.querySelectorAll('.novel-card').length === 3")
            assert await page.locator('.novel-card[data-novel-id="7"] .badge-r19').count() == 0
            await browser.close()
    asyncio.run(scenario())


def test_metadata_frontend_browser_all_sources_filters_links_and_hash():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            site = FixtureSite()
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=all")
            await wait_loaded(page)
            assert await page.locator(".novel-card").count() == 18
            identities = await page.locator(".novel-card").evaluate_all("cards => cards.map(c => c.dataset.source + ':' + c.dataset.novelId)")
            assert len(set(identities)) == 18
            assert all(f"{source}:7" in identities for source in SOURCES)
            assert await page.locator('#sourceSelect option[value="nweb"]').is_enabled()
            await page.select_option("#sourceSelect", "nweb")
            await wait_loaded(page)
            assert await page.locator(".novel-card").count() == 3
            href = await page.locator('.novel-card[data-novel-id="7"] .card-cover-wrap').get_attribute("href")
            assert href == "https://novel.\u006e\u0061\u0076\u0065\u0072.com/best/list?novelId=7"
            assert "Fantasy" in await page.locator(".card-tags").first.inner_text()
            assert "Wrong overwrite" not in await page.locator("body").inner_text()
            await page.select_option("#sortSelect", "views")
            await page.select_option("#orderSelect", "asc")
            await page.wait_for_timeout(200)
            assert await page.locator(".novel-card").last.get_attribute("data-novel-id") == "7"
            await page.select_option("#orderSelect", "desc")
            await page.wait_for_timeout(200)
            assert await page.locator(".novel-card").last.get_attribute("data-novel-id") == "7"
            await page.select_option("#statusSelect", "ongoing")
            await page.wait_for_timeout(200)
            assert await page.locator(".novel-card").count() == 1
            assert await page.locator(".novel-card").get_attribute("data-novel-id") == "9"
            await page.select_option("#statusSelect", "complete")
            await page.wait_for_timeout(200)
            assert await page.locator(".novel-card").get_attribute("data-novel-id") == "8"
            await page.evaluate("location.hash = 'src=jara&sort=rank%3Ajara%3Anative'")
            await page.wait_for_function("document.querySelector('#sourceSelect').value === 'jara'")
            await wait_loaded(page)
            assert await page.locator(".novel-card").first.get_attribute("data-source") == "jara"
            assert await page.locator(".novel-card").first.get_attribute("data-novel-id") == "8"
            await page.wait_for_function("document.querySelector('.card-synopsis')?.textContent.includes('jara synopsis')")
            assert chr(92) + "n" in await page.locator(".card-synopsis").first.inner_text()
            assert not errors
            await browser.close()
    asyncio.run(scenario())


def test_metadata_frontend_browser_restores_page_from_complete_catalog():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(many_npia=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=npia&page=2&batch=30")
            await wait_loaded(page)
            assert "page 2 of 3" in await page.locator("#resultCount").inner_text()
            assert "page=2" in page.url
            assert await page.locator(".novel-card").first.get_attribute("data-novel-id") == "37"
            await browser.close()
    asyncio.run(scenario())


def test_metadata_frontend_browser_missing_manifest_failure_and_cancellation():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            site = FixtureSite(missing={"jara"}, broken={"mpia"}, delay_nweb=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=nweb")
            await page.wait_for_function("!document.querySelector('#sourceSelect option[value=nweb]').disabled")
            await page.select_option("#sourceSelect", "kpage")
            await wait_loaded(page)
            await page.wait_for_timeout(550)
            assert await page.locator(".novel-card").count() == 1, await page.locator('#resultCount').inner_text()
            assert await page.locator(".novel-card").get_attribute("data-source") == "kpage"
            await page.select_option("#sourceSelect", "all")
            await wait_loaded(page)
            assert await page.locator(".novel-card").count() == 12
            assert await page.locator('#sourceSelect option[value="jara"]').is_disabled()
            assert "Partial results" in await page.locator("#resultCount").inner_text()
            assert "Mpia unavailable" in await page.locator("#resultCount").inner_text()
            assert not errors
            await browser.close()
    asyncio.run(scenario())


def test_progressive_updates_reuse_cards_and_loaded_cover_nodes():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(delay_nweb=True, covers=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/")
            await page.wait_for_selector('.novel-card[data-source="npia"] img.loaded')
            await page.evaluate("""() => {
                window.savedCard = document.querySelector('.novel-card[data-source="npia"]');
                window.savedImage = savedCard.querySelector('img.card-cover');
                window.imageSources = [];
                window.coverObserver = new MutationObserver(events => events.forEach(e => imageSources.push(e.target.getAttribute('src'))));
                coverObserver.observe(savedImage, {attributes: true, attributeFilter: ['src']});
            }""")
            await wait_loaded(page)
            assert await page.evaluate("savedCard === document.querySelector('.novel-card[data-source=npia]')")
            assert await page.evaluate("savedImage === savedCard.querySelector('img.card-cover')")
            assert await page.evaluate("imageSources.every(value => !!value)")
            assert await page.locator('#sortSelect').evaluate("el => el.getBoundingClientRect().width") <= 250
            assert await page.locator('#sortSelect').evaluate("el => el.closest('.control-group').nextElementSibling.querySelector('select').id") == 'audienceSelect'
            await browser.close()
    asyncio.run(scenario())


def test_mpia_covers_no_referrer_retry_and_visible_failure(monkeypatch):
    original_row = row
    def covered_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source == 'mpia':
            protocol = 'http:' if ident == 7 else ''
            result[3] = f'{protocol}//cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/{ident}.svg'
        return result
    monkeypatch.setitem(globals(), 'row', covered_row)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 1440, 'height': 1200})
            site = FixtureSite()
            requests = {7: [], 8: [], 9: []}
            async def route_cover(route):
                url = urlsplit(route.request.url)
                if url.hostname != 'cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com':
                    await site.route(route)
                    return
                ident = int(url.path.removeprefix('/').removesuffix('.svg'))
                requests[ident].append(route.request.headers.get('referer'))
                assert url.scheme == 'https'
                if ident == 9 or (ident == 8 and len(requests[ident]) == 1):
                    await route.fulfill(status=503, body='Unavailable')
                elif ident == 7 and requests[ident][-1]:
                    await route.fulfill(status=403, body='External referrer rejected')
                else:
                    await route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="150"><rect width="100" height="150" fill="purple"/></svg>', content_type='image/svg+xml')
            await page.route('**/*', route_cover)
            await page.goto('https://metadata.test/#src=mpia')
            await wait_loaded(page)
            await page.wait_for_selector('.novel-card[data-novel-id="7"] img.loaded')
            await page.wait_for_selector('.novel-card[data-novel-id="8"] img.loaded')
            missing = page.locator('.novel-card[data-novel-id="9"] .no-img')
            await missing.wait_for()
            assert requests[7] == [None]
            assert requests[8] == [None, 'https://metadata.test/']
            assert len(requests[9]) == 2
            assert await missing.evaluate('el => getComputedStyle(el).opacity') == '1'
            assert await missing.evaluate('el => getComputedStyle(el.parentElement).animationName') == 'none'
            # A failed cover must remain settled when tag highlighting rebuilds a card.
            await page.locator('.novel-card[data-novel-id="9"] .card-tag').first.click()
            await page.wait_for_function('!!document.querySelector(".card-tag.active")')
            await missing.wait_for()
            assert len(requests[9]) == 2
            await browser.close()
    asyncio.run(scenario())


def test_failed_npia_default_cover_settles_to_visible_placeholder():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(covers=True)
            requests = []
            async def fail_covers(route):
                host = urlsplit(route.request.url).hostname
                if host in ('covers.test', 'images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com'):
                    requests.append(route.request.url)
                    await route.fulfill(status=404, body='Missing image')
                else:
                    await site.route(route)
            await page.route('**/*', fail_covers)
            await page.goto('http://metadata.test/#src=npia')
            await wait_loaded(page)
            missing = page.locator('.card-cover.no-img')
            await missing.wait_for()
            assert len(requests) == 3  # primary, one retry, default cover
            assert await missing.evaluate('el => getComputedStyle(el).opacity') == '1'
            await browser.close()
    asyncio.run(scenario())


def test_large_covers_use_small_decodes_and_release_full_size_images():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            for fmt in ['JPEG','PNG','WEBP','EXIF','SVG']:
                output = io.BytesIO()
                exif = Image.Exif()
                if fmt == 'EXIF': exif[274] = 6
                if fmt == 'SVG':
                    output.write(b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="150"><rect width="100" height="150" fill="purple"/></svg>')
                else:
                    Image.new('RGB',(1600,2400),'#8040c0').save(output,format='JPEG' if fmt == 'EXIF' else fmt,exif=exif)
                page = await browser.new_page(viewport={'width':390,'height':844},device_scale_factor=2,is_mobile=True)
                await page.add_init_script("""window.bitmapCalls=[];window.closedBitmaps=0;
                    const originalBitmap=window.createImageBitmap,originalClose=ImageBitmap.prototype.close;
                    window.createImageBitmap=(image,options)=>{bitmapCalls.push(options);return originalBitmap(image,options)};
                    ImageBitmap.prototype.close=function(){closedBitmaps++;return originalClose.call(this)};""")
                site = FixtureSite(covers=True)
                async def route(request):
                    if urlsplit(request.request.url).hostname == 'covers.test':
                        await request.fulfill(body=output.getvalue(),content_type='image/svg+xml' if fmt == 'SVG' else 'image/'+fmt.lower(),headers={'access-control-allow-origin':'*'})
                    else:
                        await site.route(request)
                await page.route('**/*',route)
                await page.goto('https://metadata.test/#src=npia')
                await wait_loaded(page)
                cover = page.locator('img.card-cover.loaded')
                await cover.wait_for()
                await cover.evaluate('c=>c.decode()')
                size = await cover.evaluate('c=>({width:c.naturalWidth,height:c.naturalHeight})')
                assert size['width'] <= 320 and size['height'] <= 480
                assert abs(size['width']/size['height']-(1.5 if fmt == 'EXIF' else 2/3)) < .01
                assert size['width']*size['height'] <= 1600*2400/10
                assert await page.locator('canvas.card-cover').count() == 0
                calls = await page.evaluate('bitmapCalls')
                assert (not calls) if fmt == 'SVG' else (calls and all(c['resizeWidth'] <= 320 for c in calls))
                assert await page.evaluate('closedBitmaps') == len(calls)
                await cover.evaluate('c=>window.savedCoverCanvas=c')
                await page.locator('#statusSelect').select_option('ongoing')
                await wait_loaded(page)
                assert await page.evaluate('savedCoverCanvas===document.querySelector("img.card-cover")')
                await page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));document.dispatchEvent(new Event('visibilitychange'))")
                assert await cover.evaluate('c=>c.complete&&c.naturalWidth>0&&c.src.startsWith("blob:")')
                # Draw the ordinary thumbnail into a fresh canvas to prove the
                # retained encoded image still contains the expected pixels.
                assert await cover.evaluate("c=>{const a=document.createElement('canvas');a.width=a.height=1;const x=a.getContext('2d');x.drawImage(c,0,0,1,1);return x.getImageData(0,0,1,1).data[3]===255}")
                url = await cover.get_attribute('src')
                await page.locator('#statusSelect').select_option('complete')
                await wait_loaded(page)
                assert await page.locator('.novel-card').count() == 0
                assert await page.evaluate("async url=>{try{await fetch(url);return false}catch(_){return true}}",url)
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_cover_resize_fallback_handles_blocked_cors_and_older_decoders():
    output = io.BytesIO()
    Image.new('RGB',(1600,2400),'#4080c0').save(output,format='JPEG')
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            for bitmap_supported in [True,False]:
                page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
                if not bitmap_supported:
                    await page.add_init_script('window.createImageBitmap=undefined')
                site = FixtureSite(covers=True)
                async def route(request):
                    if urlsplit(request.request.url).hostname == 'covers.test':
                        await request.fulfill(body=output.getvalue(),content_type='image/jpeg',
                            headers={'access-control-allow-origin':'https://different.test'})
                    else:
                        await site.route(request)
                await page.route('**/*',route)
                await page.goto('https://metadata.test/#src=npia')
                await wait_loaded(page)
                cover = page.locator('canvas.card-cover.loaded')
                await cover.wait_for()
                assert await cover.evaluate('c=>c.width<=320&&c.height<=480')
                assert await page.locator('img.card-cover').count() == 0
                assert await cover.evaluate("c=>{try{c.toDataURL();return false}catch(e){return e.name==='SecurityError'}}")
                # Simulate a discarded mobile drawing buffer and returning
                # through both tab visibility and back/forward restoration.
                for event in ['visibilitychange','pageshow','contextrestored']:
                    await cover.evaluate('c=>{window.oldCanvas=c;c.width=c.width}')
                    if event == 'contextrestored':
                        await cover.dispatch_event(event)
                    elif event == 'visibilitychange':
                        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                    else:
                        await page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
                    await page.wait_for_function("document.querySelector('canvas.card-cover.loaded') !== oldCanvas && !!document.querySelector('canvas.card-cover.loaded')")
                    cover = page.locator('canvas.card-cover.loaded')
                    assert await cover.evaluate('c=>c.width>0&&c.width<=320&&c.height<=480')
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_source_menu_icons_keyboard_history_and_site_icon():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            await page.route('**/*',FixtureSite(missing={'jara'}).route)
            await page.goto('http://metadata.test/')
            await wait_loaded(page)
            assert await page.locator('link[rel="icon"]').get_attribute('href') == 'halgakos.ico?v=1'
            await page.wait_for_function('document.querySelector(".brand-icon").naturalWidth>0')
            button = page.locator('#sourcePickerButton')
            await button.click()
            menu = page.get_by_role('listbox',name='Sources')
            assert await menu.get_by_role('option').count() == 9
            await page.wait_for_function('[...document.querySelectorAll(".source-picker-option img")].every(i=>i.complete&&i.naturalWidth>0)')
            assert await menu.get_by_role('option',name='Jara',exact=True).is_disabled()
            await menu.get_by_role('option',name='Nweb',exact=True).click()
            await wait_loaded(page)
            assert await button.inner_text() == 'Nweb'
            assert await page.locator('#sourceSelect').input_value() == 'nweb'
            await button.focus()
            await page.keyboard.press('ArrowDown')
            await page.keyboard.press('Home')
            await page.keyboard.press('ArrowDown')
            await page.keyboard.press('Enter')
            await wait_loaded(page)
            assert await button.inner_text() == 'Npia'
            assert 'src=' not in page.url
            await page.go_back()
            await page.wait_for_function('document.querySelector("#sourcePickerButton").textContent==="Nweb"')
            await button.click()
            await page.keyboard.press('Escape')
            assert await button.get_attribute('aria-expanded') == 'false'
            assert await button.evaluate('b=>b===document.activeElement')
            await browser.close()
    asyncio.run(scenario())


def test_cover_downloads_are_bounded_and_cancel_when_source_changes():
    output = io.BytesIO()
    Image.new('RGB',(1600,2400),'#4080c0').save(output,format='JPEG')
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width':1440,'height':1000})
            await page.add_init_script("""window.activeCoverFetches=0;window.peakCoverFetches=0;window.cancelledCoverFetches=0;
                const originalFetch=window.fetch;
                window.fetch=async(...args)=>{
                    if(!String(args[0]).includes('covers.test'))return originalFetch(...args);
                    activeCoverFetches++;peakCoverFetches=Math.max(peakCoverFetches,activeCoverFetches);
                    try{return await originalFetch(...args)}catch(e){if(e.name==='AbortError')cancelledCoverFetches++;throw e}
                    finally{activeCoverFetches--}
                };""")
            site = FixtureSite(covers=True,many_npia=True)
            async def route(request):
                if urlsplit(request.request.url).hostname == 'covers.test':
                    await asyncio.sleep(.3)
                    await request.fulfill(body=output.getvalue(),content_type='image/jpeg',headers={'access-control-allow-origin':'*'})
                else:
                    await site.route(request)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/#src=npia')
            await page.wait_for_function('activeCoverFetches===2')
            await page.locator('#sourceSelect').select_option('nweb')
            await wait_loaded(page)
            await page.wait_for_function('document.querySelectorAll("img.card-cover.loaded").length===3')
            assert await page.evaluate('peakCoverFetches') == 2
            assert await page.evaluate('cancelledCoverFetches') > 0
            assert await page.locator('.novel-card[data-source="npia"]').count() == 0
            assert await page.locator('.novel-card[data-source="nweb"]').count() == 3
            await browser.close()
    asyncio.run(scenario())


def test_cover_loading_waits_for_scroll_and_unchanged_titles_skip_refitting():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            site = FixtureSite(many_npia=True, covers=True)
            await page.route('**/*', site.route)
            await page.add_init_script("localStorage.setItem('noveldb.loadDescriptions', 'false')")
            await page.goto('http://metadata.test/#src=npia&batch=60')
            await wait_loaded(page)
            await page.wait_for_selector('img.loaded')
            await page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert await page.locator('.novel-card').count() == 60
            assert 0 < await page.locator('img.card-cover[src]').count() < 60
            last = page.locator('.novel-card').last.locator('img.card-cover')
            assert await last.get_attribute('src') is None
            # Reapplying identical filters should preserve DOM and fitted sizes.
            work = await page.evaluate('''async () => {
                let changes = 0;
                let serializations = 0;
                const originalStringify = JSON.stringify;
                JSON.stringify = function(value, ...args) {
                    if (value && typeof value === 'object' && !Array.isArray(value) && 'title' in value && 'tags' in value) serializations++;
                    return originalStringify(value, ...args);
                };
                const observer = new MutationObserver(events => { changes += events.length; });
                const titles = [...document.querySelectorAll('.card-title')];
                titles.forEach(t => observer.observe(t, {attributes:true, attributeFilter:['style','class']}));
                document.querySelector('#statusSelect').dispatchEvent(new Event('change'));
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                observer.disconnect();
                JSON.stringify = originalStringify;
                return {changes, serializations};
            }''')
            assert work == {'changes': 0, 'serializations': 0}
            await last.scroll_into_view_if_needed()
            await page.wait_for_function('document.querySelector(".novel-card:last-child img")?.classList.contains("loaded")')
            await browser.close()
    asyncio.run(scenario())


def test_description_preference_prevents_requests_and_persists():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite()
            await page.route("**/*", site.route)
            await page.add_init_script("localStorage.setItem('noveldb.loadDescriptions', 'false')")
            await page.goto("http://metadata.test/")
            await wait_loaded(page)
            assert not await page.locator('#loadDescriptions').is_checked()
            assert not any('descriptions_shard_' in url for url in site.requests)
            assert await page.locator('.card-synopsis:visible').count() == 0
            await page.check('#loadDescriptions')
            await page.wait_for_selector('.card-synopsis[aria-busy="false"]')
            assert any('descriptions_shard_' in url for url in site.requests)
            await page.uncheck('#loadDescriptions')
            assert await page.evaluate("localStorage.getItem('noveldb.loadDescriptions')") == 'false'
            site.requests.clear()
            await page.reload()
            await wait_loaded(page)
            assert not await page.locator('#loadDescriptions').is_checked()
            assert not any('descriptions_shard_' in url for url in site.requests)
            await browser.close()
    asyncio.run(scenario())


def test_disabling_descriptions_ignores_delayed_shard_responses():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(delay_nweb=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=nweb")
            await page.wait_for_selector('.novel-card')
            await page.uncheck('#loadDescriptions')
            await page.wait_for_timeout(600)
            assert await page.locator('.card-synopsis').count() == 0
            await page.check('#loadDescriptions')
            await page.wait_for_selector('.card-synopsis[aria-busy="false"]')
            await browser.close()
    asyncio.run(scenario())


def test_specific_coverage_messages_do_not_equate_rankings_with_catalogs():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            for coverage, message in [
                ({"catalog": {"has_complete_baseline": False}, "rankings": {"complete": True}}, "Rankings collected; catalog pending"),
                ({"catalog": {"started": True, "discovery_complete": False}}, "Catalog collection in progress"),
                ({"catalog": {"started": True, "discovery_complete": True}, "enrichment": {"pending": 12}}, "Details pending"),
                ({"catalog": {"started": True, "errors": [{"partition": "latest", "error": "reset"}]}}, "1 catalog scan stopped"),
            ]:
                page = await browser.new_page()
                site = FixtureSite(coverage=coverage)
                await page.route("**/*", site.route)
                await page.goto("http://metadata.test/#src=nweb")
                await wait_loaded(page)
                assert message in await page.locator('#resultCount').inner_text()
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_jara_catalog_diagnostics_explain_legacy_pages_and_clear_on_source_change():
    async def scenario():
        reset = "ValueError: Pagination reset/invalid: requested=101, returned=1, rows=0, total=0, size=0"
        failed_row = "ValueError: Jara catalog row lacks identity or title"
        errors = [
            {"partition": partition, "error": f"Jara {partition} requested page {number}: {reason}"}
            for partition, number, reason in [
                ("series:latest", 101, reset),
                ("series:latest:category:22", 101, reset),
                ("series:latest:category:9", 101, reset),
                ("series:finished", 621, failed_row),
                ("nobless:latest", 101, reset),
                ("nobless:finished", 75, failed_row),
                ("premium:latest", 101, reset),
            ]
        ]
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(coverage_by_source={"jara": {"catalog": {"started": True, "errors": errors}}})
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=jara")
            await wait_loaded(page)
            status = await page.locator('#resultCount').inner_text()
            assert "Jara: 7 catalog scans stopped" in status
            assert "requested page" not in status
            details = page.locator('#catalogDiagnostics')
            assert await details.is_visible()
            assert not await details.evaluate("el => el.open")
            await details.locator('summary').focus()
            await page.keyboard.press("Enter")
            assert await details.evaluate("el => el.open")
            items = await details.locator('li').all_inner_texts()
            assert len(items) == 7
            assert sum("requires cursor pagination" in item for item in items) == 5
            assert "Free publication · Latest · Romance fantasy (category 22)" in items[1]
            assert "Free publication · Latest · Parody (category 9)" in items[2]
            assert "Free publication · Completed — page 621" in items[3]
            assert "Noblesse · Completed — page 75" in items[5]
            assert "Premium · Latest — page 101" in items[6]
            for index in (3, 5):
                assert "failed page and later pages were not collected in this scan" in items[index]
            for error, item in zip(errors, items):
                assert error['error'] in item
            await page.select_option('#sourceSelect', 'nweb')
            await wait_loaded(page)
            assert await details.is_hidden()
            assert await details.locator('li').count() == 0
            assert "catalog scans stopped" not in await page.locator('#resultCount').inner_text()
            await browser.close()
    asyncio.run(scenario())


def test_catalog_diagnostics_distinguish_skipped_rows_and_escape_reported_text():
    async def scenario():
        unsafe = '<img src="x" onerror="window.catalogInjected=true">'
        coverage = {"catalog": {"started": True, "errors": [
            {"partition": "series:finished", "page": 14, "error": "HTTP 503"},
        ], "skipped_rows": [
            {"partition": "nobless:finished", "page": 75, "row": 4, "id": "8123", "error": "Title unavailable in public catalog"},
            {"partition": unsafe, "page": 10, "row": 2, "id": "8124", "error": unsafe},
        ]}}
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(coverage_by_source={"jara": coverage})
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=jara")
            await wait_loaded(page)
            assert "1 catalog scan stopped; 2 invalid catalog rows skipped" in await page.locator('#resultCount').inner_text()
            details = page.locator('#catalogDiagnostics')
            await details.locator('summary').click()
            items = await details.locator('li').all_inner_texts()
            assert "Free publication · Completed — page 14" in items[0]
            assert "This catalog scan stopped at page 14" in items[0]
            assert "Noblesse · Completed — page 75 — row 4 — novel 8123" in items[1]
            assert "other rows were collected and the scan continued" in items[1]
            assert "Omitted listings are checked against the work's own detail record on the next catalog run." in items[1]
            assert "later pages were not collected" not in items[1]
            assert unsafe in items[2]
            assert await details.locator('img').count() == 0
            assert not await page.evaluate("Boolean(window.catalogInjected)")
            await browser.close()
    asyncio.run(scenario())


def test_compact_metrics_and_synopsis_fill_remaining_card_space():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            for width in (1440, 390):
                page = await browser.new_page(viewport={"width": width, "height": 1200})
                site = FixtureSite(covers=True)
                await page.route("**/*", site.route)
                await page.goto("http://metadata.test/#src=jara")
                await wait_loaded(page)
                await page.wait_for_selector('.card-synopsis[aria-busy="false"]')
                assert await page.get_by_label('Load Synopsis', exact=True).is_checked()
                known = page.locator('.novel-card[data-novel-id="8"]')
                for label, icon in [('Views', '👁'), ('Episodes', '📄'), ('Favorites', '❤'), ('Recommendations', '👍')]:
                    stat = known.locator(f'.stat[title="{label}"]')
                    assert icon in await stat.inner_text()
                    assert label not in await stat.inner_text()
                    assert (await stat.get_attribute('aria-label')).startswith(label + ':')
                # Uneven tag counts reproduce the spare space in stretched grid cards.
                await page.evaluate('''() => {
                    const cards = [...document.querySelectorAll('.novel-card')];
                    cards[1].querySelector('.card-tags').innerHTML = '<span class="card-tag">Fantasy Adventure</span>'.repeat(18);
                    for (const card of cards) {
                        card.querySelector('.card-synopsis').textContent = ('A long synopsis paragraph. '.repeat(12) + 'https://example.test/' + 'longpath'.repeat(40) + '\\n').repeat(8);
                    }
                }''')
                geometry = await page.locator('.novel-card').evaluate_all('''cards => cards.map(card => {
                    const body = card.querySelector('.card-body');
                    const synopsis = card.querySelector('.card-synopsis');
                    return {
                        gap: body.getBoundingClientRect().bottom - parseFloat(getComputedStyle(body).paddingBottom) - synopsis.getBoundingClientRect().bottom,
                        height: synopsis.clientHeight,
                        scrollHeight: synopsis.scrollHeight,
                        overflow: synopsis.scrollWidth - synopsis.clientWidth,
                        cardOverflow: card.scrollWidth - card.clientWidth,
                    };
                })''')
                assert all(abs(item['gap']) <= 2 for item in geometry), geometry
                assert all(item['overflow'] <= 1 and item['cardOverflow'] <= 1 for item in geometry), geometry
                assert geometry[0]['height'] > geometry[1]['height'] + 20, geometry
                assert all(item['scrollHeight'] > item['height'] for item in geometry), geometry
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_clicked_card_tags_keep_source_order_across_all_platforms(monkeypatch):
    original_row = row
    def tagged_row(*args, **kwargs):
        result = original_row(*args, **kwargs)
        result[4] = ['Fantasy', 'Academy', 'Harem']
        return result
    monkeypatch.setitem(globals(), 'row', tagged_row)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite().route)
            await page.goto('http://metadata.test/#src=all')
            await wait_loaded(page)
            for source in SOURCES:
                card = page.locator(f'.novel-card[data-source="{source}"][data-novel-id="7"]')
                tags = card.locator('.card-tag')
                before = await tags.all_text_contents()
                clicked = card.locator('.card-tag[data-tag="Harem"]')
                await clicked.click()
                await page.wait_for_function('''source => document.querySelector(`.novel-card[data-source="${source}"][data-novel-id="7"] .card-tag[data-tag="Harem"]`)?.classList.contains('active')''', arg=source)
                assert await tags.all_text_contents() == before, source
                assert await page.locator('.novel-card').count() == 18
                # Other cards sharing the selected tag must retain their order too.
                orders = await page.locator('.card-tags').evaluate_all('elements => elements.map(el => [...el.children].map(tag => tag.textContent))')
                assert all(order == before for order in orders)
                await clicked.click()
                await page.wait_for_function('!document.querySelector(".card-tag.active")')
                assert await tags.all_text_contents() == before, source
            await browser.close()
    asyncio.run(scenario())


def test_nweb_novels_without_synopsis_stay_visible(monkeypatch):
    original_row = row
    def marked_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source == 'nweb':
            result[14]['synopsis_available'] = ident != 7
        return result
    monkeypatch.setitem(globals(), 'row', marked_row)
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(delay_nweb=True)
            await page.route('**/*', site.route)
            await page.goto('http://metadata.test/#src=nweb')
            await wait_loaded(page)
            missing = page.locator('.novel-card[data-source="nweb"][data-novel-id="7"]')
            # Shown whether or not descriptions are loaded.
            await missing.wait_for()
            await page.uncheck('#loadDescriptions')
            await missing.wait_for()
            await page.reload()
            await wait_loaded(page)
            await missing.wait_for()
            await page.check('#loadDescriptions')
            await missing.wait_for()
            assert await page.locator('.novel-card').count() > 0
            await browser.close()
    asyncio.run(scenario())


def test_tag_selection_stays_in_sync_between_cards_cloud_and_summary(monkeypatch):
    original_row = row
    def tagged_row(*args, **kwargs):
        result = original_row(*args, **kwargs)
        result[4] = ['Fantasy', 'Harem']
        return result
    monkeypatch.setitem(globals(), 'row', tagged_row)

    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite().route)
            await page.goto('http://metadata.test/#src=nweb')
            await wait_loaded(page)
            card_tag = page.locator('.novel-card[data-novel-id="8"] .card-tag[data-tag="Harem"]')
            cloud_chip = page.locator('#tagContainer .tag-chip[data-tag="Harem"]')
            summary = page.locator('#activeTagsSummary')
            await card_tag.click()
            await page.wait_for_function("document.querySelector('#activeTagsSummary').textContent.includes('Harem')")
            assert await cloud_chip.evaluate("el => el.classList.contains('active')")
            # Clearing from the card clears the summary row and the cloud chip.
            await card_tag.click()
            await page.wait_for_function("!document.querySelector('.card-tag.active')")
            assert await summary.inner_text() == ''
            assert not await cloud_chip.evaluate("el => el.classList.contains('active')")
            # Clearing from the summary row clears the card highlight.
            await card_tag.click()
            await page.wait_for_function("document.querySelector('.card-tag.active')")
            await summary.locator('.summary-chip').click()
            await page.wait_for_function("!document.querySelector('.card-tag.active')")
            assert await summary.inner_text() == ''
            # Clearing from the tag cloud does the same.
            await card_tag.click()
            await page.wait_for_function("document.querySelector('.card-tag.active')")
            await page.click('#tagToggle span')
            await cloud_chip.click()
            await page.wait_for_function("!document.querySelector('.card-tag.active')")
            assert await summary.inner_text() == ''
            assert await page.locator('.novel-card').count() == 3
            # A tag chosen from the cloud looks pressed on every card that has it,
            # so clicking it on a card visibly toggles it off instead of surprising.
            await cloud_chip.click()
            await page.wait_for_function("document.querySelectorAll('.card-tag.active[data-tag=\"Harem\"]').length === 3")
            await card_tag.click()
            await page.wait_for_function("!document.querySelector('.card-tag.active')")
            assert await summary.inner_text() == ''
            # OR selections are distinguished on cards as they are in the cloud.
            await page.click('#tagModeOr')
            await cloud_chip.click()
            await page.wait_for_function("document.querySelectorAll('.card-tag.active-or[data-tag=\"Harem\"]').length === 3")
            assert await cloud_chip.evaluate("el => el.classList.contains('active-or')")
            await browser.close()
    asyncio.run(scenario())
