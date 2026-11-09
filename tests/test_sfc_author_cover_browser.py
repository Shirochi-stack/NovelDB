import asyncio
import gzip
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from test_cover_save_browser import image_bytes
from test_metadata_frontend_browser import FixtureSite, row, wait_loaded, open_settings, close_settings


@pytest.mark.parametrize('mobile', [False, True])
@pytest.mark.parametrize('outcome', ['match', 'no_match', 'image_missing', 'index_missing', 'invalid_host'])
def test_sfc_author_cover_lookup_details_download_and_fallback(mobile, outcome):
    async def scenario():
        root = 'https://rss.\u0073\u0066\u0061\u0063\u0067.com/web/novel/images/'
        standard = root + 'NovelCover/Big/2026/10/id.jpg'
        author = root + 'UploadPic/2026/10/art.jpg'
        original = image_bytes('PNG')
        output = io.BytesIO()
        Image.open(io.BytesIO(original)).resize((368, 460)).save(output, format='PNG')
        small = output.getvalue()
        site = FixtureSite(covers=True)
        lookups, images = [], []
        async def route(request):
            url = urlsplit(request.request.url)
            if url.path.endswith('/sfc_author_covers.json.gz'):
                lookups.append(request.request.url)
                await asyncio.sleep(.15)
                if outcome == 'index_missing':
                    await request.fulfill(status=503, body='unavailable')
                    return
                entry = author if outcome != 'invalid_host' else 'https://evil.test/art.jpg'
                covers = {urlsplit(standard).path: {'url': entry}} if outcome != 'no_match' else {}
                await request.fulfill(body=gzip.compress(json.dumps({'schema': 1, 'covers': covers}).encode()),
                                      content_type='application/gzip')
            elif request.request.url in (standard, author):
                images.append(request.request.url)
                if request.request.url == author and outcome == 'image_missing':
                    await request.fulfill(status=404, body='missing')
                else:
                    await request.fulfill(body=original if request.request.url == author else small,
                        content_type='image/png', headers={'access-control-allow-origin':'*'})
            elif url.path.endswith('/sfc_chunk_0.json.gz'):
                novel = row('sfc'); novel[3] = standard
                await request.fulfill(body=gzip.compress(json.dumps({'novels':[novel]}).encode()), content_type='application/gzip')
            else:
                assert url.hostname != 'evil.test'
                await site.route(request)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(accept_downloads=True,
                viewport={'width':390,'height':844} if mobile else {'width':1440,'height':1000}, is_mobile=mobile)
            await page.add_init_script("localStorage.setItem('noveldb.lowResCovers','false')")
            await page.route('**/*', route)
            await page.goto('https://metadata.test/#src=sfc')
            await wait_loaded(page)
            await page.locator('.cover-details').first.click(force=True)
            expected = author if outcome == 'match' else standard
            width = 800 if outcome == 'match' else 368
            await page.wait_for_function('(width)=>document.querySelector(".novel-details-image img")?.naturalWidth===width', arg=width)
            assert await page.locator('.novel-details-image img').get_attribute('src') == expected
            await page.wait_for_function('(width)=>document.querySelector("img.card-cover.loaded")?.naturalWidth===width', arg=width)
            assert await page.locator('img.card-cover.loaded').first.get_attribute('data-original-src') == expected
            assert len(lookups) == 1
            async with page.expect_download() as event:
                await page.get_by_role('dialog').get_by_role('button', name='Download Image').click()
            download = await event.value
            assert Path(await download.path()).read_bytes() == (original if outcome == 'match' else small)
            await page.get_by_role('button', name='Close Details').click()
            await open_settings(page)
            await page.locator('#lowResCovers').check()
            await close_settings(page)
            await page.wait_for_function('document.querySelector("img.card-cover.loaded")?.src.startsWith("blob:")')
            assert await page.locator('img.card-cover.loaded').first.get_attribute('data-original-src') == expected
            assert len(lookups) == 1
            if outcome in ('no_match', 'index_missing', 'invalid_host'):
                assert author not in images
            await browser.close()
    asyncio.run(scenario())
