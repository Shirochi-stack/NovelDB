"""Source originals, thumbnail fallback, settings refresh, and downloads."""
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
from test_metadata_frontend_browser import FixtureSite, row, wait_loaded


@pytest.mark.parametrize('source,stored', [
    ('mpia', 'https://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/v2/files/tcover/2026/1020/21/id'),
    ('nweb', 'https://novel-phinf.pstatic.net/image.png?type=f280_224_2'),
    ('nseries', 'https://comicthumb-phinf.pstatic.net/image.png?type=m79'),
    ('kpage', 'https://dn-img-page.\u006b\u0061\u006b\u0061\u006f.com/download/resource?kid=a+b/c&filename=th3'),
])
@pytest.mark.parametrize('original_missing', [False, True])
def test_source_originals_and_fallback_survive_compression_refresh_and_download(source, stored, original_missing):
    async def scenario():
        original = image_bytes('PNG')
        output = io.BytesIO()
        Image.new('RGB', (79, 119), 'purple').save(output, format='PNG')
        thumbnail = output.getvalue()
        site = FixtureSite(covers=True)
        requests = []
        async def route(r):
            url = urlsplit(r.request.url)
            if url.hostname == urlsplit(stored).hostname:
                requests.append(r.request.url)
                is_original = r.request.url != stored
                if is_original and original_missing:
                    await r.fulfill(status=404, body='missing', headers={'access-control-allow-origin':'*'})
                else:
                    await r.fulfill(body=original if is_original else thumbnail, content_type='image/png',
                                    headers={'access-control-allow-origin':'*'})
            elif url.path.endswith(f'/data/{source}_chunk_0.json.gz'):
                novel = row(source); novel[3] = stored
                await r.fulfill(body=gzip.compress(json.dumps({'novels':[novel]}).encode()), content_type='application/gzip')
            else:
                await site.route(r)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(accept_downloads=True)
            await page.route('**/*', route)
            await page.goto(f'https://metadata.test/#src={source}')
            await wait_loaded(page)
            cover = page.locator('img.card-cover.loaded').first
            await cover.wait_for()
            assert await cover.evaluate('img=>img.naturalWidth') <= 320
            assert any(value != stored for value in requests)
            if original_missing:
                assert stored in requests
            expected = stored if original_missing else await cover.get_attribute('data-src')
            assert await cover.get_attribute('data-original-src') == expected
            await page.evaluate('NovelCovers.setLowResolution(false)')
            width = 79 if original_missing else 800
            await page.wait_for_function('(width)=>document.querySelector("img.card-cover.loaded")?.naturalWidth===width', arg=width)
            await page.locator('.cover-details').first.click()
            await page.wait_for_function('(width)=>document.querySelector(".novel-details-image img")?.naturalWidth===width', arg=width)
            async with page.expect_download() as event:
                await page.get_by_role('dialog').get_by_role('button', name='Download Image').click()
            download = await event.value
            path = Path(await download.path())
            assert path.read_bytes() == (thumbnail if original_missing else original)
            await page.get_by_role('button', name='Close Details').click()
            await page.evaluate('NovelCovers.setLowResolution(true)')
            await page.wait_for_function('document.querySelector("img.card-cover.loaded")?.src.startsWith("blob:")')
            assert await cover.get_attribute('data-original-src') == expected
            await browser.close()
    asyncio.run(scenario())
