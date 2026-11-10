"""Reopening replaced EPUBs and dismissing a compact, wrapping contents panel."""

import base64
from zipfile import ZipFile

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


@pytest.mark.parametrize("assets", [DESKTOP, *MOBILE], ids=["desktop", "android", "ios"])
@pytest.mark.parametrize("inserted", [False, True])
@pytest.mark.parametrize("layout", ["paged", "scroll"])
def test_live_contents_refresh_preserves_current_text_page_and_position(browser, assets, inserted, layout):
    with browser.new_context(viewport={"width": 390, "height": 800}) as context:
        page = context.new_page()
        page.set_content((assets / "index.html").read_text(encoding="utf-8"))
        page.evaluate("window.calls=[]; window.NovelDBQt={postMessage:m=>calls.push(JSON.parse(m))}")
        page.add_script_tag(path=assets / "generic.js")
        page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/book.epub',title:'Book'})")
        page.evaluate("NovelDBGeneric.fetched(calls.find(m=>m.type==='genericBook').id,{chapters:[{name:'One'},{name:'Two'}]})")
        page.evaluate("NovelDBGeneric.fetched(calls.find(m=>m.type==='genericChapter').id,{html:'<p>'+'Saved text. '.repeat(1500)+'</p>'})")
        page.wait_for_timeout(200)
        if layout == "scroll":
            page.evaluate("document.querySelector('[data-look=scroll]').click()")
            page.locator("#viewport").evaluate("e=>e.scrollTop=350")
            page.wait_for_timeout(50)
        else:
            page.click("#rNext")
        before = page.locator("#flow p").first.inner_html()
        position = page.locator("#viewport").evaluate("e=>e.scrollTop") if layout == "scroll" else page.locator("#rWhere").inner_text()
        page.click("#rToc")
        assert page.evaluate("NovelDBGeneric.refreshLocal('file:///library/book.epub')")
        # A slow refresh leaves the current page readable.
        assert page.locator("#flow p").first.inner_html() == before
        chapters = [{"name": "One"}, {"name": "Two"}, {"name": "Three"}]
        if inserted:
            chapters = [{"name": "Inserted"}, {"name": "One"}, {"name": "Two"}]
        page.evaluate("chapters=>NovelDBGeneric.fetched(calls.filter(m=>m.type==='genericBook').pop().id,{chapters})", chapters)
        page.wait_for_timeout(150)
        assert page.locator("#flow p").first.inner_html() == before
        after = page.locator("#viewport").evaluate("e=>e.scrollTop") if layout == "scroll" else page.locator("#rWhere").inner_text()
        assert after == position
        assert page.locator("#tocList [data-chapter]").count() == 3
        assert page.locator("#rChapterNo").inner_text() == ("Chapter 2 of 3" if inserted else "Chapter 1 of 3")
        page.click("#rToc")
        page.evaluate("NovelDBGeneric.refreshLocal('file:///library/book.epub')")
        page.click("#rClose")
        page.evaluate("NovelDBGeneric.fetched(calls.filter(m=>m.type==='genericBook').pop().id,{chapters:[{name:'One'}]})")
        page.wait_for_timeout(50)
        assert "open" not in (page.locator("#reader").get_attribute("class") or "")
        assert not page.evaluate("NovelDBGeneric.refreshLocal('file:///library/book.epub')")


@pytest.mark.parametrize("assets", MOBILE, ids=["android", "ios"])
@pytest.mark.parametrize("early", [False, True])
def test_live_refresh_reads_replaced_epub_without_reopening_reader(browser, assets, tmp_path, early):
    def encoded(path, count):
        epub(path, count)
        with ZipFile(path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["0.xhtml"] = ('<html><body><p>' + 'Saved chapter text. ' * 1500 + '</p></body></html>').encode()
        with ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return base64.b64encode(path.read_bytes()).decode()
    original, updated = encoded(tmp_path / "old.epub", 2), encoded(tmp_path / "new.epub", 3)
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
            window.NovelDBTransport=request=>{
                if(request.op!=='file')return Promise.resolve({skip:true});
                ++fileReads;
                if(window.holdFirst && fileReads===1)return new Promise(resolve=>window.releaseFile=resolve);
                return Promise.resolve({responseBase64:window.fileBytes});
            };""")
        page.goto("https://catalog.test/index.html")
        page.evaluate("data=>{window.fileBytes=data.bytes;window.holdFirst=data.early}", {"bytes": original, "early": early})
        page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/book.epub',title:'Book'})")
        if early:
            page.wait_for_function("!!window.releaseFile")
            page.evaluate("bytes=>window.fileBytes=bytes", updated)
            assert page.evaluate("NovelDBGeneric.refreshLocal('file:///library/book.epub')")
            page.evaluate("bytes=>releaseFile({responseBase64:bytes})", original)
        else:
            page.wait_for_function("document.getElementById('rChapterNo').textContent==='Chapter 1 of 2'")
            page.click("#rNext")
            before = page.locator("#flow").inner_html()
            position = page.locator("#rWhere").inner_text()
            page.evaluate("bytes=>window.fileBytes=bytes", updated)
            assert page.evaluate("NovelDBGeneric.refreshLocal('file:///library/book.epub')")
        page.wait_for_function("document.getElementById('rChapterNo').textContent==='Chapter 1 of 3'")
        if not early:
            assert page.locator("#flow").inner_html() == before
            assert page.locator("#rWhere").inner_text() == position
        page.click("#rToc")
        assert page.locator("#tocList [data-chapter]").count() == 3
        assert page.evaluate("fileReads") == 2


@pytest.mark.parametrize("assets", [DESKTOP, *MOBILE], ids=["desktop", "android", "ios"])
def test_chapter_chosen_during_refresh_follows_its_name_after_insertion(browser, assets):
    with browser.new_context(viewport={"width": 390, "height": 800}) as context:
        page = context.new_page()
        page.set_content((assets / "index.html").read_text(encoding="utf-8"))
        page.evaluate("window.calls=[]; window.NovelDBQt={postMessage:m=>calls.push(JSON.parse(m))}")
        page.add_script_tag(path=assets / "generic.js")
        page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/book.epub',title:'Book'})")
        page.evaluate("NovelDBGeneric.fetched(calls.find(m=>m.type==='genericBook').id,{chapters:[{name:'One'},{name:'Two'}]})")
        page.evaluate("NovelDBGeneric.fetched(calls.find(m=>m.type==='genericChapter').id,{html:'<p>Saved chapter One.</p>'})")
        page.wait_for_timeout(100)
        page.evaluate("NovelDBGeneric.refreshLocal('file:///library/book.epub')")
        page.click("#rToc")
        page.click('#tocList [data-chapter="1"]')
        assert "Saved chapter One." in page.locator("#flow").inner_text()
        page.evaluate("NovelDBGeneric.fetched(calls.filter(m=>m.type==='genericBook').pop().id,{chapters:[{name:'Inserted'},{name:'One'},{name:'Two'}]})")
        page.wait_for_function("calls.some(m=>m.type==='genericChapter' && m.index===2)")
        page.evaluate("NovelDBGeneric.fetched(calls.filter(m=>m.type==='genericChapter' && m.index===2).pop().id,{html:'<p>Chapter Two.</p>'})")
        page.wait_for_function("document.getElementById('flow').textContent.includes('Chapter Two.')")
        assert page.locator("#rChapterNo").inner_text() == "Chapter 3 of 3"
