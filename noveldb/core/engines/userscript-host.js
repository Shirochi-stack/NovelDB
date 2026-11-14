/*
 * The host of a userscript engine (a .user.js file added through "Add custom engine").
 *
 * A userscript is not an engine: it runs in a page, and saves what it finds through its own menu. This file is
 * what the apps put around one so a link can be read with nobody watching, in a page nobody sees:
 *
 * - the GM_ functions the script asks for (values kept for the page's life; requests through the app when the
 *   page itself may not make them),
 * - every file the script saves (a link with `download`, GM_download) kept instead of handed to the browser,
 *   a ZIP opened into the images in it,
 * - the script's own panel reached even when it is in a closed shadow root, to press its "All" and
 *   "download" buttons,
 * - the page scrolled, or paged, to its end first, so a reader has drawn every page.
 *
 * It runs before the script, at the start of the document, in the page's own world (the script's too):
 *
 *   window.__NDB_USERSCRIPT.run(options)  ->  Promise<{files, title, pressed}>
 *
 * Each image goes to the app as it is found: window.__ndbUserscriptFile(json) where the app gave one (the
 * desktop's binding), window.NovelDBUserscript.file(json) on Android, the "ndbUserscript" message handler on
 * iOS; with none of them the images stay in __NDB_USERSCRIPT.files ({name, mime, base64}).
 */
(function () {
    'use strict';
    if (window.__NDB_USERSCRIPT) return;
    var top = true;
    try { top = window === window.top; } catch (e) { top = false; }

    var host = window.__NDB_USERSCRIPT = { files: [], roots: [], delivered: 0, pending: 0, lastFile: 0, values: {}, log: [] };
    function note(text) { host.log.push(String(text)); if (host.log.length > 200) host.log.shift(); }

    // -- the script's panel: a closed shadow root is still the page's, and the buttons in it are pressed below --
    var attachShadow = Element.prototype.attachShadow;
    if (attachShadow) {
        Element.prototype.attachShadow = function () {
            var root = attachShadow.apply(this, arguments);
            try { host.roots.push(root); } catch (e) { /* keep the page working */ }
            return root;
        };
    }

    // -- bytes --
    function toBase64(bytes) {
        var text = '', step = 0x8000;
        for (var i = 0; i < bytes.length; i += step) text += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
        return btoa(text);
    }
    function fromBase64(text) {
        var binary = atob(text || ''), bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        return bytes;
    }
    // What the bytes are, whatever the file was called: an image's kind, "zip", or "".
    function kindOf(bytes) {
        var b = bytes;
        if (b.length < 12) return '';
        if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4E && b[3] === 0x47) return 'image/png';
        if (b[0] === 0xFF && b[1] === 0xD8 && b[2] === 0xFF) return 'image/jpeg';
        if (b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return 'image/gif';
        if (b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 && b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) return 'image/webp';
        if (b[4] === 0x66 && b[5] === 0x74 && b[6] === 0x79 && b[7] === 0x70 && b[8] === 0x61 && b[9] === 0x76 && b[10] === 0x69) return 'image/avif';
        if (b[0] === 0x42 && b[1] === 0x4D) return 'image/bmp';
        if (b[0] === 0x50 && b[1] === 0x4B && (b[2] === 0x03 || b[2] === 0x05) && (b[3] === 0x04 || b[3] === 0x06)) return 'zip';
        return '';
    }

    // -- a ZIP's files, in the order its directory lists them (stored or deflated; nothing else is written by scripts) --
    function inflate(bytes) {
        if (typeof DecompressionStream !== 'function') return Promise.reject(new Error('this browser cannot open a compressed ZIP'));
        var stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
        return new Response(stream).arrayBuffer().then(function (buffer) { return new Uint8Array(buffer); });
    }
    function unzip(bytes) {
        var view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength), end = -1;
        for (var i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
            if (view.getUint32(i, true) === 0x06054b50) { end = i; break; }
        }
        if (end < 0) return Promise.reject(new Error('not a ZIP'));
        var count = view.getUint16(end + 10, true), at = view.getUint32(end + 16, true), entries = [];
        for (var n = 0; n < count && at + 46 <= bytes.length; n++) {
            if (view.getUint32(at, true) !== 0x02014b50) break;
            var method = view.getUint16(at + 10, true), packed = view.getUint32(at + 20, true);
            var nameLength = view.getUint16(at + 28, true), extra = view.getUint16(at + 30, true), comment = view.getUint16(at + 32, true);
            var local = view.getUint32(at + 42, true);
            var name = new TextDecoder().decode(bytes.subarray(at + 46, at + 46 + nameLength));
            at += 46 + nameLength + extra + comment;
            if (!name || name.charAt(name.length - 1) === '/' || local + 30 > bytes.length) continue;
            var start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
            entries.push({ name: name, method: method, data: bytes.subarray(start, start + packed) });
        }
        return entries.reduce(function (done, entry) {
            return done.then(function (files) {
                var read = entry.method === 0 ? Promise.resolve(entry.data)
                    : entry.method === 8 ? inflate(entry.data) : Promise.reject(new Error('unsupported ZIP compression'));
                return read.then(function (data) { files.push({ name: entry.name, data: data }); return files; },
                    function (error) { note('zip entry ' + entry.name + ': ' + error.message); return files; });
            });
        }, Promise.resolve([]));
    }

    // -- an image for the app --
    function deliver(name, bytes, mime) {
        var file = { name: String(name || ''), mime: mime, index: host.delivered++, base64: toBase64(bytes) };
        host.lastFile = Date.now();
        var text = JSON.stringify(file);
        try {
            if (typeof window.__ndbUserscriptFile === 'function') { window.__ndbUserscriptFile(text); return; }
            if (window.NovelDBUserscript && window.NovelDBUserscript.file) { window.NovelDBUserscript.file(text); return; }
            var handlers = window.webkit && window.webkit.messageHandlers;
            if (handlers && handlers.ndbUserscript) { handlers.ndbUserscript.postMessage(text); return; }
        } catch (e) { note('could not hand over ' + file.name + ': ' + e.message); }
        host.files.push(file);
    }
    function keep(name, bytes) {
        var kind = kindOf(bytes);
        if (kind === 'zip') {
            return unzip(bytes).then(function (files) {
                // By name, numbers as numbers (page 2 before page 10): the order the script numbered them in.
                files.sort(function (a, b) { return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }); });
                files.forEach(function (file) {
                    var inner = kindOf(file.data);
                    if (inner && inner !== 'zip') deliver(file.name, file.data, inner);
                });
            });
        }
        if (kind) deliver(name, bytes, kind);
        return Promise.resolve();
    }
    // A file the script saves: read from its address (a blob:, a data: or the site) and kept.
    function save(address, name) {
        host.pending++;
        host.lastFile = Date.now();
        return pageFetch(String(address)).then(function (response) { return response.arrayBuffer(); })
            .then(function (buffer) { return keep(name, new Uint8Array(buffer)); })
            .catch(function (error) { note('could not read ' + name + ': ' + (error && error.message)); })
            .then(function () { host.pending--; host.lastFile = Date.now(); });
    }

    var pageFetch = window.fetch.bind(window);
    var anchorClick = HTMLAnchorElement.prototype.click;
    function saved(anchor) {
        if (!anchor || !anchor.hasAttribute || !anchor.hasAttribute('download') || !anchor.href) return false;
        save(anchor.href, anchor.getAttribute('download') || '');
        return true;
    }
    HTMLAnchorElement.prototype.click = function () {
        if (saved(this)) return undefined;
        return anchorClick.apply(this, arguments);
    };
    var dispatch = EventTarget.prototype.dispatchEvent;
    EventTarget.prototype.dispatchEvent = function (event) {
        if (event && event.type === 'click' && this instanceof HTMLAnchorElement && saved(this)) return true;
        return dispatch.apply(this, arguments);
    };
    document.addEventListener('click', function (event) {
        var anchor = event.target && event.target.closest ? event.target.closest('a[download]') : null;
        if (anchor && event.isTrusted === false && saved(anchor)) event.preventDefault();
    }, true);

    // -- the GM_ functions --
    function headerText(headers) {
        var lines = [];
        if (headers && headers.forEach) headers.forEach(function (value, key) { lines.push(key + ': ' + value); });
        else Object.keys(headers || {}).forEach(function (key) { lines.push(key.toLowerCase() + ': ' + headers[key]); });
        return lines.join('\r\n');
    }
    function shaped(details, status, statusText, finalUrl, headers, bytes) {
        var type = String(details.responseType || '').toLowerCase(), mime = /content-type:\s*([^\r\n;]+)/i.exec(headers);
        var response = { status: status, statusText: statusText || '', readyState: 4, finalUrl: finalUrl, responseHeaders: headers, context: details.context };
        var text = null;
        function asText() { if (text === null) text = new TextDecoder().decode(bytes); return text; }
        if (type === 'blob') response.response = new Blob([bytes], { type: mime ? mime[1].trim() : '' });
        else if (type === 'arraybuffer') response.response = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
        else if (type === 'json') { try { response.response = JSON.parse(asText()); } catch (e) { response.response = null; } }
        else { response.response = asText(); }
        if (type !== 'blob' && type !== 'arraybuffer') response.responseText = asText();
        return response;
    }
    // Through the app: its own requests carry the site's cookies and are not held to the page's cross-origin rules.
    function appRequest(details, url) {
        var send = window.__npia_gm_xmlhttp_request || (window.NovelDBUserscript && window.NovelDBUserscript.request ? function (payload) {
            return new Promise(function (resolve) {
                var id = 'r' + (++appRequest.count);
                appRequest.waiting[id] = resolve;
                window.NovelDBUserscript.request(id, JSON.stringify(payload));
            });
        } : (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.ndbUserscriptRequest ? function (payload) {
            return window.webkit.messageHandlers.ndbUserscriptRequest.postMessage(JSON.stringify(payload)).then(function (reply) {
                return typeof reply === 'string' ? JSON.parse(reply) : reply;
            });
        } : null));
        if (!send) return null;
        return Promise.resolve(send({
            url: url, method: details.method || 'GET', headers: details.headers || {}, data: typeof details.data === 'string' ? details.data : null,
            responseType: 'arraybuffer', timeout: details.timeout || 30000, cookie: details.cookie || ''
        })).then(function (reply) {
            if (typeof reply === 'string') reply = JSON.parse(reply);
            if (!reply || reply.error) throw new Error((reply && reply.error) || 'empty response');
            var bytes = reply.responseBase64 ? fromBase64(reply.responseBase64) : new TextEncoder().encode(reply.responseText || '');
            var headers = typeof reply.responseHeaders === 'string' ? reply.responseHeaders : headerText(reply.responseHeaders || reply.headers);
            return shaped(details, reply.status || 200, reply.statusText, reply.finalUrl || reply.url || url, headers, bytes);
        });
    }
    appRequest.count = 0;
    appRequest.waiting = {};
    host.reply = function (id, text) {
        var resolve = appRequest.waiting[id];
        delete appRequest.waiting[id];
        if (resolve) { try { resolve(JSON.parse(text)); } catch (e) { resolve({ error: 'unreadable reply' }); } }
    };
    function pageRequest(details, url) {
        var headers = {};
        Object.keys(details.headers || {}).forEach(function (key) {
            // The page may not set these itself; the browser sends its own.
            if (!/^(referer|cookie|host|origin|user-agent|pragma|cache-control|sec-|accept-encoding|connection|content-length)/i.test(key)) headers[key] = details.headers[key];
        });
        return pageFetch(url, {
            method: details.method || 'GET', headers: headers, body: details.data == null ? undefined : details.data,
            credentials: details.anonymous ? 'omit' : 'include', cache: 'force-cache'
        }).then(function (answer) {
            return answer.arrayBuffer().then(function (buffer) {
                return shaped(details, answer.status, answer.statusText, answer.url || url, headerText(answer.headers), new Uint8Array(buffer));
            });
        });
    }
    function GM_xmlhttpRequest(details) {
        details = details || {};
        var url, aborted = false;
        try { url = new URL(details.url, location.href).href; } catch (e) { url = String(details.url || ''); }
        var local = /^(blob:|data:)/i.test(url), same = url.indexOf(location.origin + '/') === 0;
        // The page first where it may ask (its own site, a blob); the app where it may not, or where the page was refused.
        var first = local || same ? pageRequest(details, url) : (appRequest(details, url) || pageRequest(details, url));
        first.catch(function (error) {
            var other = local ? null : same ? appRequest(details, url) : pageRequest(details, url);
            if (!other) throw error;
            return other;
        }).then(function (response) {
            if (aborted) return;
            if (details.onreadystatechange) { try { details.onreadystatechange(response); } catch (e) { /* the script's */ }
            }
            if (details.onload) details.onload(response);
            if (details.onloadend) details.onloadend(response);
        }, function (error) {
            if (aborted) return;
            var failure = { error: String((error && error.message) || error), status: 0, readyState: 4, finalUrl: url, context: details.context };
            if (details.onerror) details.onerror(failure);
            if (details.onloadend) details.onloadend(failure);
        });
        return { abort: function () { aborted = true; if (details.onabort) details.onabort(); } };
    }
    var gm = {
        GM_getValue: function (key, fallback) { return Object.prototype.hasOwnProperty.call(host.values, key) ? host.values[key] : fallback; },
        GM_setValue: function (key, value) { host.values[key] = value; },
        GM_deleteValue: function (key) { delete host.values[key]; },
        GM_listValues: function () { return Object.keys(host.values); },
        GM_xmlhttpRequest: GM_xmlhttpRequest,
        GM_download: function (details, name) {
            var url = details && typeof details === 'object' ? details.url : details;
            var file = details && typeof details === 'object' ? details.name : name;
            var done = save(url, file || '');
            if (details && typeof details === 'object' && details.onload) done.then(details.onload);
            return { abort: function () {} };
        },
        GM_addStyle: function (css) {
            var style = document.createElement('style');
            style.textContent = css;
            (document.head || document.documentElement).appendChild(style);
            return style;
        },
        GM_registerMenuCommand: function (name, run) { host.commands.push({ name: String(name), run: run }); return host.commands.length; },
        GM_unregisterMenuCommand: function () {},
        GM_notification: function () {},
        GM_openInTab: function () { return { close: function () {} }; },
        GM_setClipboard: function () {},
        GM_log: function () { note(Array.prototype.join.call(arguments, ' ')); },
        GM_getResourceText: function () { return ''; },
        GM_getResourceURL: function () { return ''; },
        unsafeWindow: window
    };
    host.commands = [];
    host.gm = gm;
    // A script written for the GM. object: the same functions, answering with promises.
    host.gm4 = {
        getValue: function (key, fallback) { return Promise.resolve(gm.GM_getValue(key, fallback)); },
        setValue: function (key, value) { gm.GM_setValue(key, value); return Promise.resolve(); },
        deleteValue: function (key) { gm.GM_deleteValue(key); return Promise.resolve(); },
        listValues: function () { return Promise.resolve(gm.GM_listValues()); },
        xmlHttpRequest: GM_xmlhttpRequest, download: gm.GM_download, addStyle: gm.GM_addStyle,
        registerMenuCommand: gm.GM_registerMenuCommand, notification: gm.GM_notification, openInTab: gm.GM_openInTab,
        setClipboard: gm.GM_setClipboard
    };

    /** Runs the script's text with its GM_ functions (the apps call this right after this file, same document). */
    host.start = function (source, meta) {
        host.meta = meta || {};
        var info = { script: host.meta, scriptHandler: 'NovelDB', version: '1' };
        host.gm.GM_info = info;
        host.gm4.info = info;
        var names = Object.keys(gm).concat(['GM']);
        var values = names.map(function (name) { return name === 'GM' ? host.gm4 : gm[name]; });
        function go() {
            try {
                Function.apply(null, names.concat([source + '\n//# sourceURL=noveldb-userscript.user.js'])).apply(window, values);
            } catch (error) {
                note('the script stopped: ' + (error && error.message));
                host.failed = String((error && error.message) || error);
            }
        }
        // A script that runs at the document's start still expects the document's root to be there (a userscript
        // manager starts it then). This file can arrive before it, so the script waits for it: still ahead of
        // the page's own scripts.
        if (document.documentElement) { go(); return; }
        var seen = new MutationObserver(function () {
            if (!document.documentElement) return;
            seen.disconnect();
            go();
        });
        seen.observe(document, { childList: true });
    };

    // -- the page read to its end, then the script's own buttons pressed --
    function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }
    function panelSize() {
        var total = 0;
        host.roots.forEach(function (root) { try { total += root.querySelectorAll('*').length; } catch (e) { /* gone */ } });
        return total;
    }
    function drawn() {
        var total = document.images.length;
        Array.prototype.forEach.call(document.querySelectorAll('canvas'), function (canvas) { total += canvas.width * canvas.height > 0 ? 1 : 0; });
        return total + panelSize();
    }
    function scroller() {
        var best = document.scrollingElement || document.documentElement, most = best.scrollHeight - best.clientHeight;
        Array.prototype.forEach.call(document.querySelectorAll('div, main, section, article'), function (node) {
            var room = node.scrollHeight - node.clientHeight;
            if (room > most + 200 && node.clientHeight > window.innerHeight / 2) {
                var style = getComputedStyle(node);
                if (/(auto|scroll)/.test(style.overflowY)) { best = node; most = room; }
            }
        });
        return best;
    }
    function scrollThrough(limit) {
        // The end is reached when nothing has moved, grown or been drawn for a while: a slow site draws a page
        // seconds after it is scrolled to, and the page's first pictures may not be in yet.
        var deadline = Date.now() + limit, still = 0, quiet = Math.max(4, Math.round((arguments[1] || 3500) / 350));
        function step() {
            if (Date.now() > deadline) return Promise.resolve();
            var node = scroller(), before = node.scrollTop, height = node.scrollHeight, shown = drawn();
            node.scrollTop = before + Math.max(200, Math.round(window.innerHeight * 0.8));
            window.dispatchEvent(new Event('scroll'));
            return wait(350).then(function () {
                var moved = node.scrollTop > before + 1, grew = node.scrollHeight > height + 1, drew = drawn() !== shown;
                still = moved || grew || drew ? 0 : still + 1;
                return still >= quiet ? undefined : step();
            });
        }
        return step();
    }
    function press(key, code) {
        var targets = [document.activeElement, document.body, document, window], seen = [];
        targets.forEach(function (target) {
            if (!target || seen.indexOf(target) >= 0) return;
            seen.push(target);
            ['keydown', 'keyup'].forEach(function (type) {
                try { target.dispatchEvent(new KeyboardEvent(type, { key: key, code: key, keyCode: code, which: code, bubbles: true, cancelable: true })); } catch (e) { /* next */ }
            });
        });
    }
    // A reader that turns pages (nothing to scroll): the keys that turn them, while new pages keep being drawn.
    function pageThrough(limit) {
        var deadline = Date.now() + limit, keys = [['ArrowLeft', 37], ['ArrowRight', 39], ['PageDown', 34], [' ', 32]];
        function turn(key, quiet, turned) {
            if (Date.now() > deadline || quiet >= 5 || turned >= 600) return Promise.resolve(turned);
            var before = drawn();
            press(key[0], key[1]);
            return wait(450).then(function () { return turn(key, drawn() > before ? 0 : quiet + 1, turned + 1); });
        }
        return keys.reduce(function (done, key) {
            return done.then(function (found) {
                if (found) return found;
                var before = drawn();
                return turn(key, 0, 0).then(function () { return drawn() > before + 1; });
            });
        }, Promise.resolve(false));
    }
    function buttons() {
        var found = [];
        host.roots.concat([document]).forEach(function (root) {
            try {
                Array.prototype.forEach.call(root.querySelectorAll('button, input[type=button], [role=button], a'), function (node) { found.push(node); });
            } catch (e) { /* gone */ }
        });
        return found;
    }
    function label(node) { return String(node.value || node.textContent || '').replace(/\s+/g, ' ').trim(); }
    /*
     * What is known of a script's own panel, by the script's name. Without an entry, the panel's "All" and
     * "download" are pressed.
     *
     * NutAID lists what it caught by where it caught it, one letter each, and has a button per letter: a page
     * it put back together from a reader's scrambled pieces (e), a page drawn on a canvas (c, d, p), a picture
     * the page made (b, u), a picture the page loaded (i). The same page is often caught more than one way (the
     * pieces as loaded, and the page as drawn), so one kind is picked, the nearest to what was shown, not "All".
     */
    var PANELS = [
        { script: /nutaid|nut'?s all image/i, open: 'details', rows: 'tr', name: 2, kinds: ['e', 'c', 'd', 'p', 'b', 'u', 'i'] }
    ];
    function panelOf() {
        var name = String((host.meta && host.meta.name) || '');
        return PANELS.filter(function (panel) { return panel.script.test(name); })[0] || null;
    }
    function pressButtons(options) {
        var panel = panelOf(), pressed = [];
        if (panel && panel.open) {
            // A closed panel is not laid out, and the script reads its rows' names as they are shown.
            host.roots.forEach(function (root) {
                try { Array.prototype.forEach.call(root.querySelectorAll(panel.open), function (node) { node.open = true; }); } catch (e) { /* gone */ }
            });
        }
        return wait(panel ? 400 : 0).then(function () {
            var all = buttons(), scripted = host.roots.length > 0;
            // Only the script's own panel when it has one: a page's "Download" link is not the script's.
            if (scripted) all = all.filter(function (node) { return node.getRootNode && node.getRootNode() !== document; });
            var pick = options.select ? new RegExp(options.select, 'i') : /^(all|select all|check all)$/i;
            if (panel && panel.kinds && !options.select) {
                var caught = {};
                host.roots.forEach(function (root) {
                    try {
                        Array.prototype.forEach.call(root.querySelectorAll(panel.rows), function (row) {
                            var cell = row.children[panel.name], text = cell ? String(cell.textContent || '').trim() : '';
                            if (text) caught[text.charAt(0)] = true;
                        });
                    } catch (e) { /* gone */ }
                });
                var kind = panel.kinds.filter(function (letter) { return caught[letter]; })[0];
                if (kind) pick = new RegExp('^' + kind + '$');
            }
            var go = options.download ? new RegExp(options.download, 'i') : /^(download|download all|download selected|save all|bulk download)$/i;
            var select = all.filter(function (node) { return pick.test(label(node)); })[0];
            var download = all.filter(function (node) { return go.test(label(node)); })[0];
            if (select) { select.click(); pressed.push(label(select)); }
            return wait(300).then(function () {
                if (download) { download.click(); pressed.push(label(download)); }
                return pressed;
            });
        });
    }
    function settled(limit, quiet, patience) {
        var started = Date.now(), deadline = started + limit;
        function check() {
            if (Date.now() > deadline) return Promise.resolve();
            if (host.pending === 0 && host.delivered > 0 && Date.now() - host.lastFile > quiet) return Promise.resolve();
            // Nothing saved and nothing on its way: the script found nothing here.
            if (host.pending === 0 && host.delivered === 0 && Date.now() - Math.max(started, host.lastFile) > patience) return Promise.resolve();
            return wait(400).then(check);
        }
        return check();
    }
    // The script saved nothing (a page of plain pictures gives a capturing script nothing to catch): the page's
    // own pictures, the large ones, in the order the page has them.
    function pagePictures() {
        var seen = {}, list = [];
        Array.prototype.forEach.call(document.images, function (image) {
            var address = image.currentSrc || image.src;
            if (!address || seen[address] || image.naturalWidth < 200 || image.naturalHeight < 200) return;
            seen[address] = true;
            list.push(address);
        });
        return list.reduce(function (done, address, index) {
            return done.then(function () {
                return new Promise(function (resolve) {
                    GM_xmlhttpRequest({
                        url: address, responseType: 'arraybuffer', headers: { Referer: location.href },
                        onload: function (response) {
                            try { keep('page_' + String(index + 1).padStart(4, '0'), new Uint8Array(response.response)); } catch (e) { note(e.message); }
                            resolve();
                        },
                        onerror: function () { resolve(); }
                    });
                });
            });
        }, Promise.resolve());
    }

    /*
     * For an app that asks in short calls (the phones' web views answer a script within a minute, and a page
     * can take longer to read): begin() starts the reading, take() hands over the images found since the last
     * call, a few megabytes at a time, with `done` once the reading has ended and none are left.
     */
    host.begin = function (options) {
        if (host.reading) return true;
        host.reading = true;
        host.done = null;
        host.run(options).then(function (result) { host.done = result; }, function (error) {
            host.done = { files: host.delivered, title: document.title, pressed: [], failed: String((error && error.message) || error) };
        });
        return true;
    };
    host.take = function (budget) {
        var files = [], size = 0;
        budget = budget || 3000000;
        while (host.files.length && (!files.length || size + host.files[0].base64.length <= budget)) {
            size += host.files[0].base64.length;
            files.push(host.files.shift());
        }
        return { files: files, done: host.files.length ? null : host.done };
    };

    host.run = function (options) {
        options = options || {};
        if (!top) return Promise.resolve({ files: 0, title: document.title, pressed: [] });
        var pressed = [];
        var loaded = document.readyState === 'complete' ? Promise.resolve() : new Promise(function (resolve) {
            window.addEventListener('load', resolve, { once: true });
            setTimeout(resolve, 20000);
        });
        return loaded.then(function () { return wait(options.settle || 1500); })
            .then(function () { return scrollThrough(options.scrollLimit || 90000, options.quiet); })
            .then(function () {
                var node = scroller();
                return node.scrollHeight - node.clientHeight < 200 ? pageThrough(options.pageLimit || 120000) : false;
            })
            .then(function () { return wait(800); })
            .then(function () { return host.delivered ? [] : pressButtons(options); })
            .then(function (names) {
                pressed = names;
                // A script gathers a whole chapter before it saves its ZIP: minutes, for a long one. With nothing
                // pressed there is nothing to wait for but a script that saves by itself.
                return settled(options.saveLimit || 300000, 2500, names.length ? options.patience || 120000 : options.idle || 6000);
            })
            .then(function () { return host.delivered ? undefined : pagePictures(); })
            .then(function () {
                return { files: host.delivered, title: document.title, pressed: pressed, failed: host.failed || '', log: host.log.slice(-20),
                    canvases: document.querySelectorAll('canvas').length, pictures: document.images.length };
            });
    };
})();
