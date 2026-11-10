"""Benchmark the installed Android catalogue through its forwarded WebView DevTools endpoint.

Open Generic search with All selected, then forward the app's webview_devtools_remote_<pid>
socket to localhost port 9223. This checks real packaged assets with online queries stalled.
Each run resets the catalogue search and Top cache.
"""
import argparse
import base64
import json
from pathlib import Path
import time

import requests
import websocket

try:
    from .source_names import dumps
except ImportError:
    from source_names import dumps


class DevTools:
    def __init__(self, address, url_contains="appassets.androidplatform.net"):
        targets = requests.get(address + "/json", timeout=10).json()
        target = next(target for target in targets if url_contains in target.get("url", ""))
        self.socket = websocket.create_connection(target["webSocketDebuggerUrl"], suppress_origin=True, timeout=20)
        self.sequence = 0

    def command(self, method, params=None):
        self.sequence += 1
        self.socket.send(json.dumps({"id": self.sequence, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.socket.recv())
            if reply.get("id") == self.sequence:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply.get("result", {})

    def evaluate(self, expression):
        reply = self.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in reply:
            raise RuntimeError(reply["exceptionDetails"])
        return reply.get("result", {}).get("value")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devtools", default="http://127.0.0.1:9223")
    parser.add_argument("--query", default="mesugaki")
    parser.add_argument("--expected-first", default="Mesugaki Tank Enters The Academy")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--max-ms", type=float, default=1000)
    parser.add_argument("--minimum", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("artifacts/catalog-instant-check.json"))
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.repeat < 1 or not 1 <= args.minimum <= 30 or args.max_ms <= 0 or not args.query.strip():
        parser.error("Use a nonempty query, positive repeat/time limits and 1-30 required results")
    tools = DevTools(args.devtools.rstrip("/"))
    results = []
    try:
        deadline = time.monotonic() + 30
        while not tools.evaluate("!!(window.NovelDBGeneric&&document.getElementById('query'))"):
            if time.monotonic() >= deadline:
                raise AssertionError("Open the Generic catalogue before running the benchmark")
            time.sleep(.05)
        for iteration in range(args.repeat):
            tools.evaluate("document.getElementById('query').value='';document.getElementById('query').dispatchEvent(new Event('input',{bubbles:true}))")
            time.sleep(.2)
            tools.evaluate("localStorage.removeItem('noveldb.generic.search.v2');Object.keys(localStorage).filter(key=>key.startsWith('noveldb.generic.top.')).forEach(key=>localStorage.removeItem(key))")
            origin = tools.evaluate("performance.timeOrigin")
            started = time.monotonic()
            tools.command("Page.reload", {"ignoreCache": True})
            deadline = time.monotonic() + 30
            ready = False
            while time.monotonic() < deadline:
                try:
                    ready = tools.evaluate(f"performance.timeOrigin!=={origin}&&!!window.NovelDBGeneric&&document.querySelectorAll('#grid .card .title').length>=12&&!!window.NovelDBInstant&&NovelDBInstant.ready()")
                    if ready:
                        break
                except RuntimeError:
                    pass
                time.sleep(.05)
            if not ready:
                raise AssertionError("Initial catalogue did not load")
            catalogue_ready_ms = (time.monotonic() - started) * 1000
            tools.evaluate("""window.__instantTransport=window.NovelDBTransport;
                window.NovelDBTransport=()=>new Promise(()=>{});
                window.__instantProbe={};
                window.__instantObserver=new MutationObserver(()=>{
                    const result=window.__instantProbe;
                    if(!result.started||result.ms!=null)return;
                    const titles=Array.from(document.querySelectorAll('#grid .card .title')).map(node=>node.textContent);
                    if(!document.getElementById('heading').textContent.includes(result.query))return;
                    if(result.first_ms==null&&titles.length>=1){result.first_ms=performance.now()-result.started;result.first_titles=titles;}
                    if(titles.length>=result.minimum){
                        result.ms=performance.now()-result.started;result.titles=titles;
                    }
                });
                __instantObserver.observe(document.getElementById('grid'),{childList:true,subtree:true});
                window.__instantInput=event=>{if(event.target.value===__instantProbe.query)__instantProbe.started=performance.now()};
                document.getElementById('query').addEventListener('input',__instantInput,true);
                document.getElementById('query').focus();""")
            tools.evaluate(f"Object.assign(__instantProbe,{{query:{json.dumps(args.query)},minimum:{args.minimum}}})")
            for character in args.query:
                tools.command("Input.insertText", {"text": character})
                time.sleep(.025)
            deadline = time.monotonic() + 10
            result = {}
            while time.monotonic() < deadline:
                result = tools.evaluate("window.__instantProbe")
                if result.get("ms") is not None:
                    break
                time.sleep(.02)
            result.update(iteration=iteration + 1, catalogue_ready_ms=catalogue_ready_ms)
            results.append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(dumps({"platform": "installed Android WebView", "cold_page_context": True,
                                         "online_keyword_requests": "stalled", "results": results}, indent=2), encoding="utf-8")
            assert result.get("first_ms", float("inf")) < 60, result
            assert result.get("ms", float("inf")) < args.max_ms, result
            assert args.minimum <= len(result.get("titles", [])) <= 30, result
            assert result["titles"][0] == args.expected_first, result
            print(dumps({"iteration": iteration + 1, "first_ms": result["first_ms"], "ms": result["ms"], "catalogue_ready_ms": catalogue_ready_ms,
                         "count": len(result["titles"]), "first": result["titles"][0]}), flush=True)
            tools.evaluate("__instantObserver.disconnect();document.getElementById('query').removeEventListener('input',__instantInput,true);window.NovelDBTransport=__instantTransport")
        if args.screenshot:
            picture = tools.command("Page.captureScreenshot", {"format": "png"})
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            args.screenshot.write_bytes(base64.b64decode(picture["data"]))
    finally:
        try:
            tools.evaluate("window.__instantObserver?.disconnect();if(window.__instantInput)document.getElementById('query').removeEventListener('input',__instantInput,true);if(window.__instantTransport)window.NovelDBTransport=__instantTransport;document.getElementById('stop')?.click()")
        except (RuntimeError, websocket.WebSocketException):
            pass
        tools.socket.close()


if __name__ == "__main__":
    main()
