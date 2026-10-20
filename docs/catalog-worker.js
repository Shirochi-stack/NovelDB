/* Disk-backed catalog import and bounded-memory query execution. */
"use strict";
importScripts("catalog-import.js?v=3");
const PAGE_CACHE = 3, HEAP_LIMIT = 5000, RUN_BLOCK = 32, FAN_IN = 16, DOWNLOADS = 8, PARSERS = 4;
let serial = Promise.resolve(), latestQuery = 0;
const configs = new Map(), blocks = new Map(), pages = new Map();
const revisions = new Map();
const readySources = new Map();
let savedPages;
let parserPool;
const totals = new Map();
function createParserPool() {
    const slots = Array.from({length:PARSERS},()=>({worker:null,job:null})), queue = [];
    let sequence = 0, closed = false;
    function finish(slot,error,result) {
        const job = slot.job; slot.job = null;
        // Release this chunk's heap before the slot takes another chunk.
        slot.worker?.terminate(); slot.worker = null;
        if (job) error ? job.reject(error) : job.resolve(result);
        pump();
    }
    function pump() {
        if (closed) return;
        for (const slot of slots) {
            if (slot.job || !queue.length) continue;
            if (!slot.worker) {
                try {
                    const worker = slot.worker = new Worker('catalog-parser.js?v=3');
                    worker.onmessage = ({data}) => {
                        if (closed || slot.worker !== worker || data.id !== slot.job?.id) return;
                        finish(slot,data.error ? Object.assign(new Error(data.error.message),{name:data.error.name}) : null,data.result);
                    };
                    worker.onerror = event => {
                        if (closed || slot.worker !== worker) return;
                        event.preventDefault(); worker.terminate(); slot.worker = null;
                        finish(slot,new Error('Catalog parser could not load'));
                    };
                } catch (error) { for (const job of queue.splice(0)) job.reject(error); return; }
            }
            slot.job = queue.shift();
            try { slot.worker.postMessage({id:slot.job.id,...slot.job.value},[slot.job.value.compressed]); }
            catch (error) { finish(slot,error); }
        }
    }
    return {
        parse(value) { return new Promise((resolve,reject)=>{
            if (closed) { reject(new DOMException('Import cancelled','AbortError')); return; }
            queue.push({id:++sequence,value,resolve,reject}); pump();
        }); },
        close() {
            closed = true;
            const error = new DOMException('Import cancelled','AbortError');
            for (const job of queue.splice(0)) job.reject(error);
            for (const slot of slots) { slot.worker?.terminate(); slot.job?.reject(error); slot.worker = slot.job = null; }
        },
    };
}
let runSerial = 0;
let runsReset = false;
let runNamespace = [Date.now(),crypto.getRandomValues(new Uint32Array(4)).join("-")];
const runKey = (id,part) => [...runNamespace,id,part];
async function clearRuns() {
    const tx = (await db()).transaction("runs","readwrite"), done = transaction(tx);
    tx.objectStore("runs").delete(IDBKeyRange.bound([...runNamespace,0,0],[...runNamespace,Infinity,Infinity]));
    await done;
}

async function importSource(source, cfg, progress, labels) {
    if (!runsReset) {
        const tx = (await db()).transaction("runs","readwrite"), done = transaction(tx);
        tx.objectStore("runs").delete(IDBKeyRange.upperBound([Date.now()-86400000]));
        await done; runsReset = true;
    }
    configs.set(source, cfg);
    const files = cfg.chunked ? cfg.chunkFiles || Array.from({length: cfg.chunkCount}, (_, i) => `${cfg.chunkPrefix}${i}.json.gz`) : [cfg.dataUrl];
    const keys = [], fingerprints = [], counts = new Map();
    let total = 0;
    const warnings = [];
    // At most eight chunks are in flight; four parser workers consume their
    // compressed buffers. Results are merged in source order, not finish order.
    const downloads = new Map(), controller = new AbortController();
    async function download(chunk) {
        let old;
        try {
            old = await get("meta", [source, chunk]);
            const url = files[chunk];
            // Plain HTTP preview servers may lack the checksum API. They keep
            // the original per-file validation path instead of failing to load.
            const hash = crypto.subtle ? cfg.chunkHashes?.[chunk] : null;
            const signature = version => JSON.stringify([3,url, version, cfg.format, cfg.coverPrefix, cfg.linkHosts,cfg.purchaseHosts,cfg.sfcRanks,cfg.tagVersion]);
            const hashedFingerprint = hash ? signature(hash) : null;
            // The freshly fetched manifest identifies unchanged content without
            // revalidating or downloading each catalog chunk on repeat visits.
            if (hashedFingerprint && old?.fingerprint === hashedFingerprint) return { old, info: old };
            const response = await fetch(url, { cache: "no-cache", signal: controller.signal });
            if (!response.ok) { await response.body?.cancel(); throw new Error(`HTTP ${response.status}`); }
            const validator = response.headers.get("etag") || response.headers.get("last-modified");
            const fingerprint = hashedFingerprint || signature(validator || crypto.getRandomValues(new Uint32Array(4)).join("-"));
            if (!hash && validator && old?.fingerprint === fingerprint) {
                await response.body?.cancel();
                return { old, info: old };
            }
            const compressed = await response.arrayBuffer();
            if (hash) {
                const digest = await crypto.subtle.digest("SHA-256",compressed);
                const actual = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2,"0")).join("");
                if (actual !== hash) throw new Error("Catalog chunk does not match its manifest");
            }
            if (!parserPool) parserPool = createParserPool();
            const info = await parserPool.parse({source,cfg:{...cfg,chunk},old,fingerprint,compressed,labels});
            return { old, info };
        } catch (error) { return { old, error }; }
    }
    const schedule = chunk => { if (chunk < files.length) downloads.set(chunk, download(chunk)); };
    for (let chunk = 0; chunk < DOWNLOADS; chunk++) schedule(chunk);
    try {
        for (let chunk = 0; chunk < files.length; chunk++) {
            let old;
            try {
                const downloaded = await downloads.get(chunk);
                downloads.delete(chunk);
                old = downloaded.old;
                if (downloaded.error) throw downloaded.error;
                const info = downloaded.info;
                keys.push(...info.keys); total += info.total;
                fingerprints.push(info.fingerprint);
                combineGroups(counts, info.groups);
            } catch (error) {
                if (["QuotaExceededError", "SecurityError", "InvalidStateError"].includes(error.name)) throw error;
                warnings.push(`${source}: chunk ${chunk + 1}/${files.length} unavailable (${error.message})`);
                if (old) {
                    // A failed refresh must not discard a committed earlier chunk.
                    keys.push(...old.keys); total += old.total;
                    fingerprints.push(old.fingerprint);
                    if (JSON.parse(old.fingerprint).at(-1) !== (cfg.tagVersion ?? null)) {
                        for (const key of old.keys) countTags(await get("index",key),labels,counts);
                    } else combineGroups(counts,old.groups);
                    warnings.push(`${source}: using last saved chunk ${chunk + 1}`);
                }
            }
            progress({ source, loaded: chunk + 1, total: files.length });
            schedule(chunk + DOWNLOADS);
        }
    } finally {
        controller.abort();
        downloads.clear();
        // Release parsed-chunk heaps between sources, including on failures.
        parserPool?.close(); parserPool = null;
    }
    if (!keys.length && warnings.length) throw new Error(`No ${source} catalog chunks available: ${warnings.join("; ")}`);
    if (cfg.totalEntries != null && total !== cfg.totalEntries) warnings.push(`${source}: loaded ${total} of ${cfg.totalEntries} records`);
    blocks.set(source, keys); pages.clear();
    revisions.set(source,JSON.stringify(fingerprints));
    totals.set(source,total);
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
    const collator = new Intl.Collator('en');
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
    parserPool?.close(); parserPool = null;
    try { return await executeQuery(q,token); }
    finally { if (q.page*q.size > HEAP_LIMIT) await clearRuns(); }
}
async function executeQuery(q, token) {
    const check = () => { if (token !== latestQuery) throw new DOMException("Superseded query", "AbortError"); };
    check();
    const cacheKey = JSON.stringify(q);
    if (pages.has(cacheKey)) { const value = pages.get(cacheKey); pages.delete(cacheKey); pages.set(cacheKey, value); return value; }
    const revision = JSON.stringify([2,q.sources.map(source => [source,revisions.get(source)])]);
    if (!savedPages) savedPages = await get("meta",["saved-pages"]) || [];
    const saved = savedPages.find(page => page.key === cacheKey && page.revision === revision);
    check();
    if (saved) { pages.set(cacheKey,saved.result); while (pages.size > PAGE_CACHE) pages.delete(pages.keys().next().value); return saved.result; }
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
    // In the combined period ranking every npia row precedes other sources.
    // Once npia alone fills the requested page, the other sources contribute
    // only their known totals; searches and filters still scan all sources.
    const npiaFirst = q.sort?.defaultSource === 'all' && ['daily','weekly','monthly'].includes(q.sort.sortBy)
        && q.sources.includes('npia') && wanted <= (totals.get('npia') || 0)
        && !q.text && !q.author && !q.focus && (!q.status || q.status === 'all')
        && (!q.audience || q.audience === 'all') && !q.and.length && !q.or.length && !q.exclude.length;
    for (const source of q.sources) {
        if (npiaFirst && source !== 'npia') { count += totals.get(source) || 0; seq += totals.get(source) || 0; continue; }
        for (const key of blocks.get(source) || []) {
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
    // A few exact result pages avoid a million-row rescan after reopening.
    // Content fingerprints and the complete query protect against stale hits.
    savedPages = savedPages.filter(page => page.key !== cacheKey);
    savedPages.push({key:cacheKey,revision,result});
    while (savedPages.length > PAGE_CACHE) savedPages.shift();
    try {
        const tx = (await db()).transaction("meta","readwrite"), done = transaction(tx);
        tx.objectStore("meta").put(savedPages,["saved-pages"]);
        await done;
    } catch (_) { /* Result caching is optional when site storage is full. */ }
    return result;
}
self.onmessage = ({data}) => {
    const {id, type, value} = data;
    // A source switch must stop an obsolete scan before its activation runs.
    if (type === "query" || type === "activate") latestQuery = id;
    serial = serial.catch(() => {}).then(async () => {
        try {
            let result;
            if (type === "import" || type === "activate") {
                // The application includes the dictionary version in cfg.
                // Avoid retaining a duplicate dictionary string per source.
                const signature = JSON.stringify(value.cfg);
                const saved = readySources.get(value.source);
                // Source changes reuse the existing disk index and its small
                // summary. Explicit imports still validate upstream changes.
                if (type === "activate" && saved?.signature === signature) result = {...saved.result,reused:true};
                else {
                    readySources.delete(value.source);
                    result = await importSource(value.source,value.cfg,progress => self.postMessage({id,progress}),value.tagLabels || {});
                    // A partial import stays usable on disk, but the next
                    // activation must retry its unavailable chunks.
                    if (!result.warnings.length) readySources.set(value.source,{signature,result});
                }
            } else result = await query(value,id);
            self.postMessage({id,result});
        } catch (error) { self.postMessage({id,error:{name:error.name,message:error.message}}); }
    });
};
