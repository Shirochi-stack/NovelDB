/*
 * NovelDB app bridge for the catalog website, injected by the mobile app.
 * - Tapping a card opens View Details instead of the novel page.
 * - "Open novel" and other outside links go to the app (Browser tab).
 * - A link-cart button on each card adds or removes its URL from the app's cart.
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
    function decorate() {
        var areas = document.querySelectorAll('.novel-card .card-cover-area');
        for (var i = 0; i < areas.length; i++) {
            var area = areas[i], card = area.closest('.novel-card'), url = cardUrl(card);
            if (!url) continue;
            var button = area.querySelector('.noveldb-cart');
            if (!button) {
                button = document.createElement('button');
                button.type = 'button';
                button.className = 'noveldb-cart';
                button.textContent = '🔗';
                area.appendChild(button);
            }
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
    function external(anchor) {
        if (!anchor || !anchor.href || anchor.hasAttribute('download')) return false;
        if (!/^https?:/i.test(anchor.href)) return false;
        try { return new URL(anchor.href).origin !== location.origin; } catch (_) { return false; }
    }
    document.addEventListener('click', function (event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var cartButton = target.closest('.noveldb-cart');
        if (cartButton) {
            event.preventDefault();
            event.stopPropagation();
            var card = cartButton.closest('.novel-card');
            var url = cartButton.dataset.url || cardUrl(card);
            var titleEl = card && card.querySelector('.card-title');
            var adding = !cart.has(url);
            if (adding) cart.add(url); else cart.delete(url);
            paint(cartButton);
            send({type: adding ? 'cartAdd' : 'cartRemove', url: url,
                  title: titleEl ? titleEl.textContent.trim() : '', source: card ? card.dataset.source || '' : ''});
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
            send({type: 'open', url: anchor.href, title: (anchor.textContent || '').trim().slice(0, 200)});
        }
    }, true);
    function viewport() {
        if (innerHeight > 100) document.documentElement.style.setProperty('--noveldb-vh', innerHeight + 'px');
    }
    addEventListener('resize', viewport);
    var style = document.createElement('style');
    style.textContent =
        '.noveldb-cart{position:absolute;left:.4rem;bottom:.4rem;z-index:2;display:grid;place-items:center;width:32px;height:32px;' +
        'padding:0;border:0;border-radius:.4rem;background:rgba(0,0,0,.55);font-size:17px;line-height:1;cursor:pointer;' +
        'filter:grayscale(1);opacity:.8;-webkit-tap-highlight-color:transparent}' +
        '.noveldb-cart[aria-pressed="true"]{background:#E18F98;filter:none;opacity:1;box-shadow:0 0 0 2px #fff}' +
        '.novel-details{max-height:calc(var(--noveldb-vh,100vh) - 1rem)!important}' +
        '.novel-details-image img{max-height:min(calc(var(--noveldb-vh,100vh) * .2),140px)!important}' +
        '.novel-details-content [data-field=synopsis]{max-height:min(calc(var(--noveldb-vh,100vh) * .25),150px)!important}' +
        '.site-settings-dialog{max-height:min(calc(var(--noveldb-vh,100vh) * .9),560px)!important}' +
        '.mobile-controls-floating{max-height:calc(var(--noveldb-vh,100vh) * .75)!important}';
    (document.head || document.documentElement).appendChild(style);
    function start() {
        viewport();
        new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
        schedule();
    }
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
    window.NovelDBApp = {
        setCart: function (urls) { cart = new Set(urls || []); schedule(); }
    };
    send({type: 'ready'});
})();
