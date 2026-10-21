"""Compact toolbar behavior, settings focus and saved choices with local fixtures."""
import asyncio

from playwright.async_api import async_playwright

from test_metadata_frontend_browser import FixtureSite, open_settings, close_settings, wait_loaded


def test_compact_toolbar_badges_settings_and_keyboard_keep_catalog_state():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/*', FixtureSite(many_npia=True).route)
            await page.goto('https://metadata.test/#sort=updated')
            await wait_loaded(page)
            assert await page.locator('#sortPickerButton').inner_text() == '◷\nUPD'
            for width in (320, 360, 390, 430):
                await page.set_viewport_size({'width': width, 'height': 568 if width == 320 else 844})
                assert await page.locator('.controls').evaluate('el=>el.getBoundingClientRect().height<260')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                positions = await page.locator('#sourcePickerButton,#statusPickerButton,#audiencePickerButton,#orderPickerButton').evaluate_all('els=>els.map(e=>e.getBoundingClientRect().top)')
                assert max(positions)-min(positions)<1
                assert await page.locator('#settingsButton').evaluate('e=>e.offsetWidth===48 && e.offsetHeight===42')
                await page.evaluate('window.card=document.querySelector(".novel-card")')
                await open_settings(page)
                assert await page.locator('#siteSettingsDialog').evaluate('d=>d.open && d.getBoundingClientRect().bottom<=innerHeight && d.scrollWidth<=d.clientWidth')
                assert await page.get_by_text('Load Synopsis', exact=True).is_visible()
                assert await page.locator('#imageResolution').is_visible()
                assert await page.locator('#imageQuality').is_visible()
                if width in (320, 390):
                    await page.screenshot(path=f'artifacts/toolbar-settings-sheet-{width}.png')
                await page.keyboard.press('Escape')
                await page.wait_for_function('!document.querySelector("#siteSettingsDialog").open')
                assert await page.locator('#settingsButton').evaluate('e=>e===document.activeElement && e.getAttribute("aria-expanded")==="false"')
                assert await page.evaluate('card===document.querySelector(".novel-card")')
                # Every control menu fits; opening another closes the previous one.
                for kind in ('source', 'status', 'audience', 'order', 'batch', 'sort'):
                    await page.locator(f'#{kind}PickerButton').click()
                    menu = page.locator(f'#{kind}PickerMenu')
                    assert await menu.evaluate('m=>{const r=m.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight}')
                assert await page.locator('.source-picker-menu:visible').count() == 1
                await page.keyboard.press('Escape')
            # Compact selected badges keep their full accessible descriptions.
            for value, text in [('general','G'),('r15','15+'),('adult','19+'),('all','All')]:
                await page.locator('#audiencePickerButton').click()
                await page.locator('#audiencePickerMenu').get_by_role('option', name={'general':'General','r15':'Teen Only (R15+)','adult':'Adult Only (19+)','all':'All'}[value], exact=True).click()
                assert await page.locator('#audienceSelect').input_value() == value
                assert (await page.locator('#audiencePickerButton').inner_text()).strip() == text
                assert (await page.locator('#audiencePickerButton').get_attribute('aria-label')).startswith('Audience: ')
            await wait_loaded(page)
            await page.locator('#orderPickerButton').focus()
            await page.keyboard.press('ArrowDown')
            await page.keyboard.press('End')
            await page.keyboard.press('Enter')
            assert await page.locator('#orderSelect').input_value() == 'asc'
            assert 'ASC' in await page.locator('#orderPickerButton').inner_text()
            await page.locator('#tagToggle').click()
            assert await page.locator('#includeTagsPanel').is_visible()
            assert await page.locator('#tagToggle').get_attribute('aria-expanded') == 'true'
            await page.locator('#tagToggle').click()
            assert not await page.locator('#includeTagsPanel').is_visible()
            await page.locator('#tagModeOr').click()
            assert await page.locator('#tagModeOr').get_attribute('aria-pressed') == 'true'
            await open_settings(page)
            await page.locator('#loadDescriptions').uncheck()
            await close_settings(page)
            await page.reload(); await wait_loaded(page)
            assert not await page.locator('#loadDescriptions').is_checked()
            assert not await page.locator('#siteSettingsDialog').evaluate('d=>d.open')
            assert 'UPD' in await page.locator('#sortPickerButton').inner_text()
            assert 'ASC' in await page.locator('#orderPickerButton').inner_text()
            await open_settings(page)
            await page.mouse.click(3, 3)
            await page.wait_for_function('!document.querySelector("#siteSettingsDialog").open')
            await page.set_viewport_size({'width': 1280, 'height': 900})
            assert not await page.locator('#settingsButton').is_visible()
            assert await page.locator('#settingsInline #loadDescriptions').is_visible()
            assert await page.locator('#settingsInline #imageResolution').is_visible()
            assert await page.locator('#includeTagsPanel .tag-header #tagToggle').is_visible()
            assert await page.locator('#excludeTagsPanel .tag-header #excludeToggle').is_visible()
            assert await page.locator('#tagToggle').inner_text() == 'Filter by tags\n▸'
            assert await page.locator('#excludeToggle').inner_text() == 'Exclude tags\n▸'
            assert await page.locator('#includeTagsPanel .tag-mode').is_visible()
            assert not await page.locator('.tag-toolbar').is_visible()
            assert await page.locator('#sortSelect').is_visible()
            assert not await page.locator('#sortPickerButton').is_visible()
            assert await page.locator('label[for=sortSelect]').inner_text() == 'SORT BY'
            await page.screenshot(path='artifacts/toolbar-desktop.png')
            # Moving an open mobile sheet to desktop keeps the same preference nodes.
            await page.evaluate('window.settingsNode=document.querySelector(".site-settings");window.card=document.querySelector(".novel-card")')
            await page.set_viewport_size({'width':390,'height':844})
            await open_settings(page)
            await page.set_viewport_size({'width':1920,'height':1080})
            assert not await page.locator('#siteSettingsDialog').evaluate('d=>d.open')
            assert await page.evaluate('settingsNode===document.querySelector("#settingsInline .site-settings") && card===document.querySelector(".novel-card")')
            assert not await page.locator('#loadDescriptions').is_checked()
            await page.locator('#loadDescriptions').check()
            await page.locator('#tagToggle').click()
            assert await page.locator('#tagSearch').is_visible()
            await page.locator('#excludeToggle').click()
            assert await page.locator('#excludeTagSearch').is_visible()
            await page.screenshot(path='artifacts/toolbar-desktop-inline-1920.png')
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.locator('#tagToggle').get_attribute('aria-expanded') == 'true'
            assert await page.locator('#tagSearch').is_visible()
            await open_settings(page)
            assert await page.locator('#loadDescriptions').is_checked()
            await close_settings(page)
            assert not errors
            await browser.close()
    asyncio.run(scenario())
