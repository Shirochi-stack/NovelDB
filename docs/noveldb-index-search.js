/*
 * A text search answered at once from NovelDB's search index, before (or without) the catalog's chunks.
 *
 * The index (scripts/noveldb_index/build.cjs, rebuilt when the data changes) lives as separate small
 * files on the repository's `noveldb-index` branch: a table (manifest.json), word buckets
 * (b/<n>.json.gz: {token: {source: [book number, ...]}}, each source's books the most read first,
 * written as differences, with "+source": [count, first page, ...] for a list longer than the bucket
 * keeps), their further pages (p/<n>.json.gz) and card blocks (c/<n>.json.gz). A search reads one
 * bucket per query word and the blocks of the books on the page asked for, a few small files instead
 * of the whole catalog. Results take each source's next book in turn, npia's first (every source,
 * whatever the source menu shows). The
 * tokens are the builder's; keep them in step.
 *
 * A search may have words, picked tags (each must match; "or" tags: any of them) and an author: each
 * is a token of the index ("#tag", "@author"). A tag's spellings (its English name and the sources' own)
 * are searched together with the tags sharing their alias key (the builder's tagConcept: "Gender
 * Bender" also finds "Genderbend (TS)"), each source's books of any of them merged.
 *
 * window.NovelIndexSearch.eligible(query) -> true for a search the index can answer (no excluded tags,
 *     any status and audience)
 * window.NovelIndexSearch.search(query)   -> Promise of {rows, total, page} (rows as metadata-core's parseRows gives them)
 */
(function () {
    "use strict";
    if (window.NovelIndexSearch) return;

    const BASE = "https://raw.githubusercontent.com/Shirochi-stack/NovelDB/noveldb-index/";
    const STOP = new Set(["the", "a", "an", "of", "to", "in", "and", "is", "on", "at", "for", "with"]);
    const CJK = /[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]/;
    const METADATA_V1 = new Set(["nweb", "jara", "mpia", "rbooks", "nseries"]);
    const FORMAT = 2, LEAD = "npia", MULTI_PAGES = 40;
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
        if (value.format !== FORMAT) throw new Error("an index for other code");
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
        return !!(query && (query.text || query.author || query.and.length || query.or.length)
            && query.status === "all" && query.audience === "all" && !query.exclude.length);
    }

    const normal = (text) => String(text || "").normalize("NFKC").toLowerCase();
    const tagToken = (tag) => "#" + normal(tag).replace(/[\s_\-]+/g, " ").trim();
    const authorToken = (author) => "@" + normal(author).replace(/\s+/g, " ").trim();

    // What a search must match, as groups of tokens: a book matches a group through any one of its tokens.
    function groups(query) {
        const out = tokens(query.text).map((token) => [token]);
        const spelled = (tags, names) => [...new Set([...(names || []), ...tags.flat()].map(tagToken))].filter((t) => t.length > 1);
        (query.and || []).forEach((spellings, i) => out.push(spelled([spellings], [(query.andNames || [])[i]].filter(Boolean))));
        if ((query.or || []).length) out.push(spelled(query.or, query.orNames));
        if (query.author) out.push([authorToken(query.author)]);
        return out.filter((group) => group.length);
    }

    const undelta = (values) => { let total = 0; return (values || []).map((value) => (total += value)); };

    // One source's books for a token: the first page from the bucket, further pages read as needed.
    function list(index, first, more) {
        const page = index.page || 500;
        const pageOf = async (n) => (n === 0 ? first : more && n <= more[2].length ? undelta(await gzJson(`p/${more[1] + n - 1}.json.gz`)) : []);
        return {
            head: first, total: more ? more[0] : first.length, first: first.length ? first[0] : Infinity, page, pageOf,
            async numbers(start, end) {
                end = Math.min(end, this.total);
                if (end <= start) return [];
                const pages = [];
                for (let n = Math.floor(start / page); n <= Math.floor((end - 1) / page); n++) pages.push(pageOf(n));
                const skip = start - Math.floor(start / page) * page;
                return (await Promise.all(pages)).flat().slice(skip, skip + end - start);
            },
        };
    }
    const fixed = (numbers) => ({head: numbers, total: numbers.length, first: numbers.length ? numbers[0] : Infinity, page: Math.max(1, numbers.length),
                                 pageOf: async () => numbers, numbers: async (start, end) => numbers.slice(start, end)});

    // Merged lists: `head` (merged up to where every list was read), then `rest`'s books after its first `skip`.
    function chain(head, rest, skip) {
        const size = head.length, page = rest.page;
        const out = {head, page, total: size + Math.max(0, rest.total - skip), first: size ? head[0] : rest.first};
        out.numbers = async (start, end) => {
            end = Math.min(end, out.total);
            if (end <= start) return [];
            const own = head.slice(start, Math.min(end, size));
            return end <= size ? own : own.concat(await rest.numbers(skip + Math.max(0, start - size), skip + end - size));
        };
        out.pageOf = (n) => out.numbers(n * page, n * page + page);
        return out;
    }

    async function lists(index, token) {
        const entry = (await gzJson(`b/${bucketOf(token, index.buckets)}.json.gz`))[token] || {};
        const out = {};
        for (const [source, first] of Object.entries(entry)) {
            if (!source.startsWith("+") && first.length) out[source] = list(index, undelta(first), entry["+" + source]);
        }
        return out;
    }

    // A tag's alias key (the builder's tagConcept): "Gender Bender" and "Genderbend (TS)" share "=genderbend".
    const BRACKETED = /[(\[{（【][^)\]}）】]*[)\]}）】]/g, UNION_PAGES = 1;  // the first page of each alias, already in its bucket
    function concept(tag) {
        const text = normal(tag);
        const letters = (value) => value.replace(/[^\p{L}\p{N}]+/gu, "");
        let key = letters(text.replace(BRACKETED, " ")) || letters(text);
        if (key.length > 5 && !CJK.test(key)) key = key.replace(/(ing|er|s)$/, "");
        return key ? "=" + key : "";
    }
    // The tag tokens sharing a tag token's alias key (its bucket lists them).
    async function aliases(index, token) {
        const key = concept(token.slice(1));
        if (!key) return [];
        const found = (await gzJson(`b/${bucketOf(key, index.buckets)}.json.gz`))[key];
        return Array.isArray(found) ? found.filter((t) => typeof t === "string") : [];
    }

    // A group's lists: in each source, the books of any of its tokens (a tag's spellings and aliases), merged.
    async function groupLists(index, group) {
        const more = await Promise.all(group.filter((token) => token.startsWith("#")).map((token) => aliases(index, token)));
        const tokens = [...new Set([...group, ...more.flat()])];
        const found = await Promise.all(tokens.map((token) => lists(index, token)));
        const bySource = {};
        for (const each of found) for (const [name, one] of Object.entries(each)) (bySource[name] = bySource[name] || []).push(one);
        const out = {};
        // A tag and its translation list the same books: read them once.
        const same = (a, b) => a.total === b.total && a.head.length === b.head.length && a.head.every((n, i) => n === b.head[i]);
        await Promise.all(Object.entries(bySource).map(async ([name, every]) => {
            const all = every.filter((one, i) => !every.slice(0, i).some((other) => same(one, other)));
            if (all.length === 1) { out[name] = all[0]; return; }
            const read = await Promise.all(all.map((one) => one.numbers(0, UNION_PAGES * one.page)));
            // A list read only in part says nothing of the books after its last one read: the merge stops
            // there, and the longest list's own books follow.
            let cutoff = Infinity;
            read.forEach((numbers, i) => { if (numbers.length && numbers.length < all[i].total) cutoff = Math.min(cutoff, numbers[numbers.length - 1]); });
            const numbers = [...new Set(read.flat().filter((n) => n <= cutoff))].sort((a, b) => a - b);
            if (cutoff === Infinity) { if (numbers.length) out[name] = fixed(numbers); return; }
            const longest = all.reduce((best, one, i) => (one.total > all[best].total ? i : best), 0);
            out[name] = chain(numbers, all[longest], read[longest].filter((n) => n <= cutoff).length);
        }));
        return out;
    }

    // Each source's books matching every group: one group's lists, or for several the books in all of
    // them (among their first pages).
    async function streams(index, wanted, sources) {
        const perToken = await Promise.all(wanted.map((group) => groupLists(index, group)));
        const keep = (name) => !sources.size || sources.has(name);
        if (perToken.some((found) => !Object.keys(found).length)) return {};
        if (perToken.length === 1) return Object.fromEntries(Object.entries(perToken[0]).filter(([name]) => keep(name)));
        const out = {};
        await Promise.all(Object.keys(perToken[0]).filter((name) => keep(name) && perToken.every((found) => found[name])).map(async (name) => {
            const each = perToken.map((found) => found[name]);
            const read = await Promise.all(each.map((one) => one.numbers(0, MULTI_PAGES * one.page)));
            // A list read only in part says nothing of the books after its last one read.
            let cutoff = Infinity;
            read.forEach((numbers, i) => { if (numbers.length && numbers.length < each[i].total) cutoff = Math.min(cutoff, numbers[numbers.length - 1]); });
            const others = read.slice(1).map((numbers) => new Set(numbers));
            const numbers = read[0].filter((n) => n <= cutoff && others.every((set) => set.has(n))).sort((a, b) => a - b);
            if (numbers.length) out[name] = fixed(numbers);
        }));
        return out;
    }

    // Every source is searched, whichever the source menu shows: the index has them all at the same cost.
    async function search(query) {
        const wanted = groups(query);
        if (!wanted.length) return null;
        const index = await table();
        const found = await streams(index, wanted, new Set());
        const order = Object.keys(found).sort((a, b) => (a !== LEAD) - (b !== LEAD) || found[a].first - found[b].first);
        const total = order.reduce((sum, name) => sum + found[name].total, 0);
        const size = query.size || 60, page = Math.max(1, Math.min(query.page || 1, Math.ceil(total / size) || 1));
        // The places of this page in the order that takes each source's next book in turn.
        const picks = [], offset = (page - 1) * size;
        let place = 0;
        const longest = Math.max(0, ...order.map((name) => found[name].total));
        for (let round = 0; round < longest && picks.length < size; round++) {
            const active = order.filter((name) => found[name].total > round);
            if (place + active.length <= offset) { place += active.length; continue; }
            for (const name of active) { if (place >= offset && picks.length < size) picks.push([name, round]); place++; }
        }
        const numbers = await Promise.all(picks.map(async ([name, at]) => {
            const one = found[name];
            return (await one.pageOf(Math.floor(at / one.page)))[at % one.page];
        }));
        const rows = await Promise.all(numbers.filter((n) => n != null).map(async (n) => {
            const card = (await gzJson(`c/${Math.floor(n / index.block)}.json.gz`))[n % index.block];
            return card ? row(card) : null;
        }));
        return {rows: rows.filter(Boolean), total, page};
    }

    window.NovelIndexSearch = {eligible, search, tokens, bucketOf, groups};
})();
