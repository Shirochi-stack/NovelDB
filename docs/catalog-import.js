/* Shared catalog indexing and atomic chunk storage for import workers. */
"use strict";
importScripts("metadata-core.js?v=7");
const M = self.NovelMetadata;
const BLOCK = 512;
let database;

function request(req) { return new Promise((resolve, reject) => { req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); }); }
function transaction(tx) { return new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onabort = tx.onerror = () => reject(tx.error || new Error("Catalog storage transaction failed")); }); }
async function db() {
    if (!database) database = new Promise((resolve, reject) => {
        const req = indexedDB.open("noveldb-catalog-v1", 1);
        req.onupgradeneeded = () => { for (const store of ["meta", "index", "cards", "runs"]) req.result.createObjectStore(store); };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
        req.onblocked = () => reject(new Error("Catalog storage is blocked by another tab"));
    });
    return database;
}
async function get(store, key) { return request((await db()).transaction(store).objectStore(store).get(key)); }

// Authors and tags repeat extensively inside each block. Search blocks omit
// covers, links and synopses; the original rows live in a separate disk store.
function pack(rows, translations, source, cfg) {
    const authors = [], tags = [], authorIds = new Map(), tagIds = new Map();
    const intern = (text, values, ids) => { if (!ids.has(text)) { ids.set(text, values.length); values.push(text); } return ids.get(text); };
    const entries = M.parseRows(rows, source, cfg).map(n => {
        const author = intern(n.author, authors, authorIds);
        const tagList = n.tags.map(t => intern(t.trim(), tags, tagIds));
        const title = n.title, id = n.id;
        delete n.cover; delete n.canonicalUrl; delete n.purchaseUrl; delete n.synopsis; delete n.tier;
        delete n.title; delete n.id; delete n.author; delete n.tags; delete n.source;
        return [id, title, String(translations[id] || ""), author, tagList, n];
    });
    return { authors, tags, entries };
}
function unpack(entry, block, source) {
    return { ...entry[5], id: entry[0], title: entry[1], titleEn: entry[2], author: block.authors[entry[3]],
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
    let stream = new Blob([compressed]).stream();
    compressed = null;
    if (JSON.parse(fingerprint)[1].split('?')[0].endsWith('.gz')) stream = stream.pipeThrough(new DecompressionStream('gzip'));
    const raw = JSON.parse(await new Response(stream).text());
    const rows = raw.novels || raw;
    if (!Array.isArray(rows)) throw new Error('Invalid catalog array');
    const translations = raw.translations || {}, keys = [], groups = new Map();
    // Each parser commits one complete chunk. IndexedDB serializes overlapping
    // write transactions across workers; failures preserve the previous chunk.
    const tx = (await db()).transaction(['index','cards','meta'],'readwrite'), done = transaction(tx);
    let info;
    try {
        if (old) for (const key of old.keys) { tx.objectStore('index').delete(key); tx.objectStore('cards').delete(key); }
        for (let start = 0; start < rows.length; start += BLOCK) {
            const slice = rows.slice(start,start+BLOCK), key = [source,cfg.chunk,start/BLOCK];
            const index = pack(slice,translations,source,cfg);
            const titles = slice.map(row => String(translations[String(row[0])] || ''));
            countTags(index,labels,groups);
            tx.objectStore('index').put(index,key);
            tx.objectStore('cards').put({rows:slice,titles},key);
            keys.push(key);
        }
        info = {fingerprint,keys,total:rows.length,groups:serializeGroups(groups)};
        tx.objectStore('meta').put(info,[source,cfg.chunk]);
    } catch (error) { tx.abort(); await done.catch(()=>{}); throw error; }
    await done;
    return info;
}
