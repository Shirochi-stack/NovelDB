/* Bounded downloads and application-owned cache maintenance. */
(function () {
    'use strict';
    async function read(url,options={},kind='arrayBuffer') {
        const {timeout=15000,signal,...init}=options, controller=new AbortController();
        const abort=()=>controller.abort(signal.reason);
        signal?.addEventListener('abort',abort,{once:true});
        if(signal?.aborted) abort();
        const timer=setTimeout(()=>controller.abort(new Error('Download timed out')),timeout);
        try {
            const response=await fetch(url,{...init,signal:controller.signal});
            if(kind==='jsonResponse') return {status:response.status,ok:response.ok,value:response.ok ? await response.json() : (await response.body?.cancel(),null)};
            if(!response.ok) {await response.body?.cancel();throw new Error(`HTTP ${response.status}`);}
            return await response[kind]();
        } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
    }
    async function clearCache(visible=[]) {
        const result=await new Promise((resolve,reject)=>{
            const worker=new Worker('cache-worker.js?v=2');
            const timer=setTimeout(()=>{worker.terminate();reject(new Error('Cache cleanup timed out; current page kept'));},40000);
            const finish=(error,result)=>{clearTimeout(timer);worker.terminate();error?reject(error):resolve(result);};
            worker.onmessage=({data})=>{if(data.error)finish(new Error(data.error));else if(data.result)finish(null,data.result);};
            worker.onerror=()=>finish(new Error('Cache cleanup could not start'));
            worker.postMessage({visible});
        });
        if('caches' in window) {
            let timer;
            try {await Promise.race([(async()=>{
                for(const name of await caches.keys()) if(name.startsWith('noveldb-')) {
                    const cache=await caches.open(name);
                    for(const request of await cache.keys()) {
                        const response=await cache.match(request);
                        const size=(await response.blob()).size;
                        if(await cache.delete(request)) result.bytes+=size;
                    }
                    await caches.delete(name);
                }
            })(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Downloaded cache cleanup timed out')),10000);})]);}
            finally {clearTimeout(timer);}
        }
        try {localStorage.setItem('noveldb.cacheEpoch',String(Date.now()));} catch (_) {}
        return result;
    }
    const formatBytes=bytes=>bytes<1024 ? `${bytes} B` : bytes<1048576 ? `${(bytes/1024).toFixed(1)} KB` : `${(bytes/1048576).toFixed(1)} MB`;
    window.NovelSiteTools = {clearCache,read,formatBytes,get cacheEpoch(){
        try{return localStorage.getItem('noveldb.cacheEpoch') || '';}catch(_){return '';}
    }};
})();
