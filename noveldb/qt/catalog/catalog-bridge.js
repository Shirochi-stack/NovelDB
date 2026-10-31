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
 * - download {url, title}: the page can be downloaded, so a floating Download button (bottom right)
 *   adds it to the cart. It reads "✓ In cart" while the URL is in the cart; tapping again removes it.
 *   The app sends it on supported sites (the ones it downloads from) and never on NovelDB.
 * - cartLinks: only on sites that are neither NovelDB nor supported; links to other sites that show
 *   text are renamed "Download" and go to the cart. Otherwise no link is renamed.
 * - Fill mode (desktop app) widens page columns that stay narrower than the window to the whole window.
 * - Card buttons, the Download button and renamed links show the app's download artwork (DOWNLOAD_ICON).
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
    // The app's download artwork (Halgakos with a download arrow), 96 px WebP.
    var DOWNLOAD_ICON = 'data:image/webp;base64,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';
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
    var DOWNLOAD_LABEL = 'Download', RENAMED = 'data-noveldb-renamed';
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
            if (!nodes.length || (anchor.hasAttribute(RENAMED) && anchor.textContent.trim() === DOWNLOAD_LABEL)) continue;
            if (!renamed.has(anchor)) renamed.set(anchor, nodes.map(function (n) { return [n, n.nodeValue]; }));
            nodes.forEach(function (n, index) { n.nodeValue = index === 0 ? DOWNLOAD_LABEL : ''; });
            anchor.setAttribute(RENAMED, '');
        }
        renamed.forEach(function (originals, anchor) { if (!anchor.isConnected) renamed.delete(anchor); });
        if (!mode.cartLinks && renamed.size) {
            renamed.forEach(function (originals, anchor) {
                originals.forEach(function (pair) { pair[0].nodeValue = pair[1]; });
                anchor.removeAttribute(RENAMED);
            });
            renamed.clear();
        }
    }
    // Fill mode (desktop): page columns whose max-width is 900px or more but narrower than the window
    // (NovelDB Next keeps 1100px) widen to the whole window, with no fixed cap. Dialogs and side panels
    // keep their size. A full-width NovelDB Next grid on a large window holds many more cards, so a very
    // wide window can scroll below 60fps; that is the trade for using the whole width.
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
    /** The floating button for the page the app can download: "Download" after the artwork, or "✓ In cart" once added. */
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
        'filter:grayscale(1);opacity:.8;-webkit-tap-highlight-color:transparent;' +
        'background-image:url(' + DOWNLOAD_ICON + ');background-position:center;background-size:26px 26px;background-repeat:no-repeat}' +
        '.noveldb-cart[aria-pressed="true"]{background-color:#E18F98;filter:none;opacity:1;box-shadow:0 0 0 2px #fff}' +
        '.noveldb-cart-next{left:auto;right:.3rem;bottom:.3rem;z-index:40;width:30px;height:30px;font-size:15px;background-size:24px 24px}' +
        '.noveldb-cart:disabled{opacity:.5}' +
        '.noveldb-download{position:fixed;right:16px;bottom:max(64px,calc(env(safe-area-inset-bottom) + 16px));z-index:2147483647;display:block;' +
        'height:40px;margin:0;padding:0 14px;border:0;border-radius:.4rem;background:rgba(0,0,0,.55);color:#fff;' +
        'font:600 14px/40px system-ui,-apple-system,sans-serif;white-space:nowrap;cursor:pointer;opacity:.85;' +
        '-webkit-tap-highlight-color:transparent}' +
        '.noveldb-download[aria-pressed="true"]{background:#E18F98;opacity:1;box-shadow:0 0 0 2px #fff}' +
        '.noveldb-download[aria-pressed="false"]::before,a[' + RENAMED + ']::before{content:"";display:inline-block;' +
        'width:1.6em;height:1.6em;margin-right:.35em;vertical-align:-.45em;' +
        'background:url(' + DOWNLOAD_ICON + ') center/contain no-repeat}' +
        // NovelDB Next's hover preview covers the neighbouring cards; the details view shows the same.
        '.scroll-main div.pop[role=presentation]{display:none!important}' +
        '[data-noveldb-fill]{max-width:none!important}' +
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
