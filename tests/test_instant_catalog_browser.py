"""Cold-context timing against the shipped million-book index with online requests stalled."""
import json
import base64
from zipfile import ZipFile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from noveldb.core.catalog.instant import read_piece
from scripts.source_names import dumps
from tests.test_generic_page_merge_browser import browser

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "noveldb/qt/catalog/generic"
TARGETS = [PAGE, ROOT / "android/app/src/main/assets/catalog", ROOT / "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog"]


def open_catalogue(browser, target, rate=6, hold_configuration=False, saved_top=None, transport=None, viewport=None, installed=True):
    context = browser.new_context(viewport=viewport or {"width": 390, "height": 844})
    page = context.new_page()
    context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": rate})

    pending = []

    def serve(route):
        relative = urlsplit(route.request.url).path.lstrip("/") or "index.html"
        if hold_configuration and relative == "generic_sites.json":
            pending.append(route)
            return
        path = target / relative
        if path.is_file() and path.is_relative_to(target):
            route.fulfill(path=path, content_type="application/octet-stream" if relative.endswith((".gz", ".bin")) else None)
        else:
            route.fulfill(status=404, body="")

    page.route("http://instant.test/**", serve)
    page.route("https://**/*", lambda route: route.fulfill(status=503, body=""))
    page.add_init_script(transport or "window.NovelDBTransport=()=>new Promise(()=>{});")
    def local_piece(message):
        return read_piece(message["key"], message.get("token"), message.get("prefix", False), message.get("rows"), directory=ROOT / "assets")

    def compressed_piece(message):
        key = message["key"]
        part = int(key.split("/")[-1]) % 2 if "/" in key else 0
        with ZipFile(ROOT / f"assets/instant-catalog-{part}.zip") as archive:
            return {"base64": base64.b64encode(archive.read(key + ".json.gz")).decode()}

    page.expose_function("localPiece", local_piece)
    page.expose_function("compressedPiece", compressed_piece)
    page.add_init_script("""const originalTransport=window.NovelDBTransport;
        window.NovelDBTransport=details=>details.op==='catalogData'
            ? details.action==='read'?window.compressedPiece(details):Promise.resolve({version:window.testDataInstalled?'fixture':'',books:100,built:'2026-11-11'})
            :originalTransport(details);""")
    page.add_init_script(f"window.testDataInstalled={str(installed).lower()};")
    page.add_init_script(f"window.holdConfiguration={str(hold_configuration).lower()};")
    if saved_top is not None:
        page.add_init_script(f"""localStorage.setItem('noveldb.generic.top.v9.webnovel.en',
            JSON.stringify({{how:'sites-v2', time:Date.now(), items:{json.dumps(saved_top)}}}));""")
    if target == PAGE:
        page.add_init_script("""window.NovelDBQt={postMessage:raw=>{
            const message=JSON.parse(raw);
            if(message.type==='genericInstant')window.localPiece(message).then(data=>window.NovelDBGeneric.fetched(message.id,{data}));
            if(message.type==='genericCatalogData')window.NovelDBGeneric.fetched(message.id,{version:window.testDataInstalled?'fixture':'',books:100,built:'2026-11-11'});
            if(message.type==='genericReady'&&!window.holdConfiguration)window.NovelDBGeneric.init({sites:[],comic:[],lnreader:[]});
        }};""")
    page.goto("http://instant.test/index.html")
    if target == PAGE:
        for name in ("instant-boot.js", "top-start.js", "instant.js", "generic.js"):
            page.add_script_tag(path=target / name)
    page.wait_for_function("!!window.NovelDBGeneric")
    return context, page


@pytest.mark.parametrize("target", TARGETS, ids=["desktop", "android", "ios"])
def test_catalogue_opens_without_optional_data_or_automatic_download(browser, target):
    context, page = open_catalogue(browser, target, rate=1, installed=False)
    page.locator('#settingsButton').click()
    assert page.locator('#catalogDataAuto').is_checked() is False
    assert page.locator('#catalogDataDownload').inner_text() == 'Download data'
    assert page.locator('#catalogDataRemove').is_disabled()
    assert not (target / 'instant/manifest.bin').exists()
    page.locator('#settingsButton').click()
    page.locator('#query').fill('mesugaki')
    page.wait_for_function("document.querySelectorAll('#grid .card .title').length >= 1")
    assert page.locator('#grid .card .title').count() <= 3
    context.close()


@pytest.mark.parametrize("target", TARGETS, ids=["desktop", "android", "ios"])
def test_real_mesugaki_results_within_one_second_and_canonical_tank_first(browser, target):
    context, page = open_catalogue(browser, target)
    page.evaluate("""()=>{
        window.measure={};
        document.getElementById('query').addEventListener('input',event=>{
            if(event.target.value==='mesugaki')measure.started=performance.now();
        },true);
        new MutationObserver(()=>{
            if(!measure.started||measure.ms!=null||!document.getElementById('heading').textContent.includes('mesugaki'))return;
            const titles=Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent);
            if(titles.filter(title=>/mesugaki/i.test(title)).length>=20){measure.ms=performance.now()-measure.started;measure.titles=titles;}
        }).observe(document.getElementById('grid'),{childList:true,subtree:true});
    }""")
    page.locator("#query").press_sequentially("mesugaki", delay=25)
    page.wait_for_function("measure.ms!=null", timeout=10000)
    result = page.evaluate("measure")
    print(dumps({"target": str(target.relative_to(ROOT)), "cpu_throttle": 6, **result}))
    assert result["ms"] < 1000, result
    assert 20 <= len(result["titles"]) <= 30, result
    assert result["titles"][0] == "Mesugaki Tank Enters The Academy", result
    assert all("mesugaki" in title.lower() for title in result["titles"]), result
    context.close()


@pytest.mark.parametrize("target", TARGETS, ids=["desktop", "android", "ios"])
def test_top_100_appears_while_all_online_services_are_stalled(browser, target):
    context, page = open_catalogue(browser, target, hold_configuration=True)
    assert page.locator("#grid .card .title").count() == 12
    assert page.locator("#status").inner_text() == "Showing saved titles"
    for kind in ("manga", "manhwa", "comic"):
        page.select_option("#kind", kind)
        assert page.locator("#grid .card .title").count() == 12
        assert page.locator("#status").inner_text() == "Showing saved titles"
    assert page.evaluate("typeof window.NovelDBReferences") == "undefined"
    context.close()


@pytest.mark.parametrize("target", TARGETS, ids=["desktop", "android", "ios"])
def test_short_saved_english_top_fills_immediately_and_excludes_old_index_cards(browser, target):
    saved = [dict(url="https://books.test/book/1", site="books.test", source="site", title="Saved English title"),
             dict(url="https://books.test/book/2", site="books.test", source="noveldb", title="Old index title",
                  noveldb={"source": "sfc", "id": "2"})]
    context, page = open_catalogue(browser, target, rate=1, hold_configuration=True, saved_top=saved)
    titles = page.locator("#grid .card .title").all_text_contents()
    assert len(titles) == 12
    assert titles[0] == "Saved English title"
    assert "Old index title" not in titles
    context.close()


def test_wide_catalogue_fills_the_viewport_and_scrolls_with_all_sources_stalled(browser):
    context, page = open_catalogue(browser, PAGE, rate=1, hold_configuration=True,
                                  viewport={"width": 1760, "height": 1430})
    visible_cards = page.locator("#grid .card:visible")
    page.wait_for_function("""() => document.querySelectorAll('#grid .card').length > 12
        && document.scrollingElement.scrollHeight > innerHeight""")
    first = visible_cards.count()
    assert first == page.locator("#grid .card").count()
    lead = visible_cards.first.locator(".title").inner_text()
    page.mouse.move(600, 700)
    page.mouse.wheel(0, 20000)
    page.wait_for_function("(first) => document.querySelectorAll('#grid .card').length > first", arg=first)
    assert visible_cards.count() >= 48
    assert visible_cards.first.locator(".title").inner_text() == lead
    context.close()


@pytest.mark.parametrize("target", TARGETS[1:], ids=["android", "ios"])
def test_scrolling_fetches_a_live_source_next_page(browser, target):
    context, page = open_catalogue(browser, target, rate=1, transport=r"""
        window.popularCalls = [];
        window.slowSourceRequested = false;
        window.NovelDBTransport = async request => {
            if (request.url === 'https://novelfire.net/ranking/most-read') {
                slowSourceRequested = true;
                return new Promise(() => {});
            }
            if (!request.url.startsWith('https://novelbuddy.me/popular')) return {status: 404};
            popularCalls.push(request.url);
            const next = request.url.includes('page=2');
            const titles = ['The Test Hero', 'The Test Mage', 'The Test Swordsman'];
            const cards = titles.map((title, index) => {
                const name = title + (next ? ' Returns' : ' Begins');
                return '<article><a href="/novel/catalogue-fixture-' + (next ? 'next-' : 'first-') + index
                    + '"><img src="/cover.jpg" alt="' + name + '">' + name + '</a></article>';
            }).join('');
            const link = '<a rel="next" href="/popular?page=2">Next</a>';
            return {status: 200, responseText: '<main>' + cards + '</main>' + link};
        };
    """)
    page.wait_for_function("slowSourceRequested && popularCalls.length === 1")
    page.mouse.move(190, 650)
    for _ in range(4):
        page.mouse.wheel(0, 100000)
        page.wait_for_timeout(350)
    page.wait_for_function("popularCalls.some(url => url.includes('page=2'))")
    next_title = page.locator("#grid .title").get_by_text("The Test Hero Returns", exact=True)
    next_title.wait_for()
    assert next_title.count() == 1
    context.close()


@pytest.mark.parametrize("target", TARGETS[1:], ids=["android", "ios"])
def test_mobile_new_uses_the_publisher_transport(browser, target):
    context, page = open_catalogue(browser, target, rate=1, transport=r"""
        window.newFeedCalls = [];
        window.NovelDBTransport = async request => {
            newFeedCalls.push(request.url);
            if (!request.url.includes('/rank/new.html')) return {status:503};
            return {status:200, responseText:'<ul><a href="/b/123/"><li><div id="listtitle">First new novel</div></li></a>'
                + '<a href="/b/124/"><li><div id="listtitle">Second new novel</div></li></a></ul>'};
        };
    """)
    page.click("#settingsButton")
    page.select_option("#language", "zh")
    page.select_option("#sort", "new")
    page.wait_for_function("document.querySelectorAll('#grid .card .title').length===2")
    assert page.locator("#grid .card .title").all_text_contents() == ["First new novel", "Second new novel"]
    assert page.evaluate(r"newFeedCalls.some(url => url === 'https://m.\u0073\u0066\u0061\u0063\u0067.com/rank/new.html')")
    context.close()


@pytest.mark.parametrize("target", TARGETS, ids=["desktop", "android", "ios"])
def test_resident_first_matches_render_within_sixty_ms(browser, target):
    context, page = open_catalogue(browser, target, rate=1)
    assert page.evaluate("NovelDBInstant.ready()"), "Cold page required an asynchronous preload"
    page.wait_for_function("document.querySelectorAll('#grid .card .title').length>=12")
    page.evaluate("""()=>{
        window.firstPass={};
        window.longTasks=[];
        new PerformanceObserver(list=>list.getEntries().forEach(entry=>{
            if(firstPass.started&&entry.startTime>=firstPass.started)longTasks.push(entry.duration);
        })).observe({type:'longtask',buffered:false});
        document.getElementById('query').addEventListener('input',event=>{
            if(event.target.value==='mesugaki')firstPass.started=performance.now();
        },true);
        new MutationObserver(()=>{
            if(!firstPass.started||firstPass.ms!=null||!document.getElementById('heading').textContent.includes('mesugaki'))return;
            const titles=Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent);
            if(titles.length>=1){firstPass.ms=performance.now()-firstPass.started;firstPass.titles=titles;}
        }).observe(document.getElementById('grid'),{childList:true,subtree:true});
    }""")
    page.locator("#query").press_sequentially("mesugaki", delay=25)
    page.wait_for_function("firstPass.ms!=null")
    result = page.evaluate("firstPass")
    print(dumps({"platform": str(target.relative_to(ROOT)), "first_pass": result}))
    assert result["ms"] < 60, result
    assert len(result["titles"]) == 1, result
    assert result["titles"][0] == "Mesugaki Tank Enters The Academy", result
    page.wait_for_function("document.querySelectorAll('#grid .card .title').length>=20")
    assert page.locator("#grid .card .title").first.inner_text() == result["titles"][0]
    page.wait_for_timeout(150)
    assert max(page.evaluate("longTasks"), default=0) < 60
    context.close()


def test_resident_prefixes_and_source_filters_keep_real_identity(browser):
    context, page = open_catalogue(browser, TARGETS[1], rate=1)
    assert page.evaluate("NovelDBInstant.ready()"), "Cold page required an asynchronous preload"
    result = page.evaluate("""()=>({
        prefix:NovelDBInstant.first('mesugak',{kind:'novel'}),
        words:NovelDBInstant.first('mesugaki academy',{kind:'novel'}),
        comic:NovelDBInstant.first('mesugaki',{kind:'manga'}),
        unknownWord:NovelDBInstant.first('constructor academy',{kind:'novel'}),
        unknown:NovelDBInstant.first('zzzznonexistenttitle',{kind:'novel'}),
        excluded:NovelDBInstant.first('mesugaki',{kind:'novel',sites:['unrelated.invalid']})
    })""")
    assert result['prefix'][0]['title'] == 'Mesugaki Tank Enters The Academy'
    assert result['words'][0]['title'] == result['prefix'][0]['title']
    assert result['prefix'][0]['noveldb'] == {'source': 'npia', 'id': '143618'}
    assert not result['comic'] and not result['unknown'] and not result['excluded']
    assert isinstance(result['unknownWord'], list)
    context.close()


@pytest.mark.parametrize("target", TARGETS, ids=["desktop", "android", "ios"])
def test_cold_document_has_immediate_results_without_preload_or_top_wait(browser, target):
    context, page = open_catalogue(browser, target, rate=1)
    result = page.evaluate("""()=>{
        const ready=NovelDBInstant.ready(), plugins=!!window.__LNR_search;
        const started=performance.now();
        const query=document.getElementById('query');
        query.value='mesugaki';query.dispatchEvent(new Event('input',{bubbles:true}));
        return {ready,plugins,elapsed:performance.now()-started,
            titles:Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent)};
    }""")
    assert result['ready'], result
    assert not result['plugins'], 'The plugin bundle blocked initial interaction'
    assert result['elapsed'] < 60, result
    assert result['titles'][0] == 'Mesugaki Tank Enters The Academy', result
    context.close()


def test_mobile_loads_real_plugins_on_demand(browser):
    context, page = open_catalogue(browser, TARGETS[1], rate=1)
    assert not page.evaluate("!!window.__LNR_search")
    page.evaluate("NovelDBQt.postMessage(JSON.stringify({type:'genericPlugins',id:987654}))")
    page.wait_for_function("!!window.__LNR_search")
    assert page.evaluate("JSON.parse(__LNR_sites()).length") > 100
    context.close()
