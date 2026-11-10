"""Reopening replaced EPUBs and dismissing a compact, wrapping contents panel."""

import base64

import pytest

from tests.test_reader_toc_browser import browser, DESKTOP, MOBILE
from tests.test_library_updates import epub


@pytest.mark.parametrize("assets", [DESKTOP, *MOBILE], ids=["desktop", "android", "ios"])
def test_contents_wraps_and_outside_touch_closes_without_turning(browser, assets):
    with browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True) as context:
        page = context.new_page()
        page.set_content((assets / "index.html").read_text(encoding="utf-8"))
        page.evaluate("window.calls=[]; window.NovelDBQt={postMessage:m=>calls.push(JSON.parse(m))}")
        page.add_script_tag(path=assets / "generic.js")
        page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/book.epub',title:'Book'})")
        chapters = [{"name": "A chapter with a very long title that must wrap onto several lines " * 3}, {"name": "Second chapter"}]
        page.evaluate("chapters => NovelDBGeneric.fetched(calls.find(m=>m.type==='genericBook').id,{chapters})", chapters)
        page.evaluate("NovelDBGeneric.fetched(calls.find(m=>m.type==='genericChapter').id,{html:'<p>'+'Chapter text. '.repeat(1500)+'</p>'})")
        page.wait_for_timeout(300)
        page.tap("#rToc")
        panel = page.locator("#toc")
        assert panel.bounding_box()["width"] <= 300
        link = page.locator('#tocList [data-chapter="0"]')
        assert link.bounding_box()["height"] > 70
        assert link.evaluate("e=>e.scrollWidth<=e.clientWidth")
        page.tap("#tocFilter")
        assert "open" in panel.get_attribute("class")
        before = page.locator("#rWhere").inner_text()
        page.tap("#zoneRight", position={"x": 65, "y": 100})
        assert "open" not in panel.get_attribute("class")
        assert page.locator("#rWhere").inner_text() == before
        assert page.evaluate("calls.some(m=>m.type==='genericReaderOpened')")


@pytest.mark.parametrize("assets", MOBILE, ids=["android", "ios"])
def test_reopening_same_epub_address_reads_new_bytes_and_chapters(browser, assets, tmp_path):
    original = base64.b64encode(epub(tmp_path / "old.epub", 2).read_bytes()).decode()
    updated = base64.b64encode(epub(tmp_path / "new.epub", 3).read_bytes()).decode()
    with browser.new_context(viewport={"width": 390, "height": 800}) as context:
        page = context.new_page()
        def serve(route):
            name = route.request.url.rsplit("/", 1)[-1]
            if name in {"index.html", "generic.js", "generic-lib.js", "catalog-app.js"}:
                route.fulfill(path=assets / name)
            elif name.endswith(".json"):
                route.fulfill(json={})
            else:
                route.fulfill(body="")
        page.route("**/*", serve)
        page.add_init_script("""window.fileReads=0;
            window.NovelDBTransport=request=>Promise.resolve(request.op==='file'
                ? (++fileReads,{responseBase64:window.fileBytes}) : {skip:true});""")
        page.goto("https://catalog.test/index.html")
        for data, count in [(original, 2), (updated, 3)]:
            page.evaluate("bytes=>window.fileBytes=bytes", data)
            page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/book.epub',title:'Book'})")
            page.wait_for_function("count=>document.getElementById('rChapterNo').textContent==='Chapter 1 of '+count", arg=count)
            page.click("#rToc")
            assert page.locator("#tocList [data-chapter]").count() == count
            page.click("#rToc")
            page.click("#rClose")
        assert page.evaluate("fileReads") == 2
