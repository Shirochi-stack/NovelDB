"""Check original downloads, image details and thumbnail compression with local images."""
import asyncio
import gzip
import io
import json
from urllib.parse import urlsplit

import pytest
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from test_metadata_frontend_browser import open_settings, close_settings, FixtureSite, SOURCES, row, wait_loaded


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


def test_details_translate_tags_in_both_render_phases_without_changing_filter_values():
    async def scenario():
        raw_tags=['판타지','퓨전펑크','추가검증태그','미등록검증태그']
        labels=['Fantasy','Fusion Punk','Extra translated tag','미등록검증태그']
        site=FixtureSite()
        async def route(r):
            name=urlsplit(r.request.url).path.rsplit('/',1)[-1]
            if name=='tags_en.txt.gz':
                await r.fulfill(body=gzip.compress('퓨전펑크|||Fusion Punk\n'.encode()),content_type='application/gzip')
            elif name=='tags_extra.json.gz':
                await r.fulfill(body=gzip.compress(json.dumps({'추가검증태그':'Extra translated tag'}).encode()),content_type='application/gzip')
            elif '_chunk_' in name and name.endswith('.json.gz'):
                source=name.split('_')[0]
                novel=row(source);novel[4]=raw_tags.copy()
                await r.fulfill(body=gzip.compress(json.dumps({'novels':[novel]}).encode()),content_type='application/gzip')
            else:
                await site.route(r)
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.evaluate('''()=>{
                const original=window.NovelDetailsData;
                window.NovelDetailsData=async(...args)=>{
                    const record=await original(...args);
                    await new Promise(resolve=>window.finishDetails=resolve);
                    record.synopsis='Completed details synopsis';
                    return record;
                };
            }''')
            for source in SOURCES:
                if source!='npia':
                    await page.select_option('#sourceSelect',source);await wait_loaded(page)
                card=page.locator('.novel-card').first
                assert await card.locator('.card-tag').all_text_contents()==labels
                await card.locator('.cover-details').click()
                dialog=page.get_by_role('dialog');await dialog.wait_for()
                assert await dialog.locator('[data-field=tags] .details-tag').all_text_contents()==labels
                await page.wait_for_function('typeof window.finishDetails==="function"')
                await page.evaluate('finishDetails();delete window.finishDetails')
                await page.wait_for_function('document.querySelector("[data-field=synopsis]")?.textContent==="Completed details synopsis"')
                assert await dialog.locator('[data-field=tags] .details-tag').all_text_contents()==labels
                assert await card.evaluate('card=>card._record.tags')==raw_tags
                await page.get_by_role('button',name='Close Details').click()
                assert await card.locator('.card-tag').evaluate_all('tags=>tags.map(tag=>tag.dataset.tag)')==raw_tags
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


def test_mobile_details_and_settings_fit_small_phones_without_outer_scrolling():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True)
            site=FixtureSite(covers=True);data=image_bytes('PNG')
            async def route(r):
                if urlsplit(r.request.url).hostname=='covers.test':
                    await r.fulfill(body=data,content_type='image/png',headers={'access-control-allow-origin':'*'})
                else:await site.route(r)
            await page.route('**/*',route)
            await page.goto('https://metadata.test/');await wait_loaded(page)
            await page.evaluate('''()=>{
                const card=document.querySelector('.novel-card');
                Object.assign(card._record,{
                    titleEn:'I Became the Blacksmith the Top Rankers Are Obsessed Over',
                    title:'A long original title with several words to check mobile wrapping',
                    author:'Example author',complete:0,age:0,updated:'2026-10-20 12:34:56',views:9803195,likes:621028,chapters:146,
                    dailyRank:1,weeklyRank:2,monthlyRank:3,tags:['Fantasy','Crafting','Blacksmith','Obsession']
                });
                window.NovelDetailsData=card=>new Promise(resolve=>{window.finishDetails=(long=false)=>resolve({
                    ...card._record,synopsis:long ? 'A long synopsis. '.repeat(250) : 'A short synopsis, visible together with the metadata.',metrics:{extra:'Additional metadata'}
                });});
            }''')
            for width,height in [(320,568),(360,640),(390,844),(430,932)]:
                await page.set_viewport_size({'width':width,'height':height})
                await page.evaluate('scrollTo({top:0,behavior:"instant"})')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not await page.locator('#siteSettingsDialog').evaluate('dialog=>dialog.open')
                assert await page.locator('.search-row').evaluate('el=>el.getBoundingClientRect().height<230')
                positions=await page.locator('#sourcePickerButton,#statusPickerButton').evaluate_all('nodes=>nodes.map(n=>n.getBoundingClientRect().top)')
                assert abs(positions[0]-positions[1])<2
                if width in (320,390):await page.screenshot(path=f'artifacts/compact-settings-{width}.png')
                badge=page.locator('.cover-details').first
                assert await badge.evaluate('el=>el.offsetWidth===32 && el.offsetHeight===32 && el.querySelector("img").width===22')
                await badge.click()
                dialog=page.get_by_role('dialog')
                await page.wait_for_function('document.querySelector(".novel-details-image img").naturalWidth===800')
                async def fits():
                    assert await dialog.evaluate('''d=>{
                        const r=d.getBoundingClientRect(),b=d.querySelector('.novel-details-body');
                        return r.top>=0 && r.bottom<=innerHeight && r.right<=innerWidth
                            && d.scrollHeight<=d.clientHeight && d.scrollWidth<=d.clientWidth
                            && b.scrollHeight<=b.clientHeight;
                    }''')
                await fits()
                for field in ('author','source','id','complete','age','updated','views','likes','chapters'):
                    assert await dialog.locator(f'[data-field={field}]').is_visible()
                assert await dialog.get_by_role('link',name='Open novel').is_visible()
                assert await dialog.get_by_role('tab').count()==0
                assert await dialog.get_by_role('tablist').count()==0
                await page.evaluate('finishDetails()')
                await page.wait_for_function('document.querySelector("[data-field=synopsis]").textContent.startsWith("A short synopsis")')
                assert await dialog.locator('[data-field=synopsis]').is_visible()
                assert 'Additional metadata' in await dialog.locator('[data-field=metrics]').inner_text()
                assert await dialog.get_by_role('link',name='Open novel').is_visible()
                await fits()
                assert await dialog.evaluate('el=>el.getBoundingClientRect().height<600')
                await page.screenshot(path=f'artifacts/compact-details-{width}.png')
                # Rotation keeps the same metadata visible in the desktop layout.
                if width==390:
                    await page.set_viewport_size({'width':1200,'height':900})
                    assert await dialog.locator('[data-field=synopsis]').is_visible()
                    assert await dialog.locator('[data-field=metrics]').is_visible()
                    assert await dialog.get_by_role('tab').count()==0
                    await page.set_viewport_size({'width':width,'height':height});await fits()
                await page.get_by_role('button',name='Close Details').click()
                assert await badge.evaluate('b=>b===document.activeElement')
            # Unusually long text stays available without expanding the dialog off-screen.
            await page.set_viewport_size({'width':390,'height':640})
            await page.locator('.cover-details').first.click();await page.evaluate('finishDetails(true)')
            await page.wait_for_function('document.querySelector("[data-field=synopsis]").textContent.length>3000')
            synopsis=page.locator('[data-field=synopsis]')
            assert await synopsis.is_visible()
            assert await synopsis.evaluate('el=>el.scrollHeight>el.clientHeight && el.clientHeight<=150')
            await fits()
            await page.get_by_role('button',name='Close Details').click()
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('readable',[True,False])
def test_resolution_and_webp_quality_are_independent_without_flicker_and_persist(readable):
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
            for ident in ('imageResolution','imageQuality'):
                assert await page.locator('#'+ident).get_attribute('min')=='20'
                assert await page.locator('#'+ident).get_attribute('max')=='100'
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await page.evaluate('window.originalCard=document.querySelector(".novel-card")')
            async def adjust(ident,value):
                await page.evaluate('''({ident,value})=>{
                    window.oldCover=document.querySelector('.card-cover-wrap > .card-cover.loaded');
                    const slider=document.getElementById(ident);slider.value=value;
                    slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true}));
                    if(!oldCover.isConnected || getComputedStyle(oldCover).opacity!=='1') throw new Error('Cover blanked');
                }''',{'ident':ident,'value':value})
                await page.wait_for_function('document.querySelector(".card-cover-wrap > .card-cover.loaded")!==oldCover && !document.querySelector(".cover-replacement")')
                assert await page.locator('#'+ident+'Value').inner_text()==f'{value}%'
                assert await page.evaluate('originalCard===document.querySelector(".novel-card")')
                return await page.locator('.card-cover-wrap > .card-cover.loaded').evaluate('''async img=>{
                    const info={width:img.naturalWidth||img.width,height:img.naturalHeight||img.height};
                    if(img.tagName==='IMG') {const blob=await(await fetch(img.src)).blob();info.size=blob.size;info.type=blob.type;}
                    return info;
                }''')
            pixels=[]
            for resolution in (20,50,100):
                info=await adjust('imageResolution',resolution)
                assert info['width']<=320*resolution/100 and info['height']<=480*resolution/100
                pixels.append(info['width']*info['height'])
                assert await page.locator('#imageQuality').input_value()=='72'
            assert pixels[0]<pixels[1]<pixels[2] and pixels[0]<pixels[2]*.05
            sizes=[]
            for quality in (20,50,100):
                info=await adjust('imageQuality',quality)
                assert info['width']*info['height']==pixels[2]
                assert await page.locator('#imageResolution').input_value()=='100'
                if readable:
                    assert info['type']=='image/webp';sizes.append(info['size'])
            if readable: assert sizes[0]<sizes[1]<sizes[2]
            await adjust('imageResolution',50)
            assert await page.locator('#imageQuality').input_value()=='100'
            await open_settings(page)
            await page.locator('.image-compression').screenshot(path=f'artifacts/separate-image-sliders-{readable}.png')
            await close_settings(page)
            await page.reload();await wait_loaded(page)
            assert await page.locator('#imageResolution').input_value()=='50'
            assert await page.locator('#imageQuality').input_value()=='100'
            await open_settings(page)
            await page.locator('#lowResCovers').uncheck()
            await close_settings(page)
            for ident in ('imageResolution','imageQuality'):assert await page.locator('#'+ident).is_disabled()
            await page.reload();await wait_loaded(page)
            assert not await page.locator('#lowResCovers').is_checked()
            for ident in ('imageResolution','imageQuality'):assert await page.locator('#'+ident).is_disabled()
            await page.wait_for_function('document.querySelector(".card-cover.loaded")?.naturalWidth===800')
            await browser.close()
    asyncio.run(scenario())


def test_combined_slider_preferences_migrate_once_without_recoupling_on_reload():
    async def scenario():
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            page=await browser.new_page()
            await page.route('**/*',FixtureSite().route)
            await page.add_init_script('''if(!localStorage.getItem('migrationSeeded')) {
                localStorage.setItem('noveldb.imageQuality','35');localStorage.setItem('migrationSeeded','true');
            }''')
            await page.goto('https://metadata.test/');await wait_loaded(page)
            assert await page.locator('#imageResolution').input_value()=='35'
            assert await page.locator('#imageQuality').input_value()=='35'
            await page.locator('#imageQuality').evaluate("slider=>{slider.value=85;slider.dispatchEvent(new Event('input',{bubbles:true}));slider.dispatchEvent(new Event('change',{bubbles:true}));}")
            await page.reload();await wait_loaded(page)
            assert await page.locator('#imageResolution').input_value()=='35'
            assert await page.locator('#imageQuality').input_value()=='85'
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
