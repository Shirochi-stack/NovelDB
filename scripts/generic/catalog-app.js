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
    var slots = {free: 16, waiting: []};
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
        var pool = options.priority ? {free: 1, waiting: []} : slots;
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
    var ndb = {manifest: null, pieces: new Map(), loading: null};

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
        if (kept && kept._read && Date.now() - kept._read < NDB_HOURS * 3600e3) { ndb.manifest = kept; return Promise.resolve(kept); }
        if (ndb.loading) return ndb.loading;
        ndb.loading = fetchUrl(NDB_INDEX + 'noveldb-search.json', {binary: true, priority: true}).then(function (result) {
            ndb.loading = null;
            if (!result || result.status >= 400) return ndb.manifest;
            var found;
            try { found = JSON.parse(new TextDecoder().decode(result.bytes)); } catch (e) { return ndb.manifest; }
            if (found.format !== 1) return null;  // an index for a newer app
            found._read = Date.now();
            ndb.manifest = found;
            ndb.pieces.clear();
            store(NDB_KEY, found);
            return found;
        });
        return ndb.loading;
    }
    function ndbPiece(manifest, kind, index) {
        var key = manifest.built + '|' + kind + '|' + index;
        if (ndb.pieces.has(key)) return ndb.pieces.get(key);
        var at = (kind === 'bucket' ? manifest.bucketAt : manifest.blockAt)[index];
        var asked = fetchUrl(NDB_INDEX + manifest.bin, {binary: true, priority: true, headers: {Range: 'bytes=' + at[0] + '-' + (at[0] + at[1] - 1)}})
            .then(function (result) {
                // A server that ignores the range sends the whole file: not something to read here.
                if (!result || (result.status !== 200 && result.status !== 206) || result.bytes.length !== at[1]) return null;
                return gunzip(result.bytes).then(JSON.parse);
            }).catch(function () { return null; });
        ndb.pieces.set(key, asked);
        if (ndb.pieces.size > 256) ndb.pieces.delete(ndb.pieces.keys().next().value);
        return asked;
    }
    function ndbCount(value) { var n = Number(value) || 0; return n > 0 ? n.toLocaleString('en-US') : ''; }
    /** Cards of NovelDB's books for a title or tag search, the most read first. */
    function ndbSearch(text, mode, limit) {
        var wanted = mode === 'tag' ? [ndbTag(text)] : ndbTokens(text);
        if (!wanted.length) return Promise.resolve([]);
        return ndbManifest().then(function (manifest) {
            if (!manifest) return [];
            return Promise.all(wanted.map(function (token) {
                return ndbPiece(manifest, 'bucket', ndbBucket(token, manifest.buckets)).then(function (bucket) { return (bucket && bucket[token]) || []; });
            })).then(function (lists) {
                if (lists.some(function (list) { return !list.length; })) return [];
                var numbers = lists[0].filter(function (n) { return lists.every(function (list) { return list.indexOf(n) >= 0; }); })
                    .sort(function (a, b) { return a - b; }).slice(0, limit || 40);
                return Promise.all(numbers.map(function (number) {
                    return ndbPiece(manifest, 'block', Math.floor(number / manifest.block)).then(function (block) {
                        var row = block && block[number % manifest.block];
                        if (!row) return null;
                        var url = row[11];
                        return {url: url, title: row[3] || row[2], originalTitle: row[3] ? row[2] : '', author: row[4] || '',
                                cover: row[5] || '', description: '', tags: row[6] || [], site: G().siteOf(host(url)), comic: false,
                                source: 'noveldb', noveldb: {source: row[0], id: row[1]}, complete: false,
                                views: ndbCount(row[7]), likes: ndbCount(row[8]), chapters: ndbCount(row[9]),
                                status: row[10] === 1 ? 'Completed' : row[10] === 0 ? 'Ongoing' : '', updated: String(row[12] || '').slice(0, 10)};
                    });
                }));
            });
        }).then(function (cards) { return (cards || []).filter(Boolean); });
    }
    function ndbShard(id, count) {
        var text = String(id || '');
        if (/^-?\d+$/.test(text) && Number.isSafeInteger(Number(text))) return Math.abs(Number(text)) % count;
        var value = 0;
        for (var i = 0; i < text.length; i++) value = ((value * 31) + text.charCodeAt(i)) >>> 0;
        return value % count;
    }
    /** A NovelDB book's synopsis, from the site's synopsis shard for it. */
    function ndbDescription(card) {
        var ref = card.noveldb || {};
        return ndbManifest().then(function (manifest) {
            var info = manifest && manifest.sources && manifest.sources[ref.source] && manifest.sources[ref.source].descriptions;
            if (!info || !ref.id) return '';
            var shard = String(ndbShard(ref.id, info.count || 128)).padStart(3, '0');
            return fetchUrl(NDB_PAGES + info.prefix + shard + '.json.gz', {binary: true, priority: true}).then(function (result) {
                if (!result || result.status >= 400) return '';
                var bytes = result.bytes;
                var text = bytes[0] === 0x1f && bytes[1] === 0x8b ? gunzip(bytes) : Promise.resolve(new TextDecoder().decode(bytes));
                return text.then(function (json) { return String(JSON.parse(json)[String(ref.id)] || '').replace(/\\n/g, '\n').trim(); });
            });
        }).catch(function () { return ''; });
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
                if (filled && (name !== 'description' || value.length > String(item.description || '').length)) merged[name] = value;
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
                    if (!result || result.status >= 400) return next();
                    var cards = extractCards(result.text(), result.url || home).filter(function (c) { return relevant(c.title, text); });
                    if (!cards.length) return next();
                    if (position) {
                        made.ways.splice(made.ways.indexOf(way), 1);
                        made.ways.unshift(way);
                        savePlans();
                    }
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
                return extractCards(result.text(), result.url || url).slice(0, 24).map(function (c) { return Object.assign(c, {verified: true}); });
            }) : Promise.resolve([]);
            return own.then(function (cards) {
                if (cards.length) return cards;
                var ways = made.ways.slice(0, 3), index = 0;
                function next() {
                    if (index >= ways.length || stopped(token)) return Promise.resolve([]);
                    return query(ways[index++], tag, token).then(function (result) {
                        if (!result || result.status >= 400 || !searched(result.url || '', made.home, tag)) return next();
                        var found = extractCards(result.text(), result.url || home);
                        if (homeAgain(found, made) || !found.length) return next();
                        return found.slice(0, 24);
                    }, next);
                }
                return next();
            });
        });
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
                        return result && result.status < 400 ? extractCards(result.text(), result.url || made.home).slice(0, 30) : [];
                    });
                }
                var url = tries[index++];
                return fetchUrl(url, {quick: url !== known, stop: token}).then(function (result) {
                    if (!result || result.status >= 400) return next();
                    var cards = extractCards(result.text(), result.url || url);
                    if (cards.length < 6) return next();
                    if (made.popular !== url) { made.popular = url; savePlans(); }
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
    function runSearch(id, text, mode, token) {
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
        var sources = SITES.filter(function (site) { return !(site.lnreader || []).length; }).map(function (site) {
            return function () {
                var started = Date.now(), home = 'https://' + site.host + '/';
                var asked = mode === 'name' ? siteSearch(home, text, token) : siteTag(home, text, token);
                return asked.then(function (hits) {
                    var ms = Date.now() - started;
                    var cards = hits.map(function (hit) {
                        return Object.assign(card(hit.url, hit.title, hit.snippet, 'site', hit.cover), {ms: ms, verified: !!hit.verified});
                    });
                    summary.site += cards.length;
                    var fresh = offer(cards);
                    var refusedSite = plans[home] && plans[home].reachable === false;
                    report(site.site, started, fresh.length, refusedSite ? 'refused' : 'ok');
                    enrich(fresh.filter(function (c) { return !c.cover; }));
                }, function () { summary.failed++; report(site.site, started, 0, 'failed'); });
            };
        });
        sources.unshift(function () {
            var started = Date.now();
            return ndbSearch(text, mode, 40).then(function (cards) {
                var fresh = offer(cards.map(function (c) { return Object.assign(c, {ms: Date.now() - started, verified: mode === 'tag'}); }));
                report('NovelDB', started, fresh.length);
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
        call('progress', id, {total: sources.length});
        var all = each(sources, 12, function (source) { return source(); }, token).then(function () {
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

    /** The popular lists of the top sites of one kind, the preferred language's sites first. */
    function runTop(id, kind, language, token) {
        var comic = kind === 'manga' || kind === 'manhwa' || kind === 'comic';
        var sites = SITES.filter(function (site) {
            return (!!site.comic === comic && !(site.lnreader || []).length) || (comic && COMIC_SITE.test(site.site));
        });
        if (comic) {
            var manga = sites.filter(function (s) { return MANGA_SITE.test(s.site); });
            var manhwa = sites.filter(function (s) { return MANHWA_SITE.test(s.site) && manga.indexOf(s) < 0; });
            var rest = sites.filter(function (s) { return manga.indexOf(s) < 0 && manhwa.indexOf(s) < 0; });
            sites = ({manga: manga, manhwa: manhwa}[kind]) || (rest.length ? rest : sites);
        }
        if (language && language !== 'all') {
            var fits = function (site) { return (TLD_LANG[site.site.split('.').pop()] || 'en') === language; };
            sites = sites.filter(fits).concat(sites.filter(function (s) { return !fits(s); }));
        }
        sites = sites.slice(0, 30);
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
                var run = item.mode === 'id' ? runById(id, item.query, token) : runSearch(id, item.query, item.mode === 'tag' ? 'tag' : 'name', token);
                return run.catch(function () { return {failed: 1}; }).then(function (summary) {
                    Object.keys(summary).forEach(function (name) {
                        var value = summary[name];
                        totals[name] = typeof value === 'number' ? (totals[name] || 0) + value : (totals[name] || value);
                    });
                });
            })).then(function () { call('done', id, totals); });
        });
    }
    function top(id, kind, language) {
        var token = newToken();
        ready.then(function () { return runTop(id, kind, language, token); })
            .catch(function () { return {failed: 1}; })
            .then(function (summary) { call('done', id, summary); });
    }

    // -- the details cache and covers --------------------------------------------------------------

    var DETAILS_KEY = 'noveldb.generic.details', DETAILS_DAYS = 3;
    function cachedDetails(url) {
        var all = store(DETAILS_KEY) || {};
        var found = all[url];
        return found && Date.now() - found.at < DETAILS_DAYS * 86400000 ? found.value : null;
    }
    function keepDetails(url, value) {
        var all = store(DETAILS_KEY) || {};
        all[url] = {at: Date.now(), value: value};
        var keys = Object.keys(all);
        if (keys.length > 400) {
            keys.sort(function (a, b) { return all[a].at - all[b].at; }).slice(0, keys.length - 400).forEach(function (k) { delete all[k]; });
        }
        store(DETAILS_KEY, all);
    }
    function detailsFor(item) {
        var cached = cachedDetails(item.url);
        if (cached) { call('details', Object.assign({}, item, cached, {complete: true})); return; }
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

    function engineFor(url) {
        var engines = [];
        if (harunekoListed(url)) engines.push('haruneko');
        engines.push('generic');
        return engines;
    }
    function parseJson(text) {
        var value = typeof text === 'string' ? JSON.parse(text) : text;
        if (!value || value.error) throw new Error((value && value.error) || 'no answer');
        return value;
    }
    function readBook(url) {
        var engines = engineFor(url), errors = [];
        function next() {
            if (!engines.length) return Promise.reject(new Error(errors.join('; ') || 'no engine could read the book'));
            var name = engines.shift();
            var start = name === 'haruneko'
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
                errors.push(name + ': ' + ((error && error.message) || error));
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
            var chapters = known.data.chapters;
            if (index < 0 || index >= chapters.length) throw new Error('no such chapter');
            var info = chapters[index];
            note('Scraping ' + (info.name || 'the chapter') + '…');
            var item = JSON.stringify(info);
            var haruneko = known.engine === 'haruneko';
            if (haruneko) watchPages(reading.id);
            var asked = haruneko ? window.__HN_parseChapter(item) : window.__GEN_parseChapter(item);
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
            call('init', {sites: SITES.map(function (s) { return s.site; }), lnreader: lnreader, downloadIcon: window.NovelDBDownloadIcon || ''});
        });
    }
    function handle(message) {
        switch (message.type) {
        case 'genericReady': init(); break;
        case 'genericSearch': search(message.id || 0, message.queries || [{query: message.query, mode: message.mode}]); break;
        case 'genericStop': if (current.token) current.token.stopped = true; break;
        case 'genericTop': top(message.id || 0, message.kind, String(message.language || 'all').slice(0, 5)); break;
        case 'genericFetch':
            transport(message.request).then(function (reply) { call('fetched', message.id, reply); });
            break;
        case 'genericImage': image(message.id, message.url, message.referer || ''); break;
        case 'genericDetails': if (message.card && /^https?:/.test(message.card.url || '')) detailsFor(message.card); break;
        case 'genericCover': coverGet(String(message.key)).then(function (data) { call('fetched', message.id, data ? {dataUrl: data} : null); }); break;
        case 'genericCoverPut': if (/^data:image\//.test(message.dataUrl || '')) coverPut(String(message.key), message.dataUrl); break;
        case 'genericBook':
            if (/^https?:/.test(message.url || '')) read(message.id, function () { return readBook(message.url); });
            else call('fetched', message.id, {error: 'only web addresses can be read here'});
            break;
        case 'genericChapter':
            if (/^https?:/.test(message.url || '')) read(message.id, function () { return readChapter(message.url, message.index | 0); });
            else call('fetched', message.id, {error: 'only web addresses can be read here'});
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
