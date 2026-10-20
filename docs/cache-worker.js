/* Remove unused catalog blocks without deleting a database open in other tabs. */
'use strict';
self.onmessage = async ({data}) => {
    let db, timer;
    try {
        db = await new Promise((resolve,reject)=>{
            let settled = false;
            const req = indexedDB.open('noveldb-catalog-v1',1);
            timer = setTimeout(()=>{settled=true;reject(new Error('Storage did not open in time'));},8000);
            req.onupgradeneeded = ()=>{for(const name of ['meta','index','cards','runs']) req.result.createObjectStore(name);};
            req.onsuccess = ()=>{clearTimeout(timer);if(settled) req.result.close();else resolve(req.result);};
            req.onerror = ()=>{clearTimeout(timer);reject(req.error);};
        });
        const visible = new Set(data.visible.map(n=>`${n.source}:${n.id}`));
        const kept = new Set(), encoder = new TextEncoder();
        let bytes = 0, entries = 0;
        await new Promise((resolve,reject)=>{
            const tx = db.transaction(['cards','index','meta','runs'],'readwrite');
            timer = setTimeout(()=>tx.abort(),30000);
            tx.oncomplete = ()=>{clearTimeout(timer);resolve();};
            tx.onabort = tx.onerror = ()=>{clearTimeout(timer);reject(tx.error || new Error('Storage cleanup timed out; current page kept'));};
            function scan(name,next) {
                self.postMessage({progress:name});
                const req = tx.objectStore(name).openCursor();
                req.onsuccess = ()=>{
                    const cursor = req.result;
                    if(!cursor) {next?.();return;}
                    const key = JSON.stringify(cursor.key), value = cursor.value;
                    const keep = name==='cards' ? value.rows?.some(row=>visible.has(`${cursor.key[0]}:${row[0]}`))
                        : name==='index' && kept.has(key);
                    if(keep) kept.add(key);
                    else {
                        bytes += encoder.encode(key).byteLength + encoder.encode(JSON.stringify(value) || '').byteLength;
                        entries++; cursor.delete();
                    }
                    cursor.continue();
                };
            }
            // Retained blocks are no longer a complete chunk. Its next import
            // must validate and restore all the missing blocks.
            scan('cards',()=>scan('index',()=>scan('meta',()=>scan('runs'))));
        });
        self.postMessage({result:{bytes,entries}});
    } catch(error) {self.postMessage({error:error.message});}
    finally {clearTimeout(timer);db?.close();}
};
