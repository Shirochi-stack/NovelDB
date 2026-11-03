/*
 * The Catalog's generic search page (desktop). Runs in the bridge's isolated world, next to
 * catalog-bridge.js (NovelDBQt.postMessage) and the LNReader bundle (window.__LNR_*).
 * - Searching asks the app ({type: 'genericSearch', queries}): the title, and each picked tag. The app
 *   searches the sites' own search pages, their genre and tag pages and lightnovel-crawler's searches,
 *   and sends cards back (NovelDBGeneric.add); LNReader plugins search here, their requests made by the
 *   app ({type: 'genericFetch'}, answered by NovelDBGeneric.fetched).
 * - The same book from several sites is one card; the site that answered first gives its cover.
 * - With no search, the page shows the top 100 web novels from the sites' own popular lists.
 * - Cards in view ask the app for their book page's details ({type: 'genericDetails'}).
 * - The details panel downloads ({type: 'genericDownload'}), adds to the link cart ({type: 'cartAdd'})
 *   or opens the reader, which scrapes one chapter at a time ({type: 'genericBook'/'genericChapter'},
 *   or the LNReader plugin here for its sites).
 */
(function () {
    'use strict';
    if (window.NovelDBGeneric) return;
    var $ = function (id) { return document.getElementById(id); };
    var config = {sites: [], lnreader: []};
    var groups = new Map(), byUrl = new Map(), order = [];
    var cart = new Set(), pending = {}, nextId = 0, searchId = 0, running = 0;
    var view = 'top', current = {text: '', tags: []}, picked = [], resultTags = new Map();
    // TOP_KEY's version: a new one drops the top lists saved by older code.
    var TOP_KEY = 'noveldb.generic.top.v5', TOP_HOURS = 12, TOP_SIZE = 100;
    var TAGS = ['Action', 'Adventure', 'Apocalypse', 'BL', 'Comedy', 'Cultivation', 'Drama', 'Dungeon', 'Ecchi', 'Fantasy',
        'Game', 'Gender Bender', 'GL', 'Harem', 'Historical', 'Horror', 'Isekai', 'Josei', 'LitRPG', 'Magic', 'Martial Arts',
        'Mature', 'Mecha', 'Military', 'Modern', 'Murim', 'Mystery', 'Psychological', 'Regression', 'Reincarnation', 'Revenge',
        'Romance', 'Romantic Comedy', 'School Life', 'Sci-fi', 'Seinen', 'Shoujo', 'Shounen', 'Slice of Life', 'Sports',
        'Supernatural', 'System', 'Thriller', 'Time Travel', 'Tragedy', 'Transmigration', 'Villainess', 'Wuxia', 'Xianxia',
        'Xuanhuan', 'Yaoi', 'Yuri'];
    // A site's "nothing matched your search" page (the app drops these too; see generic_search.NO_RESULTS).
    var NO_RESULTS = /(no (items?|results?|books?|novels?|titles?|matches|products?)( were)? (found|matching|match)|nothing (was )?found|did not match any|didn't match any|(found|returned) (0|no) (results?|items?|matches)|\b0 results\b|no results for|見つかりませんでした|該当する.{0,6}(ありません|見つかりません)|検索結果は?0件|검색\s*결과가\s*없|결과가\s*없습니다|일치하는.{0,10}없|没有找到|未找到|沒有找到|找不到相关|暂无(结果|数据|相关)|無結果|无结果|查無|no se encontraron|sin resultados|aucun résultat|ничего не найдено|не найдено|keine (ergebnisse|treffer)|nenhum resultado)/i;
    var JUNK = /^(request|requests|リクエスト|login|log in|sign in|ログイン|search|検索|more|もっと見る|home|top|next|prev(ious)?|menu)$/i;

    function send(message) { window.NovelDBQt && window.NovelDBQt.postMessage(JSON.stringify(message)); }
    function key(url) { return String(url || '').replace(/\/+$/, ''); }
    function esc(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }
    function store(name, value) {
        try { if (value === undefined) return JSON.parse(localStorage.getItem(name) || 'null'); localStorage.setItem(name, JSON.stringify(value)); }
        catch (e) { return null; }
    }
    // An app request answered through NovelDBGeneric.fetched.
    function ask(message, onId) {
        return new Promise(function (resolve) {
            var id = ++nextId;
            pending[id] = resolve;
            message.id = id;
            if (onId) onId(id);
            send(message);
        });
    }
    window.__LNR_transport = function (request) { return ask({type: 'genericFetch', request: request}); };

    function status(text) { $('status').textContent = text; }

    // -- the preferred language of the top lists (English unless picked otherwise) -----------------
    var LANG_KEY = 'noveldb.generic.language';
    var language = (store(LANG_KEY) || 'en');
    var LANG_NAMES = {english: 'en', japanese: 'ja', korean: 'ko', chinese: 'zh', russian: 'ru', spanish: 'es',
                      portuguese: 'pt', french: 'fr', indonesian: 'id', thai: 'th', arabic: 'ar', turkish: 'tr',
                      german: 'de', vietnamese: 'vi'};
    var TLD_LANG = {jp: 'ja', kr: 'ko', cn: 'zh', tw: 'zh', hk: 'zh', ru: 'ru', es: 'es', mx: 'es', ar: 'es', br: 'pt', pt: 'pt',
                    fr: 'fr', id: 'id', th: 'th', tr: 'tr', de: 'de', vn: 'vi'};
    var pluginLangs = {};
    var allPlugins = [];  // every LNReader plugin: {id, site, rank (its place among the top sites, or -1), ln}
    // Sites of translated Japanese/Korean/Chinese light novels (the Light Novels top list comes from them).
    var LN_SITE = /light ?novel|lnmtl|\bln\.|lnori|novelight|hako|syosetu|kakuyomu|yomou|bookwalker|j-?novel/i;
    function plugins() { return allPlugins.length ? allPlugins : (config.lnreader || []); }
    function langCode(name) {
        var value = String(name || '').toLowerCase();
        return LANG_NAMES[value] || (value.length === 2 ? value : '');
    }
    // A book's language: its LNReader plugin's, else the script of its title, else its site's country.
    function langOf(card) {
        var plugin = card.plugin && pluginLangs[card.plugin];
        if (plugin) return plugin;
        var title = String(card.title || '');
        if (/[\uac00-\ud7af]/.test(title)) return 'ko';
        if (/[\u3040-\u30ff]/.test(title)) return 'ja';
        var tld = String(card.site || '').split('.').pop();
        if (/[\u4e00-\u9fff]/.test(title)) return tld === 'jp' ? 'ja' : 'zh';
        if (/[\u0400-\u04ff]/.test(title)) return 'ru';
        if (/[\u0e00-\u0e7f]/.test(title)) return 'th';
        if (/[\u0600-\u06ff]/.test(title)) return 'ar';
        return TLD_LANG[tld] || 'en';
    }
    function inLanguage(card) { return language === 'all' || langOf(card) === language; }

    // -- progress: every source (a site's search, a plugin, the web search) reports when it answers --
    var progress = {expected: 0, answered: 0, started: 0, sites: [], timer: 0};
    function startProgress(expected) {
        progress = {expected: expected || 0, answered: 0, started: Date.now(), sites: [], timer: progress.timer};
        clearInterval(progress.timer);
        progress.timer = setInterval(showProgress, 500);
        $('sitesButton').style.display = '';
        showProgress();
    }
    function siteAnswered(item) {
        progress.answered++;
        progress.sites.push(item);
        showProgress();
        if ($('sitespanel').classList.contains('open')) drawSites();
    }
    function showProgress() {
        var on = running > 0;
        $('spinner').classList.toggle('on', on);
        $('progressbar').classList.toggle('on', on);
        var share = progress.expected ? Math.min(1, progress.answered / progress.expected) : 0;
        $('progressbar').firstElementChild.style.width = (share * 100) + '%';
        if (!on) { clearInterval(progress.timer); return; }
        if (view !== 'search') return;
        var seconds = Math.round((Date.now() - progress.started) / 1000);
        status('Searching… ' + progress.answered + ' of ' + (progress.expected || '?') + ' sources answered · ' + seconds + ' s · '
               + shownCount() + ' book' + (shownCount() === 1 ? '' : 's') + ' so far');
    }
    function seconds(ms) { return ms == null ? '' : (ms < 10000 ? (ms / 1000).toFixed(1) : Math.round(ms / 1000)) + ' s'; }
    function drawSites() {
        var rows = progress.sites.slice().sort(function (a, b) { return a.ms - b.ms; });
        var html = '<h3>' + progress.answered + ' of ' + (progress.expected || '?') + ' sources answered</h3>';
        html += rows.map(function (item) {
            var what = item.state === 'failed' ? 'failed' : item.state === 'refused' ? 'refused' : item.state === 'timeout' ? 'too slow'
                : item.found + ' found';
            return '<div class="siterow ' + esc(item.state || 'ok') + '"><span>' + esc(item.site) + '</span><span class="ms">'
                + seconds(item.ms) + '</span><span class="n">' + esc(what) + '</span></div>';
        }).join('');
        if (running && progress.expected > progress.answered) html += '<p>' + (progress.expected - progress.answered) + ' still searching…</p>';
        $('sitespanel').innerHTML = html;
    }
    $('sitesButton').addEventListener('click', function (event) {
        event.stopPropagation();
        $('settingspanel').classList.remove('open');
        $('sitespanel').classList.toggle('open');
        if ($('sitespanel').classList.contains('open')) drawSites();
    });

    // The title shares the query's words (most of them), as the app's own site search requires.
    function words(text) {
        return (String(text || '').toLowerCase().match(/[\p{L}\p{N}']+/gu) || []).filter(function (w) {
            return w.length > 1 || /[^\x00-\x7f]/.test(w);
        });
    }
    function relevant(title, query) {
        var want = words(query), have = words(title).join(' ');
        if (!want.length) return true;
        if (want.length === 1 && /[^\x00-\x7f]/.test(want[0])) return have.replace(/ /g, '').indexOf(want[0]) >= 0;
        var hits = want.filter(function (w) { return have.indexOf(w) >= 0; }).length;
        return hits >= Math.max(1, want.length - Math.floor(want.length / 3));
    }
    function within(promise, ms, fallback) {
        return Promise.race([promise, new Promise(function (resolve) { setTimeout(function () { resolve(fallback === undefined ? '[]' : fallback); }, ms); })]);
    }
    function parse(raw) { try { return typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) { return null; } }

    function setRunning(delta) {
        running = Math.max(0, running + delta);
        $('stop').disabled = running === 0;
        showProgress();
    }

    // -- what a book is ----------------------------------------------------------------------------
    var COMIC_SITE = /(manga|manhwa|manhua|toon|comic|piccoma|mechacomic|booklive|toomics)/i;
    function isComic(card) {
        if (card.source === 'lnreader') return !!card.comic;  // LNReader is a novel app
        if (typeof card.comic === 'boolean' && (config.sites || []).indexOf(card.site) >= 0) return card.comic;
        return !!card.comic || COMIC_SITE.test(card.site || '');
    }
    function kindOf(group) {
        if (group.topKind && group.topKind !== 'webnovel') return group.topKind;
        var text = [group.title, (group.tags || []).join(' '), group.sources.map(function (s) { return s.site; }).join(' ')].join(' ');
        if (group.comic) {
            if (/manhwa|webtoon|korean|웹툰|만화|\.kr\b|toomics|manhwa/i.test(text)) return 'manhwa';
            if (/manga|japanese|漫画|マンガ|\.jp\b|piccoma|mechacomic|booklive/i.test(text) && !/manhua|chinese/i.test(text)) return 'manga';
            return 'comic';
        }
        if (/light ?novel|\bln\b|ライトノベル|ラノベ|라노벨|轻小说|輕小說|bookwalker|j-novel/i.test(text)) return 'lightnovel';
        return 'webnovel';
    }
    var KIND_NAMES = {webnovel: 'Web novel', lightnovel: 'Light novel', manga: 'Manga', manhwa: 'Manhwa', comic: 'Comic'};
    function groupKey(card) {
        var title = String(card.title || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
        return (isComic(card) ? 'c:' : 'n:') + (title || key(card.url));
    }
    function wanted(group) {
        var kind = $('kind').value;
        if (view === 'top') return true;  // each category has its own top list
        return kind === 'all' || kindOf(group) === kind;
    }

    // -- cards -------------------------------------------------------------------------------------
    function reset(nextView) {
        view = nextView;
        groups.clear(); byUrl.clear(); order = []; resultTags.clear();
        $('grid').innerHTML = '';
        queue.length = 0;
    }

    // A card into its book's group: a new group (placed after the ones already shown, so the fastest
    // site leads), or more for one already shown (a second site, or the book page's details).
    // "Site - Its slogan - The Book" (a page title): the part that matches the search.
    // Only a title of three parts or more, or one with a part naming the site, is cut: "Title - Subtitle" stays.
    function bookTitle(title, site) {
        var parts = String(title || '').split(/\s+[-|–—]\s+/).filter(Boolean);
        if (parts.length < 2 || !current.text) return title;
        var label = String(site || '').split('.')[0].toLowerCase();
        var naming = label.length > 2 && parts.some(function (part) { return part.toLowerCase().indexOf(label) >= 0; });
        if (parts.length < 3 && !naming) return title;
        var hits = parts.filter(function (part) { return relevant(part, current.text); });
        if (!hits.length || hits.length === parts.length) return title;
        var first = parts.indexOf(hits[0]);
        return parts.slice(first).filter(function (part) { return !(label.length > 2 && part.toLowerCase().indexOf(label) >= 0); }).join(' - ');
    }
    function merge(card, rank) {
        if (card && card.title) card.title = bookTitle(card.title, card.site);
        if (!card || !card.url || !card.title || JUNK.test(String(card.title).trim())) return null;
        if (NO_RESULTS.test(card.title) || NO_RESULTS.test(String(card.description || '').slice(0, 400))) return null;
        var group = byUrl.get(key(card.url)) || groups.get(groupKey(card));
        if (!group) {
            group = {key: groupKey(card), title: card.title, author: '', cover: '', description: '', tags: [], status: '',
                     comic: isComic(card), sources: [], rank: rank, loaded: false};
            groups.set(group.key, group);
            order.push(group);
        }
        var source = group.sources.find(function (s) { return key(s.url) === key(card.url); });
        if (!source) {
            source = {url: card.url, site: card.site || hostOf(card.url), source: card.source || '', plugin: card.plugin || '', path: card.path,
                      ms: card.ms};
            group.sources.push(source);
            byUrl.set(key(card.url), group);
        }
        ['author', 'status', 'published', 'updated', 'rating', 'ratings', 'likes', 'views', 'chapters'].forEach(function (name) {
            if (card[name] && !group[name]) group[name] = card[name];
        });
        if (card.cover && !group.cover) group.cover = card.cover;  // the first site's cover stays
        if (card.cover && (group.covers || (group.covers = [])).indexOf(card.cover) < 0) group.covers.push(card.cover);
        if (card.description && card.description.length > (group.description || '').length) group.description = card.description;
        if (card.tags && card.tags.length) {
            var seen = new Set(group.tags.map(function (t) { return t.toLowerCase(); }));
            card.tags.forEach(function (tag) {
                if (!seen.has(tag.toLowerCase())) { seen.add(tag.toLowerCase()); group.tags.push(tag); }
            });
        }
        if (card.complete && key(card.url) === key(group.sources[0].url)) group.loaded = true;
        if (card.complete && card.description) group.loaded = true;
        return group;
    }
    // A source that turned out to be an empty search page leaves its book; a book with no source left goes.
    function drop(group, url) {
        group.sources = group.sources.filter(function (s) { return key(s.url) !== key(url); });
        byUrl.delete(key(url));
        if (group.timer) { clearTimeout(group.timer); group.timer = 0; loading = Math.max(0, loading - 1); }
        if (group.sources.length) {
            group.asked = false;
            group.loaded = false;
            if (group.element) render(group);
        } else {
            groups.delete(group.key);
            order = order.filter(function (g) { return g !== group; });
            if (group.element) group.element.remove();
            if ($('dialog').open && group.onDetails) $('dialog').close();
        }
        if (view === 'search') status(shownCount() + ' books found' + (running ? '…' : '.'));
        empty();
        pump();
    }
    // A tag search (tags picked, no title): only books shown to have the tag appear. A book from a site's own
    // page for the tag is shown at once; one a search for the tag's words found waits, hidden, for its page.
    function tagOnly() { return view === 'search' && current.tags.length > 0 && !current.text; }
    function holdForTag(group) {
        if (group.held) return;
        group.held = true;
        if (!group.loaded && !group.asked) { group.asked = true; queue.push(group); pump(); }
    }
    function hasTag(group, card) {
        var listed = (card.tags || []).concat(group.tags || []).map(tagWords);
        return current.tags.some(function (tag) {
            var want = tagWords(tag);
            return listed.some(function (have) { return have === want || have.indexOf(want) >= 0; });
        });
    }
    // A book found by a tag search whose page lists tags, none of them a picked tag: not what was asked for.
    function tagWords(tag) { return String(tag || '').toLowerCase().replace(/[\s_\-]+/g, ' ').trim(); }
    function offTag(group, card) {
        if (view !== 'search' || !current.tags.length || current.text) return false;
        var listed = (card.tags || []).map(tagWords).filter(Boolean);
        if (listed.length < 2) return false;  // too few to tell
        return !current.tags.some(function (tag) {
            var want = tagWords(tag);
            return listed.some(function (have) { return have === want || have.indexOf(want) >= 0 || want.indexOf(have) >= 0; });
        });
    }
    function hostOf(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; } }

    // A count worth showing: not missing, and not a page's empty "0" counter.
    function some(value) { return !!value && /[1-9]/.test(String(value)); }
    function figures(group) {
        var out = [];
        if (!some(group.rating)) group.rating = '';
        if (!some(group.likes)) group.likes = '';
        if (!some(group.views)) group.views = '';
        if (!some(group.ratings)) group.ratings = '';
        if (!some(group.chapters)) group.chapters = '';
        if (group.rating) out.push('★ ' + group.rating + (group.ratings ? ' (' + group.ratings + ')' : ''));
        if (group.likes) out.push('♥ ' + group.likes);
        if (group.views) out.push('👁 ' + group.views);
        if (group.chapters) out.push(group.chapters + ' ch');
        return out;
    }

    function render(group) {
        if (groups.get(group.key) !== group) return;  // replaced (the top list re-sorted) or dropped
        var element = group.element;
        if (!element) {
            element = group.element = document.createElement('div');
            element.className = 'card';
            element.addEventListener('click', function (event) {
                if (event.target.closest('.add')) return;
                details(group);
            });
            observer && observer.observe(element);
            element.__group = group;
        }
        if (element.parentNode !== $('grid')) $('grid').appendChild(element);
        var inCart = group.sources.some(function (s) { return cart.has(s.url); });
        var more = group.sources.length > 1 ? ' +' + (group.sources.length - 1) : '';
        var synopsis = group.description
            ? '<div class="synopsis">' + esc(group.description) + '</div>'
            : (group.loaded ? '' : '<div class="loading"><i></i><i></i><i></i></div>');
        element.innerHTML = '<div class="cover' + (group.cover ? ' loading' : '') + '">'
            + (group.cover ? '<img loading="lazy" referrerpolicy="no-referrer" alt="" src="' + esc(group.cover) + '">' : '')
            + '</div>'
            + '<span class="site" title="' + esc(group.sources.map(function (s) { return s.site + (s.ms != null ? ' (' + seconds(s.ms) + ')' : ''); }).join(', ')) + '">'
            + esc(group.sources[0].site + more) + '</span>'
            // The download artwork: greyed out until the book is in the link cart.
            + '<button class="add' + (inCart ? ' in' : '') + (config.downloadIcon ? ' art' : '') + '" title="'
            + (inCart ? 'In the link cart (click to remove)' : 'Add to the link cart') + '" type="button"'
            + (config.downloadIcon ? ' style="background-image:url(' + config.downloadIcon + ')"' : '') + '>'
            + (config.downloadIcon ? '' : inCart ? '✓' : '+') + '</button>'
            + '<div class="body"><div class="title">' + esc(group.title) + '</div>'
            + '<div class="meta">' + esc([group.author, KIND_NAMES[kindOf(group)], group.status].filter(Boolean).join(' · ')) + '</div>'
            + (figures(group).length ? '<div class="figures">' + esc(figures(group).join('  ')) + '</div>' : '')
            + (group.tags.length ? '<div class="tags">' + group.tags.slice(0, 6).map(function (t) { return '<span>' + esc(t) + '</span>'; }).join('') + '</div>' : '')
            + synopsis + '</div>';
        element.querySelector('.add').addEventListener('click', function () { toggleCart(group); });
        var image = element.querySelector('img');
        if (image && imaging.low && !/^data:/.test(group.cover) && !group.cacheAsked) {
            // The small copy made before with these settings, from the offline cache; else the cover itself.
            group.cacheAsked = true;
            var wanted_ = group.cover;
            image.removeAttribute('src');
            ask({type: 'genericCover', key: coverKey(wanted_)}).then(function (data) {
                if (group.cover !== wanted_) return;
                if (data && data.dataUrl) { group.cover = data.dataUrl; image.dataset.small = '1'; }
                image.src = group.cover;
            });
        }
        if (image) {
            image.addEventListener('error', function () { coverFailed(group, image); }, {once: true});
            image.addEventListener('load', function () {
                if (image.parentNode) image.parentNode.classList.remove('loading');
                shrink(group, image);
            }, {once: true});
        }
        element.style.display = wanted(group) && !(group.held && !group.verified) ? '' : 'none';
        fit(element.querySelector('.title'));
        empty();
    }

    // Some sites refuse covers to other pages: the app fetches it with the site's address; then the
    // other sites' covers, if the book has them.
    function coverFailed(group, image) {
        var holder = image.parentNode;
        image.style.visibility = 'hidden';  // no broken-image outline while the app tries again
        if (group.coverTried) {
            // The app couldn't fetch it either: the book's cover from its next site, if it has one.
            var next = (group.covers || []).filter(function (c) { return c !== group.cover && !(group.failedCovers || []).includes(c); })[0];
            (group.failedCovers || (group.failedCovers = [])).push(group.cover);
            if (next) { group.cover = next; group.coverTried = false; render(group); return; }
            if (holder) holder.classList.remove('loading');
            image.remove();
            return;
        }
        group.coverTried = true;
        ask({type: 'genericImage', url: group.cover, referer: group.sources[0].url}).then(function (data) {
            if (data && data.dataUrl) group.cover = data.dataUrl;
            else { if (holder) holder.classList.remove('loading'); image.remove(); }
            if (data && data.dataUrl) render(group);
        });
    }

    // -- covers at lower quality (NovelDB's cover-loader): decoded once, kept as a small WebP ----------
    var IMG_KEY = 'noveldb.generic.images';
    var imaging = Object.assign({low: true, quality: 72, resolution: 72}, store(IMG_KEY) || {});
    // A cover's offline-cache key: its address and the compression settings its small copy was made with.
    // "v2": covers made by the older, harsher shrinking are not reused.
    function coverKey(url) { return ['v2', url, imaging.quality, imaging.resolution, Math.round(window.devicePixelRatio || 1)].join('|'); }
    function shrink(group, image) {
        if (!imaging.low || image.dataset.small || !image.naturalWidth) return;
        var original = group.cover;
        // Only a cover much bigger than the card is made smaller: one up to twice the size it is shown at
        // (in device pixels) is kept exactly as the site sent it, since re-encoding a small thumbnail only
        // blurs it. A bigger one shrinks by the resolution setting, never below 1.5 times the shown size.
        var card = image.closest('.cover');
        var shown = Math.max(1, ((card && card.clientWidth) || image.clientWidth || 180) * (window.devicePixelRatio || 1));
        if (image.naturalWidth <= shown * 2) { image.dataset.small = '1'; return; }
        var scale = Math.min(1, Math.max(shown * 1.5 / image.naturalWidth, imaging.resolution / 100));
        var canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        var context = canvas.getContext('2d');
        if (!context) return;
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = 'high';
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        var keepCanvas = function () {
            // A cover from another site without CORS can't be read back: the small canvas stands in for it.
            image.dataset.small = '1';
            if (image.parentNode) image.replaceWith(canvas);
        };
        try {
            canvas.toBlob(function (blob) {
                if (!blob || blob.type !== 'image/webp') { keepCanvas(); return; }
                var reader = new FileReader();
                reader.onload = function () {
                    image.dataset.small = '1';
                    group.cover = reader.result;  // later renders reuse the small copy
                    image.src = reader.result;
                    if (!/^data:/.test(original)) send({type: 'genericCoverPut', key: coverKey(original), dataUrl: reader.result});
                };
                reader.readAsDataURL(blob);
            }, 'image/webp', imaging.quality / 100);
        } catch (e) {
            keepCanvas();
        }
    }
    function showImaging() {
        $('imgLow').checked = !!imaging.low;
        $('imgQuality').value = imaging.quality;
        $('imgResolution').value = imaging.resolution;
        $('imgQualityValue').textContent = imaging.quality + '%';
        $('imgResolutionValue').textContent = imaging.resolution + '%';
        $('imgQuality').disabled = $('imgResolution').disabled = !imaging.low;
    }
    $('language').value = language;
    $('language').addEventListener('change', function () {
        language = $('language').value;
        store(LANG_KEY, language);
        if (view === 'top') showTop();
    });
    ['imgLow', 'imgQuality', 'imgResolution'].forEach(function (id) {
        $(id).addEventListener('input', function () {
            imaging.low = $('imgLow').checked;
            imaging.quality = Number($('imgQuality').value);
            imaging.resolution = Number($('imgResolution').value);
            store(IMG_KEY, imaging);
            showImaging();
            clearTimeout(imaging.timer);
            imaging.timer = setTimeout(function () {
                order.forEach(function (group) {
                    // Back to each book's own cover, to be made again with the new settings.
                    if (group.covers && group.covers.length) group.cover = group.covers[0];
                    group.cacheAsked = false;
                    group.coverTried = false;
                    if (group.element) render(group);
                });
            }, 400);
        });
    });
    $('settingsButton').addEventListener('click', function (event) {
        event.stopPropagation();
        $('sitespanel').classList.remove('open');
        $('settingspanel').classList.toggle('open');
        showImaging();
    });
    document.addEventListener('click', function (event) {
        if (!event.target.closest('.popover')) { $('sitespanel').classList.remove('open'); $('settingspanel').classList.remove('open'); }
    });

    // The title at the largest size (13px down to 7px) that shows all of it in its box: the box is two
    // lines of 13px text tall, and smaller text gets more lines in it, so it shrinks only as far as needed.
    var TITLE_BOX = 2 * 13 * 1.25;
    function fit(title) {
        if (!title || !title.offsetParent) return;
        title.classList.remove('clamp');
        title.style.webkitLineClamp = '';
        for (var size = 13; size >= 7; size -= 0.5) {
            title.style.fontSize = size + 'px';
            if (title.scrollHeight <= TITLE_BOX + 1) return;
        }
        title.classList.add('clamp');
        title.style.webkitLineClamp = String(Math.max(2, Math.floor(TITLE_BOX / (7 * 1.25))));
    }

    function empty() {
        var shown = order.filter(wanted).length;
        var text = '';
        if (!order.length && view === 'top') text = running ? 'Loading the top ' + TOP_NAMES[top.kind] + '…' : 'Nothing in the top lists right now. Type a title, or pick tags with the Tags button.';
        else if (!order.length && !running) text = 'Nothing found.';
        else if (order.length && !shown) text = 'No ' + $('kind').selectedOptions[0].text.toLowerCase() + ' among these.';
        $('empty').textContent = text;
        $('empty').style.display = text ? '' : 'none';
    }

    function toggleCart(group) {
        var url = group.sources[0].url;
        var inCart = group.sources.filter(function (s) { return cart.has(s.url); });
        if (inCart.length) inCart.forEach(function (s) { send({type: 'cartRemove', url: s.url, title: group.title}); });
        else send({type: 'cartAdd', url: url, title: group.title, source: ''});
    }

    function add(list, id) {
        if (id !== undefined && id !== searchId) return;
        (list || []).forEach(function (card) {
            if (card.empty) {
                var known = byUrl.get(key(card.url));
                if (known) drop(known, card.url);
                return;
            }
            if (card.via === 'top') {
                if (view === 'top') {
                    topCollect(card, 1 / Math.sqrt((card.siteRank || 0) + 1), card.position || 0, card.listLength || 1);
                    topRedrawSoon();
                }
                return;
            }
            if (card.via === 'id') card.complete = true;
            // With a title and tags, tag results must match the title too.
            if (card.via === 'tag' && current.text && current.tags.length && !relevant(card.title, current.text)) return;
            var group = merge(card);
            if (group) {
                (card.tags || []).forEach(function (tag) { resultTags.set(tag, (resultTags.get(tag) || 0) + 1); });
                if (card.verified) group.verified = true;
                if (tagOnly() && card.via === 'tag' && !group.verified && !group.confirmed) holdForTag(group);
                render(group);
            }
        });
        if (view === 'search') status(shownCount() + ' books found' + (running ? '…' : '.'));
    }
    function shownCount() { return order.filter(function (g) { return !(g.held && !g.verified); }).length; }

    // -- details for the cards in view -------------------------------------------------------------
    var queue = [], loading = 0;
    var observer = window.IntersectionObserver ? new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            var group = entry.target.__group;
            if (entry.isIntersecting && group && !group.loaded && !group.asked) {
                group.asked = true;
                queue.push(group);
            }
            if (entry.isIntersecting && group) fit(entry.target.querySelector('.title'));
        });
        pump();
    }, {rootMargin: '200px'}) : null;
    function pump() {
        while (loading < (tagOnly() ? 8 : 4) && queue.length) {
            var group = queue.shift();
            if (!groups.has(group.key)) continue;
            loading++;
            send({type: 'genericDetails', id: searchId, card: strip(group, group.sources[0])});
            group.timer = setTimeout(function (g) { finishDetails(g); }.bind(null, group), 45000);
        }
    }
    function finishDetails(group) {
        if (group.timer) { clearTimeout(group.timer); group.timer = 0; loading = Math.max(0, loading - 1); }
        group.loaded = true;
        if (group.element) render(group);
        pump();
    }
    function strip(group, source) {
        return {url: source.url, title: group.title, author: group.author, cover: group.cover, description: group.description,
                tags: group.tags, site: source.site, comic: group.comic, source: source.source, complete: false};
    }

    // -- the details panel -------------------------------------------------------------------------
    function fact(label, value) { return some(value) || /[a-z]/i.test(String(value || '')) ? '<div><span>' + esc(label) + '</span>' + esc(value) + '</div>' : ''; }
    function details(group) {
        var dialog = $('dialog');
        var show = function () {
            var inCart = group.sources.some(function (s) { return cart.has(s.url); });
            var rating = group.rating ? '★ ' + group.rating + (group.ratings ? ' (' + group.ratings + ' ratings)' : '') : '';
            // The panel shows the cover as the site sent it: the card's copy is made small for the card.
            var full = (group.covers || []).filter(function (c) { return !(group.failedCovers || []).includes(c); })[0] || group.cover;
            // The cover with the book's own page under it; the actions show the app's artwork.
            dialog.innerHTML = '<div class="details"><div class="side"><div class="cover">'
                + (full ? '<img referrerpolicy="no-referrer" alt="" src="' + esc(full) + '">' : '') + '</div>'
                + '<button type="button" data-act="open">Source page</button></div>'
                + '<div class="info"><h2>' + esc(group.title) + '</h2>'
                + '<div class="meta">' + esc([group.author, KIND_NAMES[kindOf(group)], group.status].filter(Boolean).join(' · ')) + '</div>'
                + '<div class="facts">' + fact('Rating', rating) + fact('Likes', group.likes) + fact('Views', group.views)
                + fact('Chapters', group.chapters) + fact('Published', group.published) + fact('Updated', group.updated)
                + fact('Status', group.status) + '</div>'
                + '<div class="chips">' + group.tags.map(function (tag) { return '<span class="chip">' + esc(tag) + '</span>'; }).join('') + '</div>'
                + (group.description ? '<div class="desc">' + esc(group.description) + '</div>'
                    : group.loaded ? '<div class="desc">No description on the book page.</div>'
                    : '<div class="loading" style="margin:10px 0"><i></i><i></i><i></i><i></i></div>')
                + (group.sources.length < 2 ? '' : '<div class="sources"><h4>On ' + group.sources.length + ' sites</h4>' + group.sources.map(function (source, index) {
                    return '<div><span>' + esc(source.site) + (source.ms != null ? ' · ' + seconds(source.ms) : '')
                        + (index === 0 ? ' (first to answer)' : '') + '</span>'
                        + '<button type="button" data-read="' + index + '">Read</button>'
                        + '<button type="button" data-download="' + index + '">Download</button>'
                        + '<button type="button" data-open="' + index + '">Source</button></div>';
                }).join('') + '</div>') + '</div></div>'
                + '<div class="actions">'
                + '<button type="button" class="primary icon" data-act="read"><img src="icon-mining.webp" alt="">Read now</button>'
                + '<button type="button" class="primary icon" data-act="download">'
                + (config.downloadIcon ? '<img src="' + config.downloadIcon + '" alt="">' : '') + 'Download</button>'
                + '<button type="button" class="icon' + (inCart ? ' on' : '') + '" data-act="cart"><img src="icon-cart.webp" alt="">'
                + (inCart ? 'In cart' : 'Add to cart') + '</button>'
                + '<button type="button" data-act="close">Close</button></div>';
            var image = dialog.querySelector('img');
            if (image) image.addEventListener('error', function () {
                // The site refuses its cover to this page: the card's copy, which the card did load.
                if (full !== group.cover && /^data:/.test(group.cover || '')) { image.src = group.cover; return; }
                // The same way back as the card's cover: the app, then the book's other sites.
                var cardImage = group.element && group.element.querySelector('.cover img');
                if (cardImage) coverFailed(group, cardImage); else image.remove();
                setTimeout(function () { if (group.onDetails && $('dialog').open) group.onDetails(); }, 1500);
            }, {once: true});
            dialog.querySelector('[data-act=read]').onclick = function () { dialog.close(); openReader(group, 0); };
            dialog.querySelector('[data-act=download]').onclick = function () { download(group, group.sources[0]); };
            dialog.querySelector('[data-act=cart]').onclick = function () { toggleCart(group); };
            dialog.querySelector('[data-act=close]').onclick = function () { dialog.close(); };
            var openPage = dialog.querySelector('[data-act=open]');
            if (openPage) openPage.onclick = function () { send({type: 'open', url: group.sources[0].url, title: group.title}); };
            dialog.querySelectorAll('[data-read]').forEach(function (button) {
                button.onclick = function () { dialog.close(); openReader(group, Number(button.dataset.read)); };
            });
            dialog.querySelectorAll('[data-download]').forEach(function (button) {
                button.onclick = function () { download(group, group.sources[Number(button.dataset.download)]); };
            });
            dialog.querySelectorAll('[data-open]').forEach(function (button) {
                button.onclick = function () { var s = group.sources[Number(button.dataset.open)]; send({type: 'open', url: s.url, title: group.title}); };
            });
            dialog.querySelectorAll('.chip').forEach(function (chip) {
                chip.onclick = function () { dialog.close(); picked = [chip.textContent]; $('query').value = ''; showPicked(); search(); };
            });
        };
        group.onDetails = show;
        show();
        if (!dialog.open) dialog.showModal();
        if (!group.loaded && !group.asked) { group.asked = true; queue.unshift(group); pump(); }
    }
    function download(group, source) {
        send({type: 'genericDownload', url: source.url, title: group.title});
    }

    // -- LNReader: the sites' own search, run here --------------------------------------------------
    function lnreaderRun(id, jobs, each) {
        var index = 0, active = 0;
        setRunning(1);
        return new Promise(function (resolve) {
            function next() {
                if (id !== searchId) { setRunning(-1); resolve(); return; }
                while (active < 8 && index < jobs.length) {
                    var job = jobs[index++];
                    active++;
                    job.started = Date.now();
                    within(job.run(), 25000, '"timeout"').then(function (raw) {
                        var found = parse(raw), ms = Date.now() - this.started;
                        if (Array.isArray(found) && id === searchId) {
                            found.forEach(function (item) { item.ms = ms; });
                            each(this, found);
                        }
                        if (id === searchId && this.report !== false) {
                            siteAnswered({site: this.plugin.site, ms: ms, found: Array.isArray(found) ? found.length : 0,
                                          state: found === 'timeout' ? 'timeout' : Array.isArray(found) ? 'ok' : 'failed'});
                        }
                    }.bind(job)).catch(function () {}).then(function () { active--; next(); });
                }
                if (active === 0 && index >= jobs.length) { setRunning(-1); resolve(); }
            }
            next();
        });
    }
    function lnreaderCard(plugin, item, via) {
        return {url: item.url, title: item.name, cover: item.cover || '', site: plugin.site, comic: !!plugin.comic,
                source: 'lnreader', plugin: item.plugin || plugin.id, path: item.path, via: via, complete: false, ms: item.ms,
                verified: !!item.verified};
    }
    function lnreaderSearch(id, queries) {
        var every = plugins();
        if (!every.length || !window.__LNR_search) return;
        var jobs = [];
        queries.forEach(function (q) {
            every.forEach(function (plugin) {
                jobs.push({plugin: plugin, query: q, run: function () {
                    return q.mode === 'tag' && window.__LNR_genre ? window.__LNR_genre(plugin.id, q.query) : window.__LNR_search(plugin.id, q.query, 1);
                }});
            });
        });
        progress.expected += jobs.length;
        lnreaderRun(id, jobs, function (job, found) {
            if (job.query.mode !== 'tag') found = found.filter(function (item) { return relevant(item.name, job.query.query); });
            add(found.slice(0, 15).map(function (item) { return lnreaderCard(job.plugin, item, job.query.mode); }), id);
        }).then(function () { finishedSearch(id); });
    }

    // -- the top 100s: the sites' popular lists, the busiest sites counting most ----------------------
    // Web novels (and All) come from the LNReader plugins' popular lists, light novels are the ones of those
    // lists that are light novels, and manga, manhwa and comics come from the comic sites' popular or
    // ranking pages, read by the app. Each list is kept for 12 hours.
    var TOP_NAMES = {all: 'web novels', webnovel: 'web novels', lightnovel: 'light novels', manga: 'manga',
                     manhwa: 'manhwa', comic: 'comics'};
    var top = {kind: 'webnovel', scores: new Map(), first: 0, timer: 0};
    function topKind() { var kind = $('kind').value; return kind === 'all' ? 'webnovel' : kind; }
    function showTop() {
        searchId++;
        reset('top');
        current = {text: '', tags: []};
        top = {kind: topKind(), scores: new Map(), first: 0, timer: 0};
        var chosen = $('language').selectedOptions[0];
        $('heading').textContent = 'Top 100 ' + TOP_NAMES[top.kind] + (language === 'all' ? '' : ' · ' + chosen.text);
        var cached = store(TOP_KEY + '.' + top.kind + '.' + language);
        if (cached && cached.items && cached.items.length) {
            cached.items.forEach(function (item, index) {
                if (notInTop(item)) return;
                var group = merge(item, index + 1);
                if (group) { (item.more || []).forEach(function (m) { merge(m); }); render(group); }
            });
            status('The most read ' + TOP_NAMES[top.kind] + ' on the top sites, as of ' + new Date(cached.time).toLocaleString() + '.');
            if (Date.now() - cached.time < TOP_HOURS * 3600e3) { empty(); return; }
        }
        loadTop(searchId);
    }
    // Kept out of the top lists (a search still finds them): a book whose tags or title say so.
    var NOT_IN_TOP = /(yaoi|smut)/i;
    function notInTop(item) {
        return NOT_IN_TOP.test(item.title || '') || (item.tags || []).some(function (tag) { return NOT_IN_TOP.test(tag); });
    }
    function dropFromTop(group) {
        group.sources.slice().forEach(function (source) { drop(group, source.url); });
        topRedrawSoon();  // the next book in line takes its place
    }
    function topCollect(card, weight, position, length) {
        if (!card.title || JUNK.test(card.title) || NO_RESULTS.test(card.title) || !inLanguage(card) || notInTop(card)) return;
        var k = groupKey(card);
        var entry = top.scores.get(k) || {key: k, score: 0, first: top.first++, cards: []};
        entry.score += weight * (1 - 0.7 * position / Math.max(1, length));
        entry.cards.push(card);
        top.scores.set(k, entry);
    }
    // The list as books arrive: a new book goes at the end and stays where it is (no reshuffling as more
    // sites answer); a book another site lists too just gains that site. It stops at 100 books.
    function topRedraw() {
        var entries = Array.from(top.scores.values()).sort(function (a, b) { return a.first - b.first; });
        entries.forEach(function (entry) {
            if (entry.excluded) return;
            if (entry.group && !groups.has(entry.group.key)) { entry.excluded = true; return; }  // dropped from the list
            if (!entry.group) {
                if (order.length >= TOP_SIZE) return;
                var group = merge(entry.cards[0]);
                if (!group) return;
                group.topKind = top.kind;
                entry.group = group;
                entry.used = 1;
            }
            entry.cards.slice(entry.used).forEach(function (card) { merge(card); });
            entry.used = entry.cards.length;
            render(entry.group);
        });
        empty();
    }
    function topRedrawSoon() {
        clearTimeout(top.timer);
        top.timer = setTimeout(topRedraw, 250);
    }
    function topSave() {
        var items = order.map(function (group) {
            var first = group.sources[0];
            // The site's own cover address, not the small copy made here (that one follows the settings).
            var original = (group.covers && group.covers[0]) || (/^data:/.test(group.cover) ? '' : group.cover);
            return Object.assign({}, strip(group, first), {cover: original, plugin: first.plugin, path: first.path, ms: first.ms,
                more: group.sources.slice(1).map(function (s) {
                    return {url: s.url, title: group.title, site: s.site, source: s.source, plugin: s.plugin, path: s.path, comic: group.comic, ms: s.ms};
                })});
        });
        if (items.length) store(TOP_KEY + '.' + top.kind + '.' + language, {time: Date.now(), items: items});
    }
    function loadTop(id) {
        if (top.kind === 'manga' || top.kind === 'manhwa' || top.kind === 'comic') {
            // The comic sites' popular pages, read by the app (cards come back through add()).
            running = 0;
            setRunning(1);
            startProgress(0);
            status('Reading the popular pages of the top ' + TOP_NAMES[top.kind] + ' sites…');
            send({type: 'genericTop', id: id, kind: top.kind, language: language});
            return;
        }
        var wantLn = top.kind === 'lightnovel';
        var chosen = plugins().filter(function (p) {
            return !p.comic && (language === 'all' || !pluginLangs[p.id] || pluginLangs[p.id] === language)
                && (allPlugins.length ? !!p.ln === wantLn : true);
        }).sort(function (a, b) { return (a.rank < 0 ? 999 : a.rank) - (b.rank < 0 ? 999 : b.rank); }).slice(0, 60);
        if (!chosen.length || !window.__LNR_popular) { empty(); return; }
        status('Reading the popular lists of ' + chosen.length + ' ' + TOP_NAMES[top.kind] + ' sites…');
        var jobs = chosen.map(function (plugin, siteIndex) {
            // The busiest sites (the top 100 list) count most; the rest count alike.
            var weight = plugin.rank >= 0 || !allPlugins.length ? 1 / Math.sqrt((plugin.rank >= 0 ? plugin.rank : siteIndex) + 1) : 0.3;
            return {plugin: plugin, weight: weight, report: true,
                    run: function () { return window.__LNR_popular(plugin.id, 1, false); }};
        });
        var plugins_ = chosen;
        startProgress(jobs.length);
        lnreaderRun(id, jobs, function (job, found) {
            found.forEach(function (item, position) { topCollect(lnreaderCard(job.plugin, item, 'top'), job.weight, position, found.length); });
            if (id === searchId) topRedrawSoon();
        }).then(function () {
            if (id !== searchId) return;
            topRedraw();
            topSave();
            status(order.length ? 'The most read ' + TOP_NAMES[top.kind] + ' on ' + plugins_.length + ' ' + TOP_NAMES[top.kind] + ' sites, by their popular lists.'
                : 'No ' + TOP_NAMES[top.kind] + ' in the popular lists right now.');
            empty();
        });
    }

    // -- searching ---------------------------------------------------------------------------------
    function allTags() {
        var names = new Map();
        TAGS.forEach(function (tag) { names.set(tag.toLowerCase(), tag); });
        resultTags.forEach(function (_n, tag) { if (!names.has(tag.toLowerCase())) names.set(tag.toLowerCase(), tag); });
        return names;
    }
    function search(event) {
        if (event) event.preventDefault();
        closeTags();
        var text = $('query').value.trim();
        if (!text && !picked.length) { showTop(); return; }
        var queries = [];
        var byId = !picked.length && /^\s*(\d{3,12}|n\d{4}[a-z]{1,3})\s*$/i.test(text);
        if (byId) {
            queries.push({query: text, mode: 'id'});  // a novel ID: its book page on the sites that address books by ID
        } else if (text) {
            queries.push({query: text, mode: 'name'});
            if (!picked.length && allTags().has(text.toLowerCase())) queries.push({query: text, mode: 'tag'});
        }
        picked.slice(0, 5).forEach(function (tag) { queries.push({query: tag, mode: 'tag'}); });
        searchId++;
        reset('search');
        current = {text: text, tags: picked.slice()};
        running = 0;
        setRunning(1);
        var label = [text ? (byId ? 'ID ' : '') + '“' + text + '”' : ''].concat(picked.map(function (t) { return '#' + t; })).filter(Boolean).join(' + ');
        $('heading').innerHTML = 'Results for ' + esc(label) + ' <a id="backTop">Top 100</a>';
        $('backTop').onclick = function () { $('query').value = ''; picked = []; showPicked(); showTop(); };
        startProgress(0);
        status('Searching ' + ((config.sites.length || 100) + plugins().length) + ' sites for ' + label + '…');
        empty();
        send({type: 'genericSearch', id: searchId, queries: queries});
        if (!byId) lnreaderSearch(searchId, queries);
    }
    function finishedSearch(id) {
        if (id !== searchId || running) return;
        status(shownCount() + ' books found in ' + seconds(Date.now() - progress.started) + '.');
        empty();
    }

    // -- the tag menu ------------------------------------------------------------------------------
    function showPicked() {
        $('picked').innerHTML = picked.map(function (tag, index) {
            return '<span class="picked">' + esc(tag) + '<b data-unpick="' + index + '" title="Remove">×</b></span>';
        }).join('');
        $('picked').querySelectorAll('[data-unpick]').forEach(function (x) {
            x.onclick = function () { picked.splice(Number(x.dataset.unpick), 1); showPicked(); drawTags(); };
        });
        $('tagCount').innerHTML = picked.length ? '<span class="badge">' + picked.length + '</span>' : '';
        $('tagsButton').classList.toggle('on', picked.length > 0);
    }
    function togglePick(tag) {
        var at = picked.findIndex(function (t) { return t.toLowerCase() === tag.toLowerCase(); });
        if (at >= 0) picked.splice(at, 1); else picked.push(tag);
        showPicked();
        drawTags();
    }
    function chipList(tags, counts) {
        var on = new Set(picked.map(function (t) { return t.toLowerCase(); }));
        return '<div class="chips">' + tags.map(function (tag) {
            return '<span class="chip' + (on.has(tag.toLowerCase()) ? ' on' : '') + '" data-tag="' + esc(tag) + '">' + esc(tag)
                + (counts && counts.get(tag) ? '<small>' + counts.get(tag) + '</small>' : '') + '</span>';
        }).join('') + '</div>';
    }
    function drawTags() {
        var filter = $('tagFilter').value.trim().toLowerCase();
        var match = function (tag) { return !filter || tag.toLowerCase().indexOf(filter) >= 0; };
        var common = TAGS.filter(match);
        var found = Array.from(resultTags.keys()).filter(function (tag) {
            return match(tag) && !TAGS.some(function (t) { return t.toLowerCase() === tag.toLowerCase(); });
        }).sort(function (a, b) { return resultTags.get(b) - resultTags.get(a); }).slice(0, 60);
        var html = '';
        if (picked.length) html += '<h3>Picked</h3>' + chipList(picked);
        if (common.length) html += '<h3>Genres and tags</h3>' + chipList(common);
        if (found.length) html += '<h3>In these results</h3>' + chipList(found, resultTags);
        if (!common.length && !found.length && filter) html += '<p>Press Enter to pick “' + esc($('tagFilter').value.trim()) + '”.</p>';
        $('tagLists').innerHTML = html;
        $('tagLists').querySelectorAll('[data-tag]').forEach(function (chip) {
            chip.onclick = function () { togglePick(chip.dataset.tag); };
        });
    }
    function closeTags() { $('tagmenu').classList.remove('open'); }
    $('tagsButton').addEventListener('click', function () {
        var menu = $('tagmenu');
        menu.classList.toggle('open');
        if (menu.classList.contains('open')) { drawTags(); $('tagFilter').focus(); }
    });
    $('tagFilter').addEventListener('input', drawTags);
    $('tagFilter').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            var text = $('tagFilter').value.trim();
            if (text) { togglePick(allTags().get(text.toLowerCase()) || text); $('tagFilter').value = ''; drawTags(); }
        } else if (event.key === 'Escape') closeTags();
    });
    $('tagClear').addEventListener('click', function () { picked = []; showPicked(); drawTags(); });
    $('tagSearch').addEventListener('click', function () { search(); });
    document.addEventListener('click', function (event) {
        if (!event.target.closest('#tagmenu') && !event.target.closest('#tagsButton') && !event.target.closest('[data-unpick]')) closeTags();
    });

    $('form').addEventListener('submit', search);
    // Clearing the search box (and with no tags picked) goes back to the Top 100.
    $('query').addEventListener('input', function () {
        if (!$('query').value.trim() && !picked.length && view === 'search') {
            send({type: 'genericStop', id: searchId});
            showTop();
        }
    });
    $('stop').addEventListener('click', function () {
        send({type: 'genericStop', id: searchId});
        searchId++;
        running = 0;
        setRunning(0);
        status(shownCount() + ' books found (stopped).');
    });
    $('kind').addEventListener('change', function () {
        if (view === 'top' && topKind() !== top.kind) { showTop(); return; }
        order.forEach(function (group) {
            if (group.element) group.element.style.display = wanted(group) ? '' : 'none';
            if (group.element) fit(group.element.querySelector('.title'));
        });
        empty();
    });
    window.addEventListener('resize', function () {
        clearTimeout(window.__fitTimer);
        window.__fitTimer = setTimeout(function () { order.forEach(function (g) { g.element && fit(g.element.querySelector('.title')); }); }, 150);
    });

    // == the reader ===============================================================================
    // One chapter at a time, scraped when it is opened (the next one is read ahead). Paged like an
    // e-book (two pages side by side in a wide window) or one long scroll; the place is remembered.
    var R = {group: null, source: null, chapters: [], index: 0, page: 0, pages: 1, cache: new Map(), token: 0, lnreader: null};
    var LOOK_KEY = 'noveldb.reader.look.v2', look = Object.assign({size: 19, leading: 1.75, font: 'serif', theme: 'auto', layout: 'paged'}, store(LOOK_KEY) || {});
    var darkPage = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    function theme() { return look.theme === 'auto' ? (darkPage && darkPage.matches ? 'dark' : 'light') : look.theme; }
    // Comics read as one scroll of full-width images; novels in the layout picked.
    function layout() { return R.comic ? 'scroll' : look.layout; }
    if (darkPage && darkPage.addEventListener) darkPage.addEventListener('change', function () { if ($('reader').classList.contains('open')) applyLook(); });

    function applyLook() {
        var reader = $('reader');
        reader.style.setProperty('--r-size', look.size + 'px');
        reader.style.setProperty('--r-leading', look.leading);
        reader.classList.toggle('sans', look.font === 'sans');
        reader.classList.toggle('sepia', theme() === 'sepia');
        reader.classList.toggle('dark', theme() === 'dark');
        reader.classList.toggle('scroll', layout() === 'scroll');
        reader.classList.toggle('comic', !!R.comic);
        document.querySelectorAll('#look [data-look]').forEach(function (button) {
            var name = button.dataset.look;
            button.classList.toggle('on', name === look.font || name === look.theme || name === layout());
            if (name === 'paged') button.disabled = !!R.comic;
        });
        store(LOOK_KEY, look);
    }
    document.querySelectorAll('#look [data-look]').forEach(function (button) {
        button.addEventListener('click', function () {
            var name = button.dataset.look, at = readingAt();
            if (name === 'smaller') look.size = Math.max(12, look.size - 1);
            else if (name === 'larger') look.size = Math.min(34, look.size + 1);
            else if (name === 'tighter') look.leading = Math.max(1.2, Math.round((look.leading - 0.1) * 10) / 10);
            else if (name === 'looser') look.leading = Math.min(2.6, Math.round((look.leading + 0.1) * 10) / 10);
            else if (name === 'serif' || name === 'sans') look.font = name;
            else if (name === 'auto' || name === 'light' || name === 'sepia' || name === 'dark') look.theme = name;
            else look.layout = name;
            applyLook();
            paginate(at);
        });
    });

    function lnreaderSite(source) {
        if (source.source === 'lnreader' && source.plugin) return true;
        return (config.lnreader || []).some(function (p) { return p.site === source.site; });
    }

    // HTML from a site, kept to text, headings, lists, tables and images (no scripts, styles or links).
    var KEEP = /^(p|br|hr|h[1-6]|em|strong|b|i|u|s|sub|sup|small|blockquote|pre|code|ul|ol|li|table|thead|tbody|tr|td|th|img|ruby|rt|rp|figure|figcaption|center)$/;
    var DROP = /^(script|style|iframe|frame|object|embed|form|input|button|select|textarea|svg|math|link|meta|noscript|template|video|audio|canvas|nav|header|footer|aside|ins)$/;
    function clean(html, base) {
        var dom = new DOMParser().parseFromString('<div id="r">' + String(html || '') + '</div>', 'text/html');
        var root = dom.getElementById('r');
        (function walk(node) {
            Array.prototype.slice.call(node.childNodes).forEach(function (child) {
                if (child.nodeType === 8) { child.remove(); return; }
                if (child.nodeType !== 1) return;
                var tag = child.tagName.toLowerCase();
                if (DROP.test(tag)) { child.remove(); return; }
                walk(child);
                if (!KEEP.test(tag)) {
                    // A block that isn't one of ours becomes a paragraph when it holds text, else its contents stay.
                    if (/^(div|section|article|main)$/.test(tag) && !child.querySelector('p, div, img, h1, h2, h3, table, ul, ol') && child.textContent.trim()) {
                        var p = dom.createElement('p');
                        while (child.firstChild) p.appendChild(child.firstChild);
                        child.replaceWith(p);
                    } else {
                        child.replaceWith.apply(child, Array.prototype.slice.call(child.childNodes));
                    }
                    return;
                }
                var src = tag === 'img' ? (child.getAttribute('data-src') || child.getAttribute('data-original') || child.getAttribute('src') || '') : '';
                Array.prototype.slice.call(child.attributes).forEach(function (attribute) { child.removeAttribute(attribute.name); });
                if (tag === 'img') {
                    try { src = new URL(src, base).href; } catch (e) { src = ''; }
                    if (!/^(https?:|data:image\/)/i.test(src)) { child.remove(); return; }
                    child.setAttribute('src', src);
                    child.setAttribute('referrerpolicy', 'no-referrer');
                    child.setAttribute('loading', 'eager');
                }
            });
        })(root);
        return root.innerHTML;
    }
    function textHtml(text) {
        return String(text || '').split(/\n+/).map(function (line) { return line.trim(); }).filter(Boolean)
            .map(function (line) { return '<p>' + esc(line) + '</p>'; }).join('');
    }

    function openReader(group, sourceIndex) {
        var reader = $('reader');
        R.group = group;
        R.blocked = '';
        R.comic = !!group.comic;
        R.token++;
        R.cache = new Map();
        R.chapters = [];
        reader.classList.add('open');
        document.body.style.overflow = 'hidden';
        applyLook();
        $('rBook').textContent = group.title;
        $('rChapter').textContent = '';
        tryBook(group, sourceIndex || 0, R.token, []);
    }
    // What the reader is doing, with a clock; after `limit` seconds it gives up with a way out.
    function note(text, limit, retry) {
        clearInterval(R.clock);
        var started = Date.now(), token = R.token;
        R.noteText = text;
        R.noteDetail = '';
        var draw = R.drawNote = function () {
            if (token !== R.token || R.streamed === R.index) { clearInterval(R.clock); return; }
            var seconds = Math.round((Date.now() - started) / 1000);
            if (limit && seconds >= limit) {
                clearInterval(R.clock);
                failed(text.replace(/…$/, '') + ' took too long (' + limit + ' s).', retry);
                return;
            }
            $('flow').innerHTML = '<p class="note">' + esc(text) + (seconds >= 2 ? ' <span class="clock">' + seconds + ' s</span>' : '')
                + (R.noteDetail ? '<br><span class="detail">' + esc(R.noteDetail) + '</span>' : '') + '</p>';
        };
        draw();
        if (limit) R.clock = setInterval(draw, 1000);
        R.pages = 1; R.page = 0;
        place();
    }
    // A failure, with Retry, the page itself, and the book's other sites.
    function failed(message, retry, blocked) {
        clearInterval(R.clock);
        var others = R.group ? R.group.sources.filter(function (s) { return !R.source || s.url !== R.source.url; }) : [];
        var page = R.source || (R.group && R.group.sources[R.tried || 0]);
        $('flow').innerHTML = '<p class="note">' + esc(message) + '</p><p class="next-chapter">'
            + (blocked && page ? '<button type="button" id="rLogin" class="primary">Sign in with Enter Browser</button> ' : '')
            + (retry ? '<button type="button" id="rRetry">Try again</button> ' : '')
            + (R.source ? '<button type="button" id="rOpen">Open the page</button> ' : '')
            + others.map(function (s, i) { return '<button type="button" data-other="' + i + '">Read on ' + esc(s.site) + '</button> '; }).join('')
            + '</p>';
        if (retry) $('rRetry').onclick = retry;
        if ($('rLogin')) $('rLogin').onclick = function () { send({type: 'genericLogin', url: page.url}); };
        if ($('rOpen')) $('rOpen').onclick = function () { send({type: 'open', url: R.source.url, title: R.group.title}); };
        $('flow').querySelectorAll('[data-other]').forEach(function (button) {
            button.onclick = function () {
                var source = others[Number(button.dataset.other)];
                openReader(R.group, R.group.sources.indexOf(source));
            };
        });
        R.pages = 1; R.page = 0;
        place();
    }
    // The chapter list from the chosen site, else from the book's other sites in turn.
    function tryBook(group, start, token, errors) {
        var order = group.sources.slice(start).concat(group.sources.slice(0, start));
        var attempt = function (i) {
            if (token !== R.token) return;
            if (i >= order.length) {
                R.source = null;
                failed(R.blocked || 'The book could not be read: ' + errors.join('; '), function () { openReader(group, start); }, !!R.blocked);
                return;
            }
            R.tried = group.sources.indexOf(order[i]);
            var source = order[i];
            note('Reading the chapter list from ' + source.site + '…', 0);
            loadBook(source).then(function (book) {
                if (token !== R.token) return;
                if (!book || book.error || !book.chapters || !book.chapters.length) {
                    if (book && book.blocked) R.blocked = book.message;
                    errors.push(source.site + ': ' + ((book && book.error) || 'no chapters'));
                    attempt(i + 1);
                    return;
                }
                R.source = source;
                R.chapters = book.chapters;
                R.lnreader = book.lnreader || null;
                var saved = store('noveldb.read.' + key(source.url)) || {};
                drawToc();
                openChapter(Math.min(saved.chapter || 0, R.chapters.length - 1), saved.at || 0);
            });
        };
        attempt(0);
    }
    // An app request that gives up after `seconds` (the reader shows what failed instead of waiting forever).
    function askWithin(message, seconds) {
        return within(ask(message, function (id) { R.requestId = id; }), seconds * 1000,
                      {error: 'the site did not answer in ' + seconds + ' s'});
    }
    function loadBook(source) {
        if (lnreaderSite(source) && window.__LNR_parseBook) {
            return within(window.__LNR_parseBook(source.url), 60000, '{"error":"the site took too long"}').then(function (raw) {
                var book = parse(raw) || {};
                if (book.error || !(book.chapters || []).length) return askWithin({type: 'genericBook', url: source.url}, 90);
                return {lnreader: book, chapters: book.chapters.map(function (c, i) { return {name: c.name || 'Chapter ' + (i + 1), section: c.sectionName || ''}; })};
            });
        }
        return askWithin({type: 'genericBook', url: source.url}, 90);
    }
    function loadChapter(index) {
        if (R.cache.has(index)) return R.cache.get(index);
        var source = R.source, promise;
        if (R.lnreader) {
            var item = Object.assign({}, R.lnreader.chapters[index]);
            promise = within(window.__LNR_parseChapter(JSON.stringify(item)), 60000, '{"error":"the site took too long"}').then(parse);
        } else {
            promise = askWithin({type: 'genericChapter', url: source.url, index: index}, 90);
        }
        promise = promise.then(function (result) {
            if (!result || result.error) {
                R.cache.delete(index);
                return {error: (result && result.error) || 'the chapter could not be read', blocked: !!(result && result.blocked),
                        message: result && result.message};
            }
            var html = result.html || result.contentHtml ? clean(result.html || result.contentHtml, source.url) : '';
            if (!html.replace(/<[^>]+>/g, '').trim() && html.indexOf('<img') < 0) html = textHtml(result.text || result.contentText);
            return {name: result.name || result.chapterName || R.chapters[index].name, html: html};
        });
        R.cache.set(index, promise);
        return promise;
    }
    function openChapter(index, at) {
        var token = R.token;
        R.streamed = -1;
        resetPictures();
        R.index = index;
        $('rChapter').textContent = '· ' + (R.chapters[index].name || '');
        $('rChapterNo').textContent = 'Chapter ' + (index + 1) + ' of ' + R.chapters.length;
        markToc();
        var retry = function () { R.cache.delete(index); openChapter(index, at); };
        note('Scraping chapter ' + (index + 1) + (R.chapters[index].name ? ' (' + R.chapters[index].name + ')' : '') + ' from '
             + R.source.site + '…', 90, retry);
        loadChapter(index).then(function (chapter) {
            if (token !== R.token || index !== R.index) return;
            clearInterval(R.clock);
            if (chapter.error) {
                failed(chapter.blocked ? chapter.message : 'Chapter ' + (index + 1) + ' could not be read: ' + chapter.error, retry, chapter.blocked);
                return;
            }
            if (R.streamed === index) { R.streamed = -1; finishStream(chapter); return; }
            var last = index + 1 < R.chapters.length;
            var many = (chapter.html.match(/<img/g) || []).length >= 4;
            if (many) {
                R.streamed = -1;
                resetPictures();
                chapter.html = chapter.html.replace(/<img[^>]*?src="([^"]+)"[^>]*>/g, function (_tag, src) { return slotHtml(src); });
            }
            $('flow').innerHTML = '<h2 class="chapter">' + esc(chapter.name) + '</h2>' + chapter.html
                + (layout() === 'scroll' && last ? '<p class="next-chapter"><button type="button" id="rNextChapter">Next chapter ›</button></p>' : '');
            if ($('rNextChapter')) $('rNextChapter').onclick = function () { openChapter(index + 1, 0); };
            if (many) {
                $('flow').querySelectorAll('img.slot').forEach(function (slot) { fetchPicture(slot); });
                pictures.total = $('flow').querySelectorAll('img.slot').length;
                showPictures();
            }
            var images = $('flow').querySelectorAll('img:not(.slot)'), loadedImages = 0;
            var imageNote = function () {
                if (!images.length) return;
                $('rWhere').textContent = loadedImages < images.length
                    ? 'Images ' + loadedImages + ' of ' + images.length + '…' : (layout() === 'scroll' ? '' : 'Page ' + (R.page + 1) + ' of ' + R.pages);
            };
            imageNote();
            images.forEach(function (image) {
                var counted = function () { loadedImages++; imageNote(); };
                image.addEventListener('load', counted, {once: true});
                image.addEventListener('error', counted, {once: true});
                image.addEventListener('load', function () { paginate(readingAt()); }, {once: true});
                image.addEventListener('error', function () {
                    ask({type: 'genericImage', url: image.src, referer: R.source.url}).then(function (data) {
                        if (data && data.dataUrl) image.src = data.dataUrl; else image.remove();
                    });
                }, {once: true});
            });
            $('viewport').scrollTop = 0;
            paginate(at === 'end' ? 1 : at || 0, at === 'end');
            if (last) loadChapter(index + 1);  // read ahead
        });
    }
    // -- chapter images: fetched by the app (no hotlink refusals), 4 at a time, counted ------------------
    var pictures = {queue: [], active: 0, done: 0, total: 0, bytes: 0, started: 0, urls: []};
    function resetPictures() {
        pictures.urls.forEach(function (url) { URL.revokeObjectURL(url); });
        pictures = {queue: [], active: 0, done: 0, total: 0, bytes: 0, started: Date.now(), urls: [], token: R.token};
        showPictures();
    }
    function showPictures() {
        var bar = $('rProgress');
        if (!pictures.total || pictures.done >= pictures.total) { bar.classList.remove('on'); return; }
        bar.classList.add('on');
        var seconds = Math.max(0.5, (Date.now() - pictures.started) / 1000);
        bar.firstElementChild.style.width = (100 * pictures.done / pictures.total) + '%';
        bar.lastElementChild.textContent = 'Images ' + pictures.done + ' of ' + pictures.total + ' · '
            + (pictures.bytes / 1048576).toFixed(1) + ' MB · ' + (pictures.bytes / 1048576 / seconds).toFixed(2) + ' MB/s';
    }
    function fetchPicture(image) {
        pictures.queue.push(image);
        pumpPictures();
    }
    function pumpPictures() {
        while (pictures.active < 4 && pictures.queue.length) {
            var image = pictures.queue.shift(), token = pictures.token;
            pictures.active++;
            ask({type: 'genericImage', url: image.dataset.src, referer: R.source ? R.source.url : ''}).then(function (data) {
                if (token !== pictures.token) return;
                pictures.active--;
                pictures.done++;
                if (data && data.dataUrl) {
                    pictures.bytes += data.bytes || Math.round(data.dataUrl.length * 0.75);
                    fetch(data.dataUrl).then(function (r) { return r.blob(); }).then(function (blob) {
                        var url = URL.createObjectURL(blob);
                        pictures.urls.push(url);
                        this.src = url;
                        this.classList.add('ready');
                    }.bind(this));
                } else {
                    this.src = this.dataset.src;  // the browser itself, as a last try
                    this.classList.add('ready');
                }
                showPictures();
                pumpPictures();
            }.bind(image));
        }
        showPictures();
    }
    // A chapter of images as slots: each shows Halgakos until its image arrives.
    function slotHtml(src) { return '<img class="slot" alt="" data-src="' + esc(src || '') + '">'; }
    function streamImages(index, found) {
        if (index !== R.index) return;
        if (R.streamed !== index) {
            R.streamed = index;
            clearInterval(R.clock);
            resetPictures();
            $('flow').innerHTML = '<h2 class="chapter">' + esc(R.chapters[index].name || '') + '</h2>';
            R.slots = [];
        }
        var flow = $('flow');
        while (R.slots.length < found.total) {
            flow.insertAdjacentHTML('beforeend', slotHtml(''));
            R.slots.push(flow.lastElementChild);
        }
        pictures.total = Math.max(found.total, found.images.length);
        found.images.forEach(function (src, i) {
            var slot = R.slots[i];
            if (!slot) { flow.insertAdjacentHTML('beforeend', slotHtml('')); slot = flow.lastElementChild; R.slots.push(slot); }
            if (!slot.dataset.src) { slot.dataset.src = src; fetchPicture(slot); }
        });
        showPictures();
    }
    function finishStream(chapter) {
        // The whole chapter is in: slots that never got an image go, the rest keep loading.
        var images = (chapter.html.match(/<img[^>]+src="([^"]+)"/g) || []).map(function (tag) { return tag.match(/src="([^"]+)"/)[1]; });
        streamImages(R.index, {images: images, total: images.length});
        R.slots.slice(images.length).forEach(function (slot) { slot.remove(); });
        pictures.total = images.length;
        showPictures();
        paginate(0);
        if (R.index + 1 < R.chapters.length) loadChapter(R.index + 1);
    }

    function columns() { return $('viewport').clientWidth > 1100 ? 2 : 1; }
    function paginate(at, toEnd) {
        var flow = $('flow'), viewport = $('viewport');
        if (layout() === 'scroll') {
            flow.style.width = ''; flow.style.columnWidth = ''; flow.style.columnGap = ''; flow.style.padding = '';
            R.pages = 1; R.page = 0;
            viewport.scrollTop = (at || 0) * Math.max(0, flow.scrollHeight - viewport.clientHeight);
            place();
            return;
        }
        // Each page is `width` wide. CSS columns share one gap, so the gap between pages (the side
        // margins of two pages) is also the gap between the two columns of a spread.
        var width = viewport.clientWidth, cols = columns(), gap, column;
        if (cols === 2) {
            gap = Math.max(48, Math.round(width * 0.05));
            column = width / 2 - gap;
        } else {
            gap = 2 * Math.max(24, Math.round((width - 760) / 2));
            column = width - gap;
        }
        flow.style.width = width + 'px';
        flow.style.padding = '28px ' + (gap / 2) + 'px';
        flow.style.columnGap = gap + 'px';
        flow.style.columnWidth = column + 'px';
        flow.style.transition = 'none';
        flow.style.transform = 'translateX(0)';
        R.pageWidth = width;
        R.pages = Math.max(1, Math.ceil((flow.scrollWidth - 4) / width));
        R.page = toEnd ? R.pages - 1 : Math.min(R.pages - 1, Math.round((at || 0) * (R.pages - 1)));
        place();
        requestAnimationFrame(function () { flow.style.transition = ''; });
    }
    function readingAt() {
        if (layout() === 'scroll') {
            var v = $('viewport'), f = $('flow');
            return f.scrollHeight > v.clientHeight ? v.scrollTop / (f.scrollHeight - v.clientHeight) : 0;
        }
        return R.pages > 1 ? R.page / (R.pages - 1) : 0;
    }
    function place() {
        if (layout() !== 'scroll') $('flow').style.transform = 'translateX(' + (-R.page * (R.pageWidth || 0)) + 'px)';
        $('rWhere').textContent = layout() === 'scroll' ? '' : 'Page ' + (R.page + 1) + ' of ' + R.pages;
        $('rSlider').max = String(R.pages);
        $('rSlider').value = String(R.page + 1);
        $('rSlider').style.display = layout() === 'scroll' ? 'none' : '';
        if (R.source) store('noveldb.read.' + key(R.source.url), {chapter: R.index, at: readingAt(), time: Date.now()});
        if ($('toc').classList.contains('open') && tocTab === 'pages') drawPages();
    }
    function turn(step) {
        if (!R.chapters.length) return;
        if (layout() === 'scroll') {
            var v = $('viewport');
            var end = v.scrollTop + v.clientHeight >= $('flow').scrollHeight - 4;
            if (step > 0 && end && R.index + 1 < R.chapters.length) openChapter(R.index + 1, 0);
            else if (step < 0 && v.scrollTop <= 0 && R.index > 0) openChapter(R.index - 1, 'end');
            else v.scrollBy({top: step * (v.clientHeight - 60), behavior: 'smooth'});
            return;
        }
        var page = R.page + step;
        if (page >= R.pages) { if (R.index + 1 < R.chapters.length) openChapter(R.index + 1, 0); return; }
        if (page < 0) { if (R.index > 0) openChapter(R.index - 1, 'end'); return; }
        R.page = page;
        place();
    }

    // The contents: chapters (with the volume they're in) and the current chapter's pages.
    var tocTab = 'chapters';
    function drawToc() {
        if (tocTab === 'pages') { drawPages(); return; }
        var filter = $('tocFilter').value.trim().toLowerCase(), section = null, html = '';
        R.chapters.forEach(function (chapter, index) {
            if (filter && String(chapter.name).toLowerCase().indexOf(filter) < 0 && String(index + 1) !== filter) return;
            if (chapter.section && chapter.section !== section && !filter) html += '<div class="section">' + esc(chapter.section) + '</div>';
            section = chapter.section;
            html += '<a data-chapter="' + index + '"' + (index === R.index ? ' class="on"' : '') + '>' + (index + 1) + '. ' + esc(chapter.name) + '</a>';
        });
        $('tocList').className = 'list';
        $('tocList').innerHTML = html || '<div class="section">No chapter matches.</div>';
        $('tocList').querySelectorAll('[data-chapter]').forEach(function (link) {
            link.onclick = function () { $('toc').classList.remove('open'); openChapter(Number(link.dataset.chapter), 0); };
        });
        markToc();
    }
    function drawPages() {
        $('tocList').className = 'list pages';
        var html = '';
        for (var page = 0; page < R.pages; page++) html += '<a data-page="' + page + '"' + (page === R.page ? ' class="on"' : '') + '>' + (page + 1) + '</a>';
        $('tocList').innerHTML = html;
        $('tocList').querySelectorAll('[data-page]').forEach(function (link) {
            link.onclick = function () { R.page = Number(link.dataset.page); place(); };
        });
    }
    function markToc() {
        var on = $('tocList').querySelector('[data-chapter="' + R.index + '"]');
        $('tocList').querySelectorAll('a.on[data-chapter]').forEach(function (a) { a.classList.remove('on'); });
        if (on) { on.classList.add('on'); if ($('toc').classList.contains('open')) on.scrollIntoView({block: 'center'}); }
    }
    function setTocTab(tab) {
        tocTab = tab;
        $('tabChapters').classList.toggle('on', tab === 'chapters');
        $('tabPages').classList.toggle('on', tab === 'pages');
        $('tocFilter').style.display = tab === 'chapters' ? '' : 'none';
        drawToc();
    }
    $('tabChapters').onclick = function () { setTocTab('chapters'); };
    $('tabPages').onclick = function () { setTocTab('pages'); };
    $('tocFilter').addEventListener('input', drawToc);
    $('rToc').onclick = function () {
        $('look').classList.remove('open');
        $('toc').classList.toggle('open');
        if ($('toc').classList.contains('open')) { drawToc(); markToc(); }
    };
    $('rLook').onclick = function () { $('toc').classList.remove('open'); $('look').classList.toggle('open'); };
    $('rClose').onclick = closeReader;
    $('rPrev').onclick = function () { turn(-1); };
    $('rNext').onclick = function () { turn(1); };
    $('zoneLeft').onclick = function () { turn(-1); };
    $('zoneRight').onclick = function () { turn(1); };
    $('rSlider').addEventListener('input', function () { R.page = Number($('rSlider').value) - 1; place(); });
    $('viewport').addEventListener('click', function () { $('toc').classList.remove('open'); $('look').classList.remove('open'); });
    $('viewport').addEventListener('scroll', function () { if (layout() === 'scroll') place(); });
    $('viewport').addEventListener('wheel', function (event) {
        if (layout() === 'scroll' || Math.abs(event.deltaY) < 20) return;
        event.preventDefault();
        if (R.wheelLock) return;
        R.wheelLock = true;
        setTimeout(function () { R.wheelLock = false; }, 300);
        turn(event.deltaY > 0 ? 1 : -1);
    }, {passive: false});
    function closeReader() {
        R.token++;
        $('reader').classList.remove('open');
        $('toc').classList.remove('open');
        $('look').classList.remove('open');
        document.body.style.overflow = '';
    }
    document.addEventListener('keydown', function (event) {
        if (!$('reader').classList.contains('open') || event.target.matches('input')) return;
        if (event.key === 'ArrowRight' || event.key === 'PageDown' || (event.key === ' ' && !event.shiftKey)) { event.preventDefault(); turn(1); }
        else if (event.key === 'ArrowLeft' || event.key === 'PageUp' || (event.key === ' ' && event.shiftKey)) { event.preventDefault(); turn(-1); }
        else if (event.key === 'Escape') closeReader();
        else if (event.key === 'Home') { R.page = 0; place(); }
        else if (event.key === 'End') { R.page = R.pages - 1; place(); }
    });
    window.addEventListener('resize', function () {
        if (!$('reader').classList.contains('open')) return;
        clearTimeout(R.resizeTimer);
        var at = readingAt();
        R.resizeTimer = setTimeout(function () { paginate(at); }, 120);
    });

    window.NovelDBGeneric = {
        init: function (value) {
            config = value || config;
            try {
                var top = {};
                (config.lnreader || []).forEach(function (p, index) { top[p.id] = index; });
                allPlugins = (parse(window.__LNR_sites && window.__LNR_sites()) || []).map(function (p) {
                    pluginLangs[p.id] = langCode(p.lang);
                    var site = hostOf(p.site) || p.site;
                    return {id: p.id, site: site, comic: false, rank: p.id in top ? top[p.id] : -1,
                            ln: LN_SITE.test(site + ' ' + p.name)};
                });
            } catch (e) { allPlugins = []; /* no plugin list: the top sites' plugins only */ }
            if (view === 'top' && !order.length) showTop();
        },
        add: add,
        progress: function (id, item) {
            if (id !== searchId || !item) return;
            if (item.total) { progress.expected += item.total; showProgress(); return; }
            siteAnswered(item);
        },
        done: function (id, summary) {
            if (id !== searchId) return;
            setRunning(-1);
            if (view === 'top') {
                topRedraw();
                topSave();
                status(order.length ? 'The most read ' + TOP_NAMES[top.kind] + ' on the top ' + TOP_NAMES[top.kind] + ' sites, by their popular pages.'
                    : 'The ' + TOP_NAMES[top.kind] + ' sites\' popular pages did not answer.');
                return;
            }
            var slow = summary && (summary.failed || summary.timed_out);
            if (!running) {
                status(shownCount() + ' books found in ' + seconds(Date.now() - progress.started)
                       + (slow ? ' (some sites did not answer in time)' : '') + '.');
                empty();
            }
        },
        details: function (card) {
            var group = byUrl.get(key(card.url));
            if (!group) return;
            if (offTag(group, card)) { drop(group, card.url); return; }
            if (group.held && !group.verified) {
                // Its page is in: it stays only if the page lists the tag.
                if (!hasTag(group, card)) { drop(group, card.url); return; }
                group.held = false;
                group.confirmed = true;
            }
            if (card.empty || NO_RESULTS.test(card.title || '') || NO_RESULTS.test(String(card.description || '').slice(0, 400))) {
                drop(group, card.url);
                return;
            }
            card.complete = true;
            merge(card);
            // Its page shows tags the list card lacked.
            if (view === 'top' && notInTop(group)) { dropFromTop(group); return; }
            finishDetails(group);
            if (group.onDetails && $('dialog').open) group.onDetails();
        },
        // A book from the Library (an EPUB on this computer), in the reader.
        readLocal: function (book) {
            var group = {key: 'local:' + book.url, title: book.title, author: '', cover: '', description: '', tags: [],
                         comic: false, loaded: true, sources: [{url: book.url, site: 'Library', source: 'local'}]};
            if ($('dialog').open) $('dialog').close();
            openReader(group, 0);
        },
        readerStatus: function (id, text) {
            if (id !== R.requestId || !R.drawNote) return;
            R.noteDetail = text;
            // Once the chapter's images are showing, the note would cover them: the bottom bar says it instead.
            if (R.streamed === R.index) { $('rWhere').textContent = text; return; }
            R.drawNote();
        },
        readerImages: function (id, found) {
            if (id !== R.requestId || !found || !found.images) return;
            streamImages(R.index, found);
        },
        fetched: function (id, result) {
            var resolve = pending[id];
            delete pending[id];
            if (resolve) resolve(result);
        },
        setCart: function (urls) {
            cart = new Set(urls || []);
            order.forEach(function (group) { if (group.element) render(group); });
        }
    };
    showPicked();
    empty();
    send({type: 'genericReady'});
})();
