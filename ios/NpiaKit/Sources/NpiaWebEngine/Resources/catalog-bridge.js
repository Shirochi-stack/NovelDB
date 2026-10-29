/*
 * NovelDB app bridge for the catalog website, injected by the mobile and desktop apps.
 * - Tapping a card opens View Details instead of the novel page.
 * - "Open novel" and other outside links go to the app, which opens its Browser tab (or, on other
 *   sites, the link cart).
 * - A link-cart button on each card adds or removes its URL from the app's cart. NovelDB Next
 *   cards hold no link, so their button (bottom right of the cover, above the title) reads it from
 *   the card's details, opened out of sight, and remembers it.
 * - The page address is reported as {type: 'location', url, title}: on start and whenever it changes
 *   (pushState, replaceState, popstate, hashchange; many sites are single-page apps). The app decides
 *   what the page is and answers with setMode({cartLinks, download}); setMode({cartLinks}) alone works too.
 * - download {url, title}: the page can be downloaded, so a floating "⬇️ Download" button (bottom right)
 *   adds it to the cart. It reads "✓ In cart" while the URL is in the cart; tapping again removes it.
 *   The app sends it on supported sites (the ones it downloads from) and never on NovelDB.
 * - cartLinks: only on sites that are neither NovelDB nor supported; links to other sites that show
 *   text are renamed "⬇️ Download" and go to the cart. Otherwise no link is renamed.
 * - Fill mode (desktop app) widens page columns capped narrower than the window, up to 1300px.
 * - Taps show no highlight flash, the NovelDB header is slimmer, and NovelDB Next's hover preview is off.
 * - On phones, dialog heights use the window height (some Android WebViews resolve dvh units to 0)
 *   and the details cover is kept short without changing its shape.
 */
(function () {
    'use strict';
    if (window.NovelDBApp) return;
    // Ads and embeds inside a page (Android runs this in every frame) are neither reported nor decorated.
    try { if (window.top !== window) return; } catch (_) { return; }
    function send(message) {
        var text = JSON.stringify(message);
        try {
            if (window.NovelDBAndroid) window.NovelDBAndroid.postMessage(text);
            else if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.noveldb) window.webkit.messageHandlers.noveldb.postMessage(text);
            else if (window.NovelDBQt) window.NovelDBQt.postMessage(text);
        } catch (_) {}
    }
    var cart = new Set();
    function cardUrl(card) {
        var link = card && card.querySelector('.card-cover-wrap');
        return link ? link.href : '';
    }
    function paint(button) {
        var inCart = cart.has(button.dataset.url), pressed = String(inCart);
        if (button.getAttribute('aria-pressed') === pressed) return;
        button.setAttribute('aria-pressed', pressed);
        button.title = inCart ? 'Remove from link cart' : 'Add to link cart';
        button.setAttribute('aria-label', button.title);
    }
    function cartButton(parent, extraClass) {
        var button = parent.querySelector('.noveldb-cart');
        if (!button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'noveldb-cart' + (extraClass ? ' ' + extraClass : '');
            button.textContent = '⬇️';
            parent.appendChild(button);
        }
        return button;
    }
    // NovelDB Next: links read from details, keyed by title and author, kept between visits.
    var nextLinks = {};
    try { nextLinks = JSON.parse(localStorage.getItem('noveldb-app-links') || '{}') || {}; } catch (_) {}
    function nextKey(card) {
        var title = card.querySelector('.line-clamp-2');
        var author = card.querySelector('.text-ink-2 .truncate');
        return (title ? title.textContent.trim() : '') + '|' + (author ? author.textContent.trim() : '');
    }
    function rememberNext(card, url) {
        nextLinks[nextKey(card)] = url;
        var keys = Object.keys(nextLinks);
        if (keys.length > 2000) keys.slice(0, keys.length - 2000).forEach(function (key) { delete nextLinks[key]; });
        try { localStorage.setItem('noveldb-app-links', JSON.stringify(nextLinks)); } catch (_) {}
    }
    // What each NovelDB Next card was last decorated for. The list reuses its card elements while it
    // scrolls, so a card gets work again only when it shows another novel (or after a resize).
    var nextSeen = new WeakMap();
    function decorateNext() {
        var cards = document.querySelectorAll('div.card[data-gid]'), fresh = [];
        for (var i = 0; i < cards.length; i++) {
            var card = cards[i];
            if (!card.querySelector('.card-btn')) continue;
            var cover = card.querySelector('.bg-raised') || card;
            // The compact layout prints the title on a strip across the bottom of the cover.
            var strip = cover.querySelector('.bg-gradient-to-t');
            var key = nextKey(card), seen = key + (strip ? '|strip' : '');
            var known = cover.querySelector('.noveldb-cart');
            if (known && nextSeen.get(card) === seen) { paint(known); continue; }
            fresh.push({card: card, cover: cover, strip: strip, key: key, seen: seen});
        }
        // Measure every new strip first, then write: one layout for all of them, not one per card.
        fresh.forEach(function (item) {
            // Its middle sits on the strip's top edge, just above the title text.
            item.bottom = item.strip ? Math.max(4, item.strip.offsetHeight - 15) + 'px' : '';
        });
        fresh.forEach(function (item) {
            var button = cartButton(item.cover, 'noveldb-cart-next');
            if (button.style.bottom !== item.bottom) button.style.bottom = item.bottom;
            var url = nextLinks[item.key] || '';
            if (button.dataset.url !== url) button.dataset.url = url;
            paint(button);
            nextSeen.set(item.card, item.seen);
        });
    }
    /** Opens a NovelDB Next card's details out of sight and reads its source link and title. */
    function peekNext(card) {
        return new Promise(function (resolve) {
            var opener = card.querySelector('.card-btn');
            if (!opener) { resolve(null); return; }
            var root = document.documentElement;
            root.classList.add('noveldb-peek');
            opener.click();
            var tries = 0;
            function finish(result) {
                var close = document.querySelector('button[aria-label="Close details"]');
                if (close) close.click(); else history.back();
                setTimeout(function () { root.classList.remove('noveldb-peek'); }, 350);
                resolve(result);
            }
            (function poll() {
                var links = document.querySelectorAll('#app > aside a[title^="Open source"]');
                var link = links[links.length - 1];
                if (link && link.href) finish({url: link.href, title: linkTitle(link)});
                else if (++tries > 60) finish(null);
                else setTimeout(poll, 50);
            })();
        });
    }
    function toggleCart(button, url, title, source) {
        var adding = !cart.has(url);
        if (adding) cart.add(url); else cart.delete(url);
        paint(button);
        send({type: adding ? 'cartAdd' : 'cartRemove', url: url, title: title, source: source});
    }
    // Outside links renamed while the app sends them to the cart; their original text, to restore.
    var mode = {cartLinks: false, fill: false, download: null};
    var DOWNLOAD_LABEL = '⬇️ Download';
    var renamed = new Map();
    function shownText(anchor) {
        var nodes = [], walker = document.createTreeWalker(anchor, NodeFilter.SHOW_TEXT), node;
        while ((node = walker.nextNode())) if (node.nodeValue.trim()) nodes.push(node);
        return nodes;
    }
    function renameLinks() {
        if (!mode.cartLinks && !renamed.size) return;
        var anchors = document.querySelectorAll('a[href]');
        for (var i = 0; i < anchors.length; i++) {
            var anchor = anchors[i];
            if (!mode.cartLinks || !external(anchor)) continue;
            var nodes = shownText(anchor);
            if (!nodes.length || anchor.textContent.trim() === DOWNLOAD_LABEL) continue;
            if (!renamed.has(anchor)) renamed.set(anchor, nodes.map(function (n) { return [n, n.nodeValue]; }));
            nodes.forEach(function (n, index) { n.nodeValue = index === 0 ? DOWNLOAD_LABEL : ''; });
        }
        renamed.forEach(function (originals, anchor) { if (!anchor.isConnected) renamed.delete(anchor); });
        if (!mode.cartLinks && renamed.size) {
            renamed.forEach(function (originals) { originals.forEach(function (pair) { pair[0].nodeValue = pair[1]; }); });
            renamed.clear();
        }
    }
    // Fill mode (desktop): page columns capped at 900px or more (NovelDB Next keeps 1100px) widen to
    // the window, up to 1300px. Dialogs and side panels keep their size. A full-width NovelDB Next grid
    // on a large window holds 180 cards, more than the desktop app's browser draws at 60 frames a
    // second while scrolling (it held about 45); at 1300px it holds 100 and keeps about 60.
    var lastFill = 0, fillChecked = new WeakSet(), filled = false;
    function fillWidth() {
        var i;
        if (!mode.fill) {
            if (!filled) return;
            var marked = document.querySelectorAll('[data-noveldb-fill]');
            for (i = 0; i < marked.length; i++) marked[i].removeAttribute('data-noveldb-fill');
            filled = false;
            return;
        }
        var now = Date.now();
        if (now - lastFill < 400) return;
        lastFill = now;
        var candidates = document.querySelectorAll('[class*="max-w-"],[class*="container"],main,#app > div,body > div');
        for (i = 0; i < candidates.length; i++) {
            var el = candidates[i];
            if (fillChecked.has(el)) continue;
            fillChecked.add(el);
            if (el.hasAttribute('data-noveldb-fill')) continue;
            if (el.closest('[role=dialog],dialog,aside,[aria-modal=true],.novel-details')) continue;
            var max = parseFloat(getComputedStyle(el).maxWidth);
            if (max >= 900 && max < innerWidth - 40) { el.setAttribute('data-noveldb-fill', ''); filled = true; }
        }
    }
    // The page address, reported to the app on start and whenever it changes. The app answers with
    // setMode({download}) for a page it can download; the address it names must be the one reported.
    var reported = '', reportedTitle = '', titleReports = 0;
    function sameAddress(a, b) {
        function plain(url) { return String(url).replace(/#$/, '').replace(/\/+(?=$|[?#])/, ''); }
        return plain(a) === plain(b);
    }
    function reportLocation() {
        var href = location.href, title = (document.title || '').replace(/\s+/g, ' ').trim();
        var moved = href !== reported;
        // A title that settles after navigation is reported again, a few times at most.
        if (!moved && (title === reportedTitle || titleReports >= 3)) return;
        titleReports = moved ? 0 : titleReports + 1;
        reported = href;
        reportedTitle = title;
        // The old page's Download button must not outlive the address it was made for.
        if (moved && mode.download) { mode.download = null; downloadButton(); }
        send({type: 'location', url: href, title: title});
    }
    function toggleDownload(info) {
        var adding = !cart.has(info.url);
        if (adding) cart.add(info.url); else cart.delete(info.url);
        send({type: adding ? 'cartAdd' : 'cartRemove', url: info.url, title: info.title, source: ''});
        decorate();
    }
    /** The floating button for the page the app can download: "⬇️ Download", or "✓ In cart" once added. */
    function downloadButton() {
        var info = mode.download, button = document.querySelector('.noveldb-download');
        if (!info || !sameAddress(info.url, location.href) || !document.body) {
            if (button) button.remove();
            return;
        }
        if (!button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'noveldb-download';
            document.body.appendChild(button);
        }
        var inCart = cart.has(info.url), label = inCart ? '✓ In cart' : DOWNLOAD_LABEL;
        if (button.textContent !== label) button.textContent = label;
        button.dataset.url = info.url;
        button.setAttribute('aria-pressed', String(inCart));
        var tip = inCart ? 'Remove from link cart' : 'Add to link cart';
        if (button.title !== tip) button.title = tip;
        button.setAttribute('aria-label', tip);
    }
    function decorate() {
        reportLocation();
        renameLinks();
        downloadButton();
        fillWidth();
        decorateNext();
        var areas = document.querySelectorAll('.novel-card .card-cover-area');
        for (var i = 0; i < areas.length; i++) {
            var area = areas[i], card = area.closest('.novel-card'), url = cardUrl(card);
            if (!url) continue;
            var button = cartButton(area);
            if (button.dataset.url !== url) button.dataset.url = url;
            paint(button);
        }
    }
    var queued = false, lastDecorated = 0, SPACING = 100;
    function schedule() {
        if (queued) return;
        queued = true;
        // A hidden page (another tab on top, an offscreen view) gets no animation frames; the timer still decorates it.
        function run() { if (queued) { queued = false; lastDecorated = Date.now(); decorate(); } }
        function soon() { requestAnimationFrame(run); setTimeout(run, 150); }
        // The first change shows on the next frame; while a list keeps changing (scrolling), every 100 ms.
        var wait = lastDecorated + SPACING - Date.now();
        if (wait > 0) setTimeout(soon, wait); else soon();
    }
    /** A change made by this script only (a cart button going in, its label): no second pass needed. */
    function ours(record) {
        var target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        if (target && target.closest && target.closest('.noveldb-cart,.noveldb-download')) return true;
        if (record.type !== 'childList') return false;
        var nodes = Array.prototype.slice.call(record.addedNodes).concat(Array.prototype.slice.call(record.removedNodes));
        return nodes.length > 0 && nodes.every(function (node) {
            return node.nodeType === 1 && (node.classList.contains('noveldb-cart') || node.classList.contains('noveldb-download'));
        });
    }
    /** A name for a link going to the cart: its text, or the nearest heading when the text is generic ("Source page"). */
    function linkTitle(anchor) {
        var text = (anchor.textContent || '').replace(/\s+/g, ' ').trim();
        if (text.length > 15 && !/^(open|view|read|source|link|visit|go)\b/i.test(text)) return text.slice(0, 200);
        for (var node = anchor.parentElement, depth = 0; node && depth < 8; node = node.parentElement, depth++) {
            var heading = node.querySelector('h1, h2, h3');
            var name = heading && heading.textContent.replace(/\s+/g, ' ').trim();
            if (name) return name.slice(0, 200);
        }
        return text.slice(0, 200);
    }
    function external(anchor) {
        if (!anchor || !anchor.href || anchor.hasAttribute('download')) return false;
        if (!/^https?:/i.test(anchor.href)) return false;
        try { return new URL(anchor.href).origin !== location.origin; } catch (_) { return false; }
    }
    document.addEventListener('click', function (event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var download = target.closest('.noveldb-download');
        if (download) {
            event.preventDefault();
            event.stopPropagation();
            if (mode.download) toggleDownload(mode.download);
            return;
        }
        var pressed = target.closest('.noveldb-cart');
        if (pressed) {
            event.preventDefault();
            event.stopPropagation();
            var nextCard = pressed.closest('div.card[data-gid]');
            if (nextCard) {
                var known = pressed.dataset.url;
                var titleNode = nextCard.querySelector('.line-clamp-2');
                if (known) { toggleCart(pressed, known, titleNode ? titleNode.textContent.trim() : '', ''); return; }
                if (pressed.disabled) return;
                pressed.disabled = true;
                peekNext(nextCard).then(function (found) {
                    pressed.disabled = false;
                    if (!found) return;
                    rememberNext(nextCard, found.url);
                    pressed.dataset.url = found.url;
                    toggleCart(pressed, found.url, found.title, '');
                });
                return;
            }
            var card = pressed.closest('.novel-card');
            var titleEl = card && card.querySelector('.card-title');
            toggleCart(pressed, pressed.dataset.url || cardUrl(card), titleEl ? titleEl.textContent.trim() : '',
                       card ? card.dataset.source || '' : '');
            return;
        }
        var cover = target.closest('.card-cover-wrap');
        if (cover) {
            viewport();
            event.preventDefault();
            event.stopPropagation();
            var details = cover.closest('.novel-card').querySelector('.cover-details');
            if (details) details.click();
            return;
        }
        var anchor = target.closest('a');
        if (external(anchor)) {
            event.preventDefault();
            event.stopPropagation();
            send({type: 'open', url: anchor.href, title: linkTitle(anchor)});
        }
    }, true);
    function viewport() {
        if (innerHeight > 100 && document.documentElement) document.documentElement.style.setProperty('--noveldb-vh', innerHeight + 'px');
    }
    addEventListener('resize', viewport);
    addEventListener('resize', function () { nextSeen = new WeakMap(); fillChecked = new WeakSet(); schedule(); });
    var style = document.createElement('style');
    style.textContent =
        '*{-webkit-tap-highlight-color:transparent!important}' +
        ':focus:not(:focus-visible){outline:none!important}' +
        'header:has(.header-inner .brand-icon){padding:.2rem .8rem!important}' +
        'header:has(.header-inner .brand-icon) h1{font-size:1rem!important;gap:.35rem!important}' +
        'header:has(.header-inner .brand-icon) .brand-icon{width:20px!important;height:20px!important}' +
        'header:has(.header-inner .brand-icon) .subtitle{display:none!important}' +
        '.noveldb-cart{position:absolute;left:.4rem;bottom:.4rem;z-index:2;display:grid;place-items:center;width:32px;height:32px;' +
        'padding:0;border:0;border-radius:.4rem;background:rgba(0,0,0,.55);font-size:17px;line-height:1;cursor:pointer;' +
        'filter:grayscale(1);opacity:.8;-webkit-tap-highlight-color:transparent}' +
        '.noveldb-cart[aria-pressed="true"]{background:#E18F98;filter:none;opacity:1;box-shadow:0 0 0 2px #fff}' +
        '.noveldb-cart-next{left:auto;right:.3rem;bottom:.3rem;z-index:40;width:30px;height:30px;font-size:15px}' +
        '.noveldb-cart:disabled{opacity:.5}' +
        '.noveldb-download{position:fixed;right:16px;bottom:max(64px,calc(env(safe-area-inset-bottom) + 16px));z-index:2147483647;display:block;' +
        'height:40px;margin:0;padding:0 14px;border:0;border-radius:.4rem;background:rgba(0,0,0,.55);color:#fff;' +
        'font:600 14px/40px system-ui,-apple-system,sans-serif;white-space:nowrap;cursor:pointer;opacity:.85;' +
        '-webkit-tap-highlight-color:transparent}' +
        '.noveldb-download[aria-pressed="true"]{background:#E18F98;opacity:1;box-shadow:0 0 0 2px #fff}' +
        // NovelDB Next's hover preview covers the neighbouring cards; the details view shows the same.
        '.scroll-main div.pop[role=presentation]{display:none!important}' +
        '[data-noveldb-fill]{max-width:1300px!important}' +
        'html.noveldb-peek #app > aside,html.noveldb-peek button[aria-label="Close details"]' +
        '{visibility:hidden!important;animation:none!important;transition:none!important}';
    // Phone-sized dialogs (window height instead of dvh, a short cover). The desktop app keeps the
    // site's own layout: these rules squashed the cover image there.
    if (!window.NovelDBQt) style.textContent +=
        '.novel-details{max-height:calc(var(--noveldb-vh,100vh) - 1rem)!important}' +
        '.novel-details-image img{max-height:min(calc(var(--noveldb-vh,100vh) * .2),140px)!important;' +
        'object-fit:contain!important;width:auto!important;max-width:100%!important}' +
        '.novel-details-content [data-field=synopsis]{max-height:min(calc(var(--noveldb-vh,100vh) * .25),150px)!important}' +
        '.site-settings-dialog{max-height:min(calc(var(--noveldb-vh,100vh) * .9),560px)!important}' +
        '.mobile-controls-floating{max-height:calc(var(--noveldb-vh,100vh) * .75)!important}';
    // Some hosts run this before the page has an <html> or <body> element (and a page that never finishes loading
    // never fires DOMContentLoaded), so wait for the elements themselves.
    function whenReady(ready, callback) {
        if (ready()) { callback(); return; }
        var wait = new MutationObserver(function () { if (ready()) { wait.disconnect(); callback(); } });
        wait.observe(document, {childList: true, subtree: true});
    }
    whenReady(function () { return document.head || document.documentElement; }, function () {
        (document.head || document.documentElement).appendChild(style);
    });
    // Same-document navigation. The wrapped history methods only see the page's own calls where this
    // script shares its world (Android); the desktop app runs it in an isolated world, where the
    // Navigation API, popstate, hashchange, DOM changes and a slow check catch the same changes.
    ['pushState', 'replaceState'].forEach(function (name) {
        try {
            var original = history[name];
            history[name] = function () {
                var result = original.apply(this, arguments);
                reportLocation();
                return result;
            };
        } catch (_) {}
    });
    addEventListener('popstate', reportLocation);
    addEventListener('hashchange', reportLocation);
    try { if (window.navigation) window.navigation.addEventListener('currententrychange', function () { reportLocation(); }); } catch (_) {}
    setInterval(reportLocation, 1000);
    function start() {
        viewport();
        new MutationObserver(function (records) {
            for (var i = 0; i < records.length; i++) if (!ours(records[i])) { schedule(); return; }
        }).observe(document.body, {childList: true, subtree: true, characterData: true});
        var titleNode = document.querySelector('title');
        if (titleNode) new MutationObserver(reportLocation).observe(titleNode, {childList: true, characterData: true, subtree: true});
        schedule();
    }
    whenReady(function () { return document.body; }, start);
    window.NovelDBApp = {
        setCart: function (urls) { cart = new Set(urls || []); schedule(); },
        setMode: function (next) {
            var page = next && next.download;
            mode = {
                cartLinks: !!(next && next.cartLinks),
                fill: !!(next && next.fill),
                download: page && typeof page.url === 'string' && /^https?:/i.test(page.url)
                    ? {url: page.url, title: String(page.title || '')} : null
            };
            lastFill = 0;
            fillChecked = new WeakSet();
            schedule();
        }
    };
    send({type: 'ready'});
    reportLocation();
})();
