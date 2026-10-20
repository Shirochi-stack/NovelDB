/* Small, local-only diagnostics and application cache controls. */
(function () {
    'use strict';
    const timing = document.getElementById('viewTiming');
    const memory = document.getElementById('memoryUsage');
    let view = null, catalog = null, memoryPending = false, nextPageMemory = 0;

    function updateTiming() {
        const results = view?.elapsed == null ? 'loading…' : `${view.elapsed} ms`;
        const full = catalog?.failed ? 'failed' : catalog?.elapsed == null ? 'loading…' : `${catalog.elapsed} ms`;
        timing.textContent = ` — Results: ${results}${catalog ? ` · Catalog: ${full}` : ''}`;
        timing.hidden = false;
        timing.dataset.elapsedMs = view?.elapsed == null ? '' : String(view.elapsed);
        timing.dataset.catalogMs = catalog?.elapsed == null ? '' : String(catalog.elapsed);
        timing.dataset.source = catalog?.source || '';
    }
    function afterPaint(callback) {
        // A task after the next animation frame observes the first paint
        // opportunity without adding a whole second frame to the reading.
        requestAnimationFrame(() => setTimeout(callback,0));
    }

    function begin(key, started = performance.now()) {
        if (view?.key === key) return;
        view = {key,started,elapsed:null,queued:false};
        updateTiming();
    }
    function sourceStarted(source,key,started = performance.now()) {
        catalog = {source,started,elapsed:null,failed:false};
        // Source activations are new measurements even if their query keys
        // happen to match the previous view (reloads and repeated selections).
        view = null;
        begin(key,started);
        sampleMemory();
        return catalog;
    }
    function catalogReady(token) {
        afterPaint(() => {
            if (catalog !== token || token.elapsed != null || token.failed) return;
            token.elapsed = Math.round(Math.max(0,performance.now()-token.started));
            updateTiming();
            sampleMemory();
        });
    }
    function catalogFailed(token) {
        if (catalog !== token) return;
        token.failed = true;
        updateTiming();
    }
    function painted(key) {
        const current = view;
        if (!current || current.key !== key || current.elapsed != null || current.queued) return;
        current.queued = true;
        afterPaint(() => {
            if (view !== current) return;
            current.elapsed = Math.round(Math.max(0,performance.now()-current.started));
            updateTiming();
        });
    }
    function showMemory(bytes) {
        if (!Number.isFinite(bytes) || bytes < 0) return false;
        memory.textContent = `Page RAM (est.): ${(bytes/1048576).toFixed(1)} MB`;
        memory.title = 'Browser estimate including page JavaScript, DOM and workers. Some allocations may be excluded; exact total tab RAM requires browser or operating-system tools.';
        memory.dataset.scope = 'page-estimate';
        return true;
    }
    async function sampleMemory() {
        if (document.visibilityState === 'hidden') return;
        if (memoryPending || performance.now() < nextPageMemory || !window.crossOriginIsolated
                || typeof performance.measureUserAgentSpecificMemory !== 'function') return;
        memoryPending = true;
        nextPageMemory = performance.now()+60000;
        try {showMemory((await performance.measureUserAgentSpecificMemory()).bytes);}
        catch (_) { /* Keep the explicit unavailable label or last estimate. */ }
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
    window.NovelSiteTools = {begin,painted,sourceStarted,catalogReady,catalogFailed,clearCache,cacheEpoch};
})();
