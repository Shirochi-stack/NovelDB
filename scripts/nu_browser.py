"""Read NU documents in installed Chrome, including its interactive verification.

Keep one isolated browser session. Do not attach Playwright or enable Runtime
while the site's verification runs; inspect the finished document through DOM.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


@contextmanager
def connection(target):
    import websocket

    ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5)
    number = 0

    def call(method, params=None):
        nonlocal number
        number += 1
        ws.send(json.dumps({"id": number, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 10
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"NU browser command timed out: {method}")
            ws.settimeout(min(5, remaining))
            reply = json.loads(ws.recv())
            if reply.get("id") == number:
                if "error" in reply:
                    raise RuntimeError(str(reply["error"]))
                return reply.get("result", {})

    try:
        yield call
    finally:
        ws.close()


def checkbox(node):
    attributes = node.get("attributes", [])
    attrs = dict(zip(attributes[::2], attributes[1::2]))
    if node.get("nodeName") == "INPUT" and attrs.get("type") == "checkbox":
        return node
    for child in node.get("children", []) + node.get("shadowRoots", []):
        found = checkbox(child)
        if found:
            return found
    return None


def click_verification(target):
    """Click only a checkbox actually present in the challenge frame's DOM."""
    with connection(target) as call:
        root = call("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
        node = checkbox(root)
        if not node:
            return False
        model = call("DOM.getBoxModel", {"nodeId": node["nodeId"]})["model"]
        if model["width"] <= 0 or model["height"] <= 0:
            return False
        quad = model["border"]
        x, y = sum(quad[::2]) / 4, sum(quad[1::2]) / 4
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1,
            })
        return True


def document(target):
    with connection(target) as call:
        root = call("DOM.getDocument", {"depth": 0})["root"]
        return call("DOM.getOuterHTML", {"nodeId": root["nodeId"]})["outerHTML"]


class Browser:
    def __init__(self):
        from external_scraper import ExternalScraper

        self.launcher = ExternalScraper(logger=lambda message: print(message, flush=True))
        directory = Path(__file__).resolve().parents[1] / ".cache"
        directory.mkdir(exist_ok=True)
        self.profile = tempfile.TemporaryDirectory(prefix="nu-chrome-", dir=directory,
                                                   ignore_cleanup_errors=True)
        self.process = None
        self.port = None
        try:
            self.process, self.port = self.launcher._open_system_chrome(
                "about:blank", remote_debugging=True, user_data_dir=self.profile.name, hidden=True)
            if not self.process:
                raise RuntimeError("Installed Chrome/Edge was not found")
            deadline = time.monotonic() + 20
            while True:
                try:
                    self.request("/json/version")
                    break
                except Exception:
                    if time.monotonic() >= deadline or self.process.poll() is not None:
                        raise RuntimeError("NU Chrome did not start")
                    time.sleep(0.25)
        except Exception:
            self.close()
            raise

    def request(self, path, method="GET"):
        req = Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        with urlopen(req, timeout=5) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.startswith(("{", "[")) else raw

    def fetch(self, url, validate, timeout=90):
        print(f"NovelUpdates opening: {url}", flush=True)
        target = self.request("/json/new?" + quote(url, safe=""), "PUT")
        deadline = time.monotonic() + timeout
        clicked = {}
        last_error = "document did not finish loading"
        last_title = ""
        try:
            while time.monotonic() < deadline:
                targets = self.request("/json/list")
                page = next((item for item in targets if item["id"] == target["id"]), None)
                if page is None:
                    raise RuntimeError("NU browser tab closed before the document loaded")
                last_title = page.get("title", "")
                challenged = any(word in last_title.lower() for word in (
                    "just a moment", "attention required", "security verification"))
                if last_title and not challenged and page.get("url") != "about:blank":
                    try:
                        html = document(page)
                        validate(html, url)
                        return html
                    except Exception as error:
                        last_error = str(error)
                if challenged:
                    for frame in targets:
                        if (frame.get("type") != "iframe" or
                                urlsplit(frame.get("url", "")).hostname != "challenges.cloudflare.com"):
                            continue
                        count, previous = clicked.get(frame["id"], (0, 0))
                        if count >= 3 or time.monotonic() - previous < 10:
                            continue
                        try:
                            if click_verification(frame):
                                clicked[frame["id"]] = count + 1, time.monotonic()
                                print("NovelUpdates: clicked verification checkbox", flush=True)
                        except Exception:
                            # The frame may be replaced or still displaying its progress indicator.
                            pass
                time.sleep(1)
            raise RuntimeError(f'NU document timed out after {timeout}s (page titled "{last_title}"): {last_error}')
        finally:
            try:
                self.request("/json/close/" + target["id"])
            except Exception:
                pass

    def close(self):
        if self.port:
            self.launcher._request_cdp_browser_close(self.port)
        if self.process:
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.terminate()
                self.process.wait(timeout=5)
        self.profile.cleanup()
