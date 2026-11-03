/*
 * The mobile apps' half of the generic search page's link to the app (see catalog-app.js):
 * - window.NovelDBTransport(details): Android's NovelDBCatalogTransport.request(id, json), answered
 *   through __noveldbTransportReply(id, json); iOS's noveldbCatalogTransport message handler, which
 *   replies directly.
 * - window.NovelDBNative.postMessage(json): the app's message channel (Android's NovelDBAndroid,
 *   iOS's noveldb handler), which the catalog bridge uses too.
 */
(function () {
    'use strict';
    if (window.NovelDBTransport) return;
    var pending = {}, next = 0;

    function handler(name) {
        return window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers[name];
    }
    window.__noveldbTransportReply = function (id, text) {
        var resolve = pending[id];
        delete pending[id];
        if (!resolve) return;
        try { resolve(JSON.parse(text)); } catch (e) { resolve({error: 'The app sent an unreadable reply'}); }
    };
    window.NovelDBTransport = function (details) {
        var text = JSON.stringify(details || {});
        if (window.NovelDBCatalogTransport) {
            return new Promise(function (resolve) {
                var id = String(++next);
                pending[id] = resolve;
                window.NovelDBCatalogTransport.request(id, text);
            });
        }
        var ios = handler('noveldbCatalogTransport');
        if (ios) {
            return ios.postMessage(text).then(function (reply) {
                return typeof reply === 'string' ? JSON.parse(reply) : reply;
            }, function (error) { return {error: String((error && error.message) || error)}; });
        }
        return Promise.resolve({error: 'This page needs the NovelDB app'});
    };
    window.NovelDBNative = {postMessage: function (text) {
        if (window.NovelDBAndroid) window.NovelDBAndroid.postMessage(text);
        else if (handler('noveldb')) handler('noveldb').postMessage(text);
    }};
})();
