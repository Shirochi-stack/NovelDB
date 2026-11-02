/*
 * The Catalog's generic search page (desktop). Runs in the bridge's isolated world, next to
 * catalog-bridge.js (NovelDBQt.postMessage) and the LNReader bundle (window.__LNR_*).
 * - Searching asks the app ({type: 'genericSearch'}): it searches the sites' own search pages, their
 *   genre and tag pages and lightnovel-crawler's searches, and sends cards back (NovelDBGeneric.add).
 * - The LNReader plugins of the sites in the list search here too; their requests go to the app
 *   ({type: 'genericFetch'}, answered by NovelDBGeneric.fetched).
 * - Opening a card asks the app for the book page's details ({type: 'genericDetails'}).
 * - The + button adds the book to the link cart ({type: 'cartAdd'}); the app keeps the cart.
 */
(function () {
    'use strict';
    if (window.NovelDBGeneric) return;
    var $ = function (id) { return document.getElementById(id); };
    var config = {sites: [], lnreader: []};
    var cards = new Map(), cart = new Set(), pending = {}, nextId = 0, searchId = 0, running = 0;

    function send(message) { window.NovelDBQt && window.NovelDBQt.postMessage(JSON.stringify(message)); }
    function key(url) { return String(url || '').replace(/\/+$/, ''); }
    function esc(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }

    // -- the LNReader plugins' requests, made by the app ------------------------------------------
    window.__LNR_transport = function (request) {
        return new Promise(function (resolve) {
            var id = ++nextId;
            pending[id] = resolve;
            send({type: 'genericFetch', id: id, request: request});
        });
    };

    function status(text) { $('status').textContent = text; }

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
    function within(promise, ms) {
        return Promise.race([promise, new Promise(function (resolve) { setTimeout(function () { resolve('[]'); }, ms); })]);
    }

    function setRunning(delta) {
        running = Math.max(0, running + delta);
        $('stop').disabled = running === 0;
        $('go').disabled = false;
    }

    function wanted(card) {
        var kind = $('kind').value;
        return kind === 'all' || (kind === 'comic') === !!card.comic;
    }

    function render(card) {
        var element = card.element;
        if (!element) {
            element = card.element = document.createElement('div');
            element.className = 'card';
            element.addEventListener('click', function (event) {
                if (event.target.closest('.add')) return;
                details(card);
            });
            $('grid').appendChild(element);
        }
        var cover = card.cover
            ? '<img loading="lazy" referrerpolicy="no-referrer" alt="" src="' + esc(card.cover) + '">'
            : esc(card.title);
        var inCart = cart.has(card.url);
        element.innerHTML = '<div class="cover">' + cover + '</div>'
            + '<span class="site">' + esc(card.site) + '</span>'
            + '<button class="add' + (inCart ? ' in' : '') + '" title="' + (inCart ? 'In the link cart' : 'Add to the link cart')
            + '" type="button">' + (inCart ? '✓' : '+') + '</button>'
            + '<div class="body"><div class="title">' + esc(card.title) + '</div>'
            + '<div class="meta">' + esc(card.author || (card.tags || []).slice(0, 3).join(', ')) + '</div></div>';
        element.querySelector('.add').addEventListener('click', function () { toggleCart(card); });
        var image = element.querySelector('img');
        if (image) {
            image.addEventListener('error', function () {
                // Some sites refuse covers to other pages: the app fetches it with the site's address.
                if (card.coverTried) { image.remove(); return; }
                card.coverTried = true;
                var id = ++nextId;
                pending[id] = function (data) { if (data && data.dataUrl) { card.cover = data.dataUrl; render(card); } };
                send({type: 'genericImage', id: id, url: card.cover, referer: card.url});
            }, {once: true});
        }
        element.style.display = wanted(card) ? '' : 'none';
        $('empty').style.display = cards.size ? 'none' : '';
    }

    function toggleCart(card) {
        if (cart.has(card.url)) {
            send({type: 'cartRemove', url: card.url, title: card.title});
        } else {
            send({type: 'cartAdd', url: card.url, title: card.title, source: ''});
        }
    }

    function add(list, id) {
        if (id !== undefined && id !== searchId) return;
        (list || []).forEach(function (incoming) {
            var known = cards.get(key(incoming.url));
            if (known) {
                ['title', 'author', 'cover', 'description', 'tags', 'status'].forEach(function (name) {
                    var value = incoming[name];
                    if (value && (!known[name] || (name === 'description' && value.length > known[name].length)
                            || (name === 'title' && incoming.complete) || (name === 'cover' && !known.coverTried))) {
                        known[name] = value;
                    }
                });
                render(known);
            } else {
                cards.set(key(incoming.url), incoming);
                render(incoming);
            }
        });
        status(cards.size + ' books found' + (running ? '…' : '.'));
    }

    function details(card) {
        var dialog = $('dialog');
        var show = function () {
            var inCart = cart.has(card.url);
            dialog.innerHTML = '<div class="details"><div class="cover">'
                + (card.cover ? '<img referrerpolicy="no-referrer" alt="" src="' + esc(card.cover) + '">' : '') + '</div>'
                + '<div><h2>' + esc(card.title) + '</h2><div class="meta">' + esc([card.author, card.site, card.status].filter(Boolean).join(' · ')) + '</div>'
                + '<div class="chips">' + (card.tags || []).map(function (tag) { return '<span class="chip">' + esc(tag) + '</span>'; }).join('') + '</div>'
                + '<p>' + esc(card.description || (card.complete ? 'No description on the book page.' : 'Reading the book page…')) + '</p></div></div>'
                + '<div class="actions"><button type="button" data-act="open">Open page</button>'
                + '<button type="button" class="primary" data-act="cart">' + (inCart ? 'Remove from cart' : 'Add to cart') + '</button>'
                + '<button type="button" data-act="close">Close</button></div>';
            dialog.querySelector('[data-act=open]').onclick = function () { send({type: 'open', url: card.url, title: card.title}); };
            dialog.querySelector('[data-act=cart]').onclick = function () { toggleCart(card); };
            dialog.querySelector('[data-act=close]').onclick = function () { dialog.close(); };
            dialog.querySelectorAll('.chip').forEach(function (chip) {
                chip.onclick = function () { dialog.close(); $('mode').value = 'tag'; $('query').value = chip.textContent; search(); };
            });
        };
        card.onDetails = show;
        show();
        if (!dialog.open) dialog.showModal();
        if (!card.complete) send({type: 'genericDetails', id: searchId, card: strip(card)});
    }

    function strip(card) {
        var out = {};
        ['url', 'title', 'author', 'cover', 'description', 'tags', 'site', 'comic', 'source', 'complete'].forEach(function (name) { out[name] = card[name]; });
        return out;
    }

    // -- LNReader: the sites' own search, run here --------------------------------------------------
    function lnreaderSearch(id, query, mode) {
        var plugins = config.lnreader || [];
        if (!plugins.length || !window.__LNR_search) return;
        setRunning(1);
        var index = 0, active = 0;
        function next() {
            if (id !== searchId) { setRunning(-1); return; }
            while (active < 4 && index < plugins.length) {
                var plugin = plugins[index++];
                active++;
                within(mode === 'tag' ? lnreaderTag(plugin, query) : window.__LNR_search(plugin.id, query, 1), 25000)
                    .then(function (raw) {
                        var found = [];
                        try { found = JSON.parse(raw); } catch (e) { found = []; }
                        if (Array.isArray(found)) {
                            if (mode !== 'tag') found = found.filter(function (item) { return relevant(item.name, query); });
                            add(found.slice(0, 15).map(function (item) {
                                return {url: item.url, title: item.name, cover: item.cover || '', author: '', description: '',
                                        tags: [], site: this.site, comic: !!this.comic, source: 'lnreader', complete: false};
                            }, this), id);
                        }
                    }.bind(plugin))
                    .catch(function () {})
                    .then(function () { active--; next(); });
            }
            if (active === 0 && index >= plugins.length) setRunning(-1);
        }
        next();
    }

    // A tag: the plugin's genre filter when one of its options has that name, else a search.
    function lnreaderTag(plugin, tag) {
        return window.__LNR_genre ? window.__LNR_genre(plugin.id, tag) : window.__LNR_search(plugin.id, tag, 1);
    }

    function search(event) {
        if (event) event.preventDefault();
        var query = $('query').value.trim();
        if (!query) return;
        searchId++;
        cards.clear();
        $('grid').innerHTML = '';
        $('empty').style.display = 'none';
        running = 0;
        setRunning(1);
        status('Searching ' + (config.sites.length || 100) + ' sites for “' + query + '”…');
        send({type: 'genericSearch', id: searchId, query: query, mode: $('mode').value});
        lnreaderSearch(searchId, query, $('mode').value);
    }

    $('form').addEventListener('submit', search);
    $('stop').addEventListener('click', function () {
        send({type: 'genericStop', id: searchId});
        searchId++;
        running = 0;
        setRunning(0);
        status(cards.size + ' books found (stopped).');
    });
    $('kind').addEventListener('change', function () { cards.forEach(render); });

    window.NovelDBGeneric = {
        init: function (value) { config = value || config; },
        add: add,
        done: function (id, summary) {
            if (id !== searchId) return;
            setRunning(-1);
            var slow = summary && (summary.failed || summary.timed_out);
            if (!running) status(cards.size + ' books found' + (slow ? ' (some sites did not answer in time)' : '') + '.');
        },
        details: function (card) {
            var known = cards.get(key(card.url));
            if (!known) return;
            add([card]);
            known.complete = true;
            if (known.onDetails && $('dialog').open) known.onDetails();
        },
        fetched: function (id, result) {
            var resolve = pending[id];
            delete pending[id];
            if (resolve) resolve(result);
        },
        setCart: function (urls) {
            cart = new Set(urls || []);
            cards.forEach(render);
        }
    };
    send({type: 'genericReady'});
})();
