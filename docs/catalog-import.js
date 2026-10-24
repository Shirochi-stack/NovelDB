/* Shared catalog indexing and atomic chunk storage for import workers. */
"use strict";
importScripts("metadata-core.js?v=13");
const M = self.NovelMetadata;
const BLOCK = 512;
const RANK_FIELDS = ['weeklyRank','monthlyRank','dailyRank','weeklyRankAdult','monthlyRankAdult','dailyRankAdult','weeklyRankTeen','monthlyRankTeen','dailyRankTeen'];
const SFC_FIELDS = ['popularityRank','bestSellerRank','newBooksRank','bookmarksRank','jpRank','ticketRank'];
let database;

function request(req) { return new Promise((resolve, reject) => { req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); }); }
function transaction(tx) { return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = tx.onerror = () => reject(tx.error || new Error("Catalog storage transaction failed")); }); }
async function db() {
    if (!database) database = new Promise((resolve, reject) => {
        let expired=false;
        const timer=setTimeout(()=>{expired=true;database=null;reject(new Error('Catalog storage did not open in time'));},8000);
        const req = indexedDB.open("noveldb-catalog-v1", 2);
        req.onupgradeneeded = () => {
            for (const store of ["meta", "index", "cards", "runs"]) {
                if (!req.result.objectStoreNames.contains(store)) req.result.createObjectStore(store);
                else req.transaction.objectStore(store).clear();
            }
        };
        req.onsuccess = () => {
            clearTimeout(timer);
            if(expired) {req.result.close();return;}
            req.result.onversionchange = () => { req.result.close(); database = null; };
            resolve(req.result);
        };
        req.onerror = () => {clearTimeout(timer);database=null;reject(req.error);};
        req.onblocked = () => {clearTimeout(timer);expired=true;database=null;reject(new Error("Catalog storage is blocked by another tab"));};
    });
    return database;
}
async function rawGet(store,key) {return request((await db()).transaction(store).objectStore(store).get(key));}
async function inflateCatalog(compressed,gzip) {
    let stream=new Blob([compressed]).stream();
    if(gzip) stream=stream.pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
}
async function selectCardRows(compressed,gzip,positions,source,cfg) {
    const raw=await inflateCatalog(compressed,gzip),rows=raw.novels || raw;
    const result=M.parseRows(positions.map(position=>rows[position]),source,cfg);
    result.forEach(n=>{if(raw.translations?.[n.id]) n.titleEn=raw.translations[n.id];});
    return result;
}
async function get(store, key) {
    const value=await rawGet(store,key);
    if(store!=='cards' || !value?.ids) return value;
    const payload=await rawGet('cards',[key[0],key[1],'payload','compact']);
    const raw=await inflateCatalog(payload.compressed,payload.gzip);
    const rows=(raw.novels || raw).slice(value.start,value.start+value.ids.length);
    return {rows,titles:rows.map(row=>String(raw.translations?.[String(row[0])] || ''))};
}

// Authors and tags repeat extensively inside each block. Search blocks omit
// covers, links and synopses; the original rows live in a separate disk store.
function pack(rows, translations, source, cfg) {
    const authors = [], tags = [], authorIds = new Map(), tagIds = new Map();
    const intern = (text, values, ids) => { if (!ids.has(text)) { ids.set(text, values.length); values.push(text); } return ids.get(text); };
    const entries = M.parseRows(rows, source, cfg).map(n => {
        const author = intern(n.author, authors, authorIds);
        const tagList = n.tags.map(t => intern(t.trim(), tags, tagIds));
        // Positional values avoid a property dictionary for every search row.
        // Display-only fields remain in the card store, never in this index.
        const values = [n.views,n.likes,n.chapters,n.complete,n.updated,n.age,n.metadataV1,cfg.sfcRanks ? 1 : source==='npia' ? 2 : 0];
        if (n.metadataV1) values.push(n.metrics,n.rankings);
        else values.push(...(cfg.sfcRanks ? SFC_FIELDS : source==='npia' ? RANK_FIELDS : ['weeklyRank']).map(key=>n[key]));
        values.push(n.startDate || null);
        if (!n.metadataV1 && source === 'kpage') values.push(n.metrics || {});
        return [n.id, n.title, String(translations[n.id] || ""), author, tagList, values];
    });
    return { authors, tags, entries };
}
function unpack(entry, block, source) {
    const values=entry[5];
    // Previously saved object indexes remain readable without a re-download.
    let fields=values;
    if(Array.isArray(values)) {
        fields={views:values[0],likes:values[1],chapters:values[2],complete:values[3],updated:values[4],age:values[5],metadataV1:values[6]};
        if(fields.metadataV1) {fields.metrics=values[8];fields.rankings=values[9];fields.startDate=values[10] || null;}
        else {
            const rankFields=values[7]===1 ? SFC_FIELDS : values[7]===2 ? RANK_FIELDS : ['weeklyRank'];
            rankFields.forEach((key,i)=>{fields[key]=values[8+i];});
            fields.startDate=values[8+rankFields.length] || null;
            if(source==='kpage') fields.metrics=values[9+rankFields.length] || {};
        }
    }
    return { ...fields, id: entry[0], title: entry[1], titleEn: entry[2], author: block.authors[entry[3]],
        tags: entry[4].map(t => block.tags[t]), source };
}
function tagKey(tag, labels) {
    const normalize = s => String(s || "").trim().toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9\u3131-\uD79D\u4E00-\u9FFF]+/g, "");
    const raw = tag.trim().toLowerCase(), label = normalize(labels[tag] || tag);
    if (["하렘", "后宫", "гарем"].includes(raw) || label === "harem") return "group:harem";
    if (["백합", "百合", "橘味", "gl"].includes(raw) || ["yurigl", "glyuri", "girlslove"].includes(label)) return "group:yuri-gl";
    return `label:${label || normalize(tag)}`;
}
function combineGroups(target, groups) {
    for (const [key,count,originals] of groups) {
        if (!target.has(key)) target.set(key,{count:0,originals:new Map()});
        const group = target.get(key); group.count += count;
        for (const [tag,n] of originals) group.originals.set(tag,(group.originals.get(tag)||0)+n);
    }
}
function serializeGroups(groups) { return [...groups].map(([key,g]) => [key,g.count,[...g.originals]]); }
function countTags(index, labels, groups) {
    // Translate each distinct tag once per block rather than for every novel.
    const keys = index.tags.map(tag => tagKey(tag,labels));
    const counts = new Map();
    for (const entry of index.entries) {
        const seen = new Set();
        for (const id of entry[4]) {
            const key = keys[id];
            if (seen.has(key)) continue; seen.add(key);
            counts.set(id,(counts.get(id)||0)+1);
        }
    }
    for (const [id,count] of counts) combineGroups(groups,[[keys[id],count,[[index.tags[id],count]]]]);
}

async function importChunk(source, cfg, compressed, old, fingerprint, labels) {
    const gzip=JSON.parse(fingerprint)[1].split('?')[0].endsWith('.gz');
    const raw = await inflateCatalog(compressed,gzip);
    const rows = raw.novels || raw;
    if (!Array.isArray(rows)) throw new Error('Invalid catalog array');
    const translations = raw.translations || {}, keys = [], groups = new Map();
    if(typeof labels==='function') {
        const tags=new Set();
        for(const row of rows) {
            const rawTags=row?.[4];
            for(const tag of Array.isArray(rawTags) ? rawTags : rawTags && typeof rawTags==='object' ? Object.values(rawTags) : []) {
                if(typeof tag==='string') tags.add(tag.trim());
            }
        }
        labels=await labels([...tags]);
    }
    // Each parser commits one complete chunk. IndexedDB serializes overlapping
    // write transactions across workers; failures preserve the previous chunk.
    const tx = (await db()).transaction(['index','cards','meta'],'readwrite'), done = transaction(tx);
    let info, failure;
    try {
        if (old) for (const key of old.keys) {
            if(key[3]!=='compact') continue; // Older open tabs still use their original blocks.
            tx.objectStore('index').delete(key); tx.objectStore('cards').delete(key);
        }
        // Keep the already-downloaded compressed original once. Card blocks
        // contain only row locations, avoiding a second expanded catalog in
        // the browser's storage buffers. No new download or compression work.
        tx.objectStore('cards').put({compressed,gzip},[source,cfg.chunk,'payload','compact']);
        compressed=null;
        let start=0;
        function enqueue() {
            try {
                if(start>=rows.length) {
                    info={fingerprint,keys,total:rows.length,groups:serializeGroups(groups)};
                    tx.objectStore('meta').put(info,[source,cfg.chunk,'compact']);
                    return;
                }
                // Keep only one block's structured clones in flight per parser.
                // Continue from an IDB callback so the chunk stays one atomic
                // transaction, including rollback of any failed refresh.
                const end=Math.min(start+BLOCK,rows.length), slice=rows.slice(start,end), key=[source,cfg.chunk,start/BLOCK,'compact'];
                const index=pack(slice,translations,source,cfg);
                countTags(index,labels,groups);
                tx.objectStore('index').put(index,key);
                const pending=tx.objectStore('cards').put({ids:slice.map(row=>String(row[0])),start},key);
                keys.push(key);rows.fill(null,start,end);start=end;
                pending.onsuccess=enqueue;
            } catch(error) { failure=error;tx.abort(); }
        }
        enqueue();
    } catch (error) { tx.abort(); await done.catch(()=>{}); throw error; }
    try {await done;} catch(error) {throw failure || error;}
    return info;
}
