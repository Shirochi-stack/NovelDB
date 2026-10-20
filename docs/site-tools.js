/* Small, local-only diagnostics and application cache controls. */
(function () {
    'use strict';
    const timing = document.getElementById('viewTiming');
    const memory = document.getElementById('memoryUsage');
    let view = null, memoryPending = false, nextPageMemory = 0;

    function begin(key, started = performance.now()) {
        if (view?.key === key) return;
        view = {key,started,done:false,queued:false};
        timing.hidden = true;
    }
    function painted(key) {
        const current = view;
        if (!current || current.key !== key || current.done || current.queued) return;
        current.queued = true;
        // The second frame includes the browser's first opportunity to paint
        // the cards. Background imports must not replace this measurement.
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (view !== current) return;
            current.done = true;
            const elapsed = Math.round(Math.max(0,performance.now()-current.started));
            timing.textContent = ` — ${elapsed} ms`;
            timing.dataset.elapsedMs = String(elapsed);
            timing.hidden = false;
        }));
    }
    function showMemory(bytes, page) {
        if (!Number.isFinite(bytes) || bytes < 0) return false;
        memory.textContent = `${page ? 'RAM' : 'RAM (JS)'}: ${(bytes/1048576).toFixed(1)} MB`;
        memory.title = page ? 'Browser estimate of page memory, including workers; not the total browser process or GPU memory'
            : 'Approximate JavaScript heap reported by this browser. Images, native memory and separate worker heaps may be excluded; this is not total tab RAM.';
        memory.dataset.scope = page ? 'page' : 'js-heap';
        return true;
    }
    async function sampleMemory() {
        if (document.visibilityState === 'hidden') return;
        if (memory.dataset.scope !== 'page') {
            try {showMemory(performance.memory?.usedJSHeapSize,false);} catch (_) {}
        }
        if (memoryPending || performance.now() < nextPageMemory || !window.crossOriginIsolated
                || typeof performance.measureUserAgentSpecificMemory !== 'function') return;
        memoryPending = true;
        nextPageMemory = performance.now()+60000;
        try {showMemory((await performance.measureUserAgentSpecificMemory()).bytes,true);}
        catch (_) { /* The heap reading or unavailable label remains useful. */ }
        finally {memoryPending=false;}
    }
    sampleMemory();
    setInterval(sampleMemory,5000);
    document.addEventListener('visibilitychange',sampleMemory);

    async function clearCache(blocked) {
        // Only app-owned stores are removed, so other sites on the same
        // hosting origin and the user's saved preferences are preserved.
        let names = ['noveldb-catalog-v1'];
        if (typeof indexedDB.databases === 'function') {
            names = [...new Set([...names,...(await indexedDB.databases()).map(db=>db.name)
                .filter(name=>name?.startsWith('noveldb-'))])];
        }
        await Promise.all(names.map(name=>new Promise((resolve,reject)=>{
            const request = indexedDB.deleteDatabase(name);
            request.onsuccess = resolve;
            request.onerror = () => reject(request.error || new Error('Could not clear catalog storage'));
            request.onblocked = blocked;
        })));
        if ('caches' in window) {
            const names = await caches.keys();
            await Promise.all(names.filter(name=>name.startsWith('noveldb-')).map(name=>caches.delete(name)));
        }
        // Data URLs get a fresh version after reload, bypassing old HTTP
        // responses without claiming access to the browser's entire cache.
        try {localStorage.setItem('noveldb.cacheEpoch',String(Date.now()));} catch (_) {}
    }
    let cacheEpoch = '';
    try {cacheEpoch=localStorage.getItem('noveldb.cacheEpoch') || '';} catch (_) {}
    window.NovelSiteTools = {begin,painted,clearCache,cacheEpoch};
})();
