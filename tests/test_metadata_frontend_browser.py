"""Real browser checks; every request is fulfilled by local in-memory fixtures."""
import asyncio
import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


DOCS = Path(__file__).resolve().parents[1] / "docs"
SOURCES = ("novelpia", "kakao", "sfacg", "naver", "joara", "munpia", "ridi", "naverseries")


def test_sort_changes_update_url_refresh_and_browser_history():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.route('**/*', FixtureSite(delay_naver=True).route)
            await page.goto('http://metadata.test/#sort=updated&src=novelpia')
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'updated'
            await page.locator('#sortSelect').select_option('daily')
            assert 'sort=' not in page.url and 'src=novelpia' in page.url
            await page.reload()
            await wait_loaded(page)
            assert await page.locator('#sortSelect').input_value() == 'daily'
            await page.locator('#sortSelect').select_option('weekly')
            assert 'sort=weekly' in page.url
            await page.go_back()
            await page.wait_for_function('document.querySelector("#sortSelect").value === "daily"')
            await page.go_forward()
            await page.wait_for_function('document.querySelector("#sortSelect").value === "weekly"')
            await page.locator('#sourceSelect').select_option('naver')
            await page.locator('#sortSelect').select_option('updated')
            await wait_loaded(page)
            assert 'sort=updated' in page.url
            await page.locator('#sortSelect').select_option('daily')
            assert 'sort=updated' not in page.url
            await browser.close()
    asyncio.run(scenario())


def test_joara_and_ridi_use_shared_tags_in_cards_and_filters(monkeypatch):
    original_row = row
    def tagged_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source in ('joara', 'ridi'):
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
            for source in ('joara', 'ridi'):
                await page.goto(f'http://metadata.test/#src={source}')
                await wait_loaded(page)
                chip = page.locator('.card-tag', has_text='Shared translated tag').first
                await chip.click()
                assert 'Shared translated tag' in await page.locator('#activeTagsSummary').inner_text()
                assert await page.locator('.novel-card').count() > 0
            await browser.close()
    asyncio.run(scenario())


def row(source, ident=7, known=False, completed=False):
    if source == "novelpia":
        return [ident, "Novelpia original", "Author", "", [], 100, 8, 3, 0, "2026-01-01", 1, 0, 1, 1, 0, 0, 0, 1, 1, 1]
    if source == "kakao":
        return [ident, "Kakao original", "Author", "", [], 100, 0, 0, 0, "2026-01-01", 0, 0]
    if source == "sfacg":
        return [ident, "SFACG original", "Author", "", [], 100, 7, 9000, 0, "2026-01-01", 19, 1, 0, 0, 0, 0, 0, "", "Chapter", 9, "2026-01-01"]
    url = {"naver": f"https://novel.naver.com/best/list?novelId={ident}",
           "joara": f"https://www.joara.com/book/{ident}",
           "munpia": f"https://www.munpia.com/novel/detail/{ident}", "ridi": f"https://ridibooks.com/books/{ident}", "naverseries": f"https://series.naver.com/novel/detail.series?productNo={ident}"}[source]
    return [str(ident), f"{source} original {ident}", "작가", "", ["판타지"],
            100 if known else None, None, 5 if known else None,
            int(completed) if known else None, "2026-09-13" if known else None,
            0 if known else None, url, "public", None,
            {"favorites": 17, "recommendations": 23} if known else {}, {"native": ident} if known else {}]


class FixtureSite:
    def __init__(self, *, missing=(), broken=(), delay_naver=False, many_novelpia=False, covers=False, coverage=None, coverage_by_source=None):
        self.coverage = coverage
        self.coverage_by_source = coverage_by_source or {}
        self.covers = covers
        self.missing = set(missing)
        self.broken = set(broken)
        self.delay_naver = delay_naver
        self.many_novelpia = many_novelpia
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
        if path in ("/", "/index.html", "/app.js", "/metadata-core.js", "/style.css"):
            name = "index.html" if path == "/" else path.lstrip("/")
            mime = "text/html" if name.endswith("html") else "text/css" if name.endswith("css") else "text/javascript"
            await route.fulfill(status=200, body=(DOCS / name).read_bytes(), content_type=mime)
            return
        name = path.rsplit("/", 1)[-1]
        if name == "tags_en.txt.gz":
            await route.fulfill(body=gzip.compress("판타지|||Fantasy\n".encode()), content_type="application/gzip")
            return
        if name == "tags_extra.json.gz":
            await route.fulfill(body=gzip.compress(json.dumps({"판타지": "Wrong overwrite", "새|||태그": "New tag"}).encode()), content_type="application/gzip")
            return
        source = next((source for source in SOURCES if name.startswith(source + "_")), "novelpia")
        if name.endswith("_chunk_manifest.json"):
            if source in self.missing:
                await route.fulfill(status=404, body="Not published")
                return
            manifest = {"format": "metadata-v1", "files": [f"{source}_chunk_0.json.gz"],
                        "chunks": 1, "totalEntries": 3, "descriptionShardCount": 128,
                        "descriptionShardPrefix": f"{source}_descriptions_shard_", "topUrl": None,
                        "boards": {"native": {"label": "Native board", "observed_at": "2026-09-13T00:00:00Z", "stale": False}},
                        "coverage": self.coverage_by_source.get(source, self.coverage or {"complete": True})}
            await route.fulfill(json=manifest)
            return
        if "_descriptions_shard_" in name or name.startswith("descriptions_shard_"):
            if self.delay_naver and source == "naver":
                await asyncio.sleep(0.3)
            payload = {str(ident): f"{source} synopsis {ident} literal " + chr(92) + "n" + chr(10) + "second line"
                       for ident in (7, 8, 9)}
        elif "_top.json.gz" in name:
            payload = {"novels": [row(source)], "translations": {"7": f"{source} translated"}, "descriptions": {"7": f"{source} synopsis 7"}}
        elif "_chunk_" in name:
            if source in self.broken:
                await route.fulfill(status=503, body="Fixture failure")
                return
            if self.delay_naver and source == "naver":
                await asyncio.sleep(0.4)
            if source in ("naver", "joara", "munpia", "ridi", "naverseries"):
                novels = [row(source), row(source, 8, True, True), row(source, 9, True, False)]
            else:
                novels = [row(source)] if name.endswith("_0.json.gz") else []
                if self.many_novelpia and source == "novelpia" and novels:
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
    await page.wait_for_function("!document.querySelector('#resultCount').textContent.includes('loading') && !document.querySelector('#resultCount').textContent.startsWith('Loading')")


def test_new_sources_r19_badges_audience_filter_and_reload(monkeypatch):
    original_row = row
    new_sources = ("naver", "joara", "munpia", "ridi", "naverseries")
    def age_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source in new_sources:
            result[10] = {7: None, 8: 19, 9: 0}[ident]
            if source == "naverseries":
                result[14]["synopsis_is_preview"] = True
        elif source == "novelpia":
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
            assert set(await page.locator('.novel-card').evaluate_all("cards => cards.map(c => c.dataset.source)")) == {"novelpia", *new_sources}
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
            assert await page.locator('#sourceSelect').input_value() == 'naverseries'
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
            await page.goto("http://metadata.test/")
            await wait_loaded(page)
            assert await page.locator(".novel-card").count() == 18
            identities = await page.locator(".novel-card").evaluate_all("cards => cards.map(c => c.dataset.source + ':' + c.dataset.novelId)")
            assert len(set(identities)) == 18
            assert all(f"{source}:7" in identities for source in SOURCES)
            assert await page.locator('#sourceSelect option[value="naver"]').is_enabled()
            await page.select_option("#sourceSelect", "naver")
            await wait_loaded(page)
            assert await page.locator(".novel-card").count() == 3
            href = await page.locator('.novel-card[data-novel-id="7"] .card-cover-wrap').get_attribute("href")
            assert href == "https://novel.naver.com/best/list?novelId=7"
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
            await page.evaluate("location.hash = 'src=joara&sort=rank%3Ajoara%3Anative'")
            await page.wait_for_function("document.querySelector('#sourceSelect').value === 'joara'")
            await wait_loaded(page)
            assert await page.locator(".novel-card").first.get_attribute("data-source") == "joara"
            assert await page.locator(".novel-card").first.get_attribute("data-novel-id") == "8"
            await page.wait_for_function("document.querySelector('.card-synopsis')?.textContent.includes('joara synopsis')")
            assert chr(92) + "n" in await page.locator(".card-synopsis").first.inner_text()
            assert not errors
            await browser.close()
    asyncio.run(scenario())


def test_metadata_frontend_browser_restores_page_after_small_top_bundle():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(many_novelpia=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=novelpia&page=2")
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
            site = FixtureSite(missing={"joara"}, broken={"munpia"}, delay_naver=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=naver")
            await page.wait_for_function("!document.querySelector('#sourceSelect option[value=naver]').disabled")
            await page.select_option("#sourceSelect", "kakao")
            await wait_loaded(page)
            await page.wait_for_timeout(550)
            assert await page.locator(".novel-card").count() == 1
            assert await page.locator(".novel-card").get_attribute("data-source") == "kakao"
            await page.select_option("#sourceSelect", "all")
            await wait_loaded(page)
            assert await page.locator(".novel-card").count() == 12
            assert await page.locator('#sourceSelect option[value="joara"]').is_disabled()
            assert "Partial results" in await page.locator("#resultCount").inner_text()
            assert "Munpia unavailable" in await page.locator("#resultCount").inner_text()
            assert not errors
            await browser.close()
    asyncio.run(scenario())


def test_progressive_updates_reuse_cards_and_loaded_cover_nodes():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(delay_naver=True, covers=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/")
            await page.wait_for_selector('.novel-card[data-source="novelpia"] img.loaded')
            await page.evaluate("""() => {
                window.savedCard = document.querySelector('.novel-card[data-source="novelpia"]');
                window.savedImage = savedCard.querySelector('img.card-cover');
                window.imageSources = [];
                window.coverObserver = new MutationObserver(events => events.forEach(e => imageSources.push(e.target.getAttribute('src'))));
                coverObserver.observe(savedImage, {attributes: true, attributeFilter: ['src']});
            }""")
            await wait_loaded(page)
            assert await page.evaluate("savedCard === document.querySelector('.novel-card[data-source=novelpia]')")
            assert await page.evaluate("savedImage === savedCard.querySelector('img.card-cover')")
            assert await page.evaluate("imageSources.every(value => !!value)")
            assert await page.locator('#sortSelect').evaluate("el => el.getBoundingClientRect().width") <= 250
            assert await page.locator('#sortSelect').evaluate("el => el.closest('.control-group').nextElementSibling.querySelector('select').id") == 'audienceSelect'
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
            assert not any('_top.json.gz' in url or 'descriptions_shard_' in url for url in site.requests)
            assert await page.locator('.card-synopsis:visible').count() == 0
            await page.check('#loadDescriptions')
            await page.wait_for_selector('.card-synopsis')
            assert any('descriptions_shard_' in url for url in site.requests)
            await page.uncheck('#loadDescriptions')
            assert await page.evaluate("localStorage.getItem('noveldb.loadDescriptions')") == 'false'
            site.requests.clear()
            await page.reload()
            await wait_loaded(page)
            assert not await page.locator('#loadDescriptions').is_checked()
            assert not any('_top.json.gz' in url or 'descriptions_shard_' in url for url in site.requests)
            await browser.close()
    asyncio.run(scenario())


def test_disabling_descriptions_ignores_delayed_shard_responses():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(delay_naver=True)
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=naver")
            await page.wait_for_selector('.novel-card')
            await page.uncheck('#loadDescriptions')
            await page.wait_for_timeout(600)
            assert await page.locator('.card-synopsis').count() == 0
            await page.check('#loadDescriptions')
            await page.wait_for_selector('.card-synopsis')
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
                await page.goto("http://metadata.test/#src=naver")
                await wait_loaded(page)
                assert message in await page.locator('#resultCount').inner_text()
                await page.close()
            await browser.close()
    asyncio.run(scenario())


def test_joara_catalog_diagnostics_explain_legacy_pages_and_clear_on_source_change():
    async def scenario():
        reset = "ValueError: Pagination reset/invalid: requested=101, returned=1, rows=0, total=0, size=0"
        failed_row = "ValueError: Joara catalog row lacks identity or title"
        errors = [
            {"partition": partition, "error": f"Joara {partition} requested page {number}: {reason}"}
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
            site = FixtureSite(coverage_by_source={"joara": {"catalog": {"started": True, "errors": errors}}})
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=joara")
            await wait_loaded(page)
            status = await page.locator('#resultCount').inner_text()
            assert "Joara: 7 catalog scans stopped" in status
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
            await page.select_option('#sourceSelect', 'naver')
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
            site = FixtureSite(coverage_by_source={"joara": coverage})
            await page.route("**/*", site.route)
            await page.goto("http://metadata.test/#src=joara")
            await wait_loaded(page)
            assert "1 catalog scan stopped; 2 invalid catalog rows skipped" in await page.locator('#resultCount').inner_text()
            details = page.locator('#catalogDiagnostics')
            await details.locator('summary').click()
            items = await details.locator('li').all_inner_texts()
            assert "Free publication · Completed — page 14" in items[0]
            assert "This catalog scan stopped at page 14" in items[0]
            assert "Noblesse · Completed — page 75 — row 4 — novel 8123" in items[1]
            assert "other rows were collected and the scan continued" in items[1]
            assert "Omitted listings are checked again on a fresh catalog scan." in items[1]
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
                await page.goto("http://metadata.test/#src=joara")
                await wait_loaded(page)
                await page.wait_for_selector('.card-synopsis')
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


def test_naver_missing_synopses_visible_only_when_loading_disabled(monkeypatch):
    original_row = row
    def marked_row(source, ident=7, known=False, completed=False):
        result = original_row(source, ident, known, completed)
        if source == 'naver':
            result[14]['synopsis_available'] = ident != 7
        return result
    monkeypatch.setitem(globals(), 'row', marked_row)
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            site = FixtureSite(delay_naver=True)
            await page.route('**/*', site.route)
            await page.goto('http://metadata.test/#src=naver')
            await wait_loaded(page)
            missing = page.locator('.novel-card[data-source="naver"][data-novel-id="7"]')
            assert await missing.count() == 0
            await page.uncheck('#loadDescriptions')
            await missing.wait_for()
            await page.reload()
            await wait_loaded(page)
            await missing.wait_for()
            await page.check('#loadDescriptions')
            assert await missing.count() == 0
            assert await page.locator('.novel-card').count() > 0
            await browser.close()
    asyncio.run(scenario())
