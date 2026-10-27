// Generated from the Android app asset catalog-bridge.js; keep the two copies identical.

/// Script injected into the catalog website (see CatalogView).
enum CatalogBridgeScript {
    static let source = #"""
/*
 * NovelDB app bridge for the catalog website, injected by the mobile app.
 * - Tapping a card opens View Details instead of the novel page.
 * - "Open novel" and other outside links go to the app (Browser tab).
 * - A link-cart button on each card adds or removes its URL from the app's cart. NovelDB Next
 *   cards hold no link, so their button (bottom right of the cover, above the title) reads it from
 *   the card's details, opened out of sight, and remembers it.
 * - When the app sends outside links to the cart (sites other than NovelDB, setMode cartLinks),
 *   links to other sites that show text are renamed "🔗 Download".
 * - Taps show no highlight flash, and the NovelDB header is slimmer.
 * - Dialog heights use the window height: some Android WebViews resolve dvh units to 0.
 */
(function () {
    'use strict';
    if (window.NovelDBApp) return;
    function send(message) {
        var text = JSON.stringify(message);
        try {
            if (window.NovelDBAndroid) window.NovelDBAndroid.postMessage(text);
            else if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.noveldb) window.webkit.messageHandlers.noveldb.postMessage(text);
        } catch (_) {}
    }
    var cart = new Set();
    function cardUrl(card) {
        var link = card && card.querySelector('.card-cover-wrap');
        return link ? link.href : '';
    }
    function paint(button) {
        var inCart = cart.has(button.dataset.url);
        button.setAttribute('aria-pressed', String(inCart));
        button.title = inCart ? 'Remove from link cart' : 'Add to link cart';
        button.setAttribute('aria-label', button.title);
    }
    function cartButton(parent, extraClass) {
        var button = parent.querySelector('.noveldb-cart');
        if (!button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'noveldb-cart' + (extraClass ? ' ' + extraClass : '');
            button.textContent = '🔗';
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
    function decorateNext() {
        var cards = document.querySelectorAll('div.card[data-gid]');
        for (var i = 0; i < cards.length; i++) {
            var card = cards[i];
            if (!card.querySelector('.card-btn')) continue;
            var cover = card.querySelector('.bg-raised') || card;
            var button = cartButton(cover, 'noveldb-cart-next');
            // The compact layout prints the title on a strip across the bottom of the cover.
            var strip = cover.querySelector('.bg-gradient-to-t');
            var bottom = strip ? (strip.offsetHeight + 4) + 'px' : '';
            if (button.style.bottom !== bottom) button.style.bottom = bottom;
            var url = nextLinks[nextKey(card)] || '';
            if (button.dataset.url !== url) button.dataset.url = url;
            paint(button);
        }
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
    var mode = {cartLinks: false};
    var DOWNLOAD_LABEL = '🔗 Download';
    var renamed = new Map();
    function shownText(anchor) {
        var nodes = [], walker = document.createTreeWalker(anchor, NodeFilter.SHOW_TEXT), node;
        while ((node = walker.nextNode())) if (node.nodeValue.trim()) nodes.push(node);
        return nodes;
    }
    function renameLinks() {
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
    function decorate() {
        renameLinks();
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
    var queued = false;
    function schedule() {
        if (queued) return;
        queued = true;
        requestAnimationFrame(function () { queued = false; decorate(); });
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
        if (innerHeight > 100) document.documentElement.style.setProperty('--noveldb-vh', innerHeight + 'px');
    }
    addEventListener('resize', viewport);
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
        'html.noveldb-peek #app > aside,html.noveldb-peek button[aria-label="Close details"]' +
        '{visibility:hidden!important;animation:none!important;transition:none!important}' +
        '.novel-details{max-height:calc(var(--noveldb-vh,100vh) - 1rem)!important}' +
        '.novel-details-image img{max-height:min(calc(var(--noveldb-vh,100vh) * .2),140px)!important}' +
        '.novel-details-content [data-field=synopsis]{max-height:min(calc(var(--noveldb-vh,100vh) * .25),150px)!important}' +
        '.site-settings-dialog{max-height:min(calc(var(--noveldb-vh,100vh) * .9),560px)!important}' +
        '.mobile-controls-floating{max-height:calc(var(--noveldb-vh,100vh) * .75)!important}';
    (document.head || document.documentElement).appendChild(style);
    function start() {
        viewport();
        new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true, characterData: true});
        schedule();
    }
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
    window.NovelDBApp = {
        setCart: function (urls) { cart = new Set(urls || []); schedule(); },
        setMode: function (next) { mode = {cartLinks: !!(next && next.cartLinks)}; schedule(); }
    };
    send({type: 'ready'});
})();
"""#
}
