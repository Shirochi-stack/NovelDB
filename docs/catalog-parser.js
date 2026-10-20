/* Parse and index one downloaded chunk at a time in each pool worker. */
"use strict";
importScripts("catalog-import.js?v=6");
self.onmessage = async ({data}) => {
    const {id,source,cfg,compressed,old,fingerprint,labels} = data;
    try {
        const result = await importChunk(source,cfg,compressed,old,fingerprint,labels);
        self.postMessage({id,result});
    } catch (error) { self.postMessage({id,error:{name:error.name,message:error.message}}); }
};
