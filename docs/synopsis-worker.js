/* Fetch and parse visible-card synopses without blocking the interface. */
'use strict';
self.onmessage = async ({data:{id,url,ids}}) => {
    let failure;
    for (let attempt = 0; attempt < 2; attempt++) {
        const controller=new AbortController(), timer=setTimeout(()=>controller.abort(new Error('Synopsis download timed out')),15000);
        try {
            const response = await fetch(url,{credentials:'omit',priority:'low',signal:controller.signal});
            if (!response.ok) {await response.body?.cancel();throw new Error(`HTTP ${response.status}`);}
            if (!response.body || typeof DecompressionStream === 'undefined') throw new Error('Synopsis decompression unavailable');
            const stream = response.body.pipeThrough(new DecompressionStream('gzip'));
            const descriptions = JSON.parse(await new Response(stream).text());
            if (!descriptions || typeof descriptions !== 'object' || Array.isArray(descriptions)) throw new Error('Invalid synopsis shard');
            // Return only requested texts. Entire shards never reach the page
            // and are released once this bounded job finishes.
            const result = Object.fromEntries(ids.map(key=>[key,
                Object.hasOwn(descriptions,key) && typeof descriptions[key] === 'string' ? descriptions[key] : '']));
            self.postMessage({id,result});
            return;
        } catch (error) {
            failure = error;
            if (!attempt) await new Promise(resolve=>setTimeout(resolve,250));
        } finally {clearTimeout(timer);}
    }
    self.postMessage({id,error:failure?.message || 'Synopsis unavailable'});
};
