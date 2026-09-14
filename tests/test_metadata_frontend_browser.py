"""Real browser checks; every request is fulfilled by local in-memory fixtures."""
import asyncio
import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


DOCS = Path(__file__).resolve().parents[1] / "docs"
SOURCES = ("novelpia", "kakao", "sfacg", "naver", "joara", "munpia")


def row(source, ident=7, known=False, completed=False):
    if source == "novelpia":
        return [ident, "Novelpia original", "Author", "", [], 100, 8, 3, 0, "2026-01-01", 1, 0, 1, 1, 0, 0, 0, 1, 1, 1]
    if source == "kakao":
        return [ident, "Kakao original", "Author", "", [], 100, 0, 0, 0, "2026-01-01", 0, 0]
    if source == "sfacg":
        return [ident, "SFACG original", "Author", "", [], 100, 7, 9000, 0, "2026-01-01", 19, 1, 0, 0, 0, 0, 0, "", "Chapter", 9, "2026-01-01"]
    url = {"naver": f"https://novel.naver.com/best/list?novelId={ident}",
           "joara": f"https://www.joara.com/book/{ident}",
           "munpia": f"https://www.munpia.com/novel/detail/{ident}"}[source]
    return [str(ident), f"{source} original {ident}", "작가", "", ["판타지"],
            100 if known else None, None, 5 if known else None,
            int(completed) if known else None, "2026-09-13" if known else None,
            0 if known else None, url, "public", None,
            {"favorites": 17} if known else {}, {"native": ident} if known else {}]


class FixtureSite:
    def __init__(self, *, missing=(), broken=(), delay_naver=False, many_novelpia=False, covers=False, coverage=None):
        self.coverage = coverage
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
                        "coverage": self.coverage or {"complete": True}}
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
            if source in ("naver", "joara", "munpia"):
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
            assert await page.locator(".novel-card").count() == 12
            identities = await page.locator(".novel-card").evaluate_all("cards => cards.map(c => c.dataset.source + ':' + c.dataset.novelId)")
            assert len(set(identities)) == 12
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
            assert await page.locator(".novel-card").count() == 6
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
                ({"catalog": {"started": True, "errors": [{"partition": "latest", "error": "reset"}]}}, "Some catalog pages unavailable"),
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
