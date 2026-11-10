import pytest

from tests.test_reader_toc_browser import browser, DESKTOP, MOBILE


@pytest.mark.parametrize("assets", [DESKTOP, *MOBILE], ids=["desktop", "android", "ios"])
@pytest.mark.parametrize("layout", ["paged", "scroll"])
def test_full_contents_arrive_live_without_replacing_current_text_or_loading_ui(browser, assets, layout):
    with browser.new_context(viewport={"width": 390, "height": 800}) as context:
        page = context.new_page()
        page.set_content((assets / "index.html").read_text(encoding="utf-8"))
        page.evaluate("""window.calls=[];window.live={};window.content={};
            window.NovelDBQt={postMessage(text){
                const m=JSON.parse(text);calls.push(m);
                let reply;
                if(m.type==='genericBook')reply={chapters:[{name:'Cover'},{name:'One'},{name:'Two'}]};
                else if(m.type==='genericChapter')reply={html:'<p>'+('Saved chapter text. '.repeat(1500))+'</p>'};
                else if(m.type==='genericLibraryLive') {
                    if(m.key!==undefined) {
                        reply=content[m.key] || {error:'Not ready'};
                        if(window.holdChapter){window.releaseChapter=()=>NovelDBGeneric.fetched(m.id,reply);return;}
                    } else {
                        reply={...live};
                        if(reply.session===m.session && m.after>=1)delete reply.chapters;
                        reply.states=(live.states||[]).filter(s=>m.session!==live.session || s.revision>m.after);
                    }
                }else return;
                setTimeout(()=>NovelDBGeneric.fetched(m.id,reply),0);
            }};""")
        page.add_script_tag(path=assets / "generic.js")
        page.evaluate("NovelDBGeneric.readLocal({url:'file:///library/book.epub',title:'Book',chapter:1})")
        page.wait_for_function("document.getElementById('flow').textContent.includes('Saved chapter text.')")
        if layout == "scroll":
            page.evaluate("document.querySelector('[data-look=scroll]').click()")
            page.locator("#viewport").evaluate("e=>e.scrollTop=350")
        else:
            page.click("#rNext")
        page.wait_for_timeout(100)
        original = page.locator("#flow").inner_html()
        position = page.locator("#viewport").evaluate("e=>e.scrollTop") if layout == "scroll" else page.locator("#rWhere").inner_text()
        page.evaluate("window.mutations=[];new MutationObserver(r=>mutations.push(...r.map(x=>x.type))).observe(document.getElementById('flow'),{childList:true,subtree:true})")
        page.click("#rToc")
        page.evaluate("""window.live={session:'job',version:1,active:true,saved:false,
            chapters:['One','Two','Three','Four'].map((name,i)=>({name,key:String(i),identity:name})),
            states:[0,1,2,3].map(i=>({key:String(i),state:'pending',revision:1}))};
            NovelDBGeneric.refreshLocal('file:///library/book.epub');""")
        page.wait_for_function("document.querySelectorAll('#tocList [data-chapter]').length===5")
        assert page.locator('#tocList [data-chapter="3"]').get_attribute("aria-disabled") == "true"
        page.click('#tocList [data-chapter="3"]')
        assert "open" in page.locator("#toc").get_attribute("class")
        assert page.locator("#flow").inner_html() == original
        assert page.locator("#flow .note").count() == 0
        assert page.locator("#rProgress").is_hidden()
        page.evaluate("""content['2']={html:'<p>Fresh chapter Three.</p>',name:'Three'};
            live.version=2;live.states[2]={key:'2',state:'ready',revision:2};
            NovelDBGeneric.refreshLocal('file:///library/book.epub');""")
        page.wait_for_function("document.querySelector('#tocList [data-chapter=\"3\"]').getAttribute('aria-disabled')==='false'")
        assert page.locator("#flow").inner_html() == original
        after = page.locator("#viewport").evaluate("e=>e.scrollTop") if layout == "scroll" else page.locator("#rWhere").inner_text()
        assert after == position and page.evaluate("mutations.length") == 0
        page.evaluate("window.holdChapter=true")
        page.click('#tocList [data-chapter="3"]')
        page.wait_for_function("!!window.releaseChapter")
        assert page.locator("#flow").inner_html() == original
        page.evaluate("releaseChapter()")
        page.wait_for_function("document.getElementById('flow').textContent.includes('Fresh chapter Three.')")
        assert page.locator("#flow .note").count() == 0
        assert page.locator("#rChapterNo").inner_text() == "Chapter 4 of 5"
        assert page.evaluate("calls.filter(m=>m.type==='genericReaderOpened').length") == 1
        assert not page.evaluate("calls.some(m=>m.type==='genericChapter' && m.index>2)")
        finished_text = page.locator("#flow").inner_html()
        page.evaluate("live.version=3;live.active=false;live.saved=true;live.savedOffset=1;NovelDBGeneric.refreshLocal('file:///library/book.epub')")
        page.wait_for_timeout(200)
        assert page.locator("#flow").inner_html() == finished_text
        assert page.evaluate("calls.filter(m=>m.type==='genericBook').length") == 1
