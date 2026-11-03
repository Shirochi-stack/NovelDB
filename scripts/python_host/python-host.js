/*
 * The mobile apps' Python engines page (see noveldb_mobile.py): Pyodide with the desktop's
 * lightnovel-crawler, FanFicFare and gallery-dl engines, in a hidden page the app keeps open.
 *
 * The page is served by the app (Android: https://appassets.androidplatform.net/noveldb-python/;
 * iOS: noveldb-local://python/):
 *   pyodide/            Pyodide and the packages the engines need (scripts/fetch_python_host.py)
 *   py/                 noveldb_mobile.py and the desktop's engine modules
 *   bundles/<engine>/   each engine's bundle (the app's current one) and its manifest.json
 *   transport?r=...     the app's answer to one request (base64url JSON in, JSON out), synchronous
 *
 * window.NovelDBPython.start()                  -> Promise of {ready, error}
 * window.NovelDBPython.parseBook(engine, url)   -> JSON text: the book, or {error}
 * window.NovelDBPython.parseChapter(engine, j)  -> JSON text: the chapter, or {error}
 */
(function () {
    'use strict';
    if (window.NovelDBPython) return;

    var BASE = location.href.replace(/[^/]*([?#].*)?$/, '');
    var ENGINES = ['lncrawl', 'fanficfare', 'gallerydl'];
    var MODULES = ['noveldb_mobile.py', 'lncrawl_engine.py', 'fanficfare_engine.py', 'gallerydl_engine.py', 'generic.py'];
    var PACKAGES = ['beautifulsoup4', 'soupsieve', 'typing-extensions', 'requests', 'urllib3', 'idna', 'charset-normalizer',
                    'certifi', 'html5lib', 'six', 'webencodings', 'lxml', 'regex', 'pycryptodome', 'brotli',
                    'ssl', 'hashlib', 'sqlite3'];
    // Pure-Python wheels from PyPI next to Pyodide's (scripts/fetch_python_host.py PYPI).
    var WHEELS = ['requests_file-3.0.1-py2.py3-none-any.whl', 'chardet-5.2.0-py3-none-any.whl',
                  'html2text-2025.4.15-py3-none-any.whl'];
    var py = null, starting = null;

    function base64url(text) {
        var bytes = new TextEncoder().encode(text), binary = '';
        for (var i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    // The engines' blocking requests: a synchronous request to the page's own address, answered by the app.
    window.NovelDBSyncTransport = function (json) {
        var request = new XMLHttpRequest();
        request.open('GET', BASE + 'transport?r=' + base64url(json), false);
        try {
            request.send(null);
        } catch (error) {
            return JSON.stringify({error: 'the app did not answer: ' + error});
        }
        return request.status === 200 ? request.responseText : JSON.stringify({error: 'the app answered ' + request.status});
    };

    function bytes(url) {
        return fetch(url).then(function (response) {
            if (!response.ok) throw new Error(url + ': ' + response.status);
            return response.arrayBuffer();
        }).then(function (buffer) { return new Uint8Array(buffer); });
    }
    function text(url) {
        return fetch(url).then(function (response) {
            if (!response.ok) throw new Error(url + ': ' + response.status);
            return response.text();
        });
    }

    function start() {
        if (starting) return starting;
        starting = loadPyodide({indexURL: BASE + 'pyodide/'}).then(function (loaded) {
            py = loaded;
            return py.loadPackage(PACKAGES.concat(WHEELS.map(function (wheel) { return BASE + 'pyodide/' + wheel; })),
                                  {messageCallback: function () {}});
        }).then(function () {
            var FS = py.FS;
            FS.mkdirTree('/app/noveldb/core/engines');
            ['/app/noveldb/__init__.py', '/app/noveldb/core/__init__.py', '/app/noveldb/core/engines/__init__.py']
                .forEach(function (file) { FS.writeFile(file, ''); });
            return Promise.all(MODULES.map(function (name) {
                return text(BASE + 'py/' + name).then(function (source) {
                    FS.writeFile(name === 'noveldb_mobile.py' ? '/app/noveldb_mobile.py' : '/app/noveldb/core/engines/' + name, source);
                });
            })).then(function () {
                return Promise.all(ENGINES.map(function (engine) {
                    return text(BASE + 'bundles/' + engine + '/manifest.json').then(function (manifestText) {
                        var manifest = JSON.parse(manifestText);
                        FS.mkdirTree('/bundles/' + engine);
                        FS.writeFile('/bundles/' + engine + '/manifest.json', manifestText);
                        return bytes(BASE + 'bundles/' + engine + '/' + manifest.file).then(function (data) {
                            FS.writeFile('/bundles/' + engine + '/' + manifest.file, data);
                        });
                    }).catch(function (error) { console.warn('[python] no ' + engine + ' bundle: ' + error); });
                }));
            });
        }).then(function () {
            py.runPython('import sys\nsys.path.insert(0, "/app")\nimport noveldb_mobile\nnoveldb_mobile.start()');
            return {ready: true};
        }).catch(function (error) {
            starting = null;
            return {ready: false, error: String((error && error.message) || error)};
        });
        return starting;
    }

    function call(name, args) {
        if (!py) return JSON.stringify({error: 'the Python engines are not started'});
        try {
            var module = py.pyimport('noveldb_mobile');
            var result = module[name].apply(null, args);
            module.destroy && module.destroy();
            return String(result);
        } catch (error) {
            return JSON.stringify({error: String((error && error.message) || error).slice(-500)});
        }
    }

    window.NovelDBPython = {
        start: start,
        // Diagnostics: what an import or a call says, from Python itself.
        check: function (code) {
            return start().then(function () {
                try { return String(py.runPython(code)); } catch (error) { return String((error && error.message) || error).slice(-1500); }
            });
        },
        knows: function (engine, url) { return py ? call('knows', [engine, url]) === 'True' : false; },
        parseBook: function (engine, url) { return start().then(function () { return call('parse_book', [engine, url]); }); },
        parseChapter: function (engine, chapterJson) {
            return start().then(function () { return call('parse_chapter', [engine, chapterJson]); });
        }
    };
})();
