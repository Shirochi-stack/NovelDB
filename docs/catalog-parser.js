/* Parse and index one downloaded chunk at a time in each pool worker. */
"use strict";
importScripts("catalog-import.js?v=10");
const labelRequests=new Map();
self.onmessage = async ({data}) => {
    if(data.kind==='labels') {labelRequests.get(data.id)?.(data.labels);labelRequests.delete(data.id);return;}
    const {id,source,cfg,compressed,old,fingerprint,labels,positions,gzip} = data;
    try {
        let result;
        if(positions) {
            result=await selectCardRows(compressed,gzip,positions,source,cfg);
        } else result = await importChunk(source,cfg,compressed,old,fingerprint,labels || (tags=>new Promise(resolve=>{
            labelRequests.set(id,resolve);self.postMessage({id,tags});
        })));
        self.postMessage({id,result});
    } catch (error) { self.postMessage({id,error:{name:error.name,message:error.message}}); }
};
