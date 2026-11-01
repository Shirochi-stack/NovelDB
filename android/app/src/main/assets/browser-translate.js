/*
 * Page translation for the Browser tab, injected by the app while Translate is on (as a page starts showing,
 * when it finishes loading, and when a single-page site moves on). It loads Google's website translator into
 * the page, so the real site stays (sign-in, scripts, the app's own buttons), picks the target language
 * through the translator's select box and keeps doing so while the page changes.
 * Some sites load Google's translator themselves (for a menu of their own); two copies on one page break each
 * other, so the page's copy is used when there is one, and the app's copy waits until the page's HTML has
 * been read, so that a copy the page loads is found first.
 * The app polls NpiaTranslate.status():
 *   'idle' (off), 'loading', 'translated', 'same' (the translator is there but changed nothing, as on a page
 *   already in the target language), 'failed' (nothing was translated within 15 seconds; the app offers to
 *   try again), or 'blocked' (the site's security policy refused the translator), in which case the app
 *   offers a translated copy instead. A page that translates later still turns 'translated'.
 * start(lang) is safe to call again on every page load or address change; stop() turns translating off (the
 * app then reloads the page to show the original).
 */
(function () {
    'use strict';
    if (window.NpiaTranslate) return;
    var CALLBACK = '__npiaTranslateInit', HOLDER = 'npia-translate-holder', STYLE = 'npia-translate-style', OURS = 'data-npia-translate';
    var LIMIT = 15000;
    // Where the translator loads from; the site's policy refusing one of these is a real block.
    var TRANSLATOR = /^https?:\/\/(translate\.google\.com|translate\.googleapis\.com|translate-pa\.googleapis\.com)(\/|$)|^https?:\/\/www\.gstatic\.com\/_\/translate_http\//i;
    var state = {on: false, lang: 'en', status: 'idle', since: 0, refused: false};
    var requested = false, retries = 0, retryAt = 0, built = false, libraryAt = 0, picks = 0, pickedAt = 0, timer = 0;

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
    function combos() { return document.querySelectorAll('select.goog-te-combo'); }
    function translated() { return /(^|\s)translated-(ltr|rtl)(\s|$)/.test(document.documentElement.className); }
    function library() { return !!(window.google && google.translate && google.translate.TranslateElement); }
    // A copy of the translator the page loads itself.
    function pageCopy() { return document.querySelector('script[src*="translate_a/element.js"]:not([' + OURS + '])'); }
    // Nothing translated: on a page already in the target language that is expected (an unknown language counts).
    function sameLanguage() {
        var lang = (document.documentElement.lang || '').toLowerCase().split('-')[0];
        return !lang || lang === state.lang.toLowerCase().split('-')[0];
    }

    document.addEventListener('securitypolicyviolation', function (event) {
        // Reports from a report-only policy block nothing, and other Google services (ads, fonts) are not the translator.
        if (event.disposition === 'report' || !TRANSLATOR.test(event.blockedURI || '')) return;
        if (state.on && !translated()) state.refused = true;
    });

    function build() {
        var node = holder();
        if (!node) return;
        built = true;
        try {
            new google.translate.TranslateElement({pageLanguage: 'auto', autoDisplay: false}, HOLDER);
        } catch (_) { /* reported as 'failed' once the time is up */ }
    }
    function load() {
        requested = true;
        window[CALLBACK] = function () { if (!built && !combos().length) build(); };
        try {
            var script = document.createElement('script');
            script.setAttribute(OURS, '');
            script.src = 'https://translate.google.com/translate_a/element.js?cb=' + CALLBACK;
            script.async = true;
            // A network hiccup gets two more tries; a policy block also shows up as a violation (above).
            script.onerror = function () {
                script.remove();
                if (retries < 2) { retries++; requested = false; retryAt = Date.now() + 2000; }
            };
            (document.head || document.documentElement).appendChild(script);
        } catch (_) { /* reported as 'failed' once the time is up */ }
    }
    // Gets one translator onto the page: the page's own, or ours.
    function prepare() {
        if (built || combos().length) return;
        if (library()) {
            if (!libraryAt) libraryAt = Date.now();
            // A site with a language box of its own builds it as the translator arrives; give it a moment.
            if (requested || Date.now() - libraryAt > 1000) build();
            return;
        }
        if (requested || Date.now() < retryAt) return;
        var waited = Date.now() - state.since;
        // The page's copy defines the translator's namespace as soon as it runs; one that never ran is not waited for.
        if (pageCopy() && (!window.google || !google.translate) && waited < 8000) return;
        if (pageCopy() && window.google && google.translate) return;
        // Wait for the HTML to be read, so that a copy the page loads itself is found first.
        if (document.readyState === 'loading' && waited < 6000) return;
        load();
    }
    function apply() {
        var boxes = combos();
        // A pick made before the translator listens is lost, so an untranslated page gets the language again.
        var again = !translated() && picks < 4 && Date.now() - pickedAt > 2500;
        for (var i = 0; i < boxes.length; i++) {
            var select = boxes[i];
            if (select.value === state.lang && !again) continue;
            // The language list fills in after the box appears.
            if (!select.querySelector('option[value="' + state.lang + '"]')) continue;
            select.value = state.lang;
            select.dispatchEvent(new Event('change', {bubbles: true}));
            picks++;
            pickedAt = Date.now();
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
        prepare();
        apply();
        if (translated()) state.status = 'translated';
        else if (state.refused) state.status = 'blocked';
        else if (Date.now() - state.since > LIMIT) state.status = combos().length && sameLanguage() ? 'same' : 'failed';
        else state.status = 'loading';
    }

    window.NpiaTranslate = {
        start: function (lang) {
            if (lang) state.lang = lang;
            state.on = true;
            state.since = Date.now();
            hideBanner();
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
