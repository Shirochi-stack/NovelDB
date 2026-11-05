#!/usr/bin/env node
/*
 * Builds the NovelDB search index: every book of the NovelDB site's data (docs/data), searchable by a
 * few small downloads instead of the site's whole catalog.
 *
 *     node scripts/noveldb_index/build.cjs [docs/data] [out folder]
 *
 * Writes two files, published as assets of the `engines` release (.github/workflows/noveldb-index.yml):
 * - noveldb-search-<build time>.bin: compressed pieces, one after another (gzip members), read with
 *   HTTP Range requests. Each build has a name of its own, so a client never reads a new table of
 *   contents against the last build's pieces.
 * - noveldb-search.json: where each piece is, and what the clients need to know about the sources.
 *
 * The pieces:
 * - 4096 word buckets: {token: {source: [book number, ...], "+source": [count, first page, [[offset,
 *   length], ...]]}}. A token is a word of a title (its English title and its own), two-character
 *   pieces of Chinese, Japanese and Korean words, "#tag", "@author" and "!source" (all of a source's
 *   books: its Top list). Each source's list of a token
 *   has every one of its books, the most read first (book numbers count up from the most read), as
 *   differences from the number before (the first one as it is). The bucket holds its first PAGE
 *   books; a longer list's "+source" says how many it has and where its further pages are (pieces of
 *   PAGE numbers each, written the same way: in the .bin at those offsets, and as p/<n>.json.gz from
 *   n = first page on). So a search shows a source's first books from the bucket alone, and reads
 *   further pages only as the results are scrolled.
 * - Card blocks of BLOCK books, in book-number order: [source, id, title, English title, author,
 *   cover, tags, views, likes, chapters, complete, address, updated, age rating, original tags,
 *   publication date].
 *
 * It also writes the same pieces as separate files (split/: manifest.json, b/<bucket>.json.gz,
 * c/<block>.json.gz) for the NovelDB site, which reads them from the `noveldb-index` branch: a web page
 * cannot read release assets (no CORS), and the branch's files can be fetched one by one.
 *
 * The clients (noveldb/core/catalog/noveldb_index.py, scripts/generic/catalog-app.js) tokenize a
 * query the same way (tokens() below); keep the three in step.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const ROOT = path.resolve(__dirname, "..", "..");
const M = require(path.join(ROOT, "docs", "metadata-core.js"));
const DATA = path.resolve(process.argv[2] || path.join(ROOT, "docs", "data"));
const OUT = path.resolve(process.argv[3] || path.join(ROOT, "build", "noveldb-index"));
const BUCKETS = 4096;
const BLOCK = 256;
const PAGE = 500;
const TAGS_PER_CARD = 12;

// The sources the site lists (docs/app.js SOURCES): where their links and covers point.
const U = (text) => text;  // addresses below are written with escapes (the codename rule)
const SOURCES = {
    npia: {coverPrefix: U("https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"),
           linkPrefix: U("https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/"),
           titles: "titles_en.txt", descriptions: "descriptions_shard_"},
    kpage: {coverPrefix: "", linkPrefix: U("https://page.\u006b\u0061\u006b\u0061\u006f.com/content/"),
            titles: "kpage_titles_en.txt", descriptions: "kpage_descriptions_shard_"},
    sfc: {coverPrefix: U("https://rss.\u0073\u0066\u0061\u0063\u0067.com/web/novel/images/NovelCover/Big/"),
          linkPrefix: U("https://book.\u0073\u0066\u0061\u0063\u0067.com/Novel/"), sfcRanks: true,
          titles: "sfc_titles_en.txt", descriptions: "sfc_descriptions_shard_"},
    nweb: {format: "metadata-v1", linkHosts: [U("novel.\u006e\u0061\u0076\u0065\u0072.com")],
           purchaseHosts: [U("series.\u006e\u0061\u0076\u0065\u0072.com"), U("m.series.\u006e\u0061\u0076\u0065\u0072.com"),
                           U("novel.\u006e\u0061\u0076\u0065\u0072.com")]},
    jara: {format: "metadata-v1", linkHosts: [U("www.\u006a\u006f\u0061\u0072\u0061.com"), U("\u006a\u006f\u0061\u0072\u0061.com")]},
    mpia: {format: "metadata-v1", linkHosts: [U("www.\u006d\u0075\u006e\u0070\u0069\u0061.com")]},
    rbooks: {format: "metadata-v1", linkHosts: [U("\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com")]},
    nseries: {format: "metadata-v1", linkHosts: [U("series.\u006e\u0061\u0076\u0065\u0072.com")]},
};

// -- tokens (the clients do the same) -----------------------------------------------------------------

const STOP = new Set(["the", "a", "an", "of", "to", "in", "and", "is", "on", "at", "for", "with"]);
const CJK = /[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]/;

function normal(text) {
    return String(text || "").normalize("NFKC").toLowerCase();
}

/** The searchable tokens of a title (or a query): its words, and two-character pieces of CJK words. */
function tokens(text) {
    const out = new Set();
    for (const word of normal(text).match(/[\p{L}\p{N}]+/gu) || []) {
        if (CJK.test(word)) {
            if (word.length <= 2) out.add(word);
            for (let i = 0; i + 2 <= word.length; i++) out.add(word.slice(i, i + 2));
        } else if (word.length >= 2 && !STOP.has(word)) {
            out.add(word);
        }
    }
    return [...out];
}

function tagToken(tag) {
    return "#" + normal(tag).replace(/[\s_\-]+/g, " ").trim();
}

/**
 * A tag's alias key: "=" and its letters and digits, without a bracketed note when anything is left
 * without it, and without an ending -ing, -er or -s (a word of 6+ letters, not Chinese, Japanese or
 * Korean). "Gender Bender", "Genderbend (TS)" and "Genderbending" share "=genderbend". The bucket of
 * a key lists the tag tokens that share it (ALIASES of them, the most used first), and a tag search
 * reads all of them. The clients compute the same key: keep them in step.
 */
const BRACKETED = /[(\[{（【][^)\]}）】]*[)\]}）】]/g;
const ALIASES = 30, ALIAS_BOOKS = 3;
// The tag menu's list of NovelDB's tags: those on 5 books or more, at most 4000.
const TAG_BOOKS = 5, TAG_LIST = 4000;
// The names a site's own tag search is asked for, per tag of that list.
const NATIVE_NAMES = 3;
function tagConcept(tag) {
    const text = normal(tag);
    const letters = (value) => value.replace(/[^\p{L}\p{N}]+/gu, "");
    let key = letters(text.replace(BRACKETED, " ")) || letters(text);
    if (key.length > 5 && !CJK.test(key)) key = key.replace(/(ing|er|s)$/, "");
    return key ? "=" + key : "";
}

// Tags meaning the same under different words ("Gender Swap" for "Gender Bender"): each group's alias
// keys share one list of tags, so a search for any of its names finds the books of all of them.
const SYNONYMS = JSON.parse(fs.readFileSync(path.join(__dirname, "tag-synonyms.json"), "utf8")).groups;
const SYNONYM_ALIASES = 48;
function mergeSynonyms(aliases) {
    let keys = 0;
    for (const names of SYNONYMS) {
        const group = [...new Set(names.map(tagConcept).filter(Boolean))];
        const merged = new Map();
        merged.synonyms = true;
        for (const key of group) {
            for (const [token, n] of aliases.get(key) || []) merged.set(token, (merged.get(token) || 0) + n);
        }
        // Every key of the group answers with it, also one that no book's tag has.
        for (const key of group) aliases.set(key, merged);
        keys += group.length;
    }
    return keys;
}

function authorToken(author) {
    return "@" + normal(author).replace(/\s+/g, " ").trim();
}

/** FNV-1a over the token's UTF-8 bytes: its bucket. */
function bucketOf(token) {
    let hash = 0x811c9dc5;
    for (const byte of Buffer.from(token, "utf8")) {
        hash ^= byte;
        hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash % BUCKETS;
}

// -- reading the site's data --------------------------------------------------------------------------

function readJson(file) {
    const raw = fs.readFileSync(file);
    return JSON.parse((file.endsWith(".gz") ? zlib.gunzipSync(raw) : raw).toString("utf8"));
}

function readPairs(file) {
    const out = new Map();
    if (!fs.existsSync(file)) return out;
    for (const line of fs.readFileSync(file, "utf8").split("\n")) {
        const parts = line.split("|||");
        if (parts.length >= 2 && parts[parts.length - 1].trim()) out.set(parts[0].trim(), parts[parts.length - 1].trim());
    }
    return out;
}

function sourceBooks(source, cfg, tagNames) {
    const manifest = readJson(path.join(DATA, `${source}_chunk_manifest.json`));
    const config = cfg.format === "metadata-v1" ? {...M.manifestConfig(manifest, source), ...cfg} : cfg;
    const titles = cfg.titles ? readPairs(path.join(DATA, cfg.titles)) : new Map();
    const books = [];
    for (const file of manifest.files) {
        const chunk = readJson(path.join(DATA, file));
        const translations = chunk.translations || {};
        for (const novel of M.parseRows(chunk.novels || [], source, config)) {
            const url = novel.canonicalUrl || (cfg.linkPrefix ? cfg.linkPrefix + novel.id : "");
            if (!url) continue;
            books.push({
                source, id: novel.id, title: novel.title, titleEn: translations[novel.id] || titles.get(novel.id) || "",
                author: novel.author, cover: novel.cover || "",
                tags: novel.tags.map((tag) => tagNames.get(tag) || tag),
                originalTags: novel.tags,
                views: Number(novel.views) || 0, likes: Number(novel.likes) || 0, chapters: Number(novel.chapters) || 0,
                complete: novel.complete == null ? null : (novel.complete ? 1 : 0), url, updated: novel.updated || "",
                age: novel.age == null ? null : Number(novel.age) || 0,
                published: String(novel.startDate || "").slice(0, 10),
            });
        }
    }
    return books;
}

function main() {
    const started = Date.now();
    const tagNames = readPairs(path.join(DATA, "tags_en.txt"));
    let books = [];
    const sources = {};
    for (const [source, cfg] of Object.entries(SOURCES)) {
        const found = sourceBooks(source, cfg, tagNames);
        // Each source's most read first: a book's place in its own source (views differ in scale between sites).
        found.sort((a, b) => b.views - a.views || b.likes - a.likes);
        found.forEach((book, index) => { book.rank = index / Math.max(1, found.length); });
        books = books.concat(found);
        const prefix = cfg.descriptions || `${source}_descriptions_shard_`;
        sources[source] = {books: found.length, descriptions: {prefix, count: 128}};
        console.log(`${source}: ${found.length} books`);
    }
    // Book numbers interleave the sources by their place in each, so a token's first books are the most read anywhere.
    books.sort((a, b) => a.rank - b.rank || b.views - a.views);

    // token -> {source: [book number, ...]} in each bucket, every book of the token, in number order.
    const buckets = Array.from({length: BUCKETS}, () => new Map());
    // alias key -> {tag token: books}: a tag's English name and its original share the English name's key.
    const aliases = new Map();
    const alias = (key, token) => {
        if (!key) return;
        let counts = aliases.get(key);
        if (!counts) aliases.set(key, counts = new Map());
        counts.set(token, (counts.get(token) || 0) + 1);
    };
    // How many books carry each English tag name: the apps' tag menus offer them (TAG_LIST).
    const tagCounts = new Map();
    // alias key -> {source: {original tag: books}}: what each site calls a tag, for the apps' searches of
    // the sites themselves ("TS" on npia for "Gender Bender").
    const natives = new Map();
    const native = (key, source, original) => {
        if (!key) return;
        let bySource = natives.get(key);
        if (!bySource) natives.set(key, bySource = new Map());
        let counts = bySource.get(source);
        if (!counts) bySource.set(source, counts = new Map());
        counts.set(original, (counts.get(original) || 0) + 1);
    };
    books.forEach((book, number) => {
        // "!source": every book of a source, the most read first (a source's Top list).
        const own = new Set([...tokens(book.titleEn), ...tokens(book.title), "!" + book.source]);
        if (book.author) own.add(authorToken(book.author));
        // Its status as a tag: "Completed" and "Ongoing" are searched like any tag.
        const status = book.complete === 1 ? "Completed" : book.complete === 0 ? "Ongoing" : "";
        if (status) { own.add(tagToken(status)); tagCounts.set(status, (tagCounts.get(status) || 0) + 1); }
        for (const tag of new Set(book.tags)) if (tag && tag !== status) tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
        book.tags.forEach((tag, i) => {
            const original = book.originalTags[i];
            if (tag) alias(tagConcept(tag), tagToken(tag));
            if (tag && original && original !== tag) alias(tagConcept(tag), tagToken(original));
            if (original && original !== tag) alias(tagConcept(original), tagToken(original));
            if (tag && original) native(tagConcept(tag), book.source, original);
        });
        for (const tag of new Set([...book.tags, ...book.originalTags])) if (tag) own.add(tagToken(tag));
        for (const token of own) {
            const bucket = buckets[bucketOf(token)];
            let lists = bucket.get(token);
            if (!lists) bucket.set(token, lists = {});
            (lists[book.source] || (lists[book.source] = [])).push(number);
        }
    });
    // Synonyms (tag-synonyms.json): every key of a group lists the tags of all of them.
    const synonymKeys = mergeSynonyms(aliases);
    for (const names of SYNONYMS) {
        const group = [...new Set(names.map(tagConcept).filter(Boolean))];
        const merged = new Map();
        for (const key of group) {
            for (const [source, counts] of natives.get(key) || []) {
                const into = merged.get(source) || merged.set(source, new Map()).get(source);
                for (const [original, n] of counts) into.set(original, (into.get(original) || 0) + n);
            }
        }
        for (const key of group) natives.set(key, merged);
    }
    // A key with two tags or more (each on ALIAS_BOOKS books or more) goes into its bucket as a list of them.
    let aliasKeys = 0;
    for (const [key, counts] of aliases) {
        const limit = counts.synonyms ? SYNONYM_ALIASES : ALIASES;
        const used = [...counts].filter(([, n]) => n >= ALIAS_BOOKS).sort((a, b) => b[1] - a[1]).slice(0, limit).map(([token]) => token);
        if (used.length < (counts.synonyms ? 1 : 2)) continue;  // a synonym's key may stand for one other tag
        buckets[bucketOf(key)].set(key, used);
        aliasKeys++;
    }
    aliases.clear();
    console.log(`${aliasKeys} tag alias keys, ${synonymKeys} of them synonyms`);

    fs.mkdirSync(OUT, {recursive: true});
    const built = new Date().toISOString();
    const binName = `noveldb-search-${built.replace(/[^0-9]/g, "").slice(0, 14)}.bin`;
    const binPath = path.join(OUT, binName);
    const out = fs.openSync(binPath, "w");
    let offset = 0;
    const split = path.join(OUT, "split");
    fs.rmSync(split, {recursive: true, force: true});
    fs.mkdirSync(path.join(split, "b"), {recursive: true});
    fs.mkdirSync(path.join(split, "c"), {recursive: true});
    fs.mkdirSync(path.join(split, "p"), {recursive: true});
    const write = (value, file) => {
        const data = zlib.gzipSync(Buffer.from(JSON.stringify(value), "utf8"), {level: 9});
        fs.writeSync(out, data);
        fs.writeFileSync(path.join(split, file), data);
        const at = [offset, data.length];
        offset += data.length;
        return at;
    };
    const deltas = (list) => list.map((n, i) => (i ? n - list[i - 1] : n));
    let pages = 0;
    const bucketAt = buckets.map((bucket, index) => {
        const value = {};
        for (const [token, lists] of bucket) {
            if (Array.isArray(lists)) { value[token] = lists; continue; }  // an alias key's tag tokens
            const entry = value[token] = {};
            for (const [source, list] of Object.entries(lists)) {
                entry[source] = deltas(list.slice(0, PAGE));
                if (list.length <= PAGE) continue;
                const first = pages, at = [];
                for (let start = PAGE; start < list.length; start += PAGE) at.push(write(deltas(list.slice(start, start + PAGE)), `p/${pages++}.json.gz`));
                entry["+" + source] = [list.length, first, at];
            }
        }
        bucket.clear();
        return write(value, `b/${index}.json.gz`);
    });
    const blockAt = [];
    for (let start = 0; start < books.length; start += BLOCK) {
        blockAt.push(write(books.slice(start, start + BLOCK).map((b) => [
            b.source, b.id, b.title, b.titleEn, b.author, b.cover, b.tags.slice(0, TAGS_PER_CARD),
            b.views, b.likes, b.chapters, b.complete, b.url, b.updated, b.age, b.originalTags.slice(0, TAGS_PER_CARD),
            b.published]),
            `c/${blockAt.length}.json.gz`));
    }
    // The tag names on TAG_BOOKS books or more, the most used first, at most TAG_LIST: [[name, books,
    // {source: [what the site calls it, ...]}], ...], each site's names the most used first (NATIVE_NAMES).
    const tagList = [...tagCounts].filter(([, n]) => n >= TAG_BOOKS).sort((a, b) => b[1] - a[1]).slice(0, TAG_LIST)
        .map(([name, n]) => {
            const own = {};
            for (const [source, counts] of natives.get(tagConcept(name)) || []) {
                const used = [...counts].filter(([, k]) => k >= ALIAS_BOOKS).sort((a, b) => b[1] - a[1])
                    .slice(0, NATIVE_NAMES).map(([original]) => original);
                if (used.length) own[source] = used;
            }
            return [name, n, own];
        });
    natives.clear();
    const tagsAt = write(tagList, "tags.json.gz");
    console.log(`${tagList.length} tags listed`);
    fs.closeSync(out);
    const manifest = {
        format: 2, built, books: books.length, buckets: BUCKETS, block: BLOCK,
        page: PAGE, bin: binName, binSize: offset, bucketAt, blockAt, tagsAt, sources,
    };
    fs.writeFileSync(path.join(OUT, "noveldb-search.json"), JSON.stringify(manifest));
    // The split copy's table: no offsets, a file per piece.
    fs.writeFileSync(path.join(split, "manifest.json"), JSON.stringify({
        format: 2, built, books: books.length, buckets: BUCKETS, block: BLOCK, page: PAGE, blocks: blockAt.length, pages, sources,
    }));
    console.log(`${books.length} books, ${pages} further pages, ${(offset / 1048576).toFixed(1)} MB in ${Math.round((Date.now() - started) / 1000)} s`);
}

if (require.main === module) main();
module.exports = {tokens, tagToken, tagConcept, authorToken, bucketOf, BUCKETS, PAGE};
