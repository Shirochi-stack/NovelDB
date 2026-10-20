/* Application cache controls. */
(function () {
    'use strict';
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
    window.NovelSiteTools = {clearCache,cacheEpoch};
})();
