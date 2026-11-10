/* Build the local title-search pieces from the same normalized catalogue rows as the online index. */
'use strict';
const fs = require('fs');
const path = require('path');
const index = require('./build.cjs');
const data = path.resolve(process.argv[2]);
const out = path.resolve(process.argv[3]);
const buckets = 256, block = 128, limit = 1000;
const tags = index.readPairs(path.join(data, 'tags_en.txt'));
const aliases = process.argv[4] ? JSON.parse(fs.readFileSync(process.argv[4], 'utf8')) : {};
let books = [];
for (const [source, config] of Object.entries(index.SOURCES)) {
    const found = index.sourceBooks(source, config, tags);
    found.sort((a, b) => b.views - a.views || b.likes - a.likes);
    found.forEach((book, position) => { book.rank = position / found.length; });
    books = books.concat(found);
    console.log(source, found.length);
}
books = index.mergeSame(books).books;
books.sort((a, b) => a.rank - b.rank || b.views - a.views);
fs.mkdirSync(path.join(out, 'b'), {recursive: true});
fs.mkdirSync(path.join(out, 'c'), {recursive: true});
const words = Array.from({length: buckets}, () => Object.create(null));
const prefix = token => [...token].slice(0, 2).join('');
books.forEach((book, number) => {
    book.aliases = aliases[book.source + '/' + book.id] || [];
    const own = new Set([...index.tokens(book.titleEn), ...index.tokens(book.title), ...book.aliases.flatMap(index.tokens)]);
    for (const token of own) {
        const bucket = words[index.bucketOf(prefix(token)) % buckets];
        const list = bucket[token] || (bucket[token] = []);
        if (list.length < limit) list.push(number);
    }
});
const row = book => [book.source, book.id, book.title, book.titleEn, book.author, book.cover,
    book.tags.slice(0, 8), book.views, book.likes, book.chapters, book.complete, book.url,
    book.updated, book.published, book.also || [], book.rating || 0, book.aliases];
for (let n = 0; n < buckets; n++) fs.writeFileSync(path.join(out, 'b', n + '.json'), JSON.stringify(words[n]));
for (let n = 0; n < books.length; n += block) {
    fs.writeFileSync(path.join(out, 'c', n / block + '.json'), JSON.stringify(books.slice(n, n + block).map(row)));
}
const excluded = /\b(josei|danmei|yaoi|smut|bl|boys?'?s? ?love|shounen[- ]?ai|adult|hentai|r-?18)\b/i;
const top = books.filter(book => (book.source === 'npia' || book.source === 'sfc') && book.titleEn
    && !excluded.test(book.titleEn) && !book.tags.some(tag => excluded.test(tag))).slice(0, 300);
fs.writeFileSync(path.join(out, 'top.json'), JSON.stringify(top.map(row)));
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({version: 1, books: books.length, buckets, block}));
console.log('Local title index', books.length, 'books');
