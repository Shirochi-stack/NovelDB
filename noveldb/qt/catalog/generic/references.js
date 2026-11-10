/* NU/MU discovery and reading-source correlation, shared by desktop, Android and iOS. */
(function () {
    'use strict';
    var NU = 'https://www.novelupdates.com', MU = 'https://api.mangaupdates.com/v1';
    var cache = new Map(), nuNext = 0, googleRetry = 0;
    var SITE_WORDS = /novel|manga|manhwa|manhua|webtoon|wuxia|ranobe|lnmtl|baka-tsuki|asura|reaper|flame|leviatan|comick|comic|scans/i;
    var GENRES = new Set(['action','adult','adventure','comedy','drama','ecchi','fantasy','gender bender','harem','historical','horror','josei','martial arts','mature','mecha','mystery','psychological','romance','school life','sci-fi','seinen','shoujo','shounen','shoujo ai','shounen ai','slice of life','smut','sports','supernatural','tragedy','wuxia','xianxia','xuanhuan','yaoi','yuri']);
    function normal(text) { return String(text || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ''); }
    function host(url) { try { return new URL(url).hostname.toLowerCase().replace(/^(www|m)\./, ''); } catch (e) { return ''; } }
    function text(node) { return node ? node.textContent.replace(/\s+/g, ' ').trim() : ''; }
    function unique(values) { return Array.from(new Set(values.filter(Boolean))); }
    function clean(value) { var doc = new DOMParser().parseFromString(String(value || ''), 'text/html'); return text(doc.body); }
    function aliases(node) {
        if (!node) return [];
        node.querySelectorAll('br').forEach(function (br) { br.replaceWith('\n'); });
        node.querySelectorAll('p,li,div').forEach(function (block) { block.after('\n'); });
        return unique(node.textContent.split('\n').map(function (line) { return line.trim(); }));
    }
    function responseText(reply) {
        if (!reply || Number(reply.status) >= 400 || reply.error) throw new Error('Catalogue unavailable');
        if (reply.responseBase64) return new TextDecoder().decode(Uint8Array.from(atob(reply.responseBase64), function (c) { return c.charCodeAt(0); }));
        return String(reply.responseText == null ? reply.text || '' : reply.responseText);
    }
    function documentOf(reply) {
        var doc = new DOMParser().parseFromString(responseText(reply), 'text/html');
        if (/just a moment|attention required|verify you are human|security verification/i.test(doc.title)) throw new Error('Catalogue verification required');
        return doc;
    }
    async function request(io, url, body) {
        if (!io.alive()) throw new Error('Cancelled');
        var key = url + JSON.stringify(body || null), kept = cache.get(key);
        if (kept && Date.now() - kept.time < 600000) return kept.value;
        if (host(url) === 'novelupdates.com') {
            var wait = Math.max(0, nuNext - Date.now());
            nuNext = Date.now() + wait + 1000;
            if (wait) await new Promise(function (resolve) { setTimeout(resolve, wait); });
        }
        if (!io.alive()) throw new Error('Cancelled');
        var details = {url: url, method: body ? 'POST' : 'GET', timeout: 15000};
        if (body) { details.headers = {'Content-Type': 'application/json'}; details.data = JSON.stringify(body); }
        var value = await Promise.race([io.request(details), new Promise(function (_, reject) { setTimeout(function () { reject(new Error('Catalogue timeout')); }, 18000); })]);
        var payload = responseText(value);
        if (/<title[^>]*>\s*(just a moment|attention required)/i.test(payload)) throw new Error('Catalogue verification required');
        cache.set(key, {time: Date.now(), value: value});
        if (cache.size > 300) cache.delete(cache.keys().next().value);
        return value;
    }
    function nuRows(doc, page) {
        var rows = Array.from(doc.querySelectorAll('.search_main_box_nu')).map(function (box, index) {
            var a = box.querySelector('.search_title a[href]'), image = box.querySelector('.search_img_nu img');
            if (!a) return null;
            var url = new URL(a.getAttribute('href'), NU).href;
            if (host(url) !== 'novelupdates.com' || !/^\/series\//.test(new URL(url).pathname)) return null;
            var description = (box.querySelector('.search_body_nu') || box).cloneNode(true);
            description.querySelectorAll('.search_title,.search_stats,.search_genre,.morelink,.dots').forEach(function (node) { node.remove(); });
            return {key: 'nu:' + new URL(url).pathname, provider: 'NU', url: url, title: text(a), aliases: [text(a)],
                authors: [], tags: Array.from(box.querySelectorAll('.search_genre a')).map(text), description: text(description),
                cover: image ? image.getAttribute('src') || '' : '', comic: false, rank: (page - 1) * 25 + index};
        }).filter(Boolean);
        if (!rows.length && !doc.querySelector('#tags_include') && !/no results|no series found/i.test(text(doc.body))) throw new Error('NU unavailable');
        return {records: rows, more: !!doc.querySelector('.digg_pagination a[rel="next"], .digg_pagination .next_page')};
    }
    async function nuSearch(options, io) {
        var params = new URLSearchParams({sf: '1', sort: ({title:'abc',views:'srate',likes:'sread',chapters:'srel',updated:'sdate'})[options.sort] || 'srank',
            order: options.sort === 'title' || !options.sort ? 'asc' : 'desc', pg: String(options.page || 1)});
        if (options.text) params.set('sh', options.text);
        var tags = options.tags || [];
        if (tags.length) {
            var finder = documentOf(await request(io, NU + '/series-finder/'));
            var tagMap = new Map(), genreMap = new Map();
            finder.querySelectorAll('#tags_include option').forEach(function (o) { tagMap.set(normal(text(o)), o.value); });
            finder.querySelectorAll('a.genre').forEach(function (a) { genreMap.set(normal(text(a)), a.getAttribute('genreid') || a.getAttribute('gid')); });
            finder.querySelectorAll('a[genreid]').forEach(function (a) { if (GENRES.has(text(a).toLowerCase())) genreMap.set(normal(text(a)), a.getAttribute('genreid')); });
            var wantedTags = [], wantedGenres = [];
            tags.forEach(function (tag) {
                var key = normal(tag);
                if (tagMap.has(key)) wantedTags.push(tagMap.get(key));
                else if (genreMap.has(key)) wantedGenres.push(genreMap.get(key));
                else throw new Error('NU cannot resolve tag ' + tag);
            });
            if (wantedTags.length) { params.set('tgi', wantedTags.join(',')); params.set('mtgi', 'and'); }
            if (wantedGenres.length) { params.set('gi', wantedGenres.join(',')); params.set('mgi', 'and'); }
        }
        if (options.kind === 'lightnovel') params.set('nt', '2443');
        var doc = documentOf(await request(io, NU + '/series-finder/?' + params));
        return nuRows(doc, options.page || 1);
    }
    function muRecord(row, rank) {
        var image = row.image && row.image.url || {}, comic = !/novel/i.test(row.type || '');
        var authors = (row.authors || []).filter(function (a) { return !a.type || a.type === 'Author'; }).map(function (a) { return a.name; });
        return {key: 'mu:' + row.series_id, provider: 'MU', id: row.series_id, url: row.url, title: clean(row.title),
            aliases: unique([clean(row.title)].concat((row.associated || []).map(function (a) { return clean(a.title); }))),
            authors: authors, tags: (row.genres || []).map(function (g) { return g.genre; }).concat((row.categories || []).map(function (c) { return c.category; })),
            description: clean(String(row.description || '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')), linksText: row.description || '',
            cover: image.original || image.thumb || '', comic: comic, type: row.type, rating: row.bayesian_rating || '', rank: rank};
    }
    async function muSearch(options, io) {
        var types = {manga:['Manga'],manhwa:['Manhwa'],comic:['Manga','Manhwa','Manhua']};
        var body = {page: options.page || 1, perpage: 25,
            orderby: ({title:'title',new:'year',updated:'date_added',views:'list_reading',likes:'rating',chapters:'list_reading'})[options.sort] || (options.text ? 'score' : 'list_reading'),
            include_rank_metadata: true};
        if (options.kind === 'all') {
            if (options.sort === 'views') body.orderby = 'rating';
            if (options.sort === 'likes') body.orderby = 'list_reading';
            body.type = ['Manga', 'Manhwa', 'Manhua'];
        }
        if (options.text) body.search = options.text;
        if (types[options.kind]) body.type = types[options.kind];
        var tags = options.tags || [];
        body.genre = tags.filter(function (tag) { return GENRES.has(tag.toLowerCase()); });
        body.category = tags.filter(function (tag) { return !GENRES.has(tag.toLowerCase()); });
        var data = JSON.parse(responseText(await request(io, MU + '/series/search', body)));
        if (!Array.isArray(data.results)) throw new Error('MU unavailable');
        var records = data.results.map(function (hit, index) {
            var record = muRecord(hit.record || hit, ((options.page || 1) - 1) * 25 + index);
            if (hit.hit_title) record.aliases = unique(record.aliases.concat([clean(hit.hit_title)]));
            return record;
        }).filter(function (record) {
            if (!record.id || !record.title || !record.url || (types[options.kind] && types[options.kind].indexOf(record.type) < 0)) return false;
            if (options.text) {
                var wanted = normal(options.text);
                var words = String(options.text).toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
                if (!record.aliases.some(function (name) {
                    return normal(name).includes(wanted) || words.every(function (word) { return name.toLowerCase().includes(word); });
                })) return false;
            }
            return true;
        });
        return {records: records, more: (data.page || options.page || 1) * (data.per_page || 25) < data.total_hits};
    }
    async function search(options, io, receive) {
        if (options.kind === 'all') {
            var combined = await Promise.allSettled([nuSearch(options, io), muSearch(options, io)].map(function (work, provider) {
                return work.then(function (result) {
                    result.records.forEach(function (record) { record.rank = record.rank * 2 + provider; });
                    if (receive && io.alive()) receive(result);
                    return result;
                });
            }));
            var records = [], more = false, available = false;
            combined.forEach(function (result, provider) {
                if (result.status !== 'fulfilled') return;
                available = true; more = more || result.value.more;
                result.value.records.forEach(function (record) { records.push(record); });
            });
            if (!available) throw new Error('NU/MU unavailable');
            return {records: records.sort(function (a, b) { return a.rank - b.rank; }), more: more, available: true};
        }
        var result = await (/^(manga|manhwa|comic)$/.test(options.kind) ? muSearch(options, io) : nuSearch(options, io));
        if (receive && io.alive()) receive(result);
        return Object.assign(result, {available: true});
    }
    function externalLinks(value) {
        return unique((String(value || '').match(/https?:\/\/[^\s<>"')\]]+/g) || []).filter(function (url) {
            return !/(novelupdates|mangaupdates|facebook|twitter|discord|amazon|youtube|patreon|cdn\.)/.test(host(url));
        }));
    }
    async function detail(ref, io) {
        if (ref.provider === 'MU') {
            var data = JSON.parse(responseText(await request(io, MU + '/series/' + ref.id)));
            var full = muRecord(data, ref.rank);
            var groups = await request(io, MU + '/series/' + ref.id + '/groups').then(function (r) { return JSON.parse(responseText(r)); }).catch(function () { return {}; });
            full.groupSites = unique((groups.group_list || []).map(function (g) { return g.social && g.social.site; }).filter(function (url) { return url && !/mangadex\.org\/group\//.test(url); }));
            full.groupNames = unique((groups.group_list || []).map(function (g) { return g.name; }));
            full.links = externalLinks(full.linksText);
            return full;
        }
        var doc = documentOf(await request(io, ref.url)), title = doc.querySelector('.seriestitlenu');
        if (!title) throw new Error('NU details unavailable');
        var image = doc.querySelector('.seriesimg img'), description = doc.querySelector('#editdescription');
        var full = Object.assign({}, ref, {title: text(title), aliases: unique([text(title)].concat(aliases(doc.querySelector('#editassociated')))),
            authors: Array.from(doc.querySelectorAll('#showauthors a')).map(text),
            tags: Array.from(doc.querySelectorAll('#seriesgenre a,#showtags a')).map(text),
            description: text(description) || ref.description, cover: image && image.getAttribute('src') || ref.cover});
        full.groupSites = [];
        var groupLinks = Array.from(doc.querySelectorAll('a[href*="/group/"]')).filter(function (a) { return /\/group\/[^/]+/.test(a.getAttribute('href')); });
        full.groupNames = unique(groupLinks.map(text));
        full.groupPages = unique(groupLinks.map(function (a) { return new URL(a.getAttribute('href'), NU).href; }));
        return full;
    }
    async function groupSites(ref, io, full) {
        var sites = (ref.groupSites || []).slice();
        await each(full ? ref.groupPages || [] : (ref.groupPages || []).slice(0, 3), 1, async function (url) {
            try {
                var group = documentOf(await request(io, url));
                group.querySelectorAll('tr').forEach(function (row) {
                    if (/^(url|website)$/i.test(text(row.querySelector('td,th')))) row.querySelectorAll('a[href]').forEach(function (a) {
                        var link = new URL(a.getAttribute('href'), url).href;
                        if (externalLinks(link).length) sites.push(link);
                    });
                });
                Array.from(group.querySelectorAll('a[href]')).forEach(function (a) {
                    if (/website|visit site/i.test(text(a)) || /website/i.test(a.parentElement && text(a.parentElement).slice(0, 100))) {
                        var link = new URL(a.getAttribute('href'), url).href;
                        if (externalLinks(link).length) sites.push(link);
                    }
                });
            } catch (e) {}
        });
        return unique(sites);
    }
    async function each(items, workers, work) {
        var position = 0;
        await Promise.all(Array.from({length: Math.min(workers, items.length)}, async function () {
            while (position < items.length) { var item = items[position++]; await work(item); }
        }));
    }
    function matches(ref, card) {
        var names = [card.title, card.originalTitle].concat(card.alternateTitles || []).map(normal).filter(Boolean);
        var wanted = ref.aliases.concat([ref.title]).map(normal).filter(Boolean);
        var brand = normal(host(card.url).split('.')[0]);
        if (!wanted.some(function (name) { return names.some(function (candidate) {
            if (candidate === name) return true;
            if (name.length < 4 || !candidate.startsWith(name)) return false;
            var suffix = candidate.slice(name.length);
            return /^(mainstory|sidestories|ifstories|readonline)$/.test(suffix)
                || (name.length >= 8 && (suffix === brand || /^(novel|manga|manhwa|webtoon)$/.test(suffix)));
        }); })) return false;
        var author = normal(card.author), nativeAuthor = /[\u3400-\u9fff\uac00-\ud7af]/;
        if (author && ref.authors.length) {
            var comparable = ref.authors.filter(function (name) { return nativeAuthor.test(name) === nativeAuthor.test(card.author); });
            var names = [card.author].concat(String(card.author).split(/[,/&]/)).map(normal);
            if (comparable.length && !comparable.some(function (a) { return names.indexOf(normal(a)) >= 0; })) return false;
        }
        return card.comic == null || !!card.comic === !!ref.comic;
    }
    async function prepare(original, options, io, emit) {
        var ref = original, sent = [], indexed = new Set();
        async function index() {
            if (!options.index || !io.alive()) return;
            var names = unique([ref.title].concat(ref.aliases.filter(function (name) { return /[\u3400-\u9fff\uac00-\ud7af]/.test(name); }), ref.aliases))
                .filter(function (name) { return !indexed.has(name); }).slice(0, options.full ? 24 : 6);
            while (names.length && io.alive()) {
                var batch = names.splice(0, 6);
                batch.forEach(function (name) { indexed.add(name); });
                var cards = await io.index(batch).catch(function () { return []; });
                sent = sent.concat(cards.filter(function (card) { return matches(ref, card); }));
                if (io.alive() && sent.length && ref.authors.length) emit(sent.map(function (card) { return Object.assign({}, card, {catalogue: ref, complete: true, via: options.top ? 'top' : 'name'}); }));
            }
        }
        var enriching = detail(ref, io).catch(function () { return ref; });
        await index();
        ref = await enriching;
        await index();
        if (io.alive() && sent.length) emit(sent.filter(function (card) { return matches(ref, card); }).map(function (card) { return Object.assign({}, card, {catalogue: ref, complete: true, via: options.top ? 'top' : 'name'}); }));
        return ref;
    }
    async function correlate(ref, options, io, emit) {
        var sent = new Map(), checked = new Set();
        function offer(cards) {
            if (!io.alive()) return;
            var out = [];
            cards.forEach(function (card) {
                if (!card.url || /^(novelupdates|mangaupdates)\.com$/.test(host(card.url)) || !matches(ref, card)) return;
                var site = host(card.url), previous = sent.get(site);
                if (previous && previous.url !== card.url) return;
                sent.set(site, card);
                out.push(Object.assign({}, card, {catalogue: ref, comic: ref.comic, complete: true, via: options.top ? 'top' : 'name'}));
            });
            if (out.length) emit(out);
        }
        if (!io.alive()) return;
        var groups = unique((await groupSites(ref, io, options.full)).map(host)), pool = unique(groups.concat(options.sites || []));
        function allowed(url) { var name = host(url); return pool.some(function (site) { return name === site || name.endsWith('.' + site); }); }
        async function verify(url, groupMatch) {
            var name = host(url);
            var groupSource = groupMatch && /^[a-z0-9.-]+\.[a-z]{2,}$/.test(name) && externalLinks(url).length;
            if (url.length > 2000 || checked.has(url) || (!allowed(url) && !groupSource) || !io.alive()) return;
            checked.add(url);
            var cards = await io.site({url: url}).catch(function () { return []; });
            offer(cards);
        }
        await each((ref.links || []).slice(0, 8), 2, async function (url) { await verify(url, true); });
        var title = ref.title.replace(/"/g, '').trim();
        var queries = [{text:'"' + title + '" (novel OR manga OR manhwa OR webtoon)'},
            {text:'"' + title + '" (' + pool.slice(0, 8).map(function (site) { return 'site:' + site; }).join(' OR ') + ')'}];
        (options.full ? ref.groupNames || [] : (ref.groupNames || []).slice(0, 3)).forEach(function (name) {
            queries.push({text:'"' + title + '" "' + name.replace(/"/g, '') + '"', group:true});
        });
        await each(queries, 1, async function (query) {
            if (!io.alive() || Date.now() < googleRetry) return;
            try {
                var result = await io.web(query.text + ' -inurl:chapter -inurl:profile -inurl:search -intitle:"Chapter"');
                if (result.blocked) { googleRetry = Date.now() + 60000; return; }
                var hits = result.hits || [];
                await each(hits.filter(function (hit) { return query.group || allowed(hit.url); }).slice(0, options.full ? 20 : 8), 2, async function (hit) { await verify(hit.url, query.group); });
            } catch (e) {}
        });
        var direct = options.full ? pool : groups.slice(0, 3).concat(sent.size ? [] : pool.slice(0, 3));
        await each(unique(direct), 2, async function (site) {
            if (io.alive()) offer(await io.site({host: site, query: ref.title}).catch(function () { return []; }));
        });
    }
    window.NovelDBReferences = {search: search, prepare: prepare, correlate: correlate, each: each, matches: matches, parseNU: nuRows, parseMU: muRecord,
        allowedTopSite: function (site) { return SITE_WORDS.test(site); }, normal: normal};
}());
