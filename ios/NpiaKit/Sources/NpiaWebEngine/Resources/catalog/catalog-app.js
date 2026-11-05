/*
 * The app side of the Catalog's generic search page, for the mobile apps. On the desktop the page
 * (noveldb/qt/catalog/generic/index.html + generic.js) talks to Python (noveldb/qt/catalog/generic.py
 * with noveldb/core/catalog/generic_search.py, site_search.py and live_reader.py); on a phone this file
 * answers the same messages inside the page, a port of that Python. Keep the two in step.
 *
 * What the app provides (Android and iOS):
 * - window.NovelDBTransport(details) -> Promise of {status, statusText, finalUrl, responseHeaders,
 *   responseBase64} or {error}: the app's HTTP client, with the web view's cookies. Tagged
 *   {op: "window", url, script} it opens the address in a hidden page and runs the script there.
 * - window.NovelDBNative.postMessage(json): the app's own actions (download, link cart, sign in).
 * - Next to the page: generic.js, generic-lib.js, lnreader-lib.js, haruneko-lib.js,
 *   generic_sites.json and id_templates.json.
 *
 * Readers: LNReader's sites are read by the page itself (generic.js); HaruNeko's websites with the
 * HaruNeko bundle, loaded here when a book needs it; any other site with the generic engine.
 */
(function () {
    'use strict';
    if (window.NovelDBQt) return;

    var G = function () { return window.__GEN_lib; };
    var page = function () { return window.NovelDBGeneric; };
    var BASE = location.href.replace(/[^/]*$/, '');

    // -- talking to the page and the app -----------------------------------------------------------

    function call(name) {
        var args = Array.prototype.slice.call(arguments, 1);
        var target = page();
        if (target && typeof target[name] === 'function') {
            try { target[name].apply(target, args); } catch (e) { console.warn('[catalog]', name, e); }
        }
    }
    function native(message) {
        try { window.NovelDBNative && window.NovelDBNative.postMessage(JSON.stringify(message)); } catch (e) { /* no app */ }
    }
    function store(name, value) {
        try {
            if (value === undefined) return JSON.parse(localStorage.getItem(name) || 'null');
            localStorage.setItem(name, JSON.stringify(value));
        } catch (e) { return null; }
        return null;
    }

    // -- the network -------------------------------------------------------------------------------

    var USER_HEADERS = {'Accept-Language': 'en-US,en;q=0.9,*;q=0.5'};
    var CHALLENGE = /just a moment|attention required|security check|verify you are human/i;
    var slots = {free: 24, waiting: []};
    var renders = {free: 2, waiting: []};

    function take(pool) {
        if (pool.free > 0) { pool.free--; return Promise.resolve(); }
        return new Promise(function (resolve) { pool.waiting.push(resolve); });
    }
    function give(pool) {
        var next = pool.waiting.shift();
        if (next) next(); else pool.free++;
    }
    function bytesOf(base64) {
        var binary = atob(base64 || '');
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        return bytes;
    }
    function base64Of(text) {
        return btoa(unescape(encodeURIComponent(text)));
    }
    function transport(details) {
        if (typeof window.NovelDBTransport !== 'function') return Promise.resolve({error: 'no transport'});
        return Promise.resolve(window.NovelDBTransport(details)).then(function (reply) {
            return typeof reply === 'string' ? JSON.parse(reply) : reply;
        }, function (error) { return {error: String((error && error.message) || error)}; });
    }

    /**
     * A plain request: {status, url, bytes, headers, text()} or null. `quick`: a short timeout, for
     * guesses. A GET a site refuses (its bot check) is read in a hidden page instead, as the
     * desktop's BrowserFetcher does (not for quick guesses).
     */
    function fetchUrl(url, options) {
        options = options || {};
        var headers = Object.assign({}, USER_HEADERS, options.headers || {});
        if (options.referer && !headers.Referer) headers.Referer = options.referer;
        var details = {url: url, method: (options.method || 'GET').toUpperCase(), headers: headers,
                       responseType: 'arraybuffer', timeout: options.quick ? 8000 : 15000};
        if (options.data != null) details.dataBase64 = base64Of(String(options.data));
        if (stopped(options.stop)) return Promise.resolve(null);
        // `priority`: a few small reads (NovelDB's index) that never wait behind the sites' pages.
        var pool = options.pool || (options.priority ? {free: 1, waiting: []} : slots);
        return take(pool).then(function () {
            // A search or top list stopped while this waited for a slot: its request is not sent.
            return stopped(options.stop) ? {error: 'stopped'} : transport(details);
        }).then(function (reply) {
            give(pool);
            var result = reply && !reply.error ? wrap(reply, url) : null;
            if (details.method === 'GET' && !options.binary && !options.quick && refused(result) && !stopped(options.stop)) {
                note('Loading ' + short(url) + ' in a browser page…');
                return render(url).then(function (rendered) { return rendered || result; });
            }
            return result;
        }, function () { give(pool); return null; });
    }
    function wrap(reply, url) {
        var bytes = reply.responseBase64 ? bytesOf(reply.responseBase64) : new TextEncoder().encode(reply.responseText || '');
        var text = null;
        return {
            status: Number(reply.status) || 0,
            url: reply.finalUrl || url,
            bytes: bytes,
            headers: reply.responseHeaders || '',
            text: function () {
                if (text === null) text = G() ? G().decode(bytes, reply.responseHeaders || '') : new TextDecoder().decode(bytes);
                return text;
            }
        };
    }
    function refused(result) {
        if (!result) return true;
        if ([401, 403, 418, 429, 503].indexOf(result.status) >= 0) return true;
        return CHALLENGE.test(result.text().slice(0, 3000));
    }
    /** The page as a browser shows it after its scripts ran (a hidden page of the app). */
    function render(url) {
        return take(renders).then(function () {
            return transport({op: 'window', url: url, script: 'document.documentElement.outerHTML', delay: 1200});
        }).then(function (reply) {
            give(renders);
            if (!reply || reply.error || typeof reply.value !== 'string' || !reply.value) return null;
            var html = reply.value;
            return {status: 200, url: url, bytes: new TextEncoder().encode(html), headers: 'content-type: text/html; charset=utf-8',
                    text: function () { return html; }};
        }, function () { give(renders); return null; });
    }
    function short(url) {
        try {
            var parts = new URL(url);
            return parts.hostname.replace(/^www\./, '') + (parts.pathname.length > 1 ? parts.pathname.slice(0, 40) : '');
        } catch (e) { return url; }
    }
    function stopped(token) { return token && token.stopped; }

    /** `fn` over `items`, `limit` at a time; resolves when all are done or `token` stops. */
    function each(items, limit, fn, token) {
        var next = 0;
        function worker() {
            if (next >= items.length || stopped(token)) return Promise.resolve();
            var index = next++;
            return Promise.resolve().then(function () { return fn(items[index], index); })
                .catch(function () { /* one source failing stops nothing */ }).then(worker);
        }
        var workers = [];
        for (var i = 0; i < Math.min(limit, items.length); i++) workers.push(worker());
        return Promise.all(workers);
    }
    function within(promise, ms, token) {
        return new Promise(function (resolve) {
            var timer = setTimeout(function () { if (token) token.stopped = true; resolve('late'); }, ms);
            promise.then(function () { clearTimeout(timer); resolve('done'); });
        });
    }

    // -- the sites ---------------------------------------------------------------------------------

    var SITES = [], ID_TEMPLATES = [], siteHosts = {};
    function loadJson(name) {
        return fetch(BASE + name).then(function (r) { return r.json(); }).catch(function () { return null; });
    }
    var ready = Promise.all([loadJson('generic_sites.json'), loadJson('id_templates.json')]).then(function (found) {
        SITES = ((found[0] || {}).sites) || [];
        ID_TEMPLATES = ((found[1] || {}).templates) || [];
        SITES.forEach(function (site) { (site.hosts || [site.host]).forEach(function (host) { siteHosts[host] = site; }); });
    });

    function host(url) {
        try { return new URL(url).hostname.toLowerCase().replace(/^www\./, ''); } catch (e) { return ''; }
    }
    function siteFor(url) {
        var name = host(url);
        if (siteHosts[name]) return siteHosts[name];
        var root = G().siteOf(name);
        for (var i = 0; i < SITES.length; i++) if (SITES[i].site === root) return SITES[i];
        return null;
    }
    /** An engine reads the site: a top site, an LNReader plugin's or a HaruNeko website's. */
    function knownSite(url) {
        return !!siteFor(url) || lnreaderKnows(url) || harunekoListed(url);
    }

    // -- telling books from other pages ------------------------------------------------------------

    var NO_RESULTS = /(no (items?|results?|books?|novels?|titles?|matches|products?)( were)? (found|matching|match)|nothing (was )?found|did not match any|didn't match any|(found|returned) (0|no) (results?|items?|matches)|\b0 results\b|no results for|見つかりませんでした|該当する.{0,6}(ありません|見つかりません)|検索結果は?0件|검색\s*결과가\s*없|결과가\s*없습니다|일치하는.{0,10}없|没有找到|未找到|沒有找到|找不到相关|暂无(结果|数据|相关)|無結果|无结果|查無|no se encontraron|sin resultados|aucun résultat|ничего не найдено|не найдено|keine (ergebnisse|treffer)|nenhum resultado)/i;
    var NOT_FOUND = /(\b404\b|not found|page not found|does not exist|doesn't exist|no longer available|has been removed|존재하지 않|삭제된|없는 (작품|페이지|소설)|ページが見つかりません|存在しません|不存在|已下架|找不到)/i;
    var FIGURES = ['published', 'updated', 'rating', 'ratings', 'likes', 'views', 'chapters'];
    function isNoResults() {
        for (var i = 0; i < arguments.length; i++) if (NO_RESULTS.test(arguments[i] || '')) return true;
        return false;
    }
    var LISTING = /^(search|tags?|genres?|categor(y|ies)|authors?|users?|members?|forums?|login|register|latest|popular|ranking|rankings|top|list|library|blogs?|contests?|novel-list|manga-list|originals|canvas|page|contact|contact-us|about|about-us|privacy|privacy-policy|terms|terms-of-service|dmca|faq|feedback|support|donate)$/i;

    /** A book's page rather than a chapter, a list, a profile or the home page. */
    function looksLikeBook(url, title) {
        var parts;
        try { parts = new URL(url); } catch (e) { return false; }
        var path = parts.pathname.replace(/\/+$/, '');
        if (!path || path.split('/').length < 2) return false;
        var segments = path.split('/').filter(function (s) { return s; });
        if (segments.length && /^[a-z]{2}(-[a-z]{2})?$/i.test(segments[0])) segments = segments.slice(1);
        if (!segments.length) return false;
        var hasId = /(^|&)(title_no|id|book_?id|novel_?id|bid|no)=/i.test(parts.search.replace(/^\?/, ''));
        if (LISTING.test(segments[0]) && !hasId && !/^(novel|book|series|story|fiction|manga|comic|webtoon|title|works?)$/i.test(segments[0])) return false;
        if (segments.length === 1 && LISTING.test(segments[0])) return false;
        var last = path.split('/').pop();
        if (/(chapter|chap|ch|episode|ep)[-_.]?\d/i.test(last)
            || (/^\d{1,5}(\.html?)?$/.test(last) && /\/(novel|book|series|story|fiction|manga|comic|title)\/[^/]+\/\d+/i.test(path))) return false;
        if (title && G().CHAPTER_WORDS.test(title) && /\d/.test(title) && !/(novel|manga|webtoon|manhwa|series)/i.test(title)) return false;
        return true;
    }
    function unescapeHtml(text) {
        var area = document.createElement('textarea');
        area.innerHTML = text;
        return area.value;
    }
    function cleanTitle(title, url) {
        title = unescapeHtml(String(title || '').replace(/<[^>]+>/g, '')).trim();
        title = G().cleanPageTitle(title, url);
        title = title.replace(/^(read\s+)/i, '');
        title = title.replace(/\s+(novel|light novel|manga|manhwa|webtoon)?\s*(online)?\s*(for\s+)?free.*$/i, '');
        return title.replace(/^[ \-|–—]+|[ \-|–—]+$/g, '').slice(0, 200);
    }

    // Sites that address their books by one number: the ID search tries them with other IDs.
    var LEARNED_KEY = 'noveldb.generic.idTemplates';
    var learned = store(LEARNED_KEY) || {};
    function learnTemplate(url) {
        if (!looksLikeBook(url)) return;
        var parts = new URL(url);
        var numbers = (parts.pathname + '?' + parts.search.replace(/^\?/, '')).match(/(^|[^\d])(\d{3,12})(?!\d)/g) || [];
        numbers = numbers.map(function (n) { return n.replace(/^[^\d]/, ''); });
        if (numbers.length !== 1) return;
        var site = G().siteOf(host(url));
        var whole = parts.protocol + '//' + parts.host + parts.pathname + parts.search;
        var template = whole.replace(new RegExp('(^|[^\\d])' + numbers[0] + '(?!\\d)'), function (m, lead) { return lead + '{id}'; });
        if (site && !learned[site] && Object.keys(learned).length < 400) {
            learned[site] = template;
            store(LEARNED_KEY, learned);
        }
    }

    function card(url, title, snippet, source, cover) {
        learnTemplate(url);
        var site = siteFor(url);
        return {url: url, title: cleanTitle(title, url) || url, author: '', cover: cover || '',
                description: String(snippet || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim().slice(0, 600), tags: [],
                site: (site && site.site) || G().siteOf(host(url)), comic: !!(site && site.comic), source: source || 'web',
                complete: false};
    }

    // -- NovelDB's own books (noveldb/core/catalog/noveldb_index.py; scripts/noveldb_index/build.cjs) --
    // The index's table of contents is read once a day; a search reads one word bucket per query word
    // and the card blocks of the books found, with Range requests. A synopsis comes from NovelDB's
    // synopsis shard for the book when its card is opened.

    var NDB_INDEX = 'https://github.com/Shirochi-stack/NovelDB/releases/download/engines/';
    var NDB_PAGES = 'https://shirochi-stack.github.io/NovelDB/data/';
    var NDB_KEY = 'noveldb.generic.ndbIndex', NDB_HOURS = 24;
    var NDB_STOP = new Set(['the', 'a', 'an', 'of', 'to', 'in', 'and', 'is', 'on', 'at', 'for', 'with']);
    var NDB_CJK = /[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]/;
    // Format 2: each token's books per source, in pages (scripts/noveldb_index/build.cjs). Results take
    // the next book of each source in turn, the lead source's (npia) first; more pages as the user scrolls.
    var NDB_FORMAT = 2, NDB_LEAD = 'npia', NDB_MULTI_PAGES = 40;
    var ndb = {manifest: null, pieces: new Map(), loading: null, reading: 0, idle: []};
    /** The search index's reads in flight: synopses wait until there are none (on a phone every reply
     *  goes through the page, and a synopsis shard is many times an index piece). */
    function ndbReading(asked) {
        ndb.reading++;
        var done = function () { if (--ndb.reading === 0) ndb.idle.splice(0).forEach(function (go) { go(); }); };
        asked.then(done, done);
        return asked;
    }
    function ndbIdle() { return ndb.reading ? new Promise(function (go) { ndb.idle.push(go); }) : Promise.resolve(); }

    function ndbNormal(text) { return String(text || '').normalize('NFKC').toLowerCase(); }
    function ndbTokens(text) {
        var out = [];
        (ndbNormal(text).match(/[\p{L}\p{N}]+/gu) || []).forEach(function (word) {
            var add = function (token) { if (out.indexOf(token) < 0) out.push(token); };
            if (NDB_CJK.test(word)) {
                if (word.length <= 2) add(word);
                for (var i = 0; i + 2 <= word.length; i++) add(word.slice(i, i + 2));
            } else if (word.length >= 2 && !NDB_STOP.has(word)) {
                add(word);
            }
        });
        return out;
    }
    function ndbTag(tag) { return '#' + ndbNormal(tag).replace(/[\s_\-]+/g, ' ').trim(); }
    /** A tag's alias key (the builder's tagConcept): "Gender Bender" and "Genderbend (TS)" share "=genderbend". */
    var NDB_BRACKETED = /[(\[{（【][^)\]}）】]*[)\]}）】]/g, NDB_UNION_PAGES = 1;  // the first page of each alias, already in its bucket
    function ndbConcept(tag) {
        var text = ndbNormal(tag);
        var letters = function (value) { return value.replace(/[^\p{L}\p{N}]+/gu, ''); };
        var key = letters(text.replace(NDB_BRACKETED, ' ')) || letters(text);
        if (key.length > 5 && !NDB_CJK.test(key)) key = key.replace(/(ing|er|s)$/, '');
        return key ? '=' + key : '';
    }
    function ndbBucket(token, buckets) {
        var hash = 0x811c9dc5;
        new TextEncoder().encode(token).forEach(function (byte) { hash = Math.imul(hash ^ byte, 0x01000193) >>> 0; });
        return hash % buckets;
    }
    function gunzip(bytes) {
        return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
    }
    function ndbManifest() {
        if (ndb.manifest && Date.now() - ndb.manifest._read < NDB_HOURS * 3600e3) return Promise.resolve(ndb.manifest);
        var kept = store(NDB_KEY);
        if (kept && kept._read && kept.format === NDB_FORMAT && Date.now() - kept._read < NDB_HOURS * 3600e3) { ndb.manifest = kept; return Promise.resolve(kept); }
        if (ndb.loading) return ndb.loading;
        ndb.loading = ndbReading(fetchUrl(NDB_INDEX + 'noveldb-search.json', {binary: true, priority: true})).then(function (result) {
            ndb.loading = null;
            if (!result || result.status >= 400) return ndb.manifest;
            var found;
            try { found = JSON.parse(new TextDecoder().decode(result.bytes)); } catch (e) { return ndb.manifest; }
            if (found.format !== NDB_FORMAT) return null;  // an index for another version of the app
            found._read = Date.now();
            ndb.manifest = found;
            ndb.pieces.clear();
            store(NDB_KEY, found);
            return found;
        });
        return ndb.loading;
    }
    /** A word bucket or card block by its number, or a list page (`place`: its [offset, length]). */
    function ndbPiece(manifest, kind, index, retried, place) {
        var key = manifest.built + '|' + kind + '|' + index;
        if (ndb.pieces.has(key)) return ndb.pieces.get(key);
        var at = place || (kind === 'bucket' ? manifest.bucketAt : manifest.blockAt)[index];
        var asked = ndbReading(fetchUrl(NDB_INDEX + manifest.bin, {binary: true, priority: true, headers: {Range: 'bytes=' + at[0] + '-' + (at[0] + at[1] - 1)}}))
            .then(function (result) {
                if (result && result.status === 404 && !retried && !place) {
                    // A table of contents kept from an earlier build points at data a newer build replaced.
                    ndb.pieces.delete(key);
                    ndb.manifest = null;
                    store(NDB_KEY, null);
                    return ndbManifest().then(function (fresh) { return fresh ? ndbPiece(fresh, kind, index, true) : null; });
                }
                // A server that ignores the range sends the whole file: not something to read here.
                if (!result || (result.status !== 200 && result.status !== 206) || result.bytes.length !== at[1]) return null;
                return gunzip(result.bytes).then(JSON.parse);
            }).catch(function () { return null; });
        ndb.pieces.set(key, asked);
        if (ndb.pieces.size > 512) ndb.pieces.delete(ndb.pieces.keys().next().value);
        return asked;
    }
    function ndbUndelta(values) {
        var total = 0;
        return (values || []).map(function (value) { return (total += value); });
    }
    /** One source's books for a token: the first page from the bucket, further pages read as needed. */
    function ndbList(manifest, first, more) {
        var page = manifest.page || 500;
        var list = {head: first, total: more ? more[0] : first.length, first: first.length ? first[0] : Infinity, page: page};
        list.pageOf = function (number) {
            if (number === 0) return Promise.resolve(list.head);
            if (!more || number > more[2].length) return Promise.resolve([]);
            return ndbPiece(manifest, 'page', more[1] + number - 1, false, more[2][number - 1]).then(ndbUndelta);
        };
        list.numbers = function (start, end) {
            end = Math.min(end, list.total);
            if (end <= start) return Promise.resolve([]);
            var wanted = [];
            for (var n = Math.floor(start / page); n <= Math.floor((end - 1) / page); n++) wanted.push(list.pageOf(n));
            return Promise.all(wanted).then(function (pages) {
                var skip = start - Math.floor(start / page) * page;
                return [].concat.apply([], pages).slice(skip, skip + end - start);
            });
        };
        return list;
    }
    function ndbFixed(numbers) {
        return {head: numbers, total: numbers.length, first: numbers.length ? numbers[0] : Infinity, page: Math.max(1, numbers.length),
                pageOf: function () { return Promise.resolve(numbers); },
                numbers: function (start, end) { return Promise.resolve(numbers.slice(start, end)); }};
    }
    function ndbLists(manifest, token) {
        return ndbPiece(manifest, 'bucket', ndbBucket(token, manifest.buckets)).then(function (bucket) {
            var entry = (bucket && bucket[token]) || {}, out = {};
            Object.keys(entry).forEach(function (source) {
                if (source.charAt(0) !== '+' && entry[source].length) out[source] = ndbList(manifest, ndbUndelta(entry[source]), entry['+' + source]);
            });
            return out;
        });
    }
    /** Merged lists: `head` (merged up to where every list was read), then `rest`'s books after its first `skip`. */
    function ndbChain(head, rest, skip) {
        var size = head.length, page = rest.page;
        var chain = {head: head, page: page, total: size + Math.max(0, rest.total - skip), first: size ? head[0] : rest.first};
        chain.numbers = function (start, end) {
            end = Math.min(end, chain.total);
            if (end <= start) return Promise.resolve([]);
            var own = head.slice(start, Math.min(end, size));
            if (end <= size) return Promise.resolve(own);
            return rest.numbers(skip + Math.max(0, start - size), skip + end - size).then(function (more) { return own.concat(more); });
        };
        chain.pageOf = function (number) { return chain.numbers(number * page, number * page + page); };
        return chain;
    }
    /** A tag's books in each source: its own token's and those of the tags sharing its alias key, merged. */
    function ndbTagLists(manifest, tag) {
        var key = ndbConcept(tag), first = ndbTag(tag);
        var aliases = key ? ndbPiece(manifest, 'bucket', ndbBucket(key, manifest.buckets)).then(function (bucket) {
            var found = bucket && bucket[key];
            return Array.isArray(found) ? found : [];
        }) : Promise.resolve([]);
        return aliases.then(function (more) {
            var wanted = [first].concat(more.filter(function (token) { return typeof token === 'string' && token !== first; }));
            return Promise.all(wanted.map(function (token) { return ndbLists(manifest, token); }));
        }).then(function (found) {
            var out = {}, sources = {};
            found.forEach(function (lists) { Object.keys(lists).forEach(function (source) { (sources[source] = sources[source] || []).push(lists[source]); }); });
            // A tag and its translation list the same books: read them once.
            var same = function (a, b) { return a.total === b.total && a.head.length === b.head.length && a.head.every(function (n, i) { return n === b.head[i]; }); };
            return Promise.all(Object.keys(sources).map(function (source) {
                var lists = sources[source].filter(function (list, i, all) { return !all.slice(0, i).some(function (other) { return same(list, other); }); });
                if (lists.length === 1) { out[source] = lists[0]; return null; }
                return Promise.all(lists.map(function (list) { return list.numbers(0, NDB_UNION_PAGES * list.page); })).then(function (read) {
                    // A list read only in part says nothing of the books after its last one read: the merge
                    // stops there, and the longest list's own books follow.
                    var cutoff = Infinity, seen = new Set();
                    read.forEach(function (numbers, i) { if (numbers.length && numbers.length < lists[i].total) cutoff = Math.min(cutoff, numbers[numbers.length - 1]); });
                    read.forEach(function (numbers) { numbers.forEach(function (n) { if (n <= cutoff) seen.add(n); }); });
                    var numbers = Array.from(seen).sort(function (a, b) { return a - b; });
                    if (cutoff === Infinity) { if (numbers.length) out[source] = ndbFixed(numbers); return; }
                    var longest = 0;
                    lists.forEach(function (list, i) { if (list.total > lists[longest].total) longest = i; });
                    out[source] = ndbChain(numbers, lists[longest], read[longest].filter(function (n) { return n <= cutoff; }).length);
                });
            })).then(function () { return out; });
        });
    }
    /** Each source's books with every token (for several: those in all of them, among their first pages). */
    var NDB_INTERSECT_LISTS = 4;  // the shortest lists of a search's words intersected (per source)
    function ndbStreams(manifest, wanted) {
        return Promise.all(wanted.map(function (token) { return ndbLists(manifest, token); })).then(function (perToken) {
            if (perToken.some(function (lists) { return !Object.keys(lists).length; })) return {};
            if (perToken.length === 1) return perToken[0];
            var sources = Object.keys(perToken[0]).filter(function (source) { return perToken.every(function (lists) { return lists[source]; }); });
            var out = {};
            return Promise.all(sources.map(function (source) {
                // The source's shortest lists only (a long title's common pieces, such as "生活" in 6,000
                // titles, are not read page by page): every book with the whole title is in them too.
                var lists = perToken.map(function (lists) { return lists[source]; })
                    .sort(function (a, b) { return a.total - b.total; }).slice(0, NDB_INTERSECT_LISTS);
                return Promise.all(lists.map(function (list) { return list.numbers(0, NDB_MULTI_PAGES * list.page); })).then(function (read) {
                    // A list read only in part says nothing of the books after its last one read.
                    var cutoff = Infinity;
                    read.forEach(function (numbers, i) { if (numbers.length && numbers.length < lists[i].total) cutoff = Math.min(cutoff, numbers[numbers.length - 1]); });
                    var others = read.slice(1).map(function (numbers) { return new Set(numbers); });
                    var numbers = read[0].filter(function (n) { return n <= cutoff && others.every(function (set) { return set.has(n); }); })
                        .sort(function (a, b) { return a - b; });
                    if (numbers.length) out[source] = ndbFixed(numbers);
                });
            })).then(function () { return out; });
        });
    }
    /** Places `offset` to `offset + count` of the order that takes each list's next book in turn. */
    function ndbRoundRobin(totals, offset, count) {
        var out = [], place = 0, longest = 0, sum = 0;
        totals.forEach(function (t) { longest = Math.max(longest, t[1]); sum += t[1]; });
        for (var round = 0; round < longest && out.length < count; round++) {
            var active = totals.filter(function (t) { return t[1] > round; });
            if (place + active.length <= offset) { place += active.length; continue; }
            active.forEach(function (t) { if (place >= offset && out.length < count) out.push([t[0], round]); place++; });
        }
        return {picks: out, more: offset + out.length < sum};
    }
    function ndbCount(value) { var n = Number(value) || 0; return n > 0 ? n.toLocaleString('en-US') : ''; }
    /** {cards, more}: `limit` of NovelDB's books for a title or tag search, from place `offset` of its
     *  results. `each(card)`: each card as soon as its block is read; `onTotal(n)`: how many books
     *  match, as soon as that is known (before any card is read). Mode 'source': `text` names sources
     *  ("npia,sfc") and the results are their books, the most read first (their Top lists). */
    var NDB_FIRST = 12, NDB_MORE = 40;  // the first batch is small, to show quickly; scrolling reads the rest
    function ndbSourceLists(manifest, names) {
        return Promise.all(names.map(function (name) { return ndbLists(manifest, '!' + name); })).then(function (found) {
            var out = {};
            found.forEach(function (lists, i) { if (lists[names[i]]) out[names[i]] = lists[names[i]]; });
            return out;
        });
    }
    function ndbSearch(text, mode, limit, offset, each, onTotal) {
        var wanted = mode === 'source' ? String(text).split(',').filter(Boolean) : mode === 'tag' ? [ndbTag(text)]
            : mode === 'author' ? ['@' + ndbNormal(text).replace(/\s+/g, ' ').trim()] : ndbTokens(text);
        limit = limit || NDB_MORE;
        if (!wanted.length) return Promise.resolve({cards: [], more: false});
        var more = false;
        return ndbManifest().then(function (manifest) {
            if (!manifest) return [];
            var lists = mode === 'source' ? ndbSourceLists(manifest, wanted) : mode === 'tag' ? ndbTagLists(manifest, text) : ndbStreams(manifest, wanted);
            return lists.then(function (streams) {
                var order = Object.keys(streams).sort(function (a, b) {
                    return (a !== NDB_LEAD) - (b !== NDB_LEAD) || streams[a].first - streams[b].first;
                });
                if (onTotal) onTotal(order.reduce(function (sum, name) { return sum + streams[name].total; }, 0));
                var merged = ndbRoundRobin(order.map(function (name) { return [name, streams[name].total]; }), offset || 0, limit);
                more = merged.more;
                return Promise.all(merged.picks.map(function (pick) {
                    var list = streams[pick[0]];
                    return list.pageOf(Math.floor(pick[1] / list.page)).then(function (numbers) { return numbers[pick[1] % list.page]; });
                }));
            }).then(function (numbers) {
                // A book on several of the sources asked is in each one's list: shown once.
                numbers = numbers.filter(function (n, i) { return n != null && numbers.indexOf(n) === i; });
                return Promise.all(numbers.map(function (number) {
                    return ndbPiece(manifest, 'block', Math.floor(number / manifest.block)).then(function (block) {
                        var row = block && block[number % manifest.block];
                        if (!row) return null;
                        var url = row[11];
                        return {url: url, title: row[3] || row[2], originalTitle: row[3] ? row[2] : '', author: row[4] || '',
                                cover: row[5] || '', description: '', tags: row[6] || [], site: G().siteOf(host(url)), comic: false,
                                source: 'noveldb', noveldb: {source: row[0], id: row[1]}, complete: false,
                                views: ndbCount(row[7]), likes: ndbCount(row[8]), chapters: ndbCount(row[9]),
                                status: row[10] === 1 ? 'Completed' : row[10] === 0 ? 'Ongoing' : '', updated: String(row[12] || '').slice(0, 10),
                                published: String(row[15] || '').slice(0, 10), also: ndbAlso(row[16])};
                    }).then(function (card) { if (card && each) each(card); return card; });
                }));
            });
        }).then(function (cards) { return {cards: (cards || []).filter(Boolean), more: more}; });
    }
    // The book's pages on NovelDB's other sources (one book there: the index merged them).
    function ndbAlso(others) {
        return (Array.isArray(others) ? others : []).filter(function (o) { return Array.isArray(o) && o[2]; })
            .map(function (o) { return {url: o[2], noveldb: {source: o[0], id: o[1]}}; });
    }
    function ndbShard(id, count) {
        var text = String(id || '');
        if (/^-?\d+$/.test(text) && Number.isSafeInteger(Number(text))) return Math.abs(Number(text)) % count;
        var value = 0;
        for (var i = 0; i < text.length; i++) value = ((value * 31) + text.charCodeAt(i)) >>> 0;
        return value % count;
    }
    // The synopsis shards read lately (a shard holds hundreds of books' synopses): one read for every
    // card of a search that falls in it.
    // Shards are 80-300 KB each: two at a time, so they never crowd out the search index's small reads.
    var ndbShards = new Map(), shardSlots = {free: 2, waiting: []};
    function ndbShardFile(url) {
        if (ndbShards.has(url)) return ndbShards.get(url);
        var asked = ndbIdle().then(function () { return fetchUrl(url, {binary: true, pool: shardSlots}); }).then(function (result) {
            if (!result || result.status >= 400) { ndbShards.delete(url); return {}; }
            var bytes = result.bytes;
            var text = bytes[0] === 0x1f && bytes[1] === 0x8b ? gunzip(bytes) : Promise.resolve(new TextDecoder().decode(bytes));
            return text.then(JSON.parse);
        }).catch(function () { ndbShards.delete(url); return {}; });
        ndbShards.set(url, asked);
        if (ndbShards.size > 24) ndbShards.delete(ndbShards.keys().next().value);
        return asked;
    }
    /** A NovelDB book's synopsis, from the site's synopsis shard for it. */
    function ndbDescription(card) {
        var ref = card.noveldb || {};
        return ndbManifest().then(function (manifest) {
            var info = manifest && manifest.sources && manifest.sources[ref.source] && manifest.sources[ref.source].descriptions;
            if (!info || !ref.id) return '';
            var shard = String(ndbShard(ref.id, info.count || 128)).padStart(3, '0');
            return ndbShardFile(NDB_PAGES + info.prefix + shard + '.json.gz').then(function (all) {
                return String((all || {})[String(ref.id)] || '').replace(/\\n/g, '\n').trim();
            });
        }).catch(function () { return ''; });
    }

    /** The text names the book (a word of its title): not the site's blurb shown on every page. */
    function namesBook(text, title) {
        var words = (String(title || '').toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) || [])
            .filter(function (w) { return ['manga', 'novel', 'read', 'online'].indexOf(w) < 0; });
        var low = String(text || '').toLowerCase();
        return !words.length || words.some(function (w) { return low.indexOf(w) >= 0; });
    }
    /** `next` replaces `old`: it names the book and `old` doesn't (a site blurb), else it is longer. */
    function betterSynopsis(next, old, title, fromPage) {
        if (!old) return true;
        var a = namesBook(next, title), b = namesBook(old, title);
        return a !== b ? a : (!b && fromPage) || next.length > old.length;
    }
    /** The card with what the book's page says. */
    function details(item) {
        if (item.source === 'noveldb') {
            // NovelDB's card has everything but its synopsis.
            return ndbDescription(item).then(function (text) { return Object.assign({}, item, {complete: true, description: text || item.description}); });
        }
        return fetchUrl(item.url).then(function (result) {
            if (!result || result.status >= 400) return Object.assign({}, item, {complete: true});
            var meta = G().metadata(G().parse(result.text()), result.url || item.url);
            if (isNoResults(meta.title, String(meta.description || '').slice(0, 400))) return Object.assign({}, item, {complete: true, empty: true});
            var merged = Object.assign({}, item, {complete: true});
            ['title', 'author', 'cover', 'description', 'tags', 'status'].concat(FIGURES).forEach(function (name) {
                var value = meta[name];
                var filled = Array.isArray(value) ? value.length : value;
                if (filled && (name !== 'description' || betterSynopsis(value, String(item.description || ''), meta.title || item.title, true))) merged[name] = value;
            });
            return merged;
        });
    }

    // -- a site's own search (site_search.py) ------------------------------------------------------

    var COMMON_SEARCHES = ['/?s={q}', '/search?q={q}', '/search?keyword={q}', '/search?query={q}', '/search/{q}',
                           '/search?title={q}', '/?post_type=wp-manga&s={q}'];
    var CHROME = /(^|[\s_-])(nav|navbar|menu|gnb|lnb|snb|header|footer|breadcrumbs?|pagination|pager|sidebar|login|account|util|toolbar|topbar|top-bar|dropdown|modal|popup|banner|notice|cookie)([\s_-]|$)/i;
    var TAG_LINK = /\/(genres?|tags?|categor(y|ies)|keywords?|genero|generos|kategori|theme|themes|type|directory|manga-genre|manga_genre|series-genre)s?[/=]/i;
    var ACCOUNT_FIELD = /(user|login|email|mail|pass|code|phone|account|nick|captcha)/i;
    var POPULAR_PATHS = ['/popular', '/ranking', '/rankings', '/rank', '/top', '/trending', '/most-popular',
                         '/manga?order=popular', '/series?order=popular', '/comics?order=popular', '/manga/?m_orderby=views',
                         '/directory?sort=popular', '/browse?sort=popular', '/list?sort=views'];
    var PLANS_KEY = 'noveldb.generic.plans', PLAN_DAYS = 7;
    var plans = store(PLANS_KEY) || {};
    var planning = {};
    function savePlans() {
        var keys = Object.keys(plans);
        if (keys.length > 300) keys.slice(0, keys.length - 300).forEach(function (k) { delete plans[k]; });
        store(PLANS_KEY, plans);
    }

    function cardShape(url) {
        var parts = new URL(url);
        var segments = parts.pathname.split('/').filter(function (s) { return s; });
        var first = segments.length > 1 ? segments[0].replace(/\d+/g, '{n}') : '';
        var query = parts.search.replace(/^\?/, '').split('&').filter(function (q) { return q; })
            .map(function (q) { return q.split('=')[0]; }).sort().join('&');
        return parts.host.replace(/^www\./, '') + '|' + segments.length + '|' + first + '|' + query;
    }
    function sameSite(url, base) { return G().siteOf(host(url)) === G().siteOf(host(base)); }
    function words(text) {
        return (String(text || '').toLowerCase().match(/[\p{L}\p{N}_']+/gu) || [])
            .filter(function (w) { return w.length > 1 || /[^\x00-\x7f]/.test(w); });
    }
    function relevant(title, query) {
        var want = words(query), have = words(title).join(' ');
        if (!want.length) return true;
        if (want.length === 1 && /[^\x00-\x7f]/.test(want[0])) return have.replace(/ /g, '').indexOf(want[0]) >= 0;
        var hits = want.filter(function (w) { return have.indexOf(w) >= 0; }).length;
        return hits >= Math.max(1, want.length - Math.floor(want.length / 3));
    }
    function tagName(name) { return String(name || '').trim().toLowerCase().replace(/[\s_\-]+/g, ' '); }

    function openSearch(doc, base) {
        var link = Array.prototype.find.call(doc.querySelectorAll('link[href][type]'), function (l) {
            return /opensearchdescription/i.test(l.getAttribute('type'));
        });
        if (!link) return Promise.resolve(null);
        return fetchUrl(G().cleanUrl(link.getAttribute('href'), base), {quick: true}).then(function (result) {
            if (!result || result.status !== 200) return null;
            var text = result.text(), match, pattern = /<Url\b([^>]*)>/gi;
            while ((match = pattern.exec(text))) {
                var kind = /type="([^"]+)"/.exec(match[1]), template = /template="([^"]+)"/.exec(match[1]);
                if (template && (!kind || kind[1].indexOf('html') >= 0)) {
                    var url = template[1].replace(/&amp;/g, '&');
                    if (url.indexOf('{searchTerms}') >= 0) {
                        url = url.replace('{searchTerms}', '{q}').replace(/[?&][^=&?]+=\{(?!q\})[^}]*\}/g, '');
                        if (url.indexOf('?') < 0 && url.indexOf('&') >= 0) url = url.replace('&', '?');
                        return {kind: 'get', template: url};
                    }
                }
            }
            return null;
        });
    }
    function searchForm(doc, base) {
        var best = null;
        Array.prototype.forEach.call(doc.querySelectorAll('form'), function (form) {
            var fields = Array.prototype.slice.call(form.querySelectorAll('input'));
            var type = function (f) { return (f.getAttribute('type') || 'text').toLowerCase(); };
            var text = fields.filter(function (f) {
                return (type(f) === 'text' || type(f) === 'search') && f.getAttribute('name')
                    && f.getAttribute('name').toLowerCase().indexOf('pass') < 0;
            });
            if (!text.length || fields.some(function (f) {
                return type(f) !== 'hidden' && type(f) !== 'submit' && ACCOUNT_FIELD.test(f.getAttribute('name') || '');
            })) return;
            var name = text[0].getAttribute('name');
            var hint = [form.getAttribute('action') || '', form.getAttribute('id') || '', form.getAttribute('class') || '',
                        name, text[0].getAttribute('placeholder') || ''].join(' ').toLowerCase();
            var score = 2 * (hint.indexOf('search') >= 0) + (name === 'q') + (name === 's') + (hint.indexOf('keyword') >= 0)
                + (hint.indexOf('searchkey') >= 0) - 3 * (/login|comment|mail/.test(hint) ? 1 : 0);
            if (best === null || score > best[0]) {
                var hidden = {};
                fields.forEach(function (f) { if (type(f) === 'hidden' && f.getAttribute('name')) hidden[f.getAttribute('name')] = f.getAttribute('value') || ''; });
                best = [score, {kind: (form.getAttribute('method') || 'get').toLowerCase(),
                                action: G().cleanUrl(form.getAttribute('action') || base, base) || base, field: name, hidden: hidden}];
            }
        });
        return best && best[0] >= 0 ? best[1] : null;
    }
    function tagLinks(doc, base) {
        var tags = {};
        Array.prototype.forEach.call(doc.querySelectorAll('a[href]'), function (anchor) {
            var href = G().cleanUrl(anchor.getAttribute('href'), base);
            if (!href || !sameSite(href, base) || !TAG_LINK.test(href)) return;
            var name = G().textOf(anchor).trim().toLowerCase().replace(/\s*\(\d+\)$/, '');
            if (name.length > 1 && name.length <= 40 && !(name in tags)) tags[name] = href;
        });
        return tags;
    }

    /** How to search `home`'s site: {ways, tags, home, home_cards, reachable}; remembered for a week. */
    function plan(home) {
        var known = plans[home];
        if (known && Date.now() / 1000 - (known.checked || 0) < PLAN_DAYS * 86400 && known.home_cards) return Promise.resolve(known);
        if (planning[home]) return planning[home];
        planning[home] = fetchUrl(home).then(function (result) {
            var made = {checked: Date.now() / 1000, ways: [], tags: {}, home: home, reachable: !!(result && result.status < 400)};
            var steps = Promise.resolve();
            if (result && result.status < 400) {
                var final = result.url || home, html = result.text(), doc = G().parse(html);
                made.home = final;
                steps = openSearch(doc, final).then(function (found) {
                    if (found) made.ways.push(found);
                    var form = searchForm(doc, final);
                    if (form) made.ways.push(form);
                    made.tags = tagLinks(doc, final);
                    made.home_cards = extractCards(html, final).map(function (c) { return c.url; }).slice(0, 80);
                });
            }
            return steps.then(function () {
                var origin = new URL(made.home).origin;
                COMMON_SEARCHES.forEach(function (path) { made.ways.push({kind: 'get', template: origin + path, guess: true}); });
                plans[home] = made;
                savePlans();
                delete planning[home];
                return made;
            });
        }).catch(function () { delete planning[home]; return {ways: [], tags: {}, home: home, reachable: false}; });
        return planning[home];
    }
    function query(way, text, token) {
        if (way.kind === 'get' && way.template) {
            var value = way.template.indexOf('/{q}') >= 0 ? encodeURIComponent(text) : encodeURIComponent(text).replace(/%20/g, '+');
            return fetchUrl(way.template.replace('{q}', value), {quick: !!way.guess, stop: token});
        }
        var data = new URLSearchParams(Object.assign({}, way.hidden || {}));
        data.set(way.field, text);
        if (way.kind === 'post') {
            return fetchUrl(way.action, {method: 'POST', data: data.toString(), stop: token,
                                         headers: {'Content-Type': 'application/x-www-form-urlencoded'}});
        }
        return fetchUrl(way.action + (way.action.indexOf('?') >= 0 ? '&' : '?') + data.toString(), {stop: token});
    }

    // -- next pages (site_search.py next_page): each results page's next one, read as the list is scrolled --
    var NEXT_WORDS = /^\s*(next( page)?|older( posts| entries)?|more|›|»|>|>>|→|다음( 페이지)?|次へ|次のページ|下一页|下页|后页|siguiente|suivant|próxima|proxima|weiter|далее|следующая|التالي|selanjutnya|berikutnya|sonraki|tiếp)\s*[›»>→]*\s*$/i;
    var PAGE_IN_URL = /([?&](?:page|p|pg|paged|pageno|page_no|pagenum)=)(\d+)|(\/page\/)(\d+)/i;
    var nextPages = {};  // home|kind|query -> the next page of the list read last
    function sameSite(a, b) {
        try { return G().siteOf(new URL(a).hostname) === G().siteOf(new URL(b).hostname); } catch (e) { return false; }
    }
    function nextPage(html, url) {
        var doc = G().parse(html), link = doc.querySelector('link[rel~=next][href], a[rel~=next][href]');
        var href = link && G().cleanUrl(link.getAttribute('href'), url);
        if (href && href !== url && sameSite(href, url)) return href;
        var anchors = Array.prototype.slice.call(doc.querySelectorAll('a[href]'));
        for (var i = 0; i < anchors.length; i++) {
            var a = anchors[i], label = G().textOf(a) || a.getAttribute('aria-label') || a.getAttribute('title') || '';
            if (NEXT_WORDS.test(label) || /(^|[\s_-])next([\s_-]|$)/i.test(a.getAttribute('class') || '')) {
                href = G().cleanUrl(a.getAttribute('href'), url);
                if (href && href.replace(/\/+$/, '') !== url.replace(/\/+$/, '') && sameSite(href, url) && !/^javascript/i.test(href)) return href;
            }
        }
        var found = PAGE_IN_URL.exec(url);
        if (found) {
            // A page past the last one has no books: reading more stops there.
            var digits = found[2] || found[4], start = found.index + (found[1] || found[3]).length;
            return url.slice(0, start) + (Number(digits) + 1) + url.slice(start + digits.length);
        }
        var path = new URL(url).pathname.replace(/\/+$/, '');
        for (var j = 0; j < anchors.length; j++) {
            href = G().cleanUrl(anchors[j].getAttribute('href'), url);
            if (href && sameSite(href, url) && /([?&](page|p|paged)=2(&|$))|\/page\/2\/?($|\?)/.test(href)
                && new URL(href).pathname.split('/page/')[0].replace(/\/+$/, '') === path) return href;
        }
        return '';
    }
    function rememberNext(home, kind, text, result, url) {
        var where = (result && result.url) || url, key = home + '|' + kind + '|' + text;
        var found = result && where ? nextPage(result.text(), where) : '';
        if (found) nextPages[key] = found; else delete nextPages[key];
    }
    function hasMore(home, kind, text) { return !!nextPages[home + '|' + kind + '|' + text]; }
    /** The next page of the list a search, tag or popular list of `home` read last (its own next page is kept). */
    function siteMore(home, kind, text, token) {
        var key = home + '|' + kind + '|' + text, url = nextPages[key];
        delete nextPages[key];
        if (!url) return Promise.resolve([]);
        return fetchUrl(url, {stop: token}).then(function (result) {
            if (!result || result.status >= 400) return [];
            var cards = extractCards(result.text(), result.url || url);
            if (kind === 'search') cards = cards.filter(function (c) { return relevant(c.title, text); });
            if (cards.length) rememberNext(home, kind, text, result, url);
            return cards.slice(0, 40).map(function (c) { return kind === 'tag' ? Object.assign(c, {verified: true}) : c; });
        }, function () { return []; });
    }

    /** Cards from the site's own search for `text` (relevant ones only), within 20 seconds. */
    function siteSearch(home, text, token) {
        var started = Date.now();
        return plan(home).then(function (made) {
            if (made.reachable === false) return [];
            var ways = made.ways.slice(), index = 0;
            function next() {
                if (index >= ways.length || Date.now() - started > 20000 || stopped(token)) return Promise.resolve([]);
                var position = index++, way = ways[position];
                return query(way, text, token).then(function (result) {
                    if (result && [401, 403, 429, 503].indexOf(result.status) >= 0) return [];
                    if (wrongGuess(made, way, result)) return next();
                    if (!result || result.status >= 400) return next();
                    var cards = extractCards(result.text(), result.url || home).filter(function (c) { return relevant(c.title, text); });
                    if (!cards.length) return next();
                    if (position || way.guess) {
                        made.ways.splice(made.ways.indexOf(way), 1);
                        made.ways.unshift(Object.assign(way, {guess: false}));  // it answers: no longer a guess
                        savePlans();
                    }
                    rememberNext(home, 'search', text, result);
                    return cards.slice(0, 20);
                }, next);
            }
            return next();
        });
    }
    /** Cards from the site's page for `tag`, else its search for the tag. */
    function siteTag(home, tag, token) {
        return plan(home).then(function (made) {
            if (made.reachable === false) return [];
            var want = tagName(tag), names = {};
            Object.keys(made.tags || {}).forEach(function (name) { names[tagName(name)] = made.tags[name]; });
            var url = names[want] || (Object.keys(names).filter(function (n) { return n.indexOf(want) >= 0 || want.indexOf(n) >= 0; })
                .map(function (n) { return names[n]; })[0]);
            var own = url ? fetchUrl(url, {stop: token}).then(function (result) {
                if (!result || result.status >= 400) return [];
                rememberNext(home, 'tag', tag, result, url);
                return extractCards(result.text(), result.url || url).slice(0, 24).map(function (c) { return Object.assign(c, {verified: true}); });
            }) : Promise.resolve([]);
            return own.then(function (cards) {
                if (cards.length) return cards;
                var ways = made.ways.slice(0, 3), index = 0;
                function next() {
                    if (index >= ways.length || stopped(token)) return Promise.resolve([]);
                    var way = ways[index++];
                    return query(way, tag, token).then(function (result) {
                        if (wrongGuess(made, way, result)) return next();
                        if (!result || result.status >= 400 || !searched(result.url || '', made.home, tag)) return next();
                        var found = extractCards(result.text(), result.url || home);
                        if (homeAgain(found, made) || !found.length) return next();
                        rememberNext(home, 'tag', tag, result);
                        return found.slice(0, 24);
                    }, next);
                }
                return next();
            });
        });
    }
    /** A guessed search address the site has no such page for (404/410, or it leads back to the home
     *  page): it is wrong for every search, so it leaves the site's plan and is not tried again. */
    function wrongGuess(made, way, result) {
        if (!way || !way.guess || !result) return false;
        var home = String(made.home || '').replace(/\/+$/, ''), final = String(result.url || '').replace(/\/+$/, '');
        if (result.status !== 404 && result.status !== 410 && !(result.status < 400 && final === home)) return false;
        var at = made.ways.indexOf(way);
        if (at >= 0) { made.ways.splice(at, 1); savePlans(); }
        return true;
    }
    function searched(final, home, text) {
        if (final.replace(/\/+$/, '') === home.replace(/\/+$/, '')) return false;
        var wanted = tagName(text).split(' ').filter(function (w) { return w.length > 1; });
        var where;
        try { where = tagName(decodeURIComponent(final.replace(/\+/g, ' '))); } catch (e) { where = tagName(final); }
        return !wanted.length || wanted.some(function (w) { return where.indexOf(w) >= 0; });
    }
    function homeAgain(cards, made) {
        var home = new Set(made.home_cards || []);
        if (!home.size || !cards.length) return false;
        return cards.filter(function (c) { return home.has(c.url); }).length >= cards.length * 0.5;
    }
    /** Cards from the site's popular or ranking list, else the home page's biggest list. */
    function sitePopular(home, token) {
        return plan(home).then(function (made) {
            if (made.reachable === false) return [];
            var origin = new URL(made.home).origin, known = made.popular;
            var tries = (known ? [known] : []).concat(POPULAR_PATHS.map(function (p) { return origin + p; })
                .filter(function (u) { return u !== known; }));
            var index = 0;
            function next() {
                if (index >= tries.length || stopped(token)) {
                    return fetchUrl(made.home, {stop: token}).then(function (result) {
                        if (!result || result.status >= 400) return [];
                        rememberNext(home, 'popular', '', result, made.home);
                        return extractCards(result.text(), result.url || made.home).slice(0, 30);
                    });
                }
                var url = tries[index++];
                return fetchUrl(url, {quick: url !== known, stop: token}).then(function (result) {
                    if (!result || result.status >= 400) return next();
                    var cards = extractCards(result.text(), result.url || url);
                    if (cards.length < 6) return next();
                    if (made.popular !== url) { made.popular = url; savePlans(); }
                    rememberNext(home, 'popular', '', result, url);
                    return cards.slice(0, 30);
                }, next);
            }
            return next();
        });
    }

    // -- reading a results page --------------------------------------------------------------------

    function linksIn(node, base) {
        var out = new Set();
        Array.prototype.forEach.call(node.querySelectorAll('a[href]'), function (a) {
            var href = G().cleanUrl(a.getAttribute('href'), base);
            if (href) out.add(href);
        });
        return out;
    }
    function cardBox(anchor, url, base) {
        var box = anchor, wanted = cardShape(url);
        for (var parent = anchor.parentElement; parent; parent = parent.parentElement) {
            if (/^(BODY|HTML)$/.test(parent.nodeName)) break;
            var other = false;
            linksIn(parent, base).forEach(function (link) {
                if (!other && link !== url) { try { other = cardShape(link) === wanted; } catch (e) { /* not an address */ } }
            });
            if (other) break;
            box = parent;
        }
        return box;
    }
    function imageOf(node, base) {
        if (!node) return '';
        var image = node.nodeName === 'IMG' ? node : node.querySelector('img');
        if (!image) {
            var styled = Array.prototype.find.call(node.querySelectorAll('[style]'), function (el) {
                return /background(-image)?\s*:\s*url/i.test(el.getAttribute('style'));
            });
            if (styled) {
                var found = /url\(['"]?([^'")]+)/.exec(styled.getAttribute('style'));
                return found ? G().cleanUrl(found[1], base) : '';
            }
            return '';
        }
        var names = ['data-src', 'data-original', 'data-lazy-src', 'data-srcset', 'srcset', 'src'];
        for (var i = 0; i < names.length; i++) {
            var value = image.getAttribute(names[i]);
            if (value && value.indexOf('data:') !== 0) return G().cleanUrl(value.split(',')[0].trim().split(/\s+/)[0], base);
        }
        return '';
    }
    function inChrome(anchor) {
        var level = 0;
        for (var parent = anchor.parentElement; parent && level < 6; parent = parent.parentElement, level++) {
            if (/^(BODY|HTML|MAIN)$/.test(parent.nodeName)) return false;
            if (/^(NAV|MENU)$/.test(parent.nodeName)) return true;
            var hint = (parent.getAttribute('class') || '') + ' ' + (parent.getAttribute('id') || '') + ' ' + (parent.getAttribute('role') || '');
            if (CHROME.test(hint)) return true;
        }
        return false;
    }
    var RANK = /^\s*(?:#|no\.?\s*)?"?\d+(?:\.\d+)?"?\s*(?:up\s*\d+|new|hot|down\s*\d+)?\s+/i;
    var NOT_TITLES = /^\s*\[?(cover|image|thumbnail|read( now| more)?|new|hot|more|details?|lihat detail|start reading)\]?\s*$/i;
    function bestTitle(item, anchors, base) {
        var candidates = [];
        var box = anchors.length ? cardBox(anchors[0], item.url, base) : null;
        if (box) {
            var heading = box.querySelector('h1, h2, h3, h4, h5') || box.querySelector('[class*=title], [class*=name]');
            if (heading) candidates.push(G().textOf(heading));
        }
        anchors.forEach(function (anchor) {
            candidates.push(anchor.getAttribute('title') || '');
            var image = anchor.querySelector('img');
            if (image) candidates.push(image.getAttribute('alt') || image.getAttribute('title') || '');
        });
        candidates.push(item.title);
        for (var i = 0; i < candidates.length; i++) {
            var text = String(candidates[i] || '').replace(/\s+/g, ' ').replace(RANK, '').trim().replace(/\s+(cover|thumbnail|poster)$/i, '');
            if (text.length > 2 && text.length <= 200 && !NOT_TITLES.test(text)) return text;
        }
        return item.url.replace(/\/+$/, '').split('/').pop().replace(/-/g, ' ');
    }

    /** Book cards on a search, tag or list page: [{url, title, cover, snippet}]. */
    function extractCards(html, base) {
        var doc = G().parse(html);
        Array.prototype.forEach.call(doc.querySelectorAll('script, style, noscript, header, footer, nav'), function (n) { n.remove(); });
        var groups = new Map();
        Array.prototype.forEach.call(doc.querySelectorAll('a[href]'), function (anchor) {
            var url = G().cleanUrl(anchor.getAttribute('href'), base);
            if (!url || !sameSite(url, base) || url.replace(/\/+$/, '') === base.replace(/\/+$/, '')) return;
            if (inChrome(anchor)) return;
            var image = anchor.querySelector('img');
            var title = image ? (G().textOf(anchor) || anchor.getAttribute('title') || image.getAttribute('alt') || '')
                : (G().textOf(anchor) || anchor.getAttribute('title') || '');
            if (!looksLikeBook(url, title)) return;
            var shape = cardShape(url);
            if (!groups.has(shape)) groups.set(shape, []);
            groups.get(shape).push([url, title, anchor]);
        });
        var best = null, bestScore = 0;
        groups.forEach(function (members) {
            var urls = new Set(members.map(function (m) { return m[0]; }));
            var withImages = members.filter(function (m) { return m[2].querySelector('img'); }).length / members.length;
            var titled = members.filter(function (m) { return m[1].length > 3; }).length / members.length;
            if (urls.size < 2 && !withImages) return;
            var average = members.reduce(function (sum, m) { return sum + m[1].length; }, 0) / members.length;
            if (withImages < 0.2 && average < 6) return;
            var score = Math.pow(urls.size, 0.7) * (1 + 2 * withImages + titled);
            if (score > bestScore) { best = members; bestScore = score; }
        });
        if (!best) return [];
        var cards = new Map();
        best.forEach(function (m) {
            if (!cards.has(m[0])) cards.set(m[0], {url: m[0], title: '', cover: '', snippet: ''});
            var item = cards.get(m[0]);
            if (m[1].length > item.title.length && m[1].length < 200) item.title = m[1];
            if (!item.cover) item.cover = imageOf(m[2], base);
        });
        var out = [];
        cards.forEach(function (item) {
            var anchors = best.filter(function (m) { return m[0] === item.url; }).map(function (m) { return m[2]; });
            if (!item.cover || !item.snippet) {
                var box = cardBox(anchors[0], item.url, base);
                if (!item.cover) item.cover = imageOf(box, base);
                var snippet = G().textOf(box);
                if (item.title && snippet.indexOf(item.title) === 0) snippet = snippet.slice(item.title.length).trim();
                item.snippet = snippet.slice(0, 300);
            }
            item.title = bestTitle(item, anchors, base);
            if (item.title) out.push(item);
        });
        return out;
    }

    // -- the web search ----------------------------------------------------------------------------

    function webSearch(text, token) {
        var ddg = fetchUrl('https://html.duckduckgo.com/html/', {method: 'POST', stop: token, data: 'q=' + encodeURIComponent(text),
                                                               headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
            .then(function (result) {
                if (!result || result.status !== 200) return [];
                var html = result.text(), out = [], match, pattern = /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
                while ((match = pattern.exec(html))) {
                    var href = unescapeHtml(match[1]);
                    var target = /[?&]uddg=([^&]+)/.exec(href);
                    if (target) href = decodeURIComponent(target[1]);
                    if (href.indexOf('//') === 0) href = 'https:' + href;
                    var tail = html.slice(pattern.lastIndex, pattern.lastIndex + 2000);
                    var found = /class="result__snippet"[^>]*>([\s\S]*?)<\/a>/.exec(tail);
                    out.push({url: href, title: unescapeHtml(match[2].replace(/<[^>]+>/g, '')),
                              snippet: found ? unescapeHtml(found[1].replace(/<[^>]+>/g, '')) : ''});
                }
                return out;
            });
        var bing = fetchUrl('https://www.bing.com/search?format=rss&count=50&q=' + encodeURIComponent(text), {stop: token})
            .then(function (result) {
                if (!result || result.status !== 200) return [];
                var out = [], item, pattern = /<item>([\s\S]*?)<\/item>/g, xml = result.text();
                while ((item = pattern.exec(xml))) {
                    var link = /<link>([\s\S]*?)<\/link>/.exec(item[1]), title = /<title>([\s\S]*?)<\/title>/.exec(item[1]);
                    var description = /<description>([\s\S]*?)<\/description>/.exec(item[1]);
                    if (link) out.push({url: unescapeHtml(link[1].trim()), title: title ? unescapeHtml(title[1].trim()) : '',
                                        snippet: description ? unescapeHtml(description[1].trim()) : ''});
                }
                return out;
            });
        return Promise.all([ddg.catch(function () { return []; }), bing.catch(function () { return []; })])
            .then(function (both) { return both[0].concat(both[1]); });
    }

    // -- searches ----------------------------------------------------------------------------------

    var current = {token: null};
    function newToken() {
        if (current.token) current.token.stopped = true;
        current.token = {stopped: false};
        return current.token;
    }

    /** Every site for `text`, for up to 45 seconds; cards reach the page as they come. */
    // How quickly each site's search answered, remembered across searches: {host: {ms, ok, at}}.
    var SPEED_KEY = 'noveldb.generic.siteSpeed', SITE_SECONDS = 12, REST_HOURS = 24;
    var speeds = store(SPEED_KEY) || {}, speedTimer = 0;
    // Failures count only once some site answered in the search: offline, every one fails and none should rest.
    var speedAnswered = false, speedFailed = [];
    function noteSpeed(host, ms, ok) {
        ok = ok && ms < SITE_SECONDS * 1000;
        if (ok) { speedAnswered = true; speedFailed.splice(0).forEach(function (f) { speeds[f[0]] = {ms: f[1], ok: false, at: Date.now()}; }); }
        if (!ok && !speedAnswered) { speedFailed.push([host, ms]); return; }
        speeds[host] = {ms: ms, ok: ok, at: Date.now()};
        clearTimeout(speedTimer);
        speedTimer = setTimeout(function () { store(SPEED_KEY, speeds); }, 1000);
    }
    /** A site that failed or was slow in the last day is not asked (the web search still finds its books). */
    function resting(host) {
        var known = speeds[host];
        return !!known && !known.ok && Date.now() - known.at < REST_HOURS * 3600e3;
    }
    /** The order sites are asked in: the quickest known first, then the ones not tried yet. */
    function speedRank(host) {
        var known = speeds[host];
        return known && known.ok ? known.ms : SITE_SECONDS * 1000;
    }

    // -- NovelDB's own sites, by tag and author (noveldb/core/catalog/source_search.py) ------------------
    // Each site is asked directly (its own search API or page, the way the metadata scrapers read it) for
    // a tag, under the site's own name for it (NovelDB's tag list: "TS" on npia for "Gender Bender"), or
    // for an author. Requests carry the web view's cookies, so a signed-in user finds what members see.
    // A searcher: (mode, query, page, token) -> Promise of {cards, more}. Keep in step with the Python.

    var SRC_NATIVE_PER_SOURCE = 2;
    var SRC_STATUS_TAGS = {completed: 1, ongoing: 1, hiatus: 1};
    // Their requests' own slots: they never wait behind the hundred other sites a search asks at once.
    var SRC_POOL = {free: 8, waiting: []};
    var srcNext = {};
    var SRC_BROWSER = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';  // "source\tmode\ttext\tname" -> the page of that list to read next

    function srcJson(result) {
        if (!result || result.status >= 400) return null;
        try { return JSON.parse(result.text()); } catch (e) { return null; }
    }
    function srcDoc(result) {
        if (!result || result.status >= 400) return null;
        return new DOMParser().parseFromString(result.text(), 'text/html');
    }
    function srcText(node) { return node ? String(node.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
    function srcCount(value) {
        var number = Math.floor(Number(String(value == null ? '' : value).replace(/,/g, '')));
        return number > 0 ? number.toLocaleString('en-US') : '';
    }
    function srcDate(value) {
        var found = String(value || '').trim().match(/^(\d{4})[-./]?(\d{2})[-./]?(\d{2})/);
        return found ? found[1] + '-' + found[2] + '-' + found[3] : '';
    }
    function srcQuery(params) {
        return Object.keys(params).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    }
    /** A book card as the page takes it; `figures`: views, likes, chapters, status, updated, published. */
    function srcCard(source, url, title, author, cover, tags, description, figures) {
        figures = figures || {};
        var out = {url: url, title: String(title || '').trim() || url, author: String(author || '').trim(), cover: cover || '',
                   description: String(description || '').replace(/\s+/g, ' ').trim().slice(0, 600),
                   tags: (tags || []).map(function (t) { return String(t).trim(); }).filter(Boolean),
                   site: G().siteOf(host(url)), comic: false, source: 'site', complete: false, verified: true, sourceName: source};
        ['views', 'likes', 'chapters'].forEach(function (name) { var v = srcCount(figures[name]); if (v) out[name] = v; });
        ['updated', 'published'].forEach(function (name) { var v = srcDate(figures[name]); if (v) out[name] = v; });
        if (figures.status) out.status = figures.status;
        if (figures.rating) out.rating = figures.rating;
        return out;
    }
    function srcNames(list) { return String(list || '').split(',').map(function (n) { return n.trim(); }); }

    // kpage: its search API; a tag is a genre (the only tag its books have).
    var KPAGE = 'https://page.\u006b\u0061\u006b\u0061\u006f.com', KPAGE_API = 'https://bff-page.\u006b\u0061\u006b\u0061\u006f.com/api/gateway';
    var KPAGE_GENRES = {'판타지': 86, '현판': 120, '로맨스': 89, '로판': 117, '무협': 87, 'bl': 123};
    function kpageCard(row) {
        var done = ['ST66', 'END', 'COMPLETE'].indexOf(String(row.state || '')) >= 0 || ['E', 'End', 'Complete'].indexOf(String(row.on_issue || '')) >= 0
            || String(row.title || '').indexOf('[완결]') >= 0;
        return srcCard('kpage', KPAGE + '/content/' + row.series_id, row.title, row.authors,
                       row.thumbnail ? 'https://dn-img-page.\u006b\u0061\u006b\u0061\u006f.com/download/resource?kid=' + row.thumbnail + '&filename=th3' : '',
                       row.sub_category ? [row.sub_category] : [], '',
                       {views: (row.service_property || {}).view_count, status: done ? 'Completed' : 'Ongoing',
                        updated: row.last_slide_added_dt, published: row.start_sale_dt});
    }
    function kpageSearch(mode, query, page, token) {
        var url;
        if (mode === 'tag') {
            var genre = KPAGE_GENRES[query.toLowerCase()];
            if (genre == null) return Promise.resolve({cards: [], more: false});  // its search matches titles only
            url = KPAGE_API + '/view/v1/landing/genre?' + srcQuery({category_uid: 11, screen_uid: 84, subcategory_uid: genre, sort_type: 'UPDATE', page: page});
        } else {
            url = KPAGE_API + '/api/v1/search/series?' + srcQuery({keyword: query, category_uid: 11, is_complete: 'false', sort_type: 'ACCURACY', page: page, size: 40});
        }
        return fetchUrl(url, {pool: SRC_POOL, stop: token, headers: {Accept: 'application/json', Referer: KPAGE + '/', Origin: KPAGE}}).then(function (result) {
            var data = (srcJson(result) || {}).result || {};
            var rows = (data.list || []).filter(function (row) { return row && row.series_id; });
            if (mode === 'author') rows = rows.filter(function (row) { return srcNames(row.authors).indexOf(query) >= 0; });
            return {cards: rows.map(kpageCard), more: !!(data.list || []).length && data.is_end === false};
        });
    }

    // nweb: its search page, by exact tag or author, in each of its three leagues.
    var NWEB = 'https://novel.\u006e\u0061\u0076\u0065\u0072.com';
    function nwebPage(result) {
        var doc = srcDoc(result);
        if (!doc) return {cards: [], more: false};
        var cards = [];
        doc.querySelectorAll('ul.default_list > li.item').forEach(function (item) {
            var link = item.querySelector('a.link[href]') || item.querySelector('a[href*="novelId="]');
            var href = link && link.getAttribute('href');
            if (!href || href.indexOf('novelId=') < 0) return;
            var tags = (srcText(item.querySelector('span.tag')).match(/#[^\s#]+/g) || []).map(function (t) { return t.slice(1); });
            var image = item.querySelector('.thumbnail img');
            var authors = Array.prototype.map.call(item.querySelectorAll('.info_group .author'), srcText).join(', ');
            cards.push(srcCard('nweb', href.charAt(0) === '/' ? NWEB + href : href, srcText(item.querySelector('.title')), authors,
                               image ? image.getAttribute('src') || '' : '', tags, '',
                               {status: item.querySelector('.bullet_comp') ? 'Completed' : ''}));
        });
        var next = Array.prototype.some.call(doc.querySelectorAll('.default_paging a'), function (a) { return srcText(a).indexOf('다음') >= 0; });
        return {cards: cards, more: next || cards.length >= 15};
    }
    function nwebSearch(mode, query, page, token) {
        query = query.replace(/^#/, '');
        return Promise.all(['webnovel', 'best', 'challenge'].map(function (section) {
            return fetchUrl(NWEB + '/search?' + srcQuery({keyword: query, target: mode === 'tag' ? 'tag' : 'author', section: section, page: page + 1}),
                            {pool: SRC_POOL, stop: token}).then(nwebPage);
        })).then(function (found) {
            return {cards: [].concat.apply([], found.map(function (f) { return f.cards; })),
                    more: found.some(function (f) { return f.more; })};
        });
    }

    // nseries: a tag is a genre (its books have no others); authors through its search.
    var NSERIES = 'https://series.\u006e\u0061\u0076\u0065\u0072.com';
    var NSERIES_GENRES = {'로맨스': 201, '로판': 207, '판타지': 202, '현판': 208, '무협': 206, '미스터리': 203, '라이트노벨': 205, 'bl': 209};
    function nseriesPage(result) {
        var doc = srcDoc(result);
        if (!doc) return {cards: [], more: false};
        var cards = [];
        doc.querySelectorAll('ul.lst_list > li').forEach(function (item) {
            var link = item.querySelector('h3 a[href*="productNo="]');
            if (!link) return;
            var full = srcText(link), href = link.getAttribute('href');
            var image = item.querySelector('a.pic img');
            var date = srcText(item.querySelector('p.info')).match(/\d{4}\.\d{2}\.\d{2}/);
            cards.push(srcCard('nseries', href.charAt(0) === '/' ? NSERIES + href : href,
                               full.replace(/\s*\((총\s*)?\d+\s*(화|권)\s*\/\s*(미?완결)\)\s*$/, ''), srcText(item.querySelector('.author')),
                               image ? image.getAttribute('src') || '' : '', [], srcText(item.querySelector('.dsc')),
                               {status: /\/\s*완결\)/.test(full) ? 'Completed' : /미완결\)/.test(full) ? 'Ongoing' : '',
                                updated: date ? date[0] : '', rating: srcText(item.querySelector('.score_num'))}));
        });
        var pages = doc.querySelector('.pagenate');
        return {cards: cards, more: !!(pages && pages.querySelector('a.next')) || cards.length >= 25};
    }
    function nseriesSearch(mode, query, page, token) {
        if (mode === 'tag') {
            var genre = NSERIES_GENRES[query.toLowerCase()];
            if (genre == null) return Promise.resolve({cards: [], more: false});  // its search matches titles only
            return fetchUrl(NSERIES + '/novel/categoryProductList.series?' + srcQuery({categoryTypeCode: 'genre', genreCode: genre, orderTypeCode: 'new', page: page + 1}),
                            {pool: SRC_POOL, stop: token}).then(nseriesPage);
        }
        return fetchUrl(NSERIES + '/search/search.series?' + srcQuery({t: 'novel', fs: 'default', q: query, page: page + 1}), {pool: SRC_POOL, stop: token}).then(function (result) {
            var found = nseriesPage(result);
            // It matches titles too: only the author's own books.
            return {cards: found.cards.filter(function (c) { return srcNames(c.author).indexOf(query) >= 0; }), more: found.more};
        });
    }

    // npia: its search API (the one its search page and the metadata scraper use).
    var NPIA = 'https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com', NPIA_IMAGES = 'https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com', NPIA_ROWS = 40;
    function npiaCover(row) {
        var names = ['novel_img_all', 'novel_thumb_all', 'cover_url', 'novel_img', 'novel_thumb'];
        for (var i = 0; i < names.length; i++) {
            var value = String(row[names[i]] || '').trim();
            if (value && value.indexOf('readycover') < 0 && value.indexOf('adult_cover_img') < 0) {
                return value.indexOf('//') === 0 ? 'https:' + value : value.charAt(0) === '/' ? NPIA_IMAGES + value : value;
            }
        }
        return '';
    }
    function npiaSearch(mode, query, page, token) {
        var params = {cmd: 'novel_search', search_type: mode === 'tag' ? 'novel_genre' : 'writer_nick', search_val: query.replace(/^#/, ''),
                      page: page + 1, rows: NPIA_ROWS, sort_col: 'count_view', novel_type: '', start_count_book: '', end_count_book: '',
                      novel_age: '', start_days: '', novel_genre: '', block_out: 0, block_stop: 0, is_contest: 0, is_complete: '',
                      is_challenge: 0, list_display: 'grid'};
        var ask = function () {
            // Without a browser's User-Agent it answers an empty page.
            return fetchUrl(NPIA + '/proc/novel?' + srcQuery(params), {pool: SRC_POOL, stop: token, referer: NPIA + '/search',
                                                                       headers: {'X-Requested-With': 'XMLHttpRequest', 'User-Agent': SRC_BROWSER}}).then(srcJson);
        };
        return ask().then(function (data) { return data || ask(); }).then(function (data) {  // it answers 502 now and then
            var rows = ((data || {}).list || []).filter(function (row) { return row && row.novel_no; });
            var want = query.replace(/^#/, '').toLowerCase();
            var cards = [];
            rows.forEach(function (row) {
                var tags = (row.novel_genre_arr || []).map(String).filter(function (t) { return t.trim(); });
                // Its matches are any part of a tag or a nickname ("TS" finds "TS있음"): only the exact one.
                if (mode === 'tag' && tags.map(function (t) { return t.toLowerCase(); }).indexOf(want) < 0) return;
                if (mode === 'author' && String(row.writer_nick || '').toLowerCase() !== want) return;
                cards.push(srcCard('npia', NPIA + '/novel/' + row.novel_no, row.novel_name, row.writer_nick, npiaCover(row), tags, row.novel_story,
                                   {views: row.count_view, likes: row.count_good, chapters: row.count_book,
                                    status: ['1', 'True', 'true'].indexOf(String(row.is_complete)) >= 0 ? 'Completed' : 'Ongoing',
                                    updated: row.last_viewdate, published: row.start_date}));
            });
            return {cards: cards, more: rows.length >= NPIA_ROWS && (page + 1) * NPIA_ROWS < Number((data || {}).total_cnt || 0)};
        });
    }

    // mpia: its search API, by whole tag or author.
    var MPIA = 'https://www.\u006d\u0075\u006e\u0070\u0069\u0061.com';
    function mpiaSearch(mode, query, page, token) {
        return fetchUrl(MPIA + '/api/v1/main/search?' + srcQuery({query: query.replace(/^#/, ''), tab: mode === 'tag' ? 'TAG' : 'AUTHOR', page: page, size: 40, adultMode: 'true'}),
                        {pool: SRC_POOL, stop: token, headers: {Accept: 'application/json'}}).then(function (result) {
            var data = srcJson(result);
            if (!data || data.code !== 'M000_00000') return {cards: [], more: false};
            var found = data.result || {};
            var rows = (found.searchNovelTabDtos || []).filter(function (row) { return row && row.novelId; });
            if (mode === 'author') rows = rows.filter(function (row) { return String(row.author || '').trim() === query; });
            return {cards: rows.map(function (row) {
                var tags = [row.mainGenre, row.subGenre].concat(row.tag || []).filter(function (t, i, all) { return typeof t === 'string' && t && all.indexOf(t) === i; });
                return srcCard('mpia', MPIA + '/novel/detail/' + row.novelId, row.title, row.author, row.coverUrl, tags, row.story,
                               {views: row.viewCount, chapters: row.entryCount, status: row.finished ? 'Completed' : 'Ongoing', updated: row.updateAt});
            }), more: !!found.hasNext};
        });
    }

    // jara: its search API (the site's own; its public key is read from the site's script).
    var JARA = 'https://www.\u006a\u006f\u0061\u0072\u0061.com', JARA_API = 'https://api.\u006a\u006f\u0061\u0072\u0061.com', jaraParams = null;
    function jaraPublic(token) {
        if (jaraParams) return jaraParams;
        jaraParams = fetchUrl(JARA + '/', {pool: SRC_POOL, stop: token}).then(function (home) {
            var found = (home ? home.text() : '').match(/<script[^>]+src="([^"]*\/static\/js\/main\.[a-zA-Z0-9]+(?:\.chunk)?\.js)"/g) || [];
            var last = found.length ? found[found.length - 1].match(/src="([^"]+)"/)[1] : '';
            if (!last) return null;
            return fetchUrl(last.indexOf('http') === 0 ? last : JARA + last, {pool: SRC_POOL, stop: token}).then(function (script) {
                var bundle = (script ? script.text() : '').replace(/\\u([0-9a-fA-F]{4})/g, function (_m, hex) { return String.fromCharCode(parseInt(hex, 16)); });
                // The production config: the API's address, then its public key (never a user's token).
                var at = [JARA_API + '"', JARA_API + "'"].map(function (s) { return bundle.indexOf(s); }).filter(function (i) { return i >= 0; })[0];
                if (at == null || !/^.{1,3}\s*,\s*apiKey\s*:/.test(bundle.slice(at + JARA_API.length, at + JARA_API.length + 40))) return null;
                var config = bundle.slice(at, at + 600), fields = {
                    apiKey: config.match(/\bapiKey\s*:\s*["']([^"']*)["']/), device: config.match(/\bdevice\s*:\s*["']([^"']*)["']/),
                    devicetoken: config.match(/\bdevicetoken\s*:\s*["']([^"']*)["']/), version: config.match(/\bversion\s*:\s*["']([^"']*)["']/)};
                Object.keys(fields).forEach(function (key) { fields[key] = fields[key] ? fields[key][1] : ''; });
                if (!fields.apiKey || !fields.device || !fields.devicetoken || !fields.version) return null;
                var uid = Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(16)), function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
                return {api_key: fields.apiKey, ver: fields.version, device: fields.device, devicetoken: fields.devicetoken, deviceuid: uid};
            });
        }).then(function (params) { if (!params) jaraParams = null; return params; }, function () { jaraParams = null; return null; });
        return jaraParams;
    }
    function jaraSearch(mode, query, page, token) {
        return Promise.resolve(jaraPublic(token)).then(function (params) {
            if (!params) return {cards: [], more: false};
            var ask = Object.assign({}, params, {query: query.replace(/^#/, ''), target: mode === 'tag' ? 'keyword' : 'member_name',
                                                 page: page + 1, offset: 100, enable_fallback_match: 'false'});
            return fetchUrl(JARA_API + '/v2/search/query?' + srcQuery(ask), {pool: SRC_POOL, stop: token}).then(function (result) {
                var data = srcJson(result);
                if (!data || String(data.status) !== '1') return {cards: [], more: false};
                var rows = ((data.data || {}).list || []).filter(function (row) { return row && row.book_code; });
                var want = query.replace(/^#/, '').toLowerCase(), cards = [];
                rows.forEach(function (row) {
                    var tags = (row.keyword || []).map(String).filter(function (t) { return t.trim(); });
                    // Its matches are words of a tag or a name: only books with the tag itself, or by the author.
                    if (mode === 'tag' && tags.map(function (t) { return t.toLowerCase(); }).indexOf(want) < 0) return;
                    if (mode === 'author' && String(row.member_name || '').trim().toLowerCase() !== want) return;
                    var cover = String(row.cover || '');
                    cards.push(srcCard('jara', JARA + '/book/' + row.book_code, row.subject, row.member_name, cover.indexOf('//') === 0 ? 'https:' + cover : cover,
                                       (row.category_name ? [row.category_name] : []).concat(tags), row.intro,
                                       {views: row.page_read, likes: row.recommend_count, chapters: row.total_chapter_count,
                                        status: ['Y', '1', 'True', 'true'].indexOf(String(row.chkfinish)) >= 0 ? 'Completed' : 'Ongoing',
                                        updated: row.last_regist_datetime, published: row.first_regist_datetime}));
                });
                return {cards: cards, more: rows.length >= 100 && (page + 1) * 100 < Number(data.total_cnt || 0)};
            });
        });
    }

    // sfc: its app API (system tags, author search); other tags from its old tag pages.
    var SFC_API = 'https://api.\u0073\u0066\u0061\u0063\u0067.com', SFC_BOOK = 'https://book.\u0073\u0066\u0061\u0063\u0067.com';
    var SFC_HEADERS = {'User-Agent': 'boluobao/5.0.36(android;34)/H5/{}/H5', Accept: 'application/json',
                       Authorization: 'Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE='};  // the app's public login
    var SFC_EXPAND = 'typeName,tags,sysTags,latestChapter,intro';
    var SFC_TYPES = {'魔幻': 21, '玄幻': 22, '古风': 23, '科幻': 24, '校园': 25, '都市': 26, '游戏': 27, '同人': 28, '悬疑': 29};
    var sfcTags = null;
    function sfcData(url, token) {
        return fetchUrl(url, {pool: SRC_POOL, stop: token, headers: SFC_HEADERS}).then(function (result) { var data = srcJson(result); return data ? data.data : null; });
    }
    function sfcCard(row) {
        var expand = row.expand || {};
        var tags = (expand.typeName ? [expand.typeName] : []).concat((expand.sysTags || []).map(function (t) { return t && t.tagName; }), expand.tags || [])
            .filter(function (t, i, all) { return typeof t === 'string' && t && all.indexOf(t) === i; });
        return srcCard('sfc', SFC_BOOK + '/Novel/' + row.novelId + '/', row.novelName, row.authorName, row.novelCover, tags, expand.intro,
                       {views: row.viewTimes, likes: row.markCount, status: row.isFinish ? 'Completed' : 'Ongoing',
                        updated: row.lastUpdateTime, published: row.addTime});
    }
    function sfcTagPage(tag, page, token) {
        return fetchUrl(SFC_BOOK + '/tag/' + encodeURIComponent(tag) + '/' + (page + 1), {pool: SRC_POOL, stop: token}).then(function (result) {
            var text = result ? result.text() : '', cards = [], pages = [];
            (text.match(/<ul class="TAG_list[^"]*">[\s\S]*?<\/ul>/g) || []).forEach(function (block) {
                var id = block.match(/href="\/Novel\/(\d+)\/"/);
                if (!id) return;
                var image = (block.match(/<img[^>]*>/) || [''])[0];
                var cover = image.match(/src="([^"]+)"/), title = image.match(/alt="([^"]*)"/), date = block.match(/(\d{4}-\d{2}-\d{2})/);
                var holder = document.createElement('textarea');
                holder.innerHTML = title ? title[1] : '';
                cards.push(srcCard('sfc', SFC_BOOK + '/Novel/' + id[1] + '/', holder.value, '', cover ? cover[1] : '', [tag], '',
                                   {status: block.indexOf('已完结') >= 0 ? 'Completed' : block.indexOf('连载中') >= 0 ? 'Ongoing' : '',
                                    updated: date ? date[1] : ''}));
            });
            (text.match(/\/tag\/[^\/"']+\/(\d+)/g) || []).forEach(function (m) { pages.push(Number(m.split('/').pop())); });
            return {cards: cards, more: pages.length > 0 && Math.max.apply(null, pages) > page + 1};
        });
    }
    function sfcSearch(mode, query, page, token) {
        if (mode === 'author') {
            return sfcData(SFC_API + '/search/novels/result?' + srcQuery({q: query, page: page, size: 20, expand: SFC_EXPAND}), token).then(function (data) {
                var rows = ((data || {}).novels || []).filter(function (row) { return row && row.novelId; });
                // It matches titles too: only the author's own books.
                return {cards: rows.filter(function (row) { return String(row.authorName || '').trim() === query; }).map(sfcCard), more: rows.length >= 20};
            });
        }
        query = query.replace(/^#/, '');
        var kind = SFC_TYPES[query];
        var tags = kind ? Promise.resolve(null) : sfcTags ? Promise.resolve(sfcTags) : sfcData(SFC_API + '/novels/0/sysTags', token).then(function (data) {
            var names = {};
            (data || []).forEach(function (entry) { if (entry && entry.tagName && entry.sysTagId) names[entry.tagName] = entry.sysTagId; });
            if (Object.keys(names).length) sfcTags = names;
            return names;
        });
        return tags.then(function (names) {
            var tag = names ? names[query] : null;
            if (!kind && !tag) return sfcTagPage(query, page, token);
            return sfcData(SFC_API + '/novels/' + (kind || 0) + '/sysTags/novels?' + srcQuery({systagids: kind ? '' : tag, page: page, size: 50, sort: 'viewtimes', expand: SFC_EXPAND}), token)
                .then(function (data) {
                    var rows = (data || []).filter(function (row) { return row && row.novelId; });
                    return {cards: rows.map(sfcCard), more: rows.length >= 50};
                });
        });
    }

    // rbooks: its keyword finder (tags) and search (authors), read from the pages' own data. The site
    // refuses plain requests (a bot check): fetchUrl reads such a page in a hidden page instead.
    var RBOOKS = 'https://\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com', RBOOKS_SETS = [['romance', 1], ['fantasy', 18], ['bl', 15]], rbooksKeywords = null;
    function rbooksProps(url, token) {
        return fetchUrl(url, {pool: SRC_POOL, stop: token}).then(function (result) {
            var found = (result ? result.text() : '').match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
            try { return found ? (JSON.parse(found[1]).props || {}).pageProps || {} : {}; } catch (e) { return {}; }
        });
    }
    function rbooksQuery(props, name) {
        var entry = ((props.dehydratedState || {}).queries || []).filter(function (q) { return (q.queryKey || [])[0] === name; })[0];
        return entry ? (entry.state || {}).data || {} : {};
    }
    function rbooksCard(book, tags) {
        var series = book.series || {}, own = book.title;
        var authors = (book.authors || []).filter(function (a) { return a && a.name && ['AUTHOR', 'STORY_WRITER'].indexOf(String(a.role || '').toUpperCase()) >= 0; })
            .map(function (a) { return a.name; });
        return srcCard('rbooks', RBOOKS + '/books/' + (series.id || book.id), series.title || (own && typeof own === 'object' ? own.main : own) || '',
                       authors.join(', '), (series.thumbnail || {}).large || (book.thumbnail || {}).large || 'https://img.\u0072\u0069\u0064\u0069\u0063\u0064\u006e.net/cover/' + book.id + '/large',
                       tags || [], (book.introduction || {}).description || '',
                       {chapters: series.totalEpisodeCount, status: series.isCompleted ? 'Completed' : book.series ? 'Ongoing' : ''});
    }
    function rbooksSearch(mode, query, page, token) {
        if (mode === 'author') {
            return rbooksProps(RBOOKS + '/search?' + srcQuery({q: query, page: page + 1}), token).then(function (props) {
                var cells = (((props.gridData || {}).riGrid || {}).grid || {}).cells || [];
                var found = (cells.filter(function (c) { return c && c.cell__SearchBookListWithTab; })[0] || {}).cell__SearchBookListWithTab || {};
                var books = (found.books || []).map(function (item) { return item.book || item; });
                var mine = books.filter(function (b) { return (b.authors || []).some(function (a) { return a && a.name === query; }); });
                return {cards: mine.map(function (b) { return rbooksCard(b); }), more: books.length >= 24};
            });
        }
        var keywords = rbooksKeywords ? Promise.resolve(rbooksKeywords) : Promise.all(RBOOKS_SETS.map(function (set) {
            return rbooksProps(RBOOKS + '/keyword-finder/' + set[0] + '?set_id=' + set[1], token).then(function (props) {
                var names = {};
                (((rbooksQuery(props, 'KeywordFinder').keywordFinder || {}).sets) || []).forEach(function (s) {
                    (s.groups || []).forEach(function (g) {
                        (g.keywords || []).forEach(function (k) {
                            var name = k && k.tag && k.tag.name;
                            if (name && k.tagId) names[name.replace(/\s+/g, '')] = [set[0], set[1], k.tagId];
                        });
                    });
                });
                return names;
            });
        })).then(function (lists) {
            var all = {};
            lists.forEach(function (names) { Object.keys(names).forEach(function (n) { if (!all[n]) all[n] = names[n]; }); });
            if (Object.keys(all).length) rbooksKeywords = all;
            return all;
        });
        return keywords.then(function (names) {
            var found = names[query.replace(/^#/, '').replace(/\s+/g, '')];
            if (!found) return {cards: [], more: false};
            return rbooksProps(RBOOKS + '/keyword-finder/' + found[0] + '?' + srcQuery({set_id: found[1], tag_ids: found[2], page: page + 1}), token).then(function (props) {
                var listed = rbooksQuery(props, 'KeywordFinderBooks').keywordFinderBooks || {};
                var items = (listed.items || []).filter(function (item) { return item && (item.bookShell || {}).book; });
                return {cards: items.map(function (item) {
                    return rbooksCard(item.bookShell.book, (item.tags || []).map(function (t) { return t && t.name; }).filter(Boolean));
                }), more: items.length >= 20 && (page + 1) * 20 < Number(listed.totalItemCount || 0)};
            });
        });
    }

    var SOURCE_SEARCHERS = {npia: npiaSearch, kpage: kpageSearch, sfc: sfcSearch, nweb: nwebSearch, mpia: mpiaSearch, jara: jaraSearch,
                            rbooks: rbooksSearch, nseries: nseriesSearch};
    function sourceSearch(source, mode, query, page, token) {
        var run = SOURCE_SEARCHERS[source];
        if (!run || (mode !== 'tag' && mode !== 'author') || !String(query || '').trim()) return Promise.resolve({cards: [], more: false});
        return Promise.resolve().then(function () { return run(mode, String(query).trim(), page, token); })
            .catch(function () { return {cards: [], more: false}; });
    }
    /** What each source calls a tag ({source: [names]}), from NovelDB's tag list; {} when it doesn't list it. */
    function ndbNativeTags(tag) {
        return ndbManifest().then(function (manifest) {
            return manifest && manifest.tagsAt ? ndbPiece(manifest, 'tags', 0, false, manifest.tagsAt) : null;
        }).then(function (listed) {
            listed = (listed || []).filter(function (e) { return Array.isArray(e) && e.length > 2 && e[2] && typeof e[2] === 'object'; });
            var want = ndbNormal(tag).trim();
            var found = listed.filter(function (e) { return ndbNormal(e[0]).trim() === want; })[0];
            if (!found && ndbConcept(tag)) found = listed.filter(function (e) { return ndbConcept(e[0]) === ndbConcept(tag); })[0];
            return found ? found[2] : {};
        }, function () { return {}; });
    }
    /** {source: [the names to ask it for]}: each site's own names for the tag, or the tag as typed for every site. */
    function sourceTagQueries(tag, natives) {
        if (SRC_STATUS_TAGS[String(tag).trim().toLowerCase()]) return {};
        var out = {}, listed = natives && Object.keys(natives).length;
        Object.keys(SOURCE_SEARCHERS).forEach(function (source) {
            var names = listed ? natives[source] || [] : [tag], seen = {}, picked = [];
            names.forEach(function (name) { var k = String(name).toLowerCase(); if (!seen[k]) { seen[k] = 1; picked.push(String(name)); } });
            if (picked.length) out[source] = picked.slice(0, SRC_NATIVE_PER_SOURCE);
        });
        return out;
    }

    function runSearch(id, text, mode, token) {
        speedAnswered = false;
        speedFailed = [];
        var seen = {}, summary = {web: 0, details: 0, failed: 0, site: 0}, details = [];
        function offer(cards) {
            var fresh = cards.filter(function (item) {
                var k = item.url.replace(/\/+$/, '');
                if (seen[k] || stopped(token) || isNoResults(item.title, String(item.description || '').slice(0, 400))) return false;
                seen[k] = item;
                return true;
            });
            if (fresh.length) call('add', fresh.map(function (c) { return Object.assign({}, c, {via: mode}); }), id);
            return fresh;
        }
        function enrich(cards) {
            cards.forEach(function (item) {
                details.push(details_(item));
            });
        }
        function details_(item) {
            if (stopped(token)) return Promise.resolve();
            return details(item).catch(function () { return Object.assign({}, item, {complete: true}); }).then(function (updated) {
                summary.details++;
                if (!stopped(token)) call('add', [Object.assign({}, updated, {via: mode})], id);
            });
        }
        function report(site, started, found, state) {
            call('progress', id, {site: site, ms: Date.now() - started, found: found, state: state || 'ok'});
        }
        // The busiest sites ("searched"); the rest come up through the web search. The quickest sites
        // (remembered from earlier searches) are asked first; one that failed or was slow rests a day.
        var searchedSites = SITES.filter(function (site) {
            return site.searched !== false && !(site.lnreader || []).length && !resting(site.host);
        }).sort(function (a, b) { return speedRank(a.host) - speedRank(b.host); });
        var sources = searchedSites.map(function (site) {
            return function () {
                var started = Date.now(), home = 'https://' + site.host + '/';
                var asked = mode === 'name' ? siteSearch(home, text, token) : siteTag(home, text, token);
                // A site gets SITE_SECONDS: past that its slot goes to the next one (it rests a day).
                var late = new Promise(function (resolve) { setTimeout(function () { resolve('late'); }, SITE_SECONDS * 1000); });
                return Promise.race([asked, late]).then(function (hits) {
                    var ms = Date.now() - started;
                    if (hits === 'late') { noteSpeed(site.host, ms, false); report(site.site, started, 0, 'failed'); return; }
                    var cards = hits.map(function (hit) {
                        return Object.assign(card(hit.url, hit.title, hit.snippet, 'site', hit.cover), {ms: ms, verified: !!hit.verified});
                    });
                    summary.site += cards.length;
                    var fresh = offer(cards);
                    var refusedSite = plans[home] && plans[home].reachable === false;
                    noteSpeed(site.host, ms, !refusedSite);
                    report(site.site, started, fresh.length, refusedSite ? 'refused' : 'ok');
                    enrich(fresh.filter(function (c) { return !c.cover; }));
                }, function () { summary.failed++; noteSpeed(site.host, Date.now() - started, false); report(site.site, started, 0, 'failed'); });
            };
        });
        sources.unshift(function () {
            var started = Date.now();
            var shown = 0;
            var mark = function (c) { return Object.assign(c, {ms: Date.now() - started, verified: mode === 'tag' || mode === 'author'}); };
            // Each card goes to the page as soon as it is read, not after the whole page of them.
            return ndbSearch(text, mode, NDB_FIRST, 0, function (c) { shown += offer([mark(c)]).length; }, function (total) {
                call('progress', id, {noveldbTotal: total});
            }).then(function (found) {
                shown += offer(found.cards.map(mark)).length;
                report('NovelDB', started, shown);
            }, function () { summary.failed++; report('NovelDB', started, 0, 'failed'); });
        });
        sources.push(function () {
            var started = Date.now();
            var words_ = mode === 'tag' ? '"' + text + '" (novel OR manga OR webtoon)' : '"' + text + '" (novel OR manga OR webtoon OR manhwa)';
            return webSearch(words_, token).then(function (hits) {
                var cards = hits.filter(function (hit) {
                    return /^https?:\/\//.test(hit.url) && looksLikeBook(hit.url, hit.title) && (siteFor(hit.url) || knownSite(hit.url));
                }).map(function (hit) { return card(hit.url, hit.title, hit.snippet, 'web'); });
                summary.web += cards.length;
                var fresh = offer(cards);
                fresh.forEach(function (c) { c.ms = Date.now() - started; });
                report('web search', started, fresh.length);
                enrich(fresh);
            });
        });
        // An author's books: NovelDB and its own sites know them by author; the other sites' searches are by title.
        if (mode === 'author') sources = sources.slice(0, 1);
        // NovelDB's own sites, asked directly: a tag under each site's own name for it, or the author.
        var asks = mode === 'author' ? Promise.resolve(Object.keys(SOURCE_SEARCHERS).reduce(function (all, s) { all[s] = [text]; return all; }, {}))
            : mode === 'tag' ? ndbNativeTags(text).then(function (natives) { return sourceTagQueries(text, natives); }) : Promise.resolve({});
        var direct = asks.then(function (found) {
            var jobs = [];
            Object.keys(found).forEach(function (source) {
                found[source].forEach(function (name) {
                    jobs.push(function () {
                        var started = Date.now();
                        return sourceSearch(source, mode, name, 0, token).then(function (result) {
                            var key = [source, mode, text, name].join('\t');
                            if (result.more) srcNext[key] = 1; else delete srcNext[key];
                            var ms = Date.now() - started;
                            summary.site += result.cards.length;
                            report(source, started, offer(result.cards.map(function (c) { return Object.assign(c, {ms: ms}); })).length);
                        });
                    });
                });
            });
            call('progress', id, {total: sources.length + jobs.length});
            return each(jobs, 8, function (job) { return job(); }, token);
        });
        call('progress', id, {total: sources.length});
        var all = Promise.all([direct, each(sources, 16, function (source) { return source(); }, token)]).then(function () {
            return Promise.all(details);
        });
        return within(all, 45000, token).then(function (how) {
            summary.results = Object.keys(seen).length;
            summary.timed_out = how === 'late';
            return summary;
        });
    }

    /** The book with this ID on every site that addresses its books by ID. */
    function runById(id, text, token) {
        var code = text.trim(), numeric = /^\d+$/.test(code), tries = [], seenSites = {}, found = 0;
        ID_TEMPLATES.forEach(function (entry) {
            if ((entry.id === 'number') === numeric) {
                tries.push([entry.site, entry.template]);
                seenSites[G().siteOf(host(entry.template.replace('{id}', '1')))] = true;
            }
        });
        if (numeric) Object.keys(learned).forEach(function (site) { if (!seenSites[site]) tries.push([site, learned[site]]); });
        call('progress', id, {total: tries.length});
        var all = each(tries, 12, function (entry) {
            var started = Date.now(), url = entry[1].replace('{id}', code);
            var item = card(url, '', '', 'id');
            item.site = G().siteOf(host(url));
            return fetchUrl(url, {stop: token}).then(function (result) {
                var final = (result && result.url) || url;
                if (!result || result.status >= 400 || final.toLowerCase().indexOf(code.toLowerCase()) < 0) return null;
                var meta = G().metadata(G().parse(result.text()), final);
                var title = String(meta.title || '').trim();
                if (!title || NOT_FOUND.test(title) || isNoResults(title, String(meta.description || '').slice(0, 300))) return null;
                return sameForAnyId(entry[1], numeric, title).then(function (same) {
                    if (same) return null;
                    var book = Object.assign({}, item, {url: final, complete: true, ms: Date.now() - started});
                    ['title', 'author', 'cover', 'description', 'tags', 'status'].concat(FIGURES).forEach(function (name) {
                        if (meta[name] && (!Array.isArray(meta[name]) || meta[name].length)) book[name] = meta[name];
                    });
                    return book;
                });
            }).then(function (book) {
                call('progress', id, {site: item.site, ms: Date.now() - started, found: book ? 1 : 0, state: book ? 'ok' : 'failed'});
                if (book && !stopped(token)) {
                    found++;
                    call('add', [Object.assign(book, {via: 'id'})], id);
                }
            });
        }, token);
        return within(all, 40000, token).then(function () { return {results: found}; });
    }
    function sameForAnyId(template, numeric, title) {
        var control = template.replace('{id}', numeric ? '987654321' : 'n0000zz');
        return fetchUrl(control, {quick: true}).then(function (result) {
            if (!result || result.status >= 400) return false;
            var other = String(G().metadata(G().parse(result.text()), result.url || control).title || '').trim();
            return !!other && other === title;
        }, function () { return false; });
    }

    var TLD_LANG = {jp: 'ja', kr: 'ko', cn: 'zh', tw: 'zh', hk: 'zh', ru: 'ru', es: 'es', mx: 'es', br: 'pt', pt: 'pt', fr: 'fr',
                    id: 'id', th: 'th', tr: 'tr', de: 'de', vn: 'vi'};
    var COMIC_SITE = /(manga|manhwa|manhua|toon|comic|piccoma|mechacomic|booklive|toomics|scans?\b)/i;
    var MANGA_SITE = /(manga(?!.*hwa)|fanfox|comick|jumpplus|shonenjump|mechacomic|piccoma|booklive|komiku|raw)/i;
    var MANHWA_SITE = /(toon|manhwa|scans?\b|asura|reaper|flame|demonic|kingofshojo|mgeko|toomics)/i;

    /** The popular lists of the top sites of one kind, the preferred language's sites first; `only`: the
     *  sites picked in the page's Sites panel, in their order. */
    function runTop(id, kind, language, token, only) {
        var comic = kind === 'manga' || kind === 'manhwa' || kind === 'comic';
        var sites = SITES.filter(function (site) {
            return (!!site.comic === comic && !(site.lnreader || []).length) || (comic && COMIC_SITE.test(site.site));
        });
        if (only && only.length) {
            sites = only.map(function (name) {
                return SITES.filter(function (s) { return s.site === name; })[0] || {site: name, host: name};
            });
            language = 'all';
        } else if (comic) {
            var manga = sites.filter(function (s) { return MANGA_SITE.test(s.site); });
            var manhwa = sites.filter(function (s) { return MANHWA_SITE.test(s.site) && manga.indexOf(s) < 0; });
            var rest = sites.filter(function (s) { return manga.indexOf(s) < 0 && manhwa.indexOf(s) < 0; });
            sites = ({manga: manga, manhwa: manhwa}[kind]) || (rest.length ? rest : sites);
        }
        if (language && language !== 'all') {
            var fits = function (site) { return (TLD_LANG[site.site.split('.').pop()] || 'en') === language; };
            sites = sites.filter(fits).concat(sites.filter(function (s) { return !fits(s); }));
        }
        sites = sites.slice(0, 80);  // the busiest 80 of the kind (with the LNReader sites the page reads, ~180)
        topSites = sites;
        call('progress', id, {total: sites.length});
        var seen = {};
        var all = each(sites, 12, function (site, index) {
            var started = Date.now();
            return sitePopular('https://' + site.host + '/', token).catch(function () { return []; }).then(function (hits) {
                var ms = Date.now() - started, cards = [];
                hits.forEach(function (hit, position) {
                    var k = hit.url.replace(/\/+$/, '');
                    if (seen[k] || isNoResults(hit.title) || !looksLikeBook(hit.url, hit.title)) return;
                    seen[k] = true;
                    cards.push(Object.assign(card(hit.url, hit.title, hit.snippet, 'site', hit.cover),
                                             {ms: ms, siteRank: index, position: position, listLength: hits.length, via: 'top'}));
                });
                call('progress', id, {site: site.site, ms: ms, found: cards.length, state: cards.length ? 'ok' : 'failed'});
                if (cards.length && !stopped(token)) call('add', cards, id);
            });
        }, token);
        return within(all, 40000, token).then(function () { return {results: Object.keys(seen).length}; });
    }

    function search(id, queries) {
        var token = newToken();
        ready.then(function () {
            var totals = {};
            return Promise.all(queries.slice(0, 6).map(function (item) {
                var mode = item.mode === 'tag' || item.mode === 'author' ? item.mode : 'name';
                var run = item.mode === 'id' ? runById(id, item.query, token) : runSearch(id, item.query, mode, token);
                return run.catch(function () { return {failed: 1}; }).then(function (summary) {
                    Object.keys(summary).forEach(function (name) {
                        var value = summary[name];
                        totals[name] = typeof value === 'number' ? (totals[name] || 0) + value : (totals[name] || value);
                    });
                });
            })).then(function () { call('done', id, totals); });
        });
    }
    var topSites = [];  // the shown top list's sites, for their next pages
    /** A search's next results as it is scrolled: NovelDB's page `ndbPage` (default `page`; 0: none) and,
     *  with `sites`, each site's next results page. The page hears moreDone(id, {more, ndbPage, ndbMore, ndbOnly}). */
    function more(id, queries, page, ndbPage, sites) {
        if (ndbPage == null) ndbPage = page;
        if (sites === undefined) sites = true;
        var token = current.token || newToken(), state = {more: false, ndbPage: ndbPage, ndbMore: false, ndbOnly: !sites};
        ready.then(function () {
            return Promise.all(queries.filter(function (q) { return q.mode !== 'id'; }).slice(0, 6).map(function (item) {
                var mode = item.mode === 'tag' || item.mode === 'author' ? item.mode : 'name', kind = mode === 'tag' ? 'tag' : 'search', text = item.query, seen = {};
                function offer(cards) {
                    var fresh = cards.filter(function (c) { var k = c.url.replace(/\/+$/, ''); if (seen[k]) return false; seen[k] = true; return true; });
                    if (fresh.length && !stopped(token)) call('add', fresh.map(function (c) { return Object.assign({}, c, {via: mode}); }), id);
                }
                var jobs = ndbPage >= 1 ? [ndbSearch(text, mode, NDB_MORE, NDB_FIRST + (ndbPage - 1) * NDB_MORE).then(function (found) {
                    offer(found.cards.map(function (c) { return Object.assign(c, {verified: mode !== 'name'}); }));
                    if (found.more) { state.more = true; state.ndbMore = true; }
                }, function () {})] : [];
                if (sites && (mode === 'tag' || mode === 'author')) Object.keys(srcNext).forEach(function (key) {
                    var parts = key.split('\t'), at = srcNext[key];
                    if (parts[1] !== mode || parts[2] !== text) return;
                    jobs.push(sourceSearch(parts[0], mode, parts[3], at, token).then(function (result) {
                        if (result.more) { srcNext[key] = at + 1; state.more = true; } else delete srcNext[key];
                        offer(result.cards);
                    }));
                });
                if (sites) SITES.forEach(function (site) {
                    if (site.searched === false) return;
                    var home = 'https://' + site.host + '/';
                    if (!hasMore(home, kind, text)) return;
                    jobs.push(siteMore(home, kind, text, token).then(function (hits) {
                        offer(hits.map(function (hit) { return Object.assign(card(hit.url, hit.title, hit.snippet, 'site', hit.cover), {verified: !!hit.verified}); }));
                        if (hasMore(home, kind, text)) state.more = true;
                    }));
                });
                return within(Promise.all(jobs), 30000, token);
            }));
        }).catch(function () {}).then(function () { call('moreDone', id, state); });
    }
    /** The next page of the shown top list's sites' popular lists. */
    /** A Top list of NovelDB's sources (`sources`: "npia", ...): page `page` of their books, the most
     *  read first, each source in turn. The page hears `ndbTopDone(id, {more})`. */
    function ndbTop(id, sources, page) {
        var position = page * NDB_MORE;
        ndbSearch(sources.join(','), 'source', NDB_MORE, position, function (c) {
            call('add', [Object.assign(c, {via: 'top', siteRank: 0, position: position++, listLength: 1e6})], id);
        }).then(function (found) {
            call('ndbTopDone', id, {more: found.more});
        }, function () { call('ndbTopDone', id, {more: false}); });
    }
    function topMore(id) {
        var token = current.token || newToken(), state = {more: false};
        ready.then(function () {
            return within(each(topSites, 12, function (site, index) {
                var home = 'https://' + site.host + '/';
                if (!hasMore(home, 'popular', '')) return Promise.resolve();
                return siteMore(home, 'popular', '', token).then(function (hits) {
                    var cards = hits.filter(function (hit) { return looksLikeBook(hit.url, hit.title); }).map(function (hit, position) {
                        return Object.assign(card(hit.url, hit.title, hit.snippet, 'site', hit.cover),
                                             {siteRank: index, position: position, listLength: hits.length, via: 'top'});
                    });
                    if (hasMore(home, 'popular', '')) state.more = true;
                    if (cards.length && !stopped(token)) call('add', cards, id);
                });
            }, token), 30000, token);
        }).catch(function () {}).then(function () { call('moreDone', id, state); });
    }
    function top(id, kind, language, only) {
        var token = newToken();
        ready.then(function () { return runTop(id, kind, language, token, only); })
            .catch(function () { return {failed: 1}; })
            .then(function (summary) { call('done', id, summary); });
    }

    // -- the details cache and covers --------------------------------------------------------------

    var DETAILS_KEY = 'noveldb.generic.details.v4', DETAILS_DAYS = 3;  // v3: none from before a site's blurb gave way
    // Read from storage once and written back a second after the last change: the whole cache is one
    // stored value (hundreds of synopses), too big to parse or write for every card.
    var detailsKept = null, detailsTimer = 0;
    function detailsCache() { return detailsKept || (detailsKept = store(DETAILS_KEY) || {}); }
    function cachedDetails(url) {
        var found = detailsCache()[url];
        return found && Date.now() - found.at < DETAILS_DAYS * 86400000 ? found.value : null;
    }
    function keepDetails(url, value) {
        var all = detailsCache();
        all[url] = {at: Date.now(), value: value};
        var keys = Object.keys(all);
        if (keys.length > 400) {
            keys.sort(function (a, b) { return all[a].at - all[b].at; }).slice(0, keys.length - 400).forEach(function (k) { delete all[k]; });
        }
        clearTimeout(detailsTimer);
        detailsTimer = setTimeout(function () { store(DETAILS_KEY, detailsKept); }, 1000);
    }
    function detailsFor(item) {
        var cached = cachedDetails(item.url);
        // Answered after the page's call returns, as a read would be: never inside its own loop.
        if (cached) { Promise.resolve().then(function () { call('details', Object.assign({}, item, cached, {complete: true})); }); return; }
        ready.then(function () { return details(item); }).catch(function () { return Object.assign({}, item, {complete: true}); })
            .then(function (found) {
                var keep = {};
                Object.keys(found).forEach(function (name) {
                    if (['url', 'site', 'source', 'via', 'complete', 'ms'].indexOf(name) < 0 && found[name]
                        && (!Array.isArray(found[name]) || found[name].length)) keep[name] = found[name];
                });
                if (Object.keys(keep).length) keepDetails(item.url, keep);
                call('details', found);
            });
    }

    // Covers the page made smaller before (IndexedDB, so they survive a restart; 300 at most).
    var covers = new Promise(function (resolve) {
        try {
            var open = indexedDB.open('noveldb-catalog', 1);
            open.onupgradeneeded = function () { open.result.createObjectStore('covers'); };
            open.onsuccess = function () { resolve(open.result); };
            open.onerror = function () { resolve(null); };
        } catch (e) { resolve(null); }
    });
    function coverGet(key) {
        return covers.then(function (db) {
            if (!db) return null;
            return new Promise(function (resolve) {
                var request = db.transaction('covers').objectStore('covers').get(key);
                request.onsuccess = function () { resolve(request.result || null); };
                request.onerror = function () { resolve(null); };
            });
        });
    }
    function coverPut(key, dataUrl) {
        covers.then(function (db) {
            if (!db) return;
            var shelf = db.transaction('covers', 'readwrite').objectStore('covers');
            shelf.put(dataUrl, key);
            var count = shelf.count();
            count.onsuccess = function () {
                if (count.result <= 300) return;
                var cursor = shelf.openCursor(), extra = count.result - 300;
                cursor.onsuccess = function () {
                    var at = cursor.result;
                    if (at && extra-- > 0) { at.delete(); at.continue(); }
                };
            };
        });
    }

    function image(id, url, referer) {
        var held = pages.cache[url];
        if (held) {
            call('fetched', id, {dataUrl: held, bytes: Math.round(held.length * 0.75)});
            return;
        }
        fetchUrl(url, {binary: true, referer: referer, headers: {Accept: 'image/*'}}).then(function (result) {
            if (!result || result.status >= 400 || !result.bytes.length) { call('fetched', id, null); return; }
            var kind = (/content-type:\s*(image\/[\w.+-]+)/i.exec(result.headers) || [])[1] || 'image/jpeg';
            var binary = '';
            for (var i = 0; i < result.bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, result.bytes.subarray(i, i + 0x8000));
            call('fetched', id, {dataUrl: 'data:' + kind + ';base64,' + btoa(binary), bytes: result.bytes.length});
        });
    }

    // -- the reader --------------------------------------------------------------------------------

    var BLOCKED = 'This page is blocked: the site may need you to sign in, or to buy the book. Sign in with Login '
        + '(the app keeps your login), then try again.';
    var reading = {id: null};
    var books = new Map();  // url -> {engine, data}; the last six
    function note(text) { if (reading.id != null) call('readerStatus', reading.id, text); }

    function loadScript(name, ready_) {
        if (ready_()) return Promise.resolve(true);
        return fetch(BASE + name).then(function (r) { return r.text(); }).then(function (source) {
            (0, eval)(source);
            return ready_();
        }).catch(function () { return false; });
    }
    function lnreaderKnows(url) {
        try {
            var name = host(url);
            return !!window.__LNR_sites && JSON.parse(window.__LNR_sites()).some(function (p) { return host(p.site) === name; });
        } catch (e) { return false; }
    }
    var harunekoHosts = null;
    function harunekoListed(url) {
        return !!harunekoHosts && harunekoHosts.has(host(url));
    }
    loadJson('haruneko-manifest.json').then(function (manifest) {
        harunekoHosts = new Set(((manifest && manifest.sites) || []).map(host).filter(function (h) { return h; }));
    });
    function harunekoReady() { return window.__HN_READY === true; }

    // The app's built-in scrapers first (npia, sfc, ntk, ...: sites the generic engine cannot read): the
    // app answers {skip} for a site none of them owns, and the page's own engines follow.
    function appAsk(request) {
        return window.NovelDBTransport ? Promise.resolve(window.NovelDBTransport(request)) : Promise.resolve({error: 'no app', skip: true});
    }
    // Then the app's site engines that list the site (gallery-dl, HaruNeko, ...: a download's), then the page's own.
    function engineFor(url) {
        var engines = ['app', 'engines'];
        if (harunekoListed(url)) engines.push('haruneko');
        engines.push('generic');
        return engines;
    }
    function parseJson(text) {
        var value = typeof text === 'string' ? JSON.parse(text) : text;
        if (!value || value.error) throw new Error((value && value.error) || 'no answer');
        return value;
    }
    // -- a Library EPUB in the reader (file: addresses; the app hands over the file) ----------------
    /** The files of a ZIP (an EPUB is one): name -> {method, size, local}. */
    function zipEntries(bytes) {
        var view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), end = -1;
        for (var i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
            if (view.getUint32(i, true) === 0x06054b50) { end = i; break; }
        }
        if (end < 0) throw new Error('not an EPUB (no ZIP directory)');
        var count = view.getUint16(end + 10, true), at = view.getUint32(end + 16, true), out = new Map();
        for (var n = 0; n < count && view.getUint32(at, true) === 0x02014b50; n++) {
            var nameLength = view.getUint16(at + 28, true), extra = view.getUint16(at + 30, true), comment = view.getUint16(at + 32, true);
            var name = new TextDecoder().decode(bytes.subarray(at + 46, at + 46 + nameLength));
            out.set(name, {method: view.getUint16(at + 10, true), size: view.getUint32(at + 20, true), local: view.getUint32(at + 42, true)});
            at += 46 + nameLength + extra + comment;
        }
        return out;
    }
    function zipRead(epub, name) {
        var entry = epub.entries.get(name);
        if (!entry) return Promise.resolve(null);
        var view = new DataView(epub.bytes.buffer, epub.bytes.byteOffset, epub.bytes.byteLength);
        var start = entry.local + 30 + view.getUint16(entry.local + 26, true) + view.getUint16(entry.local + 28, true);
        var data = epub.bytes.subarray(start, start + entry.size);
        if (entry.method === 0) return Promise.resolve(data);
        return new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer()
            .then(function (buffer) { return new Uint8Array(buffer); });
    }
    function zipText(epub, name) { return zipRead(epub, name).then(function (data) { return data ? new TextDecoder().decode(data) : ''; }); }
    function epubPath(base, href) {
        var parts = (base ? base.split('/') : []).concat(decodeURIComponent(String(href || '').split('#')[0]).split('/')), out = [];
        parts.forEach(function (part) { if (part === '..') out.pop(); else if (part && part !== '.') out.push(part); });
        return out.join('/');
    }
    function folderOf(path) { return path.indexOf('/') >= 0 ? path.replace(/\/[^\/]*$/, '') : ''; }
    function xmlDoc(text) { return new DOMParser().parseFromString(text, 'application/xml'); }
    var epubs = new Map();
    /** The EPUB at a file: address: its spine (reading order), with names from its table of contents. */
    function epubOpen(url) {
        if (epubs.has(url)) return epubs.get(url);
        var opened = appAsk({op: 'file', url: url}).then(function (reply) {
            if (!reply || reply.error || !reply.responseBase64) throw new Error((reply && reply.error) || 'the book could not be opened');
            var epub = {bytes: bytesOf(reply.responseBase64)};
            epub.entries = zipEntries(epub.bytes);
            return zipText(epub, 'META-INF/container.xml').then(function (container) {
                var rootfile = xmlDoc(container).querySelector('rootfile');
                epub.opf = rootfile ? rootfile.getAttribute('full-path') : '';
                return zipText(epub, epub.opf);
            }).then(function (opfText) {
                var opf = xmlDoc(opfText), items = {}, base = folderOf(epub.opf);
                Array.prototype.forEach.call(opf.getElementsByTagName('item'), function (item) {
                    items[item.getAttribute('id')] = {href: epubPath(base, item.getAttribute('href')),
                        type: item.getAttribute('media-type') || '', props: item.getAttribute('properties') || ''};
                });
                var meta = function (tag) { var found = opf.getElementsByTagNameNS('*', tag)[0]; return found ? found.textContent.trim() : ''; };
                epub.title = meta('title');
                epub.author = meta('creator');
                epub.spine = Array.prototype.map.call(opf.getElementsByTagName('itemref'), function (ref) { return items[ref.getAttribute('idref')]; })
                    .filter(function (item) { return item && /html/.test(item.type); });
                // Chapter names: the EPUB 3 contents page, else the EPUB 2 one (toc.ncx).
                epub.names = {};
                var all = Object.keys(items).map(function (id) { return items[id]; });
                var nav = all.filter(function (item) { return /\bnav\b/.test(item.props); })[0];
                var toc = nav || all.filter(function (item) { return /ncx/.test(item.type); })[0];
                if (!toc) return epub;
                return zipText(epub, toc.href).then(function (text) {
                    var name = function (href, label) {
                        var path = epubPath(folderOf(toc.href), href);
                        if (!epub.names[path] && label) epub.names[path] = label.replace(/\s+/g, ' ').trim();
                    };
                    if (nav) {
                        Array.prototype.forEach.call(new DOMParser().parseFromString(text, 'text/html').querySelectorAll('nav a[href]'), function (a) {
                            name(a.getAttribute('href'), a.textContent);
                        });
                    } else {
                        Array.prototype.forEach.call(xmlDoc(text).getElementsByTagName('navPoint'), function (point) {
                            var content = point.getElementsByTagName('content')[0], label = point.getElementsByTagName('text')[0];
                            if (content && label) name(content.getAttribute('src'), label.textContent);
                        });
                    }
                    return epub;
                });
            });
        });
        epubs.set(url, opened);
        opened.catch(function () { epubs.delete(url); });
        if (epubs.size > 2) epubs.delete(epubs.keys().next().value);  // a whole book each: few are kept
        return opened;
    }
    function epubBook(url) {
        return epubOpen(url).then(function (epub) {
            if (!epub.spine.length) throw new Error('the book has no chapters');
            var data = {bookname: epub.title, author: epub.author, chapters: epub.spine.map(function (item, i) {
                return {name: epub.names[item.href] || 'Part ' + (i + 1)};
            })};
            books.set(url, {engine: 'epub', data: data});
            return {title: epub.title || '', author: epub.author || '', engine: 'EPUB',
                    chapters: data.chapters.map(function (c) { return {name: c.name, section: ''}; })};
        });
    }
    var PICTURE_TYPES = {png: 'image/png', gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml', avif: 'image/avif'};
    function epubChapter(url, index) {
        return epubOpen(url).then(function (epub) {
            var item = epub.spine[index];
            if (!item) throw new Error('no such chapter');
            return zipText(epub, item.href).then(function (text) {
                var doc = new DOMParser().parseFromString(text, 'application/xhtml+xml');
                if (doc.querySelector('parsererror') || !doc.body) doc = new DOMParser().parseFromString(text, 'text/html');
                var body = doc.body;
                // Pictures from inside the book, as data: addresses (the reader cannot reach the file itself).
                var pictures = Array.prototype.map.call(body.querySelectorAll('img, image'), function (node) {
                    var href = node.getAttribute('src') || node.getAttribute('href') || node.getAttributeNS('http://www.w3.org/1999/xlink', 'href');
                    if (!href || /^(data|https?):/i.test(href)) return null;
                    var path = epubPath(folderOf(item.href), href);
                    return zipRead(epub, path).then(function (data) {
                        if (!data) return;
                        var binary = '';
                        for (var i = 0; i < data.length; i += 0x8000) binary += String.fromCharCode.apply(null, data.subarray(i, i + 0x8000));
                        var img = doc.createElement('img');
                        img.setAttribute('src', 'data:' + (PICTURE_TYPES[path.split('.').pop().toLowerCase()] || 'image/jpeg') + ';base64,' + btoa(binary));
                        var svg = node.closest('svg');
                        (node.tagName.toLowerCase() === 'img' ? node : svg || node).replaceWith(img);
                    });
                });
                Array.prototype.forEach.call(body.querySelectorAll('script, style, link'), function (node) { node.remove(); });
                return Promise.all(pictures).then(function () {
                    return {chapterName: epub.names[item.href] || '', contentHtml: body.innerHTML};
                });
            });
        });
    }

    function readBook(url) {
        if (/^file:/i.test(url)) return epubBook(url);
        var engines = engineFor(url), errors = [];
        function next() {
            if (!engines.length) return Promise.reject(new Error(errors.join('; ') || 'no engine could read the book'));
            var name = engines.shift();
            var start = name === 'app' || name === 'engines'
                ? (note(name === 'app' ? 'Reading the book with the built-in scraper for its site…'
                        : 'Reading the book with the app’s site engines (gallery-dl, HaruNeko, …)…'),
                   appAsk(name === 'app' ? {op: 'book', url: url} : {op: 'book', url: url, engines: true}).then(function (reply) {
                    if (reply && reply.skip) { name = ''; throw new Error('skip'); }
                    return reply;
                }))
                : name === 'haruneko'
                ? (note('Loading the HaruNeko websites…'), loadScript('haruneko-lib.js', harunekoReady).then(function (ok) {
                    if (!ok) throw new Error('the HaruNeko websites did not load');
                    note('Reading the book with HaruNeko…');
                    return window.__HN_parseBook(url);
                }))
                : (note('Reading the chapter list…'), Promise.resolve(window.__GEN_parseBook(url)));
            return start.then(parseJson).then(function (data) {
                if (!(data.chapters || []).length) throw new Error('no chapters found');
                books.set(url, {engine: name, data: data});
                if (books.size > 6) books.delete(books.keys().next().value);
                return {title: data.bookname || '', author: data.author || '', engine: data.ruleName || name,
                        chapters: data.chapters.map(function (c, i) { return {name: c.name || 'Chapter ' + (i + 1), section: c.sectionName || ''}; })};
            }).catch(function (error) {
                if (name) errors.push(name + ': ' + ((error && error.message) || error));
                // A site the app's scraper owns but could not open (a sign-in, a lock): its message stands.
                if (name === 'app') return Promise.reject(error);
                return next();
            });
        }
        return next();
    }
    function readChapter(url, index) {
        var known = books.get(url);
        var opened = known ? Promise.resolve() : readBook(url);
        return opened.then(function () {
            known = books.get(url);
            if (known.engine === 'epub') return epubChapter(url, index).then(function (result) {
                return {name: result.chapterName || known.data.chapters[index].name, html: result.contentHtml, text: ''};
            });
            var chapters = known.data.chapters;
            if (index < 0 || index >= chapters.length) throw new Error('no such chapter');
            var info = chapters[index];
            note('Scraping ' + (info.name || 'the chapter') + '…');
            var item = JSON.stringify(info);
            var haruneko = known.engine === 'haruneko';
            if (haruneko) watchPages(reading.id);
            var asked = known.engine === 'app' ? appAsk({op: 'chapter', url: url, index: index})
                : known.engine === 'engines' ? appAsk({op: 'chapter', url: url, index: index, engines: true})
                : haruneko ? window.__HN_parseChapter(item) : window.__GEN_parseChapter(item);
            return Promise.resolve(asked).then(parseJson).then(function (result) {
                // HaruNeko fetched each page itself (some sites' pages need it): the reader is given those bytes.
                (result.images || []).forEach(function (picture) {
                    if (picture.url && picture.data) pages.cache[picture.url] = picture.data;
                });
                return {name: result.chapterName || info.name || '', html: result.contentHtml || '', text: result.contentText || ''};
            }).then(function (value) { pages.watching = null; return value; }, function (error) { pages.watching = null; throw error; });
        });
    }
    // A HaruNeko chapter's pages, shown as each one arrives (the bundle hands them over only at the end).
    var pages = {watching: null, cache: {}};
    function watchPages(id) {
        pages.cache = {};
        pages.watching = {id: id, images: []};
    }
    function pageArrived(url, reply) {
        var watch = pages.watching;
        if (!watch || !reply || !reply.responseBase64) return;
        var kind = (/content-type:\s*(image\/[\w.+-]+)/i.exec(reply.responseHeaders || '') || [])[1];
        if (!kind || pages.cache[url]) return;
        pages.cache[url] = 'data:' + kind + ';base64,' + reply.responseBase64;
        watch.images.push(url);
        call('readerImages', watch.id, {images: watch.images.slice(), total: watch.images.length});
    }

    function read(id, work) {
        reading.id = id;
        work().then(function (value) {
            call('fetched', id, value);
        }, function (error) {
            call('fetched', id, {error: String((error && error.message) || error).slice(0, 300), blocked: true, message: BLOCKED});
        }).then(function () { if (reading.id === id) reading.id = null; });
    }

    // -- the page's messages -----------------------------------------------------------------------

    function init() {
        ready.then(function () {
            var lnreader = [];
            SITES.forEach(function (site) {
                (site.lnreader || []).slice(0, 1).forEach(function (plugin) { lnreader.push({id: plugin, site: site.site, comic: site.comic}); });
            });
            call('init', {sites: SITES.map(function (s) { return s.site; }), comic: SITES.filter(function (s) { return s.comic; }).map(function (s) { return s.site; }), lnreader: lnreader, searched: SITES.filter(function (s) { return s.searched !== false; }).length, downloadIcon: window.NovelDBDownloadIcon || ''});
        });
    }
    // A web page, or a Library EPUB (the app hands over only EPUBs in its download folder).
    function readable(url) { return /^https?:/.test(url || '') || /^file:.*\.epub$/i.test(url || ''); }
    function handle(message) {
        switch (message.type) {
        case 'genericReady': init(); break;
        case 'genericSearch': search(message.id || 0, message.queries || [{query: message.query, mode: message.mode}]); break;
        case 'genericStop': if (current.token) current.token.stopped = true; break;
        case 'genericTop':
            top(message.id || 0, message.kind, String(message.language || 'all').slice(0, 5),
                (Array.isArray(message.sites) ? message.sites : []).filter(function (s) { return typeof s === 'string' && s; }).slice(0, 50));
            break;
        case 'genericMore':
            if (message.page >= 1 && Array.isArray(message.queries)) more(message.id || 0, message.queries, message.page, message.ndbPage, message.sites !== false);
            break;
        case 'genericTopMore': topMore(message.id || 0); break;
        case 'genericNdbTags':
            // NovelDB's tag names, for the tag menu's filter (the most used first).
            ndbManifest().then(function (manifest) {
                if (!manifest || manifest.tagsAt) return manifest;
                // A table of contents kept from before the index listed its tags: read it again once.
                ndb.manifest = null;
                store(NDB_KEY, null);
                return ndbManifest();
            }).then(function (manifest) {
                return manifest && manifest.tagsAt ? ndbPiece(manifest, 'tags', 0, false, manifest.tagsAt) : null;
            }).then(function (listed) {
                call('ndbTags', (listed || []).map(function (entry) { return entry[0]; }));
            }, function () { call('ndbTags', []); });
            break;
        case 'genericNdbTop':
            ndbTop(message.id || 0, (Array.isArray(message.sources) ? message.sources : []).filter(function (s) { return /^[a-z0-9_]{1,20}$/.test(s); }).slice(0, 12),
                   Math.max(0, Math.min(10000, Number(message.page) || 0)));
            break;
        case 'genericFetch':
            transport(message.request).then(function (reply) { call('fetched', message.id, reply); });
            break;
        case 'genericImage': image(message.id, message.url, message.referer || ''); break;
        case 'genericDetails': if (message.card && /^https?:/.test(message.card.url || '')) detailsFor(message.card); break;
        case 'genericCover': coverGet(String(message.key)).then(function (data) { call('fetched', message.id, data ? {dataUrl: data} : null); }); break;
        case 'genericCoverPut': if (/^data:image\//.test(message.dataUrl || '')) coverPut(String(message.key), message.dataUrl); break;
        case 'genericBook':
            if (readable(message.url)) read(message.id, function () { return readBook(message.url); });
            else call('fetched', message.id, {error: 'only web pages and Library EPUBs can be read here'});
            break;
        case 'genericChapter':
            if (readable(message.url)) read(message.id, function () { return readChapter(message.url, message.index | 0); });
            else call('fetched', message.id, {error: 'only web pages and Library EPUBs can be read here'});
            break;
        default:
            // genericDownload, genericLogin, cartAdd, cartRemove, open: the app's own actions.
            native(message);
        }
    }

    // generic.js (and the LNReader and HaruNeko bundles) reach the app the desktop's way.
    window.NovelDBQt = {postMessage: function (text) {
        var message;
        try { message = typeof text === 'string' ? JSON.parse(text) : text; } catch (e) { return; }
        if (message && message.type) handle(message);
    }};
    window.__GEN_transport = function (details) { return transport(details); };
    window.__noveldb_engine_transport = function (details) {
        return transport(details).then(function (reply) {
            if (details && details.url) pageArrived(details.url, reply);
            return reply;
        });
    };
    window.__noveldb_window_script = function (request) { return transport(Object.assign({}, request, {op: 'window'})); };
    window.NovelDBCatalogApp = {plan: plan, extractCards: extractCards, looksLikeBook: looksLikeBook, relevant: relevant,
                                cleanTitle: cleanTitle, cardShape: cardShape, ndbSearch: ndbSearch, ndbDescription: ndbDescription};
})();
