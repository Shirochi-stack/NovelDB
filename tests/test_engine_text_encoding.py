"""The actual Python-engine transport must preserve UTF-8 chapter titles and legacy bytes."""
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from external_scraper import ExternalScraper


TITLE = "Chapter1\u00a0-Nightmare Begins"
HTML = '<html><head><meta charset="utf-8"></head><body><h1>Shadow Slave</h1><a href="/novel/shadow-slave/chapter/1"><div class="min-w-0">' + TITLE + '</div></a><div class="max-w-[65ch]"><p>“A story.” 한글 中文 café</p></div></body></html>'
URL = "https://asurascans.com/novel/shadow-slave"


def scraper_for(monkeypatch, content, header="text/html"):
    response = requests.Response()
    response.status_code = 200
    response._content = content
    response.headers["Content-Type"] = header
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.url = URL
    scraper = ExternalScraper(logger=lambda _: None)
    monkeypatch.setattr(scraper, "_storage_cookies_for_url", lambda _: [])
    monkeypatch.setattr(scraper, "_gm_session", lambda: SimpleNamespace(request=lambda *a, **k: response))
    return scraper, response


def test_lncrawl_chapter_titles_survive_the_real_desktop_transport(monkeypatch):
    from noveldb.core.engines.lncrawl_engine import LncrawlEngine
    raw = HTML.encode("utf-8")
    scraper, response = scraper_for(monkeypatch, raw)
    assert "Chapter1Â" in response.text, "Reproduce requests' charset-free HTML fallback"
    fetched = scraper.engine_fetch(URL)
    assert fetched["content"] == raw
    assert TITLE in fetched["text"] and "Â" not in fetched["text"]
    engine = LncrawlEngine(fetch=scraper.engine_fetch)
    book = engine.parse_book(URL)
    assert book["chapters"][0]["name"] == TITLE
    chapter = engine.parse_chapter(book["chapters"][0], book)
    assert chapter["chapterName"] == TITLE
    assert "“A story.” 한글 中文 café" in chapter["contentText"]


@pytest.mark.parametrize("encoding,header,text", [
    ("gbk", "text/html", '<meta charset="gbk"><p>第一章 开始</p>'),
    ("cp1252", "text/html; charset=windows-1252", "<p>café — début</p>"),
    ("utf-8", "text/html", HTML),
])
def test_desktop_text_and_original_bytes_keep_the_declared_encoding(monkeypatch, encoding, header, text):
    raw = text.encode(encoding)
    scraper, _ = scraper_for(monkeypatch, raw, header)
    result = scraper.engine_fetch(URL)
    assert result["content"] == raw
    assert result["text"] == text
    binary = scraper.engine_fetch(URL, binary=True)
    assert binary["content"] == raw and binary["text"] == ""


@pytest.mark.parametrize("encoding,header,text", [
    ("utf-8", "content-type: text/html", HTML),
    ("gbk", "content-type: text/html", '<meta charset="gbk"><p>第一章 开始</p>'),
    ("cp1252", "content-type: text/html; charset=windows-1252", "<p>café — début</p>"),
    ("utf-8", "content-type: text/html; charset=iso-8859-1", HTML),
])
def test_mobile_python_transport_uses_the_same_charset_decoder(monkeypatch, encoding, header, text):
    path = Path(__file__).resolve().parents[1] / "scripts/python_host/noveldb_mobile.py"
    spec = importlib.util.spec_from_file_location("mobile_encoding_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = text.encode(encoding)
    monkeypatch.setattr(module, "_transport", lambda _: {"status": 200,
        "responseBase64": base64.b64encode(raw).decode("ascii"), "responseHeaders": header})
    result = module.fetch(URL)
    assert result["text"] == text
    assert result["content"] == raw
    assert module.fetch(URL, binary=True)["text"] == ""


@pytest.mark.parametrize("source,version,refresh", [("_lncrawl", 1, True), ("_lncrawl", 2, False), ("_jara", 1, False)])
def test_old_crawler_cache_refreshes_once_without_discarding_other_sources(tmp_path, source, version, refresh):
    from scripts.source_names import dumps
    from noveldb.core.chapter_cache import ChapterCache
    from noveldb.core.external.pipeline import ExternalPipelineMixin
    from noveldb.core.settings.legacy_vars import Value
    path = tmp_path / "cache.json"
    entries = {"chapter1": {"chapterName": "Chapter1Â\u00a0-Nightmare Begins", "contentHtml": "<p>Old</p>"}}
    path.write_text(dumps(dict(chapters=entries, textEncodingVersion=version)), encoding="utf-8")
    logs = []
    job = SimpleNamespace(_var_use_cache=Value(True), _book_data={source: True},
                          _external_cache_path=lambda: str(path), _log=logs.append)
    cached, target = ExternalPipelineMixin._load_external_cache(job)
    assert cached == ({} if refresh else entries)
    assert target == str(path)
    ChapterCache(target).put("chapter1", {"chapterName": TITLE})
    cached, _ = ExternalPipelineMixin._load_external_cache(job)
    assert cached["chapter1"]["chapterName"] == TITLE
