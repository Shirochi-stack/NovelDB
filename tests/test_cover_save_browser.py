"""Check original downloads, image details and thumbnail compression with local images."""
import asyncio
import gzip
import io
import json
from urllib.parse import urlsplit

import pytest
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from test_metadata_frontend_browser import FixtureSite, SOURCES, row, wait_loaded


def image_bytes(fmt):
    image=Image.new('RGB',(800,1200))
    draw=ImageDraw.Draw(image)
    for y in range(0,1200,20):
        for x in range(0,800,20):
            draw.rectangle((x,y,x+19,y+19),fill=(x%256,y%256,(x+y)%256))
    stream=io.BytesIO(); image.save(stream,format=fmt)
    return stream.getvalue()


def test_source_logo_is_the_only_transparent_badge_and_opens_details_for_each_source():
    async def scenario():
        icons={'npia':'npia.png','kpage':'kpage.png','sfc':'sfc.ico','nweb':'nweb.png',
               'jara':'jara.ico','mpia':'mpia.ico','rbooks':'rbooks.png','nseries':'nseries.png'}
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            await page.route('**/*',FixtureSite().route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            for source in SOURCES:
                if source!='npia':
                    await page.select_option('#sourceSelect',source);await wait_loaded(page)
                card=page.locator('.novel-card').first;badge=card.locator('.cover-details')
                assert await card.locator('.cover-save').count()==0
                assert await card.locator('.card-cover-area button').count()==1
                assert await badge.locator('img').get_attribute('src')==f'icons/{icons[source]}'
                assert await badge.evaluate('b=>getComputedStyle(b).backgroundColor==="rgba(0, 0, 0, 0)" && getComputedStyle(b).borderTopWidth==="0px"')
                await badge.click()
                dialog=page.get_by_role('dialog');await dialog.wait_for()
                assert await dialog.locator('[data-field=source]').inner_text()==source
                assert await dialog.get_by_role('button',name='Download Image').is_visible()
                if source=='npia':
                    await page.get_by_role('button',name='Close Details').click()
                    await badge.focus();await page.keyboard.press('Enter');await dialog.wait_for()
                await page.get_by_role('button',name='Close Details').click()
            await page.select_option('#sourceSelect','all');await wait_loaded(page)
            for card in await page.locator('.novel-card').all():
                source=await card.get_attribute('data-source')
                assert await card.locator('.cover-details img').get_attribute('src')==f'icons/{icons[source]}'
            await page.locator('.novel-card').first.screenshot(path='artifacts/source-logo-badge.png')
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('fmt,context_menu',[('PNG',False),('JPEG',True),('WEBP',False),('GIF',True),('AVIF',False)])
def test_original_download_keeps_original_dimensions_and_pixels_while_display_is_small_webp(tmp_path,fmt,context_menu):
    async def scenario():
        data=image_bytes(fmt)
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},device_scale_factor=3,is_mobile=True,accept_downloads=True)
            site=FixtureSite(covers=True)
            async def route(r):
                if urlsplit(r.request.url).hostname=='covers.test':
                    await r.fulfill(body=data,content_type='application/octet-stream',headers={'access-control-allow-origin':'*','content-disposition':'attachment; filename=original.orifile'})
                elif urlsplit(r.request.url).path=='/data/npia_chunk_0.json.gz':
                    novel=row('npia');novel[3]='https://covers.test/original.orifile'
                    await r.fulfill(body=gzip.compress(json.dumps({'novels':[novel]}).encode()),content_type='application/gzip')
                else: await site.route(r)
            await page.route('**/*',route)
            await page.add_init_script("localStorage.setItem('noveldb.imageQuality','20')")
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            cover=page.locator('img.card-cover.loaded');await cover.wait_for()
            thumbnail=await cover.evaluate('''async img=>{
                const blob=await (await fetch(img.src)).blob();
                return {width:img.naturalWidth,height:img.naturalHeight,type:blob.type,size:blob.size};
            }''')
            assert thumbnail['width']<=64 and thumbnail['height']<=96
            assert thumbnail['type']=='image/webp'
            await page.evaluate('window.savedCard=document.querySelector(".novel-card");window.savedCover=document.querySelector("img.card-cover")')
            if context_menu:
                await cover.scroll_into_view_if_needed()
                await cover.click(button='right',position={'x':10,'y':10})
                await page.get_by_role('menu',name='Cover image').wait_for()
                assert await page.get_by_role('menu',name='Cover image').evaluate('menu=>{const r=menu.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}')
                assert await page.get_by_role('menuitem').all_text_contents()==['Download Image','View Details']
                button=page.get_by_role('menuitem',name='Download Image')
            else:
                await page.get_by_role('button',name='View Details').click()
                button=page.get_by_role('dialog').get_by_role('button',name='Download Image')
            async with page.expect_download() as event: await button.click()
            download=await event.value
            extension={'PNG':'png','JPEG':'jpg','WEBP':'webp','GIF':'gif','AVIF':'avif'}[fmt]
            assert download.suggested_filename==f'npia-7.{extension}'
            file=tmp_path/f'{fmt}.{extension}';await download.save_as(file)
            exported=Image.open(file)
            assert exported.format==fmt and exported.size==(800,1200)
            assert file.read_bytes()==data
            assert await page.evaluate('savedCard===document.querySelector(".novel-card")&&savedCover===document.querySelector("img.card-cover")')
            assert await cover.evaluate('img=>img.naturalWidth')<=320
            assert await page.locator('#lowResCovers').is_checked()
            await browser.close()
    asyncio.run(scenario())


def test_details_show_all_available_fields_and_original_image_without_enabling_card_synopses():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            data=image_bytes('PNG');site=FixtureSite(covers=True)
            async def route(r):
                if urlsplit(r.request.url).hostname=='covers.test':
                    await r.fulfill(body=data,content_type='application/octet-stream',headers={'content-disposition':'attachment; filename=original.orifile','access-control-allow-origin':'*'})
                else:await site.route(r)
            await page.route('**/*',route)
            await page.add_init_script("localStorage.setItem('noveldb.loadDescriptions','false');localStorage.setItem('noveldb.imageQuality','20')")
            await page.goto('https://metadata.test/#src=nweb');await wait_loaded(page)
            card=page.locator('.novel-card').first
            await card.locator('.card-cover.loaded').wait_for()
            assert await card.locator('.card-cover.loaded').evaluate('img=>img.naturalWidth<=64 && img.naturalHeight<=96')
            await card.evaluate('''card=>{
                window.originalCard=card;window.originalCover=card.querySelector('.card-cover');
                card._record.metrics.test_zero=0;card._record.metrics.test_false=false;
                card._record.metrics.test_html='<img src=x onerror="window.injected=true">';
                window.metadataKeys=Object.keys(card._record).filter(key=>!key.startsWith('_') && !['metadataV1','cover'].includes(key)
                        && (['age','complete'].includes(key) || (card._record[key]!=null && card._record[key]!=='' && (typeof card._record[key]!=='object' || Object.keys(card._record[key]).length))));
            }''')
            await card.locator('.card-cover-wrap').click(button='right',position={'x':10,'y':10})
            await page.get_by_role('menuitem',name='View Details').click()
            dialog=page.get_by_role('dialog');await dialog.wait_for()
            await page.wait_for_function('document.querySelector(".novel-details-image img")?.naturalWidth===800')
            await page.wait_for_function('document.querySelector("[data-field=synopsis]")?.textContent.includes("nweb synopsis")')
            assert await page.evaluate('metadataKeys.every(key=>document.querySelector(`[data-field="${key}"]`))')
            assert 'Test zero' in await dialog.locator('[data-field=metrics]').inner_text()
            assert '<img src=x' in await dialog.locator('[data-field=metrics]').inner_text()
            assert await dialog.locator('[data-field=metrics] img').count()==0
            assert not await page.evaluate('!!window.injected')
            positions=await dialog.locator('[data-field=author],[data-field=source]').evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().top)')
            assert abs(positions[0]-positions[1])<2
            assert not await page.locator('#loadDescriptions').is_checked()
            assert await page.locator('.novel-card .card-synopsis').count()==0
            assert await dialog.evaluate('d=>d.scrollWidth<=d.clientWidth && d.getBoundingClientRect().right<=innerWidth')
            await page.screenshot(path='artifacts/image-details-mobile.png')
            await page.set_viewport_size({'width':1200,'height':900})
            positions=await dialog.locator('[data-field=complete],[data-field=age]').evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().top)')
            assert abs(positions[0]-positions[1])<2
            await page.screenshot(path='artifacts/image-details-desktop.png')
            await page.keyboard.press('Escape')
            assert not await dialog.is_visible()
            await page.wait_for_function('!document.querySelector(".novel-details-image img").hasAttribute("src")')
            assert await page.locator('.novel-details-image img').get_attribute('src') is None
            assert await page.evaluate('originalCard===document.querySelector(".novel-card") && originalCover===originalCard.querySelector(".card-cover")')
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('readable',[True,False])
def test_quality_slider_changes_pixels_and_webp_bytes_without_flicker_and_persists(readable):
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            data=image_bytes('PNG');site=FixtureSite(covers=True)
            async def route(r):
                if urlsplit(r.request.url).hostname=='covers.test':
                    await r.fulfill(body=data,content_type='image/png',headers={'access-control-allow-origin':'*' if readable else 'https://different.test'})
                else:await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.locator('.card-cover.loaded').wait_for()
            assert 'Image Compression' in await page.locator('.image-compression').inner_text()
            assert await page.locator('#imageQuality').get_attribute('min')=='20'
            assert await page.locator('#imageQuality').get_attribute('max')=='100'
            assert await page.locator('.image-compression').evaluate('el=>{const a=el.children[0].getBoundingClientRect(),b=el.children[1].getBoundingClientRect();return Math.abs((a.top+a.bottom-b.top-b.bottom)/2)<4}')
            await page.evaluate('window.originalCard=document.querySelector(".novel-card")')
            sizes=[];pixels=[]
            for quality in (20,50,100):
                await page.evaluate('''quality=>{
                    window.oldCover=document.querySelector('.card-cover.loaded');
                    const slider=document.querySelector('#imageQuality');slider.value=quality;
                    slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true}));
                    if(!oldCover.isConnected || getComputedStyle(oldCover).opacity!=='1') throw new Error('Cover blanked');
                }''',quality)
                await page.wait_for_function('document.querySelector(".card-cover-wrap > .card-cover.loaded")!==oldCover && !document.querySelector(".cover-replacement")')
                info=await page.locator('.card-cover-wrap > .card-cover.loaded').evaluate('''async img=>{
                    const info={width:img.naturalWidth||img.width,height:img.naturalHeight||img.height};
                    if(img.tagName==='IMG') {const blob=await(await fetch(img.src)).blob();info.size=blob.size;info.type=blob.type;}
                    return info;
                }''')
                assert info['width']<=320*quality/100 and info['height']<=480*quality/100
                pixels.append(info['width']*info['height'])
                if readable:
                    assert info['type']=='image/webp';sizes.append(info['size'])
                assert await page.locator('#imageQualityValue').inner_text()==f'{quality}%'
                assert await page.evaluate('originalCard===document.querySelector(".novel-card")')
            assert pixels[0]<pixels[1]<pixels[2]
            assert pixels[0]<pixels[2]*.05  # Roughly 4% of the decoded pixels at 20 vs 100.
            if readable: assert sizes[0]<sizes[1]<sizes[2]
            await page.reload();await wait_loaded(page)
            assert await page.locator('#imageQuality').input_value()=='100'
            assert await page.locator('#imageQualityValue').inner_text()=='100%'
            await page.locator('#lowResCovers').uncheck()
            assert await page.locator('#imageQuality').is_disabled()
            await page.reload();await wait_loaded(page)
            assert not await page.locator('#lowResCovers').is_checked()
            assert await page.locator('#imageQuality').is_disabled()
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('age,expected,adult,teen',[(0,'General',False,False),(19,'19+',True,False),(15,'15+',False,True)])
def test_details_compact_facts_hide_irrelevant_ranks_and_cover_url(age,expected,adult,teen):
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':1200,'height':900})
            await page.route('**/*',FixtureSite().route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.locator('.novel-card').evaluate('''(card,age)=>{
                Object.assign(card._record,{age,complete:0,dailyRank:1,weeklyRank:2,monthlyRank:0,
                    dailyRankAdult:3,weeklyRankAdult:4,monthlyRankAdult:0,
                    dailyRankTeen:5,weeklyRankTeen:6,monthlyRankTeen:0});
            }''',age)
            await page.get_by_role('button',name='View Details').click()
            dialog=page.get_by_role('dialog')
            await dialog.wait_for()
            assert await dialog.locator('[data-field=complete]').inner_text()=='No'
            assert await dialog.locator('[data-field=age]').inner_text()==expected
            assert await dialog.locator('[data-field=cover]').count()==0
            assert await dialog.locator('[data-field=metadataV1]').count()==0
            link=dialog.get_by_role('link',name='Open novel')
            assert await link.get_attribute('href')==await page.locator('.card-cover-wrap').get_attribute('href')
            assert await dialog.locator('[data-field=dailyRankAdult]').count()==int(adult)
            assert await dialog.locator('[data-field=weeklyRankAdult]').count()==int(adult)
            assert await dialog.locator('[data-field=dailyRankTeen]').count()==int(teen)
            assert await dialog.locator('[data-field=weeklyRankTeen]').count()==int(teen)
            assert await dialog.locator('[data-field=monthlyRank]').count()==0
            assert await dialog.locator('[data-field=monthlyRankAdult]').count()==0
            assert await dialog.locator('[data-field=monthlyRankTeen]').count()==0
            assert await dialog.locator('[data-field=rankings]').inner_text()
            positions=await dialog.locator('[data-field=source],[data-field=id]').evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().top)')
            assert abs(positions[0]-positions[1])<2
            assert await dialog.locator('.novel-details-fields').evaluate('el=>el.getBoundingClientRect().height<650')
            await page.get_by_role('button',name='Close Details').click()
            await browser.close()
    asyncio.run(scenario())


def test_blocked_host_keeps_small_canvas_and_displays_original_inside_details():
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
                elif urlsplit(r.request.url).hostname=='wsrv.nl':
                    await r.fulfill(status=503,body='Helper unavailable',headers={'access-control-allow-origin':'*'})
                else: await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.locator('canvas.card-cover.loaded').wait_for()
            assert await page.locator('canvas.card-cover.loaded').evaluate('c=>c.width<=320&&c.height<=480')
            await page.locator('.card-cover-wrap').click(button='right',position={'x':10,'y':10})
            await page.get_by_role('menuitem',name='Download Image').click()
            await page.wait_for_function("document.querySelector('.cover-save-notice').textContent.includes('Image HTTP 503')")
            await page.locator('.cover-save-notice').get_by_role('button',name='View Details').click()
            await page.wait_for_function('document.querySelector(".novel-details-image img")?.naturalWidth===800')
            assert await page.get_by_role('dialog').is_visible()
            await page.get_by_role('dialog').get_by_role('button',name='Download Image').click()
            await page.wait_for_function('document.querySelector(".cover-save-notice")?.closest("dialog") && document.querySelector(".cover-save-notice").textContent.includes("Image HTTP 503")')
            assert await page.locator('.cover-save-notice').is_visible()
            await page.get_by_role('button',name='Close Details').click()
            assert not downloads
            assert await page.get_by_role('button',name='View Details').is_enabled()
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('animated',[False,True])
def test_blocked_image_host_downloads_original_size_lossless_webp_through_helper(tmp_path,animated):
    async def scenario():
        data=image_bytes('PNG')
        stream=io.BytesIO();original=Image.open(io.BytesIO(data))
        extra={'save_all':True,'append_images':[original.transpose(Image.Transpose.FLIP_LEFT_RIGHT)],'duration':100,'loop':0} if animated else {}
        original.save(stream,format='WEBP',lossless=True,**extra)
        fallback=stream.getvalue();helper_requests=[]
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(accept_downloads=True)
            site=FixtureSite(covers=True)
            async def route(r):
                url=urlsplit(r.request.url)
                if url.hostname=='covers.test':
                    await r.fulfill(body=data,content_type='image/png',headers={'access-control-allow-origin':'https://different.test'})
                elif url.hostname=='wsrv.nl':
                    from urllib.parse import parse_qs
                    helper_requests.append(parse_qs(url.query))
                    await r.fulfill(body=fallback,content_type='image/webp',headers={'access-control-allow-origin':'*'})
                else:await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.locator('canvas.card-cover.loaded').wait_for()
            await page.locator('.card-cover-wrap').click(button='right',position={'x':10,'y':10})
            async with page.expect_download() as event:
                await page.get_by_role('menuitem',name='Download Image').click()
            download=await event.value
            assert download.suggested_filename=='npia-7.webp'
            output=tmp_path/'original.webp';await download.save_as(output)
            assert output.read_bytes()==fallback
            assert Image.open(output).size==(800,1200)
            assert getattr(Image.open(output),'n_frames',1)==(2 if animated else 1)
            assert Image.open(output).convert('RGBA').tobytes()==Image.open(io.BytesIO(data)).convert('RGBA').tobytes()
            assert helper_requests==[{'url':['https://covers.test/npia/7.svg'],'output':['webp'],'ll':['1'],'n':['-1']}]
            assert 'image helper' in await page.locator('.cover-save-notice').inner_text()
            assert await page.locator('canvas.card-cover.loaded').evaluate('c=>c.width<=320')
            await browser.close()
    asyncio.run(scenario())
