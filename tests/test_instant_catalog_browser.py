"""Cold-context timing against the shipped million-book index with online requests stalled."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from noveldb.core.catalog.instant import read_piece
from scripts.source_names import dumps
from tests.test_generic_page_merge_browser import browser

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "noveldb/qt/catalog/generic"
TARGETS = [PAGE, ROOT / "android/app/src/main/assets/catalog", ROOT / "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog"]


def open_catalogue(browser, target, rate=6):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": rate})

    def serve(route):
        relative = urlsplit(route.request.url).path.lstrip("/") or "index.html"
        path = target / relative
        if path.is_file() and path.is_relative_to(target):
            route.fulfill(path=path, content_type="application/octet-stream" if relative.endswith((".gz", ".bin")) else None)
        else:
            route.fulfill(status=404, body="")

    page.route("http://instant.test/**", serve)
    page.route("https://**/*", lambda route: route.fulfill(status=503, body=""))
    page.add_init_script("window.NovelDBTransport=()=>new Promise(()=>{});")
    if target == PAGE:
        page.expose_function("localPiece", lambda message: read_piece(message["key"], message.get("token"), message.get("prefix", False), message.get("rows")))
        page.add_init_script("""window.NovelDBQt={postMessage:raw=>{
            const message=JSON.parse(raw);
            if(message.type==='genericInstant')window.localPiece(message).then(data=>window.NovelDBGeneric.fetched(message.id,{data}));
            if(message.type==='genericReady')window.NovelDBGeneric.init({sites:[],comic:[],lnreader:[]});
        }};""")
    page.goto("http://instant.test/index.html")
    if target == PAGE:
        for name in ("references.js", "instant-boot.js", "instant.js", "generic.js"):
            page.add_script_tag(path=target / name)
    page.wait_for_function("!!window.NovelDBGeneric")
    return context, page


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
    context, page = open_catalogue(browser, target)
    page.wait_for_function("document.querySelectorAll('#grid .card .title').length>=12", timeout=3000)
    titles = page.locator("#grid .card .title").all_text_contents()
    assert len(titles) == 12
    assert "Nothing in the top lists" not in page.locator("#empty").inner_text()
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
        unknown:NovelDBInstant.first('zzzznonexistenttitle',{kind:'novel'}),
        excluded:NovelDBInstant.first('mesugaki',{kind:'novel',sites:['unrelated.invalid']})
    })""")
    assert result['prefix'][0]['title'] == 'Mesugaki Tank Enters The Academy'
    assert result['words'][0]['title'] == result['prefix'][0]['title']
    assert result['prefix'][0]['noveldb'] == {'source': 'npia', 'id': '143618'}
    assert not result['comic'] and not result['unknown'] and not result['excluded']
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
