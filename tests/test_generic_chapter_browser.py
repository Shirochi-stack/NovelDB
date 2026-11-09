"""The mobile chapter extractor recovers readable text through its native browser transport."""

import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generic" / "generic-lib.js"
REAL = "붉은 지붕에 쌓여 있던 눈이 조금씩 높이를 줄였다. 하얀숲을 지나오는 바람에 봄기운이 실려 왔다. " * 8
NOISE = "각" * 300


def chapter_page(text, app_data=False):
    data = {"component": "contents/View", "props": {"episode": {"title": "1화 봄", "body": {"html": f"<p>{text}</p>"}}}}
    embedded = '<script data-page="app" type="application/json">' + json.dumps(data) + "</script>" if app_data else ""
    return f"<html><body>{embedded}<article><p>{text}</p></article></body></html>"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        instance = p.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.mark.parametrize("reply", ["markup", "app_data", "pending", "scrambled", "error"])
def test_scrambled_fetch_uses_the_browser_and_rejects_unreadable_results(browser, reply):
    tab = browser.new_page()
    try:
        tab.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
        tab.goto("https://novels.example/book")
        rendered = chapter_page(NOISE if reply == "scrambled" else REAL, app_data=reply == "app_data")
        tab.evaluate("""({raw, rendered, reply}) => {
            window.requests = [];
            window.fetch = async () => new Response(raw, {headers: {'content-type': 'text/html; charset=utf-8'}});
            window.__GEN_transport = async request => {
                window.requests.push(request);
                if (reply === 'error') return {error: 'browser unavailable'};
                if (reply === 'pending') return {pending: 'page-1'};
                return {value: rendered};
            };
            window.__noveldb_async = {wait: async id => {
                if (id !== 'page-1') throw new Error('unexpected pending request');
                return {value: rendered};
            }};
        }""", {"raw": chapter_page(NOISE, app_data=True), "rendered": rendered, "reply": reply})
        tab.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        result = json.loads(tab.evaluate("async () => await window.__GEN_parseChapter(JSON.stringify({url: 'https://novels.example/book/1-ep-1', name: '1화 봄'}))"))
        requests = tab.evaluate("window.requests")
        assert len(requests) == 1 and requests[0]["op"] == "window"
        assert requests[0]["url"] == "https://novels.example/book/1-ep-1"
        if reply in ("scrambled", "error"):
            assert "scrambled chapter text" in result["error"]
            assert "contentText" not in result
        else:
            assert "붉은 지붕" in result["contentText"] and NOISE not in result["contentText"]
    finally:
        tab.close()
