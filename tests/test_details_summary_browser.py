"""Shared detail summaries on phones and desktop, using local catalog fixtures."""
import asyncio

import pytest
from playwright.async_api import async_playwright

import test_metadata_frontend_browser as fixtures
from test_metadata_frontend_browser import FixtureSite, SOURCES, wait_loaded


@pytest.mark.parametrize('width', [320, 390, 1200])
def test_details_promote_ratings_and_format_dates_in_both_render_phases(width):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': width, 'height': 900})
            await page.route('**/*', FixtureSite(covers=True).route)
            for source in SOURCES:
                await page.goto(f'https://metadata.test/#src={source}')
                await wait_loaded(page)
                card = page.locator('.novel-card').first
                await card.evaluate('''card=>{
                    Object.assign(card._record,{
                        updated:'2024-04-26T18:01:10+09:00',
                        startDate:'2014-12-08T14:21:24+09:00',
                        metrics:{rating:9.6,rating_scale:10,rating_count:2997067},
                        synopsis:'A short synopsis.'
                    });
                    window.NovelDetailsData=card=>new Promise(resolve=>{
                        window.finishDetails=()=>resolve({...card._record,
                            synopsis:'Loaded synopsis.',
                            updated:'2024-04-26T00:01:10+09:00',
                            metrics:{rating:4.59,rating_scale:5,rating_count:42}
                        });
                    });
                }''')
                await card.locator('.cover-details').click()
                dialog = page.get_by_role('dialog')

                async def check(rating):
                    assert await dialog.locator('[data-field=rating]').inner_text() == rating
                    assert await dialog.locator('.novel-details-facts dd').first.get_attribute('data-field') == 'rating'
                    assert await dialog.locator('[data-field=metrics]').count() == 0
                    assert 'Other details' not in await dialog.inner_text()
                    assert 'Rating count' not in await dialog.inner_text()
                    assert 'Rating scale' not in await dialog.inner_text()
                    assert await dialog.locator('[data-field=startDate]').inner_text() == '8 Dec 2014'
                    assert await dialog.locator('[data-field=updated]').inner_text() == '26 Apr 2024'
                    assert await dialog.locator('[data-field=startDate] time').get_attribute('datetime') == '2014-12-08'
                    sizes = await dialog.evaluate('''d=>['updated','startDate','author'].map(key=>
                        parseFloat(getComputedStyle(d.querySelector(`[data-field=${key}]`)).fontSize))''')
                    assert sizes[0] == sizes[1] < sizes[2]
                    assert await dialog.evaluate('d=>d.scrollWidth<=d.clientWidth && d.getBoundingClientRect().right<=innerWidth')
                    assert await dialog.locator('.novel-details-facts').evaluate('e=>e.scrollWidth<=e.clientWidth')

                await check('9.6/10')
                if source == 'kpage':
                    await dialog.screenshot(path=f'artifacts/details-ui/summary-{width}.png')
                await page.evaluate('finishDetails()')
                await page.wait_for_function('document.querySelector("[data-field=synopsis]")?.textContent==="Loaded synopsis."')
                await check('4.5/5')
                await page.get_by_role('button', name='Close Details').click()
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('metrics,expected', [({}, None), ({'rating': None}, None),
    ({'rating': 'invalid'}, None), ({'rating': 0, 'rating_scale': 10}, '0/10'),
    ({'rating': 9.6}, '9.6')])
def test_details_missing_ratings_and_invalid_dates(metrics, expected):
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite().route)
            await page.goto('https://metadata.test/')
            await wait_loaded(page)
            await page.locator('.novel-card').first.evaluate('''(card,metrics)=>{
                Object.assign(card._record,{metrics,updated:'not a date',startDate:'2024-02-30'});
                window.NovelDetailsData=async card=>card._record;
            }''', metrics)
            await page.locator('.cover-details').first.click()
            dialog = page.get_by_role('dialog')
            rating = dialog.locator('[data-field=rating]')
            if expected is None:
                assert await rating.count() == 0
            else:
                assert await rating.inner_text() == expected
            for field in ('updated', 'startDate'):
                assert await dialog.locator(f'[data-field={field}]').inner_text() == 'Not available'
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('width', [320, 1200])
def test_npia_publication_date_survives_catalog_worker_and_details(monkeypatch, width):
    original_row = fixtures.row

    def dated_row(source, ident=7, known=False, completed=False):
        row = original_row(source, ident, known, completed)
        if source == 'npia':
            row.append('2025-02-04T21:24:15')
        return row
    monkeypatch.setattr(fixtures, 'row', dated_row)

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': width, 'height': 900})
            await page.route('**/*', FixtureSite().route)
            await page.goto('https://metadata.test/')
            for source in ('npia', 'all'):
                if source == 'all':
                    await page.select_option('#sourceSelect', source)
                await wait_loaded(page)
                card = page.locator('.novel-card[data-source=npia]').first
                assert await card.evaluate('card=>card._record.startDate') == '2025-02-04T21:24:15'
                await card.locator('.cover-details').click()
                date = page.get_by_role('dialog').locator('[data-field=startDate]')
                assert await date.inner_text() == '4 Feb 2025'
                await page.wait_for_function('!document.querySelector("[data-field=synopsis]")?.textContent.includes("Loading")')
                assert await date.inner_text() == '4 Feb 2025'
                await page.get_by_role('button', name='Close Details').click()
            await browser.close()
    asyncio.run(scenario())
