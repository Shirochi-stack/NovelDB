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
 * - 4096 word buckets: {token: [book number, ...]}. A token is a word of a title (its English title
 *   and its own), two-character pieces of Chinese, Japanese and Korean words, "#tag" and "@author".
 *   A token's list keeps its most read books (POSTINGS), as book numbers count up from the most read.
 * - Card blocks of BLOCK books, in book-number order: [source, id, title, English title, author,
 *   cover, tags, views, likes, chapters, complete, address, updated].
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
const POSTINGS = 200;
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

    const buckets = Array.from({length: BUCKETS}, () => new Map());
    books.forEach((book, number) => {
        const own = new Set([...tokens(book.titleEn), ...tokens(book.title)]);
        if (book.author) own.add(authorToken(book.author));
        for (const tag of new Set([...book.tags, ...book.originalTags])) if (tag) own.add(tagToken(tag));
        for (const token of own) {
            const bucket = buckets[bucketOf(token)];
            const list = bucket.get(token);
            if (!list) bucket.set(token, [number]);
            else if (list.length < POSTINGS) list.push(number);
        }
    });

    fs.mkdirSync(OUT, {recursive: true});
    const built = new Date().toISOString();
    const binName = `noveldb-search-${built.replace(/[^0-9]/g, "").slice(0, 14)}.bin`;
    const binPath = path.join(OUT, binName);
    const out = fs.openSync(binPath, "w");
    let offset = 0;
    const write = (value) => {
        const data = zlib.gzipSync(Buffer.from(JSON.stringify(value), "utf8"), {level: 9});
        fs.writeSync(out, data);
        const at = [offset, data.length];
        offset += data.length;
        return at;
    };
    const bucketAt = buckets.map((bucket) => write(Object.fromEntries(bucket)));
    const blockAt = [];
    for (let start = 0; start < books.length; start += BLOCK) {
        blockAt.push(write(books.slice(start, start + BLOCK).map((b) => [
            b.source, b.id, b.title, b.titleEn, b.author, b.cover, b.tags.slice(0, TAGS_PER_CARD),
            b.views, b.likes, b.chapters, b.complete, b.url, b.updated])));
    }
    fs.closeSync(out);
    const manifest = {
        format: 1, built, books: books.length, buckets: BUCKETS, block: BLOCK,
        postings: POSTINGS, bin: binName, binSize: offset, bucketAt, blockAt, sources,
    };
    fs.writeFileSync(path.join(OUT, "noveldb-search.json"), JSON.stringify(manifest));
    console.log(`${books.length} books, ${(offset / 1048576).toFixed(1)} MB in ${Math.round((Date.now() - started) / 1000)} s`);
}

if (require.main === module) main();
module.exports = {tokens, tagToken, authorToken, bucketOf, BUCKETS};
