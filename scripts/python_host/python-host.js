/*
 * The mobile apps' Python engines page: a hidden page the app keeps open (python-core.js says what is
 * served next to it). The engines run in a web worker (python-worker.js): an app's web views share one
 * main thread, and a page busy in Python (a start of ~15 s, every blocking request of a download)
 * froze the Catalog page for as long. Where a worker cannot start (its scripts not served to it), they
 * run in this page as before.
 *
 * window.NovelDBPython.start()                  -> Promise of {ready, error, worker}
 * window.NovelDBPython.parseBook(engine, url)   -> Promise of JSON text: the book, or {error}
 * window.NovelDBPython.parseChapter(engine, j)  -> Promise of JSON text: the chapter, or {error}
 */
(function () {
    'use strict';
    if (window.NovelDBPython) return;

    var worker = null, waiting = {}, next = 0, mode = '', starting = null;

    function ask(message) {
        return new Promise(function (resolve) {
            var id = ++next;
            waiting[id] = resolve;
            worker.postMessage(Object.assign({id: id}, message));
        });
    }
    function failWaiting(text) {
        Object.keys(waiting).forEach(function (id) {
            var resolve = waiting[id];
            delete waiting[id];
            resolve(JSON.stringify({error: text}));
        });
    }

    // The worker, started: its start's answer, or null when no worker could run.
    function startWorker() {
        return new Promise(function (resolve) {
            try {
                worker = new Worker('python-worker.js');
            } catch (error) {
                resolve(null);
                return;
            }
            worker.onmessage = function (event) {
                var data = event.data || {}, resolve = waiting[data.id];
                delete waiting[data.id];
                if (resolve) resolve(data.result);
            };
            worker.onerror = function (event) {
                if (event && event.preventDefault) event.preventDefault();
                if (mode !== 'worker') { resolve(null); return; }
                failWaiting('the Python worker stopped: ' + ((event && event.message) || 'no reason given'));
            };
            ask({op: 'start'}).then(resolve);
        });
    }

    function inlineCore() {
        if (window.NovelDBPythonCore) return Promise.resolve();
        return new Promise(function (resolve, reject) {
            var script = document.createElement('script');
            script.src = 'python-core.js';
            script.onload = resolve;
            script.onerror = function () { reject(new Error('python-core.js did not load')); };
            document.head.appendChild(script);
        });
    }

    function start() {
        if (starting) return starting;
        starting = startWorker().then(function (state) {
            if (state && state.ready) {
                mode = 'worker';
                return {ready: true, worker: true};
            }
            if (worker) { worker.terminate(); worker = null; }
            failWaiting('the Python worker did not start');
            return inlineCore().then(function () {
                return window.NovelDBPythonCore.start();
            }).then(function (inline) {
                if (inline.ready) mode = 'page';
                return Object.assign({worker: false}, inline, inline.ready || !state || !state.error ? {}
                    : {error: inline.error + ' (in a worker: ' + state.error + ')'});
            });
        }).catch(function (error) {
            return {ready: false, error: String((error && error.message) || error)};
        }).then(function (state) {
            if (!state.ready) starting = null;
            return state;
        });
        return starting;
    }

    function run(name, args) {
        return start().then(function (state) {
            if (!state.ready) return JSON.stringify({error: 'the Python engines did not start: ' + state.error});
            return mode === 'worker' ? ask({op: 'call', name: name, args: args}) : window.NovelDBPythonCore.call(name, args);
        });
    }

    window.NovelDBPython = {
        start: start,
        // Diagnostics: what an import or a call says, from Python itself.
        check: function (code) {
            return start().then(function () {
                return mode === 'worker' ? ask({op: 'check', code: code}) : window.NovelDBPythonCore.check(code);
            });
        },
        knows: function (engine, url) { return run('knows', [engine, url]).then(function (answer) { return answer === 'True'; }); },
        parseBook: function (engine, url) { return run('parse_book', [engine, url]); },
        parseChapter: function (engine, chapterJson) { return run('parse_chapter', [engine, chapterJson]); }
    };
})();
