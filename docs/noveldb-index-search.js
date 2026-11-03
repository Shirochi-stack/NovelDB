/*
 * A text search answered at once from NovelDB's search index, before (or without) the catalog's chunks.
 *
 * The index (scripts/noveldb_index/build.cjs, rebuilt when the data changes) lives as separate small
 * files on the repository's `noveldb-index` branch: a table (manifest.json), word buckets
 * (b/<n>.json.gz: {token: [book number, ...]}, the most read books first) and card blocks
 * (c/<n>.json.gz). A search reads one bucket per query word and the blocks of the books found, a few
 * small files instead of the whole catalog. The tokens are the builder's; keep them in step.
 *
 * window.NovelIndexSearch.eligible(query) -> true for a plain text search on its first page
 * window.NovelIndexSearch.search(query)   -> Promise of {rows, total, page} (rows as metadata-core's parseRows gives them)
 */
(function () {
    "use strict";
    if (window.NovelIndexSearch) return;

    const BASE = "https://raw.githubusercontent.com/Shirochi-stack/NovelDB/noveldb-index/";
    const STOP = new Set(["the", "a", "an", "of", "to", "in", "and", "is", "on", "at", "for", "with"]);
    const CJK = /[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]/;
    const METADATA_V1 = new Set(["nweb", "jara", "mpia", "rbooks", "nseries"]);
    const LIMIT = 200;
    let manifest = null;
    const pieces = new Map();

    function tokens(text) {
        const out = [];
        for (const word of String(text || "").normalize("NFKC").toLowerCase().match(/[\p{L}\p{N}]+/gu) || []) {
            const add = (token) => { if (!out.includes(token)) out.push(token); };
            if (CJK.test(word)) {
                if (word.length <= 2) add(word);
                for (let i = 0; i + 2 <= word.length; i++) add(word.slice(i, i + 2));
            } else if (word.length >= 2 && !STOP.has(word)) {
                add(word);
            }
        }
        return out;
    }

    function bucketOf(token, buckets) {
        let hash = 0x811c9dc5;
        for (const byte of new TextEncoder().encode(token)) hash = Math.imul(hash ^ byte, 0x01000193) >>> 0;
        return hash % buckets;
    }

    async function gzJson(path) {
        if (pieces.has(path)) return pieces.get(path);
        const asked = fetch(BASE + path).then(async (response) => {
            if (!response.ok) throw new Error(`${path}: ${response.status}`);
            const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
            return JSON.parse(await new Response(stream).text());
        });
        pieces.set(path, asked);
        asked.catch(() => pieces.delete(path));
        if (pieces.size > 300) pieces.delete(pieces.keys().next().value);
        return asked;
    }

    async function table() {
        if (manifest && Date.now() - manifest.at < 3600e3) return manifest.value;
        const response = await fetch(BASE + "manifest.json", {cache: "no-cache"});
        if (!response.ok) throw new Error(`manifest: ${response.status}`);
        const value = await response.json();
        if (value.format !== 1) throw new Error("an index for newer code");
        if (manifest && manifest.value.built !== value.built) pieces.clear();
        manifest = {at: Date.now(), value};
        return value;
    }

    function row(card) {
        const [source, id, title, titleEn, author, cover, tagsEn, views, likes, chapters, complete, url, updated, age, tags] = card;
        return {
            id: String(id), source, title: title || "", titleEn: titleEn || "", author: author || "", cover: cover || "",
            tags: Array.isArray(tags) && tags.length ? tags : (tagsEn || []), views: views || 0, likes: likes || 0,
            chapters: chapters || 0, complete: complete, updated: updated || "", age: age == null ? null : age,
            canonicalUrl: url || "", metadataV1: METADATA_V1.has(source), fromIndex: true,
            tier: null, purchaseUrl: null, metrics: {}, rankings: {}, startDate: null,
        };
    }

    function eligible(query) {
        return !!(query && query.text && !query.author && query.status === "all" && query.audience === "all"
            && !query.and.length && !query.or.length && !query.exclude.length && query.page === 1);
    }

    async function search(query) {
        const wanted = tokens(query.text);
        if (!wanted.length) return null;
        const index = await table();
        const lists = await Promise.all(wanted.map(async (token) => {
            const bucket = await gzJson(`b/${bucketOf(token, index.buckets)}.json.gz`);
            return bucket[token] || [];
        }));
        if (lists.some((list) => !list.length)) return {rows: [], total: 0, page: 1};
        const others = lists.slice(1).map((list) => new Set(list));
        const numbers = lists[0].filter((n) => others.every((set) => set.has(n))).sort((a, b) => a - b).slice(0, LIMIT);
        const blocks = [...new Set(numbers.map((n) => Math.floor(n / index.block)))];
        const loaded = new Map(await Promise.all(blocks.map(async (b) => [b, await gzJson(`c/${b}.json.gz`)])));
        const sources = new Set(query.sources || []);
        const rows = numbers.map((n) => loaded.get(Math.floor(n / index.block))[n % index.block])
            .filter(Boolean).map(row).filter((r) => !sources.size || sources.has(r.source));
        return {rows: rows.slice(0, query.size || 60), total: rows.length, page: 1};
    }

    window.NovelIndexSearch = {eligible, search, tokens, bucketOf};
})();
