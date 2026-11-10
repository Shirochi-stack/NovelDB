/* Local first-page results. Online discovery enriches the cards independently. */
(function () {
    'use strict';
    var stop = new Set('the a an of to in and is on at for with'.split(' '));
    var cjk = /[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿]/;
    function words(value) { return String(value || '').normalize('NFKC').toLowerCase().match(/[\p{L}\p{N}]+/gu) || []; }
    function tokens(value) {
        var out = new Set();
        words(value).forEach(function (word) {
            if (cjk.test(word)) {
                if (word.length <= 2) out.add(word);
                for (var n = 0; n + 2 <= word.length; n++) out.add(word.slice(n, n + 2));
            } else if (word.length >= 2 && !stop.has(word)) out.add(word);
        });
        return Array.from(out);
    }
    function bucket(token, count) {
        var hash = 0x811c9dc5;
        new TextEncoder().encode(Array.from(token).slice(0, 2).join('')).forEach(function (byte) { hash = Math.imul(hash ^ byte, 0x01000193) >>> 0; });
        return hash % count;
    }
    function site(url) { return new URL(url).hostname.split('.').slice(-2).join('.'); }
    function card(row) {
        var url = row[11];
        return {url: url, title: row[3] || row[2], originalTitle: row[2], author: row[4], cover: row[5],
            tags: row[6], views: row[7], likes: row[8], chapters: row[9], status: row[10] === 1 ? 'Completed' : 'Ongoing',
            site: site(url), source: 'noveldb', comic: false,
            noveldb: {source: row[0], id: row[1]}, updated: row[12], published: row[13],
            also: (row[14] || []).map(function (other) { return {url: other[2], site: site(other[2]), noveldb: {source: other[0], id: other[1]}}; }),
            ageRating: row[15], alternateTitles: row[16] || [], instant: true};
    }
    async function search(query, options, io) {
        if (/^(manga|manhwa|comic)$/.test(options.kind || '')) return [];
        var wanted = tokens(query);
        if (!wanted.length) return [];
        var manifest = await io.load('manifest');
        if (!manifest || manifest.version !== 1 || !io.alive()) return [];
        var lists = await Promise.all(wanted.map(async function (token, index) {
            var data = await io.load('b/' + bucket(token, manifest.buckets), {token: token, prefix: index === wanted.length - 1 && !cjk.test(token)});
            var numbers = new Set();
            Object.keys(data || {}).forEach(function (word) {
                if (word === token || (index === wanted.length - 1 && !cjk.test(token) && word.startsWith(token))) {
                    data[word].forEach(function (number) { numbers.add(number); });
                }
            });
            return numbers;
        }));
        lists.sort(function (a, b) { return a.size - b.size; });
        if (!io.alive()) return [];
        var numbers = Array.from(lists[0]).filter(function (number) { return lists.every(function (list) { return list.has(number); }); })
            .sort(function (a, b) { return a - b; }).slice(0, 60);
        var blocks = Array.from(new Set(numbers.map(function (number) { return Math.floor(number / manifest.block); })));
        var found = new Map();
        var position = 0;
        await Promise.all(Array.from({length: Math.min(32, blocks.length)}, async function () {
            while (position < blocks.length && io.alive()) {
                var block = blocks[position++];
                var rows = numbers.filter(function (number) { return Math.floor(number / manifest.block) === block; })
                    .map(function (number) { return number % manifest.block; });
                found.set(block, await io.load('c/' + block, {rows: rows}));
            }
        }));
        if (!io.alive()) return [];
        var phrase = words(query).join(' ');
        return numbers.map(function (number) {
            var rows = found.get(Math.floor(number / manifest.block));
            return rows && rows[number % manifest.block];
        }).filter(Boolean).map(card).filter(function (item) {
            return !(options.sites || []).length || options.sites.indexOf(item.site) >= 0
                || item.also.some(function (other) { return options.sites.indexOf(other.site) >= 0; });
        }).map(function (item, index) {
            return {item: item, prefix: words(item.title).join(' ').startsWith(phrase) ? 0 : 1,
                canonical: item.alternateTitles.length ? 0 : 1, index: index};
        }).sort(function (a, b) { return a.prefix - b.prefix || a.canonical - b.canonical || b.item.views - a.item.views || a.index - b.index; })
            .slice(0, 30).map(function (entry) { return entry.item; });
    }
    window.NovelDBInstant = {search: search, card: card, tokens: tokens, bucket: bucket};
}());
