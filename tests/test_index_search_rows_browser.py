"""A result read from the search index carries what its card holds, as a catalog record would."""
import asyncio
import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

DOCS = Path(__file__).resolve().parents[1] / "docs"
# A card as scripts/noveldb_index/build.cjs writes it: [source, id, title, English title, author, cover,
# tags, views, likes, chapters, complete, address, updated, age, original tags, publication date,
# other sources, rating].
CARD = ["npia", "338235", "원제", "A Dated Work", "작가", "", ["Modern"], 71201, 3578, 47, 1,
        "https://example.test/novel/338235", "2025-05-04", 19, ["현대"], "2025-02-21",
        [["kpage", "9", "https://example.test/9"]], 19]
UNDATED = ["kpage", "12", "다른 원제", "A Dated Sequel", "작가", "", [], 5, 1, 2, 0,
           "https://example.test/12", "2025-01-01", 0, [], "", [], 0]


def test_an_index_result_keeps_its_publication_date():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            pieces = {"manifest.json": json.dumps({"format": 2, "built": "fixture", "buckets": 1, "block": 256, "page": 500}).encode(),
                      "b/0.json.gz": gzip.compress(json.dumps({"dated": {"npia": [0], "kpage": [1]}}).encode()),
                      "c/0.json.gz": gzip.compress(json.dumps([CARD, UNDATED]).encode())}
            async def route(r):
                url = urlsplit(r.request.url)
                if url.hostname == "index.test":
                    name = url.path.lstrip("/")
                    if name == "":
                        await r.fulfill(body='<!doctype html><meta charset="utf-8"><title>index</title>', content_type="text/html")
                    else:
                        await r.fulfill(body=(DOCS / name).read_bytes(), content_type="text/javascript; charset=utf-8")
                    return
                piece = next((body for name, body in pieces.items() if url.path.endswith("/noveldb-index/" + name)), None)
                await r.fulfill(status=200 if piece is not None else 404, body=piece or b"",
                                headers={"access-control-allow-origin": "*"})
            await page.route("**/*", route)
            await page.goto("https://index.test/")
            await page.add_script_tag(url="https://index.test/noveldb-index-search.js")
            found = await page.evaluate("""() => NovelIndexSearch.search({text: 'dated', author: null, and: [], or: [], exclude: [],
                status: 'all', audience: 'all', page: 1, size: 30})""")
            rows = {row["id"]: row for row in found["rows"]}
            assert found["total"] == 2 and set(rows) == {"338235", "12"}
            # The card's date reaches Details as the catalog's own startDate does; a card without one has none.
            assert rows["338235"]["startDate"] == "2025-02-21" and rows["12"]["startDate"] is None
            assert rows["338235"]["also"] == [["kpage", "9", "https://example.test/9"]]
            assert rows["338235"]["age"] == 19 and rows["338235"]["updated"] == "2025-05-04"
            await browser.close()
    asyncio.run(scenario())
