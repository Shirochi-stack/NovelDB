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
    var config = {sites: [], lnreader: []}, referenceRun = null;
    var groups = new Map(), byUrl = new Map(), order = [];
    var cart = new Set(), pending = {}, nextId = 0, searchId = 0, running = 0;
    var view = 'top', current = {text: '', tags: []}, picked = [], resultTags = new Map();
    // TOP_KEY's version: a new one drops the top lists saved by older code.
    // A top list shows TOP_SIZE books at first, TOP_STEP more each time it is scrolled to its end.
    var TOP_KEY = 'noveldb.generic.top.v9', TOP_HOURS = 12, TOP_SIZE = 100, TOP_STEP = 50;
    var TAGS = ['Action', 'Adventure', 'Apocalypse', 'Comedy', 'Cultivation', 'Drama', 'Dungeon', 'Ecchi', 'Fantasy',
        'Game', 'Gender Bender', 'GL', 'Harem', 'Historical', 'Horror', 'Isekai', 'Josei', 'LitRPG', 'Magic', 'Martial Arts',
        'Mature', 'Mecha', 'Military', 'Modern', 'Murim', 'Mystery', 'Psychological', 'Regression', 'Reincarnation', 'Revenge',
        'Romance', 'Romantic Comedy', 'School Life', 'Sci-fi', 'Seinen', 'Shoujo', 'Shounen', 'Slice of Life', 'Sports',
        'Supernatural', 'System', 'Thriller', 'Time Travel', 'Tragedy', 'Transmigration', 'Villainess', 'Wuxia', 'Xianxia',
        'Xuanhuan', 'Yuri', 'Completed', 'Ongoing', 'Hiatus', 'R15', 'R19', 'BL', 'Yaoi'];  // BL last: a niche the tag menu doesn't lead with
    // A site's "nothing matched your search" page (the app drops these too; see generic_search.NO_RESULTS).
    var NO_RESULTS = /(no (items?|results?|books?|novels?|titles?|matches|products?)( were)? (found|matching|match)|nothing (was )?found|did not match any|didn't match any|(found|returned) (0|no) (results?|items?|matches)|\b0 results\b|no results for|見つかりませんでした|該当する.{0,6}(ありません|見つかりません)|検索結果は?0件|검색\s*결과가\s*없|결과가\s*없습니다|일치하는.{0,10}없|没有找到|未找到|沒有找到|找不到相关|暂无(结果|数据|相关)|無結果|无结果|查無|no se encontraron|sin resultados|aucun résultat|ничего не найдено|не найдено|keine (ergebnisse|treffer)|nenhum resultado)/i;
    var JUNK = /^(request|requests|リクエスト|login|log in|sign in|ログイン|search|検索|more|もっと見る|home|top|next|prev(ious)?|menu|contact( us)?|about( us)?|privacy( policy)?|terms( of (service|use))?|dmca|faq|feedback|support|donate)$/i;

    function send(message) { window.NovelDBQt && window.NovelDBQt.postMessage(JSON.stringify(message)); }
    // One book's address however a site or a web search wrote it: http or https, www. or not, a #part, and a
    // title's slug after its ID (/title/<id>/<slug>) do not make it another source (the details listed one
    // site twice).
    function key(url) {
        return String(url || '').trim().replace(/#.*$/, '').replace(/^https?:\/\//i, '').replace(/^(www|m)\./i, '')
            .replace(/^([^\/?]+)/, function (host) { return host.toLowerCase(); })
            .replace(/(\/(?:title|manga|series|comic|novel|book|works?)\/(?:[0-9a-f]{8}-[0-9a-f-]{27}|\d+))\/(?=[\w-]*[a-z])[\w-]+\/?(?=$|\?)/i, '$1')
            .replace(/\/+(?=$|\?)/, '');
    }
    // A reading place's name: the address as it was first kept (places already kept stay found).
    function placeKey(url) { return String(url || '').replace(/\/+$/, ''); }
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
    var bookProgress = null;
    function drawBookProgress() {
        if (!bookProgress || !R.drawNote) return;
        var parts = ['Source ' + bookProgress.source + ' of ' + bookProgress.sources];
        if (bookProgress.page > 1) parts.push('page ' + bookProgress.page);
        if (bookProgress.requests) parts.push(bookProgress.done + ' of ' + bookProgress.requests + ' requests finished');
        else parts.push('starting…');
        R.noteDetail = parts.join(' · ');
        R.drawNote();
    }
    function trackBookRequest(request) {
        var progress = bookProgress;
        if (!progress) return null;
        progress.requests++;
        try {
            var page = Number(new URL(request.url || '').searchParams.get('page') || 0);
            if (page > progress.page) progress.page = page;
        } catch (e) {}
        drawBookProgress();
        return progress;
    }
    window.__LNR_transport = function (request) {
        var progress = trackBookRequest(request || {});
        var reader = $('reader').classList.contains('open') && !!(progress || R.lnreader);
        return ask({type: 'genericFetch', request: request, reader: reader}).then(function (reply) {
            if (progress && bookProgress === progress) { progress.done++; drawBookProgress(); }
            return reply;
        });
    };

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
    // The language a text's letters are mostly written in, by their script ('' for Latin letters or too few).
    var SCRIPTS = [['ko', /[\uac00-\ud7af]/g], ['ja', /[\u3040-\u30ff]/g], ['zh', /[\u4e00-\u9fff]/g], ['ru', /[\u0400-\u04ff]/g],
                   ['th', /[\u0e00-\u0e7f]/g], ['ar', /[\u0600-\u06ff]/g]];
    function scriptLang(text) {
        text = String(text || '').slice(0, 600);
        var letters = (text.match(/\p{L}/gu) || []).length;
        if (letters < 4) return '';  // a date with a month's name tells a site's language too
        var best = '', most = 0;
        SCRIPTS.forEach(function (entry) {
            var count = (text.match(entry[1]) || []).length;
            if (count > most) { most = count; best = entry[0]; }
        });
        if (best === 'zh' && /[\u3040-\u30ff]/.test(text)) best = 'ja';
        return most > letters / 2 ? best : '';
    }
    // Latin-letter languages told apart by their most common little words (a Spanish site on .org).
    var LATIN_WORDS = {
        en: 'the and of to is that his her with was he she for but you they this from have',
        es: 'el los las y del por con una es su se lo pero más sus al le está ser',
        pt: 'não um uma os com para ele ela você mais mas seu sua foi é ao dos das',
        fr: 'le les des et est une du dans pour qui pas au sur il elle avec son ses',
        de: 'der die und das ist nicht ein eine mit sich auf den dem zu sie er',
        id: 'yang dan di ini itu dengan untuk tidak dari ke akan dia aku kamu',
        vi: 'của và là một những không cho người được với này khi đã',
        tr: 've bir bu için ile çok ama değil olarak daha onun'
    };
    var LATIN_SETS = Object.keys(LATIN_WORDS).map(function (lang) { return [lang, new Set(LATIN_WORDS[lang].split(' '))]; });
    function latinLang(text) {
        var words = String(text || '').slice(0, 800).toLowerCase().match(/\p{L}+/gu) || [];
        if (words.length < 6) return '';
        var counts = LATIN_SETS.map(function (entry) {
            return [entry[0], words.filter(function (w) { return entry[1].has(w); }).length];
        }).sort(function (a, b) { return b[1] - a[1]; });
        return counts[0][1] >= 3 && counts[0][1] > counts[1][1] * 1.3 ? counts[0][0] : '';
    }
    // Sites whose books turned out to be in another language than their domain says (an Arabic site on
    // .net): learned from their synopses, for their books that show none.
    var siteLangs = new Map();
    var siteTitles = new Map();  // site: the titles of its books seen so far (to tell its language)
    function titlesLang(card) {
        if (!card.site || !card.title) return '';
        var titles = siteTitles.get(card.site);
        if (!titles) siteTitles.set(card.site, titles = []);
        if (titles.length < 60 && titles.indexOf(card.title) < 0) titles.push(card.title);
        if (titles.length < 5) return '';
        var found = latinLang(titles.join(' '));
        if (found && found !== 'en') siteLangs.set(card.site, found);
        return found;
    }
    // A book's language: its LNReader plugin's, else the script of its title or synopsis, else its site's.
    function langOf(card) {
        var plugin = card.plugin && pluginLangs[card.plugin];
        if (plugin) return plugin;
        var written = scriptLang(card.description) || latinLang(card.description + ' ' + (card.title || ''));
        if (written) { if (card.site && written !== 'en') siteLangs.set(card.site, written); return written; }
        var title = String(card.title || '');
        if (/[\uac00-\ud7af]/.test(title)) return 'ko';
        if (/[\u3040-\u30ff]/.test(title)) return 'ja';
        var tld = String(card.site || '').split('.').pop();
        if (/[\u4e00-\u9fff]/.test(title)) return tld === 'jp' ? 'ja' : 'zh';
        if (/[\u0400-\u04ff]/.test(title)) return 'ru';
        if (/[\u0e00-\u0e7f]/.test(title)) return 'th';
        if (/[\u0600-\u06ff]/.test(title)) return 'ar';
        titlesLang(card);
        return siteLangs.get(card.site) || TLD_LANG[tld] || 'en';
    }
    // "ko-en": Korean books shown with their English titles (NovelDB's translated metadata).
    function baseLang() { return language.split('-')[0]; }
    function englishTitles() { return /-en$/.test(language); }
    // The language a card's text is in: "日本語 (English)" is Japanese novels in English translation (the
    // light-novel sites), so English text.
    function cardLang() { return language === 'ja-en' ? 'en' : baseLang(); }
    function inLanguage(card) { return language === 'all' || langOf(card) === cardLang(); }

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
               + shownCount() + ' book' + (shownCount() === 1 ? '' : 's') + ' so far' + noveldbCount() + memoryNote() + (progress.googleBlocked ? ' · Google requires verification' : ''));
    }
    // The page's memory (Chromium's estimate): a phone's page that grows past a few hundred MB is closed by Android.
    function memoryMB() {
        var memory = window.performance && performance.memory;
        return memory && memory.usedJSHeapSize ? Math.round(memory.usedJSHeapSize / 1048576) : 0;
    }
    function memoryNote() { var mb = memoryMB(); return mb ? ' · ' + mb + ' MB' : ''; }
    // While searching, the one progress line counts the books: no second line takes turns with it.
    function countLine() {
        if (running) { showProgress(); return; }
        status(shownCount() + ' books found' + noveldbCount() + (progress.noveldb > shownCount() ? '; scroll for more.' : '.') + (progress.googleBlocked ? ' · Google requires verification' : ''));
    }
    function noveldbCount() {
        return progress.noveldb ? ' · ' + progress.noveldb.toLocaleString() + ' in NovelDB' : '';
    }
    function seconds(ms) { return ms == null ? '' : (ms < 10000 ? (ms / 1000).toFixed(1) : Math.round(ms / 1000)) + ' s'; }
    function siteState(item) {
        return item.state === 'failed' ? 'failed' : item.state === 'refused' ? 'refused' : item.state === 'timeout' ? 'too slow' : item.found + ' found';
    }
    // On a top list the panel picks sites: their own lists, in their own order, as the top list.
    var picking = null;
    function topCandidates() {
        var names = [];
        // Only real addresses: a plugin whose site is a placeholder ("url") is no site to pick.
        var add = function (site) { if (site && !excludedTopSite(site) && /^[a-z0-9-]+(\.[a-z0-9-]+)+$/i.test(site) && names.indexOf(site) < 0) names.push(site); };
        top.only.forEach(add);
        if (!isComicKind(top.kind)) Object.keys(NOVELDB_SITES).forEach(add);
        BUILTIN_SITES.forEach(add);
        if (!isComicKind(top.kind)) topPlugins(top.kind, true).forEach(function (p) { add(p.site); });
        progress.sites.forEach(function (item) { add(item.site); });
        order.forEach(function (group) { group.sources.forEach(function (s) { add(s.site); }); });
        // Then every other site the search reads (the novel or comic ones, by what they are known as).
        (config.sites || []).forEach(function (site) {
            if (((config.comic || []).indexOf(site) >= 0) === isComicKind(top.kind)) add(site);
        });
        return names;
    }
    function drawTopSites() {
        var panel = $('sitespanel');
        if (!picking) picking = new Set(top.only);
        if (!panel.querySelector('#siteList')) {
            panel.innerHTML = '<h3>Top of chosen sites <span id="siteCount"></span></h3><p>Pick sites to see their own most read, in their order, with more as you scroll.</p>'
                + '<div class="pickbar"><input id="siteFilter" placeholder="Find a site" autocomplete="off">'
                + '<button type="button" class="primary" id="siteShow">Show</button><button type="button" id="siteClear">Clear</button>'
                + '<button type="button" id="siteAll">All sites</button></div>'
                + '<div id="siteList"></div>';
            $('siteFilter').addEventListener('input', drawTopSites);
            $('siteShow').addEventListener('click', function () {
                topOnly[top.kind] = Array.from(picking);
                picking = null;
                panel.classList.remove('open');
                showTop();
            });
            $('siteAll').addEventListener('click', function () {
                topOnly[top.kind] = [];
                picking = null;
                panel.classList.remove('open');
                showTop();
            });
            // Unticks every site; a list of picked sites on show goes back to every site's at once.
            $('siteClear').addEventListener('click', function () {
                picking.clear();
                if (top.only.length) $('siteAll').click(); else drawTopSites();
            });
        }
        var filter = $('siteFilter').value.trim().toLowerCase();
        var answered = new Map(progress.sites.map(function (item) { return [item.site, item]; }));
        var candidates = topCandidates();
        // How many sites there are to pick from (all the engines know of this kind).
        $('siteCount').textContent = '· ' + candidates.length.toLocaleString() + ' sites';
        var names = candidates.filter(function (site) { return !filter || site.toLowerCase().indexOf(filter) >= 0 || picking.has(site); });
        // A typed address of a site not listed (one whose address keeps moving): offered as it is.
        var typed = filter.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
        if (!excludedTopSite(typed) && /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(typed) && candidates.indexOf(typed) < 0) names.unshift(typed);
        // The picked sites first, then NovelDB's, then the ones that answered, quickest first, then the rest.
        var rank = function (site) { return picking.has(site) ? 0 : NOVELDB_SITES[site] && !isComicKind(top.kind) ? 1 : 2; };
        names.sort(function (a, b) {
            var x = answered.get(a), y = answered.get(b);
            return (rank(a) - rank(b)) || (!!y - !!x) || ((x ? x.ms : 0) - (y ? y.ms : 0));
        });
        $('siteShow').textContent = picking.size ? 'Show ' + picking.size : 'Show';
        $('siteShow').disabled = !picking.size;
        $('siteClear').disabled = !picking.size;
        $('siteList').innerHTML = names.map(function (site) {
            var item = answered.get(site);
            return '<label class="siterow pick ' + esc(item ? item.state || 'ok' : '') + '"><input type="checkbox" data-site="' + esc(site) + '"'
                + (picking.has(site) ? ' checked' : '') + '><span>' + esc(site) + '</span><span class="ms">'
                + (NOVELDB_SITES[site] && !isComicKind(top.kind) ? 'NovelDB' : item ? seconds(item.ms) : '')
                + '</span><span class="n">' + (item ? esc(siteState(item)) : '') + '</span></label>';
        }).join('') || '<p>No site matches.</p>';
        $('siteList').querySelectorAll('[data-site]').forEach(function (box) {
            box.onchange = function () {
                if (box.checked) picking.add(box.dataset.site); else picking.delete(box.dataset.site);
                // The last picked site unticked: back to every site's list at once (as All sites does).
                if (!picking.size && top.only.length) { $('siteAll').click(); return; }
                $('siteShow').textContent = picking.size ? 'Show ' + picking.size : 'Show';
                $('siteShow').disabled = !picking.size;
                $('siteClear').disabled = !picking.size;
            };
        });
    }
    // A search's results narrowed to some sites (ticked in the Sites panel; empty: every site). Works
    // while the search runs: books that come in later from other sites stay hidden.
    var siteFilter = new Set();
    function refilter() {
        var titles = [];
        order.forEach(function (group) {
            if (!group.element) return;
            show(group, wanted(group) && !(group.held && !group.verified));
            titles.push(group.element.querySelector('.title'));
        });
        fitTitles(titles);
        empty();
    }
    function drawSites() {
        if (view === 'top') { drawTopSites(); return; }
        // The sites the books came from, the most books first: ticking some shows only their books.
        var counts = new Map();
        order.forEach(function (group) {
            if (group.held && !group.verified) return;
            new Set(group.sources.map(function (s) { return s.site; })).forEach(function (site) { if (site) counts.set(site, (counts.get(site) || 0) + 1); });
        });
        siteFilter.forEach(function (site) { if (!counts.has(site)) counts.set(site, 0); });
        var bySite = Array.from(counts).sort(function (a, b) { return (siteFilter.has(b[0]) - siteFilter.has(a[0])) || b[1] - a[1]; });
        // Every site, always (not only those books came from): found with the box, the rest further down the list
        // as it is scrolled. Ticking a site no book came from searches it.
        var filter = siteFilterText();
        var html = '<h3>Show books from</h3><div class="pickbar"><button type="button" id="siteFilterAll"' + (siteFilter.size ? '' : ' disabled')
            + '>All sites</button> <input id="siteFind" placeholder="Find a site" autocomplete="off"></div>'
            + bySite.filter(function (entry) { return !filter || entry[0].toLowerCase().indexOf(filter) >= 0; }).map(function (entry) {
                return '<label class="siterow pick"><input type="checkbox" data-filter-site="' + esc(entry[0]) + '"'
                    + (siteFilter.has(entry[0]) ? ' checked' : '') + '><span>' + esc(entry[0]) + '</span><span class="n">'
                    + entry[1] + ' book' + (entry[1] === 1 ? '' : 's') + '</span></label>';
            }).join('')
            + '<div id="siteMore"></div>';
        var rows = progress.sites.slice().sort(function (a, b) { return a.ms - b.ms; });
        html += '<h3>' + progress.answered + ' of ' + (progress.expected || '?') + ' sources answered</h3>';
        if (memoryMB()) html += '<p>The page is using ' + memoryMB() + ' MB of memory.</p>';
        html += rows.map(function (item) {
            var what = siteState(item);
            return '<div class="siterow ' + esc(item.state || 'ok') + '"><span>' + esc(item.site) + '</span><span class="ms">'
                + seconds(item.ms) + '</span><span class="n">' + esc(what) + '</span></div>';
        }).join('');
        if (running && progress.expected > progress.answered) html += '<p>' + (progress.expected - progress.answered) + ' still searching…</p>';
        var panel = $('sitespanel'), scroll = panel.scrollTop;
        var finding = document.activeElement && document.activeElement.id === 'siteFind';
        panel.innerHTML = html;
        panel.scrollTop = scroll;  // redrawn as sites answer: the list stays where it was
        if ($('siteFind')) {
            $('siteFind').value = siteFindText;
            $('siteFind').addEventListener('input', function () {
                siteFindText = this.value;
                otherLimit = OTHER_STEP;
                drawSites();
            });
            if (finding) { $('siteFind').focus(); $('siteFind').setSelectionRange(siteFindText.length, siteFindText.length); }
            drawOtherSites(counts);
        }
        panel.querySelectorAll('[data-filter-site]').forEach(function (box) {
            box.onchange = function () {
                if (box.checked) siteFilter.add(box.dataset.filterSite); else siteFilter.delete(box.dataset.filterSite);
                refilter();
                drawSites();
                widenSoon();
            };
        });
        if ($('siteFilterAll')) $('siteFilterAll').onclick = function () { siteFilter.clear(); refilter(); drawSites(); widenSoon(); };
    }
    // Every other site the search reads (no book came from it), OTHER_STEP more each time the list is scrolled
    // to its end; all that match once a name is typed.
    var siteFindText = '', OTHER_STEP = 150, otherLimit = OTHER_STEP;
    function siteFilterText() { return siteFindText.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, ''); }
    function searchCandidates() {
        var names = [], seen = {};
        var add = function (site) {
            if (site && !seen[site] && /^[a-z0-9-]+(\.[a-z0-9-]+)+$/i.test(site)) { seen[site] = true; names.push(site); }
        };
        progress.sites.forEach(function (item) { add(item.site); });
        Object.keys(NOVELDB_SITES).forEach(add);
        BUILTIN_SITES.forEach(add);
        topPlugins('webnovel', true).forEach(function (p) { add(p.site); });
        (config.sites || []).forEach(add);
        return names;
    }
    function drawOtherSites(counts) {
        var box = $('siteMore');
        if (!box) return;
        var filter = siteFilterText();
        var names = searchCandidates().filter(function (site) {
            return !counts.has(site) && (!filter || site.toLowerCase().indexOf(filter) >= 0);
        });
        // A whole address typed that no list has: that site, to search.
        if (/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(filter) && names.indexOf(filter) < 0 && !counts.has(filter)) names.unshift(filter);
        var shown = names.slice(0, otherLimit);
        box.innerHTML = (shown.length ? '<h3>Other sites</h3>' : '') + shown.map(function (site) {
            return '<label class="siterow pick"><input type="checkbox" data-other-site="' + esc(site) + '"><span>' + esc(site)
                + '</span><span class="n">no books yet</span></label>';
        }).join('') + (names.length > shown.length ? '<p>' + (names.length - shown.length).toLocaleString() + ' more below…</p>' : '')
            + (!names.length && filter && !Array.from(counts.keys()).some(function (site) { return site.toLowerCase().indexOf(filter) >= 0; })
                ? '<p>No site matches.</p>' : '');
        box.querySelectorAll('[data-other-site]').forEach(function (input) {
            input.onchange = function () {
                if (!input.checked) return;
                siteFilter.add(input.dataset.otherSite);
                refilter();
                drawSites();
                widenSoon(true);
            };
        });
    }
    // The list goes on as it is scrolled to its end.
    $('sitespanel').addEventListener('scroll', function () {
        var panel = this;
        if (view === 'top' || !$('siteMore') || panel.scrollTop + panel.clientHeight < panel.scrollHeight - 200) return;
        var before = otherLimit;
        otherLimit += OTHER_STEP * 2;
        if (/more below/.test($('siteMore').textContent)) drawSites(); else otherLimit = before;
    }, {passive: true});
    /** A search that asked only some sites, and a site ticked that it did not ask (or All sites): searched
     *  again for the sites now ticked, once the ticking pauses. Else a site's few books from NovelDB were all
     *  that ticking it could show. */
    var widenTimer = 0;
    function widenSoon(always) {
        clearTimeout(widenTimer);
        widenTimer = setTimeout(function () {
            if (view !== 'search' || !current.queries || !current.queries.length) return;
            // A site ticked that no book came from: it may not have been asked yet (a search asks its sites in stages).
            if (always) { search(null, true); return; }
            var asked = current.queries[0].only;
            if (!asked) return;
            var missing = !siteFilter.size || Array.from(siteFilter).some(function (site) { return asked.indexOf(site) < 0; });
            if (missing) search(null, true);
        }, 900);
    }
    /** The panel closes (a tap outside it, or on Sites again): the sites ticked in it are shown, as Show does. */
    function applyPicks() {
        if (view !== 'top' || !picking || !$('sitespanel').classList.contains('open')) return false;
        var picked = Array.from(picking), shown = top.only;
        picking = null;
        if (picked.length === shown.length && picked.every(function (site) { return shown.indexOf(site) >= 0; })) return false;
        topOnly[top.kind] = picked;
        $('sitespanel').classList.remove('open');
        showTop();
        return true;
    }
    $('sitesButton').addEventListener('click', function (event) {
        event.stopPropagation();
        $('settingspanel').classList.remove('open');
        if (applyPicks()) return;
        $('sitespanel').classList.toggle('open');
        $('sitespanel').innerHTML = '';
        picking = null;
        if ($('sitespanel').classList.contains('open')) drawSites();
    });

    // The title shares the query's words (most of them), as the app's own site search requires.
    function words(text) {
        return (String(text || '').normalize('NFKC').toLowerCase().replace(/['’`]/g, '').match(/[\p{L}\p{N}]+/gu) || []).filter(function (w) {
            return w.length > 1 || /[^\x00-\x7f]/.test(w);
        });
    }
    function relevant(title, query) {
        var ignored = new Set('a an the of to in on and is be i my ill im novel manga manhwa manhua webtoon'.split(' '));
        var want = words(query).filter(function (word) { return !ignored.has(word); }), have = words(title);
        if (!want.length) return !!exactTitle(query) && exactTitle(title).indexOf(exactTitle(query)) >= 0;
        if (want.length === 1 && /[^\x00-\x7f]/.test(want[0])) return have.join('').indexOf(want[0]) >= 0;
        return want.every(function (word) { return have.indexOf(word) >= 0; });
    }
    function within(promise, ms, fallback) {
        return Promise.race([promise, new Promise(function (resolve) { setTimeout(function () { resolve(fallback === undefined ? '[]' : fallback); }, ms); })]);
    }
    function parse(raw) { try { return typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) { return null; } }

    function setRunning(delta) {
        running = Math.max(0, running + delta);
        $('stop').disabled = running === 0;
        showProgress();
        showMoreLoader();
        if (!running) setTimeout(closeSlots, 1500);  // books still being checked may yet fill them
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
        if (card.catalogue) return card.catalogue.key;
        var title = String(card.title || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
        return (isComic(card) ? 'c:' : 'n:') + (title || key(card.url));
    }
    // Tags that only say a book's status or format: shown after its genres, which say what it is about.
    var PLAIN_TAGS = new Set(['completed', 'complete', 'ongoing', 'on going', 'hiatus', 'dropped', 'cancelled', 'manga',
        'manhwa', 'manhua', 'manhuwa', 'comic', 'comics', 'webtoon', 'webtoons', 'novel', 'novels', 'web novel', 'webnovel',
        'light novel', 'lightnovel', 'full color', 'colored', 'english', 'raw', 'korean', 'japanese', 'chinese', 'new', 'hot']);
    // One name for one tag, without a site's "#": "Manhwa BL" (or "Mahwa BL"), "Boys' Love", "BL Manga" are BL.
    function tagAlias(tag) {
        var text = String(tag || '').trim().replace(/^#+\s*/, '');  // "# system": the tag is "system"
        // A site's menu link, not a tag ("All Tags", "Browse", "More genres", "Genres").
        if (/^(all|browse|more|see all|view all|show all)\b|^(tags?|genres?|categor(y|ies))$/i.test(text)) return '';
        if (/^((man?h?wa|manga|manhua|webtoon|comics?)[\s-]*bl|bl[\s-]*(man?h?wa|manga|manhua|webtoon|comics?)|boys?'?s?[\s-]*love)$/i.test(text)) return 'BL';
        return text;
    }
    function shownTags(tags) {
        var plain = function (tag) { return PLAIN_TAGS.has(String(tag).trim().toLowerCase()); };
        return tags.filter(function (t) { return !plain(t); }).concat(tags.filter(plain));
    }
    // The card's tags in two rows; the ones that don't fit become "+N" (the details panel lists them all).
    function fitTags(box) { fitTagRows([box]); }
    /** Each card's tag chips cut to the rows its box shows, with "+N" for the rest, many cards at once: every
     *  card measured, then every card changed (card by card, each change made the next card's measurement lay
     *  the page out again). */
    function fitTagRows(boxes) {
        var plans = boxes.filter(function (box) {
            return box && box.isConnected && box.children.length && box.closest('.card') && box.closest('.card').style.display !== 'none';
        }).map(function (box) {
            var limit = box.firstElementChild.offsetTop + box.clientHeight + 1;
            var chips = Array.prototype.slice.call(box.children);
            return {box: box, limit: limit, hidden: chips.filter(function (chip) { return chip.offsetTop + chip.offsetHeight > limit; })};
        }).filter(function (plan) { return plan.hidden.length; });
        plans.forEach(function (plan) {
            plan.hidden.forEach(function (chip) { chip.remove(); });
            plan.more = document.createElement('span');
            plan.more.className = 'more';
            plan.count = plan.hidden.length;
            plan.more.textContent = '+' + plan.count;
            plan.box.appendChild(plan.more);
        });
        // A "+N" that wrapped to a row of its own takes the place of the chips before it (rare: a few cards).
        plans.filter(function (plan) { return plan.more.offsetTop + plan.more.offsetHeight > plan.limit; }).forEach(function (plan) {
            while (plan.more.offsetTop + plan.more.offsetHeight > plan.limit && plan.more.previousElementSibling) {
                plan.more.previousElementSibling.remove();
                plan.more.textContent = '+' + (++plan.count);
            }
        });
    }
    function wanted(group) {
        var kind = $('kind').value;
        if (view === 'top') return true;  // each category has its own top list
        if (siteFilter.size && !group.sources.some(function (s) { return siteFilter.has(s.site); })) return false;
        return kind === 'all' || kindOf(group) === kind;
    }

    // A synopsis as shown: without what a book page puts before it (its title, a rating "3.7 / 5 (647
    // votes)", "Rate this novel", share buttons, then a "Summary" label), without a teaser cut off with
    // "[…]" that the full text after it repeats (pages that show both), and nothing for a text that is
    // only the page's figures ("5.0 English Novel Ecchi, Fantasy 33 Chapters").
    var SHARE_WORDS = /\b(facebook|twitter|whatsapp|pinterest|reddit|telegram|tumblr|share|tweet|copy link)\b[\s,|·]*/gi;
    var RATING = /\b\d(\.\d+)?\s*\/\s*(5|10)\s*(\(\s*[\d,.]+\s*votes?\s*\))?|\(\s*[\d,.]+\s*(votes?|ratings?)\s*\)|rate this (novel|book|series|story)/gi;
    var SYNOPSIS_LABEL = /\b(summary|synopsis|sinopsis|sinopse|description|deskripsi|ringkasan|resumen|résumé)\b\s*[:：-]?\s*/i;
    // A synopsis that ends in the page's hashtags ("… #Aristocracy #Found Family Suggest tag …"): the
    // text before them, and the tags (the page's own tag widgets after the last one go).
    var TAG_JUNK = /\s*\b(suggest (tags?|genres?)|community suggestions|show (more|less)|read more|add tags?|vote)\b[\s\S]*$/i;
    function hashSplit(text) {
        text = String(text || '');
        var at = text.search(/(^|\s)#(?=[^\s#\d])/);
        if (at < 0 || (text.slice(at).match(/(^|\s)#(?=[^\s#])/g) || []).length < 2) return {text: text, tags: []};
        var tags = text.slice(at).trim().slice(1).split(/\s+#/).map(function (piece) {
            return piece.replace(TAG_JUNK, '').replace(/[\s,;|·]+$/, '').trim();
        }).filter(function (tag) { return tag.length >= 2 && tag.length <= 40; });
        var before = text.slice(0, at).replace(/\s*(show more|read more|show less|more)\s*$/i, '').trim();
        return {text: before, tags: tags};
    }
    // A chapter count is a whole number ("436", "1,204", "86 chapters"), not a rating that landed there.
    function wholeCount(value) {
        var text = String(value || '');
        // No book has 100,000 chapters: a number that big is a word or character count.
        return /^\s*\d[\d,\s]*(chapters?|ch|eps?|episodes?)?\s*$/i.test(text) && Number(text.replace(/\D/g, '')) < 100000;
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
        if (!old) return !!next;
        var a = namesBook(next, title), b = namesBook(old, title);
        return a !== b ? a : (!b && fromPage) || next.length > old.length;
    }
    // What a site puts before the synopsis itself: figures the card shows already ("Ongoing Author: Fuyuki
    // Latest Chapter: Vol.07 Ch.033") and an introduction ("A brief description of the manga X: ...").
    var LEAD_LABEL = /^(?:(?:ongoing|completed|hiatus|on-?going)\s+)?(author|artist|status|latest chapters?|last chapter|type|genres?|released?|updated)s?\s*[:：]\s*/i;
    var NEXT_LABEL = /\s(?:author|artist|status|latest chapters?|last chapter|type|genres?|released?|updated)s?\s*[:：]/i;
    function synopsisLead(text) {
        for (var round = 0; round < 6; round++) {
            var before = text;
            text = text.replace(/^\s*an? (brief|short|quick) (description|summary|synopsis|introduction) of (the )?(manga|manhwa|manhua|novel|light novel|web ?novel|webtoon|comic|book|story|series)?[^:：.!?]{0,160}[:：]\s*/i, '');
            var label = text.match(LEAD_LABEL);
            if (label) {
                var rest = text.slice(label[0].length);
                var next = rest.slice(0, 80).search(NEXT_LABEL);
                var chapter = /^latest|^last/i.test(label[1]) && rest.match(/^((vol(ume)?\.?\s*[\d.]+\s*)?(ch(apter)?\.?\s*[\d.]+)|[\d.]+)\s*/i);
                if (next >= 0) text = rest.slice(next).replace(/^\s+/, '');
                else if (chapter) text = rest.slice(chapter[0].length);
                else if (/^(status|type)/i.test(label[1])) text = rest.replace(/^\S+\s*/, '');
                else break;  // a value whose end can't be told: leave the text as it is
            }
            if (text === before) break;
        }
        return text;
    }
    function synopsisText(text) {
        text = hashSplit(text).text;
        // A box of figures ("5 43 42": a rating and its votes) is no synopsis.
        if ((text.match(/[\p{L}]{2,}/gu) || []).length < 4) return '';
        // Junk before a label: everything up to the label goes (a rating or share buttons say it is junk).
        var label = text.slice(0, 400).search(SYNOPSIS_LABEL);
        if (label > 0) {
            var before = text.slice(0, label);
            if (before.search(RATING) >= 0 || (before.match(SHARE_WORDS) || []).length >= 2 || /^[A-Z\s]+$/.test(text.slice(label).match(SYNOPSIS_LABEL)[1])) {
                text = text.slice(label);
            }
        }
        text = text.replace(/^\s*((about|description of) this (novel|book|story|series|manga|manhwa|comic|title)|summary|synopsis|sinopsis|sinopse|description|deskripsi|ringkasan|resumen|résumé|story)\s*[:：-]?\s*/i, '');
        text = synopsisLead(text);
        // A run of share buttons, and ratings, anywhere at the start.
        text = text.replace(/^((\s*(facebook|twitter|whatsapp|pinterest|reddit|telegram|tumblr|share|tweet)\b[\s,|·]*){2,})/i, '');
        // Only the page's figures: a rating, a language, genres and a chapter count, no sentence.
        var plain = text.replace(RATING, ' ').replace(SHARE_WORDS, ' ').trim();
        if (plain.length < 160 && !/[.!?。！？]\s/.test(plain + ' ') && /\b\d+\s*(chapters?|ch|episodes?)\b/i.test(plain)) return '';
        var cut = text.search(/\s*(\[(…|\.\.\.)\]|…\s*(read more|more)?)\s*/i);
        if (cut > 40) {
            var rest = text.slice(cut).replace(/^\s*(\[(…|\.\.\.)\]|…\s*(read more|more)?)\s*/i, '');
            if (rest.length > cut * 0.8 && rest.slice(0, 30) === text.slice(0, 30)) return rest.trim();
        }
        return text.trim();
    }

    // -- cards -------------------------------------------------------------------------------------
    function reset(nextView) {
        // The sites picked in the Sites panel stay picked from one search to the next (a tag added, an
        // author tapped); a search from a top list of picked sites shows those sites' books.
        var from = view;
        view = nextView;
        more = {page: 1, loading: false, has: true};
        showMoreLoader();
        clearTimeout(top.timer);  // a top list still loading draws nothing more
        groups.clear(); byUrl.clear(); rawTitles.clear(); order = []; resultTags.clear();
        if (nextView !== 'search') siteFilter.clear();
        else if (from === 'top') {
            siteFilter.clear();
            if (!top.preferred) top.only.forEach(function (site) { siteFilter.add(site); });
        }
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
    // A book's original (untranslated) title, for finding it again under another site's name for it.
    var rawTitles = new Map();
    function rawKey(title) {
        var text = String(title || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
        return /[^\x00-\x7f]/.test(text) ? text : '';
    }
    // NovelDB's books lead their group (npia's first): their English title, cover and figures are the ones
    // shown, whoever answered first. A site's own card under the raw title joins them.
    function leadRank(source) { return !source || !source.noveldb ? 0 : source.noveldb.source === 'npia' ? 2 : 1; }
    // An author's name to compare by: "안현™" and "안현" are one writer.
    // (Marks go before NFKC, which would spell ™ "TM".)
    function authorKey(name) {
        return String(name || '').replace(/[^\p{L}\p{N}]+/gu, '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
    }
    function sameAuthor(a, b) {
        var x = authorKey(a), y = authorKey(b);
        return !x || !y || x === y || x.indexOf(y) >= 0 || y.indexOf(x) >= 0;
    }
    // A site's list thumbnail ("cover-193x278.jpg", "?resize=175,238") and the full-size image it was made from.
    function fullCover(url) {
        var text = String(url || '');
        if (!/^https?:/i.test(text)) return text;
        var big = text.replace(/-\d{2,4}x\d{2,4}(?=\.(jpe?g|png|webp|gif)(\?|#|$))/i, '');
        big = big.replace(/([?&])(resize|fit|w|h)=[^&#]*/gi, '$1').replace(/[?&]+(#|$)/, '$1').replace(/\?&+/, '?');
        return big;
    }
    function merge(card, rank) {
        if (view === 'top' && card && (excludedTopSite(card.site) || excludedTopSite(card.url))) return null;
        if (card && card.title) card.title = bookTitle(card.title, card.site);
        if (!card || !card.url || !card.title || JUNK.test(String(card.title).trim())) return null;
        if (NO_RESULTS.test(card.title) || NO_RESULTS.test(String(card.description || '').slice(0, 400))) return null;
        // The same book under its original title: another site's raw title, or NovelDB's for its English one.
        var raws = [rawKey(card.originalTitle), rawKey(card.title)].filter(Boolean);
        var group = byUrl.get(key(card.url)) || groups.get(groupKey(card))
            || raws.map(function (k) { return rawTitles.get(k); }).filter(function (g) {
                return g && groups.has(g.key) && sameAuthor(g.author, card.author);
            })[0];
        if (!group) {
            group = {key: groupKey(card), title: card.title, author: '', cover: '', description: '', tags: [], status: '',
                     comic: isComic(card), sources: [], rank: rank, loaded: false};
            groups.set(group.key, group);
            order.push(group);
        }
        raws.forEach(function (k) { if (!rawTitles.has(k)) rawTitles.set(k, group); });
        if (card.originalTitle && (!group.rawTitle || card.fresh) && card.originalTitle !== group.title) group.rawTitle = card.originalTitle;
        var source = group.sources.find(function (s) { return key(s.url) === key(card.url) || hostOf(s.url).replace(/^m\./, '') === hostOf(card.url).replace(/^m\./, ''); });
        if (!source) {
            // `noveldb`: a NovelDB book's source and id, for its synopsis from NovelDB's own files.
            source = {url: card.url, site: card.site || hostOf(card.url), source: card.source || '', plugin: card.plugin || '', path: card.path,
                      ms: card.ms, noveldb: card.noveldb};
            if (group.sources.length && leadRank(source) > leadRank(group.sources[0])) {
                group.sources.unshift(source);
                group.title = card.title;
                if (card.originalTitle) group.rawTitle = card.originalTitle;
                if (card.cover) { group.cover = card.cover; group.coverFrom = card.url; }
                // NovelDB's figures over a site's list card's.
                ['author', 'status', 'published', 'updated', 'rating', 'ratings', 'likes', 'views', 'chapters'].forEach(function (name) {
                    if (card[name] && (name !== 'chapters' || wholeCount(card[name]))) group[name] = card[name];
                });
                group.loaded = false;
            } else {
                group.sources.push(source);
            }
            byUrl.set(key(card.url), group);
        } else if (card.source === 'noveldb' && source.source !== 'noveldb') {
            // NovelDB's card of a book a site's card already showed, at the same address (its twin, looked up
            // in the index): the source becomes NovelDB's, with its English title and original one.
            source.source = 'noveldb';
            source.url = card.url;
            source.noveldb = card.noveldb;
            if (group.sources[0] === source || leadRank(source) > leadRank(group.sources[0])) {
                group.sources.splice(group.sources.indexOf(source), 1);
                group.sources.unshift(source);
                group.title = card.title;
                if (card.originalTitle) group.rawTitle = card.originalTitle;
                group.loaded = false;
            }
        }
        byUrl.set(key(card.url), group);
        // A NovelDB book on several of its sites (one entry in the index): its other sites' pages, after it.
        (card.also || []).forEach(function (other) {
            if (!other || !other.url || byUrl.has(key(other.url)) || group.sources.some(function (s) { return hostOf(s.url) === hostOf(other.url); })) return;
            if (view === 'top' && excludedTopSite(other.url)) return;
            group.sources.push({url: other.url, site: hostOf(other.url), source: 'noveldb', plugin: '', ms: card.ms, noveldb: other.noveldb});
            byUrl.set(key(other.url), group);
        });
        ['author', 'status', 'published', 'updated', 'rating', 'ratings', 'likes', 'views', 'chapters'].forEach(function (name) {
            // `fresh`: a NovelDB book's figures as its site shows them now (its details): they replace the index's.
            if (card[name] && (!group[name] || card.fresh) && (name !== 'chapters' || wholeCount(card[name]))) group[name] = card[name];
        });
        // Its age rating (19 adults only, 15 teens): the strictest any of its sites gives it.
        if (card.ageRating === 19 || card.ageRating === 15) group.ageRating = Math.max(group.ageRating || 0, card.ageRating);
        // The full-size image first, the thumbnail the site listed behind it (if the big one fails).
        var offered = card.cover ? [fullCover(card.cover), card.cover] : [];
        if (offered.length && !group.cover) { group.cover = offered[0]; group.coverFrom = card.url; }  // the first site's cover stays
        offered.forEach(function (cover) { if ((group.covers || (group.covers = [])).indexOf(cover) < 0) group.covers.push(cover); });
        // The longest synopsis once cleaned (a page's rating and share buttons do not count).
        if (card.description && betterSynopsis(synopsisText(card.description), synopsisText(group.description), group.title, !!card.complete)) group.description = card.description;
        var cardTags = (card.tags || []).concat(card.catalogue ? card.catalogue.tags || [] : [], hashSplit(card.description).tags).map(tagAlias).filter(Boolean);
        if (cardTags.length) {
            var seen = new Set(group.tags.map(function (t) { return t.toLowerCase(); }));
            cardTags.forEach(function (tag) {
                if (!seen.has(tag.toLowerCase())) { seen.add(tag.toLowerCase()); group.tags.push(tag); }
            });
        }
        if (card.complete && key(card.url) === key(group.sources[0].url)) group.loaded = true;
        if (card.complete && card.description) group.loaded = true;
        if (card.catalogue) group.catalogue = card.catalogue;
        if (group.catalogue) {
            group.title = group.catalogue.title;
            group.description = group.catalogue.description || group.description;
            group.author = (group.catalogue.authors || []).join(', ') || group.author;
            if (!group.cover && group.catalogue.cover) group.cover = group.catalogue.cover;
            (group.catalogue.aliases || []).concat([group.catalogue.title]).forEach(function (name) { rawTitles.set(rawKey(name), group); });
            group.loaded = true;
        }
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
            if (group.element && group.element.parentNode && group.placed && group.element.style.display !== 'none') leaveSlot(group.element);
            else if (group.element) group.element.remove();
            if ($('dialog').open && group.onDetails) $('dialog').close();
        }
        if (view === 'search') countLine();
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
    function isAgeTag(tag) { return /^r(19|15)$/i.test(String(tag || '').trim()); }
    // A book's age rating as a tag: "R19" (adults only) and "R15" (teens), as NovelDB's index lists them.
    function ageTags(group, card) {
        var rating = Math.max(group.ageRating || 0, card.ageRating || 0);
        return rating === 19 ? ['R19'] : rating === 15 ? ['R15'] : [];
    }
    function hasTag(group, card) {
        // A book's status and age rating count as tags ("Completed", "Ongoing", "Hiatus", "R19", "R15").
        var listed = (card.tags || []).concat(group.tags || [], [card.status || '', group.status || ''], ageTags(group, card)).map(tagWords);
        return current.tags.some(function (tag) {
            var want = tagWords(tag);
            return listed.some(function (have) { return have === want || have.indexOf(want) >= 0; });
        });
    }
    // A book found by a tag search whose page lists tags, none of them a picked tag: not what was asked for.
    function tagWords(tag) { return String(tag || '').toLowerCase().replace(/[\s_\-]+/g, ' ').trim(); }
    // A book NovelDB found by the tag stays: its tags may spell it another way ("Genderbend (TS)").
    function offTag(group, card) {
        if (view !== 'search' || !current.tags.length || current.text || group.verified) return false;
        var listed = (card.tags || []).concat(ageTags(group, card)).map(tagWords).filter(Boolean);
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
        if (group.rating) out.push('★ ' + group.rating + (group.ratings ? ' (' + shortCount(group.ratings) + ')' : ''));
        if (group.likes) out.push('♥ ' + shortCount(group.likes));
        if (group.views) out.push('👁 ' + shortCount(group.views));
        if (group.chapters) out.push('📄 ' + shortCount(group.chapters));
        return out;
    }
    // A book's status on a card, as one sign (the details panel keeps the word; the tag menu searches it).
    function statusIcon(status) {
        var text = String(status || '');
        if (/hiatus|paused|on hold|휴재|연재\s*중단|断更|停更|休載/i.test(text)) return '⏸️';
        if (/complet|finished|ended|완결|完结|完結|完本|已完/i.test(text)) return '✅';
        if (/ongoing|on going|serial|연재|连载|連載/i.test(text)) return '🔄';
        return text;
    }
    // A count as a card shows it: 3,429,619 is "3.4M", 120,856 "121K" (the details panel keeps every digit).
    function shortCount(value) {
        var text = String(value || '').trim();
        if (!/^[\d,.\s]+$/.test(text)) return text;  // already short ("1.2M"), or not a plain number
        var n = Number(text.replace(/[,\s]/g, ''));
        if (!isFinite(n)) return text;
        var unit = function (size, letter) {
            var v = n / size;
            return (v < 10 ? Math.round(v * 10) / 10 : Math.round(v)) + letter;
        };
        return n >= 1e9 ? unit(1e9, 'B') : n >= 1e6 ? unit(1e6, 'M') : n >= 1e4 ? unit(1e3, 'K') : n.toLocaleString();
    }

    // Cards stream in one by one, and each synopsis redraws its card: they are drawn together once a
    // frame, then measured together (a measurement lays the page out, so one per card would lag).
    var dirty = new Set(), frame = 0;
    function render(group) {
        if (groups.get(group.key) !== group) return;  // replaced (the top list re-sorted) or dropped
        dirty.add(group);
        if (frame) return;
        var go = function () { if (frame) { cancelAnimationFrame(frame.raf); clearTimeout(frame.timer); frame = 0; drawCards(); } };
        frame = {raf: requestAnimationFrame(go), timer: setTimeout(go, 120)};  // a hidden page has no frames
    }
    // -- the sort menu (NovelDB's sorts): the shown cards in that order; a search sends it along ------------
    var SORT_KEY = 'noveldb.generic.sort', indexSortRequest = 0;
    function sortBy() { return $('sort').value; }
    function figure(value) {
        var text = String(value == null ? '' : value).replace(/,/g, '').trim();
        var found = text.match(/^([\d.]+)\s*([kmb])?/i);
        if (!found) return 0;
        return parseFloat(found[1]) * ({k: 1e3, m: 1e6, b: 1e9}[(found[2] || '').toLowerCase()] || 1);
    }
    var SORT_VALUES = {
        likes: function (g) { return figure(g.likes); }, views: function (g) { return figure(g.views); },
        chapters: function (g) { return figure(g.chapters); },
        'new': function (g) { return String(g.published || ''); }, updated: function (g) { return String(g.updated || ''); },
        title: function (g) { return String(g.title || '').toLowerCase(); }
    };
    // Cards already placed keep their places: books still coming (and figures filled in later) re-sorted the
    // whole grid, cards jumping under the finger (taps landed on another card or none) and the page laid out
    // again each time. New ones are sorted among themselves after them: a little out of order is fine. The
    // full order applies when the sort is picked.
    // A tag search shows npia's and sfc's books first (the other sites' tag lists are mostly BL), in the sort
    // picked among themselves.
    var LEAD_SOURCES = {npia: 1, sfc: 1};
    function leads(group) {
        if (view !== 'search' || !current.tags || !current.tags.length) return false;
        return group.sources.some(function (s) {
            return (s.noveldb && LEAD_SOURCES[s.noveldb.source]) || (NOVELDB_SITES[s.site] || []).some(function (n) { return LEAD_SOURCES[n]; });
        });
    }
    function orderCards(force) {
        var key = sortBy(), value = SORT_VALUES[key];
        if (referenceRun) {
            var cards = order.filter(function (group) { return group.element; }), last = -1;
            if (!force) {
                cards.forEach(function (group) { if (group.element.style.order !== '') last = Math.max(last, Number(group.element.style.order)); });
                cards = cards.filter(function (group) { return group.element.style.order === ''; });
            }
            cards.sort(function (a, b) { return (a.catalogue ? a.catalogue.rank : 1e9) - (b.catalogue ? b.catalogue.rank : 1e9); })
                .forEach(function (group, index) { group.element.style.order = String(last + 1 + index); });
            return;
        }
        if (view === 'search') {
            var placed = order.filter(function (group) { return group.element; });
            if (!force) {
                placed.forEach(function (group, index) { if (group.element.style.order !== '') return; group.element.style.order = String(index); });
                return;
            }
            value = value || function (group) { return group.indexSortOrder == null ? 1e9 : group.indexSortOrder; };
            var indexed = placed.filter(function (group) { return group.sources.some(function (source) { return source.source === 'noveldb' || source.noveldb; }); });
            indexed = indexed.map(function (group, index) { return {group: group, index: index, value: value(group)}; });
            indexed.sort(function (a, b) {
                var missing = (a.value === '' || a.value === 0) - (b.value === '' || b.value === 0);
                if (missing) return missing;
                var by = a.value < b.value ? -1 : a.value > b.value ? 1 : 0;
                return (!key || key === 'title' ? by : -by) || a.index - b.index;
            });
            var next = 0;
            placed.forEach(function (group, index) {
                if (group.sources.some(function (source) { return source.source === 'noveldb' || source.noveldb; })) group = indexed[next++].group;
                group.element.style.order = String(index);
            });
            return;
        }
        var tagged = view === 'search' && current.tags && current.tags.length;
        if (!value && !tagged) { order.forEach(function (g) { if (g.element) g.element.style.order = ''; }); return; }
        value = value || function () { return ''; };  // the order the books came in, after npia's and sfc's
        var ascending = key === 'title';
        var placed = order.filter(function (g) { return g.element; });
        var last = -1;
        if (!force) {
            placed.forEach(function (g) { if (g.element.style.order !== '') last = Math.max(last, Number(g.element.style.order)); });
            placed = placed.filter(function (g) { return g.element.style.order === ''; });
            if (!placed.length) return;
        }
        placed.map(function (g, i) { return [g, value(g), i, leads(g) ? 0 : 1]; })
            .sort(function (a, b) {
                if (a[3] !== b[3]) return a[3] - b[3];
                var x = a[1], y = b[1];
                var unknown = (x === '' || x === 0) - (y === '' || y === 0);  // books without the figure last
                if (unknown) return unknown;
                var by = x < y ? -1 : x > y ? 1 : 0;
                return (ascending ? by : -by) || a[2] - b[2];
            })
            .forEach(function (entry, i) { entry[0].element.style.order = String(last + 1 + i); });
    }
    // Once a list (a search, a top list): the cards in the full order a few seconds after the first came,
    // when most are in and their figures (a book's publishing date) filled in; then they stay put until the
    // list is pulled down past its top. Kept places alone left a sort such as New in the order the
    // books came in.
    var reshuffle = {list: -1, timer: 0};
    function reshuffleOnce() {
        if (view === 'search' || referenceRun) return;
        if (reshuffle.list === searchId) return;
        reshuffle.list = searchId;
        clearTimeout(reshuffle.timer);
        var list = searchId;
        reshuffle.timer = setTimeout(function () { if (list === searchId) { orderCards(true); evenRowsSoon(); } }, 3000);
    }
    function drawCards() {
        var drawn = Array.from(dirty).filter(drawCard);
        dirty.clear();
        if (drawn.length) { orderCards(); reshuffleOnce(); evenRowsSoon(); }
        if (drawn.length && cardsTranslated) { knownTranslations(drawn); translateCardsSoon(drawn); }
        fitTitles(drawn.map(function (group) { return group.element.querySelector('.title'); }));
        fitTagRows(drawn.map(function (group) { return group.element.querySelector('.tags'); }));
        empty();
    }
    function drawCard(group) {
        if (groups.get(group.key) !== group) return false;
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
        if (element.parentNode !== $('grid')) $('grid').appendChild(element);  // hidden until show() places it
        var inCart = group.sources.some(function (s) { return cart.has(s.url); });
        var more = group.sources.length > 1 ? ' +' + (group.sources.length - 1) : '';
        var status = statusIcon(group.status), shown = figures(group).filter(Boolean);
        var statusTag = status ? ' <span class="status">' + esc(status) + '</span>' : '';
        var synopsis = group.description
            ? (synopsisText(group.description) ? '<div class="synopsis">' + esc(synopsisText(group.description)) + '</div>' : '')
            : (group.loaded ? '' : '<div class="loading"><i></i><i></i><i></i></div>');
        // The cover drawn before stays while its address is the same: a card redraws whenever it learns
        // something (another site, its synopsis, fresh figures), and a new image each time restarted the
        // download, so the busiest cards (the first ones) kept their placeholder.
        var oldCover = element.querySelector('.cover');
        var keepCover = !!(group.cover && oldCover && oldCover.querySelector('img, canvas')
            && (element.__cover === group.cover || element.__cover === group.original));
        element.innerHTML = '<div class="cover' + (group.cover ? ' loading' : '') + '">'
            + (group.cover ? '<img loading="lazy" referrerpolicy="no-referrer" alt="" src="' + esc(group.cover) + '">' : '')
            + ageBadge(group) + '</div>'
            + '<span class="site" title="' + esc(group.sources.map(function (s) { return s.site + (s.ms != null ? ' (' + seconds(s.ms) + ')' : ''); }).join(', ')) + '">'
            + esc(group.sources[0].site + more) + '</span>'
            // The download artwork: greyed out until the book is in the link cart.
            + '<button class="add' + (inCart ? ' in' : '') + (config.downloadIcon ? ' art' : '') + '" title="'
            + (inCart ? 'In the link cart (click to remove)' : 'Add to the link cart') + '" type="button"'
            + (config.downloadIcon ? ' style="background-image:url(' + config.downloadIcon + ')"' : '') + '>'
            + (config.downloadIcon ? '' : inCart ? '✓' : '+') + '</button>'
            // Compact: the status right after the author (a tap searches their books); with no author, at the
            // head of the figures (a long title wraps it onto a row of its own), else after the title. The
            // figures are shortened (the original title, the kind and every digit are in the details panel).
            + '<div class="body"><div class="title">' + esc(group.title) + (group.author || shown.length ? '' : statusTag) + '</div>'
            + (group.author ? '<div class="meta"><a class="author" data-author>' + esc(group.author) + '</a>' + statusTag + '</div>' : '')
            + (shown.length ? '<div class="figures">' + esc((group.author ? shown : [status].concat(shown).filter(Boolean)).join(' · ')) + '</div>' : '')
            + (group.tags.length ? '<div class="tags">' + shownTags(group.tags).slice(0, 24).map(function (t) { return '<span>' + esc(t) + '</span>'; }).join('') + '</div>' : '')
            + synopsis + '</div>';
        element.querySelector('.add').addEventListener('click', function () { toggleCart(group); });
        var authorLink = element.querySelector('[data-author]');
        if (authorLink) authorLink.addEventListener('click', function (event) { event.stopPropagation(); searchAuthor(group.author); });
        if (keepCover) {
            element.replaceChild(oldCover, element.querySelector('.cover'));
            var badge = oldCover.querySelector('.age');
            if (badge) badge.remove();  // the rating may have arrived since the cover was drawn
            oldCover.insertAdjacentHTML('beforeend', ageBadge(group));
            show(group, wanted(group) && !(group.held && !group.verified));
            return true;
        }
        element.__cover = group.cover;
        var image = element.querySelector('.cover img');
        if (image && imaging.low && !/^data:/.test(group.cover) && !group.cacheAsked) {
            // The small copy made before with these settings, from the offline cache; else the cover itself.
            group.cacheAsked = true;
            var wanted_ = group.cover;
            image.removeAttribute('src');
            ask({type: 'genericCover', key: coverKey(wanted_)}).then(function (data) {
                if (group.cover !== wanted_) return;
                if (data && data.dataUrl) { group.original = wanted_; group.cover = data.dataUrl; image.dataset.small = '1'; }
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
        show(group, wanted(group) && !(group.held && !group.verified));
        return true;
    }
    // -- Google Translate: a book's details and the reader's chapter, in the device's language ----------
    // The language translations go into (the settings' "Into"): this device's own unless one is picked.
    var TRANSLATE_KEY = 'noveldb.generic.translateTo';
    var DEVICE_LANGUAGE = String(navigator.language || 'en').split('-')[0].toLowerCase() || 'en';
    var translatePicked = '';
    try { translatePicked = localStorage.getItem(TRANSLATE_KEY) || ''; } catch (e) {}
    function translateTo() { return translatePicked || DEVICE_LANGUAGE; }
    // Google's many-texts address: one translation per text, in their order (the one-text address, the texts
    // a line each, merged or split lines often, and each such batch was asked again in halves, many rounds).
    var TRANSLATE_URL = 'https://translate.googleapis.com/translate_a/t?client=gtx&sl=auto&tl=';
    // Translations kept per language (this page's, and the last TRANSLATE_KEEP of them across visits, in the
    // browser's storage): a card drawn again or met in another search shows its translation at once.
    // A book's details the site wrote: its title, original title, synopsis and tags (the app's labels stay).
    var TRANSLATED_PARTS = '.details h2, .details .rawtitle, .details .desc, .details .chips .chip';
    var TRANSLATE_KEEP = 3000, TRANSLATE_STORE = 'noveldb.generic.translations';
    var translated = new Map();  // language + "|" + text -> its translation
    try {
        (JSON.parse(localStorage.getItem(TRANSLATE_STORE) || '[]') || []).forEach(function (entry) {
            // A "translation" that is the text itself is left behind (one kept from a failed request would
            // stop that text from ever being asked again).
            if (Array.isArray(entry) && entry.length === 2 && entry[0].slice(entry[0].indexOf('|') + 1) !== entry[1]) {
                translated.set(entry[0], entry[1]);
            }
        });
    } catch (e) {}
    var translateSaveTimer = 0;
    function keepTranslations() {
        clearTimeout(translateSaveTimer);
        translateSaveTimer = setTimeout(function () {
            var entries = Array.from(translated.entries()).filter(function (entry) {
                return entry[0].slice(entry[0].indexOf('|') + 1) !== entry[1];
            }).slice(-TRANSLATE_KEEP);
            try { localStorage.setItem(TRANSLATE_STORE, JSON.stringify(entries)); } catch (e) {
                try { localStorage.setItem(TRANSLATE_STORE, JSON.stringify(entries.slice(-Math.floor(TRANSLATE_KEEP / 3)))); } catch (e2) {}
            }
        }, 1500);
    }
    function translationOf(text) { return translated.get(translateTo() + '|' + text); }
    function rememberTranslation(text, translation) {
        var key = translateTo() + '|' + text;
        translated.delete(key);  // the newest last: the oldest go first when the store is full
        translated.set(key, translation);
        keepTranslations();
    }
    /** `texts` in translateTo(), in their order (a text Google can't translate comes back as it is). They go in
     * batches of about 4,500 characters (and 120 texts), all asked at once: the shown cards are one or two
     * requests, a chapter a few. */
    function googleTranslate(texts) {
        var seen = new Set();  // a text shown many times (a tag on many cards) asked once
        var wanted = texts.filter(function (text) {
            if (!text.trim() || translationOf(text) != null || seen.has(text)) return false;
            seen.add(text);
            return true;
        });
        var batches = [], batch = [], size = 0;
        wanted.forEach(function (text) {
            if (size && (size + text.length > 4500 || batch.length >= 120)) { batches.push(batch); batch = []; size = 0; }
            batch.push(text); size += text.length + 1;
        });
        if (batch.length) batches.push(batch);
        return Promise.all(batches.map(translateBatch)).then(function () {
            return texts.map(function (text) { var out = translationOf(text); return out == null ? text : out; });
        });
    }
    /** Google's answer (JSON) to a translate request: asked directly, else through the app (the desktop's page is
     *  a local file, and Qt lets it ask no other site), as the LNReader plugins' requests go. */
    var translateFailure = '';  // why the last request failed, for the settings' button to say
    function translateRequest(url, body) {
        var headers = {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'};
        var viaApp = function () {
            return ask({type: 'genericFetch', request: {url: url, method: 'POST', headers: headers, data: body}}).then(function (reply) {
                if (!reply || reply.error || reply.status >= 400 || !reply.responseBase64) {
                    translateFailure = !reply ? 'no answer' : reply.error || ('HTTP ' + reply.status);
                    return null;
                }
                var binary = atob(reply.responseBase64), bytes = new Uint8Array(binary.length);
                for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                try { return JSON.parse(new TextDecoder().decode(bytes)); } catch (e) { translateFailure = 'an answer that is not a translation'; return null; }
            });
        };
        if (location.protocol === 'file:') return viaApp();
        return fetch(url, {method: 'POST', body: body, headers: headers})
            .then(function (response) { return response.ok ? response.json() : null; })
            .catch(function () { return null; })
            .then(function (data) { return data || viaApp(); });
    }
    /** One request for `texts`: each a `q` of its own, and the answer a translation per text ([translation,
     *  language], or the translation alone when the language is given). */
    function translateBatch(texts) {
        var body = texts.map(function (text) { return 'q=' + encodeURIComponent(text); }).join('&');
        return translateRequest(TRANSLATE_URL + encodeURIComponent(translateTo()), body)
            .then(function (data) {
                if (!Array.isArray(data)) return;
                if (texts.length === 1 && typeof data[0] === 'string' && data.length === 2) data = [data];
                texts.forEach(function (text, i) {
                    var out = Array.isArray(data[i]) ? data[i][0] : data[i];
                    if (typeof out === 'string' && out.trim()) rememberTranslation(text, out.trim());
                });
            }, function () {});
    }
    /** The text nodes of `root` (an element, or a list of them: one request for all) swapped for their
     * translation, or back to the original (`back`). */
    function translateNodes(root, back) {
        var nodes = [];
        (Array.isArray(root) ? root : [root]).forEach(function (one) {
            var walker = document.createTreeWalker(one, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                var node = walker.currentNode;
                if (node.parentNode && /^(SCRIPT|STYLE|BUTTON)$/.test(node.parentNode.nodeName)) continue;
                if (back ? node.__original != null : node.nodeValue.trim()) nodes.push(node);
            }
        });
        if (back) {
            nodes.forEach(function (node) { node.nodeValue = node.__original; node.__original = null; });
            return Promise.resolve();
        }
        nodes = nodes.filter(function (node) { return node.__original == null; });
        return googleTranslate(nodes.map(function (node) { return node.nodeValue; })).then(function (out) {
            nodes.forEach(function (node, i) {
                if (out[i] === node.nodeValue) return;
                node.__original = node.nodeValue;
                var lead = node.nodeValue.match(/^\s*/)[0], tail = node.nodeValue.match(/\s*$/)[0];
                node.nodeValue = lead + out[i] + tail;
            });
        });
    }

    // The shown cards' titles, tags and synopses translated (the settings menu); on, the cards that come in or
    // are drawn again are translated too, until it is switched off.
    var cardsTranslated = false;
    function cardParts(groups) {
        var parts = [];
        groups.filter(function (g) { return g.element && g.element.style.display !== 'none'; }).forEach(function (g) {
            var fromNovelDB = g.sources.some(function (s) { return s.source === 'noveldb'; });
            Array.prototype.slice.call(g.element.querySelectorAll('.body .title, .body .tags span, .body .synopsis')).forEach(function (part) {
                if (!(fromNovelDB && part.classList.contains('title'))) parts.push(part);  // NovelDB's English title stays
            });
        });
        return parts;
    }
    /** A card drawn again (its synopsis or figures came in) with the cards translated: the words already
     *  translated swapped at once, as it is drawn (it showed the original, then the translation again). */
    function knownTranslations(groups) {
        cardParts(groups).forEach(function (part) {
            var walker = document.createTreeWalker(part, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                var node = walker.currentNode, text = node.nodeValue;
                if (node.__original != null || !text.trim()) continue;
                var out = translationOf(text);
                if (out == null || out === text) continue;
                node.__original = text;
                node.nodeValue = text.match(/^\s*/)[0] + out + text.match(/\s*$/)[0];
            }
        });
    }
    function translateCards(groups) {
        var parts = cardParts(groups);  // every card's together: a few requests for all of them
        // The titles fitted again to their new words (a translation is longer or shorter: it overflowed or stayed small).
        var titles = parts.filter(function (part) { return part.classList.contains('title'); });
        return translateNodes(parts, !cardsTranslated).then(function () { fitTitles(titles); });
    }

    // Cards drawn as a search streams in, gathered for a moment: one request for many draws, not one each.
    var cardsToTranslate = [], cardsTimer = 0;
    function translateCardsSoon(groups) {
        cardsToTranslate.push.apply(cardsToTranslate, groups);
        if (cardsTimer) return;
        cardsTimer = setTimeout(function () {
            var groups = cardsToTranslate;
            cardsToTranslate = []; cardsTimer = 0;
            if (cardsTranslated) translateCards(groups);
        }, 400);
    }

    // NovelDB's age badges, at the cover's bottom right: 19+ (adults only) and 15 (teens).
    function ageBadge(group) {
        if (group.ageRating === 19) return '<span class="age r19" title="Adults only (19+)" aria-label="Rated 19+">19+</span>';
        if (group.ageRating === 15) return '<span class="age r15" title="Teens (15+)" aria-label="Rated 15+">15</span>';
        return '';
    }
    // Cards keep their places once shown (no reshuffling as books come and go). A card shown for the
    // first time takes the first free slot (one a removed card left), else goes at the end; a book
    // that leaves the list leaves its slot to the next one (see drop). The exact order is not kept.
    function show(group, visible) {
        var element = group.element;
        if (visible && !group.placed) {
            group.placed = true;
            var hole = $('grid').querySelector('.hole');
            if (hole) $('grid').replaceChild(element, hole); else $('grid').appendChild(element);
        }
        element.style.display = visible ? '' : 'none';
    }
    function leaveSlot(element) {
        var hole = document.createElement('div');
        hole.className = 'card hole';
        // Its place in a sort (a hole without one went first: the first row of a sorted top list stood empty).
        hole.style.order = element.style.order;
        $('grid').replaceChild(hole, element);
        setTimeout(closeSlots, 1500);  // nothing loading any more: no book will come to fill it
    }
    /** Slots no book came to fill, once nothing more is loading: closed (the cards after them move up). */
    function closeSlots() {
        if (running || more.loading) return;
        $('grid').querySelectorAll('.hole').forEach(function (hole) { hole.remove(); });
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
        // The cover's own site as the referrer (the card's first site may have changed since).
        ask({type: 'genericImage', url: group.cover, referer: group.coverFrom || group.sources[0].url}).then(function (data) {
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
        if (image.dataset.small || !image.naturalWidth) return;
        var original = group.cover;
        // Only a cover much bigger than the card is made smaller: one up to twice the size it is shown at
        // (in device pixels) is kept exactly as the site sent it, since re-encoding a small thumbnail only
        // blurs it. A bigger one shrinks by the resolution setting, never below 1.5 times the shown size.
        var card = image.closest('.cover');
        var shown = Math.max(1, ((card && card.clientWidth) || image.clientWidth || 180) * (window.devicePixelRatio || 1));
        // With compression off only a huge cover is made smaller (an original of 4000 x 6000 pixels takes
        // ~90 MB decoded), to twice the shown size at high quality: the card looks the same.
        var huge = !imaging.low;
        if (image.naturalWidth <= shown * (huge ? 4 : 2)) { image.dataset.small = '1'; return; }
        var scale = huge ? shown * 2 / image.naturalWidth : Math.min(1, Math.max(shown * 1.5 / image.naturalWidth, imaging.resolution / 100));
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
                    if (!/^data:/.test(original)) group.original = original;  // for compression turned off later
                    group.cover = reader.result;  // later renders reuse the small copy
                    image.src = reader.result;
                    if (!/^data:/.test(original) && !huge) send({type: 'genericCoverPut', key: coverKey(original), dataUrl: reader.result});
                };
                reader.readAsDataURL(blob);
            }, 'image/webp', huge ? 0.92 : imaging.quality / 100);
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
    // The settings' two searches (both on unless switched off, for testing one without the other): NovelDB's
    // index, and the deep search of NovelDB's sites. A query says only the ones that are off.
    var SEARCHES_KEY = 'noveldb.generic.searches';
    var searches = Object.assign({noveldb: true, deep: true, full: false}, store(SEARCHES_KEY) || {});
    function searchesIn(query) {
        if (!searches.noveldb) query.noveldb = false;
        if (!searches.deep) query.deep = false;
        if (searches.full) query.full = true;
        // The sites picked in the Sites panel: the search asks only them (and NovelDB for their books).
        if (siteFilter.size) query.only = Array.from(siteFilter);
        return query;
    }
    $('useNovelDB').checked = searches.noveldb;
    $('useDeep').checked = searches.deep;
    $('useFull').checked = searches.full;
    [['useNovelDB', 'noveldb'], ['useDeep', 'deep'], ['useFull', 'full']].forEach(function (pair) { ['input', 'change'].forEach(function (type) {
        $(pair[0]).addEventListener(type, function () {
            if (searches[pair[1]] === this.checked) return;
            searches[pair[1]] = this.checked;
            store(SEARCHES_KEY, searches);
            if (view === 'search' && current.queries && current.queries.length) search();
            else if (view === 'top' && pair[1] === 'full') showTop();
        });
    }); });
    $('language').value = language;
    $('language').addEventListener('change', function () {
        language = $('language').value;
        store(LANG_KEY, language);
        if (view === 'top') showTop();
    });
    $('preferredSite').addEventListener('change', function () {
        var kind = topKind();
        preferredSites[kind] = this.value;
        store(PREFERRED_SITES_KEY, preferredSites);
        delete topOnly[kind];
        if (view === 'top') showTop();
    });
    // "change" too: some phones' web views send no "input" for a checkbox.
    ['imgLow', 'imgQuality', 'imgResolution'].forEach(function (id) { ['input', 'change'].forEach(function (type) {
        $(id).addEventListener(type, function () {
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
                    else if (group.original) group.cover = group.original;
                    group.cacheAsked = false;
                    group.coverTried = false;
                    if (group.element) { group.element.__cover = null; render(group); }  // drawn again
                });
            }, 400);
        });
    }); });
    $('settingsButton').addEventListener('click', function (event) {
        event.stopPropagation();
        $('sitespanel').classList.remove('open');
        $('settingspanel').classList.toggle('open');
        showPreferredSites();
        showImaging();
    });
    // A tap anywhere outside the Sites and settings panels closes them: on a touch down, which every
    // part of the page sends (a phone sends no click for a tap on plain text or an empty area). Their
    // own buttons toggle them on the click that follows.
    document.addEventListener('pointerdown', function (event) {
        if (event.target.closest('.popover, #sitesButton, #settingsButton')) return;
        applyPicks();
        $('sitespanel').classList.remove('open');
        $('settingspanel').classList.remove('open');
    }, true);
    // A tap on the dimmed page around the details panel closes it (the backdrop is the dialog itself).
    $('dialog').addEventListener('click', function (event) {
        if (event.target !== $('dialog')) return;
        var box = $('dialog').getBoundingClientRect();
        var outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
        if (outside) $('dialog').close();
    });

    // The title at the largest size (13px down to 7px) that shows all of it in its box: the box is two
    // lines of 13px text tall, and smaller text gets more lines in it, so it shrinks only as far as needed.
    var TITLE_BOX = 2 * 13 * 1.25;
    function fit(title) { fitTitles([title]); }
    /** Card titles at the size that fits their two lines, many at once: every change first, then every
     *  measurement (a size tried and measured title by title made the browser lay the page out up to a dozen
     *  times per card, and the page lagged with many cards). A title too long at the size the first
     *  measurement suggests is clamped at 7 px. */
    function fitTitles(titles) {
        titles = titles.filter(function (title) { return title && title.isConnected && title.closest('.card') && title.closest('.card').style.display !== 'none'; });
        if (!titles.length) return;
        titles.forEach(function (title) {
            title.classList.remove('clamp');
            title.style.webkitLineClamp = '';
            title.style.fontSize = '';
        });
        var heights = titles.map(function (title) { return title.scrollHeight; });  // one layout for all
        var long = [];
        titles.forEach(function (title, i) {
            if (heights[i] <= TITLE_BOX + 1) return;  // most titles fit at full size
            // Text area grows with the square of the size: the size whose height fits, to the half pixel below.
            var size = Math.floor(13 * Math.sqrt((TITLE_BOX + 1) / heights[i]) * 2) / 2;
            title.style.fontSize = Math.max(7, Math.min(12.5, size)) + 'px';
            long.push(title);
        });
        var after = long.map(function (title) { return title.scrollHeight; });  // a second layout, for those only
        long.forEach(function (title, i) {
            if (after[i] <= TITLE_BOX + 1) return;
            title.style.fontSize = '7px';
            title.classList.add('clamp');
            title.style.webkitLineClamp = String(Math.max(2, Math.floor(TITLE_BOX / (7 * 1.25))));
        });
    }

    function empty() {
        var shown = order.filter(wanted).length;
        var text = '';
        if (!order.length && view === 'top') text = running ? 'Loading the top ' + TOP_NAMES[top.kind] + '…' : 'Nothing in the top lists right now. Type a title, or pick tags with the Tags button.';
        else if (!order.length && !running) text = 'Nothing found.';
        // Books found, all hidden by the type menu or the picked sites (an R19 search with Manga picked, left from
        // a top list, showed "nothing"): how many, and a button that shows them.
        var widen = '';
        if (order.length && !shown) {
            var bySite = siteFilter.size && order.some(function (g) { var k = $('kind').value; return k === 'all' || kindOf(g) === k; });
            text = (bySite ? order.length + ' books found, none from the picked sites (yet).'
                : order.length + ' books found, no ' + $('kind').selectedOptions[0].text.toLowerCase() + ' among them.');
            widen = bySite ? 'sites' : 'kind';
        }
        $('empty').textContent = text;
        if (widen) {
            var button = document.createElement('button');
            button.type = 'button';
            button.id = 'emptyWiden';
            button.style.marginLeft = '10px';
            button.textContent = widen === 'sites' ? 'Show all sites' : 'Show all types';
            button.onclick = function () {
                if (widen === 'sites') { siteFilter.clear(); drawSites(); widenSoon(); }
                else $('kind').value = 'all';
                refilter();
            };
            $('empty').appendChild(button);
        }
        $('empty').style.display = text ? '' : 'none';
    }

    function toggleCart(group) {
        var url = group.sources[0].url;
        var inCart = group.sources.filter(function (s) { return cart.has(s.url); });
        if (inCart.length) inCart.forEach(function (s) { send({type: 'cartRemove', url: s.url, title: group.title}); });
        else send({type: 'cartAdd', url: url, title: group.title, source: ''});
    }

    // A title as an exact match compares it (catalog-app.js exactTitle): letters and digits only, one case and form.
    var EXACT_GRACE_MS = 3000;
    function exactTitle(title) { return String(title || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ''); }
    function add(list, id) {
        if (id !== undefined && id !== searchId) return;
        (list || []).forEach(function (card) {
            if (card.empty) {
                var known = byUrl.get(key(card.url));
                if (known) drop(known, card.url);
                return;
            }
            if (view === 'top' && notInTop(card)) return;
            var catalogueMatch = card.catalogMatch && ['mangak.io', 'mangadex.org', 'comix.to', 'comix.ws'].indexOf(hostOf(card.url)) >= 0;
            if (card.via === 'name' && current.text && !card.catalogue && !catalogueMatch && ![card.title, card.originalTitle].concat(card.alternateTitles || []).some(function (title) {
                return relevant(title || '', current.text);
            })) return;
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
            // A title search found a book of exactly that title: the plugins stop being asked (lnreaderSearch).
            if (group && card.via === 'name' && !(current.tags || []).length && current.text && !current.exact
                    && [card.title, card.originalTitle].some(function (t) { return exactTitle(t) === exactTitle(current.text); })) {
                current.exact = true;
            }
            if (group) {
                (card.tags || []).forEach(function (tag) { resultTags.set(tag, (resultTags.get(tag) || 0) + 1); });
                if (card.verified) group.verified = true;
                if (tagOnly() && card.via === 'tag' && !group.verified && !group.confirmed) holdForTag(group);
                render(group);
            }
        });
        if (view === 'search') { countLine(); searchSave(); }
        showMoreLoader();
    }
    function shownCount() { return order.filter(function (g) { return !(g.held && !g.verified); }).length; }

    // -- details for the cards in view -------------------------------------------------------------
    var queue = [], loading = 0;
    var observer = window.IntersectionObserver ? new IntersectionObserver(function (entries) {
        var inView = [];
        entries.forEach(function (entry) {
            var group = entry.target.__group;
            if (entry.isIntersecting && group && !group.loaded && !group.asked) {
                group.asked = true;
                queue.push(group);
            }
            if (entry.isIntersecting && group) inView.push(entry.target.querySelector('.title'));
        });
        fitTitles(inView);
        pump();
    }, {rootMargin: '200px'}) : null;
    function pump() {
        // A NovelDB book's details are one small read of its synopsis: never behind the sites' book pages.
        // Taken off the queue before any is sent: an answer can come back at once and pump again.
        var noveldb = queue.filter(function (group) { return group.sources[0] && group.sources[0].noveldb; });
        if (noveldb.length) {
            queue = queue.filter(function (group) { return noveldb.indexOf(group) < 0; });
            noveldb.forEach(function (group) {
                if (groups.has(group.key)) send({type: 'genericDetails', id: searchId, card: strip(group, group.sources[0])});
            });
        }
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
    // How much a book page told: its synopsis counts most, then tags, the author and the figures.
    function richness(card) {
        return (synopsisText(card.description) ? 4 : 0) + ((card.tags || []).length ? 2 : 0) + (card.author ? 1 : 0)
            + (card.chapters || card.rating || card.views ? 1 : 0);
    }
    // A book page in: the site that told the most leads the card (its name on it, the first to read),
    // however much slower it answered; while the card still lacks a synopsis or tags, the next site's
    // page is read too (atsu.moe shows neither, novelbuddy both).
    function moreDetails(group, card) {
        var asked = group.detailsAsked || (group.detailsAsked = {});
        asked[key(card.url)] = true;
        var source = group.sources.find(function (s) { return key(s.url) === key(card.url); });
        if (source) source.richness = richness(card);
        var first = group.sources[0];
        if (source && first !== source && !(first.noveldb && first.noveldb.source === 'npia')
                && source.richness > (first.richness == null ? -1 : first.richness)) {
            group.sources.splice(group.sources.indexOf(source), 1);
            group.sources.unshift(source);
        }
        if (synopsisText(group.description) && group.tags.length) return false;
        var next = group.sources.find(function (s) { return !asked[key(s.url)] && !s.noveldb; });
        if (!next || Object.keys(asked).length >= 4) return false;
        asked[key(next.url)] = true;
        send({type: 'genericDetails', id: searchId, card: strip(group, next)});
        return true;
    }
    function thin(group) { return !synopsisText(group.description) && !group.tags.length; }
    // Sites whose books' pages tell nothing (atsu.moe): after three such books, none of theirs in the top lists.
    var THIN_KEY = 'noveldb.generic.thinSites';
    var thinSites = store(THIN_KEY) || {};
    function thinSite(site) {
        if (!site) return;
        thinSites[site] = (thinSites[site] || 0) + 1;
        store(THIN_KEY, thinSites);
    }
    function thinSiteNow(site) { return (thinSites[site] || 0) >= 3; }
    function strip(group, source) {
        return {url: source.url, title: group.title, author: group.author, cover: group.cover, description: group.description,
                tags: group.tags, site: source.site, comic: group.comic, source: source.source, noveldb: source.noveldb, catalogue: group.catalogue, complete: false};
    }

    // -- the details panel -------------------------------------------------------------------------
    function fact(label, value) { return some(value) || /[a-z]/i.test(String(value || '')) ? '<div><span>' + esc(label) + '</span>' + esc(value) + '</div>' : ''; }
    // The details' tags in two rows; the rest wait behind a "+N" chip that shows them all.
    function foldTags(box, group) {
        if (!box || group.tagsOpen || !box.children.length) return;
        var limit = box.firstElementChild.offsetTop + 2 * (box.firstElementChild.offsetHeight + 4) - 2;
        var over = function (chip) { return chip.offsetTop + chip.offsetHeight > limit; };
        var hidden = Array.prototype.filter.call(box.children, over);
        if (!hidden.length) return;
        var more = document.createElement('span');
        more.className = 'chip more';
        box.appendChild(more);
        hidden.forEach(function (chip) { chip.style.display = 'none'; });
        more.textContent = '+' + hidden.length;
        // The "+N" chip itself must fit in the two rows: the last shown tags make room for it.
        while (over(more)) {
            var last = Array.prototype.filter.call(box.children, function (c) { return c !== more && c.style.display !== 'none'; }).pop();
            if (!last) break;
            last.style.display = 'none';
            hidden.unshift(last);
            more.textContent = '+' + hidden.length;
        }
        more.onclick = function (event) {
            event.stopPropagation();
            group.tagsOpen = true;
            hidden.forEach(function (chip) { chip.style.display = ''; });
            more.remove();
        };
    }
    var openGroup = null;  // the book whose details panel was opened last
    function details(group) {
        var dialog = $('dialog');
        // A card on its way out of the list (dropped, its sites gone) opens nothing: its panel had no site
        // to show and failed before it appeared.
        if (!group.sources.length || !groups.has(group.key)) return;
        openGroup = group;
        var show = function () {
            if (!group.sources.length) return;
            var inCart = group.sources.some(function (s) { return cart.has(s.url); });
            var rating = group.rating ? '★ ' + group.rating + (group.ratings ? ' (' + group.ratings + ' ratings)' : '') : '';
            // The panel shows the cover as the site sent it: the card's copy is made small for the card.
            var full = (group.covers || []).filter(function (c) { return !(group.failedCovers || []).includes(c); })[0] || group.cover;
            // One row a site: one book's page found twice on a site (its own search and a web search's) is one.
            var seen = {}, sites = group.sources.filter(function (source) {
                var site = String(source.site || hostOf(source.url)).replace(/^www\./, '');
                return seen[site] ? false : (seen[site] = true);
            });
            // The cover with the book's own page under it; the actions show the app's artwork.
            dialog.innerHTML = '<div class="details"><div class="side"><div class="cover">'
                + (full ? '<img referrerpolicy="no-referrer" alt="" src="' + esc(full) + '">' : '') + '</div>'
                + '<div class="pagebuttons"><button type="button" data-act="open" title="Source page"><span class="long">Source page</span><span class="short">Source</span></button>'
                + '<button type="button" data-act="translate" title="Translate with Google" aria-label="Translate with Google">🌐</button></div></div>'
                // The head (title, author, kind and the figures) sits beside the cover; on a phone the rest goes
                // full width under both. The status is in the kind's line, so the figures leave it out.
                + '<div class="info"><div class="head"><h2>' + esc(group.title) + '</h2>'
                + (group.rawTitle ? '<div class="rawtitle">' + esc(group.rawTitle) + '</div>' : '')
                + (group.author ? '<div class="meta"><a class="author" data-author>' + esc(group.author) + '</a></div>' : '')
                + '<div class="meta">' + esc([KIND_NAMES[kindOf(group)], group.status].filter(Boolean).join(' · ')) + '</div>'
                + '<div class="facts">' + fact('Rating', rating) + fact('Likes', group.likes) + fact('Views', group.views)
                + fact('Chapters', group.chapters) + '</div>'
                + '<div class="facts">' + fact('Published', group.published) + fact('Updated', group.updated) + '</div></div>'
                + '<div class="chips">' + shownTags(group.tags).map(function (tag) { return '<span class="chip">' + esc(tag) + '</span>'; }).join('') + '</div>'
                + (synopsisText(group.description) ? '<div class="desc">' + esc(synopsisText(group.description)) + '</div>'
                    : group.loaded || group.description ? '<div class="desc">No description on the book page.</div>'
                    : '<div class="loading" style="margin:10px 0"><i></i><i></i><i></i><i></i></div>')
                + (sites.length < 2 ? '' : '<div class="sources"><h4>On ' + sites.length + ' sites</h4>' + group.sources.map(function (source, index) {
                    if (sites.indexOf(source) < 0) return '';
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
            // Google Translate for the site's words (title, original title, synopsis, tags; the app's labels
            // stay). The panel is drawn again as the book's details arrive: a translation asked for is kept.
            var translateButton = dialog.querySelector('[data-act=translate]');
            var applyTranslation = function () {
                var back = !group.translateDetails;
                translateButton.disabled = true;
                // With NovelDB's card the original title is the book's own (and NovelDB has its English one).
                var fromNovelDB = group.sources.some(function (s) { return s.source === 'noveldb'; });
                var parts = Array.prototype.slice.call(dialog.querySelectorAll(TRANSLATED_PARTS))
                    .filter(function (part) { return !(fromNovelDB && part.classList.contains('rawtitle')); });
                translateNodes(parts, back).then(function () {
                    translateButton.disabled = false;
                    translateButton.classList.toggle('on', !back);
                    translateButton.title = back ? 'Translate with Google' : 'Show the original';
                });
            };
            translateButton.onclick = function () { group.translateDetails = !group.translateDetails; applyTranslation(); };
            // With the cards translated, a book's details open translated too (its 🌐 shows them as written).
            if (group.translateDetails == null) group.translateDetails = cardsTranslated;
            if (group.translateDetails) applyTranslation();
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
            var byAuthor = dialog.querySelector('[data-author]');
            if (byAuthor) byAuthor.onclick = function () { dialog.close(); searchAuthor(group.author); };
            dialog.querySelectorAll('.chip').forEach(function (chip) {
                chip.onclick = function () { dialog.close(); picked = [chip.textContent]; $('query').value = ''; showPicked(); search(); };
            });
            if (dialog.open) foldTags(dialog.querySelector('.details .chips'), group);
        };
        group.onDetails = show;
        show();
        // Open as a modal panel; one left open without being modal (or hidden) is opened again.
        if (dialog.open && !dialog.matches(':modal')) dialog.close();
        if (!dialog.open) {
            try { dialog.showModal(); } catch (e) { dialog.show(); }
            foldTags(dialog.querySelector('.details .chips'), group);
        }
        // An open book's page is read now, not after the cards waiting before it (that took 20 s).
        if (!group.loaded && !group.timer) {
            group.asked = true;
            queue = queue.filter(function (other) { return other !== group; });
            loading++;
            send({type: 'genericDetails', id: searchId, card: strip(group, group.sources[0])});
            group.timer = setTimeout(function () { finishDetails(group); }, 45000);
        }
    }
    function download(group, source) {
        send({type: 'genericDownload', url: source.url, title: group.title});
    }

    // -- LNReader: the sites' own search, run here --------------------------------------------------
    // How quickly each LNReader plugin's search answered, remembered across searches: {id: {ms, ok, at}}.
    // A search asks the quickest first and lets the ones that failed or were slow rest a day.
    var PLUGIN_SPEED_KEY = 'noveldb.generic.pluginSpeed', PLUGIN_SECONDS = 15, PLUGIN_REST_HOURS = 24;
    var pluginSpeeds = store(PLUGIN_SPEED_KEY) || {}, pluginSpeedTimer = 0;
    function notePlugin(id, ms, ok) {
        pluginSpeeds[id] = {ms: ms, ok: ok, at: Date.now()};
        clearTimeout(pluginSpeedTimer);
        pluginSpeedTimer = setTimeout(function () { store(PLUGIN_SPEED_KEY, pluginSpeeds); }, 1000);
    }
    function pluginResting(id) {
        var known = pluginSpeeds[id];
        return !!known && !known.ok && Date.now() - known.at < PLUGIN_REST_HOURS * 3600e3;
    }
    function sourceResponseRank(host) {
        var profile = config.responseProfile || {}, measured = (profile.measurements || {})[host];
        if (measured && Date.now() - measured.at * 1000 < (measured.ok ? 30 : 1) * 86400000) return measured.ok ? measured.ms : 1000000;
        var index = (profile.preferred || []).indexOf(host);
        return index >= 0 ? 6000 + index : 100000;
    }
    function pluginRank(id) {
        var known = pluginSpeeds[id];
        if (known && Date.now() - known.at < 30 * 86400000) return known.ok ? known.ms : 1000000;
        var plugin = plugins().find(function (entry) { return entry.id === id; });
        return plugin ? sourceResponseRank(plugin.site) : 100000;
    }

    // `options`: {active: plugins at once (8), seconds: each one's time (25), remember: note their speeds}.
    function lnreaderRun(id, jobs, each, options) {
        options = options || {};
        var index = 0, active = 0, most = options.active || 8, limit = (options.seconds || 25) * 1000;
        // Failures count only once some plugin answered: offline, every one fails and none should rest.
        var answered = false, failed = [];
        function remember(plugin, ms, ok) {
            if (ok) { answered = true; failed.splice(0).forEach(function (f) { notePlugin(f[0], f[1], false); }); }
            if (ok || answered) notePlugin(plugin, ms, ok); else failed.push([plugin, ms]);
        }
        setRunning(1);
        return new Promise(function (resolve) {
            var ended = false, stopping = false;
            function end() { if (!ended) { ended = true; setRunning(-1); resolve(); } }
            function next() {
                if (ended) return;
                if (id !== searchId) { end(); return; }
                // `stopWhen`: no more plugins are started, and the ones asking get EXACT_GRACE_MS to answer.
                if (!stopping && options.stopWhen && options.stopWhen()) {
                    stopping = true;
                    index = jobs.length;
                    setTimeout(end, EXACT_GRACE_MS);
                }
                while (active < most && index < jobs.length) {
                    var job = jobs[index++];
                    active++;
                    job.started = Date.now();
                    within(job.run(), limit, '"timeout"').then(function (raw) {
                        var found = parse(raw), ms = Date.now() - this.started;
                        if (options.remember && id === searchId && (!Array.isArray(found) || found.length)) remember(this.plugin.id, ms, Array.isArray(found) && ms < limit);
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
                if (active === 0 && index >= jobs.length) end();
            }
            next();
            // An exact match may come from elsewhere while every plugin slot is busy: looked for every second too.
            if (options.stopWhen) var watch = setInterval(function () { if (ended) clearInterval(watch); else next(); }, 1000);
        });
    }
    function lnreaderCard(plugin, item, via) {
        return {url: item.url, title: item.name, cover: item.cover || '', site: plugin.site, comic: !!plugin.comic,
                source: 'lnreader', plugin: item.plugin || plugin.id, path: item.path, via: via, complete: false, ms: item.ms,
                verified: !!item.verified};
    }
    function lnreaderSearch(id, queries) {
        var every = plugins().slice()
            .sort(function (a, b) { return pluginRank(a.id) - pluginRank(b.id); });
        if (!every.length || !window.__LNR_search) return;
        var jobs = [];
        queries.forEach(function (q) {
            if (q.mode === 'tag' && isAgeTag(q.query)) return;  // NovelDB's own rating: no plugin lists it
            every.forEach(function (plugin) {
                if (q.only && q.only.length ? q.only.indexOf(plugin.site) < 0 : !q.full && (pluginResting(plugin.id) || pluginRank(plugin.id) >= 12000)) return;
                if ((!q.only || !q.only.length) && jobs.filter(function (job) { return job.query === q; }).length >= (q.full ? 100 : 8)) return;
                jobs.push({plugin: plugin, query: q, run: function () {
                    return q.mode === 'tag' && window.__LNR_genre ? window.__LNR_genre(plugin.id, q.query) : window.__LNR_search(plugin.id, q.query, 1);
                }});
            });
        });
        progress.expected += jobs.length;
        lnreaderRun(id, jobs, function (job, found) {
            if (job.query.mode !== 'tag') found = found.filter(function (item) { return relevant(item.name, job.query.query); });
            add(found.slice(0, 15).map(function (item) { return lnreaderCard(job.plugin, item, job.query.mode); }), id);
        }, {active: 3, seconds: PLUGIN_SECONDS, remember: true, stopWhen: function () { return !queries.some(function (q) { return q.full; }) && !!current.exact; }})
            .then(function () { finishedSearch(id); });
    }

    // -- the top 100s: the sites' popular lists, the busiest sites counting most ----------------------
    // Web novels (and All) come from the LNReader plugins' popular lists, light novels are the ones of those
    // lists that are light novels, and manga, manhwa and comics come from the comic sites' popular or
    // ranking pages, read by the app. Each list is kept for 12 hours.
    var TOP_NAMES = {all: 'novels and comics', webnovel: 'web novels', lightnovel: 'light novels', manga: 'manga',
                     manhwa: 'manhwa', comic: 'comics'};
    var top = {kind: 'webnovel', scores: new Map(), first: 0, timer: 0, limit: TOP_SIZE, only: [], seen: {}};
    // The sites picked in the Sites panel for each kind: their own lists, in their own order.
    var topOnly = {};
    var PREFERRED_SITES_KEY = 'noveldb.generic.preferredSites';
    var preferredSites = store(PREFERRED_SITES_KEY) || {};
    // Hosts focused on josei or BL. Genre labels also exclude individual books on mixed sites.
    var EXCLUDED_TOP_HOSTS = new Set(['chrysanthemumgarden.com', 'dummynovels.com', 'knoxt.space',
        'hyacinthbloom.com', 'whitemoonlightnovels.com', 'moonlightnovel.com']);
    function excludedTopSite(site) {
        var name = String(site || '').toLowerCase().replace(/^https?:\/\//, '').split('/')[0]
            .replace(/^(www|m)\./, '').replace(/:\d+$/, '');
        return /josei|yaoi|danmei|boys?-?love|blnovels/.test(name)
            || Array.from(EXCLUDED_TOP_HOSTS).some(function (host) { return name === host || name.endsWith('.' + host); });
    }
    function showPreferredSites() {
        var kind = topKind(), selected = preferredSites[kind] || '';
        var sites = new Set(config.sites || []);
        plugins().forEach(function (p) { if (p.site) sites.add(p.site); });
        Object.keys(NOVELDB_SITES).forEach(function (site) { sites.add(site); });
        BUILTIN_SITES.forEach(function (site) { sites.add(site); });
        if (selected) sites.add(selected);
        $('preferredSite').innerHTML = '<option value="">Automatic</option>'
            + Array.from(sites).filter(function (site) { return !excludedTopSite(site); }).sort().map(function (site) {
                var label = NOVELDB_SITES[site] ? NOVELDB_SITES[site].join(' / ') : site;
                if (site === BUILTIN_SITES[0]) label = 'qdn';
                if (site === BUILTIN_SITES[1]) label = 'floo';
                return '<option value="' + esc(site) + '">' + esc(label) + '</option>';
            }).join('');
        $('preferredSite').value = selected;
    }
    // NovelDB's sources by the site their books are on: a pick of one shows its Top list from NovelDB's
    // index (every book of the source, the most read first). Written with escapes (the codename rule).
    var NOVELDB_SITES = {
        '\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com': ['npia'],
        '\u006b\u0061\u006b\u0061\u006f.com': ['kpage'],
        '\u0073\u0066\u0061\u0063\u0067.com': ['sfc'],
        '\u006e\u0061\u0076\u0065\u0072.com': ['nweb', 'nseries'],
        '\u006a\u006f\u0061\u0072\u0061.com': ['jara'],
        '\u006d\u0075\u006e\u0070\u0069\u0061.com': ['mpia'],
        '\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com': ['rbooks']
    };
    // NovelDB's sources by their books' language: the Korean and Chinese top lists come from its index.
    var NDB_LANGS = {ko: ['npia', 'kpage', 'nweb', 'nseries', 'jara', 'mpia', 'rbooks'], zh: ['sfc']};
    // The built-in scrapers' other sites: their Top lists come from their own popular pages.
    var BUILTIN_SITES = ['\u0071\u0069\u0064\u0069\u0061\u006e.com', '\u0066\u0061\u006c\u006f\u006f.com', 'fenrirealm.com', 'xiyuwx.com', 'yeduji.com', 'kobo.com', '1qxs.com', '69shuba.tw'];
    // How the shown top list is made: NovelDB's sources, or the sites (by how many the app knows).
    function topRecipe() {
        return top.ndbOnly ? 'ndb:' + (top.ndbSources || []).join(',')
            : 'references-v1:' + (config.sites || []).length + ':' + TOP_SIZE;
    }
    function topKind() { var kind = $('kind').value; return kind === 'all' ? 'webnovel' : kind; }
    function isComicKind(kind) { return kind === 'manga' || kind === 'manhwa' || kind === 'comic'; }
    // -- the search on screen, kept: Android ends a page left in the background (another app opened) and the app
    // opens it again, which showed the top list; the search comes back as it was, its cards and place too.
    var SEARCH_KEY = 'noveldb.generic.search.v2', SEARCH_HOURS = 12, searchSaveTimer = 0, searchHistory = [];
    function searchSave(now) {
        clearTimeout(searchSaveTimer);
        if (!now) { searchSaveTimer = setTimeout(function () { searchSave(true); }, 1000); return; }
        if (view !== 'search' || !current.queries) return;
        var items = order.slice(0, 300).map(function (group) {
            var first = group.sources[0];
            var original = (group.covers && group.covers[0]) || (/^data:/.test(group.cover) ? '' : group.cover);
            return Object.assign({}, strip(group, first), {cover: original, plugin: first.plugin, path: first.path, ms: first.ms,
                verified: !!group.verified || !group.held, ageRating: group.ageRating || 0,
                likes: group.likes, views: group.views, chapters: group.chapters, published: group.published, updated: group.updated,
                status: group.status, rating: group.rating, originalTitle: group.rawTitle || '', catalogue: group.catalogue,
                more: group.sources.slice(1).map(function (s) {
                    return {url: s.url, title: group.title, site: s.site, source: s.source, plugin: s.plugin, path: s.path, comic: group.comic, ms: s.ms};
                })});
        });
        var saved = {time: Date.now(), current: {text: current.text, tags: current.tags, queries: current.queries, byId: current.byId,
                                                      author: current.author || '', reference: current.reference || null},
                           kind: $('kind').value, sort: sortBy(), sites: Array.from(siteFilter), scroll: window.scrollY,
                           heading: $('heading').innerHTML, items: items};
        store(SEARCH_KEY, saved);
        return saved;
    }
    function rememberSearch() {
        searchHistory.push(view === 'search' ? searchSave(true) : {top: true});
        if (searchHistory.length > 10) searchHistory.shift();
    }
    function searchRestore(kept) {
        kept = kept || store(SEARCH_KEY);
        if (!kept || !kept.items || Date.now() - kept.time > SEARCH_HOURS * 3600e3) return false;
        searchId++;
        reset('search');
        current = kept.current;
        referenceRun = current.reference ? {id: searchId, options: current.reference.options, page: current.reference.page,
            more: current.reference.more !== false, seen: new Set(kept.items.filter(function (item) { return item.catalogue; }).map(function (item) { return item.catalogue.key; })), queue: [], active: 0} : null;
        referenceSortLabels(referenceRun && referenceRun.options.kind);
        picked = (current.tags || []).slice();
        showPicked();
        $('query').value = current.author ? '' : current.text || '';
        if (kept.kind) $('kind').value = kept.kind;
        if (kept.sort) $('sort').value = kept.sort;
        (kept.sites || []).forEach(function (site) { siteFilter.add(site); });
        $('heading').innerHTML = kept.heading;
        if ($('backTop')) $('backTop').onclick = function () { $('query').value = ''; picked = []; showPicked(); showTop(); };
        running = 0;
        kept.items.forEach(function (item, index) {
            var group = merge(item, index + 1);
            if (!group) return;
            (item.more || []).forEach(function (m) { merge(m); });
            if (item.verified) group.verified = true;
            render(group);
        });
        status(order.length + ' books found; scroll for more.');
        // Back where it was once the cards are drawn (and the one reshuffle is not wanted: they are in their order).
        reshuffle.list = searchId;
        setTimeout(function () { orderCards(true); window.scrollTo(0, kept.scroll || 0); }, 400);
        return true;
    }
    window.addEventListener('scroll', function () { if (view === 'search') searchSave(); }, {passive: true});
    document.addEventListener('visibilitychange', function () { if (document.hidden) searchSave(true); });
    window.addEventListener('pagehide', function () { searchSave(true); });

    function showTop(goingBack) {
        referenceRun = null;
        referenceSortLabels(null);
        if (goingBack !== true && view === 'search') rememberSearch();
        store(SEARCH_KEY, null);  // the top list asked for: no search to come back to
        searchId++;
        reset('top');
        current = {text: '', tags: []};
        var kind = topKind();
        var sites = kind in topOnly ? topOnly[kind] : preferredSites[kind] ? [preferredSites[kind]] : [];
        top = {kind: kind, scores: new Map(), first: 0, timer: 0, limit: TOP_SIZE,
            preferred: !(kind in topOnly) && !!preferredSites[kind],
            only: sites.filter(function (site) { return !excludedTopSite(site); }), seen: {}, page: 1};
        showPreferredSites();
        var chosen = $('language').selectedOptions[0];
        $('heading').textContent = 'Top ' + TOP_NAMES[top.kind] + (top.only.length ? ' · ' + top.only.join(', ')
            : language === 'all' ? '' : ' · ' + chosen.text);
        $('sitesButton').style.display = '';
        if (canReference() && !top.only.length && !NDB_LANGS[baseLang()]) {
            startReferences({kind: $('kind').value, text: '', tags: [], sort: sortBy(), top: true});
            return;
        }
        if (!top.only.length && NDB_LANGS[baseLang()] && !isComicKind(top.kind)) {
            // Korean and Chinese: NovelDB's list (kept or read anew), and its next pages as it is scrolled.
            top.ndbSources = NDB_LANGS[baseLang()].slice();
            top.ndbOnly = true;
            top.plugins = [];
            top.others = [];
        }
        var cached = !top.only.length && store(TOP_KEY + '.' + top.kind + '.' + language);
        // A list made another way than this code makes it now (NovelDB's sources or the sites, and how many
        // sites there are) is not shown: a change of how lists are made shows at once.
        if (cached && cached.how !== topRecipe()) cached = null;
        if (cached && cached.items && cached.items.length) {
            if (top.ndbOnly) top.ndbPage = Math.ceil(cached.items.length / 40);
            cached.items.forEach(function (item, index) {
                if (notInTop(item) || (!top.ndbOnly && !inLanguage(item))) return;
                var group = merge(item, index + 1);
                if (group) { (item.more || []).forEach(function (m) { merge(m); }); render(group); }
            });
            status('The most read ' + TOP_NAMES[top.kind] + ' on the top sites, as of ' + new Date(cached.time).toLocaleString() + '.');
            // A kept list: its sites are asked again only when it is scrolled past its end.
            if (Date.now() - cached.time < TOP_HOURS * 3600e3) { top.cached = true; empty(); return; }
        }
        loadTop(searchId);
    }
    // Kept out of the top lists (a search still finds them): a book whose tags or title say so.
    var NOT_IN_TOP = /\b(josei|danmei|yaoi|smut|bl|boys?'?s? ?love|shounen[- ]?ai|(?<!young )adult[oa]?s?|hentai|r-?18)\b|\b1[89]\+|\+1[89]\b/i;
    function notInTop(item) {
        return excludedTopSite(item.site) || excludedTopSite(item.url)
            || NOT_IN_TOP.test(item.title || '') || (item.tags || []).some(function (tag) { return NOT_IN_TOP.test(tag); });
    }
    // A book whose page shows it does not belong (another language, nothing to tell) leaves the top list, but not
    // once it is on screen or above it: cards vanished as the list was scrolled and the ones after them moved up.
    // `always`: it leaves anyway (a book kept out of the top lists).
    function dropFromTop(group, always) {
        var box = !always && group.element && group.element.parentNode ? group.element.getBoundingClientRect() : null;
        if (box && box.height && box.top < window.innerHeight) return false;  // shown (a hidden card has no box)
        group.sources.slice().forEach(function (source) { drop(group, source.url); });
        topRedrawSoon();  // the next book in line takes its place
        return true;
    }
    function topCollect(card, weight, position, length) {
        if (view !== 'top') return;  // a search started meanwhile: its results own the page
        // Picked sites show whatever they list, in any language; so does NovelDB's list of a language.
        var picked = top.only.length > 0 || !!top.ndbOnly;
        if (!picked && !card.catalogue && window.NovelDBReferences && !window.NovelDBReferences.allowedTopSite(card.site || hostOf(card.url))) return;
        if (card.catalogue && notInTop(card.catalogue)) return;
        if (!card.catalogue && !picked && /\s[-–—:]\s*(?:main story|side stories|if stories)\s*[✅✔]?$/i.test(card.title || '')) {
            card = Object.assign({}, card, {title: card.title.replace(/\s[-–—:]\s*(?:main story|side stories|if stories)\s*[✅✔]?$/i, '')});
        }
        if (top.only.length && top.only.indexOf(card.site) < 0) return;
        if (top.ndbOnly && card.noveldb && card.originalTitle && !englishTitles()) {
            // The book's own title first (its English one second); "(English)" keeps NovelDB's English title.
            card = Object.assign({}, card, {title: card.originalTitle, originalTitle: card.title});
        }
        if (!card.title || JUNK.test(card.title) || NO_RESULTS.test(card.title) || (!picked && !inLanguage(card)) || notInTop(card)) return;
        if (!picked && thinSiteNow(card.site)) return;  // its books' pages tell nothing
        var k = groupKey(card);
        // Picked sites take turns: each one's first book, then each one's second, and so on.
        var place = picked ? (top.seen[card.site] = (top.seen[card.site] || 0) + 1) * 100 + top.only.indexOf(card.site) : top.first++;
        var entry = top.scores.get(k) || {key: k, score: 0, first: place, cards: []};
        entry.score += weight * (1 - 0.7 * position / Math.max(1, length));
        entry.cards.push(card);
        top.scores.set(k, entry);
    }
    // The list as books arrive: a new book goes at the end and stays where it is (no reshuffling as more
    // sites answer); a book another site lists too just gains that site. It stops at 100 books.
    function topRedraw() {
        if (view !== 'top') return;
        var entries = Array.from(top.scores.values()).sort(function (a, b) { return a.first - b.first; });
        entries.forEach(function (entry) {
            if (entry.excluded) return;
            // A site found meanwhile to be in another language (its synopses): its books leave the list.
            // (Not in NovelDB's list of a language: its books are that language whatever their titles. Not
            // for picked sites either: they show whatever they list, in any language.)
            if (entry.group && !top.ndbOnly && !top.only.length && !inLanguage(entry.cards[0])) {
                if (dropFromTop(entry.group)) entry.excluded = true;
                return;
            }
            if (entry.group && !groups.has(entry.group.key)) { entry.excluded = true; return; }  // dropped from the list
            if (!entry.group) {
                if (order.length >= top.limit) return;
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
        if (referenceRun && !referenceRun.loading) referenceStatus(referenceRun);
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
        if (items.length && !top.only.length) store(TOP_KEY + '.' + top.kind + '.' + language, {time: Date.now(), items: items, how: topRecipe()});
    }
    // The LNReader sites a novel top list reads: the picked ones, else the busiest of the language and kind.
    function topPlugins(kind, all) {
        var wantLn = kind === 'lightnovel' || language === 'ja-en';  // translated Japanese novels
        var chosen = plugins().filter(function (p) {
            return !p.comic && !excludedTopSite(p.site) && /\./.test(p.site || '') && (!window.NovelDBReferences || window.NovelDBReferences.allowedTopSite(p.site) || p.ln) && (language === 'all' || !pluginLangs[p.id] || pluginLangs[p.id] === cardLang())
                && (allPlugins.length ? !!p.ln === wantLn : true);
        }).sort(function (a, b) { return pluginRank(a.id) - pluginRank(b.id); });
        return all ? chosen : chosen.filter(function (plugin) { return !pluginResting(plugin.id) && pluginRank(plugin.id) < 12000; }).slice(0, 12);
    }
    function loadTop(id) {
        if (isComicKind(top.kind)) {
            // The comic sites' popular pages, read by the app (cards come back through add()).
            running = 0;
            setRunning(1);
            startProgress(0);
            status('Reading the popular pages of ' + (top.only.length ? top.only.join(', ') : 'the top ' + TOP_NAMES[top.kind] + ' sites') + '…');
            send(Object.assign({type: 'genericTop', id: id, kind: top.kind, language: cardLang()}, top.only.length ? {sites: top.only} : {}));
            return;
        }
        // Korean and Chinese web novels: NovelDB's index of those sites (every book, the most read first).
        if (!top.only.length && NDB_LANGS[baseLang()]) {
            top.plugins = [];
            top.others = [];
            top.ndbSources = NDB_LANGS[baseLang()].slice();
            top.ndbOnly = true;
            running = 0;
            startProgress(0);
            status('Reading the most read ' + TOP_NAMES[top.kind] + ' from NovelDB…');
            askNdbTop(id, false);
            return;
        }
        // Picked sites: NovelDB's sources from its index, LNReader's sites from their popular lists (read
        // here), any other site from its popular pages (read by the app).
        var picks = top.only;
        var chosen = picks.length
            ? picks.map(function (site) { return plugins().filter(function (p) { return p.site === site; })[0]; }).filter(Boolean)
            : topPlugins(top.kind);
        top.plugins = chosen;
        top.ndbSources = !picks.length && top.kind === 'webnovel' && (baseLang() === 'en' || language === 'all') ? Object.keys(LEAD_SOURCES) : [];
        picks.forEach(function (site) { (NOVELDB_SITES[site] || []).forEach(function (source) { top.ndbSources.push(source); }); });
        top.others = picks.filter(function (site) { return !NOVELDB_SITES[site] && !chosen.some(function (p) { return p.site === site; }); });
        if (!chosen.length && !top.ndbSources.length && !top.others.length) { empty(); return; }
        running = 0;
        startProgress(chosen.length);
        if (top.ndbSources.length) askNdbTop(id, false);
        if (top.others.length) {
            setRunning(1);
            send({type: 'genericTop', id: id, kind: top.kind, language: cardLang(), sites: top.others});
        }
        if (!chosen.length || !window.__LNR_popular) { status('Reading the Top lists of ' + picks.join(', ') + '…'); return; }
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
            // Every source read: the LNReader sites and the sites the app read (progress counts both).
            status(order.length ? 'The most read ' + TOP_NAMES[top.kind] + ' on ' + Math.max(plugins_.length, progress.expected) + ' ' + TOP_NAMES[top.kind] + ' sites, by their popular lists.'
                : top.only.length ? top.only.join(', ') + ' did not send a popular list (a bot check, or the site is down). Pick other sites in Sites.'
                : 'No ' + TOP_NAMES[top.kind] + ' in the popular lists right now.');
            empty();
        });
    }
    // A novel top list's next page: page `top.page + 1` of each of its sites' popular lists. A site that
    // ignores the page number sends its first page again, which adds no book, so the list ends.
    // The picked NovelDB sources' next page of books (`more`: as the list is scrolled); the app answers
    // with the cards and then `ndbTopDone`.
    function askNdbTop(id, more_) {
        top.ndbScrolled = more_;
        setRunning(1);
        send({type: 'genericNdbTop', id: id, sources: top.ndbSources, page: top.ndbPage || 0});
    }
    function topMoreNovels(id) {
        // Each part (the LNReader sites, NovelDB's sources, the other sites) reports with moreDone.
        var lnr = (top.plugins || []).length > 0, ndb = (top.ndbSources || []).length > 0 && top.ndbMore !== false;
        var others = (top.others || []).length > 0;
        more.parts = lnr + ndb + others;
        if (!more.parts) { window.NovelDBGeneric.moreDone(id, {more: false}); return; }
        if (ndb) askNdbTop(id, true);
        if (others) send({type: 'genericTopMore', id: id});
        if (!lnr) return;
        var page = top.page + 1, before = top.scores.size;
        var jobs = (top.plugins || []).map(function (plugin) {
            return {plugin: plugin, weight: 0.3, report: false, run: function () { return window.__LNR_popular(plugin.id, page, false); }};
        });
        lnreaderRun(id, jobs, function (job, found) {
            found.forEach(function (item, position) { topCollect(lnreaderCard(job.plugin, item, 'top'), job.weight, position, found.length); });
        }).then(function () {
            if (id !== searchId) return;
            top.page = page;
            window.NovelDBGeneric.moreDone(id, {more: top.scores.size > before});
        });
    }

    // -- NU/MU discovery and source correlation -----------------------------------------------------
    function canReference() { return !!window.NovelDBReferences; }
    function referenceIO(run) {
        function request(message, timeout) {
            var id, timer;
            return Promise.race([ask(message, function (value) { id = value; }), new Promise(function (resolve) {
                timer = setTimeout(function () { resolve({error: 'Timeout', cards: []}); }, timeout);
            })]).then(function (reply) { clearTimeout(timer); delete pending[id]; return reply; });
        }
        return {
            alive: function () { return referenceRun === run && run.id === searchId; },
            request: function (details) { return request({type: 'genericFetch', request: details}, 18000); },
            index: function (names) {
                return request({type: 'genericIndexSort', queries: names.map(function (name) { return {query: name.slice(0, 300), mode: 'name'}; })}, 30000)
                    .then(function (result) { return result && result.cards || []; });
            },
            web: function (query) {
                return request({type: 'genericSiteQuery', web: true, query: query.slice(0, 2000)}, 30000)
                    .then(function (result) { return {hits: result && result.cards || [], blocked: !!(result && result.googleBlocked)}; });
            },
            site: function (query) {
                if (query.query) query.query = query.query.slice(0, 300);
                return request(Object.assign({type: 'genericSiteQuery'}, query), 22000).then(function (result) { return result && result.cards || []; });
            }
        };
    }
    function referenceSources(kind) {
        var profile = config.responseProfile || {}, preferred = profile.preferred || [], measured = profile.measurements || {};
        return (config.sites || []).filter(function (site) {
            return window.NovelDBReferences.allowedTopSite(site) && (kind === 'all' || ((config.comic || []).indexOf(site) >= 0) === isComicKind(kind));
        })
            .map(function (site, index) { return {site: site, rank: index, speed: measured[site] && measured[site].ok ? measured[site].ms : preferred.indexOf(site) >= 0 ? 6000 + preferred.indexOf(site) : 1e6}; })
            .sort(function (a, b) { return a.speed - b.speed || a.rank - b.rank; }).slice(0, 100).map(function (item) { return item.site; });
    }
    function referenceOffer(run, cards) {
        if (referenceRun !== run || run.id !== searchId) return;
        if (run.options.top) {
            cards.forEach(function (card) { topCollect(card, 1, card.catalogue.rank, 100); });
            topRedrawSoon();
        } else { add(cards, run.id); if (!run.loading) referenceStatus(run); }
    }
    function referenceStatus(run) {
        var provider = run.options.kind === 'all' ? 'NU/MU' : isComicKind(run.options.kind) ? 'MU' : 'NU';
        status(provider + ' · ' + shownCount() + ' correlated books' + (run.more ? ' · scroll for more' : ''));
    }
    function referenceBackground(run) {
        var io = referenceIO(run), options = Object.assign({}, run.options, {sites: referenceSources(run.options.kind), full: searches.full, index: searches.noveldb});
        while (io.alive() && run.active < 2 && run.queue.length) {
            var ref = run.queue.shift();
            run.active++;
            window.NovelDBReferences.correlate(ref, options, io, function (cards) { referenceOffer(run, cards); })
                .catch(function () {}).then(function () { run.active--; referenceBackground(run); if (io.alive()) setTimeout(moreSoon, 300); });
        }
    }
    function startReferences(options) {
        send({type: 'genericStop', id: searchId});
        referenceSortLabels(options.kind);
        options.sort = sortBy();
        referenceRun = {id: searchId, options: options, page: 1, more: true, seen: new Set(), queue: [], active: 0};
        current.reference = {options: options, page: 1};
        referencePage();
    }
    function referenceSortLabels(kind) {
        var labels = isComicKind(kind) ? {new:'Year',updated:'Added',likes:'Rating',views:'Readers',chapters:'Chapters'}
            : kind ? {new:'New',updated:'Updated',likes:'Readers',views:'Rating',chapters:'Chapters'}
            : {new:'New',updated:'Updated',likes:'Likes',views:'Views',chapters:'Chapters'};
        Array.from($('sort').options).forEach(function (option) {
            if (labels[option.value]) option.textContent = labels[option.value];
            option.disabled = !!kind && ((!isComicKind(kind) && option.value === 'new') || ((isComicKind(kind) || kind === 'all') && option.value === 'chapters'));
        });
        if ($('sort').selectedOptions[0] && $('sort').selectedOptions[0].disabled) $('sort').value = '';
    }
    async function referencePage() {
        var run = referenceRun;
        if (!run || run.loading || !run.more) return;
        run.queue = run.queue || []; run.active = run.active || 0;
        var io = referenceIO(run), provider = run.options.kind === 'all' ? 'NU/MU' : isComicKind(run.options.kind) ? 'MU' : 'NU';
        run.loading = true; running = 0; setRunning(1); setMoreLoading(true);
        status('Searching ' + provider + ' · correlating reading sources…');
        try {
            var result = await window.NovelDBReferences.search(Object.assign({}, run.options, {page: run.page}), io);
            if (!io.alive()) return;
            if (run.page === 1 && !run.options.top && !result.records.length) throw new Error('No catalogue matches');
            var fresh = result.records.filter(function (ref) { return !run.seen.has(ref.key); });
            fresh.forEach(function (ref) { run.seen.add(ref.key); });
            var refs = fresh.filter(function (ref) { return !run.options.top || !notInTop(ref); });
            await window.NovelDBReferences.each(refs, 4, async function (ref) {
                if (!io.alive()) return;
                var prepared = await window.NovelDBReferences.prepare(ref, Object.assign({}, run.options, {index: searches.noveldb, full: searches.full}), io,
                    function (cards) { referenceOffer(run, cards); });
                if (io.alive()) { run.queue.push(prepared); referenceBackground(run); }
            });
            if (!io.alive()) return;
            run.more = !!result.more && (!result.records.length || !!fresh.length); run.page++;
            current.reference = {options: run.options, page: run.page, more: run.more};
            more.has = run.more;
            if (run.options.top) topRedraw();
            referenceStatus(run);
        } catch (error) {
            if (!io.alive()) return;
            if (run.page === 1) {
                referenceRun = null; current.reference = null;
                referenceSortLabels(null);
                running = 0; setMoreLoading(false);
                if (run.options.top) loadTop(run.id);
                else { setRunning(1); send({type: 'genericSearch', id: run.id, queries: current.queries}); lnreaderSearch(run.id, current.queries); }
                status(provider + ' unavailable · searching sources');
            } else { run.more = false; status(provider + ' unavailable · retry search for more'); }
        } finally {
            if (referenceRun === run && run.id === searchId) {
                run.loading = false; setRunning(-1); setMoreLoading(false); empty();
                setTimeout(moreSoon, 300);
            }
        }
    }

    // -- searching ---------------------------------------------------------------------------------
    function allTags() {
        var names = new Map();
        TAGS.forEach(function (tag) { names.set(tag.toLowerCase(), tag); });
        resultTags.forEach(function (_n, tag) { if (!names.has(tag.toLowerCase())) names.set(tag.toLowerCase(), tag); });
        return names;
    }
    function search(event, keepMenu) {
        referenceRun = null;
        referenceSortLabels(null);
        if (event && event.preventDefault) event.preventDefault();
        clearTimeout(searchTimer);
        if (!keepMenu) closeTags();
        var text = $('query').value.trim();
        if (!text && !picked.length) { showTop(); return; }
        // "author:Name" or "@Name": that author's books.
        var byAuthor = !picked.length && text.match(/^(?:author\s*:|@)\s*(.+)$/i);
        if (byAuthor) { searchAuthor(byAuthor[1]); $('query').value = text; return; }
        var queries = [];
        var byId = !picked.length && /^\s*(\d{3,12}|n\d{4}[a-z]{1,3})\s*$/i.test(text);
        if (byId) {
            queries.push({query: text, mode: 'id'});  // a novel ID: its book page on the sites that address books by ID
        } else if (text) {
            queries.push({query: text, mode: 'name'});
            if (!picked.length && allTags().has(text.toLowerCase())) queries.push({query: text, mode: 'tag'});
        }
        picked.slice(0, 5).forEach(function (tag) { queries.push({query: tag, mode: 'tag'}); });
        queries.forEach(function (q) { q.sort = sortBy(); searchesIn(q); });
        rememberSearch();
        searchId++;
        reset('search');
        current = {text: text, tags: picked.slice(), queries: queries, byId: byId};
        running = 0;
        setRunning(1);
        var label = [text ? (byId ? 'ID ' : '') + '“' + text + '”' : ''].concat(picked.map(function (t) { return '#' + t; })).filter(Boolean).join(' + ');
        $('heading').innerHTML = 'Results for ' + esc(label) + ' <a id="backTop">Top 100</a>';
        $('backTop').onclick = function () { $('query').value = ''; picked = []; showPicked(); showTop(); };
        startProgress(0);
        // The sites a search asks itself (the busiest; every other one is found through the web search).
        status('Searching NovelDB and Google for ' + label + '…');
        empty();
        if (!byId && canReference() && !siteFilter.size) {
            startReferences({kind: $('kind').value, text: text, tags: picked.slice(), sort: sortBy(), top: false});
        } else {
            send({type: 'genericSearch', id: searchId, queries: queries});
            if (!byId) lnreaderSearch(searchId, queries);
        }
    }
    // An author's books (a tap on an author's name): NovelDB lists books by author, and NovelDB's sites are
    // asked for their books by that name. A name of several
    // ("Shen Jian, 神见") searches each of them.
    function searchAuthor(name) {
        name = String(name || '').trim();
        if (!name) return;
        closeTags();
        picked = [];
        showPicked();
        $('query').value = '';
        var names = name.split(/\s*[,，、]\s*/).filter(Boolean).slice(0, 3);
        var queries = names.map(function (one) { return searchesIn({query: one, mode: 'author', sort: sortBy()}); });
        rememberSearch();
        searchId++;
        reset('search');
        current = {text: '', tags: [], queries: queries, byId: false, author: name};
        running = 0;
        setRunning(1);
        $('heading').innerHTML = 'Books by ' + esc(name) + ' <a id="backTop">Top 100</a>';
        $('backTop').onclick = function () { showTop(); };
        startProgress(0);
        status('Searching NovelDB and its sites for books by ' + name + '…');
        empty();
        window.scrollTo(0, 0);
        send({type: 'genericSearch', id: searchId, queries: queries});
    }
    function finishedSearch(id) {
        if (id !== searchId || running) return;
        status(shownCount() + ' books found in ' + seconds(Date.now() - progress.started) + noveldbCount()
               + (progress.noveldb > shownCount() ? '; scroll for more.' : '.') + (progress.googleBlocked ? ' · Google requires verification' : ''));
        empty();
    }

    // -- the tag menu ------------------------------------------------------------------------------
    function showPicked() {
        $('picked').innerHTML = picked.map(function (tag, index) {
            return '<span class="picked">' + esc(tag) + '<b data-unpick="' + index + '" title="Remove">×</b></span>';
        }).join('');
        $('picked').querySelectorAll('[data-unpick]').forEach(function (x) {
            x.onclick = function () { picked.splice(Number(x.dataset.unpick), 1); showPicked(); drawTags(); searchSoon(); };
        });
        $('tagCount').innerHTML = picked.length ? '<span class="badge">' + picked.length + '</span>' : '';
        $('tagsButton').classList.toggle('on', picked.length > 0);
    }
    function togglePick(tag) {
        var at = picked.findIndex(function (t) { return t.toLowerCase() === tag.toLowerCase(); });
        if (at >= 0) picked.splice(at, 1); else picked.push(tag);
        showPicked();
        drawTags();
        searchSoon();
    }
    // A change of the picked tags searches by itself, once the picking pauses (one search for several
    // tags picked in a row); the tag menu stays open meanwhile.
    var searchTimer = 0;
    function searchSoon() {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () { search(null, true); }, 700);
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
        // The picked tags lead the list (also ones typed in that no list has), the rest follow.
        var isPicked = new Set(picked.map(function (t) { return t.toLowerCase(); }));
        var listed = function (tag) {
            return TAGS.concat(Array.from(resultTags.keys())).some(function (t) { return t.toLowerCase() === tag.toLowerCase(); });
        };
        var common = picked.filter(function (tag) {
            return match(tag) && (TAGS.some(function (t) { return t.toLowerCase() === tag.toLowerCase(); }) || !listed(tag));
        }).concat(TAGS.filter(function (tag) { return match(tag) && !isPicked.has(tag.toLowerCase()); }));
        var found = Array.from(resultTags.keys()).filter(function (tag) {
            return match(tag) && !TAGS.some(function (t) { return t.toLowerCase() === tag.toLowerCase(); });
        }).sort(function (a, b) { return (isPicked.has(b.toLowerCase()) - isPicked.has(a.toLowerCase())) || resultTags.get(b) - resultTags.get(a); }).slice(0, 60);
        // NovelDB's own tags only while filtering: they are there to be found, not shown.
        var shown = new Set(common.concat(found).map(function (t) { return t.toLowerCase(); }));
        var more = filter ? ndbTagNames.filter(function (tag) { return match(tag) && !shown.has(tag.toLowerCase()); }).slice(0, 40) : [];
        var html = '';
        if (common.length) html += '<h3>Genres and tags</h3>' + chipList(common);
        if (found.length) html += '<h3>In these results</h3>' + chipList(found, resultTags);
        if (more.length) html += '<h3>More tags in NovelDB</h3>' + chipList(more);
        if (!common.length && !found.length && !more.length && filter) html += '<p>Press Enter to pick “' + esc($('tagFilter').value.trim()) + '”.</p>';
        $('tagLists').innerHTML = html;
        $('tagLists').querySelectorAll('[data-tag]').forEach(function (chip) {
            chip.onclick = function () { togglePick(chip.dataset.tag); };
        });
    }
    function closeTags() { $('tagmenu').classList.remove('open'); }
    // NovelDB's tag names (asked for the first time the menu opens), offered while filtering.
    var ndbTagNames = [], ndbTagsAsked = false;
    $('tagsButton').addEventListener('click', function () {
        var menu = $('tagmenu');
        menu.classList.toggle('open');
        if (menu.classList.contains('open')) {
            if (!ndbTagsAsked) { ndbTagsAsked = true; send({type: 'genericNdbTags'}); }
            drawTags();
            $('tagFilter').focus();
        }
    });
    $('tagFilter').addEventListener('input', drawTags);
    $('tagFilter').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            var text = $('tagFilter').value.trim();
            var known = allTags().get(text.toLowerCase())
                || ndbTagNames.filter(function (tag) { return tag.toLowerCase() === text.toLowerCase(); })[0];
            if (text) { togglePick(known || text); $('tagFilter').value = ''; drawTags(); }
        } else if (event.key === 'Escape') closeTags();
    });
    // Clear unpicks every tag, empties the filter and searches again at once (the Top list, with no title).
    $('tagClear').addEventListener('click', function () {
        picked = [];
        $('tagFilter').value = '';
        showPicked();
        drawTags();
        search(null, true);
    });
    $('tagSearch').addEventListener('click', function () { search(); });
    // A tap anywhere outside the menu closes it: on a touch down, which every part of the page sends (a
    // phone sends no click for a tap on plain text or an empty area), before the tap does anything else.
    document.addEventListener('pointerdown', function (event) {
        if (!event.target.closest('#tagmenu') && !event.target.closest('#tagsButton') && !event.target.closest('[data-unpick]')) closeTags();
    }, true);

    $('form').addEventListener('submit', search);
    // Clearing the search box (and with no tags picked) goes back to the Top 100.
    // Typing searches by itself once it pauses (0.8 s): two letters at least (one CJK character), and
    // not again for the text already searched. Enter or Search still search at once.
    // An input method (pinyin, kana, Hangul) composing a word puts partial text in the box: nothing is
    // searched until it finishes (a pause mid-word started a search that the next keystroke cancelled).
    var typingTimer = 0, composing = false;
    $('query').addEventListener('compositionstart', function () { composing = true; clearTimeout(typingTimer); });
    $('query').addEventListener('compositionend', function () { composing = false; typed(); });
    $('query').addEventListener('input', function (event) {
        if (composing || (event && event.isComposing)) { clearTimeout(typingTimer); return; }
        typed();
    });
    function typed() {
        clearTimeout(typingTimer);
        var text = $('query').value.trim();
        // The text of the search already running (the keyboard sent an event without a change): kept.
        if (view === 'search' && running && current.text === text) return;
        if (!text && !picked.length && view === 'search') {
            send({type: 'genericStop', id: searchId});
            showTop();
            return;
        }
        if (text.length < 2 && !/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/.test(text)) return;
        typingTimer = setTimeout(function () {
            if (composing || (view === 'search' && current.text === $('query').value.trim())) return;
            search(null);
        }, 800);
    }
    $('stop').addEventListener('click', function () {
        send({type: 'genericStop', id: searchId});
        searchId++;
        referenceRun = null;
        running = 0;
        setRunning(0);
        status(shownCount() + ' books found (stopped).');
    });
    // The sort is remembered; a change orders what is shown, and searches again for NovelDB's lists in that order.
    try { var savedSort = localStorage.getItem(SORT_KEY); if (savedSort != null) $('sort').value = savedSort; } catch (e) {}
    if ($('sort').selectedIndex < 0) $('sort').value = '';
    $('translateTo').value = translatePicked;
    if ($('translateTo').selectedIndex < 0) $('translateTo').value = translatePicked = '';
    $('translateTo').addEventListener('change', function () {
        translatePicked = this.value;
        try { localStorage.setItem(TRANSLATE_KEY, this.value); } catch (e) {}
    });
    // On, it stays on: the cards of the next searches and visits are translated too, until it is pressed again.
    var CARDS_TRANSLATED_KEY = 'noveldb.generic.cardsTranslated';
    function showCardsTranslated() {
        $('translateCards').classList.toggle('on', cardsTranslated);
        $('translateCards').textContent = cardsTranslated ? '🌐 Original text' : '🌐 Translate cards';
    }
    try { cardsTranslated = localStorage.getItem(CARDS_TRANSLATED_KEY) === '1'; } catch (e) {}
    showCardsTranslated();
    $('translateCards').addEventListener('click', function () {
        var button = this;
        cardsTranslated = !cardsTranslated;
        try { localStorage.setItem(CARDS_TRANSLATED_KEY, cardsTranslated ? '1' : ''); } catch (e) {}
        button.disabled = true;
        translateFailure = '';
        translateCards(order).then(function () {
            button.disabled = false;
            showCardsTranslated();
            if (cardsTranslated && translateFailure) status('Google Translate failed (' + translateFailure + '); the cards stay as written.');
        });
    });
    function refreshIndexSort() {
        if (view !== 'search' || !current.queries || current.byId) return;
        var queries = current.queries.filter(function (query) { return query.noveldb !== false && query.mode !== 'id'; });
        if (!queries.length) return;
        var list = searchId, request = ++indexSortRequest;
        if (more.loading) more.discardPendingSort = true;
        ask({type: 'genericIndexSort', queries: queries}).then(function (result) {
            if (list !== searchId || request !== indexSortRequest || !result || result.error) return;
            order.forEach(function (group) { group.indexSortOrder = 1e9; });
            add(result.cards || [], list);
            (result.cards || []).forEach(function (card, index) {
                var group = byUrl.get(key(card.url));
                if (group) group.indexSortOrder = index;
            });
            more.ndbPage = 1;
            more.ndbHas = !!result.more;
            more.has = more.has || !!result.more;
            requestAnimationFrame(function () { orderCards(true); });
        });
    }
    $('sort').addEventListener('change', function () {
        try { localStorage.setItem(SORT_KEY, sortBy()); } catch (e) {}
        if (referenceRun) { if (view === 'top') showTop(); else search(); return; }
        orderCards(true);
        if (view === 'search' && current.queries) current.queries.forEach(function (query) { query.sort = sortBy(); });
        refreshIndexSort();
    });
    $('kind').addEventListener('change', function () {
        if (referenceRun && referenceRun.options.kind !== $('kind').value) { if (view === 'top') showTop(); else search(); return; }
        if (view === 'top' && topKind() !== top.kind) { showTop(); return; }
        refilter();
    });
    window.addEventListener('resize', function () {
        clearTimeout(window.__fitTimer);
        window.__fitTimer = setTimeout(function () {
            fitTitles(order.filter(function (g) { return g.element; }).map(function (g) { return g.element.querySelector('.title'); }));
        }, 150);
    });

    // == the reader ===============================================================================
    // One chapter at a time, scraped when it is opened (the next one is read ahead). Paged like an
    // e-book (two pages side by side in a wide window) or one long scroll; the place is remembered.
    var R = {group: null, source: null, chapters: [], index: 0, page: 0, pages: 1, cache: new Map(), token: 0, lnreader: null,
             done: new Set(), pagesFor: new Map(), alive: {}, lastPages: 0};
    var readerPausedAt = null, readerPausedFor = 0;
    function readerNow() {
        return (readerPausedAt === null ? Date.now() : readerPausedAt) - readerPausedFor;
    }
    function readerVisibility(visible) {
        if (!visible) {
            if (readerPausedAt === null) readerPausedAt = Date.now();
            return;
        }
        if (readerPausedAt === null) return;
        readerPausedFor += Date.now() - readerPausedAt;
        readerPausedAt = null;
        if ($('reader').classList.contains('open')) {
            pumpPictures();
            readAhead();
        }
    }
    function readerImageUrl(src) {
        return /^noveldb-reader:[0-9a-f]{32}\/[0-9a-f]{64}\.[a-z0-9][a-z0-9.+-]{0,31}$/.test(src);
    }
    function readerDeadline(promise, ms, fallback) {
        var deadline = readerNow() + ms, timer;
        var timeout = new Promise(function (resolve) {
            timer = setInterval(function () { if (readerNow() >= deadline) resolve(fallback); }, 1000);
        });
        return Promise.race([promise, timeout]).finally(function () { clearInterval(timer); });
    }
    var LOOK_KEY = 'noveldb.reader.look.v2', look = Object.assign({size: 19, leading: 1.75, font: 'serif', theme: 'auto', layout: 'paged'}, store(LOOK_KEY) || {});
    // A phone reads smaller text by default. The text size stays the default until A− or A+ picks one
    // (a size saved before that rule counts as picked when it is not the old default, 19).
    var DEFAULT_SIZE = Math.min(window.screen ? window.screen.width : 9999, window.innerWidth || 9999) < 600 ? 16 : 19;
    if (look.sizeSet === undefined) look.sizeSet = look.size !== 19;
    if (!look.sizeSet) look.size = DEFAULT_SIZE;
    // Comics: Auto (a manga a page per image, a manhwa or other comic one long scroll), Pages or Scroll.
    // The older single choice (look.comic) came from the bar's switch alone, so it gives way to Auto.
    delete look.comic;
    if (['auto', 'paged', 'scroll'].indexOf(look.comicLayout) < 0) look.comicLayout = 'auto';
    var darkPage = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    function theme() {
        if (look.theme === 'auto' && appTheme) return 'app';
        return look.theme === 'auto' ? (darkPage && darkPage.matches ? 'dark' : 'light') : look.theme;
    }
    // The app's own theme (its accent, light or dark), when the app says it: the page takes its colours,
    // and the reader's Auto look reads in them.
    var appTheme = null;
    var THEME_VARS = {bg: '--bg', panel: '--panel', ink: '--ink', ink2: '--ink-2', line: '--line', accent: '--accent',
                      accentInk: '--accent-ink', chip: '--chip'};
    var READER_VARS = {bg: '--r-bg', ink: '--r-ink', ink2: '--r-ink-2', line: '--r-line'};
    var THEME_KEY = 'noveldb.generic.theme';  // the app's last colours: the page wears them before it hears from the app
    function setAppTheme(colors) {
        var valid = colors && typeof colors === 'object' ? colors : null;
        var root = document.documentElement.style;
        Object.keys(THEME_VARS).forEach(function (name) {
            var value = valid && /^#[0-9a-f]{6}$/i.test(valid[name] || '') ? valid[name] : '';
            if (value) root.setProperty(THEME_VARS[name], value); else root.removeProperty(THEME_VARS[name]);
        });
        appTheme = valid;
        if (valid) store(THEME_KEY, valid);
        document.documentElement.style.colorScheme = valid ? (valid.dark ? 'dark' : 'light') : '';
        applyLook();
    }
    // Novels read in the layout picked; comics in a layout of their own: the bar's switch for this book
    // (R.comicPick), else the one picked under Aa, where Auto goes by what the book is.
    function layout() {
        if (!R.comic) return look.layout;
        var pick = R.comicPick || look.comicLayout;
        if (pick !== 'auto') return pick;
        if (!R.comicKind) R.comicKind = R.group ? comicReading(R.group) : 'scroll';
        return R.comicKind;
    }
    // Auto's layout for a comic: a webtoon, manhwa or manhua (its tags, a webtoon site) is one long strip, read
    // in scroll even when a site also tags it manga or it came from a top list of manga (Solo Leveling opened
    // in pages); a manga is read in pages; anything else in scroll.
    var STRIP = /webtoons?|manhwa|manhuwa|mahwa|manhua|full[\s-]*colou?r|long[\s-]*strip|vertical|웹툰|韩漫|國漫|国漫|\.kr\b|toomics|tapas|lezhin|bilibili|kuaikan/i;
    function comicReading(group) {
        var text = [group.title, (group.tags || []).join(' '), (group.sources || []).map(function (s) { return s.site; }).join(' ')].join(' ');
        if (STRIP.test(text)) return 'scroll';
        return kindOf(Object.assign({}, group, {comic: true, topKind: ''})) === 'manga' || group.topKind === 'manga' ? 'paged' : 'scroll';
    }
    if (darkPage && darkPage.addEventListener) darkPage.addEventListener('change', function () { if ($('reader').classList.contains('open')) applyLook(); });

    // Full screen: the reader's bars hidden (a tap in the middle shows them), the page's own full screen
    // where the browser allows it, and the app asked to hide its own bars too (genericFullscreen).
    // The change is a fade: the page fades out where it is, the reader and the app change size, and the
    // page fades back in laid out again on the same text (not a page worked out from a fraction, which
    // landed somewhere else and looked like a jump). `instant` (the reader closing) skips the fade.
    function setFull(on, instant) {
        var reader = $('reader'), open = reader.classList.contains('open');
        if (!!on === !!R.full && !R.switching) return;
        if (instant || !open) {
            R.fullToken = (R.fullToken || 0) + 1;
            R.switching = false;
            reader.classList.remove('switching');
            switchFull(on);
            return;
        }
        var token = R.fullToken = (R.fullToken || 0) + 1;
        var anchor = R.switching ? R.switchAnchor : anchorAt();
        R.switching = true;
        R.switchAnchor = anchor;
        reader.classList.add('switching');
        setTimeout(function () {
            if (token !== R.fullToken) return;
            switchFull(on);
            // The app hides or shows its bars after hearing of it: wait for the page to stop changing size.
            var size = '', still = 0, started = Date.now();
            (function settle() {
                if (token !== R.fullToken) return;
                var v = $('viewport'), now = v.clientWidth + 'x' + v.clientHeight;
                still = now === size ? still + 1 : 0;
                size = now;
                if (still < 12 && Date.now() - started < 900) { requestAnimationFrame(settle); return; }
                R.switching = false;
                clearTimeout(R.resizeTimer);
                if (reader.classList.contains('open')) restoreAnchor(anchor);
                requestAnimationFrame(function () { if (token === R.fullToken) reader.classList.remove('switching'); });
            })();
        }, 140);
    }
    function switchFull(on) {
        R.full = !!on;
        var reader = $('reader');
        reader.classList.toggle('full', R.full);
        reader.classList.remove('bars');
        $('rFull').textContent = R.full ? '🗗' : '⛶';
        $('rFull').title = R.full ? 'Leave full screen (F)' : 'Full screen (F)';
        try {
            // The browser's own full screen only without an app around the page (the apps hide their own
            // bars instead; their web views handle a page's full screen badly).
            if (R.full && !window.NovelDBQt && document.documentElement.requestFullscreen && !document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(function () { /* the app's full screen only */ });
            } else if (!R.full && document.fullscreenElement) {
                document.exitFullscreen().catch(function () {});
            }
        } catch (e) { /* no full screen in this browser */ }
        send({type: 'genericFullscreen', on: R.full});
    }
    // Where the reader is, as the text on screen: the first block showing (the part of it on the page,
    // for one split over pages) and how far it sits from the top of a scroll.
    // Pages are measured from the flow's own left edge, not the screen's: a page turn slides there, so
    // the screen still shows the page before while it does.
    function anchorAt() {
        var flow = $('flow'), box = $('viewport').getBoundingClientRect(), scroll = layout() === 'scroll';
        var left = flow.getBoundingClientRect().left, width = R.pageWidth || box.width;
        var from = R.page * width, to = from + width;
        var blocks = flow.children;
        for (var i = 0; i < blocks.length; i++) {
            var parts = blocks[i].getClientRects();
            for (var k = 0; k < parts.length; k++) {
                var part = parts[k];
                if (!part.width && !part.height) continue;
                if (scroll ? part.bottom > box.top + 1 : part.right - left > from + 1 && part.left - left < to - 1) {
                    return {el: blocks[i], part: k, dy: part.top - box.top, at: readingAt(), scroll: scroll};
                }
            }
        }
        return {at: readingAt(), scroll: scroll};
    }
    // Lays the reader out again on that text (the old fraction when the text is gone).
    function restoreAnchor(anchor) {
        var flow = $('flow'), viewport = $('viewport');
        if (!anchor || !anchor.el || !flow.contains(anchor.el) || anchor.scroll !== (layout() === 'scroll')) {
            paginate(anchor ? anchor.at : readingAt());
            return;
        }
        paginate(0);
        var parts = anchor.el.getClientRects(), part = parts[Math.min(anchor.part, parts.length - 1)];
        if (!part) return;
        if (anchor.scroll) {
            viewport.scrollTop += part.top - viewport.getBoundingClientRect().top - anchor.dy;
            place();
        } else if (R.pageWidth) {
            R.page = Math.max(0, Math.min(R.pages - 1, Math.floor((part.left - flow.getBoundingClientRect().left + 1) / R.pageWidth)));
            place();
        }
    }
    function applyLook() {
        var reader = $('reader');
        reader.style.setProperty('--r-size', look.size + 'px');
        reader.style.setProperty('--r-leading', look.leading);
        reader.classList.toggle('sans', look.font === 'sans');
        reader.classList.toggle('sepia', theme() === 'sepia');
        reader.classList.toggle('dark', theme() === 'dark');
        Object.keys(READER_VARS).forEach(function (name) {
            var value = theme() === 'app' && /^#[0-9a-f]{6}$/i.test(appTheme[name] || '') ? appTheme[name] : '';
            if (value) reader.style.setProperty(READER_VARS[name], value); else reader.style.removeProperty(READER_VARS[name]);
        });
        reader.classList.toggle('scroll', layout() === 'scroll');
        reader.classList.toggle('comic', !!R.comic);
        // The Layout row shows the choice under Aa (Auto for a comic), not the bar's switch for this book.
        var picked = !R.comic ? look.layout : look.comicLayout === 'auto' ? 'layout-auto' : look.comicLayout;
        document.querySelectorAll('#look [data-look]').forEach(function (button) {
            var name = button.dataset.look;
            button.classList.toggle('on', name === look.font || name === look.theme || name === picked);
        });
        // A comic's own switch in the bar: what a tap turns it into.
        $('rComic').style.display = R.comic ? '' : 'none';
        $('rComic').textContent = layout() === 'scroll' ? '▤ Pages' : '↕ Scroll';
        store(LOOK_KEY, look);
    }
    // A comic between a page per image and one long scroll, staying on the image being read. The bar's
    // switch is for this book; Aa picks what every comic opens in.
    function toggleComicLayout() {
        if (!R.comic) return;
        var anchor = anchorAt();
        R.comicPick = layout() === 'scroll' ? 'paged' : 'scroll';
        applyLook();
        anchor.scroll = layout() === 'scroll';
        restoreAnchor(anchor);
    }
    document.querySelectorAll('#look [data-look]').forEach(function (button) {
        button.addEventListener('click', function () {
            var name = button.dataset.look, at = readingAt();
            if (name === 'smaller') { look.size = Math.max(12, look.size - 1); look.sizeSet = true; }
            else if (name === 'larger') { look.size = Math.min(34, look.size + 1); look.sizeSet = true; }
            else if (name === 'tighter') look.leading = Math.max(1.2, Math.round((look.leading - 0.1) * 10) / 10);
            else if (name === 'looser') look.leading = Math.min(2.6, Math.round((look.leading + 0.1) * 10) / 10);
            else if (name === 'serif' || name === 'sans') look.font = name;
            else if (name === 'auto' || name === 'light' || name === 'sepia' || name === 'dark') look.theme = name;
            else if (name === 'layout-auto' || R.comic) { look.comicLayout = name === 'layout-auto' ? 'auto' : name; R.comicPick = ''; }
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
    var HIDDEN = /display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?![.\d])|(^|;)\s*height\s*:\s*0(px)?\s*(;|$)/i;
    function clean(html, base) {
        var dom = new DOMParser().parseFromString('<div id="r">' + String(html || '') + '</div>', 'text/html');
        var root = dom.getElementById('r');
        (function walk(node) {
            Array.prototype.slice.call(node.childNodes).forEach(function (child) {
                if (child.nodeType === 8) { child.remove(); return; }
                if (child.nodeType !== 1) return;
                var tag = child.tagName.toLowerCase();
                if (DROP.test(tag)) { child.remove(); return; }
                // Hidden on the site (its style, which goes below): hidden here too, not shown as text.
                if (HIDDEN.test(child.getAttribute('style') || '')) { child.remove(); return; }
                walk(child);
                var whiteSpace = tag === 'div' && child.classList.contains('mpia-content') ? child.style.whiteSpace : '';
                var keepWhiteSpace = /^(pre|pre-wrap|pre-line|break-spaces)$/.test(whiteSpace);
                if (!KEEP.test(tag) && !keepWhiteSpace) {
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
                if (keepWhiteSpace) {
                    child.className = 'mpia-content';
                    child.style.whiteSpace = whiteSpace;
                }
                if (tag === 'img') {
                    try { src = new URL(src, base).href; } catch (e) { src = ''; }
                    if (!/^(https?:|data:image\/)/i.test(src) && !readerImageUrl(src)) { child.remove(); return; }
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

    var localRefresh = {busy: false, pending: null};
    function openReader(group, sourceIndex) {
        var reader = $('reader');
        if (R.group) send({type: 'genericReaderStop'});  // stop the previous reader request, including on source changes
        R.group = group;
        R.blocked = '';
        R.comic = !!group.comic;
        R.comicPick = '';
        R.comicKind = '';
        R.token++;
        localRefresh = {busy: false, pending: null};
        clearInterval(R.liveTimer);
        R.live = null;
        reader.classList.remove('library-live');
        R.savedChapters = null;
        R.navigation = 0;
        R.chapterVersion = 0;
        R.cache = new Map();
        R.chapterIds = new Map();
        R.pagesFor = new Map();  // chapter -> the pages streamed in so far (a chapter read ahead too)
        R.done = new Set();  // chapters read (their pages show at once, no placeholders)
        R.alive = {};  // request id -> when it last streamed a page or a note
        R.chapters = [];
        R.source = null;
        R.lnreader = null;
        R.tocLoading = true;
        R.tocError = false;
        R.bookRequest = null;
        drawToc(false);
        // Nothing of the book read before stays: its streamed chapter (which kept the loading note from
        // showing), its pictures still coming, its page and place.
        R.streamed = -1;
        R.index = -1;
        R.slots = [];
        R.slotsIndex = -1;
        R.pages = 1;
        R.page = 0;
        clearInterval(R.clock);
        resetPictures();
        $('flow').innerHTML = '';
        $('flow').style.transform = 'translateX(0)';
        ['width', 'columnWidth', 'columnGap', 'padding'].forEach(function (name) { $('flow').style[name] = ''; });  // the last book's pages
        $('viewport').scrollTop = 0;
        $('rChapterNo').textContent = '';
        $('rWhere').textContent = '';
        reader.classList.add('open');
        send({type: 'genericReaderOpened'});
        document.body.style.overflow = 'hidden';
        applyLook();
        $('rBook').textContent = group.title;
        $('rChapter').textContent = '';
        tryBook(group, sourceIndex || 0, R.token, []);
        if (group.sources[0] && group.sources[0].source === 'local') {
            R.liveTimer = setInterval(pollLibraryLive, 1000);
            pollLibraryLive();
        }
    }
    // A chapter's pages are streaming in (on screen): notes go to the bottom bar, not over them. No chapter
    // yet (a book just opened: both -1) is not streaming.
    function streaming() { return R.index >= 0 && R.streamed === R.index; }
    // What the reader is doing, with a clock; after `limit` seconds it gives up with a way out.
    function note(text, limit, retry) {
        clearInterval(R.clock);
        var started = readerNow(), token = R.token;
        R.noteText = text;
        R.noteDetail = '';
        var draw = R.drawNote = function () {
            if (token !== R.token || streaming()) { clearInterval(R.clock); return; }
            if (readerPausedAt !== null) return;
            var seconds = Math.round((readerNow() - started) / 1000);
            if (limit && seconds >= limit) {
                clearInterval(R.clock);
                failed(text.replace(/…$/, '') + ' took too long (' + limit + ' s).', retry);
                return;
            }
            $('flow').innerHTML = '<p class="note">' + esc(text) + (seconds >= 2 ? ' <span class="clock">' + seconds + 's</span>' : '')
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
        $('flow').innerHTML = '<p class="note">' + esc(message) + '</p><p class="next-chapter actions">'
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
                R.tocLoading = false;
                R.tocError = true;
                if ($('toc').classList.contains('open')) drawToc(false);
                failed(R.blocked || 'The book could not be read: ' + errors.join('; '), function () { openReader(group, start); }, !!R.blocked);
                return;
            }
            R.tried = group.sources.indexOf(order[i]);
            var source = order[i];
            note('Reading the chapter list from ' + source.site + '…', 120);
            var progress = bookProgress = {source: i + 1, sources: order.length, requests: 0, done: 0, page: 0};
            drawBookProgress();
            var saved = group.start || store('noveldb.read.' + placeKey(source.url)) || {};
            var wanted = saved.chapter || 0;
            function update(book, more) {
                if (token !== R.token) return;
                R.source = source;
                if (more) R.chapters = R.chapters.concat(book.chapters);
                else if (book.chapters) R.chapters = book.chapters;
                if (source.source === 'local' && book.chapters && !more) R.savedChapters = book.chapters.slice();
                if (book.lnreader) R.lnreader = book.lnreader;
                R.tocLoading = more;
                R.tocError = !!book.error;
                if (R.index < 0 && (!more || wanted < R.chapters.length)) {
                    if (bookProgress === progress) bookProgress = null;
                    group.start = null;
                    openChapter(Math.min(wanted, R.chapters.length - 1), saved.at || 0);
                } else if (R.index >= 0) {
                    chapterNumber();
                    if (R.done.has(R.index) && R.index + 1 < R.chapters.length) {
                        if (layout() === 'scroll' && !R.slots.length) nextChapterButton(R.index);
                        readAhead();
                    }
                }
                if ($('toc').classList.contains('open')) drawToc(false);
            }
            loadBook(source, token, function (book) { update(book, true); }).then(function (book) {
                if (bookProgress === progress) bookProgress = null;
                if (token !== R.token) return;
                if (book && !book.error && book.chapters && book.chapters.length) {
                    update(book, false);
                } else if (R.chapters.length) {
                    update({error: true}, false);
                } else {
                    if (book && book.blocked) R.blocked = book.message;
                    errors.push(source.site + ': ' + ((book && book.error) || 'no chapters'));
                    attempt(i + 1);
                }
            });
        };
        attempt(0);
    }
    // Time out from the last page or progress note so a request that is still making progress can finish.
    function askAlive(message, seconds, onId) {
        var id = 0, started = readerNow();
        return new Promise(function (resolve) {
            var timer = setInterval(function () {
                if (readerNow() - Math.max(started, R.alive[id] || 0) < seconds * 1000) return;
                clearInterval(timer);
                delete pending[id];
                resolve({error: 'the site did not answer in ' + seconds + ' s'});
            }, 2000);
            ask(message, function (given) { id = given; if (onId) onId(given); }).then(function (value) {
                clearInterval(timer);
                resolve(value);
            });
        });
    }
    // A chapter of images reads as a comic: one scroll, each image showing as it arrives (the paged
    // layout could not count its pages until the last image was in).
    // A chapter of pictures: four or more, and little text beside them. A novel's chapter with illustrations (a
    // royalroad chapter opens with a few) has thousands of letters: it was read as a comic, centred, its
    // paragraphs run together.
    function comicChapter(html) {
        var pictures = (String(html || '').match(/<img\b/gi) || []).length;
        if (pictures < 4) return false;
        var text = String(html).replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, '').replace(/<[^>]+>/g, '')
            .replace(/&[#\w]+;/g, ' ').replace(/\s+/g, '');
        return text.length < pictures * 150 + 400;
    }
    function asComic() {
        if (R.comic) return;
        R.comic = true;
        applyLook();
    }
    function loadBook(source, token, onChapters) {
        function appBook() {
            var request;
            return askAlive({type: 'genericBook', url: source.url}, 90, function (id) {
                request = id;
                R.bookRequest = {id: id, update: onChapters};
                R.requestId = id;
            }).then(function (book) {
                if (R.bookRequest && R.bookRequest.id === request) R.bookRequest = null;
                return book;
            });
        }
        function lnBook(book, start) {
            return {lnreader: book, chapters: book.chapters.slice(start).map(function (c, i) {
                return {name: c.name || 'Chapter ' + (start + i + 1), section: c.sectionName || ''};
            })};
        }
        if (lnreaderSite(source) && window.__LNR_parseBook) {
            var count = 0, active = true;
            var parsed = window.__LNR_parseBook(source.url, function (book) {
                if (!active || token !== R.token) return;
                onChapters(lnBook(book, count));
                count = book.chapters.length;
            }, function () { return active && token === R.token; });
            return within(parsed, 60000, '{"error":"the site took too long"}').then(function (raw) {
                active = false;
                var book = parse(raw) || {};
                if (book.error || !(book.chapters || []).length) return count || token !== R.token ? book : appBook();
                return lnBook(book, 0);
            });
        }
        return appBook();
    }
    function loadChapter(index) {
        if (R.cache.has(index)) return R.cache.get(index);
        var source = R.source, token = R.token, version = R.chapterVersion, item = R.chapters[index], chapterName = item.name, promise;
        // The app-side reader uses the Catalog's hidden browser and, if necessary, the downloader's
        // saved browser session.  It is also the recovery path when an LNReader request hits Cloudflare.
        var appChapter = function (recovery) {
            return askAlive({type: 'genericChapter', url: source.url, index: item.localIndex >= 0 ? item.localIndex : index, recovery: recovery || null}, 90,
                             function (id) { R.chapterIds.set(index, id); });
        };
        var protectedChapter = function (item) {
            if (typeof window.__noveldb_window_script !== 'function') return appChapter('cloudflare');
            var address;
            try { address = new URL(item.url || item.path || '', source.url).href; }
            catch (e) { return Promise.resolve({error: 'the chapter address is invalid'}); }
            var script = "(async function () {"
                + "var deadline=Date.now()+30000;"
                + "while(Date.now()<deadline){"
                + "var reader=document.querySelector('.reader');"
                + "if(reader&&(reader.innerText||'').trim().length>20){"
                + "var clone=reader.cloneNode(true);"
                + "clone.querySelectorAll('.reader-nav,script,style').forEach(function(n){n.remove();});"
                + "return {chapterName:" + JSON.stringify(item.name || chapterName)
                + ",contentHtml:clone.innerHTML,contentText:clone.innerText};}"
                + "await new Promise(function(resolve){setTimeout(resolve,250);});}"
                + "throw new Error('Cloudflare verification did not expose the chapter within 30 seconds.');"
                + "})()";
            return Promise.resolve(window.__noveldb_window_script({url: address, script: script})).then(function (reply) {
                if (typeof reply === 'string') reply = parse(reply);
                return reply && reply.value && reply.value.contentHtml ? reply.value
                    : {error: (reply && reply.error) || 'the hidden browser found no chapter text'};
            }, function (error) { return {error: String(error)}; });
        };
        if (R.live && item.liveKey && (item.ready || item.previous)) {
            var liveSource = item.ready ? {session: R.live.session, key: item.liveKey} : item.previous;
            promise = askAlive({type: 'genericLibraryLive', url: source.url, session: liveSource.session, key: liveSource.key}, 90)
                .then(function (result) { return result && !result.error ? result : item.localIndex >= 0 ? appChapter() : result; });
        } else if (R.lnreader && /^https?:\/\/(?:www\.)?fuck\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061\.com\//i.test(source.url || '')) {
            // The chapter needs the app's browser session even when the plugin can read the TOC.
            promise = protectedChapter(R.lnreader.chapters[index]);
        } else if (R.lnreader) {
            var item = Object.assign({}, R.lnreader.chapters[index]);
            promise = readerDeadline(window.__LNR_parseChapter(JSON.stringify(item)), 60000, '{"error":"the site took too long"}')
                .then(parse).then(function (result) {
                    if (token !== R.token) return null;
                    if (result && !result.error) return result;
                    var detail = ((result && result.error) || '') + ' ' + ((result && result.message) || '');
                    var cloudflare = /cloudflare|http\s*403|just a moment|verify you are human|checking your browser/i.test(detail);
                    if (index === R.index) note(cloudflare ? 'Cloudflare detected; switching to the downloader browser…'
                                                           : 'The quick reader failed; retrying in the browser…', 90);
                    return appChapter(cloudflare ? 'cloudflare' : null)
                        .then(function (recovered) { return recovered || result; });
                });
        } else {
            // Each chapter's request keeps its id: the next chapter read ahead must not take over this one's
            // streamed images and notes.
            promise = appChapter();
        }
        promise = promise.then(function (result) {
            if (token !== R.token || version !== R.chapterVersion) return {error: 'the chapter list changed'};
            if (!result || result.error) {
                R.cache.delete(index);
                R.done.delete(index);
                R.pagesFor.delete(index);
                return {error: (result && result.error) || 'the chapter could not be read', blocked: !!(result && result.blocked),
                        message: result && result.message};
            }
            var html = result.html || result.contentHtml ? clean(result.html || result.contentHtml, result.imageReferer || source.url) : '';
            if (!html.replace(/<[^>]+>/g, '').trim() && html.indexOf('<img') < 0) html = textHtml(result.text || result.contentText);
            R.done.add(index);
            R.pagesFor.delete(index);  // its pages are in its HTML now (no second copy kept)
            return {name: result.name || result.chapterName || chapterName, html: html,
                    comic: !!result.comic, imageReferer: result.imageReferer || source.url,
                    imageFallback: !!result.imageFallback};
        });
        R.cache.set(index, promise);
        return promise;
    }
    function chapterNumber() {
        $('rChapterNo').textContent = 'Chapter ' + (R.index + 1) + (R.tocLoading ? '' : ' of ' + R.chapters.length);
    }
    function matchingChapter(chapters, current, preferred) {
        var same = function (chapter) { return chapter && chapter.name === current.name && (chapter.section || '') === (current.section || ''); };
        return same(chapters[preferred]) ? preferred : chapters.findIndex(same);
    }
    function chapterAvailable(chapter) {
        return !!chapter && (!chapter.liveKey || chapter.ready || chapter.localIndex >= 0 || !!chapter.previous);
    }
    function liveTitle(name) {
        return String(name || '').normalize('NFKC').replace(/^(?:chapter\s*\d+\s*[:.\-]?\s*|\d+\s*[.)、]\s*)/i, '')
            .replace(/\s+/g, ' ').trim();
    }
    function liveLocalIndices(saved, fresh) {
        var names = new Map();
        saved.forEach(function (chapter, index) {
            var name = liveTitle(chapter.name);
            if (!names.has(name)) names.set(name, []);
            names.get(name).push(index);
        });
        return fresh.map(function (chapter) {
            var matches = names.get(liveTitle(chapter.name));
            return matches && matches.length ? matches.shift() : -1;
        });
    }
    function applyLibraryLive(data) {
        if (!data || !data.session || !R.savedChapters || !R.done.has(R.index)) return false;
        var changed = false, before = R.index, current = R.chapters[before], kept = R.cache.get(before);
        if (Array.isArray(data.chapters) && data.chapters.length) {
            var previous = R.live, old = new Map();
            R.chapters.forEach(function (chapter) { if (chapter.identity) old.set(chapter.identity, chapter); });
            var indices = liveLocalIndices(R.savedChapters, data.chapters);
            var matched = indices.filter(function (index) { return index >= 0; });
            var first = matched.length ? Math.min.apply(null, matched) : 0;
            var chapters = R.savedChapters.slice(0, first).map(function (chapter, index) {
                return Object.assign({}, chapter, {localIndex: index});
            });
            data.chapters.forEach(function (chapter, index) {
                var earlier = old.get(chapter.identity), fallback = null;
                if (earlier && previous) fallback = earlier.ready ? {session: previous.session, key: earlier.liveKey} : earlier.previous;
                chapters.push({name: chapter.name, identity: chapter.identity, liveKey: String(chapter.key),
                    localIndex: indices[index], ready: false, previous: fallback});
            });
            var currentLocal = current.localIndex >= 0 ? current.localIndex : !current.liveKey ? before : -1;
            var next = chapters.findIndex(function (chapter) {
                return current.identity ? chapter.identity === current.identity
                    : currentLocal >= 0 && chapter.localIndex === currentLocal;
            });
            if (next < 0) next = matchingChapter(chapters, current, before);
            // Keep an older chapter readable if the source removed it during this update.
            if (next < 0) { chapters.unshift(Object.assign({}, current, {localIndex: currentLocal})); next = 0; }
            R.chapterVersion++;
            R.chapters = chapters;
            R.index = next;
            R.cache = new Map();
            if (kept) R.cache.set(next, kept);
            R.done = new Set([next]);
            R.chapterIds.clear(); R.pagesFor.clear();
            if (R.slotsIndex === before) R.slotsIndex = next;
            R.live = {session: data.session, version: 0};
            $('reader').classList.add('library-live');
            changed = true;
        }
        if (!R.live || R.live.session !== data.session) return false;
        var byKey = new Map();
        R.chapters.forEach(function (chapter, index) { if (chapter.liveKey) byKey.set(chapter.liveKey, index); });
        (data.states || []).forEach(function (state) {
            var index = byKey.get(String(state.key));
            if (index === undefined) return;
            var chapter = R.chapters[index], ready = state.state === 'ready';
            if (chapter.ready !== ready) {
                chapter.ready = ready;
                R.cache.delete(index);
                if (index !== R.index) R.done.delete(index);
                changed = true;
            }
        });
        R.live.version = data.version;
        if (changed) {
            chapterNumber();
            if ($('toc').classList.contains('open')) drawToc(false);
            if ($('rNextChapter')) $('rNextChapter').disabled = !chapterAvailable(R.chapters[R.index + 1]);
            place();
            readAhead();
        }
        if (data.saved && typeof data.savedOffset === 'number') {
            R.chapters.forEach(function (chapter) {
                if (chapter.liveKey) chapter.localIndex = Number(chapter.liveKey) + data.savedOffset;
            });
        }
        return true;
    }
    function pollLibraryLive() {
        var monitor = localRefresh, source = R.group && R.group.sources[0];
        if (!source || source.source !== 'local' || monitor.busy || !$('reader').classList.contains('open') || readerPausedAt !== null) return;
        monitor.busy = true;
        var token = R.token;
        askAlive({type: 'genericLibraryLive', url: source.url, session: R.live ? R.live.session : '',
                  after: R.live ? R.live.version : 0}, 5).then(function (data) {
            if (token !== R.token || monitor !== localRefresh) return;
            if (data && data.session && !applyLibraryLive(data)) monitor.pending = data;
        }).catch(function () {}).then(function () { if (monitor === localRefresh) monitor.busy = false; });
    }
    function refreshLocal(url) {
        var source = R.group && R.group.sources[0];
        if (!$('reader').classList.contains('open') || !source || source.source !== 'local' || key(source.url) !== key(url)) return false;
        pollLibraryLive();
        return true;
    }
    function savedChapterIndex() {
        var chapter = R.chapters[R.index];
        return R.live && chapter && chapter.localIndex >= 0 ? chapter.localIndex : R.index;
    }
    function nextChapterButton(index) {
        if (index + 1 >= R.chapters.length) {
            if ($('rNextChapter')) $('rNextChapter').parentNode.remove();
            return;
        }
        if (!$('rNextChapter')) $('flow').insertAdjacentHTML('beforeend', '<p class="next-chapter"><button type="button" id="rNextChapter">Next chapter ›</button></p>');
        $('rNextChapter').disabled = !chapterAvailable(R.chapters[index + 1]);
        $('rNextChapter').onclick = function () { openChapter(index + 1, 0); };
    }
    function openChapter(index, at) {
        var intent = R.navigation = (R.navigation || 0) + 1;
        if (R.live && !chapterAvailable(R.chapters[index])) {
            R.page = Math.max(0, Math.min(R.page, R.pages - 1));
            place();
            return;
        }
        var local = R.source && R.source.source === 'local';
        if ((R.live || (local && R.done.has(R.index))) && (!R.cache.has(index) || !R.done.has(index))) {
            var token = R.token, wanted = R.chapters[index];
            loadChapter(index).then(function (chapter) {
                if (token !== R.token || intent !== R.navigation) return;
                var next = R.chapters.indexOf(wanted);
                if (next < 0) next = matchingChapter(R.chapters, wanted, index);
                if (chapter.error && R.chapters[next] === wanted) return;
                if (next >= 0) openChapter(next, at);
            });
            return;
        }
        // The chapter already streaming in (its pages arriving): left as it is, not started again.
        if (index === R.index && R.slotsIndex === index && R.slots && R.slots.length) return;
        var token = R.token;
        R.streamed = -1;
        R.slotsIndex = -1;
        resetPictures();
        R.index = index;
        R.imageReferer = R.source.url;
        R.imageFallback = false;
        $('rChapter').textContent = '· ' + (R.chapters[index].name || '');
        chapterNumber();
        markToc();
        var retry = function () { R.cache.delete(index); (R.pagesFor || new Map()).delete(index); openChapter(index, at); };
        if (!local) note('Scraping chapter ' + (index + 1) + (R.chapters[index].name ? ' (' + R.chapters[index].name + ')' : '') + ' from '
                         + R.source.site + '…', 90, retry);
        // A comic's chapter shows its page slots at once: the pages already streamed (a chapter read ahead),
        // else as many placeholders as the last chapter had pages, until its own count is known.
        var early = R.pagesFor && R.pagesFor.get(index);
        early = (early || []).filter(Boolean);
        if (early.length) streamImages(index, {images: early, total: early.length});
        else if ((R.comic || (R.group && R.group.comic)) && !R.done.has(index)) streamImages(index, {images: [], total: R.lastPages || 6, placeholders: true});
        loadChapter(index).then(function (chapter) {
            if (token !== R.token || index !== R.index) return;
            clearInterval(R.clock);
            if (chapter.error) {
                failed(chapter.blocked ? chapter.message : 'Chapter ' + (index + 1) + ' could not be read: ' + chapter.error, retry, chapter.blocked);
                return;
            }
            // Its pages streamed in: finish them where they are (the slots know their chapter, even
            // when something reset R.streamed meanwhile), never draw the chapter again from 0.
            var streamedHere = R.streamed === index || (R.slotsIndex === index && R.slots && R.slots.length);
            // Its pictures streamed in, but the chapter is text with illustrations (a novel's): drawn as text below.
            R.imageReferer = chapter.imageReferer;
            R.imageFallback = chapter.imageFallback;
            if (streamedHere && !chapter.comic && !comicChapter(chapter.html) && !(R.group && R.group.comic)) {
                R.streamed = -1; R.slotsIndex = -1; R.slots = [];
                resetPictures();
            } else if (streamedHere) {
                finishStream(chapter);
                R.streamed = -1;
                return;
            }
            var last = index + 1 < R.chapters.length;
            var many = chapter.comic || comicChapter(chapter.html);
            var displayHtml = chapter.html;
            // A novel's chapter of text after one taken for a comic (its first held a cover and illustrations): read
            // as text again. A book its site calls a comic stays one.
            if (!many && R.comic && !(R.group && R.group.comic)) { R.comic = false; R.comicKind = ''; applyLook(); }
            if (many) {
                asComic();
                R.streamed = -1;
                resetPictures();
                var sources = chapterImages(chapter.html);
                displayHtml = chapter.html.replace(/<img\b[^>]*>/g, function () { return slotHtml(sources.shift()); });
            }
            $('flow').innerHTML = '<h2 class="chapter">' + esc(chapter.name) + '</h2>' + displayHtml;
            if (layout() === 'scroll') nextChapterButton(index);
            if (R.translate) translateChapter(false);
            if (many) {
                R.slots = Array.from($('flow').querySelectorAll('img.slot'));
                R.slotsIndex = index;
                R.slots.forEach(fetchPicture);
                pictures.total = R.slots.length;
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
                    ask({type: 'genericImage', url: image.src, referer: chapter.imageReferer, reader: true}).then(function (data) {
                        var src = data && (data.imageUrl || data.dataUrl);
                        if (src) image.src = src; else image.remove();
                    });
                }, {once: true});
            });
            $('viewport').scrollTop = 0;
            paginate(at === 'end' ? 1 : at || 0, at === 'end');
            if (last) readAhead();
        });
    }
    // -- chapter images: fetched by the app (no hotlink refusals), 4 at a time, counted ------------------
    var pictures = {queue: [], active: 0, done: 0, total: 0, bytes: 0, started: 0, urls: [], token: 0};
    function resetPictures() {
        pictures.urls.forEach(function (url) { URL.revokeObjectURL(url); });
        pictures = {queue: [], active: 0, done: 0, total: 0, bytes: 0, started: readerNow(), urls: [], token: pictures.token + 1};
        showPictures();
    }
    function showPictures() {
        var bar = $('rProgress');
        if (!pictures.total || pictures.done >= pictures.total) { bar.classList.remove('on'); return; }
        bar.classList.add('on');
        var seconds = Math.max(0.5, (readerNow() - pictures.started) / 1000);
        bar.firstElementChild.style.width = (100 * pictures.done / pictures.total) + '%';
        bar.lastElementChild.textContent = 'Images ' + pictures.done + ' of ' + pictures.total + ' · '
            + (pictures.bytes / 1048576).toFixed(1) + ' MB · ' + (pictures.bytes / 1048576 / seconds).toFixed(2) + ' MB/s';
    }
    function fetchPicture(image) {
        image.__readerSrc = image.__readerSrc || image.dataset.src;
        pictures.queue.push(image);
        pumpPictures();
    }
    function pumpPictures() {
        if (readerPausedAt !== null) return;
        while (pictures.active < 4 && pictures.queue.length) {
            var image = pictures.queue.shift(), token = pictures.token;
            pictures.active++;
            // A page the app already has (HaruNeko fetched it): its bytes, not the site asked again.
            var asked = readerImageUrl(image.dataset.src) ? Promise.resolve({imageUrl: image.dataset.src})
                : /^data:image\//.test(image.dataset.src)
                ? Promise.resolve({dataUrl: image.dataset.src, bytes: Math.round(image.dataset.src.length * 0.75)})
                : askAlive({type: 'genericImage', url: image.dataset.src, referer: R.imageReferer || '', reader: true}, 35);
            asked.then(function (data) {
                if (token !== pictures.token) return;
                pictures.active--;
                var image = this;
                if (data && readerImageUrl(data.imageUrl || '') && image.isConnected) {
                    pictures.bytes += data.bytes || 0;
                    showSlot(image, data.imageUrl, function () { retryPicture(image, token); });
                } else if (data && data.dataUrl && image.isConnected) {
                    pictures.bytes += data.bytes || Math.round(data.dataUrl.length * 0.75);
                    fetch(data.dataUrl).then(function (r) { return r.blob(); }).then(function (blob) {
                        if (token !== pictures.token || !image.isConnected) return;
                        var url = URL.createObjectURL(blob);
                        pictures.urls.push(url);
                        // The page's bytes are in the blob now; a copy as text in the slot would double them.
                        if (/^data:/.test(image.dataset.src)) image.dataset.src = 'data:,kept';
                        showSlot(image, url, function () { retryPicture(image, token); });
                    }, function () { retryPicture(image, token); });
                } else {
                    retryPicture(image, token);
                }
                showPictures();
                pumpPictures();
            }.bind(image));
        }
        showPictures();
    }
    // A page that did not come (the app's request timed out behind others, the site refused it a moment): asked
    // again after 1.5, 3, 6, 12 and 20 s, Halgakos holding its place meanwhile; then the browser itself tries. One
    // still missing is asked again when tapped. (Pages that came late showed as missing until the page was
    // opened again.)
    var RETRY_MS = [1500, 3000, 6000, 12000, 20000];
    function retryPicture(image, token) {
        if (token !== pictures.token || !image.isConnected) return;
        var tries = image.__tries = (image.__tries || 0) + 1;
        if (tries <= (R.imageFallback ? 2 : RETRY_MS.length) && !/^data:,kept$/.test(image.dataset.src)) {
            setTimeout(function () { if (token === pictures.token && image.isConnected) fetchPicture(image); }, RETRY_MS[tries - 1]);
            return;
        }
        if (R.imageFallback) recoverImages();
        showSlot(image, image.dataset.src, function () {
            pictureCounted(image);
            image.classList.add('failed');
            image.title = 'This page did not load: tap to try again';
            image.onclick = function (event) {
                event.stopPropagation();  // not a tap on the page's turn zones
                image.onclick = null;
                image.__tries = 0;
                image.classList.remove('failed');
                image.title = '';
                pictureCountedUndo(image);
                fetchPicture(image);
            };
        });
    }
    function recoverImages() {
        var index = R.index, token = R.token, source = R.source;
        R.imageFallback = false;
        $('rWhere').textContent = 'Trying the site engine for the missing images…';
        askAlive({type: 'genericChapter', url: source.url, index: index, recovery: 'images'}, 90).then(function (result) {
            if (token !== R.token || index !== R.index) return;
            if (!result || result.error || !result.html) { place(); return; }
            var chapter = {name: result.name, html: clean(result.html, source.url), comic: true,
                           imageReferer: result.imageReferer || source.url};
            if (!chapterImages(chapter.html).length) { place(); return; }
            R.cache.set(index, Promise.resolve(chapter));
            R.imageReferer = chapter.imageReferer;
            finishStream(chapter);
        });
    }
    function pictureCounted(image) {
        if (image.__counted) return;
        image.__counted = true;
        pictures.done++;
        showPictures();
    }
    function pictureCountedUndo(image) {
        if (!image.__counted) return;
        image.__counted = false;
        pictures.done = Math.max(0, pictures.done - 1);
        showPictures();
    }
    // Halgakos stays until the picture is decoded and laid out (a slot never shows empty or collapses, nor a
    // broken picture's outline: it is loaded aside first); `failed`: it would not load.
    function showSlot(image, src, failed) {
        var probe = new Image();
        probe.onload = function () {
            if (!image.isConnected) return;
            image.src = src;
            image.classList.add('ready');
            image.classList.remove('failed');
            pictureCounted(image);
            countPagesSoon();
            readAhead();
        };
        probe.onerror = function () { if (failed) failed(); else image.title = 'This image could not be loaded'; };
        probe.src = src;
    }
    // A chapter of images as slots: each shows Halgakos until its image arrives.
    function slotHtml(src) { return '<img class="slot" alt="" data-src="' + esc(src || '') + '">'; }
    function streamImages(index, found) {
        if (index !== R.index) return;
        if (R.slotsIndex !== index) {
            asComic();
            R.streamed = index;
            R.slotsIndex = index;
            clearInterval(R.clock);
            resetPictures();
            $('flow').innerHTML = '<h2 class="chapter">' + esc(R.chapters[index].name || '') + '</h2>';
            R.slots = [];
            // Laid out for the comic now: the columns of the text pages shown before (a novel's, the loading
            // note's) stayed on the flow, and a webtoon's strips showed tiny until the window was zoomed.
            paginate(0);
        }
        var flow = $('flow');
        // While the chapter is still being scraped, Halgakos holds the places of pages still to come (the phone
        // apps count only the pages found so far: a manhwa in scroll ended at its last page found, bare); the
        // finished chapter drops the ones left over.
        var total = found.done ? found.total
            : Math.max(found.total, R.lastPages || 0, (found.from || 0) + found.images.length + 2);
        while (R.slots.length < total) {
            flow.insertAdjacentHTML('beforeend', slotHtml(''));
            R.slots.push(flow.lastElementChild);
        }
        // `from`: the place of the first of `images` (the desktop sends each page once, as it comes).
        var from = found.from || 0;
        if (!found.placeholders) pictures.total = Math.max(found.total, from + found.images.length);
        found.images.forEach(function (src, n) {
            if (!src) return;
            var i = from + n;
            while (R.slots.length < i) { flow.insertAdjacentHTML('beforeend', slotHtml('')); R.slots.push(flow.lastElementChild); }
            var slot = R.slots[i];
            if (!slot) { flow.insertAdjacentHTML('beforeend', slotHtml('')); slot = flow.lastElementChild; R.slots.push(slot); }
            if (found.done && slot.__readerSrc && slot.__readerSrc !== src) {
                pictureCountedUndo(slot);
                var replacement = slot.cloneNode(false);
                replacement.removeAttribute('src');
                replacement.className = 'slot';
                replacement.dataset.src = '';
                slot.replaceWith(replacement);
                slot = R.slots[i] = replacement;
            }
            if (!slot.dataset.src) {
                slot.__readerSrc = slot.dataset.src = src;
                fetchPicture(slot);
            }
        });
        showPictures();
        countPagesSoon();  // the slots added are pages
    }
    function chapterImages(html) {
        var fragment = document.createElement('template');
        fragment.innerHTML = html;
        return Array.from(fragment.content.querySelectorAll('img[src]'), function (image) { return image.getAttribute('src'); });
    }
    function finishStream(chapter) {
        // The whole chapter is in: slots that never got an image go, the rest keep loading.
        var images = chapterImages(chapter.html);
        streamImages(R.index, {images: images, total: images.length, done: true});
        R.slots.slice(images.length).forEach(function (slot) { slot.remove(); });
        R.slots.length = Math.min(R.slots.length, images.length);
        if (images.length) R.lastPages = images.length;
        pictures.total = images.length;
        showPictures();
        // The reader stays where it is: a scrolled chapter keeps its scroll, pages are counted again.
        if (layout() === 'scroll') place(); else paginate(readingAt());
        readAhead();
    }

    function readAhead() {
        if (readerPausedAt !== null || !$('reader').classList.contains('open')) return;
        if (!R.done.has(R.index) || !chapterAvailable(R.chapters[R.index + 1])) return;
        if (R.comic && !$('flow').querySelector('.slot.ready')) return;
        if (R.imageFallback && pictures.done < pictures.total) return;
        loadChapter(R.index + 1);
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
        if (R.comic) {
            gap = 0;  // a comic: one image a page, the whole width (no neighbour peeking in at the sides)
            column = width;
        } else if (cols === 2) {
            gap = Math.max(48, Math.round(width * 0.05));
            column = width / 2 - gap;
        } else {
            gap = 2 * Math.max(24, Math.round((width - 760) / 2));
            column = width - gap;
        }
        flow.style.width = width + 'px';
        flow.style.padding = '28px ' + (gap / 2) + 'px';
        // A comic page's image fills the page's height (the viewport less the padding).
        flow.style.setProperty('--page-h', Math.max(200, viewport.clientHeight - 60) + 'px');
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
    var localPlaceTimer = 0;
    function localPlaceSoon() {
        clearTimeout(localPlaceTimer);
        localPlaceTimer = setTimeout(function () {
            if (R.source && R.source.source === 'local' && R.index >= 0)
                send({type: 'genericLocalPlace', url: R.source.url, chapter: savedChapterIndex(), at: readingAt()});
        }, 600);
    }
    function place() {
        if (layout() !== 'scroll') $('flow').style.transform = 'translateX(' + (-R.page * (R.pageWidth || 0)) + 'px)';
        $('rWhere').textContent = layout() === 'scroll' ? '' : 'Page ' + (R.page + 1) + ' of ' + R.pages;
        $('rSlider').max = String(R.pages);
        $('rSlider').value = String(R.page + 1);
        $('rSlider').style.display = layout() === 'scroll' ? 'none' : '';
        if (R.source) store('noveldb.read.' + placeKey(R.source.url), {chapter: savedChapterIndex(), at: readingAt(), time: Date.now()});
        // A Library book's place goes to the Library too (its book page and order know where it was left).
        if (R.source && R.source.source === 'local' && R.index >= 0) localPlaceSoon();
        if ($('toc').classList.contains('open') && tocTab === 'pages') drawPages();
        // The text being read, for laying the page out again when the window changes size.
        if (layout() !== 'scroll' && !R.switching) R.anchor = anchorAt();
        if (localRefresh.pending && R.done.has(R.index)) {
            var pendingLive = localRefresh.pending;
            localRefresh.pending = null;
            applyLibraryLive(pendingLive);
        }
    }
    // The pages counted again where they are now: a chapter's images still coming in add pages after it was laid
    // out (it was counted one page then, and every swipe went on to the next chapter until the window was
    // resized); a reader laid out before it had its width is laid out again.
    function countPages() {
        if (layout() === 'scroll' || !R.chapters.length) return;
        var width = $('viewport').clientWidth;
        if (!width) return;
        if (Math.abs(width - (R.pageWidth || 0)) > 1) { paginate(readingAt()); return; }
        R.pages = Math.max(1, Math.ceil(($('flow').scrollWidth - 4) / width));
        if (R.page > R.pages - 1) { R.page = R.pages - 1; place(); }
    }
    var countTimer = 0;
    function countPagesSoon() {
        clearTimeout(countTimer);
        countTimer = setTimeout(function () {
            countPages();
            if (layout() === 'scroll') return;
            $('rWhere').textContent = 'Page ' + (R.page + 1) + ' of ' + R.pages;
            $('rSlider').max = String(R.pages);
        }, 150);
    }
    function turn(step) {
        if (!R.chapters.length) return;
        countPages();
        if (layout() === 'scroll') {
            var v = $('viewport');
            var end = v.scrollTop + v.clientHeight >= $('flow').scrollHeight - 4;
            if (step > 0 && end && R.index + 1 < R.chapters.length) openChapter(R.index + 1, 0);
            else if (step < 0 && v.scrollTop <= 0 && R.index > 0) openChapter(R.index - 1, 'end');
            else v.scrollBy({top: step * (v.clientHeight - 60), behavior: 'smooth'});
            return;
        }
        var page = R.page + step;
        if (page >= R.pages) { if (R.index + 1 < R.chapters.length) openChapter(R.index + 1, 0); else place(); return; }
        if (page < 0) { if (R.index > 0) openChapter(R.index - 1, 'end'); else place(); return; }
        R.page = page;
        place();
    }

    // The contents: chapters (with the volume they're in) and the current chapter's pages.
    var tocTab = 'chapters';
    function drawToc(scrollToChapter) {
        if (tocTab === 'pages') { drawPages(); return; }
        var filter = $('tocFilter').value.trim().toLowerCase(), section = null, html = '';
        R.chapters.forEach(function (chapter, index) {
            if (filter && String(chapter.name).toLowerCase().indexOf(filter) < 0 && String(index + 1) !== filter) return;
            if (chapter.section && chapter.section !== section && !filter) html += '<div class="section">' + esc(chapter.section) + '</div>';
            section = chapter.section;
            var available = chapterAvailable(chapter);
            html += '<a data-chapter="' + index + '" class="' + (index === R.index ? 'on ' : '') + (available ? '' : 'pending')
                + '" aria-disabled="' + !available + '"' + (available ? '' : ' title="Not downloaded yet"')
                + '>' + (index + 1) + '. ' + esc(chapter.name) + '</a>';
        });
        $('tocList').className = 'list';
        $('tocList').innerHTML = html + (R.tocLoading ? '<div class="section">Loading more chapters…</div>'
            : R.tocError ? '<div class="section">The rest of the chapter list could not be loaded.</div>'
            : html ? '' : '<div class="section">No chapter matches.</div>');
        $('tocList').querySelectorAll('[data-chapter]').forEach(function (link) {
            link.onclick = function () {
                var index = Number(link.dataset.chapter);
                if (!chapterAvailable(R.chapters[index])) return;
                $('toc').classList.remove('open'); openChapter(index, 0);
            };
        });
        markToc(scrollToChapter);
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
    function markToc(scrollToChapter) {
        var on = $('tocList').querySelector('[data-chapter="' + R.index + '"]');
        $('tocList').querySelectorAll('a.on[data-chapter]').forEach(function (a) { a.classList.remove('on'); });
        if (on) { on.classList.add('on'); if (scrollToChapter !== false && $('toc').classList.contains('open')) on.scrollIntoView({block: 'center'}); }
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
    $('reader').addEventListener('click', function (event) {
        if (!event.target.closest('#toc, #rToc') && $('toc').classList.contains('open')) {
            $('toc').classList.remove('open');
            // The dismissal tap belongs to the panel, including over a page-turn zone.
            event.preventDefault();
            event.stopPropagation();
        }
    }, true);
    $('rClose').onclick = closeReader;
    $('rPrev').onclick = function () { turn(-1); };
    $('rNext').onclick = function () { turn(1); };
    $('zoneLeft').onclick = function () { if (Date.now() - (R.swiped || 0) > 400) turn(-1); };
    $('zoneRight').onclick = function () { if (Date.now() - (R.swiped || 0) > 400) turn(1); };
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
    // A finger swipe turns the page (left: next, right: previous); in the scrolling layout a sideways
    // swipe changes the chapter. Short or mostly vertical moves are taps and scrolls, left alone.
    // In pages the page follows the finger while it drags; let go past a fifth of the page (or with a
    // flick) and it slides on, else back. Past a chapter's first or last page the drag gets heavier.
    var swipe = null;
    var SLIDE = 'transform .3s cubic-bezier(.2, .7, .3, 1)';
    $('reader').addEventListener('touchstart', function (event) {
        var touch = event.touches.length === 1 ? event.touches[0] : null;
        swipe = touch && !event.target.closest('.bar, #toc, #look') ? {x: touch.clientX, y: touch.clientY, at: Date.now(), dragging: false} : null;
    }, {passive: true});
    $('reader').addEventListener('touchmove', function (event) {
        if (!swipe || layout() === 'scroll' || event.touches.length !== 1) return;
        var touch = event.touches[0], dx = touch.clientX - swipe.x, dy = touch.clientY - swipe.y;
        if (!swipe.dragging) {
            if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
            countPages();
            if (!R.pageWidth) return;
            swipe.dragging = true;
            $('flow').style.transition = 'none';
        }
        var edge = (R.page === 0 && dx > 0) || (R.page >= R.pages - 1 && dx < 0);
        $('flow').style.transform = 'translateX(' + (-R.page * R.pageWidth + (edge ? dx * 0.35 : dx)) + 'px)';
    }, {passive: true});
    $('reader').addEventListener('touchend', function (event) {
        if (!swipe || !event.changedTouches.length) return;
        var touch = event.changedTouches[0], dx = touch.clientX - swipe.x, dy = touch.clientY - swipe.y, quick = Date.now() - swipe.at < 800;
        if (swipe.dragging) {
            var flick = Math.abs(dx) > 40 && Date.now() - swipe.at < 300;
            swipe = null;
            var flow = $('flow');
            flow.style.transition = SLIDE;
            if (Math.abs(dx) > R.pageWidth * 0.2 || flick) turn(dx < 0 ? 1 : -1);
            else place();  // not far enough: back to the page it was on
            setTimeout(function () { flow.style.transition = ''; }, 320);
            R.swiped = Date.now();  // the tap zones ignore the click this swipe ends with
            return;
        }
        swipe = null;
        if (!quick || Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        if (layout() === 'scroll') {
            var next = R.index + (dx < 0 ? 1 : -1);
            if (next >= 0 && next < R.chapters.length) openChapter(next, 0);
        } else {
            turn(dx < 0 ? 1 : -1);
        }
        R.swiped = Date.now();  // the tap zones ignore the click this swipe ends with
    }, {passive: true});
    function closeReader() {
        clearInterval(R.liveTimer);
        // A Library book's place is kept before the reader goes (the Library shows it next).
        if (R.source && R.source.source === 'local' && R.index >= 0) {
            clearTimeout(localPlaceTimer);
            send({type: 'genericLocalPlace', url: R.source.url, chapter: savedChapterIndex(), at: readingAt()});
        }
        if (R.full || R.switching) setFull(false, true);
        R.token++;
        resetPictures();
        clearInterval(R.clock);
        R.cache.clear();
        R.pagesFor.clear();
        $('flow').replaceChildren();
        R.bookRequest = null;
        bookProgress = null;
        send({type: 'genericReaderStop'});  // the app stops scraping this book (the next one need not wait)
        $('reader').classList.remove('open');
        $('toc').classList.remove('open');
        $('look').classList.remove('open');
        document.body.style.overflow = '';
        // A Library book's reader: the phone apps close the page they opened it in, the desktop goes back to
        // the Library.
        if (R.group && R.group.sources[0] && R.group.sources[0].source === 'local') send({type: 'genericReaderClosed'});
    }
    document.addEventListener('keydown', function (event) {
        // Typing in a box keeps its keys; the page slider (a range input) does not take the arrows.
        if (!$('reader').classList.contains('open') || event.target.matches('input:not([type=range]), textarea, select')) return;
        var paged = layout() !== 'scroll';
        if (event.key === 'ArrowRight' || event.key === 'PageDown' || (paged && event.key === 'ArrowDown') || (event.key === ' ' && !event.shiftKey)) { event.preventDefault(); turn(1); }
        else if (event.key === 'ArrowLeft' || event.key === 'PageUp' || (paged && event.key === 'ArrowUp') || (event.key === ' ' && event.shiftKey)) { event.preventDefault(); turn(-1); }
        else if (event.key === 'Escape') { if (R.full) setFull(false); else closeReader(); }
        else if (event.key === 'f' || event.key === 'F') setFull(!R.full);
        else if (event.key === 't' || event.key === 'T') $('rTranslate').click();
        else if (event.key === 'p' || event.key === 'P') toggleComicLayout();
        else if (event.key === 'Home') { R.page = 0; place(); }
        else if (event.key === 'End') { R.page = R.pages - 1; place(); }
    });
    $('rFull').onclick = function () { setFull(!R.full); };
    // Google Translate for the chapter, in the device's language; on, it stays on for the next chapters.
    function translateChapter(back) {
        var at = readingAt(), button = $('rTranslate');
        button.disabled = true;
        return translateNodes($('flow'), back).then(function () {
            button.disabled = false;
            button.classList.toggle('on', !!R.translate);
            button.title = R.translate ? 'Show the original (T)' : 'Translate with Google (T)';
            paginate(at);
        });
    }
    $('rTranslate').onclick = function () { R.translate = !R.translate; translateChapter(!R.translate); };
    $('rComic').onclick = toggleComicLayout;
    // In full screen a tap in the page's middle (not a page-turning edge) shows or hides the bars.
    $('viewport').addEventListener('click', function (event) {
        if (!R.full || event.target.closest('button, a, input')) return;
        $('reader').classList.toggle('bars');
    });
    // The browser's own way out of full screen (Esc, a back gesture) leaves the reader's too.
    document.addEventListener('fullscreenchange', function () {
        if (!document.fullscreenElement && R.full) setFull(false);
    });
    window.addEventListener('resize', function () {
        if (!$('reader').classList.contains('open')) return;
        clearTimeout(R.resizeTimer);
        var at = readingAt();
        if (R.switching) return;  // the change to or from full screen lays the reader out itself
        // Back on the text that was showing (a fraction of the chapter lands a page or two away).
        var anchor = layout() === 'scroll' ? anchorAt() : R.anchor && R.anchor.el ? R.anchor : {at: at, scroll: false};
        R.resizeTimer = setTimeout(function () { restoreAnchor(anchor); }, 120);
    });

    // -- more as the list is scrolled -------------------------------------------------------------------
    // A search asks for its next results (NovelDB's next page, each site's next results page); a top list
    // shows TOP_STEP more of the books its sites listed, and asks them for their next pages when it runs short.
    var more = {page: 1, loading: false, has: true};
    // The next books are asked for well before the end (two and a half screens, 1,800 px at least), so they
    // are usually in by the time the list gets there; the loader under the cards shows while they come.
    function nearEnd() {
        var page = document.scrollingElement || document.documentElement;
        return page.scrollTop + window.innerHeight >= page.scrollHeight - Math.max(1800, window.innerHeight * 2.5);
    }
    function setMoreLoading(on) {
        more.loading = on;
        showMoreLoader();
    }
    /** Full rows while more books can come: the last cards of a row left part empty wait (hidden) for the
     *  next ones; once nothing more is coming, every card shows. */
    // Read without layout: a card's own display style says whether it shows (reading each card's layout, as this
    // first did, made the browser lay the page out once per card, each time cards came in: the page lagged).
    var waitingCards = [], gridColumns = 0;
    function evenRows() {
        var grid = $('grid');
        waitingCards.forEach(function (el) { el.classList.remove('rowwait'); });
        waitingCards = [];
        var coming = (view === 'search' || view === 'top') && (more.loading || running > 0 || more.has);
        if (!coming) return;
        if (!gridColumns) gridColumns = String(getComputedStyle(grid).gridTemplateColumns || '').split(' ').filter(Boolean).length;
        var cards = Array.prototype.filter.call(grid.children, function (el) { return el.style.display !== 'none'; });
        var left = gridColumns > 1 ? cards.length % gridColumns : 0;
        if (!left || cards.length <= left) return;
        // The cards shown last: the sort's order where one is set, else the list's own.
        waitingCards = cards.map(function (el, i) { return [el, Number(el.style.order || 0), i]; })
            .sort(function (a, b) { return a[1] - b[1] || a[2] - b[2]; })
            .slice(-left).map(function (entry) { entry[0].classList.add('rowwait'); return entry[0]; });
    }
    var evenTimer = 0;
    function evenRowsSoon() { if (!evenTimer) evenTimer = setTimeout(function () { evenTimer = 0; evenRows(); }, 250); }
    window.addEventListener('resize', function () { gridColumns = 0; evenRowsSoon(); });
    /** The loader under the cards: while their next books load, or a search is still finding books. */
    function showMoreLoader() {
        var loader = $('moreLoading');
        if (!loader) return;
        var busy = (view === 'search' || view === 'top') && order.length > 0 && (more.loading || running > 0);
        loader.classList.toggle('on', busy);
        evenRowsSoon();
    }
    function moreSoon() {
        if (!nearEnd() || $('reader').classList.contains('open')) return;
        if (referenceRun) {
            if (!running && !more.loading && referenceRun.more && (referenceRun.queue || []).length < 12) referencePage();
            return;
        }
        if (view === 'search') {
            if (more.loading || !current.queries || current.byId) return;
            // NovelDB's next page (`ndbPage`; 0: it has no more) is read as soon as the list is scrolled to
            // its end, while the sites still search; their next pages wait for the search to end.
            var ndbNext = more.ndbHas !== false && progress.noveldb > 0 ? more.ndbPage || 1 : 0;
            if (running) {
                if (!ndbNext) return;
                setMoreLoading(true);
                send({type: 'genericMore', id: searchId, queries: current.queries, page: more.page, ndbPage: ndbNext, sites: false});
                return;
            }
            if (!more.has) return;
            setMoreLoading(true);
            status('Loading more results…');
            send({type: 'genericMore', id: searchId, queries: current.queries, page: more.page, ndbPage: ndbNext});
        } else if (view === 'top') {
            var waiting = Array.from(top.scores.values()).filter(function (entry) { return !entry.group && !entry.excluded; }).length;
            if (waiting) {
                top.limit = order.length + TOP_STEP;
                topRedraw();
            }
            if (waiting >= TOP_STEP || more.loading || !more.has || running) return;
            setMoreLoading(true);
            status('Loading more ' + TOP_NAMES[top.kind] + '…');
            if (top.cached) {
                top.cached = false;
                top.limit = order.length + TOP_STEP;
                loadTop(searchId);  // a kept list: its sites' lists first, then their next pages
                setMoreLoading(false);
                setTimeout(closeSlots, 1500);
            } else if (!isComicKind(top.kind)) {
                topMoreNovels(searchId);
            } else {
                send({type: 'genericTopMore', id: searchId});
            }
        }
    }
    window.addEventListener('scroll', function () {
        clearTimeout(more.timer);
        more.timer = setTimeout(moreSoon, 150);
    }, {passive: true});
    // The search bar slides away while the results scroll down (its tag menu closed with it) and comes
    // back on any scroll up, or at the top; never while the search box has the focus.
    // Only a scroll the reader makes moves it: cards coming in above shift the page by themselves, and that
    // read as scrolling up (the bar bounced in and out while the finger scrolled down).
    var lastScroll = window.scrollY, lastInput = 0, touching = false;
    ['wheel', 'keydown', 'touchmove'].forEach(function (name) {
        window.addEventListener(name, function () { lastInput = Date.now(); }, {passive: true});
    });
    window.addEventListener('touchstart', function () { touching = true; lastInput = Date.now(); }, {passive: true});
    window.addEventListener('touchend', function () { touching = false; lastInput = Date.now(); }, {passive: true});
    window.addEventListener('scroll', function () {
        var y = window.scrollY, header = document.querySelector('header');
        if (!header) return;
        var byReader = touching || Date.now() - lastInput < 800;
        if (!byReader && y > header.offsetHeight) { lastScroll = y; return; }
        var down = y > lastScroll + 4, up = y < lastScroll - 12;
        if (down && y > header.offsetHeight && document.activeElement !== $('query')) {
            if (!header.classList.contains('away')) { closeTags(); header.classList.add('away'); }
        } else if (up || y <= header.offsetHeight) {
            header.classList.remove('away');
        }
        if (down || up) lastScroll = y;
    }, {passive: true});

    // Pulled down past the top (a finger, or the wheel turned on up at the top): the cards in the full order
    // again, with the books that came in since sorted among them.
    var pull = {from: null, wheel: 0, wheelAt: 0, sign: null};
    function pullSign(text) {
        if (!pull.sign) {
            pull.sign = document.createElement('div');
            pull.sign.id = 'pullSort';
            pull.sign.style.cssText = 'position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:50;padding:6px 14px;'
                + 'border-radius:999px;background:var(--accent,#f4b6be);color:var(--accent-ink,#2a1a1d);font-size:14px;'
                + 'box-shadow:0 2px 10px rgba(0,0,0,.35);pointer-events:none;transition:opacity .2s;opacity:0';
            document.body.appendChild(pull.sign);
        }
        if (text) pull.sign.textContent = text;
        pull.sign.style.opacity = text ? '1' : '0';
    }
    function pullCovered() {
        return $('reader').classList.contains('open') || ($('dialog') && $('dialog').open) || !order.length;
    }
    function pulledSort() {
        orderCards(true);
        evenRowsSoon();
        pullSign('Sorted again');
        setTimeout(function () { pullSign(''); }, 900);
    }
    var PULL = 90;
    window.addEventListener('touchstart', function (event) {
        pull.from = window.scrollY <= 0 && !pullCovered() && event.touches.length === 1 ? event.touches[0].clientY : null;
    }, {passive: true});
    window.addEventListener('touchmove', function (event) {
        if (pull.from == null) return;
        var dy = event.touches[0].clientY - pull.from;
        if (window.scrollY > 0 || dy < 0) { pull.from = null; pullSign(''); return; }
        pullSign(dy > PULL ? 'Release to sort again' : dy > 24 ? 'Pull to sort again' : '');
    }, {passive: true});
    window.addEventListener('touchend', function (event) {
        if (pull.from == null) return;
        var dy = (event.changedTouches[0] || {}).clientY - pull.from;
        pull.from = null;
        if (dy > PULL && window.scrollY <= 0) pulledSort(); else pullSign('');
    }, {passive: true});
    window.addEventListener('wheel', function (event) {
        if (event.deltaY >= 0 || window.scrollY > 0 || pullCovered() || event.target.closest('#sitespanel, #tagmenu, dialog')) { pull.wheel = 0; return; }
        var now = Date.now();
        if (now - pull.wheelAt > 600) pull.wheel = 0;
        pull.wheelAt = now;
        pull.wheel -= event.deltaY;
        if (pull.wheel > 600) { pull.wheel = -1e9; pulledSort(); }  // once a pull: the wheel stopped for a moment starts the next
        else if (pull.wheel > 150) {
            pullSign('Keep scrolling up to sort again');
            clearTimeout(pull.hide);
            pull.hide = setTimeout(function () { pullSign(''); }, 700);
        }
    }, {passive: true});

    // The app's theme (Halgakos Rose by default) from the start, even when the app is slow to send it or
    // its message is lost (the page showed the plain default colours).
    if (!appTheme && store(THEME_KEY)) setAppTheme(store(THEME_KEY));
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
            if (view === 'top' && !order.length && !searchRestore()) showTop();
        },
        add: add,
        moreDone: function (id, state) {
            if (id !== searchId) return;
            if (more.discardPendingSort) { more.discardPendingSort = false; setMoreLoading(false); return; }
            if (view === 'search' && state && state.ndbPage) { more.ndbPage = state.ndbPage + 1; more.ndbHas = !!state.ndbMore; }
            if (view === 'search' && state && state.ndbOnly) {
                // NovelDB's page read while the search runs: the sites' next page is still to come.
                setMoreLoading(false);
                setTimeout(closeSlots, 1500);
                countLine();
                empty();
                setTimeout(moreSoon, 300);
                return;
            }
            // A top list's next page comes in parts: the last one to answer finishes it.
            if (state && state.more) more.any = true;
            if (more.parts > 1) { more.parts--; return; }
            more.parts = 0;
            setMoreLoading(false);
            setTimeout(closeSlots, 1500);
            more.page++;
            more.has = !!more.any;
            more.any = false;
            if (view === 'top') { top.limit = order.length + TOP_STEP; topRedraw(); topSave(); }
            status(shownCount() + (more.has ? ' books so far' + noveldbCount() + '; scroll for more.' : ' books; that is all the sites have.'));
            empty();
            setTimeout(moreSoon, 300);  // still at the end (few new books): the next page
        },
        // The picked NovelDB sources' page of books is in (their cards came through add()).
        // The app's theme: {dark, bg, panel, ink, ink2, line, accent, accentInk, chip} as #rrggbb (null: the page's own).
        theme: function (colors) { setAppTheme(colors); },
        ndbTags: function (names) {
            ndbTagNames = Array.isArray(names) ? names.filter(function (n) { return typeof n === 'string' && n; }) : [];
            if ($('tagmenu').classList.contains('open')) drawTags();
        },
        ndbTopDone: function (id, state) {
            if (id !== searchId) return;
            setRunning(-1);
            top.ndbPage = (top.ndbPage || 0) + 1;
            top.ndbMore = !!(state && state.more);
            if (view !== 'top') return;
            top.limit = Math.max(top.limit, order.length + TOP_STEP);
            topRedraw();
            if (top.ndbScrolled) { top.ndbScrolled = false; window.NovelDBGeneric.moreDone(id, {more: top.ndbMore}); return; }
            if (!running) status('The most read on ' + (top.ndbOnly ? 'NovelDB · ' + $('language').selectedOptions[0].text : top.only.join(', '))
                + (top.ndbMore ? '; scroll for more.' : '.'));
            empty();
        },
        progress: function (id, item) {
            if (id !== searchId || !item) return;
            if (item.googleBlocked) { progress.googleBlocked = true; showProgress(); return; }
            if (item.total != null) { progress.expected += item.total; showProgress(); return; }
            // NovelDB's count of matching books, before its cards arrive (scrolling reads the rest).
            if (item.noveldbTotal != null) { progress.noveldb = (progress.noveldb || 0) + item.noveldbTotal; showProgress(); return; }
            siteAnswered(item);
        },
        done: function (id, summary) {
            if (id !== searchId) return;
            setRunning(-1);
            if (view === 'top') {
                topRedraw();
                topSave();
                status(order.length ? 'The most read ' + TOP_NAMES[top.kind] + ' on ' + (top.only.length ? top.only.join(', ')
                        : 'the top ' + TOP_NAMES[top.kind] + ' sites') + ', by their popular pages.'
                    : 'The ' + TOP_NAMES[top.kind] + ' sites\' popular pages did not answer.');
                return;
            }
            var slow = summary && (summary.failed || summary.timed_out);
            if (!running) {
                status(shownCount() + ' books found in ' + seconds(Date.now() - progress.started)
                       + (slow ? ' (some sites did not answer in time)' : '') + noveldbCount()
                       + (progress.noveldb > shownCount() ? '; scroll for more.' : '.') + (progress.googleBlocked ? ' · Google requires verification' : ''));
                empty();
            }
        },
        details: function (card) {
            var group = byUrl.get(key(card.url));
            if (!group) return;
            if (view === 'top' && notInTop(card)) { dropFromTop(group, true); return; }
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
            // (Picked sites show whatever they list, in any language, with or without a synopsis.)
            var picked = view === 'top' && (top.only.length > 0 || !!top.ndbOnly);
            if (view === 'top' && notInTop(group)) { dropFromTop(group, true); return; }
            if (view === 'top' && !picked && !inLanguage(card) && dropFromTop(group)) return;
            var asking = moreDetails(group, card);
            // A book no site tells anything of (no synopsis, no tags) leaves the top list for the next.
            // (Not the one whose panel is open: it would vanish under the reader's eyes.)
            if (view === 'top' && !picked && !asking && thin(group) && !(openGroup === group && $('dialog').open)) {
                thinSite(group.sources[0].site);
                if (dropFromTop(group)) return;
            }
            finishDetails(group);
            if (group.onDetails && $('dialog').open) group.onDetails();
        },
        // A Library book found again, to download what it is missing (an unfinished or outdated download):
        // a search for its title (NovelDB's card and the other sites' copies of it), and its own site's page
        // ({url}, where the download came from) open in its details, with Download.
        openBook: function (book) {
            var title = String((book && book.title) || '').trim(), url = String((book && book.url) || '').trim();
            if ($('dialog').open) $('dialog').close();
            if ($('reader').classList.contains('open')) closeReader();
            picked.splice(0);
            showPicked();
            siteFilter.clear();
            $('query').value = title || url;
            if (title) search();
            if (!/^https?:\/\//i.test(url)) return;
            add([{url: url, title: title || url, author: String(book.author || ''), cover: '', description: '', tags: [],
                  site: hostOf(url), comic: false, source: 'site', complete: false, verified: true, via: 'name'}], searchId);
            var group = byUrl.get(key(url));
            if (group) details(group);
        },
        // A book from the Library (an EPUB on this computer), in the reader.
        // The app's Reload with the reader open: its chapter scraped again, where it was (true); false: no reader.
        reloadReader: function () {
            if (!$('reader').classList.contains('open') || !R.chapters || !R.chapters.length || R.index < 0) return false;
            var index = R.index, at = readingAt();
            R.cache.delete(index);
            if (R.pagesFor) R.pagesFor.delete(index);
            R.slotsIndex = -1;
            R.slots = [];
            openChapter(index, at);
            return true;
        },
        readLocal: function (book) {
            var group = {key: 'local:' + book.url, title: book.title, author: '', cover: '', description: '', tags: [],
                         comic: false, loaded: true, sources: [{url: book.url, site: 'Library', source: 'local'}]};
            // The chapter picked on the Library's book page, else where the Library last left the book.
            if (typeof book.chapter === 'number' && book.chapter >= 0) group.start = {chapter: book.chapter, at: Number(book.at) || 0};
            if ($('dialog').open) $('dialog').close();
            openReader(group, 0);
        },
        back: function () {
            var modal = Array.from(document.querySelectorAll('dialog[open]')).pop();
            if (modal) { modal.close(); return true; }
            if ($('toc').classList.contains('open')) { $('toc').classList.remove('open'); return true; }
            if ($('reader').classList.contains('open')) {
                if (R.full) setFull(false); else closeReader();
                return true;
            }
            var panel = ['tagmenu', 'sitespanel', 'settingspanel'].map($).find(function (node) { return node.classList.contains('open'); });
            if (panel) { panel.classList.remove('open'); return true; }
            if (searchHistory.length) {
                var previous = searchHistory.pop();
                if (previous && !previous.top) return searchRestore(previous);
                $('query').value = ''; picked = []; showPicked(); showTop(true); return true;
            }
            if (view === 'search') { $('query').value = ''; picked = []; showPicked(); showTop(true); return true; }
            return false;
        },
        refreshLocal: refreshLocal,
        readerVisibility: readerVisibility,
        readerStatus: function (id, text) {
            R.alive[id] = readerNow();
            if (R.index >= 0 && R.bookRequest && id === R.bookRequest.id) return;
            if ((id !== R.requestId && id !== R.chapterIds.get(R.index)) || !R.drawNote) return;
            R.noteDetail = text;
            // Once the chapter's images are showing, the note would cover them: the bottom bar says it instead.
            if (streaming()) { $('rWhere').textContent = text; return; }
            R.drawNote();
        },
        readerChapters: function (id, chapters) {
            if (!R.bookRequest || R.bookRequest.id !== id || !chapters.length) return;
            R.alive[id] = readerNow();
            R.bookRequest.update({chapters: chapters});
        },
        readerImages: function (id, found) {
            if (!found || !found.images) return;
            R.alive[id] = readerNow();
            // Kept for the chapter it belongs to (one read ahead shows them when it is opened), shown when open.
            var index = -1;
            R.chapterIds.forEach(function (value, key) { if (value === id) index = key; });
            if (index < 0) return;
            var kept = R.pagesFor.get(index) || [];
            found.images.forEach(function (src, n) { kept[(found.from || 0) + n] = src; });
            R.pagesFor.set(index, kept);
            if (index === R.index) streamImages(R.index, found);
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
