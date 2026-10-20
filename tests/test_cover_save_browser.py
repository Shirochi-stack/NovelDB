"""Check actual WebP downloads and thumbnail pixel budgets with local images."""
import asyncio
import io
from urllib.parse import urlsplit

import pytest
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from test_metadata_frontend_browser import FixtureSite, wait_loaded


def image_bytes(fmt):
    image=Image.new('RGB',(800,1200))
    draw=ImageDraw.Draw(image)
    for y in range(0,1200,20):
        for x in range(0,800,20):
            draw.rectangle((x,y,x+19,y+19),fill=(x%256,y%256,(x+y)%256))
    stream=io.BytesIO(); image.save(stream,format=fmt)
    return stream.getvalue()


@pytest.mark.parametrize('fmt,context_menu',[('PNG',False),('JPEG',True),('WEBP',False)])
def test_original_download_keeps_original_dimensions_and_pixels_while_display_is_small_webp(tmp_path,fmt,context_menu):
    async def scenario():
        data=image_bytes(fmt)
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},device_scale_factor=3,is_mobile=True,accept_downloads=True)
            site=FixtureSite(covers=True)
            async def route(r):
                if urlsplit(r.request.url).hostname=='covers.test':
                    await r.fulfill(body=data,content_type='image/'+fmt.lower(),headers={'access-control-allow-origin':'*'})
                else: await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            cover=page.locator('img.card-cover.loaded');await cover.wait_for()
            thumbnail=await cover.evaluate('''async img=>{
                const blob=await (await fetch(img.src)).blob();
                return {width:img.naturalWidth,height:img.naturalHeight,type:blob.type,size:blob.size};
            }''')
            assert thumbnail['width']<=320 and thumbnail['height']<=480
            assert thumbnail['type']=='image/webp'
            await page.evaluate('window.savedCard=document.querySelector(".novel-card");window.savedCover=document.querySelector("img.card-cover")')
            if context_menu:
                await cover.scroll_into_view_if_needed()
                await cover.click(button='right',position={'x':10,'y':10})
                await page.get_by_role('menu',name='Cover image').wait_for()
                assert await page.get_by_role('menu',name='Cover image').evaluate('menu=>{const r=menu.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}')
                assert await page.get_by_role('menuitem',name='Open original image').get_attribute('href')=='https://covers.test/npia/7.svg'
                button=page.get_by_role('menuitem',name='Save original WebP')
            else: button=page.get_by_role('button',name='Save original cover as WebP')
            async with page.expect_download() as event: await button.click()
            download=await event.value
            assert download.suggested_filename=='npia-7.webp'
            file=tmp_path/f'{fmt}.webp';await download.save_as(file)
            exported=Image.open(file)
            assert exported.format=='WEBP' and exported.size==(800,1200)
            if fmt=='WEBP': assert file.read_bytes()==data
            else:
                assert b'VP8L' in file.read_bytes()
                assert exported.convert('RGB').tobytes()==Image.open(io.BytesIO(data)).convert('RGB').tobytes()
            assert await page.evaluate('savedCard===document.querySelector(".novel-card")&&savedCover===document.querySelector("img.card-cover")')
            assert await cover.evaluate('img=>img.naturalWidth')<=320
            assert await page.locator('#lowResCovers').is_checked()
            await browser.close()
    asyncio.run(scenario())


def test_blocked_host_keeps_small_canvas_and_offers_original_instead_of_saving_a_thumbnail():
    async def scenario():
        data=image_bytes('PNG')
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page()
            downloads=[];page.on('download',lambda value:downloads.append(value))
            site=FixtureSite(covers=True)
            async def route(r):
                if urlsplit(r.request.url).hostname=='covers.test':
                    await r.fulfill(body=data,content_type='image/png',headers={'access-control-allow-origin':'https://different.test'})
                else: await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.locator('canvas.card-cover.loaded').wait_for()
            assert await page.locator('canvas.card-cover.loaded').evaluate('c=>c.width<=320&&c.height<=480')
            await page.get_by_role('button',name='Save original cover as WebP').click()
            await page.wait_for_function("document.querySelector('.cover-save-notice').textContent.includes('blocks browser conversion')")
            assert await page.locator('.cover-save-notice a').get_attribute('href')=='https://covers.test/npia/7.svg'
            assert not downloads
            assert await page.get_by_role('button',name='Save original cover as WebP').is_enabled()
            await browser.close()
    asyncio.run(scenario())
