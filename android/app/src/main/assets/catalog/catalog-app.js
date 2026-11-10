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
    // webtoons.com sends a phone to m.webtoons.com, whose search results a script draws: a desktop browser's name
    // gets the pages with the results in them (generic-lib.js asks the same way).
    var DESKTOP_SITES = /(^|\.)webtoons\.com$/i;
    var DESKTOP_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
    var CHALLENGE = /just a moment|attention required|security check|verify you are human/i;
    var slots = {free: 24, waiting: []};
    var renders = {free: 2, waiting: []}, googleSlots = {free: 2, waiting: []};

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
        if (DESKTOP_SITES.test(host(url)) && !headers['User-Agent']) headers['User-Agent'] = DESKTOP_AGENT;
        var details = {url: url, method: (options.method || 'GET').toUpperCase(), headers: headers,
                       responseType: 'arraybuffer', timeout: options.timeout || (options.quick ? 8000 : 15000)};
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

    var SITES = [], ID_TEMPLATES = [], siteHosts = {}, SEARCH_PROFILE = {preferred: [], measurements: {}};
    function loadJson(name) {
        return fetch(BASE + name).then(function (r) { return r.json(); }).catch(function () { return null; });
    }
    var ready = Promise.all([loadJson('generic_sites.json'), loadJson('id_templates.json'), loadJson('search_profile.json')]).then(function (found) {
        SITES = ((found[0] || {}).sites) || [];
        ID_TEMPLATES = ((found[1] || {}).templates) || [];
        SEARCH_PROFILE = found[2] || SEARCH_PROFILE;
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
    var LISTING = /^(search|tags?|genres?|categor(y|ies)|authors?|users?|members?|profiles?|reviews?|comments?|discussions?|wiki|characters?|articles?|news|forums?|login|register|latest|popular|ranking|rankings|top|list|library|blogs?|contests?|novel-list|manga-list|originals|canvas|page|contact|contact-us|about|about-us|privacy|privacy-policy|terms|terms-of-service|dmca|faq|feedback|support|donate)$/i;

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
    var NDB_FORMAT = 2, NDB_LEAD = 'npia', NDB_MULTI_PAGES = 40, NDB_PIECES = 160;
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
        var asked = ndbReading(ndbRange(NDB_INDEX + manifest.bin, at[0], at[1]))
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
        // Each is a decompressed bucket or block (up to a few MB as objects): a phone's page keeps few.
        if (ndb.pieces.size > NDB_PIECES) ndb.pieces.delete(ndb.pieces.keys().next().value);
        return asked;
    }
    // The release address sends each request on to a signed one (a second round trip every time, seconds on a
    // phone): that address is used directly while it is valid (noveldb_index._range). A read that has not
    // answered in NDB_HEDGE_MS is asked again, and whichever answers first is taken (now and then one stalls).
    var NDB_SIGNED_MS = 240e3, NDB_HEDGE_MS = 2000;
    function ndbRange(url, offset, length) {
        var headers = {Range: 'bytes=' + offset + '-' + (offset + length - 1)};
        var ask = function (address) { return ndbHedged(function () { return fetchUrl(address, {binary: true, priority: true, headers: headers}); }); };
        var whole = function (result) { return result && result.status === 206 && result.bytes.length === length; };
        var signed = ndb.signed && ndb.signed.url === url && Date.now() < ndb.signed.until ? ndb.signed.final : null;
        var viaRelease = function () {
            return ask(url).then(function (result) {
                if (whole(result) && result.url && result.url !== url) ndb.signed = {url: url, final: result.url, until: Date.now() + NDB_SIGNED_MS};
                return result;
            });
        };
        if (!signed) {
            // One read finds the signed address; the others wait for it rather than each taking the long way
            // (a search's ten word buckets at once took 17 s on a phone, two round trips each and some asked twice).
            if (ndb.signing) return ndb.signing.then(function () { return ndbRange(url, offset, length); });
            var first = viaRelease();
            ndb.signing = first.then(function () { ndb.signing = null; }, function () { ndb.signing = null; });
            return first;
        }
        return ask(signed).then(function (result) {
            if (whole(result)) return result;
            ndb.signed = null;  // expired or refused: through the release address again
            return viaRelease();
        });
    }
    function ndbHedged(request) {
        return new Promise(function (resolve) {
            var answered = false, left = 1;
            var take = function (result) {
                if (answered) return;
                if (result || --left <= 0) { answered = true; resolve(result); }
            };
            request().then(take, function () { take(null); });
            setTimeout(function () { if (!answered) { left++; request().then(take, function () { take(null); }); } }, NDB_HEDGE_MS);
        });
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
    // The age-rating tags the index gives books (scripts/noveldb_index/build.cjs): the rating each one means;
    // the index keeps their lists in the catalogue's other orders too (noveldb_index.INDEX_SORTS).
    var NDB_AGE_TAGS = {r19: 19, r15: 15};
    var NDB_SORTS = ['likes', 'views', 'chapters', 'new', 'updated', 'title'];
    function ndbSearch(text, mode, limit, offset, each, onTotal, sort, sources) {
        var wanted = mode === 'source' ? String(text).split(',').filter(Boolean) : mode === 'tag' ? [ndbTag(text)]
            : mode === 'author' ? ['@' + ndbNormal(text).replace(/\s+/g, ' ').trim()] : ndbTokens(text);
        limit = limit || NDB_MORE;
        if (!wanted.length) return Promise.resolve({cards: [], more: false});
        var more = false;
        // An age rating is the index's own tag: its list alone (not the site tags that look like it, "R-19",
        // "Adult"), and only the books it rates so (the site's own rule, as NovelDB shows it).
        var rated = mode === 'tag' ? NDB_AGE_TAGS[ndbNormal(text).trim()] || 0 : 0;
        return ndbManifest().then(function (manifest) {
            if (!manifest) return [];
            var sorted = rated && NDB_SORTS.indexOf(sort) >= 0;  // "#r19|new": the newest first, every source in one list
            var lists = mode === 'source' ? ndbSourceLists(manifest, wanted)
                : mode === 'tag' ? (sorted ? ndbLists(manifest, wanted[0] + '|' + sort).then(function (found) {
                    return Object.keys(found).length ? found : ndbLists(manifest, wanted[0]);  // an older index: its own order
                }) : rated ? ndbLists(manifest, wanted[0]) : ndbTagLists(manifest, text)) : ndbStreams(manifest, wanted);
            return lists.then(function (streams) {
                // `sources`: only their books (the sites picked in the Sites panel).
                if (sources && sources.length) Object.keys(streams).forEach(function (name) { if (sources.indexOf(name) < 0) delete streams[name]; });
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
                                published: String(row[15] || '').slice(0, 10), also: ndbAlso(row[16]),
                                ageRating: row[17] === 19 || row[17] === 15 ? row[17] : 0};  // 19 adults only, 15 teens
                    }).then(function (card) {
                        if (card && rated && card.ageRating !== rated) return null;
                        if (card && each) each(card);
                        return card;
                    });
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
            // Its site asked for the book as it is now (one request), while its synopsis is read.
            var ref = item.noveldb || {};
            var live = Promise.race([sourceLookup(ref.source, ref.id, item.author), new Promise(function (done) { setTimeout(done, 12000, null); })]);
            return Promise.all([ndbDescription(item), live]).then(function (found) {
                return ndbRefreshed(Object.assign({}, item, {complete: true, description: found[0] || item.description}), found[1]);
            });
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
    // PLAN_VERSION: plans made before it are made again (2: the guesses under a language home such as /en/).
    var PLANS_KEY = 'noveldb.generic.plans', PLAN_DAYS = 7, PLAN_VERSION = 2;
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
        if (known && Date.now() / 1000 - (known.checked || 0) < PLAN_DAYS * 86400 && known.home_cards && known.version === PLAN_VERSION) {
            return Promise.resolve(known);
        }
        if (planning[home]) return planning[home];
        planning[home] = fetchUrl(home).then(function (result) {
            var made = {checked: Date.now() / 1000, version: PLAN_VERSION, ways: [], tags: {}, home: home, reachable: !!(result && result.status < 400)};
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
                var origin = new URL(made.home).origin, language = new URL(made.home).pathname.replace(/\/+$/, '');
                // A home under a language (webtoons.com/en/): its search is there too (/en/search?keyword=).
                if (/^\/[a-z]{2}(-[a-z]{2,4})?$/i.test(language)) {
                    COMMON_SEARCHES.forEach(function (path) { made.ways.push({kind: 'get', template: origin + language + path, guess: true}); });
                }
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

    function googleHits(html) {
        var doc = G().parse(html), seen = {}, out = [];
        doc.querySelectorAll('h3').forEach(function (heading) {
            var anchor = heading.closest('a') || heading.querySelector('a');
            if (!anchor || anchor.closest('[data-text-ad]')) return;
            var url = anchor.getAttribute('href') || '';
            if (/^(?:https:\/\/www\.google\.com)?\/url\?/.test(url)) {
                var redirect = new URL(url, 'https://www.google.com');
                url = redirect.searchParams.get('q') || redirect.searchParams.get('url') || '';
            }
            if (!/^https?:/.test(url) || /(^|\.)google\.com$/.test(host(url)) || seen[url]) return;
            seen[url] = true;
            out.push({url: url, title: heading.textContent.trim(), snippet: ''});
        });
        return out;
    }
    function webSearch(text, token, onHits, onBlocked) {
        var url = 'https://www.google.com/search?hl=en&udm=14&gbv=1&num=10&q=' + encodeURIComponent(text);
        return fetchUrl(url, {stop: token, quick: true, pool: googleSlots}).then(function (result) {
            if (!result || result.status !== 200) { onBlocked(); return []; }
            var html = result.text();
            if (html.indexOf('/httpservice/retry/enablejs') >= 0) {
                return render(url).then(function (page) {
                    var html = page ? page.text() : '';
                    if (!html || /detected unusual traffic|\/sorry\/|before you continue to Google/i.test(html)) { onBlocked(); return []; }
                    return googleHits(html);
                });
            }
            if (/detected unusual traffic|\/sorry\/|before you continue to Google/i.test(html)) { onBlocked(); return []; }
            return googleHits(html);
        }).catch(function () { onBlocked(); return []; }).then(onHits);
    }
    function googleDorks(text, mode) {
        var phrase = '"' + text.replace(/"/g, ' ').trim() + '"';
        var exclude = ' -inurl:chapter -inurl:profile -inurl:login -inurl:search -intitle:"Chapter"';
        var hosts = SITES.filter(function (site) { return speedRank(site.host) < 12000; })
            .sort(function (a, b) { return speedRank(a.host) - speedRank(b.host); }).slice(0, 8).map(function (site) { return site.host; });
        return (hosts.length ? [phrase + ' (' + hosts.map(function (name) { return 'site:' + name; }).join(' OR ') + ')'] : [])
            .concat([phrase + ' (novel OR manga OR webtoon OR manhwa)']).map(function (query) { return query + exclude; });
    }

    /** How much of `query` is in `title` (0 to 1), word by word: apostrophes dropped ("I'll" is "ill"), a word
     *  matching one that starts like it ("magic", "magical"), the small words not counting. For the web search's
     *  loose pass: a title remembered a little wrong. (generic_search.similar_title, the desktop's.) */
    var TITLE_FILLER = new Set('a an the of to in on and is be i my ill im novel manga manhwa manhua webtoon'.split(' '));
    function titleWords(text) {
        var words = String(text || '').normalize('NFKC').toLowerCase().replace(/['’`]/g, '').match(/[\p{L}\p{N}]+/gu) || [];
        return Array.from(new Set(words.filter(function (word) { return !TITLE_FILLER.has(word); })));
    }
    function titleMatches(query, title) {
        var wanted = titleWords(query), have = titleWords(title);
        if (!wanted.length) return !!exactTitle(query) && exactTitle(title).indexOf(exactTitle(query)) >= 0;
        if (wanted.length === 1 && /[^\x00-\x7f]/.test(wanted[0])) return exactTitle(title).indexOf(wanted[0]) >= 0;
        return wanted.every(function (word) { return have.indexOf(word) >= 0; });
    }
    function similarTitle(query, title) {
        var key = exactTitle(query);
        if (key && key === exactTitle(title)) return 1;
        var wanted = titleWords(query), have = titleWords(title);
        if (!wanted.length || !have.length) return 0;
        var found = wanted.filter(function (word) {
            return have.some(function (other) {
                return other === word || (Math.min(other.length, word.length) >= 4 && other.slice(0, 4) === word.slice(0, 4));
            });
        });
        return found.length / wanted.length;
    }
    function titleQueries(text) {
        text = text.trim().slice(0, 240);
        var words = titleWords(text);
        var compact = (words.length > 4 ? words.slice(0, 2).concat(words.slice(-2)) : words).join(' ');
        return Array.from(new Set([text, compact].filter(Boolean)));
    }
    var DIRECT_TITLE_SITES = ['mangak.io', 'mangadex.org', 'comix.to'];
    function localized(values) {
        if (!values || typeof values !== 'object') return '';
        return values.en || Object.values(values).find(function (value) { return typeof value === 'string'; }) || '';
    }
    function mangakTitles(result) {
        var data = G().parse(result.text()).querySelector('script#__NEXT_DATA__');
        var rows = data ? JSON.parse(data.textContent).props.pageProps.ssrItems : [];
        return (rows || []).slice(0, 40).filter(function (row) {
            return row && typeof row.slug === 'string' && /^[a-z0-9][a-z0-9-]{0,220}$/.test(row.slug);
        }).map(function (row) {
            var stats = row.stats || {};
            return {url: 'https://mangak.io/' + row.slug, title: row.name, description: row.summary,
                    alternateTitles: (row.altNames || []).map(function (alias) { return alias && alias.name; }),
                    cover: G().cleanUrl(row.cover || '', 'https://mangak.io/'), status: row.status,
                    chapters: stats.chaptersCount, views: stats.views, likes: stats.bookmarksCount};
        });
    }
    function mangadexTitles(result) {
        var data = JSON.parse(result.text());
        if (data.result !== 'ok') return [];
        return (data.data || []).slice(0, 40).filter(function (row) {
            return row && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(row.id || '');
        }).map(function (row) {
            var attrs = row.attributes || {}, links = {};
            (row.relationships || []).forEach(function (item) { if (item) links[item.type] = item.attributes || {}; });
            var cover = (links.cover_art || {}).fileName;
            return {url: 'https://mangadex.org/title/' + row.id, title: localized(attrs.title),
                    description: localized(attrs.description), status: attrs.status, author: (links.author || {}).name,
                    alternateTitles: (attrs.altTitles || []).flatMap(function (names) { return Object.values(names || {}); }),
                    cover: cover ? 'https://uploads.mangadex.org/covers/' + row.id + '/' + encodeURIComponent(cover) + '.256.jpg' : ''};
        });
    }
    // Runs in the site's page so its own search client handles the API.
    async function comixSearchRows() {
        if (!/^comix\.(to|ws)$/.test(location.hostname) || location.pathname !== '/browse') return [];
        var until = Date.now() + 6500;
        do {
            var links = [...document.querySelectorAll('a.lrow__title-link[href]')];
            if (links.length) return links.slice(0, 40).filter(function (link) {
                return link.origin === location.origin && /^\/title\/[a-z0-9]+(?:-[^/]+)?\/?$/i.test(link.pathname);
            }).map(function (link) {
                return {url: link.href, title: link.textContent.trim(), alternateTitles: [],
                        cover: link.closest('.lrow')?.querySelector('.lrow__poster img[src]')?.src || ''};
            });
            await new Promise(function (resolve) { setTimeout(resolve, 150); });
        } while (Date.now() < until);
        return [];
    }
    async function comixTitles(query, token) {
        var path = '/browse?order%5Brelevance%5D=desc&keyword=' + encodeURIComponent(query);
        for (var name of ['comix.to', 'comix.ws']) {
            if (stopped(token)) break;
            var response = await transport({op: 'window', url: 'https://' + name + path, script: '(' + comixSearchRows + ')()'});
            if (Array.isArray(response?.value) && response.value.length) return response.value;
        }
        return [];
    }
    async function directTitles(site, text, token) {
        var key = exactTitle(text), matches = new Map();
        for (var query of titleQueries(text)) {
            if (stopped(token)) break;
            var url = site === 'mangak.io' ? 'https://mangak.io/search?q=' + encodeURIComponent(query)
                : 'https://api.mangadex.org/manga?limit=24&includes%5B%5D=cover_art&includes%5B%5D=author&title=' + encodeURIComponent(query);
            try {
                var rows;
                if (site === 'comix.to') {
                    rows = await comixTitles(query, token);
                } else {
                    var result = await fetchUrl(url, {quick: true, timeout: 5000, priority: true, stop: token});
                    if (!result || result.status !== 200) break;
                    rows = site === 'mangak.io' ? mangakTitles(result) : mangadexTitles(result);
                }
                var scored = rows.filter(function (row) { return typeof row.title === 'string' && row.title.trim(); })
                    .map(function (row) {
                        row.alternateTitles = row.alternateTitles.filter(function (name) { return typeof name === 'string' && name.trim(); });
                        var names = [row.title].concat(row.alternateTitles);
                        var score = Math.max.apply(null, names.map(function (name) {
                            return similarTitle(text, name);
                        }));
                        row.alternateTitles = row.alternateTitles.slice(0, 30);
                        return [row, score, names.some(function (name) { return exactTitle(name) === key; })];
                    }).filter(function (item) { return item[1] >= 0.6; });
                scored.forEach(function (item) {
                    var prior = matches.get(item[0].url);
                    if (!prior || item[2] > prior[2] || (item[2] === prior[2] && item[1] > prior[1])) {
                        matches.set(item[0].url, item);
                    }
                });
                if (scored.some(function (item) { return item[1] >= (site === 'comix.to' ? 0.75 : 0.8); })) break;
            } catch (e) { break; }
        }
        return Array.from(matches.values()).sort(function (a, b) { return b[2] - a[2] || b[1] - a[1]; })
            .slice(0, 12).map(function (item) { return item[0]; });
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
        var known = speeds[host] || (SEARCH_PROFILE.measurements || {})[G().siteOf(host)];
        if (known && Date.now() - known.at * (known.at < 1e12 ? 1000 : 1) < (known.ok ? 30 : 1) * 86400000) return known.ok ? known.ms : 1000000;
        var at = (SEARCH_PROFILE.preferred || []).indexOf(G().siteOf(host));
        return at >= 0 ? 6000 + at : 100000;
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
        if (figures.ageRating === 15 || figures.ageRating === 19) out.ageRating = figures.ageRating;  // 19 adults only, 15 teens
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
        // cover_url: the site's card image (a small WebP); novel_img_all is the original upload (10 MB and more).
        var names = ['cover_url', 'novel_thumb_all', 'novel_thumb', 'novel_img', 'novel_img_all'];
        for (var i = 0; i < names.length; i++) {
            var value = String(row[names[i]] || '').trim();
            if (value && value.indexOf('readycover') < 0 && value.indexOf('adult_cover_img') < 0) {
                return value.indexOf('//') === 0 ? 'https:' + value : value.charAt(0) === '/' ? NPIA_IMAGES + value : value;
            }
        }
        return '';
    }
    // The page's sorts (NDB_SORTS) as npia's search columns; its own order is by views.
    var NPIA_SORTS = {views: 'count_view', likes: 'count_good', chapters: 'count_book', 'new': 'start_date',
                      updated: 'last_viewdate', title: 'novel_name'};
    // An age rating, read as npia's signed-in metadata scraper reads it: its search needs a term and has no
    // rating filter, so broad terms are searched and each book's own rating kept (source_search._npia_rated).
    // npia shows 19+ books only to a signed-in, age-verified account; 15+ ones to everyone.
    var NPIA_RATINGS = {r19: 19, r15: 15};
    var NPIA_RATED_TERMS = ['판타지', '현대', '로맨스', '무협', '하렘', '로판', '라이트노벨', '대체역사', '일상', 'SF'];
    var NPIA_RATED_PER_PAGE = 3, NPIA_RATED_PAGES = 25;
    function npiaAsk(searchType, value, page, sort, token) {
        var params = {cmd: 'novel_search', search_type: searchType, search_val: value,
                      page: page + 1, rows: NPIA_ROWS, sort_col: NPIA_SORTS[sort] || 'count_view', novel_type: '', start_count_book: '', end_count_book: '',
                      novel_age: '', start_days: '', novel_genre: '', block_out: 0, block_stop: 0, is_contest: 0, is_complete: '',
                      is_challenge: 0, list_display: 'grid'};
        var ask = function () {
            // Without a browser's User-Agent it answers an empty page.
            return fetchUrl(NPIA + '/proc/novel?' + srcQuery(params), {pool: SRC_POOL, stop: token, referer: NPIA + '/search',
                                                                       headers: {'X-Requested-With': 'XMLHttpRequest', 'User-Agent': SRC_BROWSER}}).then(srcJson);
        };
        return ask().then(function (data) { return data || ask(); });  // it answers 502 now and then
    }
    function npiaRows(data) { return ((data || {}).list || []).filter(function (row) { return row && row.novel_no; }); }
    function npiaTags(row) { return (row.novel_genre_arr || []).map(String).filter(function (t) { return t.trim(); }); }
    function npiaCard(row) {
        return srcCard('npia', NPIA + '/novel/' + row.novel_no, row.novel_name, row.writer_nick, npiaCover(row), npiaTags(row), row.novel_story,
                       {views: row.count_view, likes: row.count_good, chapters: row.count_book,
                        status: ['1', 'True', 'true'].indexOf(String(row.is_complete)) >= 0 ? 'Completed' : 'Ongoing',
                        updated: row.last_viewdate, published: row.start_date,
                        ageRating: Number(row.novel_age) === 19 ? 19 : Number(row.novel_age) === 15 ? 15 : 0});
    }
    /** Page `page` of npia's books rated `rating`: NPIA_RATED_PER_PAGE searches, the terms in turn (the first
     *  page of each, then the second of each, ...), each book kept when its own rating is the one asked. */
    function npiaRated(rating, page, sort, token) {
        var asks = [];
        for (var slot = page * NPIA_RATED_PER_PAGE; slot < (page + 1) * NPIA_RATED_PER_PAGE; slot++) {
            var at = Math.floor(slot / NPIA_RATED_TERMS.length);
            if (at < NPIA_RATED_PAGES) asks.push(npiaAsk('all', NPIA_RATED_TERMS[slot % NPIA_RATED_TERMS.length], at, sort, token));
        }
        return Promise.all(asks).then(function (found) {
            var cards = [], seen = {};
            found.forEach(function (data) {
                npiaRows(data).forEach(function (row) {
                    if (Number(row.novel_age) !== rating || seen[row.novel_no]) return;
                    seen[row.novel_no] = true;
                    cards.push(npiaCard(row));
                });
            });
            var last = (page + 1) * NPIA_RATED_PER_PAGE >= NPIA_RATED_TERMS.length * NPIA_RATED_PAGES;
            // None rated in a page's searches (19+ for an account npia doesn't show them to): no more asked.
            return {cards: cards, more: cards.length > 0 && !last};
        });
    }
    function npiaSearch(mode, query, page, token, sort) {
        var want = query.replace(/^#/, '').toLowerCase();
        if (mode === 'tag' && NPIA_RATINGS[want]) return npiaRated(NPIA_RATINGS[want], page, sort, token);
        return npiaAsk(mode === 'tag' ? 'novel_genre' : 'writer_nick', query.replace(/^#/, ''), page, sort, token).then(function (data) {
            var rows = npiaRows(data);
            var cards = [];
            rows.forEach(function (row) {
                var tags = npiaTags(row);
                // Its matches are any part of a tag or a nickname ("TS" finds "TS있음"): only the exact one.
                if (mode === 'tag' && tags.map(function (t) { return t.toLowerCase(); }).indexOf(want) < 0) return;
                if (mode === 'author' && String(row.writer_nick || '').toLowerCase() !== want) return;
                cards.push(npiaCard(row));
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

    // NovelDB's card of each of `cards` (from NovelDB's own sites) that the index has at the same address
    // (generic_search.noveldb_twins): sent after the site's card, the page merges the two (its English
    // title, tags and rating join it), whether or not NovelDB's own results reached that book yet.
    function noveldbTwins(cards, token) {
        var same = function (url) {
            try { var u = new URL(url); return u.hostname.toLowerCase().replace(/^www\./, '') + u.pathname.replace(/\/+$/, ''); } catch (e) { return ''; }
        };
        var twins = [];
        return each(cards || [], 4, function (card) {
            if (stopped(token) || !card.title) return null;
            var want = same(card.url), author = String(card.author || '').trim();
            // By its author: one short list from one bucket. A title is read in many pieces (a Korean one in a
            // bucket for each two letters), which for a page of cards meant hundreds of buckets read and kept:
            // the app slowed down, and the page ran out of memory.
            var asked = author ? ndbSearch(author, 'author', NDB_TWIN_AUTHOR_BOOKS, 0) : ndbSearch(card.title, 'name', 8, 0);
            return asked.then(function (found) {
                var twin = found.cards.filter(function (other) { return same(other.url) === want; })[0];
                if (twin) twins.push(Object.assign(twin, {verified: true}));
            }, function () {});
        }, token).then(function () { return twins; });
    }
    var NDB_TWIN_AUTHOR_BOOKS = 200;  // an author's books looked through for a site card's NovelDB twin
    /** {source: its site as the cards name it}: a search of picked sites asks only these (source_search.source_sites). */
    function sourceSites() {
        var homes = {npia: NPIA, kpage: KPAGE, sfc: SFC_BOOK, nweb: NWEB, mpia: MPIA, jara: JARA, rbooks: RBOOKS, nseries: NSERIES}, out = {};
        Object.keys(homes).forEach(function (source) { out[source] = G().siteOf(host(homes[source])); });
        return out;
    }
    /** NovelDB's sources of the picked sites; null: all (no pick, or a picked site that is none of NovelDB's). */
    function ndbPickedSources(only) {
        if (!only || !only.length) return null;
        var homes = sourceSites(), sites = Object.keys(homes).map(function (s) { return homes[s]; });
        var mine = Object.keys(homes).filter(function (source) { return only.indexOf(homes[source]) >= 0; });
        return mine.length && only.every(function (site) { return sites.indexOf(site) >= 0; }) ? mine : null;
    }
    var SOURCE_SEARCHERS = {npia: npiaSearch, kpage: kpageSearch, sfc: sfcSearch, nweb: nwebSearch, mpia: mpiaSearch, jara: jaraSearch,
                            rbooks: rbooksSearch, nseries: nseriesSearch};
    // The sites asked for an age rating (R19, R15): their searches say each book's rating (source_search.RATED).
    var SOURCE_RATED = ['npia'];
    function sourceSearch(source, mode, query, page, token, sort) {
        var run = SOURCE_SEARCHERS[source];
        if (!run || (mode !== 'tag' && mode !== 'author') || !String(query || '').trim()) return Promise.resolve({cards: [], more: false});
        return Promise.resolve().then(function () { return run(mode, String(query).trim(), page, token, sort || ''); })
            .catch(function () { return {cards: [], more: false}; });
    }
    // One book by its id: its live figures, for a NovelDB card as it comes into view (source_search.lookup).
    // One request a book (a detail API or page; npia and rbooks have none that reads one book, so their
    // author's list is read and the book picked from it), at most two at a time on each site.
    var SRC_LOOKUP_POOLS = {};
    function lookupPool(source) { return SRC_LOOKUP_POOLS[source] || (SRC_LOOKUP_POOLS[source] = {free: 2, waiting: []}); }
    function lookupByAuthor(source, id, author, token) {
        if (!String(author || '').trim()) return Promise.resolve(null);
        var own = new RegExp('/' + String(id).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:[/?#]|$)');
        return sourceSearch(source, 'author', author, 0, token).then(function (found) {
            return found.cards.filter(function (c) { return own.test(c.url); })[0] || null;
        });
    }
    var SOURCE_LOOKUPS = {
        // Its search by novel number makes its server fail (scripts/update_rankings.py).
        npia: function (id, author, token) { return lookupByAuthor('npia', id, author, token); },
        rbooks: function (id, author, token) { return lookupByAuthor('rbooks', id, author, token); },
        mpia: function (id, author, token) {
            return fetchUrl(MPIA + '/api/v1/pc/novel-detail/' + encodeURIComponent(id), {pool: lookupPool('mpia'), stop: token, headers: {Accept: 'application/json'}})
                .then(function (result) {
                    var info = ((srcJson(result) || {}).result || {}).novelInfo;
                    if (!info || !info.title) return null;
                    var tags = (info.genres || []).concat((info.tags || []).map(function (t) { return t && t.title; }))
                        .filter(function (t, i, all) { return typeof t === 'string' && t && all.indexOf(t) === i; });
                    return srcCard('mpia', MPIA + '/novel/detail/' + id, info.title, info.authorName, info.coverUrl || info.originCoverUrl, tags, info.introduction,
                                   {views: info.viewCount, likes: info.likeCount, chapters: info.chapterCount, status: info.finish ? 'Completed' : 'Ongoing',
                                    updated: info.updatedAt, published: info.createdAt});
                });
        },
        kpage: function (id, author, token) {
            return fetchUrl(KPAGE_API + '/api/v1/content/overview?' + srcQuery({series_id: id}),
                            {pool: lookupPool('kpage'), stop: token, headers: {Accept: 'application/json', Referer: KPAGE + '/', Origin: KPAGE}}).then(function (result) {
                var content = ((srcJson(result) || {}).result || {}).content;
                if (!content || !content.title) return null;
                var found = kpageCard(Object.assign({}, content, {series_id: id})), chapters = srcCount(content.on_sale_count);
                if (chapters) found.chapters = chapters;
                return found;
            });
        },
        jara: function (id, author, token) {
            return Promise.resolve(jaraPublic(token)).then(function (params) {
                if (!params) return null;
                return fetchUrl(JARA_API + '/v1/book/detail.joa?' + srcQuery(Object.assign({}, params, {book_code: id, promotion_code: ''})),
                                {pool: lookupPool('jara'), stop: token}).then(function (result) {
                    var data = srcJson(result), book = data && String(data.status) === '1' ? data.book : null;
                    if (!book || !book.subject) return null;
                    var cover = String(book.book_img || '');
                    return srcCard('jara', JARA + '/book/' + id, book.subject, book.writer_name, cover.indexOf('//') === 0 ? 'https:' + cover : cover,
                                   (book.category_name ? [book.category_name] : []).concat((book.keyword || []).map(String)), book.intro,
                                   {views: book.cnt_page_read, likes: book.cnt_recom, chapters: book.cnt_chapter,
                                    status: ['Y', '1', 'TRUE'].indexOf(String(book.chk_finish).toUpperCase()) >= 0 ? 'Completed' : 'Ongoing',
                                    updated: book.updated, published: book.created});
                });
            });
        },
        sfc: function (id, author, token) {
            return fetchUrl(SFC_API + '/novels/' + encodeURIComponent(id) + '?' + srcQuery({expand: SFC_EXPAND}),
                            {pool: lookupPool('sfc'), stop: token, headers: SFC_HEADERS}).then(function (result) {
                var row = (srcJson(result) || {}).data;
                return row && row.novelId && row.novelName ? sfcCard(row) : null;
            });
        },
        nseries: function (id, author, token) {
            var url = NSERIES + '/novel/detail.series?' + srcQuery({productNo: id});
            return fetchUrl(url, {pool: lookupPool('nseries'), stop: token}).then(function (result) {
                var doc = srcDoc(result), head = doc && doc.querySelector('.end_head h2');
                if (!head || !srcText(head)) return null;
                var info = doc.querySelector('.end_info'), rows = info ? Array.prototype.slice.call(info.querySelectorAll('li')) : [];
                var writer = rows.filter(function (li) { return srcText(li.querySelector('span')) === '글'; })[0];
                var genre = info && info.querySelector("a[href*='genreCode=']"), image = doc.querySelector('.pic_area img');
                var chapters = srcText(doc.body).match(/총\s*(\d+)\s*(?:화|권)/), status = srcText(info && info.querySelector('li.ing'));
                return srcCard('nseries', url, srcText(head), writer ? srcText(writer.querySelector('a')) : '', image ? image.getAttribute('src') : '',
                               genre ? [srcText(genre)] : [], '',
                               {chapters: chapters ? chapters[1] : '', status: status.indexOf('완결') >= 0 ? 'Completed' : status ? 'Ongoing' : ''});
            });
        },
        nweb: function (id, author, token) {
            // Its league's page, by redirect.
            return fetchUrl(NWEB + '/webnovel/list?' + srcQuery({novelId: id}), {pool: lookupPool('nweb'), stop: token}).then(function (result) {
                var doc = srcDoc(result), info = doc && doc.querySelector('.section_area_info');
                if (!info || !srcText(info.querySelector('h2.title'))) return null;
                var genre = info.querySelector('.info_top .info_group .item'), image = info.querySelector('.thumbnail img[src]');
                var tags = (genre && srcText(genre) ? [srcText(genre)] : []).concat(Array.prototype.map.call(
                    doc.querySelectorAll('.end_tag_area .tag_collection a.tag'), function (a) { return srcText(a).replace(/^#/, '').trim(); }));
                return srcCard('nweb', (result && result.url) || NWEB + '/webnovel/list?novelId=' + id, srcText(info.querySelector('h2.title')),
                               Array.prototype.map.call(info.querySelectorAll('a[href*="target=author"]'), srcText).join(', '),
                               image ? image.getAttribute('src') : '', tags.filter(function (t, i, all) { return t && all.indexOf(t) === i; }),
                               srcText(info.querySelector('#summaryText')),
                               {likes: srcText(info.querySelector('._reactionModule ._count')),
                                status: srcText(info.querySelector('.info_group')).indexOf('완결') >= 0 ? 'Completed' : ''});
            });
        }
    };
    /** The book `id` of `source` as its site shows it now (a card), or null when the site doesn't answer. */
    function sourceLookup(source, id, author, token) {
        var run = SOURCE_LOOKUPS[source];
        if (!run || !String(id || '').trim()) return Promise.resolve(null);
        return Promise.resolve().then(function () { return run(String(id).trim(), author || '', token); })
            .catch(function () { return null; });
    }
    /** A NovelDB card with its site's live figures. What NovelDB translated stays: the English title, tags
     *  and synopsis; the site's own title becomes its raw title (generic_search.refreshed). */
    function ndbRefreshed(item, live) {
        if (!live) return item;
        var out = Object.assign({}, item, {fresh: true});
        FIGURES.concat(['status', 'author']).forEach(function (name) { if (live[name]) out[name] = live[name]; });
        if (live.title && live.title !== live.url && (item.originalTitle || live.title !== item.title)) out.originalTitle = live.title;
        if (live.cover && !item.cover) out.cover = live.cover;
        return out;
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

    // A search's sites in stages: the first STAGE_FIRST, then STAGE_MORE each time the list is scrolled to its
    // end; a stage starts no more sites once STAGE_ENOUGH books are in. `staged`: each search's sites not asked yet.
    var STAGE_FIRST = 8, STAGE_MORE = 8, STAGE_ENOUGH = 90, staged = {};
    var EXACT_GRACE_MS = 3000, NDB_HEAD_START_MS = 4000;
    /** A title as an exact match compares it: letters and digits only, in one case and form. */
    function exactTitle(title) { return String(title || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ''); }
    /** Runs `fn` on the queue's items, `limit` at a time, taken from its front as they go (the queue may be
     *  reordered meanwhile), until it is empty, `full()` says to stop, or the token is stopped. */
    function drain(queue, limit, fn, full, token) {
        function worker() {
            if (!queue.length || full() || stopped(token)) return Promise.resolve();
            var item = queue.shift();
            return Promise.resolve().then(function () { return fn(item); })
                .catch(function () { /* one source failing stops nothing */ }).then(worker);
        }
        var workers = [];
        for (var i = 0; i < limit; i++) workers.push(worker());
        return Promise.all(workers);
    }
    /** One site's search for the text (its search page) or the tag (its tag page): its cards offered. */
    function siteAsk(site, mode, text, token, offer, report, enrich, summary) {
        var started = Date.now(), home = 'https://' + site.host + '/';
        var asked = mode === 'name' ? siteSearch(home, text, token) : siteTag(home, text, token);
        // A site gets SITE_SECONDS: past that its slot goes to the next one (it rests a day).
        var late = new Promise(function (resolve) { setTimeout(function () { resolve('late'); }, SITE_SECONDS * 1000); });
        return Promise.race([asked, late]).then(function (hits) {
            if (stopped(token)) return;
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
        }, function () { if (stopped(token)) return; summary.failed++; noteSpeed(site.host, Date.now() - started, false); report(site.site, started, 0, 'failed'); });
    }

    /** `options`: the query's settings switched off, {noveldb: false} (NovelDB's index) or {deep: false}
     *  (NovelDB's sites asked directly). */
    function runSearch(id, text, mode, token, sort, options) {
        options = options || {};
        var useNovelDB = options.noveldb !== false, deep = options.deep !== false, full = options.full === true;
        // The sites picked in the Sites panel: only they are asked, NovelDB for their books, and no web search.
        var only = (options.only || []).filter(function (s) { return typeof s === 'string'; });
        var picked = function (hostName) { return !only.length || only.indexOf(G().siteOf(hostName)) >= 0; };
        var ndbSources = ndbPickedSources(only), homes = sourceSites();
        speedAnswered = false;
        speedFailed = [];
        var seen = {}, summary = {web: 0, details: 0, failed: 0, site: 0}, detailJobs = [];
        // A title search with a book of exactly that title (its own or its original title): no more sites are
        // started, and the search ends EXACT_GRACE_MS later (for the book's other copies already on their way).
        var exactKey = mode === 'name' && !full ? exactTitle(text) : '', exactFound = false, exactSeen = null;
        var exactWait = new Promise(function (resolve) { exactSeen = resolve; });
        function offer(cards) {
            var fresh = cards.filter(function (item) {
                if (mode === 'name' && !item.catalogMatch && ![item.title, item.originalTitle].concat(item.alternateTitles || []).some(function (name) { return titleMatches(text, name); })) return false;
                var k = item.url.replace(/\/+$/, '');
                if (seen[k] || stopped(token) || isNoResults(item.title, String(item.description || '').slice(0, 400))) return false;
                seen[k] = item;
                return true;
            });
            if (fresh.length) call('add', fresh.map(function (c) { return Object.assign({}, c, {via: mode}); }), id);
            if (exactKey && !exactFound && fresh.some(function (c) {
                return [c.title, c.originalTitle].concat(c.alternateTitles || []).some(function (name) { return exactTitle(name) === exactKey; });
            })) {
                exactFound = true;
                exactSeen();
            }
            return fresh;
        }
        function enrich(cards) {
            cards.forEach(function (item) {
                detailJobs.push(details_(item));
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
        var titleSites = mode === 'name' ? DIRECT_TITLE_SITES.filter(function (name) { return siteHosts[name] && picked(name); }) : [];
        var searchedSites = SITES.filter(function (site) {
            return titleSites.indexOf(site.host) < 0 && (full || site.searched !== false) && !(site.lnreader || []).length && picked(site.host)
                && (full || only.length || (!resting(site.host) && speedRank(site.host) < 12000));
        }).sort(function (a, b) { return speedRank(a.host) - speedRank(b.host); });
        if (!only.length) searchedSites = searchedSites.slice(0, full ? 100 : 12);
        var sources = [];
        // The sites in stages (STAGE_FIRST, then STAGE_MORE as the list is scrolled): the quickest first, and
        // first of all the sites the web search found matching books on. A stage stops starting sites once
        // STAGE_ENOUGH books are in; the sites not asked wait for the next stage (`staged`).
        var staging = !only.length && mode !== 'author' && !full;
        var siteQueue = searchedSites.slice(), stageKey = [mode, text].join('\t'), startedSites = 0, askedHosts = {};
        delete staged[stageKey];
        function webFirst(urls) {
            urls.forEach(function (url) {
                var site = siteFor(url);
                if (site && askedHosts[site.host]) return;
                var at = site ? siteQueue.indexOf(site) : -1;
                if (at > 0) siteQueue.unshift(siteQueue.splice(at, 1)[0]);
                else if (site && at < 0 && !(site.lnreader || []).length) { if (siteQueue.length >= 12) siteQueue.pop(); siteQueue.unshift(site); }
            });
        }
        // NovelDB first: the sites start once it has answered, or after NDB_HEAD_START_MS at most (with them all
        // at once its reads waited behind the sites' pages, and a title it has took 15 s and more).
        var ndbAnswered = null, ndbHeadStart = new Promise(function (resolve) { ndbAnswered = resolve; });
        if (!useNovelDB) ndbAnswered();
        setTimeout(function () { ndbAnswered(); }, NDB_HEAD_START_MS);
        if (useNovelDB) sources.unshift(function () {
            var started = Date.now();
            var shown = 0;
            var mark = function (c) { return Object.assign(c, {ms: Date.now() - started, verified: mode === 'tag' || mode === 'author'}); };
            // Each card goes to the page as soon as it is read, not after the whole page of them.
            return ndbSearch(text, mode, NDB_FIRST, 0, function (c) { shown += offer([mark(c)]).length; }, function (total) {
                call('progress', id, {noveldbTotal: total});
            }, sort, ndbSources).then(function (found) {
                shown += offer(found.cards.map(mark)).length;
                report('NovelDB', started, shown);
                ndbAnswered();
            }, function () { summary.failed++; report('NovelDB', started, 0, 'failed'); ndbAnswered(); });
        });
        titleSites.forEach(function (name) {
            sources.push(function () {
                var started = Date.now();
                return directTitles(name, text, token).then(function (hits) {
                    var cards = hits.map(function (hit) {
                        var item = card(hit.url, hit.title, hit.description || '', 'site', hit.cover);
                        return Object.assign(item, {comic: true, verified: true, catalogMatch: [hit.title].concat(hit.alternateTitles || []).some(function (name) { return similarTitle(text, name) >= 0.75; }), ms: Date.now() - started,
                            alternateTitles: hit.alternateTitles, status: hit.status, author: hit.author || '',
                            chapters: hit.chapters, views: hit.views, likes: hit.likes});
                    });
                    var fresh = offer(cards);
                    summary.site += fresh.length;
                    report(name, started, fresh.length);
                });
            });
        });
        var webJobs = [];
        function verifyUnknown(item) {
            var started = Date.now();
            return fetchUrl(item.url, {stop: token, quick: true}).then(function (result) {
                if (stopped(token)) return;
                var final = result && result.url || item.url;
                var metadata = result && result.status === 200 ? G().metadata(G().parse(result.text()), final) : {};
                var valid = looksLikeBook(final, metadata.title) && metadata.title && (mode !== 'name' || titleMatches(text, metadata.title));
                noteSpeed(host(item.url), Date.now() - started, !!valid);
                if (valid) {
                    var fresh = offer([Object.assign({}, item, metadata, {url: final, complete: true, verified: mode === 'name', ms: Date.now() - started})]);
                    summary.web += fresh.length;
                }
            });
        }
        function addWebSearch(query, loose) {
            webJobs.push(function () {
                var started = Date.now(), found = 0;
                return webSearch(query, token, function (hits) {
                    hits = hits.filter(function (hit) {
                        var site = siteFor(hit.url);
                        var listed = site && (site.hosts || [site.host]).some(function (name) { return host(hit.url) === name || host(hit.url) === 'm.' + name; });
                        return /^https?:\/\//.test(hit.url) && looksLikeBook(hit.url, hit.title) && (listed || knownSite(hit.url));
                    });
                    webFirst(hits.map(function (hit) { return hit.url; }));
                    hits.sort(function (a, b) { return speedRank(host(a.url)) - speedRank(host(b.url)); });
                    var cards = hits.filter(function (hit) {
                        return !loose || titleMatches(text, cleanTitle(hit.title, hit.url));
                    }).map(function (hit) {
                        return Object.assign(card(hit.url, hit.title, hit.snippet, 'web'), {ms: Date.now() - started});
                    });
                    cards = cards.filter(function (item) {
                        var score = speedRank(host(item.url));
                        if (score >= 1000000) return false;
                        if (score >= 12000) { detailJobs.push(verifyUnknown(item)); return false; }
                        return true;
                    });
                    var fresh = offer(cards);
                    found += fresh.length;
                    enrich(fresh);
                }, function () { summary.googleBlocked = true; call('progress', id, {googleBlocked: true}); }).then(function () { summary.web += found; report('Google', started, found); });
            });
        }
        if (!only.length && mode !== 'author') {
            googleDorks(text, mode).forEach(function (query) { addWebSearch(query, mode === 'name'); });
        }
        // An author's books: NovelDB and its own sites know them by author; the other sites' searches are by title.
        // An age rating (R19, R15) is NovelDB's own tag: the index's rated books, the site's own rule (sites asked
        // for it, or for what NovelDB lists beside it, "어른" or "Adult", give books of every rating); of the
        // sites only those that say each book's rating are asked (npia, live, with the user's login).
        var ratingOnly = mode === 'tag' && !!NDB_AGE_TAGS[ndbNormal(text).trim()];
        if (mode === 'author' || ratingOnly) sources = sources.slice(0, useNovelDB ? 1 : 0);
        // NovelDB's own sites, asked directly: a tag under each site's own name for it, or the author.
        var asks = !deep ? Promise.resolve({}) : mode === 'author' ? Promise.resolve(Object.keys(SOURCE_SEARCHERS).reduce(function (all, s) { all[s] = [text]; return all; }, {}))
            : ratingOnly ? Promise.resolve(SOURCE_RATED.reduce(function (all, s) { all[s] = [text]; return all; }, {}))
            : mode === 'tag' ? ndbNativeTags(text).then(function (natives) { return sourceTagQueries(text, natives); }) : Promise.resolve({});
        var direct = asks.then(function (found) {
            var jobs = [];
            Object.keys(found).forEach(function (source) {
                if (only.length && only.indexOf(homes[source]) < 0) return;
                found[source].forEach(function (name) {
                    jobs.push(function () {
                        var started = Date.now();
                        return sourceSearch(source, mode, name, 0, token, '').then(function (result) {
                            var key = [source, mode, text, name].join('\t');
                            if (result.more) srcNext[key] = 1; else delete srcNext[key];
                            var ms = Date.now() - started;
                            summary.site += result.cards.length;
                            var fresh = offer(result.cards.map(function (c) { return Object.assign(c, {ms: ms}); }));
                            report(source, started, fresh.length);
                            return (useNovelDB ? noveldbTwins(fresh, token) : Promise.resolve([])).then(function (twins) {
                                // The same addresses as cards already sent: the page merges them in.
                                if (twins.length && !stopped(token)) call('add', twins.map(function (c) { return Object.assign(c, {ms: ms, via: mode}); }), id);
                            });
                        });
                    });
                });
            });
            call('progress', id, {total: jobs.length});
            return each(jobs, 8, function (job) { return job(); }, token);
        });
        if (mode === 'author' || ratingOnly) siteQueue = [];
        var stageSize = staging ? STAGE_FIRST : siteQueue.length;
        call('progress', id, {total: sources.length + stageSize});
        var enough = function () { return exactFound || (staging && Object.keys(seen).length >= STAGE_ENOUGH); };
        var webRun = each(webJobs, 2, function (job) { return job(); }, token);
        var sitesRun = Promise.all([ndbHeadStart, within(webRun, 2000)]).then(function () { return drain(siteQueue, 3, function (site) {
            startedSites++;
            askedHosts[site.host] = true;
            return siteAsk(site, mode, text, token, offer, report, enrich, summary);
        }, function () { return startedSites >= stageSize || enough(); }, token).then(function () {
            // The sites not asked: the next stage, as the list is scrolled (and the progress counts only those asked).
            if (staging && siteQueue.length) staged[stageKey] = siteQueue;
            if (stageSize > startedSites) call('progress', id, {total: startedSites - stageSize});
        }); });
        var all = Promise.all([direct, webRun, each(sources, 4, function (source) { return source(); }, token), sitesRun]).then(function () {
            return Promise.all(detailJobs);
        });
        var ended = Promise.race([all, exactWait.then(function () {
            return new Promise(function (resolve) { setTimeout(resolve, EXACT_GRACE_MS); });
        })]);
        return within(ended, full ? 120000 : 45000, token).then(function (how) {
            summary.results = Object.keys(seen).length;
            summary.timed_out = how === 'late';
            return summary;
        });
    }

    /** The book with this ID on every site that addresses its books by ID. */
    function runById(id, text, token, options) {
        var code = text.trim(), numeric = /^\d+$/.test(code), tries = [], seenSites = {}, found = 0;
        ID_TEMPLATES.forEach(function (entry) {
            if ((entry.id === 'number') === numeric) {
                tries.push([entry.site, entry.template]);
                seenSites[G().siteOf(host(entry.template.replace('{id}', '1')))] = true;
            }
        });
        if (numeric) Object.keys(learned).forEach(function (site) { if (!seenSites[site]) tries.push([site, learned[site]]); });
        var only = (options && options.only) || [];
        tries = tries.filter(function (entry) { return only.length ? only.indexOf(G().siteOf(host(entry[1]))) >= 0 : speedRank(host(entry[1])) < 12000; })
            .sort(function (a, b) { return speedRank(host(a[1])) - speedRank(host(b[1])); });
        if (!only.length) tries = tries.slice(0, 12);
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
        sites = sites.filter(function (site) { return only && only.length || speedRank(site.host) < 12000; }).sort(function (a, b) { return speedRank(a.host) - speedRank(b.host); }).slice(0, only && only.length ? 50 : 12);  // the busiest 80 of the kind (with the LNReader sites the page reads, ~180)
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
        // Done after 40 s, but the sites still waiting go on (not stopped, as a search's are): their books come in
        // as they answer, for the list's next rows (the manga list ended after a page or two).
        return within(all, 40000).then(function () { return {results: Object.keys(seen).length}; });
    }

    function search(id, queries) {
        var token = newToken();
        ready.then(function () {
            var totals = {};
            return Promise.all(queries.slice(0, 6).map(function (item) {
                var mode = item.mode === 'tag' || item.mode === 'author' ? item.mode : 'name';
                var run = item.mode === 'id' ? runById(id, item.query, token, item) : runSearch(id, item.query, mode, token, item.sort || '', item);
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
        // A search that ran its time stopped its token: the next results need one that is not stopped.
        var token = current.token && !current.token.stopped ? current.token : newToken();
        var state = {more: false, ndbPage: ndbPage, ndbMore: false, ndbOnly: !sites};
        ready.then(function () {
            return Promise.all(queries.filter(function (q) { return q.mode !== 'id'; }).slice(0, 6).map(function (item) {
                var mode = item.mode === 'tag' || item.mode === 'author' ? item.mode : 'name', kind = mode === 'tag' ? 'tag' : 'search', text = item.query, seen = {};
                function offer(cards) {
                    var fresh = cards.filter(function (c) { var k = c.url.replace(/\/+$/, ''); if (seen[k]) return false; seen[k] = true; return true; });
                    if (fresh.length && !stopped(token)) call('add', fresh.map(function (c) { return Object.assign({}, c, {via: mode}); }), id);
                    return fresh;
                }
                var useNovelDB = item.noveldb !== false, deep = item.deep !== false;
                var only = (item.only || []).filter(function (s) { return typeof s === 'string'; });
                var jobs = ndbPage >= 1 && useNovelDB ? [ndbSearch(text, mode, NDB_MORE, NDB_FIRST + (ndbPage - 1) * NDB_MORE, null, null, item.sort || '', ndbPickedSources(only)).then(function (found) {
                    offer(found.cards.map(function (c) { return Object.assign(c, {verified: mode !== 'name'}); }));
                    if (found.more) { state.more = true; state.ndbMore = true; }
                }, function () {})] : [];
                if (sites && deep && (mode === 'tag' || mode === 'author')) Object.keys(srcNext).forEach(function (key) {
                    var parts = key.split('\t'), at = srcNext[key];
                    if (parts[1] !== mode || parts[2] !== text) return;
                    jobs.push(sourceSearch(parts[0], mode, parts[3], at, token, '').then(function (result) {
                        if (result.more) { srcNext[key] = at + 1; state.more = true; } else delete srcNext[key];
                        var fresh = offer(result.cards);
                        return (useNovelDB ? noveldbTwins(fresh, token) : Promise.resolve([])).then(function (twins) {
                            if (twins.length && !stopped(token)) call('add', twins.map(function (c) { return Object.assign(c, {via: mode}); }), id);
                        });
                    }));
                });
                // The next stage of the search's sites (those its first stages did not ask).
                var waiting = staged[[mode, text].join('\t')];
                if (sites && waiting && waiting.length) {
                    var stage = waiting.splice(0, STAGE_MORE), noop = function () {};
                    if (!waiting.length) delete staged[[mode, text].join('\t')];
                    var stageSummary = {site: 0, failed: 0};
                    var stageReport = function (site, started, found, how) {
                        call('progress', id, {site: site, ms: Date.now() - started, found: found, state: how || 'ok'});
                    };
                    call('progress', id, {total: stage.length});
                    jobs.push(each(stage, 3, function (site) {
                        return siteAsk(site, mode, text, token, offer, stageReport, noop, stageSummary);
                    }, token).then(function () { if (staged[[mode, text].join('\t')]) state.more = true; }));
                }
                if (sites) SITES.forEach(function (site) {
                    if (site.searched === false || (only.length && only.indexOf(G().siteOf(site.host)) < 0)) return;
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

    var IMAGE_SLOTS = {free: 6, waiting: []};
    function image(id, url, referer) {
        var held = pages.cache[url];
        if (held) {
            call('fetched', id, {dataUrl: held, bytes: Math.round(held.length * 0.75)});
            return;
        }
        // Slots of their own and longer to answer: behind a search's and a chapter's own requests (15 s each)
        // pages timed out and showed as missing. A refused one is asked again with the site's home as Referer.
        var ask = function (from) {
            return fetchUrl(url, {binary: true, referer: from, pool: IMAGE_SLOTS, timeout: 30000, headers: {Accept: 'image/*'}});
        };
        if (/^https?:\/\/(?:www\.)?comix\.(?:to|ws)\/title\//i.test(referer || '')) referer = '';
        ask(referer).then(function (result) {
            var home = (/^https?:\/\/[^\/]+\//.exec(referer || '') || [])[0];
            if (result && (result.status === 401 || result.status === 403) && home && home !== referer) return ask(home);
            return result;
        }).then(function (result) {
            if (!result || result.status >= 400 || !result.bytes.length) { call('fetched', id, null); return; }
            var binary = '';
            for (var i = 0; i < result.bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, result.bytes.subarray(i, i + 0x8000));
            var encoded = btoa(binary);
            var kind = (/content-type:\s*(image\/[\w.+-]+)/i.exec(result.headers) || [])[1] || imageKind(encoded) || 'image/jpeg';
            call('fetched', id, {dataUrl: 'data:' + kind + ';base64,' + encoded, bytes: result.bytes.length});
        });
    }

    // -- the reader --------------------------------------------------------------------------------

    var BLOCKED = 'This page is blocked: the site may need you to sign in, or to buy the book. Sign in with Login '
        + '(the app keeps your login), then try again.';
    var reading = {id: null, book: null};
    var readerSession = Date.now().toString(36);
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
    // fanfox and mangahere: the phone's web view is sent to their mobile site, whose chapters say "licensed",
    // so HaruNeko's chapter window finds no pages there; the generic engine reads the desktop pages (the app
    // leaves them to it too: SourceCatalog.genericFirst). webtoons.com sends a phone to its mobile site as well.
    var GENERIC_FIRST = /(^|\.)(fanfox\.net|mangahere\.cc|mangahere\.com|webtoons\.com|rawkuma\.net)$/i;
    // Asura's comics too (its novels are lightnovel-crawler's, which read a comic as a novel).
    function genericFirst(url) {
        if (GENERIC_FIRST.test(host(url))) return true;
        return /^(www\.)?(asurascans\.com|asuracomic\.net)$/i.test(host(url)) && /^https?:\/\/[^\/]+\/(comics|series)\//i.test(url);
    }
    function engineFor(url) {
        if (genericFirst(url)) return ['app', 'generic', 'engines'];
        var engines = ['app', 'engines'];
        if (harunekoListed(url)) engines.push('haruneko');
        engines.push('generic');
        return engines;
    }
    function builtInComic(url) {
        return ['comix.to', 'comix.ws', 'mangak.io'].includes(host(url));
    }
    function parseJson(text) {
        var value = typeof text === 'string' ? JSON.parse(text) : text;
        if (!value || value.error) throw new Error((value && value.error) || 'no answer');
        return value;
    }
    // -- Library files in the reader (the app hands over their bytes) ----------------
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
    function bookFileBytes(url) {
        return appAsk({op: 'file', url: url}).then(function (reply) {
            if (!reply || reply.error || !reply.responseBase64) throw new Error((reply && reply.error) || 'the book could not be opened');
            return bytesOf(reply.responseBase64);
        });
    }
    var epubs = new Map();
    /** The EPUB at a file: address: its spine (reading order), with names from its table of contents. */
    function epubOpen(url) {
        if (epubs.has(url)) return epubs.get(url);
        var opened = bookFileBytes(url).then(function (bytes) {
            var epub = {bytes: bytes};
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
        opened.catch(function () { if (epubs.get(url) === opened) epubs.delete(url); });
        if (epubs.size > 2) epubs.delete(epubs.keys().next().value);  // a whole book each: few are kept
        return opened;
    }
    function epubBook(url) {
        epubs.delete(url);  // A Library update can replace the file at the same address.
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

    function txtListedChapters(text) {
        var lines = text.split('\n'), trimmed = lines.map(function (line) { return line.trim(); });
        var marker = trimmed.slice(0, 20).findIndex(function (line) {
            return /^(?:table of contents|contents|chapter list)\s*:?$/i.test(line);
        });
        if (marker < 0) return [];
        var start = marker + 1;
        while (start < lines.length && !trimmed[start]) start++;
        var stop = start;
        while (stop < lines.length && trimmed[stop]) stop++;
        var headings = trimmed.slice(start, stop);
        if (!headings.length || stop === lines.length) return [];
        var positions = [], offset = stop + 1;
        for (var heading of headings) {
            var position = trimmed.indexOf(heading, offset);
            if (position < 0) return [];
            positions.push(position);
            offset = position + 1;
        }
        return positions.map(function (position, i) {
            return {name: headings[i], text: lines.slice(position + 1, positions[i + 1]).join('\n').replace(/^\n+|\n+$/g, '')};
        });
    }

    /** Read the same chapter separators as the desktop TXT reader. */
    function txtBook(url) {
        return bookFileBytes(url).then(function (bytes) {
            var encoding = bytes[0] === 255 && bytes[1] === 254 ? 'utf-16le'
                : bytes[0] === 254 && bytes[1] === 255 ? 'utf-16be' : 'utf-8';
            var text = new TextDecoder(encoding).decode(bytes).replace(/\r\n?/g, '\n').replace(/^\n+|\n+$/g, '');
            var path = new URL(url).pathname;
            var title = decodeURIComponent(path.split('/').pop()).replace(/\.txt$/i, '').replace(/[_\s]+/g, ' ').trim();
            var author = '', chapters;
            var rules = Array.from(text.matchAll(/^([─━]{8,})[ \t]*\n([^\n]+)\n\1[ \t]*(?:\n|$)/gm));
            if (rules.length) {
                var front = text.slice(0, rules[0].index);
                var header = /^([^\n]+)\nAuthor:[ \t]*([^\n]*)\n={8,}\s*$/.exec(front);
                if (header) { title = header[1].trim(); author = header[2].trim(); }
                chapters = rules.map(function (rule, i) {
                    var stop = rules[i + 1] ? rules[i + 1].index : text.length;
                    return {name: rule[2].trim(), text: text.slice(rule.index + rule[0].length, stop).replace(/^\n+|\n+$/g, '')};
                });
                if (!header && front.trim()) chapters.unshift({name: 'Introduction', text: front.replace(/\n+$/, '')});
            } else if (!(chapters = txtListedChapters(text)).length) {
                var blocks = text.split(/\n(?:[ \t]*\n){2,}/);
                var headings = blocks.map(function (block) { return /^([^\n]{1,180})\n[ \t]*\n([\s\S]+)$/.exec(block); });
                if (blocks.length > 1 && headings.slice(1).every(Boolean)) {
                    chapters = blocks.map(function (block, i) {
                        var heading = headings[i];
                        return heading ? {name: heading[1].trim(), text: heading[2]} : {name: 'Introduction', text: block};
                    });
                    if (!headings[0] && blocks[0].trim() === title) chapters.shift();
                } else {
                    chapters = text.trim() ? [{name: title, text: text}] : [];
                }
            }
            if (!chapters.length) throw new Error('the book has no chapters');
            books.set(url, {engine: 'txt', data: {bookname: title, author: author, chapters: chapters}});
            return {title: title, author: author, engine: 'TXT',
                    chapters: chapters.map(function (chapter) { return {name: chapter.name, section: ''}; })};
        });
    }

    function readBook(url, id) {
        if (/^file:/i.test(url)) return /\.txt$/i.test(new URL(url).pathname) ? txtBook(url) : epubBook(url);
        var engines = engineFor(url), errors = [];
        var published = 0;
        var session = id == null ? null : readerSession + '-' + id;
        var active = function () { return id == null || reading.book === id; };
        function names(chapters, start) {
            return chapters.map(function (c, i) { return {name: c.name || 'Chapter ' + (start + i + 1), section: c.sectionName || ''}; });
        }
        function remember(name, data) {
            books.delete(url);
            books.set(url, {engine: name, data: data});
            if (books.size > 6) books.delete(books.keys().next().value);
        }
        function publish(name, data) {
            if (!active()) return;
            var chapters = data.chapters;
            remember(name, data);
            call('readerChapters', id, names(chapters.slice(published), published));
            published = chapters.length;
        }
        async function builtIn() {
            var data = await appAsk({op: 'book', url: url, reader: session, page: 0});
            if (session == null || !data || typeof data.more !== 'boolean') return data;
            var chapters = [];
            for (var page = 1; active(); page++) {
                data = parseJson(data);
                if (data.chapters.length) {
                    chapters = chapters.concat(data.chapters);
                    publish('app', {chapters: chapters, reader: session});
                }
                if (!data.more) return {chapters: chapters, reader: session};
                data = await appAsk({op: 'book', url: url, reader: session, page: page});
            }
            throw new Error('the reader was closed');
        }
        function next() {
            if (!active()) return Promise.reject(new Error('the reader was closed'));
            if (!engines.length) return Promise.reject(new Error(errors.join('; ') || 'no engine could read the book'));
            var name = engines.shift();
            var start = name === 'app' || name === 'engines'
                ? (note(name === 'app' ? 'Reading the book with the built-in scraper for its site…'
                        : 'Reading the book with the app’s site engines (gallery-dl, HaruNeko, …)…'),
                   (name === 'app' ? builtIn() : appAsk({op: 'book', url: url, engines: true})).then(function (reply) {
                    if (reply && reply.skip) { name = ''; throw new Error('skip'); }
                    return reply;
                }))
                : name === 'haruneko'
                ? (note('Loading the HaruNeko websites…'), loadScript('haruneko-lib.js', harunekoReady).then(function (ok) {
                    if (!ok) throw new Error('the HaruNeko websites did not load');
                    note('Reading the book with HaruNeko…');
                    return window.__HN_parseBook(url);
                }))
                : (note('Reading the chapter list…'), Promise.resolve(window.__GEN_parseBook(url, id == null ? null : function (chapters) {
                    publish('generic', {chapters: chapters});
                }, active)));
            return start.then(parseJson).then(function (data) {
                if (!active()) throw new Error('the reader was closed');
                if (!(data.chapters || []).length) throw new Error('no chapters found');
                remember(name, data);
                return {title: data.bookname || '', author: data.author || '', engine: data.ruleName || data.engine || name,
                        chapters: names(data.chapters, 0)};
            }).catch(function (error) {
                if (name) errors.push(name + ': ' + ((error && error.message) || error));
                // A site the app's scraper owns but could not open (a sign-in, a lock): its message stands.
                if ((name === 'app' && !builtInComic(url)) || published) return Promise.reject(error);
                return next();
            });
        }
        return next();
    }
    function readChapter(url, index, retryImages) {
        var known = books.get(url);
        var opened = known ? Promise.resolve() : readBook(url);
        return opened.then(async function () {
            known = books.get(url);
            if (known.engine === 'epub') return epubChapter(url, index).then(function (result) {
                return {name: result.chapterName || known.data.chapters[index].name, html: result.contentHtml, text: ''};
            });
            var chapters = known.data.chapters;
            if (index < 0 || index >= chapters.length) throw new Error('no such chapter');
            var info = chapters[index];
            if (known.engine === 'txt') {
                var body = document.createElement('div');
                body.style.whiteSpace = 'pre-wrap';
                body.style.overflowWrap = 'anywhere';
                body.textContent = info.text;
                return {name: info.name, html: body.outerHTML, text: info.text};
            }
            note('Scraping ' + (info.name || 'the chapter') + '…');
            var item = Object.assign({bookUrl: url}, info);
            if (item.url) {
                try { item.url = new URL(item.url, url).href; } catch (error) { item.url = ''; }
            }
            var haruneko = known.engine === 'haruneko';
            var siteReader = known.engine === 'app' && builtInComic(item.url);
            var alternate = item.url && (haruneko || known.engine === 'engines' || siteReader);
            var result;
            if (alternate && !retryImages && !siteReader) {
                var listed = JSON.parse(await window.__GEN_parseChapter(item, true));
                if (!listed.error) result = listed;
            }
            if (!result) {
                var watch = haruneko ? watchPages(reading.id) : null;
                try {
                    var reply = known.engine === 'app' ? await appAsk({op: 'chapter', url: url, index: index, reader: known.data.reader})
                        : known.engine === 'engines' ? await appAsk({op: 'chapter', url: url, index: index, engines: true})
                        : haruneko ? await window.__HN_parseChapter(item) : await window.__GEN_parseChapter(item);
                    try {
                        result = parseJson(reply);
                    } catch (error) {
                        if (!alternate || retryImages || (reply && reply.locked)) throw error;
                        note('Chapter engine failed; trying Generic fallback for the same chapter…');
                        try { result = parseJson(await window.__GEN_parseChapter(item)); }
                        catch (fallbackError) { throw siteReader ? error : fallbackError; }
                        if ((haruneko || siteReader) && !result.comic) throw error;
                    }
                } finally {
                    if (watch && pages.watching === watch) pages.watching = null;
                }
            }
            // Cache original bytes from the engine for pages that cannot be fetched by their URLs alone.
            (result.images || []).forEach(function (picture) {
                if (picture.url && picture.data) pages.cache[picture.url] = picture.data;
            });
            return {name: result.chapterName || info.name || '', html: result.contentHtml || '', text: result.contentText || '',
                    comic: !!(result.comic || (result.images || []).length) && !result.contentText,
                    imageReferer: result.imageReferer || item.url || url, imageFallback: !!result.imageFallback};
        });
    }
    // A HaruNeko chapter's pages, shown as each one arrives (the bundle hands them over only at the end).
    var pages = {watching: null, cache: {}};
    function watchPages(id) {
        pages.cache = {};
        return pages.watching = {id: id, images: []};
    }
    function imageKind(base64) {
        var head;
        try { head = atob(String(base64 || '').slice(0, 32)); } catch (e) { return ''; }
        if (head.indexOf('\xff\xd8\xff') === 0) return 'image/jpeg';
        if (head.indexOf('\x89PNG') === 0) return 'image/png';
        if (/^GIF8[79]a/.test(head)) return 'image/gif';
        if (head.slice(0, 4) === 'RIFF' && head.slice(8, 12) === 'WEBP') return 'image/webp';
        if (/^ftypavi[fs]/.test(head.slice(4, 12))) return 'image/avif';
        return '';
    }
    function pageArrived(url, reply) {
        var watch = pages.watching;
        if (!watch || !reply || !reply.responseBase64) return;
        // By the bytes' signature too: sites send pages as application/octet-stream (a scrambled page, decoded
        // by the engine later, matches none and comes with the chapter).
        var kind = (/content-type:\s*(image\/[\w.+-]+)/i.exec(reply.responseHeaders || '') || [])[1] || imageKind(reply.responseBase64);
        if (!kind || pages.cache[url]) return;
        pages.cache[url] = 'data:' + kind + ';base64,' + reply.responseBase64;
        watch.images.push(url);
        call('readerImages', watch.id, {images: watch.images.slice(), total: watch.images.length});
    }

    function read(id, work, url) {
        reading.id = id;
        work().then(function (value) {
            call('fetched', id, value);
        }, function (error) {
            var message = String((error && error.message) || error).slice(0, 300);
            var siteReader = builtInComic(url);
            var blocked = !siteReader || /human verification|http 403|sign in|locked/i.test(message);
            call('fetched', id, {error: message, blocked: blocked, message: siteReader ? message : BLOCKED});
        }).then(function () { if (reading.id === id) reading.id = null; });
    }

    // -- the page's messages -----------------------------------------------------------------------

    function init() {
        ready.then(function () {
            var lnreader = [];
            SITES.forEach(function (site) {
                (site.lnreader || []).slice(0, 1).forEach(function (plugin) { lnreader.push({id: plugin, site: site.site, comic: site.comic}); });
            });
            call('init', {sites: SITES.map(function (s) { return s.site; }), comic: SITES.filter(function (s) { return s.comic; }).map(function (s) { return s.site; }), lnreader: lnreader, searched: 12, responseProfile: SEARCH_PROFILE, instantPiecesComplete: true, downloadIcon: window.NovelDBDownloadIcon || ''});
        });
    }
    // A web page, or a readable file in the Library.
    function readable(url) { return /^https?:/.test(url || '') || /^file:.*\.(?:epub|txt)$/i.test(url || ''); }
    function handle(message) {
        switch (message.type) {
        case 'genericReady': init(); break;
        case 'genericPlugins':
            loadScript('lnreader-lib.js', function () { return !!window.__LNR_search; }).then(function (ok) { call('fetched', message.id, {ok: ok}); });
            break;
        case 'genericSearch': search(message.id || 0, message.queries || [{query: message.query, mode: message.mode}]); break;
        case 'genericInstant':
            if (!/^(manifest|top|[bc]\/[0-9]{1,6})$/.test(message.key || '')) break;
            fetch('instant/' + message.key + '.bin').then(function (response) {
                if (!response.ok) throw new Error('Local catalogue unavailable');
                return response.arrayBuffer();
            }).then(function (bytes) { return gunzip(new Uint8Array(bytes)); }).then(function (text) {
                call('fetched', message.id, {data: JSON.parse(text)});
            }).catch(function () { call('fetched', message.id, {data: null}); });
            break;
        case 'genericSiteQuery':
            ready.then(async function () {
                var token = current.token && !current.token.stopped ? current.token : {stopped: false}, cards = [];
                if (message.web && message.query) {
                    var blocked = false;
                    await webSearch(String(message.query).slice(0, 2000), token, function (hits) { cards = hits; }, function () { blocked = true; });
                    call('fetched', message.id, {cards: cards, googleBlocked: blocked});
                    return;
                } else if (message.url) {
                    var result = await fetchUrl(message.url, {stop: token, quick: true});
                    if (result && result.status === 200) {
                        var url = result.url || message.url, metadata = G().metadata(G().parse(result.text()), url);
                        if (metadata.title && looksLikeBook(url, metadata.title) && !isNoResults(metadata.title)) {
                            var found = Object.assign(card(url, metadata.title, metadata.description, 'site', metadata.cover), metadata, {complete: true});
                            if (!siteFor(url)) found.comic = null;
                            cards = [found];
                        }
                    }
                } else if (/^[a-z0-9.-]+\.[a-z]{2,}$/.test(message.host || '') && message.query) {
                    var site = siteFor('https://' + message.host + '/');
                    var hits = await siteSearch('https://' + (site && site.host || message.host) + '/', String(message.query).slice(0, 300), token);
                    cards = hits.filter(function (hit) { return looksLikeBook(hit.url, hit.title); }).slice(0, 20).map(function (hit) {
                        var found = card(hit.url, hit.title, hit.snippet, 'site', hit.cover);
                        if (!site) found.comic = null;
                        return found;
                    });
                }
                call('fetched', message.id, {cards: cards});
            }).catch(function () { call('fetched', message.id, {cards: []}); });
            break;
        case 'genericIndexSort':
            ready.then(function () {
                return Promise.all((message.queries || []).filter(function (item) { return item.noveldb !== false && item.mode !== 'id'; }).slice(0, 6).map(function (item) {
                    return ndbSearch(item.query, item.mode, NDB_FIRST, 0, null, null, item.sort || '', ndbPickedSources(item.only || []));
                }));
            }).then(function (results) {
                var cards = [];
                results.forEach(function (result, index) {
                    var query = message.queries.filter(function (item) { return item.noveldb !== false && item.mode !== 'id'; })[index];
                    cards = cards.concat(result.cards.map(function (card) { return Object.assign(card, {via: query.mode}); }));
                });
                call('fetched', message.id, {cards: cards, more: results.some(function (result) { return result.more; })});
            }).catch(function () { call('fetched', message.id, {error: 'The index could not be sorted'}); });
            break;
        case 'genericStop': if (current.token) current.token.stopped = true; break;
        // The reader closed: the book's pages still arriving are not handed to it any more.
        case 'genericReaderStop': pages.watching = null; reading.id = null; reading.book = null; break;
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
        case 'genericLibraryLive':
            appAsk(Object.assign({}, message, {op: 'libraryLive'})).then(function (reply) { call('fetched', message.id, reply); });
            break;
        case 'genericBook':
            if (readable(message.url)) {
                reading.book = message.id;
                read(message.id, function () { return readBook(message.url, message.id); }, message.url);
            }
            else call('fetched', message.id, {error: 'only web pages and Library EPUB or TXT files can be read here'});
            break;
        case 'genericChapter':
            if (readable(message.url)) read(message.id, function () { return readChapter(message.url, message.index | 0, message.recovery === 'images'); }, message.url);
            else call('fetched', message.id, {error: 'only web pages and Library EPUB or TXT files can be read here'});
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
    window.__noveldb_engine_transport = async function (details) {
        var watch = pages.watching;
        var readerGet = watch && details && String(details.method || 'GET').toUpperCase() === 'GET';
        var request = readerGet ? Object.assign({}, details, {timeout: 15000}) : details;
        var reply = await transport(request);
        for (var attempt = 2; readerGet && pages.watching === watch && attempt <= 3; attempt++) {
            if (!reply || !([408, 504].includes(Number(reply.status)) || /timeout|timed out|connection reset/i.test(reply.error || ''))) break;
            call('readerStatus', watch.id, 'Reader request timed out; retrying (' + attempt + '/3)…');
            reply = await transport(request);
        }
        if (watch && pages.watching === watch && details && details.url) pageArrived(details.url, reply);
        return reply;
    };
    window.__noveldb_window_script = function (request) { return transport(Object.assign({}, request, {op: 'window'})); };
    // NovelDB's index made ready before the first search: its table of contents, and one small piece read for
    // the signed address and an open connection (a cold first read took 20 s and more on a phone).
    ready.then(function () {
        setTimeout(function () {
            ndbManifest().then(function (manifest) { if (manifest) ndbPiece(manifest, 'bucket', 0); }).catch(function () {});
        }, 1500);
    });
    window.NovelDBCatalogApp = {plan: plan, extractCards: extractCards, looksLikeBook: looksLikeBook, relevant: relevant,
                                cleanTitle: cleanTitle, cardShape: cardShape, ndbSearch: ndbSearch, ndbDescription: ndbDescription};
})();
