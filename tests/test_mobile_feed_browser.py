"""A lazy, bounded feed behind one Infinite scrolling switch (on by default)."""
import asyncio
import gzip
import json
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from test_metadata_frontend_browser import FixtureSite, row, wait_loaded, open_settings, close_settings
from test_catalog_previews import PreviewSite, previews


class FeedSite(FixtureSite):
    def __init__(self,count=240,covers=False):
        super().__init__(covers=covers)
        self.count=count
    async def route(self,r):
        path=urlsplit(r.request.url).path
        if path.endswith('npia_chunk_manifest.json'):
            await r.fulfill(json={'files':['npia_chunk_0.json.gz'],'chunks':1,'totalEntries':self.count,'embedded':True})
        elif path.endswith('npia_chunk_0.json.gz'):
            rows=[row('npia',i) for i in range(1,self.count+1)]
            for entry in rows:
                entry[1]=f'Novel {entry[0]:04}'
                if self.covers:entry[3]=f'https://covers.test/npia/{entry[0]}.svg'
            await r.fulfill(body=gzip.compress(json.dumps({'novels':rows,'translations':{str(i):f'Novel {i:04}' for i in range(1,self.count+1)}}).encode()),content_type='application/gzip')
        else:await super().route(r)


def test_desktop_feed_is_optional_responsive_and_one_switch_covers_every_screen():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':1440,'height':900})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            # Start from page numbers: a saved "off" choice, written once so reloads keep later choices.
            await page.add_init_script("if(localStorage.getItem('noveldb.infiniteScroll')===null)localStorage.setItem('noveldb.infiniteScroll','false')")
            await page.route('**/*',FeedSite(covers=True).route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            assert not await page.locator('#infiniteScroll').is_checked()
            assert await page.locator('#paginationTop').is_visible()
            assert await page.locator('.batch-control').is_visible()
            await page.locator('#batchSelect').select_option('60')
            await page.wait_for_function('document.querySelectorAll(".novel-card").length===60')
            await page.locator('#infiniteScroll').check();await wait_loaded(page)
            assert not await page.locator('#paginationTop').is_visible()
            assert not await page.locator('.batch-control').is_visible()
            assert await page.locator('.mobile-feed-grid').evaluate('g=>getComputedStyle(g).gridTemplateColumns.split(" ").length')==5
            assert 10<=await page.locator('.novel-card').count()<=40
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await page.screenshot(path='artifacts/desktop-infinite-scrolling.png',full_page=False)
            first=await page.locator('.novel-card').first.get_attribute('data-novel-id')
            for _ in range(5):
                before=int(await page.locator('#results').get_attribute('data-feed-end'))
                await page.evaluate('scrollTo({top:document.documentElement.scrollHeight-innerHeight,behavior:"instant"})')
                await page.wait_for_function('(n)=>Number(document.querySelector("#results").dataset.feedEnd)>n',arg=before)
                assert await page.locator('.novel-card').count()<=40
                assert int(await page.locator('#results').get_attribute('data-feed-cached'))<=4
            assert int(await page.locator('#results').get_attribute('data-feed-start'))>30
            await page.evaluate('scrollBy({top:-40,behavior:"instant"})')
            await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert not await page.locator('.controls').evaluate('c=>c.classList.contains("mobile-controls-floating")')
            await page.evaluate('scrollBy({top:-110,behavior:"instant"})')
            await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert not await page.locator('.controls').evaluate('c=>c.classList.contains("mobile-controls-floating")')
            await page.evaluate('scrollBy({top:-150,behavior:"instant"})')
            await page.wait_for_function('document.querySelector(".controls").classList.contains("mobile-controls-floating")')
            await page.wait_for_function('document.querySelector(".controls").getBoundingClientRect().top===0')
            # Resize a scrolled desktop feed across non-divisors of its 30-record slices.
            for width,columns in ((1100,4),(1920,6)):
                await page.set_viewport_size({'width':width,'height':900})
                await page.wait_for_function('(n)=>getComputedStyle(document.querySelector(".mobile-feed-grid")).gridTemplateColumns.split(" ").length===n',arg=columns)
                await page.wait_for_function('(n)=>Number(document.querySelector("#results").dataset.feedStart)%n===0',arg=columns)
                indexes=await page.locator('.novel-card').evaluate_all('cs=>cs.map(c=>Number(c.dataset.feedIndex))')
                assert indexes==list(range(indexes[0],indexes[-1]+1))
            await page.evaluate('scrollTo({top:0,behavior:"instant"})')
            await page.wait_for_function('document.querySelector("#results").dataset.feedStart==="0"')
            assert await page.locator('.novel-card').first.get_attribute('data-novel-id')==first
            await page.reload();await wait_loaded(page)
            assert await page.locator('#infiniteScroll').is_checked()
            assert not await page.locator('#paginationTop').is_visible()
            # The one switch covers every screen size once it has been chosen.
            await page.set_viewport_size({'width':390,'height':844});await wait_loaded(page)
            assert await page.locator('#infiniteScroll').is_checked()
            assert not await page.locator('#paginationTop').is_visible()
            await page.set_viewport_size({'width':1440,'height':900});await wait_loaded(page)
            assert not await page.locator('#paginationTop').is_visible()
            await page.locator('#infiniteScroll').uncheck();await wait_loaded(page)
            # Turned off right after a resize, the feed finishes re-flowing before the pages return.
            await page.locator('#paginationTop').wait_for(state='visible')
            assert await page.locator('.batch-control').is_visible()
            assert await page.locator('#batchSelect').input_value()=='60'
            assert await page.locator('.novel-card').count()==60
            await page.locator('#paginationTop .next-page').click()
            await page.wait_for_function('document.querySelector("#paginationTop [aria-current=page]")?.textContent==="2"')
            await page.reload();await wait_loaded(page)
            assert not await page.locator('#infiniteScroll').is_checked()
            assert await page.locator('#paginationTop').is_visible()
            assert not errors
            await browser.close()
    asyncio.run(scenario())


def test_mobile_feed_recycles_in_both_directions_reveals_controls_and_restores_pages():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            await page.route('**/*',FeedSite(covers=True).route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            assert await page.locator('#infiniteScroll').is_checked()
            assert await page.locator('.pagination:visible').count()==0
            assert not await page.locator('.batch-control').is_visible()
            assert 4<=await page.locator('.novel-card').count()<=16
            first=await page.locator('.novel-card').first.get_attribute('data-novel-id')
            for _ in range(8):
                previous=await page.locator('#results').get_attribute('data-feed-end')
                await page.evaluate('scrollTo({top:document.documentElement.scrollHeight-innerHeight,behavior:"instant"})')
                await page.wait_for_function('(before)=>Number(document.querySelector("#results").dataset.feedEnd)>before',arg=int(previous))
                assert await page.locator('.novel-card').count()<=16
                assert int(await page.locator('#results').get_attribute('data-feed-cached'))<=4
            assert int(await page.locator('#results').get_attribute('data-feed-start'))>36
            # Separate small upward corrections should not accumulate into a reveal.
            for _ in range(4):
                await page.evaluate('scrollBy({top:-40,behavior:"instant"})')
                await page.wait_for_timeout(450)
                assert not await page.locator('.controls').evaluate('c=>c.classList.contains("mobile-controls-floating")')
            await page.evaluate('scrollBy({top:-150,behavior:"instant"})')
            await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert not await page.locator('.controls').evaluate('c=>c.classList.contains("mobile-controls-floating")')
            await page.evaluate('scrollBy({top:-150,behavior:"instant"})')
            await page.wait_for_function('document.querySelector(".controls").classList.contains("mobile-controls-floating")')
            await page.wait_for_function('document.querySelector(".controls").getBoundingClientRect().top===0')
            await page.locator('#sourcePickerButton').click()
            assert await page.locator('#sourcePickerMenu').evaluate('menu=>{const r=menu.getBoundingClientRect();return menu.contains(document.elementFromPoint(r.left+15,r.bottom-10));}')
            await page.keyboard.press('Escape')
            await page.screenshot(path='artifacts/mobile-feed-upward-controls.png')
            await page.evaluate('scrollBy({top:200,behavior:"instant"})')
            await page.wait_for_function('!document.querySelector(".controls").classList.contains("mobile-controls-floating")')
            # Returning to the beginning restores the original order from disk.
            await page.evaluate('scrollTo({top:0,behavior:"instant"})')
            await page.wait_for_function('document.querySelector("#results").dataset.feedStart==="0"')
            assert await page.locator('.novel-card').first.get_attribute('data-novel-id')==first
            await open_settings(page)
            await page.locator('#infiniteScroll').uncheck()
            await close_settings(page);await wait_loaded(page)
            assert await page.locator('.novel-card').count()==30
            assert await page.locator('#paginationTop').is_visible()
            assert await page.locator('.batch-control').is_visible()
            await page.locator('#paginationTop .next-page').click()
            await page.wait_for_function('document.querySelector("#paginationTop [aria-current=page]")?.textContent==="2"')
            await page.reload();await wait_loaded(page)
            assert not await page.locator('#infiniteScroll').is_checked()
            assert await page.locator('#paginationTop').is_visible()
            await open_settings(page)
            await page.locator('#infiniteScroll').check()
            await close_settings(page);await wait_loaded(page)
            assert not await page.locator('#paginationTop').is_visible()
            await page.locator('#searchInput').fill('Novel 0001')
            await page.wait_for_function('document.querySelector("#resultCount").textContent.startsWith("1 novel")')
            await page.wait_for_function('document.querySelectorAll(".novel-card").length===1')
            assert await page.locator('.novel-card').get_attribute('data-novel-id')=='1'
            await page.reload();await wait_loaded(page)
            assert await page.locator('#infiniteScroll').is_checked()
            # Chosen on a phone, the switch stays on when the window gets wider.
            await page.set_viewport_size({'width':1440,'height':900});await wait_loaded(page)
            assert await page.locator('body').evaluate('b=>b.classList.contains("mobile-infinite")')
            assert not await page.locator('#paginationTop').is_visible()
            assert not errors
            await browser.close()
    asyncio.run(scenario())


def test_mobile_feed_reports_a_failed_slice_and_retries_without_losing_cards():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            await page.add_init_script('''const SavedWorker=Worker;window.Worker=class extends SavedWorker {
                postMessage(message,...rest){
                    if(message.type==='query'&&message.value.page===2&&!window.sliceFailed){window.sliceFailed=true;
                        setTimeout(()=>this.dispatchEvent(new MessageEvent('message',{data:{id:message.id,error:{message:'Test slice unavailable'}}})),0);return;
                    }super.postMessage(message,...rest);
                }
            };''')
            await page.route('**/*',FeedSite().route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.evaluate('scrollTo({top:document.documentElement.scrollHeight-innerHeight,behavior:"instant"})')
            await page.wait_for_function('document.querySelector(".mobile-feed-status")?.textContent.includes("Tap to retry")')
            assert await page.locator('.novel-card').count()>0
            await page.locator('.mobile-feed-status').click()
            await page.wait_for_function('Number(document.querySelector("#results").dataset.feedEnd)>12')
            assert not await page.locator('.mobile-feed-status').is_visible()
            assert await page.locator('.novel-card').count()<=16
            await browser.close()
    asyncio.run(scenario())


def test_mobile_feed_keeps_rank_preview_order_when_import_finishes(previews):
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            site=PreviewSite(previews,blocked=True)
            await page.route('**/*',site.route)
            await page.goto('https://metadata.test/#src=all')
            await page.wait_for_function('document.querySelectorAll(".novel-card").length===12')
            assert not await page.locator('body').evaluate('b=>b.dataset.catalogReady==="true"')
            for _ in range(3):
                before=int(await page.locator('#results').get_attribute('data-feed-end'))
                await page.evaluate('scrollTo({top:document.documentElement.scrollHeight-innerHeight,behavior:"instant"})')
                await page.wait_for_function('(n)=>Number(document.querySelector("#results").dataset.feedEnd)>n',arg=before)
            await page.evaluate('window.lockedCards=[...document.querySelectorAll(".novel-card")];window.lockedIds=lockedCards.map(c=>c.dataset.novelId)')
            site.release.set();await wait_loaded(page)
            assert await page.evaluate('lockedCards.every(c=>c.isConnected)')
            assert await page.evaluate('lockedIds.every((id,i)=>lockedCards[i].dataset.novelId===id)')
            assert await page.locator('.novel-card').count()<=16
            assert not await page.locator('#paginationTop').is_visible()
            await browser.close()
    asyncio.run(scenario())


def test_one_infinite_scrolling_switch_on_by_default_with_earlier_choices_honoured():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            for width,mobile,saved,expected in (
                (390,True,None,True),          # phones: on by default
                (1440,False,None,True),        # wider screens: on by default too
                (1440,False,('noveldb.desktopInfiniteScroll','true'),True),   # earlier desktop choice
                (390,True,('noveldb.mobileInfiniteScroll','false'),False),    # earlier phone choice
                (390,True,('noveldb.infiniteScroll','false'),False),          # the new choice wins
            ):
                page=await browser.new_page(viewport={'width':width,'height':900},is_mobile=mobile)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                if saved:
                    await page.add_init_script(f"localStorage.setItem({saved[0]!r},{saved[1]!r})")
                await page.route('**/*',FeedSite().route)
                await page.goto('https://metadata.test/');await wait_loaded(page)
                assert await page.locator('input[type=checkbox][id$=InfiniteScroll], #infiniteScroll').count()==1
                assert (await page.locator('label:has(#infiniteScroll)').inner_text()).strip()=='Infinite scrolling'
                assert await page.locator('#infiniteScroll').is_checked()==expected
                assert await page.locator('body').evaluate('b=>b.classList.contains("mobile-infinite")')==expected
                assert not errors
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_wider_or_zoomed_out_windows_show_more_cards_per_row():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            for feed in (False,True):
                page=await browser.new_page(viewport={'width':1920,'height':900})
                await page.add_init_script(f"localStorage.setItem('noveldb.infiniteScroll','{str(feed).lower()}')")
                await page.route('**/*',FeedSite().route)
                await page.goto('https://metadata.test/');await wait_loaded(page)
                grid='.mobile-feed-grid' if feed else '.results-grid'
                # Zooming out makes the window wider in CSS pixels: 100%, about 80% and 50% of 1920.
                for width,columns in ((1920,6),(2400,8),(3840,12)):
                    await page.set_viewport_size({'width':width,'height':900})
                    await page.wait_for_function('([g,n])=>getComputedStyle(document.querySelector(g)).gridTemplateColumns.split(" ").length===n',arg=[grid,columns])
                await page.close()
            await browser.close()
    asyncio.run(scenario())
