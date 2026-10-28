/*
 * Page translation for the Browser tab, injected by the app after a page has loaded while Translate is on.
 * It loads Google's website translator into the page, so the real site stays (sign-in, scripts, the app's
 * own buttons), picks the target language through the translator's select box and keeps doing so while the
 * page changes. The app polls NpiaTranslate.status():
 *   'idle' (off), 'loading', 'translated', 'same' (the translator loaded but changed nothing, as on a page
 *   already in the target language), or 'blocked' (the site refused the translator, or it never appeared
 *   within 8 seconds), in which case the app offers a translated copy instead.
 * start(lang) is safe to call again on every page load or address change; stop() turns translating off (the
 * app then reloads the page to show the original).
 */
(function () {
    'use strict';
    if (window.NpiaTranslate) return;
    var CALLBACK = '__npiaTranslateInit', HOLDER = 'npia-translate-holder', STYLE = 'npia-translate-style';
    var LIMIT = 8000;
    var state = {on: false, lang: 'en', status: 'idle', since: 0};
    var requested = false, timer = 0;

    // Google's banner and balloons push the page down or cover it; the translator itself stays.
    function hideBanner() {
        if (document.getElementById(STYLE)) return;
        var style = document.createElement('style');
        style.id = STYLE;
        style.textContent =
            'body{top:0!important}' +
            'body>.skiptranslate,iframe.goog-te-banner-frame,.goog-te-banner-frame,iframe.VIpgJd-ZVi9od-ORHb-OEVmcd,' +
            '.VIpgJd-ZVi9od-ORHb-OEVmcd,.VIpgJd-ZVi9od-aZ2wEe-wOHMyf{display:none!important;visibility:hidden!important;height:0!important}' +
            '#goog-gt-tt,.goog-tooltip,.goog-te-balloon-frame,.VIpgJd-suEOdc,.VIpgJd-yAWNEb-L7lbkb{display:none!important}' +
            '.goog-text-highlight{background:none!important;box-shadow:none!important}' +
            '#' + HOLDER + '{position:fixed!important;left:-9999px!important;top:0!important;width:1px!important;height:1px!important;overflow:hidden!important}';
        (document.head || document.documentElement).appendChild(style);
    }
    function holder() {
        var node = document.getElementById(HOLDER);
        if (!node && document.body) {
            node = document.createElement('div');
            node.id = HOLDER;
            document.body.appendChild(node);
        }
        return node;
    }
    function combo() { return document.querySelector('select.goog-te-combo'); }
    function translated() { return /(^|\s)translated-(ltr|rtl)(\s|$)/.test(document.documentElement.className); }
    function fail() { if (state.on && !translated()) state.status = 'blocked'; }

    function load() {
        if (requested) return;
        requested = true;
        window[CALLBACK] = function () {
            try {
                new google.translate.TranslateElement({pageLanguage: 'auto', autoDisplay: false}, HOLDER);
            } catch (_) { fail(); }
        };
        document.addEventListener('securitypolicyviolation', function (event) {
            if (/google|gstatic/i.test(event.blockedURI || '')) fail();
        });
        try {
            var script = document.createElement('script');
            script.src = 'https://translate.google.com/translate_a/element.js?cb=' + CALLBACK;
            script.async = true;
            script.onerror = fail;
            (document.head || document.documentElement).appendChild(script);
        } catch (_) { fail(); }
    }
    function apply() {
        var select = combo();
        if (!select) return;
        if (select.value !== state.lang) {
            select.value = state.lang;
            select.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }
    // The translator remembers its language in a cookie; leave none behind when translating stops.
    function forget() {
        var past = '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/', parts = location.hostname.split('.');
        document.cookie = 'googtrans' + past;
        for (var i = 0; i < parts.length - 1; i++) {
            var domain = parts.slice(i).join('.');
            document.cookie = 'googtrans' + past + '; domain=' + domain;
            document.cookie = 'googtrans' + past + '; domain=.' + domain;
        }
    }
    function tick() {
        if (!state.on) { clearInterval(timer); timer = 0; return; }
        hideBanner();
        if (!document.getElementById(HOLDER)) holder();
        apply();
        if (translated()) state.status = 'translated';
        else if (Date.now() - state.since > LIMIT) state.status = combo() ? 'same' : 'blocked';
    }

    window.NpiaTranslate = {
        start: function (lang) {
            if (lang) state.lang = lang;
            state.on = true;
            state.since = Date.now();
            if (!translated()) state.status = 'loading';
            hideBanner();
            holder();
            load();
            if (!timer) timer = setInterval(tick, 500);
            tick();
        },
        stop: function () {
            state.on = false;
            state.status = 'idle';
            clearInterval(timer);
            timer = 0;
            forget();
        },
        status: function () { return state.status; }
    };
})();
