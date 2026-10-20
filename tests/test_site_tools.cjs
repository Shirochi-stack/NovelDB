const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../docs/site-tools.js'),'utf8');

function fixture(overrides = {}) {
    let now = 0;
    const frames = [], tasks = [], timers = [];
    const elements = {
        viewTiming:{hidden:true,dataset:{}},
        memoryUsage:{textContent:'Total RAM: unavailable',dataset:{}},
    };
    const context = {window:null,document:{getElementById:id=>elements[id],visibilityState:'visible',addEventListener(){}},
        performance:{now:()=>now},localStorage:{getItem:()=>null},
        requestAnimationFrame:fn=>frames.push(fn),setTimeout:fn=>tasks.push(fn),setInterval:fn=>timers.push(fn),...overrides};
    context.window = context;
    vm.runInNewContext(source,context);
    return {api:context.NovelSiteTools,elements,context,timers,
        frame(time) {now=time;frames.splice(0).forEach(fn=>fn());},
        flush(time) {now=time;tasks.splice(0).forEach(fn=>fn());}};
}

test('result timing includes paint, reports 48 ms, and ignores background completion', () => {
    const f = fixture();
    f.api.begin('npia',0); f.api.painted('npia');
    f.frame(32); f.flush(48);
    assert.equal(f.elements.viewTiming.textContent,' — Results: 48 ms');
    assert.equal(f.elements.viewTiming.hidden,false);
    f.api.begin('npia',2000); f.api.painted('npia'); f.frame(2200);
    assert.equal(f.elements.viewTiming.textContent,' — Results: 48 ms');
});

test('a stale result cannot overwrite timing for a later source or search', () => {
    const f = fixture();
    f.api.begin('old',0); f.api.painted('old'); f.frame(10);
    f.api.begin('new',10); f.api.painted('new'); f.frame(30); f.flush(50);
    assert.equal(f.elements.viewTiming.dataset.elapsedMs,'40');
});

test('a JavaScript heap reading is never substituted for total RAM', async () => {
    const heap = fixture({performance:{now:()=>0,memory:{usedJSHeapSize:20971520}}});
    assert.equal(heap.elements.memoryUsage.textContent,'Total RAM: unavailable');
    assert.equal(fixture().elements.memoryUsage.textContent,'Total RAM: unavailable');
    heap.context.document.visibilityState='hidden';
    heap.context.performance.memory.usedJSHeapSize=41943040;
    await heap.timers[0]();
    assert.equal(heap.elements.memoryUsage.textContent,'Total RAM: unavailable');
});

test('page memory uses the broader API only when isolation permits and survives failures', async () => {
    const page = fixture({crossOriginIsolated:true,performance:{now:()=>0,
        measureUserAgentSpecificMemory:async()=>({bytes:52428800})}});
    await new Promise(setImmediate);
    assert.equal(page.elements.memoryUsage.textContent,'Page RAM (est.): 50.0 MB');
    assert.match(page.elements.memoryUsage.title,/DOM and workers/);
    const denied = fixture({crossOriginIsolated:true,performance:{now:()=>0,memory:{usedJSHeapSize:1048576},
        measureUserAgentSpecificMemory:async()=>{throw new Error('Denied');}}});
    await new Promise(setImmediate);
    assert.equal(denied.elements.memoryUsage.textContent,'Total RAM: unavailable');
});

test('source changes reset both clocks, even for repeated query keys, and ignore old completions', () => {
    const f=fixture();
    const old=f.api.sourceStarted('npia','same',0);
    f.api.painted('same');f.frame(10);f.flush(12);
    f.api.catalogReady(old);f.frame(20);f.flush(22);
    assert.equal(f.elements.viewTiming.dataset.catalogMs,'22');
    const next=f.api.sourceStarted('npia','same',1000);
    assert.equal(f.elements.viewTiming.dataset.elapsedMs,'');
    assert.equal(f.elements.viewTiming.dataset.catalogMs,'');
    f.api.painted('same');f.frame(1030);f.flush(1048);
    f.api.catalogReady(old);f.frame(1100);f.flush(1110);
    assert.equal(f.elements.viewTiming.dataset.catalogMs,'');
    f.api.catalogReady(next);f.frame(2990);f.flush(3000);
    assert.equal(f.elements.viewTiming.textContent,' — Results: 48 ms · Catalog: 2000 ms');
    assert.equal(f.elements.viewTiming.dataset.source,'npia');
    f.api.sourceStarted('sfc','sfc',3100);
    assert.equal(f.elements.viewTiming.dataset.source,'sfc');
    f.api.catalogFailed(next);
    assert.match(f.elements.viewTiming.textContent,/Catalog: loading/);
});
