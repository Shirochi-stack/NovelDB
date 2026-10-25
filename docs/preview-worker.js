/* Decode only the preview profiles being viewed; warm other files on disk. */
'use strict';
self.onmessage=async({data:{source,cfg,url,signature,profile,cacheOnly}})=>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(new Error('Preview download timed out')),15000);
    try {
        let cache;
        try {cache=await caches.open('noveldb-previews-v1');} catch(_) { /* HTTP caching remains available. */ }
        let response=await cache?.match(url),downloaded=!response;
        let compressed;
        for(let attempt=0;attempt<2;attempt++) {
            response ||= await fetch(url,{cache:attempt ? 'no-store' : 'force-cache',priority:cacheOnly?'low':'high',signal:controller.signal});
            if(!response.ok) {await response.body?.cancel();throw new Error(`HTTP ${response.status}`);}
            compressed=await response.arrayBuffer();
            if(!crypto.subtle || [...new Uint8Array(await crypto.subtle.digest('SHA-256',compressed))].map(b=>b.toString(16).padStart(2,'0')).join('')===signature) break;
            if(attempt) throw new Error('Preview checksum mismatch');
            await cache?.delete(url);response=null;downloaded=true;
        }
        // Preserve compressed bytes on disk, not expanded object graphs in the
        // interface. A source/sort change reuses its content-addressed download.
        if(downloaded) try {await cache?.put(url,new Response(compressed));} catch(_) { /* Disk cache is optional. */ }
        if(cacheOnly) {self.postMessage({result:null});return;}
importScripts('metadata-core.js?v=14');
        const stream=new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
        const bundle=JSON.parse(await new Response(stream).text());
        self.postMessage({result:{...self.NovelMetadata.compactPreview(bundle,source,cfg,profile),_signature:signature}});
    } catch(error) {self.postMessage({error:error.message});}
    finally {clearTimeout(timer);}
};
