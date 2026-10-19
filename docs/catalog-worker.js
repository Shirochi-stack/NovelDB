/* Disk-backed catalog import and bounded-memory query execution. */
"use strict";
importScripts("metadata-core.js?v=4");
const M = self.NovelMetadata;
const BLOCK = 512, PAGE_CACHE = 3, HEAP_LIMIT = 5000, RUN_BLOCK = 32, FAN_IN = 16;
let database, serial = Promise.resolve(), latestQuery = 0;
const configs = new Map(), blocks = new Map(), pages = new Map();
let runSerial = 0;
let runsReset = false;
let runNamespace = [Date.now(),crypto.getRandomValues(new Uint32Array(4)).join("-")];
const runKey = (id,part) => [...runNamespace,id,part];
async function clearRuns() {
    const tx = (await db()).transaction("runs","readwrite"), done = transaction(tx);
    tx.objectStore("runs").delete(IDBKeyRange.bound([...runNamespace,0,0],[...runNamespace,Infinity,Infinity]));
    await done;
}

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
    for (const entry of index.entries) {
        const seen = new Set();
        for (const id of entry[4]) {
            const tag = index.tags[id], key = tagKey(tag,labels);
            if (seen.has(key)) continue; seen.add(key);
            combineGroups(groups,[[key,1,[[tag,1]]]]);
        }
    }
}
async function importSource(source, cfg, progress, labels) {
    if (!runsReset) {
        const tx = (await db()).transaction("runs","readwrite"), done = transaction(tx);
        tx.objectStore("runs").delete(IDBKeyRange.upperBound([Date.now()-86400000]));
        await done; runsReset = true;
    }
    configs.set(source, cfg);
    const files = cfg.chunked ? cfg.chunkFiles || Array.from({length: cfg.chunkCount}, (_, i) => `${cfg.chunkPrefix}${i}.json.gz`) : [cfg.dataUrl];
    const keys = [], counts = new Map();
    let total = 0;
    const warnings = [];
    for (let chunk = 0; chunk < files.length; chunk++) {
        const url = files[chunk];
        let old;
        try {
            old = await get("meta", [source, chunk]);
            const response = await fetch(url, { cache: "no-cache" });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const validator = response.headers.get("etag") || response.headers.get("last-modified");
            const fingerprint = JSON.stringify([2,url, validator, cfg.format, cfg.coverPrefix, cfg.linkHosts,cfg.tagVersion]);
            let info;
            if (validator && old?.fingerprint === fingerprint) {
                await response.body?.cancel(); info = old;
            } else {
                const stream = url.split("?")[0].endsWith(".gz") ? response.body.pipeThrough(new DecompressionStream("gzip")) : response.body;
                const raw = JSON.parse(await new Response(stream).text());
                const rows = raw.novels || raw;
                if (!Array.isArray(rows)) throw new Error("Invalid catalog array");
                const translations = raw.translations || {}, chunkKeys = [], chunkTags = new Map();
                // Each published chunk is committed atomically with its validator.
                const tx = (await db()).transaction(["index", "cards", "meta"], "readwrite");
                const done = transaction(tx);
                try {
                    if (old) for (const key of old.keys) { tx.objectStore("index").delete(key); tx.objectStore("cards").delete(key); }
                    for (let start = 0; start < rows.length; start += BLOCK) {
                        const slice = rows.slice(start, start + BLOCK), key = [source, chunk, start / BLOCK];
                        const index = pack(slice, translations, source, cfg);
                        const titles = slice.map(r => String(translations[String(r[0])] || ""));
                        countTags(index,labels,chunkTags);
                        tx.objectStore("index").put(index, key);
                        tx.objectStore("cards").put({ rows: slice, titles }, key);
                        chunkKeys.push(key);
                    }
                    info = { fingerprint, keys: chunkKeys, total: rows.length, groups: serializeGroups(chunkTags) };
                    tx.objectStore("meta").put(info, [source, chunk]);
                } catch (error) { tx.abort(); await done.catch(() => {}); throw error; }
                await done;
            }
            keys.push(...info.keys); total += info.total;
            combineGroups(counts, info.groups);
        } catch (error) {
            if (["QuotaExceededError", "SecurityError", "InvalidStateError"].includes(error.name)) throw error;
            warnings.push(`${source}: chunk ${chunk + 1}/${files.length} unavailable`);
            if (old) {
                // A failed refresh must not discard a committed earlier chunk.
                keys.push(...old.keys); total += old.total;
                if (JSON.parse(old.fingerprint).at(-1) !== (cfg.tagVersion ?? null)) {
                    for (const key of old.keys) countTags(await get("index",key),labels,counts);
                } else combineGroups(counts,old.groups);
                warnings.push(`${source}: using last saved chunk ${chunk + 1}`);
            }
        }
        progress({ source, loaded: chunk + 1, total: files.length });
    }
    if (!keys.length && warnings.length) throw new Error(`No ${source} catalog chunks available`);
    if (cfg.totalEntries != null && total !== cfg.totalEntries) warnings.push(`${source}: loaded ${total} of ${cfg.totalEntries} records`);
    blocks.set(source, keys); pages.clear();
    return { total, groups: serializeGroups(counts), warnings };
}
function matches(n, q) {
    if (q.author && n.author !== q.author) return false;
    if (q.text && !(q.matcher.test(n.title) || q.matcher.test(n.titleEn || "") || q.matcher.test(n.author) || n.id === q.text)) return false;
    if (!M.matchesStatus(n, q.status) || !M.matchesAudience(n, q.audience)) return false;
    const has = spellings => n.tags.some(t => spellings.includes(t.trim()));
    return (!q.and.length || q.and.every(has)) && (!q.or.length || q.or.some(has)) && !q.exclude.some(has);
}
function compareKeys(plan) {
    const collator = new Intl.Collator();
    return (a, b) => {
        for (let i = 0; i < plan.length; i++) {
            const k = plan[i], x = a[0][i], y = b[0][i];
            if (k.nullsLast && (x == null || y == null)) { if (x == null && y == null) continue; return x == null ? 1 : -1; }
            const delta = k.text === "collate" ? collator.compare(String(x), String(y))
                : k.text === "plain" ? (String(x) < String(y) ? -1 : String(x) > String(y) ? 1 : 0) : Number(x) - Number(y);
            if (delta) return delta * k.dir;
        }
        return a[1] - b[1];
    };
}
function heapPush(heap, value, compare) {
    heap.push(value); let i = heap.length - 1;
    while (i) { const p = (i - 1) >> 1; if (compare(heap[p], value) <= 0) break; heap[i] = heap[p]; i = p; } heap[i] = value;
}
function heapPop(heap, compare) {
    const first = heap[0], value = heap.pop();
    if (heap.length) { let i = 0; while (i * 2 + 1 < heap.length) {
        let c = i * 2 + 1; if (c + 1 < heap.length && compare(heap[c + 1], heap[c]) < 0) c++;
        if (compare(value, heap[c]) <= 0) break; heap[i] = heap[c]; i = c;
    } heap[i] = value; } return first;
}
async function saveRun(rows) {
    const run = { id: ++runSerial, parts: Math.ceil(rows.length / RUN_BLOCK) };
    const tx = (await db()).transaction("runs", "readwrite"), done = transaction(tx);
    for (let i = 0; i < rows.length; i += RUN_BLOCK) tx.objectStore("runs").put(rows.slice(i, i + RUN_BLOCK), runKey(run.id,i/RUN_BLOCK));
    await done; return run;
}
async function mergeRuns(runs, compare, check) {
    const output = { id: ++runSerial, parts: 0 }, heap = [], cursors = [];
    const cmp = (a, b) => compare(a.value, b.value);
    for (const run of runs) {
        const cursor = { run, part: 0, at: 0, rows: await get("runs", runKey(run.id,0)) }; cursors.push(cursor);
        if (cursor.rows?.length) heapPush(heap, { value: cursor.rows[0], cursor }, cmp);
    }
    let buffer = [];
    while (heap.length) {
        const {value, cursor} = heapPop(heap, cmp); buffer.push(value);
        if (buffer.length === RUN_BLOCK) {
            check(); const tx = (await db()).transaction("runs", "readwrite"), done = transaction(tx);
            tx.objectStore("runs").put(buffer, runKey(output.id,output.parts++)); await done; buffer = [];
        }
        cursor.at++;
        if (cursor.at === cursor.rows.length) { cursor.part++; cursor.at = 0; cursor.rows = cursor.part < cursor.run.parts ? await get("runs", runKey(cursor.run.id,cursor.part)) : null; }
        if (cursor.rows?.length) heapPush(heap, { value: cursor.rows[cursor.at], cursor }, cmp);
    }
    if (buffer.length) { const tx = (await db()).transaction("runs", "readwrite"), done = transaction(tx); tx.objectStore("runs").put(buffer, runKey(output.id,output.parts++)); await done; }
    const tx = (await db()).transaction("runs", "readwrite"), done = transaction(tx);
    for (const run of runs) tx.objectStore("runs").delete(IDBKeyRange.bound(runKey(run.id,0),runKey(run.id,Infinity)));
    await done; return output;
}
async function query(q, token) {
    try { return await executeQuery(q,token); }
    finally { if (q.page*q.size > HEAP_LIMIT) await clearRuns(); }
}
async function executeQuery(q, token) {
    const check = () => { if (token !== latestQuery) throw new DOMException("Superseded query", "AbortError"); };
    check();
    const cacheKey = JSON.stringify(q);
    if (pages.has(cacheKey)) { const value = pages.get(cacheKey); pages.delete(cacheKey); pages.set(cacheKey, value); return value; }
    q = {...q, and:q.and || [], or:q.or || [], exclude:q.exclude || []};
    if (q.text) q.matcher = new RegExp(q.text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "iu");
    const plan = M.sortPlan(q.sort), compare = compareKeys(plan), reverse = (a,b) => -compare(a,b);
    const wanted = q.page * q.size, deep = wanted > HEAP_LIMIT;
    const heap = [], runs = [];
    if (deep) {
        await clearRuns();
        runNamespace = [Date.now(),crypto.getRandomValues(new Uint32Array(4)).join("-")];
    }
    let count = 0, seq = 0, beforeFocus = 0, focusFound = false;
    const focus = q.focus ? [plan.map(k => k.get(q.focus)),q.focus._catalogOrdinal] : null;
    for (const source of q.sources) for (const key of blocks.get(source) || []) {
        check(); const block = await get("index", key), matching = [];
        for (let i = 0; i < block.entries.length; i++, seq++) {
            const n = unpack(block.entries[i], block, source);
            if (!matches(n, q)) continue;
            count++;
            const entry = [plan.map(k => k.get(n)), seq, key, i];
            if (focus) {
                if (compare(entry,focus) < 0) beforeFocus++;
                if (n.id === q.focus.id && n.source === q.focus.source) focusFound = true;
            }
            if (deep) matching.push(entry);
            else if (heap.length < wanted) heapPush(heap, entry, reverse);
            else if (compare(entry, heap[0]) < 0) { heapPop(heap, reverse); heapPush(heap, entry, reverse); }
        }
        if (matching.length) { matching.sort(compare); runs.push(await saveRun(matching)); }
    }
    check();
    if (focusFound && Math.floor(beforeFocus/q.size)+1 !== q.page) return query({...q,page:Math.floor(beforeFocus/q.size)+1,focus:null},token);
    const page = Math.min(q.page, Math.max(1, Math.ceil(count / q.size))), offset = (page - 1) * q.size;
    let chosen;
    if (!deep) { heap.sort(compare); chosen = heap.slice(offset, offset + q.size); }
    else {
        let level = runs;
        while (level.length > 1) { const next = []; for (let i=0;i<level.length;i+=FAN_IN) { check(); next.push(await mergeRuns(level.slice(i,i+FAN_IN),compare,check)); } level = next; }
        chosen = [];
        if (level.length) for (let part = Math.floor(offset/RUN_BLOCK); chosen.length < q.size && part < level[0].parts; part++) {
            const rows = await get("runs", runKey(level[0].id,part)); chosen.push(...rows.slice(part === Math.floor(offset/RUN_BLOCK) ? offset%RUN_BLOCK : 0, offset + q.size - part*RUN_BLOCK));
        }
        await clearRuns();
    }
    check();
    const rows = new Array(chosen.length), groups = new Map();
    chosen.forEach((entry, position) => {
        const identity = JSON.stringify(entry[2]);
        if (!groups.has(identity)) groups.set(identity,[]);
        groups.get(identity).push({entry,position});
    });
    for (const group of groups.values()) {
        const key = group[0].entry[2], block = await get("cards", key);
        for (const {entry,position} of group) {
            const n = M.parseRows([block.rows[entry[3]]], key[0], configs.get(key[0]))[0];
            if (block.titles[entry[3]]) n.titleEn = block.titles[entry[3]];
            n._catalogOrdinal = entry[1];
            rows[position] = n;
        }
    }
    const result = { rows, total:count, page };
    pages.set(cacheKey,result); while (pages.size > PAGE_CACHE) pages.delete(pages.keys().next().value);
    return result;
}
self.onmessage = ({data}) => {
    const {id, type, value} = data;
    if (type === "query") latestQuery = id;
    serial = serial.catch(() => {}).then(async () => {
        try {
            const result = type === "import" ? await importSource(value.source,value.cfg,progress => self.postMessage({id,progress}),value.tagLabels || {}) : await query(value,id);
            self.postMessage({id,result});
        } catch (error) { self.postMessage({id,error:{name:error.name,message:error.message}}); }
    });
};
