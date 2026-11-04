/*
 * The Python engines' web worker (python-core.js): the page (python-host.js) posts
 * {id, op: 'start' | 'call' | 'check', name, args, code} and hears {id, result}.
 */
importScripts('python-core.js');

self.onmessage = function (event) {
    var message = event.data || {};
    var answer = message.op === 'start' ? NovelDBPythonCore.start()
        : message.op === 'check' ? NovelDBPythonCore.check(message.code)
        : NovelDBPythonCore.start().then(function () { return NovelDBPythonCore.call(message.name, message.args || []); });
    Promise.resolve(answer).then(function (result) {
        self.postMessage({id: message.id, result: result});
    }, function (error) {
        self.postMessage({id: message.id, result: JSON.stringify({error: String((error && error.message) || error)})});
    });
};
