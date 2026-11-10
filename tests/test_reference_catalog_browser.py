"""NU/MU metadata, progressive correlation and fallback through the shipped pages."""
import json
import re
from pathlib import Path

import pytest

from tests.test_generic_page_merge_browser import browser

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "noveldb/qt/catalog/generic"
TARGETS = [PAGE, ROOT / "android/app/src/main/assets/catalog", ROOT / "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog"]
FINDER = '''<html><title>Series Finder</title><select id="tags_include"><option value="42">Time Travel</option></select>
<a genreid="8">Action</a><div class="search_main_box_nu"><div class="search_img_nu"><img src="https://covers.test/one.jpg"></div>
<div class="search_body_nu"><div class="search_title"><a href="https://www.novelupdates.com/series/canonical-book/">Canonical Book</a></div>
<div class="search_genre"><a>Action</a></div><p>A catalogue synopsis about an adventure across distant worlds.</p></div></div>
<div class="digg_pagination"><a rel="next" href="?pg=2">Next</a></div></html>'''
DETAIL = '''<html><title>Canonical Book</title><div class="seriestitlenu">Canonical Book</div>
<div id="editassociated">원래 <b>제목</b><br>바뀐 제목<br>Old English Title</div><div id="showauthors"><a>작가</a></div>
<div id="editdescription">A complete catalogue synopsis about an adventure across distant worlds.</div>
<div id="seriesgenre"><a>Action</a></div><div id="showtags"><a>Time Travel</a></div></html>'''
REF = {"key": "nu:/series/canonical-book/", "provider": "NU", "url": "https://www.novelupdates.com/series/canonical-book/",
       "title": "Canonical Book", "aliases": ["Canonical Book"], "authors": [], "tags": ["Action"],
       "description": "A catalogue synopsis about an adventure across distant worlds.", "comic": False, "rank": 0}


def new_tab(browser, target=PAGE):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    html = re.sub(r'<script\b[^>]*src=[^>]+></script>', '', (target / "index.html").read_text(encoding="utf-8"))
    page.route("http://reference.test/**", lambda route: route.fulfill(body=html, content_type="text/html"))
    page.route("https://**/*", lambda route: route.fulfill(body="", content_type="text/plain"))
    page.goto("http://reference.test/")
    page.add_script_tag(content=(target / "references.js").read_text(encoding="utf-8"))
    return context, page


@pytest.fixture(params=TARGETS, ids=["desktop", "android", "ios"])
def catalogue(browser, request):
    context, page = new_tab(browser, request.param)
    page.evaluate("""data => {
        window.__sent = []; window.__searches = []; window.__down = false;
        window.NovelDBReferences.search = async options => {
            window.__searches.push(options);
            if (window.__down) throw new Error('Unavailable');
            return {records: options.page === 1 ? [data.ref] : [], more: options.page === 1, available: true};
        };
        window.NovelDBQt = {postMessage: value => {
            const m = JSON.parse(value); window.__sent.push(m);
            setTimeout(() => {
                let result = {};
                if (m.type === 'genericIndexSort') {
                    result.cards = m.queries.some(q => q.query === '원래 제목' || q.query === '바뀐 제목') ? [
                        {url:'https://novels.test/book/1',title:'Machine Title',originalTitle:'원래 제목',author:'작가',site:'novels.test',source:'noveldb',comic:false},
                        {url:'https://novels.test/book/2',title:'Machine Title',originalTitle:'바뀐 제목',author:'작가',site:'novels.test',source:'noveldb',comic:false},
                        {url:'https://novelmirror.test/book/1',title:'Machine Title',originalTitle:'원래 제목',author:'작가',site:'novelmirror.test',source:'noveldb',comic:false}
                    ] : [];
                } else if (m.type === 'genericFetch') {
                    result = {status:200,responseText:m.request.url.includes('/series/canonical-book/') ? data.detail : '<html><title>Google</title></html>'};
                } else if (m.type === 'genericSiteQuery') result = {cards:[]};
                else if (m.type === 'genericNdbTop') { window.NovelDBGeneric.ndbTopDone(m.id,{more:false}); return; }
                else return;
                window.NovelDBGeneric.fetched(m.id,result);
            }, 0);
        }};
    }""", {"ref": REF, "detail": DETAIL})
    page.add_script_tag(content=(request.param / "generic.js").read_text(encoding="utf-8"))
    page.evaluate("""() => window.NovelDBGeneric.init({sites:['novels.test','novelmirror.test'],comic:[],lnreader:[]})""")
    page.wait_for_selector("#grid .card .title", timeout=15000)
    yield page
    context.close()


def test_native_aliases_correlate_and_same_source_is_listed_once(catalogue):
    page = catalogue
    assert page.locator("#grid .card").count() == 1
    assert page.locator("#grid .title").inner_text() == "Canonical Book"
    assert page.locator("#grid .site").inner_text().endswith("+1")
    assert "complete catalogue synopsis" in page.locator("#grid .card").inner_text()
    assert "Time Travel" in page.locator("#grid .card").inner_text()
    queries = page.evaluate("() => window.__sent.filter(m=>m.type==='genericIndexSort').flatMap(m=>m.queries.map(q=>q.query))")
    assert "원래 제목" in queries and "바뀐 제목" in queries
    page.evaluate("""()=>window.NovelDBGeneric.details({url:'https://novels.test/book/1',title:'Machine Title',
        author:'Site uploader',description:'A replacement source synopsis.',fresh:true,complete:true})""")
    assert page.locator("#grid .title").inner_text() == "Canonical Book"
    assert "complete catalogue synopsis" in page.locator("#grid .card").inner_text()


def test_provider_sort_reruns_catalogue_and_pagination_stays_with_provider(catalogue):
    page = catalogue
    page.select_option("#sort", "title")
    page.wait_for_function("window.__searches.some(q=>q.sort==='title')")
    page.wait_for_selector("#grid .card")
    assert page.evaluate("() => window.__searches.at(-1).sort") == "title"
    page.fill("#query", "Old English Title")
    page.locator("#form").dispatch_event("submit")
    page.wait_for_selector("#grid .card")
    assert page.locator("#grid .title").inner_text() == "Canonical Book"
    assert page.evaluate("() => window.__sent.some(m=>m.type==='genericSearch')")
    page.evaluate("window.scrollTo(0,document.body.scrollHeight);window.dispatchEvent(new Event('scroll'))")
    page.wait_for_function("window.__searches.some(q=>q.page===2)", timeout=15000)
    assert page.evaluate("() => window.__searches.some(q=>q.page===2)")


def test_full_search_is_optional_persisted_and_settings_fit_phone(catalogue):
    page = catalogue
    page.click("#settingsButton")
    assert not page.is_checked("#useFull")
    page.check("#useFull")
    assert page.evaluate("JSON.parse(localStorage.getItem('noveldb.generic.searches')).full")
    labels = page.locator("#settingspanel .checks").inner_text()
    assert "Index" in labels and "Native" in labels and "Full search" in labels
    bounds = page.locator("#settingspanel").bounding_box()
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390


def test_kind_change_restarts_provider_and_native_source_overrides_it(catalogue):
    page = catalogue
    assert page.evaluate("window.__searches[0].kind") == "all"
    page.select_option("#kind", "manhwa")
    page.wait_for_function("window.__searches.some(q=>q.kind==='manhwa')")
    page.select_option("#kind", "webnovel")
    page.click("#settingsButton")
    page.select_option("#preferredSite", "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com")
    page.wait_for_function("window.__sent.some(m=>m.type==='genericNdbTop'&&m.sources.includes('npia'))")
    assert page.evaluate("window.__sent.filter(m=>m.type==='genericNdbTop').at(-1).sources") == ["npia"]


def test_novel_and_comic_adaptations_keep_separate_reading_cards(catalogue):
    page = catalogue
    page.evaluate("""ref=>window.NovelDBGeneric.add([{url:'https://mangareader.test/book/1',title:ref.title,
        author:'작가',site:'mangareader.test',comic:true,complete:true,verified:true,via:'top',
        catalogue:{...ref,key:'mu:77',provider:'MU',comic:true,authors:['작가']}}])""", REF)
    page.wait_for_function("document.querySelectorAll('#grid .card').length===2")
    assert page.locator("#grid .title").all_text_contents() == ["Canonical Book", "Canonical Book"]


def test_provider_failure_uses_fallback_and_filters_western_and_duplicate_arcs(catalogue):
    page = catalogue
    page.evaluate("window.__down=true")
    page.select_option("#kind", "lightnovel")
    page.wait_for_function("document.getElementById('status').textContent.includes('unavailable')")
    cards = [{"url": f"https://rezeronovel.test/book/{i}", "title": "Re:Zero - " + name,
              "description": "An English adventure story about life in a fantasy world.", "site": "rezeronovel.test", "via": "top", "source": "site", "verified": True}
             for i, name in enumerate(["Main Story", "Side Stories", "IF Stories"])]
    cards.append({"url": "https://neobook.org/book/1", "title": "Western Original", "site": "neobook.org", "via": "top"})
    page.evaluate("cards=>window.NovelDBGeneric.add(cards)", cards)
    page.wait_for_timeout(400)
    assert page.locator("#grid .title").all_text_contents() == ["Re:Zero"]
    assert not page.locator("#grid .site").inner_text().endswith("+1")


def test_nu_parser_and_remote_tag_and_sort_parameters(browser):
    context, page = new_tab(browser)
    result = page.evaluate("""async html => {
        const requests=[];
        const io={alive:()=>true,request:async req=>{requests.push(req);return {status:200,responseText:html}}};
        const found=await window.NovelDBReferences.search({kind:'webnovel',text:'Book',tags:['Action','Time Travel'],sort:'views',page:2},io);
        return {requests,found};
    }""", FINDER)
    url = result["requests"][-1]["url"]
    assert "sort=srate" in url and "pg=2" in url and "gi=8" in url and "tgi=42" in url
    assert result["found"]["records"][0]["rank"] == 25
    assert result["found"]["records"][0]["tags"] == ["Action"]
    assert "catalogue synopsis" in result["found"]["records"][0]["description"]
    context.close()


@pytest.mark.parametrize("sort,remote", [("", "list_reading"), ("title", "title"), ("views", "list_reading"), ("updated", "date_added"), ("chapters", "list_reading")])
def test_mu_uses_documented_sort_and_tags(browser, sort, remote):
    context, page = new_tab(browser)
    result = page.evaluate("""async options => {
        let request;
        const io={alive:()=>true,request:async req=>{request=req;return {status:200,responseText:JSON.stringify({page:1,per_page:25,total_hits:1,results:[{record:{series_id:7,title:'A Manhwa',url:'https://www.mangaupdates.com/series/x/a-manhwa',type:'Manhwa',associated:[{title:'원제'}],genres:[{genre:'Action'}],description:'A catalogue synopsis.'}}]})}}};
        const found=await window.NovelDBReferences.search(options,io);
        return {request,found};
    }""", {"kind": "manhwa", "tags": ["Action", "Time Travel"], "sort": sort})
    body = json.loads(result["request"]["data"])
    assert body["orderby"] == remote and body["type"] == ["Manhwa"]
    assert "search" not in body
    assert body["genre"] == ["Action"] and body["category"] == ["Time Travel"]
    assert result["found"]["records"][0]["aliases"] == ["A Manhwa", "원제"]
    context.close()


def test_correlation_rejects_sequels_and_different_authors(browser):
    context, page = new_tab(browser)
    ref = {**REF, "authors": ["작가"], "aliases": ["Canonical Book", "원래 제목"]}
    found = page.evaluate("""ref=>[
        {title:'Canonical Book',author:'작가',url:'https://novels.test/book/1',comic:false},
        {title:'Canonical Book 2',author:'작가',url:'https://novels.test/book/2',comic:false},
        {title:'원래 제목',author:'다른작가',url:'https://novels.test/book/3',comic:false},
        {title:'Canonical Book',author:'작가',url:'https://manga.test/book/4',comic:true}
    ].map(card=>window.NovelDBReferences.matches(ref,card))""", ref)
    assert found == [True, False, False, False]
    context.close()


def test_full_correlation_checks_all_100_sources_and_translator_sites_progressively(browser):
    context, page = new_tab(browser)
    result = page.evaluate("""async original => {
        const ref={...original,groupSites:['https://translator.test/'],groupNames:['Translator Group']};
        const asked=[],emitted=[],queries=[];
        let release;
        const held=new Promise(resolve=>{release=resolve});
        const io={alive:()=>true,web:async query=>{queries.push(query);return {hits:[]}},site:async request=>{
            asked.push(request.host);
            if(request.host==='novel99.test')await held;
            return request.host==='translator.test' ? [{url:'https://translator.test/book/canonical',title:ref.title,comic:false}] : [];
        }};
        const work=window.NovelDBReferences.correlate(ref,{full:true,sites:Array.from({length:100},(_,i)=>'novel'+i+'.test')},io,cards=>emitted.push(...cards));
        while(!asked.includes('novel99.test'))await new Promise(resolve=>setTimeout(resolve,5));
        const early=emitted.length;
        release();await work;
        return {asked,early,queries};
    }""", REF)
    assert result["early"] == 1
    assert len(set(result["asked"])) == 101
    assert any('"Translator Group"' in query for query in result["queries"])
    context.close()


def test_cancelled_correlation_emits_nothing_and_stops_scheduling(browser):
    context, page = new_tab(browser)
    result = page.evaluate("""async ref=>{
        let alive=true,calls=0,emitted=0;
        await window.NovelDBReferences.correlate(ref,{full:true,sites:['novel1.test','novel2.test','novel3.test']},
            {alive:()=>alive,web:async()=>{alive=false;return {hits:[{url:'https://novel1.test/book/a'}]}},site:async()=>{calls++;return []}},
            ()=>{emitted++});
        return {calls,emitted};
    }""", REF)
    assert result == {"calls": 0, "emitted": 0}
    context.close()


@pytest.mark.parametrize("assets", TARGETS[1:], ids=["android", "ios"])
def test_shipped_mobile_adapter_correlates_provider_google_and_reading_site(browser, assets):
    from urllib.parse import urlsplit

    with browser.new_context(viewport={"width": 390, "height": 844}) as context:
        page = context.new_page()

        def serve(route):
            name = urlsplit(route.request.url).path.rsplit("/", 1)[-1]
            if name == "generic_sites.json":
                route.fulfill(json={"sites": [{"host": "novels.test", "site": "novels.test"}]})
            elif name in {"index.html", "generic.js", "references.js", "generic-lib.js", "catalog-app.js"}:
                route.fulfill(path=assets / name)
            elif name.endswith(".json"):
                route.fulfill(json={})
            else:
                route.fulfill(body="")

        page.route("**/*", serve)
        page.add_init_script("""localStorage.setItem('noveldb.generic.searches',JSON.stringify({noveldb:false,deep:false}));
            window.requests=[];
            window.NovelDBTransport=request=>{
                requests.push(request);
                const url=new URL(request.url||'https://empty.test/');
                let html='';
                if(url.hostname==='www.novelupdates.com')html=url.pathname.includes('/series/') ? DETAIL_HTML : FINDER_HTML;
                else if(url.hostname==='api.mangaupdates.com')html=JSON.stringify({results:[],total_hits:0});
                else if(url.hostname==='www.google.com')html='<a href="https://novels.test/book/canonical"><h3>Canonical Book</h3></a>';
                else if(url.hostname==='novels.test')html='<html><head><title>Canonical Book</title><meta property="og:title" content="Canonical Book"></head><body><main><h1>Canonical Book</h1><p>A source synopsis about a long adventure.</p></main></body></html>';
                return Promise.resolve({status:200,finalUrl:url.href,responseText:html});
            };""".replace("DETAIL_HTML", json.dumps(DETAIL)).replace("FINDER_HTML", json.dumps(FINDER)))
        page.goto("https://catalog.test/index.html")
        page.wait_for_selector("#grid .card", timeout=20000)
        assert page.locator("#grid .title").all_text_contents() == ["Canonical Book"]
        assert "complete catalogue synopsis" in page.locator("#grid .card").inner_text()
        urls = page.evaluate("requests.filter(r=>r.url).map(r=>r.url)")
        assert any(urlsplit(url).hostname == "www.google.com" for url in urls)
        assert "https://novels.test/book/canonical" in urls
