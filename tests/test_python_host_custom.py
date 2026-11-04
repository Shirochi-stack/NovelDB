"""The phone apps' Python page (scripts/python_host/noveldb_mobile.py): custom engines from the app."""
import base64
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (ROOT / "examples" / "custom_engine_example.py").read_text(encoding="utf-8")
PAGES = {
    "https://novels.example/book/sword": """<h1>The Wandering Sword</h1><span class="author">Lan Yu</span>
        <ul class="chapter-list"><li><a href="/book/sword/1">Chapter 1</a></li></ul>""",
    "https://novels.example/book/sword/1": """<div id="chapter-content"><p>The village burned.</p></div>""",
}


def load(monkeypatch, engines):
    spec = importlib.util.spec_from_file_location("noveldb_mobile_test", ROOT / "scripts" / "python_host" / "noveldb_mobile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    asked = []

    def transport(request):
        asked.append(request.get("op") or request["url"])
        if request.get("op") == "customEngines":
            return {"engines": engines}
        body = PAGES.get(request["url"])
        if body is None:
            return {"status": 404, "responseBase64": ""}
        return {"status": 200, "finalUrl": request["url"], "responseBase64": base64.b64encode(body.encode()).decode(),
                "responseHeaders": "content-type: text/html; charset=utf-8"}

    monkeypatch.setattr(module, "_transport", transport)
    return module, asked


def test_the_custom_engine_reads_a_book_and_a_chapter_from_the_apps_engine_files(monkeypatch):
    module, _asked = load(monkeypatch, [{"id": "example", "label": "Example Novels", "sites": ["novels.example"],
                                         "source": EXAMPLE}])
    book = json.loads(module.parse_book("custom", "https://novels.example/book/sword"))
    assert book["bookname"] == "The Wandering Sword" and book["_customId"] == "example"
    assert book["ruleName"] == "Example Novels (custom engine)"
    chapter = json.loads(module.parse_chapter("custom", json.dumps(
        {"url": "https://novels.example/book/sword/1", "name": "Chapter 1", "bookUrl": "https://novels.example/book/sword"})))
    assert chapter["contentText"] == "The village burned."
    assert chapter["contentHtml"] == "<p>The village burned.</p>"


def test_a_link_no_custom_engine_reads_is_a_miss_and_a_broken_engine_is_skipped(monkeypatch):
    module, _asked = load(monkeypatch, [{"id": "broken", "label": "Broken", "sites": ["novels.example"],
                                         "source": "class Engine:\n    def __init__(self, fetch, log): raise ValueError('nope')\n"},
                                        {"id": "example", "label": "Example Novels", "sites": ["novels.example"],
                                         "source": EXAMPLE}])
    other = json.loads(module.parse_book("custom", "https://elsewhere.example/novel/1"))
    assert other["noParser"] is True
    book = json.loads(module.parse_book("custom", "https://novels.example/book/sword"))
    assert book["_customId"] == "example"


def test_an_edited_engine_is_loaded_again(monkeypatch):
    engines = [{"id": "example", "label": "Example Novels", "sites": ["novels.example"], "source": EXAMPLE}]
    module, _asked = load(monkeypatch, engines)
    first = module._custom_engines()[0].engine
    assert module._custom_engines()[0].engine is first  # unchanged: the same engine
    engines[0]["source"] = EXAMPLE.replace('label = "Example Novels"', 'label = "Renamed"')
    assert module._custom_engines()[0].engine.label == "Renamed"
